"""S11 launch watch: one retry on a new sandbox, then owners decide; finished PLs let go.

The 2026-11-02 incident: a PL's pod did not start within 300 s (cluster full,
then a cold 3.9 GB image pull), and every Restart reused the failed sandbox.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from omnigent import factory as F
from omnigent.server.factory import launch_watch
from omnigent.server.factory.delivery import DeliveryOutcome
from omnigent.server.managed_hosts import MANAGED_REPO_LABEL_KEY
from omnigent.server.routes._sessions.common import _session_status_cache
from omnigent.server.routes._sessions.helpers import _publish_sandbox_status_impl
from tests.server.factory.conftest import Supervision
from tests.server.factory.sandbox_helpers import (
    SandboxRig,
    clean_launch_caches,
    install_sandbox_rig,
)

ERROR = (
    "managed sandbox host startup failed: Kubernetes sandbox pod 'omnigent-managed-1' "
    "did not start within 300s (last phase 'Pending'; last reason: PodInitializing)"
)
PL_HEARTBEAT_S = 600


@pytest.fixture()
def rig(supervision: Supervision, db_uri: str, monkeypatch: pytest.MonkeyPatch) -> SandboxRig:
    return install_sandbox_rig(supervision.world.app_state, db_uri, monkeypatch)


@pytest.fixture(autouse=True)
def _caches() -> Iterator[None]:
    yield from clean_launch_caches()


def _events(s: Supervision, kind: str) -> list:
    store = s.world.deps.factory_store
    return store.find_events(s.world.id("lead_a"), kind, 0)


def _launch_decisions(s: Supervision) -> list:
    store = s.world.deps.factory_store
    return [
        d
        for d in store.list_decisions(factory_id=s.world.id("lead_a"), limit=50)
        if d.dedupe_key.startswith("launch_failed:")
    ]


# ── one retry on a new sandbox ─────────────────────────────────


async def test_a_failed_launch_is_retried_once_on_a_new_sandbox(
    supervision: Supervision, rig: SandboxRig
) -> None:
    s, w = supervision, supervision.world
    pl = w.id("pl")
    rig.fail_launch(pl, ERROR)

    await s.tick()
    # Logged at once; the retry waits a short delay.
    [failed] = _events(s, "member.launch_failed")
    assert failed.data == {"tries": 1, "error": ERROR, "lost": False, "retry": True}
    assert "retrying on a new sandbox" in failed.summary
    assert rig.kicks == []

    # The failure was stamped on the wall clock: leave room for a slow test host.
    s.clock.advance(launch_watch.RETRY_DELAY_S + 30)
    await s.tick()
    assert rig.kicks == [pl]
    assert w.get("pl").labels[F.LABEL_LAUNCH_TRIES] == "2"
    [relaunched] = _events(s, "member.new_sandbox")
    assert relaunched.data["auto"] is True and relaunched.data["reason"] == ERROR
    assert (
        relaunched.summary == 'the server relaunched "PL · api-version" on a new sandbox (try 2)'
    )

    # The new sandbox is starting: nothing more until it is up or fails.
    s.clock.advance(120)
    await s.tick()
    assert rig.kicks == [pl]
    assert len(_events(s, "member.launch_failed")) == 1
    assert s.deliver.texts_to(pl) == []

    # Up: it is told where it stands, once.
    _publish_sandbox_status_impl(pl, "ready")
    rig.tracker.finish(pl)
    rig.bind_host(w.store, pl)
    await s.tick()
    await s.tick()
    [text] = s.deliver.texts_to(pl)
    assert text.startswith("[Factory control · NEW SANDBOX · by server] You run on a new sandbox")
    assert F.LABEL_LAUNCH_TRIES not in w.get("pl").labels


async def test_the_retry_failing_too_asks_owners_once(
    supervision: Supervision, rig: SandboxRig
) -> None:
    s, w = supervision, supervision.world
    pl = w.id("pl")
    w.store.set_labels(pl, {F.LABEL_LAUNCH_TRIES: "2"})
    rig.fail_launch(pl, ERROR)

    for _ in range(3):
        await s.tick()
        s.clock.advance(300)

    assert rig.kicks == []
    [decision] = _launch_decisions(s)
    assert decision.status == "open" and decision.source == "relaunch_failed"
    assert decision.question == '"PL · api-version" could not start its sandbox twice'
    assert ERROR in (decision.context or "")
    assert w.get("pl").labels[F.LABEL_LAUNCH_ASKED] == "2"
    [failed] = _events(s, "member.launch_failed")
    assert failed.data["retry"] is False and "owners decide" in failed.summary


async def test_a_sandbox_that_comes_up_ends_the_episode(
    supervision: Supervision, rig: SandboxRig
) -> None:
    s, w = supervision, supervision.world
    pl = w.id("pl")
    w.store.set_labels(pl, {F.LABEL_LAUNCH_TRIES: "2"})
    rig.fail_launch(pl, ERROR)
    await s.tick()
    [asked] = _launch_decisions(s)

    # An owner's "Try a new sandbox" came up on the third try.
    w.store.set_labels(pl, {F.LABEL_LAUNCH_TRIES: "3"})
    _publish_sandbox_status_impl(pl, "ready")
    rig.tracker.finish(pl)
    rig.bind_host(w.store, pl)
    await s.tick()

    labels = w.get("pl").labels
    assert F.LABEL_LAUNCH_TRIES not in labels and F.LABEL_LAUNCH_ASKED not in labels
    [expired] = _launch_decisions(s)
    assert expired.id == asked.id and expired.status == "expired"
    [up] = _events(s, "member.launch_recovered")
    assert up.summary == '"PL · api-version" is up on a new sandbox (try 3)'


async def test_a_launch_a_restart_cut_off_is_retried(
    supervision: Supervision, rig: SandboxRig
) -> None:
    """No host, no runner, no launch on record: the server restarted mid-launch."""
    s, w = supervision, supervision.world
    pl = w.make_session(
        "pl2",
        agent="pl",
        title="PL · build-tier-drafts",
        labels={
            F.LABEL_ID: w.id("lead_a"),
            F.LABEL_ROLE: "project_lead",
            F.LABEL_PROJECT: "build-tier-drafts",
            F.LABEL_PARENT: w.id("lead_a"),
            F.LABEL_STATE: "active",
            f"{MANAGED_REPO_LABEL_KEY}.0": "https://github.com/caffeinelabs/app#main",
        },
    )

    await s.tick()
    assert _events(s, "member.launch_failed") == []

    s.clock.advance(launch_watch.LOST_AFTER_S + 1)
    await s.tick()
    [lost] = _events(s, "member.launch_failed")
    assert lost.data["lost"] is True
    s.clock.advance(launch_watch.RETRY_DELAY_S + 1)
    await s.tick()
    assert rig.kicks == [pl.id]


@pytest.mark.parametrize("state", [F.STATE_PAUSED, F.STATE_DONE])
async def test_a_paused_or_stopped_member_is_not_retried(
    supervision: Supervision, rig: SandboxRig, state: str
) -> None:
    s, w = supervision, supervision.world
    pl = w.id("pl")
    w.store.set_labels(pl, {F.LABEL_STATE: state})
    rig.fail_launch(pl, ERROR)

    await s.tick()
    s.clock.advance(launch_watch.RETRY_DELAY_S + 1)
    await s.tick()

    assert rig.kicks == [] and _events(s, "member.launch_failed") == []


async def test_no_heartbeat_goes_to_a_member_whose_launch_is_pending(
    supervision: Supervision, rig: SandboxRig
) -> None:
    s, w = supervision, supervision.world
    pl = w.id("pl")
    w.store.set_labels(pl, {F.LABEL_LAUNCH_TRIES: "2"})
    rig.fail_launch(pl, ERROR)
    s.deliver.outcomes[pl] = [DeliveryOutcome(False, "runner_unavailable")] * 6

    s.clock.advance(PL_HEARTBEAT_S)
    await s.tick()

    assert [t for t in s.deliver.texts_to(pl) if t.startswith("[Factory heartbeat")] == []
    # The launch decision, not the 3× heartbeat one, is what owners get.
    store = w.deps.factory_store
    keys = [d.dedupe_key for d in store.list_decisions(factory_id=w.id("lead_a"), limit=50)]
    assert keys == [f"launch_failed:{pl}"]


# ── finished PLs let their sandbox go ──────────────────────────


def _close_project(s: Supervision, status: str = "cancelled") -> None:
    store = s.world.deps.factory_store
    row = store.upsert_project(s.world.id("lead_a"), "api-version", title="api-version")
    store.update_project(row.id, status=status)


async def test_a_cancelled_projects_idle_pl_releases_its_sandbox(
    supervision: Supervision, rig: SandboxRig
) -> None:
    s, w = supervision, supervision.world
    pl = w.id("pl")
    host = rig.bind_host(w.store, pl)
    w.store.set_labels(pl, {F.LABEL_STATE: F.STATE_DONE})
    _close_project(s)

    await s.tick()
    assert rig.stopped == []  # its final report may still be on its way

    s.clock.advance(launch_watch.RELEASE_GRACE_S + 1)
    await s.tick()
    assert rig.stopped == [host]
    assert w.get("pl").labels[F.LABEL_SANDBOX_RELEASED_AT] == str(int(s.clock.now))
    [released] = _events(s, "member.sandbox_released")
    assert released.summary == (
        'Stopped the idle sandbox of "PL · api-version" (project cancelled); a message wakes it'
    )

    # Stopped once: its host is offline now.
    s.clock.advance(launch_watch.RELEASE_GRACE_S + 1)
    await s.tick()
    assert rig.stopped == [host]


async def test_a_pl_that_finished_releases_its_sandbox(
    supervision: Supervision, rig: SandboxRig
) -> None:
    s, w = supervision, supervision.world
    pl = w.id("pl")
    host = rig.bind_host(w.store, pl)
    w.store.set_labels(pl, {F.LABEL_STATE: F.STATE_DONE})

    await s.tick()
    s.clock.advance(launch_watch.RELEASE_GRACE_S + 1)
    await s.tick()

    assert rig.stopped == [host]
    [released] = _events(s, "member.sandbox_released")
    assert released.data["why"] == "PL finished"


async def test_a_busy_or_active_pl_keeps_its_sandbox(
    supervision: Supervision, rig: SandboxRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    s, w = supervision, supervision.world
    pl = w.id("pl")
    rig.bind_host(w.store, pl)

    # Active, project open: nothing to release.
    s.clock.advance(launch_watch.RELEASE_GRACE_S + 1)
    await s.tick()
    assert rig.stopped == []

    # Cancelled, but still writing its final report: the grace restarts once it is idle.
    _close_project(s)
    w.store.set_labels(pl, {F.LABEL_STATE: F.STATE_DONE})
    monkeypatch.setitem(_session_status_cache, pl, "running")
    for _ in range(3):
        s.clock.advance(launch_watch.RELEASE_GRACE_S + 1)
        await s.tick()
    assert rig.stopped == []

    monkeypatch.setitem(_session_status_cache, pl, "idle")
    await s.tick()
    assert rig.stopped == []
    s.clock.advance(launch_watch.RELEASE_GRACE_S + 1)
    await s.tick()
    assert len(rig.stopped) == 1


async def test_a_pl_keeping_a_preview_up_for_review_keeps_its_sandbox(
    supervision: Supervision, rig: SandboxRig
) -> None:
    s, w = supervision, supervision.world
    pl = w.id("pl")
    rig.bind_host(w.store, pl)
    w.store.set_labels(pl, {F.LABEL_STATE: F.STATE_PAUSED, F.LABEL_KEEP_PREVIEWS: "1"})
    _close_project(s)
    w.deps.factory_store.upsert_pr(
        w.id("lead_a"),
        "caffeinelabs/app",
        9631,
        project_slug="api-version",
        preview={"state": "ready", "url": "https://p9631.example"},
    )

    s.clock.advance(launch_watch.RELEASE_GRACE_S + 1)
    await s.tick()
    s.clock.advance(launch_watch.RELEASE_GRACE_S + 1)
    await s.tick()

    assert rig.stopped == []
