"""A managed sandbox only runs its own session tree (end-to-end, real app).

Reproduces the 2026-10-29 staging incident: two ordinary sessions' hourly
scheduled tasks fired at the top of the hour and the server launched their
runners inside the managed sandbox pod of a factory member ("PL · base-env")
of the same user — the pod was simply the user's most-recently-active online
host. Everything here is production code except the sandbox itself: the
"pods" are fake hosts dialing the app's real host tunnel with the launch token
the server minted, and they record every ``host.launch_runner`` frame they get.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import pytest_asyncio
from asgiref.testing import ApplicationCommunicator
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from omnigent.host.frames import (
    HostLaunchRunnerFrame,
    HostLaunchRunnerResultFrame,
    HostStatFrame,
    HostStatResultFrame,
    decode_host_frame,
    encode_host_frame,
)
from omnigent.runner.identity import token_bound_runner_id
from omnigent.runtime.agent_cache import AgentCache
from omnigent.server.app import create_app
from omnigent.server.managed_host_isolation import MANAGED_HOST_CLAIM_LABEL_KEY
from omnigent.server.managed_hosts import parse_sandbox_config
from omnigent.server.scheduled import fire as fire_mod
from omnigent.server.scheduled.fire import FireDeps, build_run_now
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.comment_store.sqlalchemy_store import SqlAlchemyCommentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.scheduled_task_store.sqlalchemy_store import (
    SqlAlchemyScheduledTaskStore,
)
from tests.server.helpers import (
    FakeSandboxLauncher,
    HostStartInvocation,
    create_test_agent,
    install_fake_modal_launcher,
)
from tests.server.integration.test_host_session_binding import _make_hello, _websocket_scope

pytestmark = pytest.mark.asyncio


@dataclass
class _FakePod:
    """A fake managed sandbox host connected over the real tunnel."""

    host_id: str
    comm: ApplicationCommunicator
    launches: list[HostLaunchRunnerFrame] = field(default_factory=list)
    serve_task: asyncio.Task[None] | None = None


@dataclass
class _Env:
    app: FastAPI
    client: AsyncClient
    host_store: HostStore
    conv_store: SqlAlchemyConversationStore
    sched_store: SqlAlchemyScheduledTaskStore
    fake: FakeSandboxLauncher
    pods: dict[str, _FakePod]


async def _serve_pod(pod: _FakePod) -> None:
    """Answer launch (recorded) and stat frames for as long as the pod lives.

    Stat answers mirror a real pod resolving its home directory, which is what
    let the pre-fix fire go on to create a session and launch in it. Reads the
    communicator's queue directly: ``receive_output(timeout=...)`` would cancel
    the app (drop the tunnel) on a quiet interval.
    """
    while True:
        output = await pod.comm.output_queue.get()
        text = output.get("text") if output.get("type") == "websocket.send" else None
        if not isinstance(text, str):
            continue
        try:
            frame = decode_host_frame(text)
        except ValueError:
            continue
        if isinstance(frame, HostStatFrame):
            await pod.comm.send_input(
                {
                    "type": "websocket.receive",
                    "text": encode_host_frame(
                        HostStatResultFrame(
                            request_id=frame.request_id,
                            status="ok",
                            exists=True,
                            type="directory",
                            canonical_path="/root" if frame.path == "~" else frame.path,
                        )
                    ),
                }
            )
        elif isinstance(frame, HostLaunchRunnerFrame):
            pod.launches.append(frame)
            await pod.comm.send_input(
                {
                    "type": "websocket.receive",
                    "text": encode_host_frame(
                        HostLaunchRunnerResultFrame(
                            request_id=frame.request_id,
                            status="launched",
                            runner_id=token_bound_runner_id(frame.binding_token),
                        )
                    ),
                }
            )


async def _connect_pod(app: FastAPI, invocation: HostStartInvocation) -> _FakePod:
    """Dial the app's host tunnel exactly as the in-sandbox host would."""
    scope = _websocket_scope(f"/v1/hosts/{invocation.host_id}/tunnel")
    scope["headers"] = [(b"x-omnigent-host-token", invocation.token.encode("ascii"))]
    comm = ApplicationCommunicator(app, scope)
    await comm.send_input({"type": "websocket.connect"})
    accepted = await comm.receive_output(timeout=5.0)
    assert accepted["type"] == "websocket.accept", f"tunnel refused: {accepted!r}"
    await comm.send_input(
        {"type": "websocket.receive", "text": _make_hello(name=invocation.host_name)}
    )
    pod = _FakePod(host_id=invocation.host_id, comm=comm)
    pod.serve_task = asyncio.create_task(_serve_pod(pod))
    return pod


@pytest_asyncio.fixture()
async def env(
    runtime_init: None,
    db_uri: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[_Env]:
    """Production app with managed sandboxes + scheduled tasks, fake pods only."""
    artifact_store = LocalArtifactStore(str(tmp_path / "artifacts"))
    agent_cache = AgentCache(artifact_store=artifact_store, cache_dir=tmp_path / "cache")
    host_store = HostStore(db_uri)
    conv_store = SqlAlchemyConversationStore(db_uri)
    sched_store = SqlAlchemyScheduledTaskStore(db_uri)
    file_store = SqlAlchemyFileStore(db_uri)
    app = create_app(
        agent_store=SqlAlchemyAgentStore(db_uri),
        file_store=file_store,
        conversation_store=conv_store,
        artifact_store=artifact_store,
        agent_cache=agent_cache,
        comment_store=SqlAlchemyCommentStore(db_uri),
        host_store=host_store,
        scheduled_task_store=sched_store,
        sandbox_config=parse_sandbox_config(
            {
                "provider": "modal",
                "server_url": "https://managed-test.example.com",
                "modal": {"image": "docker.io/test/omnigent-host:latest"},
            }
        ),
    )
    # The scheduler (lifespan) is not started here; wire the same run-now
    # trigger it would, over the real fire path and the app's own registries.
    app.state.scheduled_task_run_now = build_run_now(
        FireDeps(
            scheduled_task_store=sched_store,
            agent_store=app.state.agent_store,
            conversation_store=conv_store,
            permission_store=None,
            host_store=host_store,
            host_registry=app.state.host_registry,
            agent_cache=agent_cache,
            runner_router=app.state.runner_router,
            tunnel_registry=app.state.tunnel_registry,
            file_store=file_store,
            artifact_store=artifact_store,
        )
    )
    monkeypatch.setattr("omnigent.server.managed_hosts.MANAGED_HOST_ONLINE_TIMEOUT_S", 10)
    monkeypatch.setattr(
        "omnigent.server.routes.sessions._HOST_RELAUNCH_RUNNER_CONNECT_TIMEOUT_S", 0.2
    )
    monkeypatch.setattr("omnigent.server.routes.sessions._HOST_BOUND_RUNNER_CONNECT_GRACE_S", 0.1)
    # No real runner process exists: report the managed runner's tunnel as
    # connected so a managed launch settles "ready" and its pod stays up.
    monkeypatch.setattr(
        app.state.tunnel_registry,
        "wait_for_runner",
        lambda _runner_id, *, timeout_s: asyncio.sleep(0, result=object()),
    )

    loop = asyncio.get_running_loop()
    pods: dict[str, _FakePod] = {}
    pending: list[asyncio.Future[_FakePod]] = []

    def _on_host_start(invocation: HostStartInvocation) -> None:
        future = asyncio.run_coroutine_threadsafe(_connect_pod(app, invocation), loop)
        wrapped = asyncio.wrap_future(future, loop=loop)
        wrapped.add_done_callback(lambda f: pods.__setitem__(invocation.host_id, f.result()))
        pending.append(wrapped)

    fake = FakeSandboxLauncher(on_host_start=_on_host_start)
    install_fake_modal_launcher(monkeypatch, fake)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield _Env(
            app=app,
            client=client,
            host_store=host_store,
            conv_store=conv_store,
            sched_store=sched_store,
            fake=fake,
            pods=pods,
        )
    await asyncio.gather(*pending, return_exceptions=True)
    for pod in pods.values():
        if pod.serve_task is not None:
            pod.serve_task.cancel()
    await asyncio.gather(
        *(p.serve_task for p in pods.values() if p.serve_task is not None),
        return_exceptions=True,
    )


async def _start_factory_member(env: _Env, agent_id: str) -> tuple[str, str]:
    """Create the managed "PL" session and wait until its pod runs its runner.

    :returns: ``(session_id, pod_host_id)``.
    """
    resp = await env.client.post(
        "/v1/sessions",
        json={"agent_id": agent_id, "host_type": "managed", "title": "PL · base-env"},
    )
    assert resp.status_code == 201, resp.text
    session_id = resp.json()["id"]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 15.0
    while loop.time() < deadline:
        launch = env.app.state.managed_launches.get(session_id)
        settled = launch is None or launch.settled.is_set()
        conv = env.conv_store.get_conversation(session_id)
        pod = env.pods.get(conv.host_id) if conv is not None and conv.host_id else None
        if settled and pod is not None and pod.launches:
            assert launch is None or launch.error is None, launch.error
            return session_id, pod.host_id
        await asyncio.sleep(0.05)
    raise AssertionError("managed PL session never came up")


async def _fire_now(env: _Env, task_id: str) -> None:
    """Trigger the task through the run-now route and wait for the fire."""
    resp = await env.client.post(f"/v1/scheduled-tasks/{task_id}/run")
    assert resp.status_code == 202, resp.text
    pending = [t for t in fire_mod._PENDING_FIRES if not t.done()]
    await asyncio.gather(*pending, return_exceptions=True)


async def test_scheduled_fire_never_borrows_a_factory_members_sandbox(env: _Env) -> None:
    """The live case: two sessions of the same user with hourly tasks; the only
    online host is the PL's managed pod. Neither fire may bind to or launch in
    that pod — each records a visible failed run instead."""
    agent = await create_test_agent(env.client, name="isolation-agent")
    pl_id, pod_id = await _start_factory_member(env, agent["id"])
    pod = env.pods[pod_id]
    assert [frame.session_id for frame in pod.launches] == [pl_id]

    for name in ("hello", "test"):
        created = await env.client.post(
            "/v1/scheduled-tasks",
            json={
                "name": name,
                "prompt": "say hi",
                "rrule": "FREQ=HOURLY",
                "agent_id": agent["id"],
                "timezone": "UTC",
            },
        )
        assert created.status_code == 200, created.text
        task_id = created.json()["id"]
        await _fire_now(env, task_id)

        runs, _ = env.sched_store.list_runs(task_id)
        assert [(r.status, r.error_code, r.conversation_id) for r in runs] == [
            ("failed", "no_online_host", None)
        ]

    # The pod only ever ran the PL's runner, and it is still the PL's.
    assert [frame.session_id for frame in pod.launches] == [pl_id]
    assert env.fake.terminated == []
    # The managed bind claimed the pod for the PL's tree — server-side only.
    pl_row = env.conv_store.get_conversation(pl_id)
    assert pl_row is not None and pl_row.host_id == pod_id
    assert pl_row.labels[MANAGED_HOST_CLAIM_LABEL_KEY] == pod_id
    snapshot = await env.client.get(f"/v1/sessions/{pl_id}")
    assert MANAGED_HOST_CLAIM_LABEL_KEY not in snapshot.json()["labels"]


async def test_requests_naming_the_sandbox_are_refused(env: _Env) -> None:
    """Every client-directed way to put another session on the pod is refused:
    pinning it on a scheduled task, a create naming it, and a runner launch."""
    agent = await create_test_agent(env.client, name="isolation-agent")
    pl_id, pod_id = await _start_factory_member(env, agent["id"])
    pod = env.pods[pod_id]

    pinned = await env.client.post(
        "/v1/scheduled-tasks",
        json={
            "name": "pinned",
            "prompt": "p",
            "rrule": "FREQ=HOURLY",
            "agent_id": agent["id"],
            "timezone": "UTC",
            "host_id": pod_id,
        },
    )
    assert pinned.status_code == 400, pinned.text
    assert "managed sandbox" in pinned.text

    create = await env.client.post(
        "/v1/sessions",
        json={"agent_id": agent["id"], "host_id": pod_id, "workspace": "/root/workspace"},
    )
    assert create.status_code == 400, create.text
    assert "managed sandbox" in create.text

    other = await env.client.post("/v1/sessions", json={"agent_id": agent["id"]})
    assert other.status_code == 201, other.text
    launch = await env.client.post(
        f"/v1/hosts/{pod_id}/runners",
        json={"session_id": other.json()["id"], "workspace": "/root/workspace"},
    )
    assert launch.status_code == 409, launch.text
    assert "managed sandbox" in launch.text

    assert [frame.session_id for frame in pod.launches] == [pl_id]


async def _seed_borrowed_binding(env: _Env, agent_id: str, pod_id: str, title: str) -> str:
    """Recreate a row the pre-fix fire produced: a fired session bound to the pod."""
    borrower = env.conv_store.create_conversation(
        agent_id=agent_id,
        title=title,
        host_id=pod_id,
        workspace="/root",
    )
    task = env.sched_store.create(
        scheduled_task_id=uuid.uuid4().hex,
        name=title,
        prompt="say hi",
        rrule="FREQ=HOURLY",
        user_id=None,
        agent_id=agent_id,
        timezone="UTC",
    )
    env.sched_store.create_run(
        run_id=uuid.uuid4().hex,
        scheduled_task_id=task.id,
        status="failed",
        scheduled_at=1,
        conversation_id=borrower.id,
        error="runner launch/dispatch failed",
        error_code="launch_failed",
    )
    return borrower.id


async def test_message_to_borrowed_binding_reprovisions_its_own_sandbox(env: _Env) -> None:
    """Repair-on-read: a fired session already bound to the PL's pod (a row left
    by the pre-fix fire) is treated as hostless on its next message and gets a
    fresh sandbox of its own — the PL's pod never sees a launch for it."""
    agent = await create_test_agent(env.client, name="isolation-agent")
    pl_id, pod_id = await _start_factory_member(env, agent["id"])
    pod = env.pods[pod_id]
    pl_runner = env.conv_store.get_conversation(pl_id).runner_id  # type: ignore[union-attr]
    borrower_id = await _seed_borrowed_binding(env, agent["id"], pod_id, "hello")

    await env.client.post(
        f"/v1/sessions/{borrower_id}/events",
        json={
            "type": "message",
            "data": {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
        },
        timeout=30.0,
    )

    repaired = env.conv_store.get_conversation(borrower_id)
    assert repaired is not None
    assert repaired.host_id is not None and repaired.host_id != pod_id
    assert repaired.labels[MANAGED_HOST_CLAIM_LABEL_KEY] == repaired.host_id
    own_pod = env.pods[repaired.host_id]
    assert {frame.session_id for frame in own_pod.launches} == {borrower_id}
    assert len(env.fake.provisioned_names) == 2

    # The PL and its pod are untouched.
    assert [frame.session_id for frame in pod.launches] == [pl_id]
    pl_row = env.conv_store.get_conversation(pl_id)
    assert pl_row is not None and pl_row.host_id == pod_id and pl_row.runner_id == pl_runner
    assert env.fake.terminated == []


async def test_deleting_borrowed_binding_keeps_the_members_sandbox(env: _Env) -> None:
    """Deleting a session bound to someone else's pod must not tear the pod down
    (before the fix this terminated the factory member's sandbox)."""
    agent = await create_test_agent(env.client, name="isolation-agent")
    pl_id, pod_id = await _start_factory_member(env, agent["id"])
    borrower_id = await _seed_borrowed_binding(env, agent["id"], pod_id, "test")

    deleted = await env.client.delete(f"/v1/sessions/{borrower_id}")
    assert deleted.status_code == 200, deleted.text

    assert env.fake.terminated == []
    assert env.host_store.get_host(pod_id) is not None
    assert env.conv_store.get_conversation(pl_id).host_id == pod_id  # type: ignore[union-attr]
