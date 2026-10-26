"""A two-member software factory, end to end on a live server and runner.

A lead and a project lead (PL) run as top-level sessions on one live runner,
each on a scripted openai-agents agent (mock LLM) whose spec declares
``executor.config.factory_role``. The first journey drives the REST API and
the real runner factory tools through seven steps:

1. the lead's first factory call (``sys_factory_roster``) stamps its labels;
2. a second top-level session joins with factory labels (the PL);
3. the lead's ``sys_session_post(kind="request")`` reaches the PL in the
   server envelope and starts a PL turn;
4. the PL's idle edge sends the lead a reply notice;
5. a forced supervisor tick delivers a heartbeat to the idle PL;
6. the roster lists both members;
7. a post to a session outside the factory is refused with 403.

Two steps stand in for what a claude-native member does on its own:

- Step 4 posts the ``external_session_status`` idle edge that Claude Code's
  Stop hook sends. Only that authoritative edge produces reply notices; a
  non-native harness's turn end never does, by design.
- Step 5 forces the pass through a test-only debug route
  (``factory_debug_router``) on a clock shifted forward, because heartbeats
  need a member idle for at least two minutes of wall time.

A second journey has a PL report to the cockpit through the real runner
tools: it asks the human a question (``sys_factory_ask``), registers its PR
(``sys_factory_pr``), reports the PR's factory preview (``sys_factory_env``)
and publishes a demo of two local files (``sys_factory_demo``), which the
server then serves back with byte ranges.

The stack is this module's own (factory flag on, automatic ticks parked), so
it never changes the shared live server other journeys use.
"""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from omnigent import factory as F
from omnigent.runner.identity import OMNIGENT_INTERNAL_WS_ORIGIN, token_bound_runner_id
from tests._helpers.compat import (
    COMPAT_RUNNER_PYTHON_ENV,
    COMPAT_SERVER_PYTHON_ENV,
    apply_server_env,
)
from tests.e2e.conftest import (
    configure_mock_llm,
    find_free_port,
    lookup_agent_id,
    poll_session_until_terminal,
    send_user_message_to_session,
    set_fallback_mock_llm,
    upload_agent,
)
from tests.e2e.helpers import HEALTH_TIMEOUT_S, POLL_INTERVAL_S

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEBUG_ROUTER_MODULE = "tests.integration.factory_debug_router"
_TURN_TIMEOUT_S = 50.0
# Far enough ahead that the PL (heartbeat 600 s) is due and the lead (3600 s) is not.
_TICK_ADVANCE_S = 1200


@dataclass
class FactoryStack:
    """A live server (factory enabled) with one runner connected to it."""

    base_url: str
    runner_id: str
    client: httpx.Client
    mock_llm_url: str
    agents_dir: Path
    db_uri: str


def _stop(proc: subprocess.Popen[bytes], timeout: float) -> None:
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


@pytest.fixture(scope="module")
def factory_stack(
    tmp_path_factory: pytest.TempPathFactory,
    mock_llm_server_url: str | None,
    harness_name: str,
) -> Iterator[FactoryStack]:
    """Start a factory-enabled server with the debug tick route, plus a runner."""
    if mock_llm_server_url is None:
        pytest.skip("the factory tree journey is scripted against the mock LLM")
    if harness_name != "openai-agents":
        pytest.skip("the scripted factory members run on openai-agents; one leg is enough")
    if os.environ.get(COMPAT_SERVER_PYTHON_ENV) or os.environ.get(COMPAT_RUNNER_PYTHON_ENV):
        pytest.skip("pinned older servers and runners predate the software factory")

    root = tmp_path_factory.mktemp("factory_tree")
    token = secrets.token_urlsafe(32)
    runner_id = token_bound_runner_id(token)
    config_path = root / "server.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "llm": {
                    "model": "_policy_llm_",
                    "connection": {
                        "base_url": f"{mock_llm_server_url}/v1",
                        "api_key": "mock-key",
                    },
                },
                "debug_router_modules": [_DEBUG_ROUTER_MODULE],
            }
        )
    )
    env = {
        **os.environ,
        "OPENAI_API_KEY": "mock-key",
        "OPENAI_BASE_URL": f"{mock_llm_server_url}/v1",
        F.FACTORY_ENABLED_ENV: "1",
        # Park the automatic supervisor loop; the test forces its passes.
        "OMNIGENT_FACTORY_TICK_S": "3600",
    }
    apply_server_env(env, _REPO_ROOT)
    port = find_free_port()
    base_url = f"http://localhost:{port}"
    server_log = (root / "server.log").open("w")
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "omnigent.cli",
            "server",
            "--port",
            str(port),
            "--database-uri",
            f"sqlite:///{root / 'factory.db'}",
            "--artifact-location",
            str(root / "artifacts"),
            "--config",
            str(config_path),
        ],
        env={**env, "OMNIGENT_RUNNER_TUNNEL_TOKEN": token},
        stdout=server_log,
        stderr=subprocess.STDOUT,
    )
    runner_log = (root / "runner.log").open("w")
    runner = subprocess.Popen(
        [sys.executable, "-m", "omnigent.runner._entry"],
        env={
            **env,
            "OMNIGENT_RUNNER_ID": runner_id,
            "OMNIGENT_RUNNER_TUNNEL_BINDING_TOKEN": token,
            "OMNIGENT_RUNNER_PARENT_PID": str(os.getpid()),
            "RUNNER_SERVER_URL": base_url,
        },
        stdout=runner_log,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + HEALTH_TIMEOUT_S
        while True:
            try:
                health = httpx.get(f"{base_url}/health", timeout=2)
                status = httpx.get(f"{base_url}/v1/runners/{runner_id}/status", timeout=2)
                if (
                    health.status_code == 200
                    and status.status_code == 200
                    and status.json()["online"] is True
                ):
                    break
            except httpx.ConnectError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"factory server/runner did not come up; logs in {root}:\n"
                    f"{(root / 'server.log').read_text()[-3000:]}\n"
                    f"{(root / 'runner.log').read_text()[-3000:]}"
                )
            time.sleep(POLL_INTERVAL_S)
        set_fallback_mock_llm(
            mock_llm_server_url, "_policy_llm_", '{"action": "allow", "reason": ""}'
        )
        with httpx.Client(base_url=base_url, timeout=30) as client:
            yield FactoryStack(
                base_url=base_url,
                runner_id=runner_id,
                client=client,
                mock_llm_url=mock_llm_server_url,
                agents_dir=root / "agents",
                db_uri=f"sqlite:///{root / 'factory.db'}",
            )
    finally:
        _stop(runner, 5)
        _stop(server, 10)
        runner_log.close()
        server_log.close()


# ── helpers ──────────────────────────────────────────────────────────────────


def _register_agent(stack: FactoryStack, *, name: str, model: str, role: str | None) -> str:
    """Upload a scripted openai-agents bundle and return its agent id."""
    config: dict[str, str] = {"harness": "openai-agents"}
    if role is not None:
        config["factory_role"] = role
    agent_dir = stack.agents_dir / name
    agent_dir.mkdir(parents=True)
    (agent_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "spec_version": 1,
                "name": name,
                "description": f"Scripted factory test agent ({role or 'no role'}).",
                "prompt": "Follow the scripted mock LLM tool calls exactly.",
                "executor": {
                    "type": "omnigent",
                    "model": model,
                    "auth": {
                        "type": "api_key",
                        "api_key": "mock-key",
                        "base_url": f"{stack.mock_llm_url}/v1",
                    },
                    "config": config,
                },
            },
            sort_keys=False,
        )
    )
    return lookup_agent_id(stack.client, upload_agent(stack.client, agent_dir))


def _create_session(
    stack: FactoryStack,
    *,
    agent_id: str,
    title: str,
    labels: dict[str, str] | None = None,
    bind_runner: bool = True,
    workspace: Path | None = None,
) -> str:
    body: dict[str, Any] = {"agent_id": agent_id, "title": title}
    if labels:
        body["labels"] = labels
    if workspace is not None:
        body["workspace"] = str(workspace)
    resp = stack.client.post(
        "/v1/sessions", json=body, headers={"Origin": OMNIGENT_INTERNAL_WS_ORIGIN}
    )
    assert resp.status_code < 400, resp.text
    session_id = str(resp.json()["id"])
    if bind_runner:
        stack.client.patch(
            f"/v1/sessions/{session_id}", json={"runner_id": stack.runner_id}
        ).raise_for_status()
    return session_id


def _labels(stack: FactoryStack, session_id: str) -> dict[str, str]:
    resp = stack.client.get(f"/v1/sessions/{session_id}/labels")
    resp.raise_for_status()
    return dict(resp.json()["labels"])


def _items(stack: FactoryStack, session_id: str) -> list[dict[str, Any]]:
    resp = stack.client.get(
        f"/v1/sessions/{session_id}/items", params={"order": "asc", "limit": 1000}
    )
    resp.raise_for_status()
    return list(resp.json()["data"])


def _texts(stack: FactoryStack, session_id: str, role: str) -> list[str]:
    """Plain text of every *role* message in the session, oldest first."""
    texts: list[str] = []
    for item in _items(stack, session_id):
        if item.get("type") != "message" or item.get("role") != role:
            continue
        content = item.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.append("".join(str(b.get("text", "")) for b in content if isinstance(b, dict)))
    return texts


def _tool_output(stack: FactoryStack, session_id: str, call_id: str) -> Any:
    for item in _items(stack, session_id):
        if item.get("type") == "function_call_output" and item.get("call_id") == call_id:
            return json.loads(str(item.get("output")))
    raise AssertionError(f"no output for tool call {call_id} in {session_id}")


def _wait_for(check: Callable[[], bool], what: str, timeout: float = _TURN_TIMEOUT_S) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for {what}")
        time.sleep(0.2)


def _wait_for_message(
    stack: FactoryStack, session_id: str, role: str, predicate: Callable[[str], bool], what: str
) -> str:
    found: list[str] = []

    def _check() -> bool:
        found[:] = [t for t in _texts(stack, session_id, role) if predicate(t)]
        return bool(found)

    _wait_for(_check, what)
    return found[-1]


def _wait_idle(stack: FactoryStack, session_id: str) -> None:
    def _idle() -> bool:
        resp = stack.client.get(f"/v1/sessions/{session_id}")
        resp.raise_for_status()
        return resp.json().get("status") == "idle"

    _wait_for(_idle, f"{session_id} to go idle")


def _run_turn(stack: FactoryStack, session_id: str, prompt: str) -> None:
    response_id = send_user_message_to_session(stack.client, session_id=session_id, content=prompt)
    result = poll_session_until_terminal(
        stack.client, session_id=session_id, response_id=response_id, timeout=_TURN_TIMEOUT_S
    )
    assert result["status"] == "completed", result


def _call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, str]:
    return {"call_id": call_id, "name": name, "arguments": json.dumps(arguments)}


# ── the journey ──────────────────────────────────────────────────────────────


def test_factory_tree_messaging_supervision_and_scope(factory_stack: FactoryStack) -> None:
    stack = factory_stack
    run = uuid.uuid4().hex[:8]
    lead_model, pl_model = f"mock-factory-lead-{run}", f"mock-factory-pl-{run}"
    slug = f"tree-{run}"
    lead_agent = _register_agent(stack, name=f"tree-lead-{run}", model=lead_model, role="lead")
    pl_agent = _register_agent(stack, name=f"tree-pl-{run}", model=pl_model, role="project_lead")
    plain_agent = _register_agent(
        stack, name=f"tree-plain-{run}", model=f"mock-factory-plain-{run}", role=None
    )

    # 1. The lead's first factory call enrols it as the root of a new factory.
    lead = _create_session(stack, agent_id=lead_agent, title="Atlas")
    assert F.LABEL_ID not in _labels(stack, lead)
    configure_mock_llm(
        stack.mock_llm_url,
        [
            {"tool_calls": [_call("call_roster_1", "sys_factory_roster", {})]},
            {"text": f"joined {run}"},
        ],
        key=lead_model,
    )
    _run_turn(stack, lead, "Join the factory.")
    labels = _labels(stack, lead)
    assert labels[F.LABEL_ID] == lead
    assert labels[F.LABEL_ROLE] == F.ROLE_LEAD
    assert labels[F.LABEL_STATE] == F.STATE_ACTIVE
    assert labels[F.LABEL_HEARTBEAT_S] == str(F.DEFAULT_HEARTBEAT_S[F.ROLE_LEAD])
    first_roster = _tool_output(stack, lead, "call_roster_1")
    assert first_roster["factory_id"] == lead
    assert [m["session_id"] for m in first_roster["members"]] == [lead]

    # 2. A second top-level session joins through its labels.
    pl_title = f"PL · {slug}"
    pl = _create_session(
        stack,
        agent_id=pl_agent,
        title=pl_title,
        labels={
            F.LABEL_ID: lead,
            F.LABEL_ROLE: F.ROLE_PROJECT_LEAD,
            F.LABEL_PROJECT: slug,
            F.LABEL_PARENT: lead,
            F.LABEL_SUPERVISOR: lead,
            F.LABEL_STATE: F.STATE_ACTIVE,
            F.LABEL_HEARTBEAT_S: "600",
        },
    )
    membership = stack.client.get(f"/v1/factory/membership/{pl}", params={"from_session_id": lead})
    assert membership.status_code == 200, membership.text
    assert membership.json()["factory_id"] == lead
    assert membership.json()["role"] == F.ROLE_PROJECT_LEAD
    assert membership.json()["project"] == slug

    # 3. lead -> PL request: delivered with the server envelope, starts a PL turn.
    configure_mock_llm(
        stack.mock_llm_url,
        [
            {
                "tool_calls": [
                    _call(
                        "call_post_1",
                        "sys_session_post",
                        {"to": f"project:{slug}", "message": f"STATUS? {run}", "kind": "request"},
                    )
                ]
            },
            {"text": "asked the PL"},
        ],
        key=lead_model,
    )
    pl_answer = f"PL status for {run}: all green"
    configure_mock_llm(stack.mock_llm_url, [{"text": pl_answer}], key=pl_model)
    _run_turn(stack, lead, "Ask the project lead for its status.")
    queued = _tool_output(stack, lead, "call_post_1")
    assert queued["status"] == "queued" and queued["to_session_id"] == pl, queued
    envelope = (
        f'[Factory message · from "Atlas" ({lead}, {F.ROLE_LEAD}) · kind=request'
        f' · id={queued["mail_id"]} · reply: sys_session_post(to="{lead}")]\nSTATUS? {run}'
    )
    _wait_for_message(stack, pl, "user", lambda t: t == envelope, "the enveloped request")
    _wait_for_message(stack, pl, "assistant", lambda t: pl_answer in t, "the PL's answer")
    _wait_idle(stack, pl)

    # 4. The PL's idle edge (Claude Code's Stop hook posts it) notifies the lead.
    configure_mock_llm(stack.mock_llm_url, [{"text": f"noted {run}"}], key=lead_model)
    edge = stack.client.post(
        f"/v1/sessions/{pl}/events",
        json={"type": "external_session_status", "data": {"status": "idle", "output": pl_answer}},
    )
    assert edge.status_code in (200, 202), edge.text
    notice = _wait_for_message(
        stack,
        lead,
        "user",
        lambda t: t.startswith("[Factory notice · "),
        "the reply notice",
    )
    assert notice == (
        f'[Factory notice · "{pl_title}" ({pl}) finished the turn your request started '
        f"(idle). Last output: «{pl_answer}». "
        f'Inspect with sys_session_get_history(conversation_id="{pl}").]'
    )
    _wait_for_message(stack, lead, "assistant", lambda t: f"noted {run}" in t, "the lead's turn")
    _wait_idle(stack, lead)

    # 5. A forced supervisor pass beats the idle PL (600 s) but not the lead (3600 s).
    configure_mock_llm(stack.mock_llm_url, [{"text": f"heartbeat handled {run}"}], key=pl_model)
    tick = stack.client.post("/debug/factory/tick", params={"advance_s": _TICK_ADVANCE_S})
    assert tick.status_code == 200, tick.text
    beat = _wait_for_message(
        stack, pl, "user", lambda t: t.startswith("[Factory heartbeat #1 · "), "the heartbeat"
    )
    assert 'Run load_skill("factory-heartbeat")' in beat
    _wait_for_message(
        stack, pl, "assistant", lambda t: f"heartbeat handled {run}" in t, "the beat's turn"
    )
    assert F.LABEL_LAST_BEAT in _labels(stack, pl)
    assert not any(t.startswith("[Factory heartbeat") for t in _texts(stack, lead, "user"))

    # 6 + 7. The roster lists both members; a non-member is out of scope.
    plain = _create_session(stack, agent_id=plain_agent, title="Unrelated", bind_runner=False)
    configure_mock_llm(
        stack.mock_llm_url,
        [
            {
                "tool_calls": [
                    _call("call_roster_2", "sys_factory_roster", {}),
                    _call("call_post_2", "sys_session_post", {"to": plain, "message": "hi"}),
                ]
            },
            {"text": f"audited {run}"},
        ],
        key=lead_model,
    )
    _run_turn(stack, lead, "Audit the roster and greet the unrelated session.")
    roster = _tool_output(stack, lead, "call_roster_2")
    rest_roster = stack.client.get("/v1/factory/roster", params={"from_session_id": lead})
    assert rest_roster.status_code == 200, rest_roster.text
    for listing in (roster, rest_roster.json()):
        by_id = {m["session_id"]: m for m in listing["members"]}
        assert set(by_id) == {lead, pl}
        assert by_id[lead]["role"] == F.ROLE_LEAD
        assert by_id[pl]["role"] == F.ROLE_PROJECT_LEAD
        assert by_id[pl]["project"] == slug
        assert by_id[pl]["supervisor_session_id"] == lead
    refused = _tool_output(stack, lead, "call_post_2")
    assert refused["error"] == "session_out_of_factory", refused
    rest_post = stack.client.post(
        "/v1/factory/messages",
        json={"from_session_id": lead, "to": plain, "message": "hi"},
    )
    assert rest_post.status_code == 403, rest_post.text
    assert rest_post.json()["error"]["code"] == "session_out_of_factory"
    assert not _texts(stack, plain, "user")


def test_factory_cockpit_reports_from_a_project_lead(
    factory_stack: FactoryStack, tmp_path: Path
) -> None:
    from omnigent.stores.factory_store.sqlalchemy_store import SqlAlchemyFactoryStore

    stack = factory_stack
    run = uuid.uuid4().hex[:8]
    pl_model = f"mock-cockpit-pl-{run}"
    slug = f"cockpit-{run}"
    number = 9600 + int(run[:3], 16) % 300
    pr_url = f"https://github.com/caffeinelabs/app/pull/{number}"
    sha = (run * 5)[:40]
    lead_agent = _register_agent(
        stack, name=f"cockpit-lead-{run}", model=f"mock-cockpit-lead-{run}", role="lead"
    )
    pl_agent = _register_agent(
        stack, name=f"cockpit-pl-{run}", model=pl_model, role="project_lead"
    )
    lead = _create_session(stack, agent_id=lead_agent, title="Atlas", bind_runner=False)
    workspace = tmp_path / "workspace"
    video = workspace / "demo" / "video.webm"
    shot = workspace / "demo" / "shots" / "01-landing.png"
    shot.parent.mkdir(parents=True)
    video.write_bytes(b"\x1aE\xdf\xa3" + bytes(range(256)) * 64)
    shot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"p" * 64)
    # The harness's demo.json; the PR comment is written next to it, inside tmp_path.
    manifest = workspace / "demo" / "demo.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "pr_url": pr_url,
                "sha": sha,
                "status": "PARTIAL",
                "summary": "The ENV badge shows in the header; build skipped",
                "steps": [
                    {
                        "n": 1,
                        "title": "Open the header",
                        "kind": "ui",
                        "status": "pass",
                        "screenshot": "shots/01-landing.png",
                    },
                    {"n": 2, "title": "Draft build", "kind": "ui", "status": "skipped"},
                ],
                "artifacts": {"video": "video.webm", "poster": "shots/01-landing.png"},
            }
        )
    )
    pl = _create_session(
        stack,
        agent_id=pl_agent,
        title=f"PL · {slug}",
        labels={
            F.LABEL_ID: lead,
            F.LABEL_ROLE: F.ROLE_PROJECT_LEAD,
            F.LABEL_PROJECT: slug,
            F.LABEL_PARENT: lead,
            F.LABEL_STATE: F.STATE_ACTIVE,
        },
        workspace=workspace,
    )
    # The stored project row (what the cockpit's start form writes) scopes PRs to its repo.
    store = SqlAlchemyFactoryStore(stack.db_uri)
    store.upsert_project(lead, slug, title="ENV badge", repo="caffeinelabs/app", pl_session_id=pl)
    app = f"https://app-{run}-p{number}.envs.sandbox.caffeinelabs.io"
    configure_mock_llm(
        stack.mock_llm_url,
        [
            {
                "tool_calls": [
                    _call(
                        "call_ask",
                        "sys_factory_ask",
                        {
                            "question": f"Where should the badge go? {run}",
                            "options": [
                                {"label": "Header", "recommended": True},
                                {"label": "Footer"},
                            ],
                        },
                    ),
                    _call("call_pr", "sys_factory_pr", {"url": pr_url, "claim": "opened"}),
                    _call(
                        "call_env",
                        "sys_factory_env",
                        {
                            "state": "ready",
                            "env_id": f"{run}-p{number}",
                            "urls": {"app": app},
                            "deployed": {"sha": sha},
                            "pr": number,
                        },
                    ),
                    _call(
                        "call_demo",
                        "sys_factory_demo",
                        {
                            "pr_url": pr_url,
                            "head_sha": sha,
                            "title": "Badge in the header",
                            "manifest_path": "demo/demo.json",
                            "preview_url": app,
                        },
                    ),
                ]
            },
            {"text": f"reported {run}"},
        ],
        key=pl_model,
    )
    _run_turn(stack, pl, "Report to the cockpit.")

    asked = _tool_output(stack, pl, "call_ask")
    registered = _tool_output(stack, pl, "call_pr")
    preview = _tool_output(stack, pl, "call_env")
    demo = _tool_output(stack, pl, "call_demo")
    assert asked["decision"] == "D-1" and asked["deduped"] is False, asked
    assert registered["project"] == slug and registered["ready"] is False, registered
    assert preview["preview"]["state"] == "ready", preview
    # The manifest's status is what gets recorded.
    assert demo["status"] == "PARTIAL", demo
    marker = f"<!-- factory:demo sha={sha} status=PARTIAL demo={demo['demo_id']} -->"
    assert demo["comment_markdown"].startswith(marker)
    assert demo["comment_path"] == str(workspace / "demo" / "pr-comment.md")
    assert Path(demo["comment_path"]).read_text().startswith(marker)

    [decision] = store.list_decisions(factory_id=lead, status="open")
    assert decision.asked_by_session_id == pl and decision.project_slug == slug
    pr = store.get_pr_by_number(lead, "caffeinelabs/app", number)
    assert pr is not None and pr.source == "tool" and pr.project_slug == slug
    assert pr.preview is not None and pr.preview["urls"] == {"app": app}
    assert pr.preview["deployed"]["sha"] == sha
    [row] = store.list_demos(lead, number)
    assert row.id == demo["demo_id"] and row.session_id == pl and row.pr_id == pr.id
    assert row.status == "PARTIAL" and row.summary.startswith("The ENV badge")
    assert [(f["name"], f["kind"]) for f in row.files] == [
        ("video.webm", "video"),
        ("shots/01-landing.png", "screenshot"),
        ("shots/01-landing.png", "poster"),
    ]
    assert row.manifest is not None
    assert row.manifest["artifact_file_ids"]["poster"] == row.files[2]["file_id"]
    kinds = {e.kind for e in store.list_events(lead, limit=50)}
    assert {"decision.opened", "pr.discovered", "pr.claim", "preview.reported"} <= kinds
    assert "demo.published" in kinds

    # The cockpit plays the video back from the server, seekable by byte range.
    video_url = f"/v1/factory/demos/{row.id}/files/{row.files[0]['file_id']}"
    full = stack.client.get(video_url)
    part = stack.client.get(video_url, headers={"Range": "bytes=0-99"})
    assert full.status_code == 200 and full.content == video.read_bytes()
    assert full.headers["content-type"] == "video/webm"
    assert part.status_code == 206 and part.content == video.read_bytes()[:100]
