"""Relaunch on a new sandbox: the cockpit control, the decision option and the texts.

``new_sandbox`` drops the PL's managed sandbox and provisions a fresh one for
the same session (project, charter, labels and records stay), unlike Restart
and "Relaunch with spec mode", which keep the sandbox.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.entities.factory import EventDraft
from omnigent.server.factory import launch_watch, server_decisions
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.launch_watch import LaunchWatch
from omnigent.server.factory.new_sandbox import (
    BRIEF_DELIVERED,
    BRIEF_NONE,
    BRIEF_PENDING,
    brief_state,
    launch_in_flight,
    relaunch_on_new_sandbox,
)
from omnigent.server.managed_host_isolation import MANAGED_HOST_CLAIM_LABEL_KEY
from omnigent.server.routes._sessions.helpers import _publish_sandbox_status_impl
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.sandbox_helpers import (
    SandboxRig,
    clean_launch_caches,
    install_sandbox_rig,
)

REPO = "caffeinelabs/app"


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in build_cockpit(world, db_uri, monkeypatch):
        yield value


@pytest.fixture()
def rig(cockpit: Cockpit, db_uri: str, monkeypatch: pytest.MonkeyPatch) -> SandboxRig:
    return install_sandbox_rig(cockpit.app.state, db_uri, monkeypatch)


@pytest.fixture(autouse=True)
def _caches() -> Iterator[None]:
    yield from clean_launch_caches()


async def _control(
    cockpit: Cockpit, target: str = "project:api-version", note: str | None = None
) -> Any:
    body: dict[str, Any] = {"action": "new_sandbox", "target": target}
    if note is not None:
        body["note"] = note
    return await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/control", body)


async def _comes_up(cockpit: Cockpit, rig: SandboxRig) -> LaunchWatch:
    """The new sandbox came up; :returns: the launch watch, after one pass."""
    pl = cockpit.world.id("pl")
    _publish_sandbox_status_impl(pl, "ready")
    rig.tracker.finish(pl)
    rig.bind_host(cockpit.world.store, pl)
    watch = LaunchWatch(cockpit.app.state)
    await watch.tick([cockpit.world.get("pl")], [], time.time())
    return watch


def _brief_event(cockpit: Cockpit, kind: str, pl: str, mail_id: str = "mail_brief") -> EventDraft:
    return EventDraft(
        factory_id=cockpit.fid,
        kind=kind,
        summary="brief",
        to_id=pl,
        subject=f"mail:{mail_id}",
        data={"kind": "brief", "mail_id": mail_id},
    )


# ── The cockpit control ────────────────────────────────────────


async def test_a_new_sandbox_replaces_the_pls_sandbox_and_keeps_the_session(
    cockpit: Cockpit, rig: SandboxRig
) -> None:
    w = cockpit.world
    pl = w.id("pl")
    old = rig.bind_host(w.store, pl)
    cockpit.store.append_event(_brief_event(cockpit, "message.queued", pl))
    cockpit.store.append_event(_brief_event(cockpit, "message.delivered", pl))

    resp = await _control(cockpit, note="The node pool was full.")

    assert resp.status_code == 202, resp.text
    # The old sandbox is torn down, the session unbound and relaunched in place.
    assert rig.terminated == [old]
    assert rig.kicks == [pl]
    after = w.get("pl")
    assert after.host_id is None
    # A healthy PL starts a new episode: try 1, on a new sandbox, not the server's retry.
    assert after.labels[F.LABEL_LAUNCH_TRIES] == "1"
    assert after.labels[F.LABEL_LAUNCH_NEW].isdigit()
    assert F.LABEL_LAUNCH_AUTO not in after.labels
    assert after.labels[F.LABEL_PROJECT] == "api-version"
    # Nothing is sent while the sandbox starts: a slow launch must not bounce it.
    assert cockpit.mailer.texts_to(pl) == []

    await _comes_up(cockpit, rig)

    # The PL already had its brief: it is told to rebuild from the server.
    [text] = cockpit.mailer.texts_to(pl)
    assert text.startswith("[Factory control · NEW SANDBOX · by alice] You run on a new sandbox")
    assert 'sys_factory_task(op="ready")' in text
    assert text.endswith("\nThe node pool was full.")
    [event] = resp.json()["events"]
    assert event["kind"] == "control.new_sandbox"
    assert event["summary"] == "alice relaunched project api-version on a new sandbox"
    [logged] = cockpit.events("member.new_sandbox")
    assert logged.data["tries"] == 1 and logged.data["auto"] is False
    assert logged.data["old_host_id"] == old
    assert logged.summary == 'alice relaunched "PL · api-version" on a new sandbox'
    assert F.LABEL_LAUNCH_TRIES not in w.get("pl").labels
    [up] = cockpit.events("member.launch_recovered")
    assert up.summary == '"PL · api-version" is up on a new sandbox'


async def test_a_pl_that_never_got_its_brief_gets_it_again(
    cockpit: Cockpit, rig: SandboxRig
) -> None:
    """A PL whose first sandbox never came up bounced its brief: the new one starts from it."""
    pl = cockpit.world.id("pl")
    cockpit.store.upsert_project(
        cockpit.fid,
        "api-version",
        title="API version",
        repo=REPO,
        charter={"goal": "Serve the API version", "dod": ["GET /version"]},
    )
    cockpit.store.append_event(_brief_event(cockpit, "message.queued", pl))
    cockpit.store.append_event(_brief_event(cockpit, "message.bounced", pl))
    rig.fail_launch(pl)

    resp = await _control(cockpit)
    assert resp.status_code == 202, resp.text
    await _comes_up(cockpit, rig)

    [item] = cockpit.mailer.to(pl)
    assert item.kind == "brief"
    assert item.text.startswith("[Factory brief · from alice via a new sandbox · id=")
    assert "Serve the API version" in item.text


async def test_a_brief_still_on_its_way_is_not_sent_twice(
    cockpit: Cockpit, rig: SandboxRig
) -> None:
    w = cockpit.world
    pl = w.id("pl")
    cockpit.store.upsert_project(cockpit.fid, "api-version", title="API version", repo=REPO)
    cockpit.store.append_event(_brief_event(cockpit, "message.queued", pl))
    rig.fail_launch(pl)
    resp = await _control(cockpit)
    assert resp.status_code == 202, resp.text

    watch = await _comes_up(cockpit, rig)

    # Up, but its first brief is still being retried: it gets that one.
    assert cockpit.mailer.to(pl) == []
    assert w.get("pl").labels[F.LABEL_LAUNCH_TRIES] == "2"

    # It never arrived: a new brief goes out, and the episode ends.
    await watch.tick([w.get("pl")], [], time.time() + launch_watch.BRIEF_WAIT_S + 1)
    [item] = cockpit.mailer.to(pl)
    assert item.kind == "brief"
    assert F.LABEL_LAUNCH_TRIES not in w.get("pl").labels


def test_brief_state_reads_the_mail_log(cockpit: Cockpit) -> None:
    pl = cockpit.world.get("pl")
    assert brief_state(cockpit.deps, cockpit.fid, pl) == BRIEF_NONE
    cockpit.store.append_event(_brief_event(cockpit, "message.queued", pl.id))
    assert brief_state(cockpit.deps, cockpit.fid, pl) == BRIEF_PENDING
    cockpit.store.append_event(_brief_event(cockpit, "message.bounced", pl.id))
    assert brief_state(cockpit.deps, cockpit.fid, pl) == BRIEF_NONE
    cockpit.store.append_event(_brief_event(cockpit, "message.delivered", pl.id, "mail_2"))
    assert brief_state(cockpit.deps, cockpit.fid, pl) == BRIEF_DELIVERED


async def test_a_borrowed_sandbox_is_left_running(cockpit: Cockpit, rig: SandboxRig) -> None:
    """Only the sandbox provisioned for the PL goes; a borrowed binding is just cleared."""
    w = cockpit.world
    pl = w.id("pl")
    rig.bind_host(w.store, pl, claim=False)
    other = rig.bind_host(w.store, w.id("lead_b"))
    w.store.set_host_id(pl, other, "/home/omnigent/workspace")
    w.store.set_labels(pl, {MANAGED_HOST_CLAIM_LABEL_KEY: "someone-else"})

    resp = await _control(cockpit)

    assert resp.status_code == 202, resp.text
    assert rig.terminated == []
    assert w.get("pl").host_id is None
    assert rig.kicks == [pl]


async def test_a_starting_sandbox_is_not_replaced(cockpit: Cockpit, rig: SandboxRig) -> None:
    pl = cockpit.world.id("pl")
    rig.tracker.begin(pl)

    resp = await _control(cockpit)

    assert (resp.status_code, error_code(resp)) == (409, "launch_in_flight")
    assert rig.kicks == []


async def test_a_server_without_managed_sandboxes_refuses(cockpit: Cockpit) -> None:
    resp = await _control(cockpit)

    assert (resp.status_code, error_code(resp)) == (409, "no_managed_sandboxes")


async def test_an_agent_on_someone_elses_host_is_refused(
    cockpit: Cockpit, rig: SandboxRig
) -> None:
    w = cockpit.world
    pl = w.id("pl")
    laptop = uuid.uuid4().hex
    rig.host_store.upsert_on_connect(laptop, "laptop", "alice")
    w.store.set_host_id(pl, laptop, "/Users/alice/app")

    resp = await _control(cockpit)

    assert (resp.status_code, error_code(resp)) == (409, "not_managed")
    assert w.get("pl").host_id == laptop


async def test_only_owners_relaunch_on_a_new_sandbox(cockpit: Cockpit, rig: SandboxRig) -> None:
    resp = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/control",
        {"action": "new_sandbox", "target": "project:api-version"},
        user="carol",
    )

    assert resp.status_code == 403, resp.text
    assert rig.kicks == []


async def test_the_whole_factory_cannot_get_new_sandboxes(
    cockpit: Cockpit, rig: SandboxRig
) -> None:
    resp = await _control(cockpit, target="factory")

    assert (resp.status_code, error_code(resp)) == (400, "invalid_target")


# ── The failed-PL decision ─────────────────────────────────────


async def test_answering_try_a_new_sandbox_relaunches_the_pl(
    cockpit: Cockpit, rig: SandboxRig
) -> None:
    w = cockpit.world
    pl = w.get("pl")
    w.store.set_labels(pl.id, {F.LABEL_LAUNCH_TRIES: "2"})
    rig.fail_launch(pl.id, "Kubernetes sandbox pod 'x' did not start within 300s")
    opened = await server_decisions.open_launch_failed_decision(
        cockpit.app.state, w.get("pl"), cockpit.fid, 2, "did not start within 300s"
    )
    assert opened is not None
    decision, _ = opened
    assert decision.question == '"PL · api-version" could not start its sandbox twice'
    assert decision.assignee == "owners" and decision.blocking
    assert [(o["id"], o["action"], o.get("recommended")) for o in decision.options] == [
        ("new_sandbox", "new_sandbox", True),
        ("pause", "pause", False),
        ("dismiss", "dismiss", False),
    ]

    resp = await cockpit.post(
        f"/v1/factory/decisions/{decision.id}/answer", {"option_id": "new_sandbox"}
    )

    assert resp.status_code == 200, resp.text
    assert rig.kicks == [pl.id]
    assert w.get("pl").labels[F.LABEL_LAUNCH_TRIES] == "3"
    assert [e.kind for e in cockpit.events("control.new_sandbox")] == ["control.new_sandbox"]


async def test_the_launch_is_claimed_before_the_old_sandbox_goes(
    cockpit: Cockpit, rig: SandboxRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A message or a second relaunch while the old sandbox is deleted waits for the new one."""
    from omnigent.server import managed_hosts

    w = cockpit.world
    pl = w.id("pl")
    rig.bind_host(w.store, pl)
    seen: dict[str, Any] = {}

    async def _terminate_and_race(host: Any, host_store: Any, _config: Any) -> None:
        # Claimed and counted before the old sandbox goes.
        seen["claim"] = rig.tracker.get(pl)
        seen["in_flight"] = launch_in_flight(cockpit.app.state, pl)
        seen["tries"] = w.get("pl").labels.get(F.LABEL_LAUNCH_TRIES)
        with pytest.raises(CockpitError) as raced:
            await relaunch_on_new_sandbox(cockpit.app.state, w.get("pl"), by="bob")
        seen["raced"] = raced.value.code
        host_store.delete_host(host.host_id)

    monkeypatch.setattr(managed_hosts, "terminate_managed_host", _terminate_and_race)

    resp = await _control(cockpit)

    assert resp.status_code == 202, resp.text
    assert seen["in_flight"] is True and seen["tries"] == "1"
    assert seen["raced"] == "launch_in_flight"
    # One launch, settling the very entry a parked message waits on.
    assert rig.kicks == [pl]
    assert rig.tracker.get(pl) is seen["claim"]


async def test_a_failed_drop_settles_the_claim(
    cockpit: Cockpit, rig: SandboxRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.server import managed_hosts

    w = cockpit.world
    pl = w.id("pl")
    rig.bind_host(w.store, pl)

    async def _boom(_host: Any, _host_store: Any, _config: Any) -> None:
        raise RuntimeError("kube API down")

    monkeypatch.setattr(managed_hosts, "terminate_managed_host", _boom)

    with pytest.raises(RuntimeError):
        await relaunch_on_new_sandbox(cockpit.app.state, w.get("pl"), by="alice")

    launch = rig.tracker.get(pl)
    assert launch is not None and launch.settled.is_set() and launch.error
    assert rig.kicks == []


async def test_the_server_never_replaces_a_sandbox_on_its_own(
    cockpit: Cockpit, rig: SandboxRig
) -> None:
    w = cockpit.world
    pl = w.id("pl")
    rig.bind_host(w.store, pl, online=False)
    rig.fail_launch(pl, "did not start within 900s", wake=True)

    with pytest.raises(CockpitError) as refused:
        await relaunch_on_new_sandbox(cockpit.app.state, w.get("pl"), by="server", auto=True)

    assert refused.value.code == "has_sandbox"
    assert rig.terminated == [] and rig.kicks == []


# ── What owners are told after a failed relaunch ───────────────


def _launch_decisions(cockpit: Cockpit) -> list[Any]:
    return [
        d
        for d in cockpit.store.list_decisions(factory_id=cockpit.fid, limit=50)
        if d.dedupe_key.startswith("launch_failed:")
    ]


async def _fails(cockpit: Cockpit, rig: SandboxRig, watch: LaunchWatch, now: float) -> float:
    """The running launch fails; the watch takes two passes RETRY_DELAY_S apart."""
    pl = cockpit.world.id("pl")
    rig.tracker.fail(pl, "did not start within 900s")
    _publish_sandbox_status_impl(pl, "failed", "did not start within 900s")
    await watch.tick([cockpit.world.get("pl")], [], now)
    now += launch_watch.RETRY_DELAY_S + 30
    await watch.tick([cockpit.world.get("pl")], [], now)
    return now


async def test_an_owners_relaunch_that_fails_is_retried_once_then_asks(
    cockpit: Cockpit, rig: SandboxRig
) -> None:
    """An owner's first new sandbox on a healthy PL fails once: the server still retries once."""
    w = cockpit.world
    pl = w.id("pl")
    old = rig.bind_host(w.store, pl)
    resp = await _control(cockpit)
    assert resp.status_code == 202, resp.text
    watch = LaunchWatch(cockpit.app.state)

    now = await _fails(cockpit, rig, watch, time.time())
    assert rig.kicks == [pl, pl] and rig.terminated == [old]
    assert w.get("pl").labels[F.LABEL_LAUNCH_TRIES] == "2"
    assert _launch_decisions(cockpit) == []

    await _fails(cockpit, rig, watch, now)
    assert rig.kicks == [pl, pl]
    [decision] = _launch_decisions(cockpit)
    assert decision.question == '"PL · api-version" could not start its sandbox twice'
    assert "The server already tried a new sandbox once" in (decision.context or "")
    assert "after alice" not in (decision.context or "")


async def test_an_owners_relaunch_after_the_servers_retry_says_so(
    cockpit: Cockpit, rig: SandboxRig
) -> None:
    w = cockpit.world
    pl = w.id("pl")
    w.store.set_labels(
        pl, {F.LABEL_LAUNCH_TRIES: "2", F.LABEL_LAUNCH_AUTO: "1", F.LABEL_LAUNCH_NEW: "1"}
    )
    rig.fail_launch(pl, "did not start within 300s")
    resp = await _control(cockpit)
    assert resp.status_code == 202, resp.text
    assert w.get("pl").labels[F.LABEL_LAUNCH_TRIES] == "3"

    await _fails(cockpit, rig, LaunchWatch(cockpit.app.state), time.time())

    assert rig.kicks == [pl]  # no second retry of the server's own
    [decision] = _launch_decisions(cockpit)
    assert decision.question == '"PL · api-version" could not start its sandbox 3 times'
    assert "It failed after alice relaunched it on a new sandbox." in (decision.context or "")
