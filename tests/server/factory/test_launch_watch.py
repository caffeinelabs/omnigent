"""S13 launch watch: one retry, then owners decide; finished PLs let go.

The 2026-11-02 incident: a PL's pod did not start within 300 s (cluster full,
then a cold 3.9 GB image pull), and every Restart reused the failed sandbox.
A first launch is retried on a new sandbox; a member's own sandbox (with its
workspace) is only ever woken again, never deleted by the server.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from omnigent import factory as F
from omnigent.entities import Conversation
from omnigent.server.factory import launch_watch
from omnigent.server.factory.delivery import DeliveryOutcome
from omnigent.server.managed_host_isolation import MANAGED_HOST_CLAIM_LABEL_KEY
from omnigent.server.managed_hosts import MANAGED_REPO_LABEL_KEY
from omnigent.server.routes._sessions.common import (
    _session_sandbox_status_cache,
    _session_status_cache,
)
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


# The server already retried this start on a new sandbox once.
RETRIED = {F.LABEL_LAUNCH_TRIES: "2", F.LABEL_LAUNCH_AUTO: "1", F.LABEL_LAUNCH_NEW: "1"}


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
    assert failed.data == {
        "tries": 1,
        "error": ERROR,
        "lost": False,
        "wake": False,
        "retry": True,
    }
    assert "retrying on a new sandbox" in failed.summary
    assert rig.kicks == []

    # The failure was stamped on the wall clock: leave room for a slow test host.
    s.clock.advance(launch_watch.RETRY_DELAY_S + 30)
    await s.tick()
    assert rig.kicks == [pl] and rig.terminated == [] and rig.wakes == []
    labels = w.get("pl").labels
    assert labels[F.LABEL_LAUNCH_TRIES] == "2"
    assert labels[F.LABEL_LAUNCH_AUTO] == "1" and labels[F.LABEL_LAUNCH_NEW].isdigit()
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
    labels = w.get("pl").labels
    assert not {F.LABEL_LAUNCH_TRIES, F.LABEL_LAUNCH_AUTO, F.LABEL_LAUNCH_NEW} & set(labels)


async def test_the_retry_failing_too_asks_owners_once(
    supervision: Supervision, rig: SandboxRig
) -> None:
    s, w = supervision, supervision.world
    pl = w.id("pl")
    w.store.set_labels(pl, RETRIED)
    rig.fail_launch(pl, ERROR)

    for _ in range(3):
        await s.tick()
        s.clock.advance(300)

    assert rig.kicks == []
    [decision] = _launch_decisions(s)
    assert decision.status == "open" and decision.source == "relaunch_failed"
    assert decision.question == '"PL · api-version" could not start its sandbox twice'
    assert ERROR in (decision.context or "")
    assert "The server already tried a new sandbox once" in (decision.context or "")
    assert [o["id"] for o in decision.options] == ["new_sandbox", "pause", "dismiss"]
    assert w.get("pl").labels[F.LABEL_LAUNCH_ASKED] == "2"
    [failed] = _events(s, "member.launch_failed")
    assert failed.data["retry"] is False and "owners decide" in failed.summary


async def test_a_sandbox_that_comes_up_ends_the_episode(
    supervision: Supervision, rig: SandboxRig
) -> None:
    s, w = supervision, supervision.world
    pl = w.id("pl")
    w.store.set_labels(pl, RETRIED)
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
    assert not {F.LABEL_LAUNCH_TRIES, F.LABEL_LAUNCH_ASKED, F.LABEL_LAUNCH_AUTO} & set(labels)
    [expired] = _launch_decisions(s)
    assert expired.id == asked.id and expired.status == "expired"
    [up] = _events(s, "member.launch_recovered")
    assert up.summary == '"PL · api-version" is up on a new sandbox (try 3)'


def _managed_pl(s: Supervision, **labels: str) -> Conversation:
    """A PL created for a managed sandbox that has no host or runner yet."""
    w = s.world
    return w.make_session(
        "pl2",
        agent="pl",
        title="PL · build-tier-drafts",
        labels={
            F.LABEL_ID: w.id("lead_a"),
            F.LABEL_ROLE: "project_lead",
            F.LABEL_PROJECT: "build-tier-drafts",
            F.LABEL_PARENT: w.id("lead_a"),
            F.LABEL_STATE: "active",
            f"{MANAGED_REPO_LABEL_KEY}.0": "https://github.com/sample-org/sample-app#main",
            **labels,
        },
    )


async def test_a_launch_a_restart_cut_off_is_retried(
    supervision: Supervision, rig: SandboxRig
) -> None:
    """No host, no runner, no launch on record: the server restarted mid-launch."""
    s, w = supervision, supervision.world
    pl = _managed_pl(s)

    await s.tick()
    assert _events(s, "member.launch_failed") == []

    s.clock.advance(launch_watch.LOST_AFTER_S + 1)
    await s.tick()
    [lost] = _events(s, "member.launch_failed")
    assert lost.data["lost"] is True
    s.clock.advance(launch_watch.RETRY_DELAY_S + 1)
    await s.tick()
    assert rig.kicks == [pl.id]
    # The cut-off launch was try 1: the retry is try 2.
    assert w.get("pl2").labels[F.LABEL_LAUNCH_TRIES] == "2"


async def test_a_relaunch_a_restart_cut_off_is_timed_from_the_relaunch(
    supervision: Supervision, rig: SandboxRig
) -> None:
    """An old PL an owner relaunched just before a server restart is not lost at once."""
    s, w = supervision, supervision.world
    pl = _managed_pl(s).id
    s.clock.advance(3 * 86400)  # the PL is days old
    # The owner's relaunch, counted before its launch began.
    w.store.set_labels(pl, {F.LABEL_LAUNCH_TRIES: "1", F.LABEL_LAUNCH_NEW: str(int(s.clock.now))})

    await s.tick()
    assert _events(s, "member.launch_failed") == []

    s.clock.advance(launch_watch.LOST_AFTER_S + 1)
    await s.tick()
    [lost] = _events(s, "member.launch_failed")
    assert lost.data["lost"] is True and lost.data["tries"] == 1
    s.clock.advance(launch_watch.RETRY_DELAY_S + 1)
    await s.tick()
    assert rig.kicks == [pl] and rig.terminated == []
    assert w.get("pl2").labels[F.LABEL_LAUNCH_TRIES] == "2"


async def test_a_lost_launch_after_the_retry_says_a_restart_cut_it_off(
    supervision: Supervision, rig: SandboxRig
) -> None:
    s = supervision
    _managed_pl(s, **RETRIED)
    s.clock.advance(launch_watch.LOST_AFTER_S + 1)
    await s.tick()

    assert rig.kicks == []
    [decision] = _launch_decisions(s)
    assert decision.question == (
        '"PL · build-tier-drafts" sandbox launch was cut off by a server restart'
    )
    assert "could not start" not in decision.question


# ── a member's own sandbox is woken again, never replaced ───────


async def test_a_failed_wake_is_retried_on_the_same_sandbox_and_never_deleted(
    supervision: Supervision, rig: SandboxRig
) -> None:
    """A Resumed PL on a full cluster: its suspended sandbox (and workspace) did not wake."""
    s, w = supervision, supervision.world
    pl = w.id("pl")
    host = rig.bind_host(w.store, pl, online=False)
    rig.fail_launch(pl, "managed sandbox did not start within 900s", wake=True)

    await s.tick()
    [failed] = _events(s, "member.launch_failed")
    assert failed.data["wake"] is True and failed.data["retry"] is True
    assert "did not wake (try 1), waking it again" in failed.summary

    s.clock.advance(launch_watch.RETRY_DELAY_S + 30)
    await s.tick()
    assert rig.wakes == [pl] and rig.kicks == [] and rig.terminated == []
    labels = w.get("pl").labels
    assert (labels[F.LABEL_LAUNCH_TRIES], labels[F.LABEL_LAUNCH_AUTO]) == ("2", "1")
    assert F.LABEL_LAUNCH_NEW not in labels and w.get("pl").host_id == host
    [woken] = _events(s, "member.wake_retried")
    assert woken.summary == (
        'the server is waking "PL · api-version" again on its own sandbox (try 2)'
    )

    # The second wake fails too: owners decide; Restart (same sandbox) comes first.
    rig.fail_launch(pl, "managed sandbox did not start within 900s", wake=True)
    for _ in range(3):
        await s.tick()
        s.clock.advance(300)
    assert rig.wakes == [pl] and rig.kicks == [] and rig.terminated == []
    [decision] = _launch_decisions(s)
    assert decision.question == '"PL · api-version" could not wake its sandbox twice'
    assert "The server already woke it once more" in (decision.context or "")
    assert [(o["id"], o.get("recommended")) for o in decision.options] == [
        ("restart", True),
        ("new_sandbox", False),
        ("pause", False),
        ("dismiss", False),
    ]

    # Up again: the episode ends; it kept its files, so no NEW SANDBOX text.
    rig.come_up(pl)
    rig.host_store.upsert_on_connect(host, "managed", "alice")
    await s.tick()
    labels = w.get("pl").labels
    assert not {F.LABEL_LAUNCH_TRIES, F.LABEL_LAUNCH_AUTO, F.LABEL_LAUNCH_ASKED} & set(labels)
    assert [d.status for d in _launch_decisions(s)] == ["expired"]
    assert [t for t in s.deliver.texts_to(pl) if "NEW SANDBOX" in t] == []
    [up] = _events(s, "member.launch_recovered")
    assert up.summary == '"PL · api-version" is up again (try 2)'


async def test_a_failure_of_a_sandbox_that_came_back_is_cleared(
    supervision: Supervision, rig: SandboxRig
) -> None:
    """A wake's runner did not connect, then a message brought it back (no "ready" stage)."""
    s, w = supervision, supervision.world
    pl = w.id("pl")
    rig.bind_host(w.store, pl, online=True)
    rig.fail_launch(pl, "managed runner did not connect", wake=True)
    assert launch_watch.launch_pending(w.get("pl"))

    for _ in range(3):
        await s.tick()
        s.clock.advance(launch_watch.RETRY_DELAY_S + 30)

    assert rig.terminated == [] and rig.kicks == [] and rig.wakes == []
    assert pl not in _session_sandbox_status_cache
    assert not launch_watch.launch_pending(w.get("pl"))
    assert _events(s, "member.launch_failed") == [] and _launch_decisions(s) == []


async def test_a_retry_skips_a_sandbox_that_came_back_meanwhile(
    supervision: Supervision, rig: SandboxRig
) -> None:
    s, w = supervision, supervision.world
    pl = w.id("pl")
    host = rig.bind_host(w.store, pl, online=False)
    rig.fail_launch(pl, ERROR, wake=True)
    await s.tick()

    rig.host_store.upsert_on_connect(host, "managed", "alice")
    s.clock.advance(launch_watch.RETRY_DELAY_S + 30)
    await s.tick()

    assert rig.terminated == [] and rig.kicks == [] and rig.wakes == []
    assert pl not in _session_sandbox_status_cache


async def test_a_borrowed_sandbox_that_did_not_wake_goes_to_owners(
    supervision: Supervision, rig: SandboxRig
) -> None:
    s, w = supervision, supervision.world
    pl = w.id("pl")
    other = rig.bind_host(w.store, w.id("lead_b"), online=False)
    w.store.set_host_id(pl, other, "/home/omnigent/workspace")
    w.store.set_labels(pl, {MANAGED_HOST_CLAIM_LABEL_KEY: "someone-else"})
    rig.fail_launch(pl, ERROR, wake=True)

    await s.tick()
    s.clock.advance(launch_watch.RETRY_DELAY_S + 30)
    await s.tick()

    assert rig.terminated == [] and rig.kicks == [] and rig.wakes == []
    [decision] = _launch_decisions(s)
    assert decision.question == '"PL · api-version" could not wake its sandbox'


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
    w.store.set_labels(pl, RETRIED)
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


async def test_a_pl_whose_ic_still_works_keeps_its_sandbox(
    supervision: Supervision, rig: SandboxRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its sub-agents run in its sandbox: an IC tearing previews down after STOP."""
    s, w = supervision, supervision.world
    pl = w.id("pl")
    rig.bind_host(w.store, pl)
    _close_project(s)
    w.store.set_labels(pl, {F.LABEL_STATE: F.STATE_DONE})
    monkeypatch.setitem(_session_status_cache, pl, "idle")
    monkeypatch.setitem(_session_status_cache, w.id("ic"), "running")

    for _ in range(3):
        s.clock.advance(launch_watch.RELEASE_GRACE_S + 1)
        await s.tick()
    assert rig.stopped == []

    monkeypatch.setitem(_session_status_cache, w.id("ic"), "idle")
    await s.tick()
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
        "sample-org/sample-app",
        9631,
        project_slug="api-version",
        preview={"state": "ready", "url": "https://p9631.example"},
    )

    s.clock.advance(launch_watch.RELEASE_GRACE_S + 1)
    await s.tick()
    s.clock.advance(launch_watch.RELEASE_GRACE_S + 1)
    await s.tick()

    assert rig.stopped == []
