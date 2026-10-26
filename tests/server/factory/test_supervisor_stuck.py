"""S3 stuck: one alert per stalled ``running`` episode, to the right member, never a cancel."""

from __future__ import annotations

from typing import Any

import pytest

from omnigent import factory as F
from omnigent.server.factory import delivery
from omnigent.server.factory.supervisor import DEFAULT_STUCK_S
from omnigent.server.routes._sessions.common import _session_status_cache
from tests.server.factory.conftest import Supervision


def _stuck(title: str, session_id: str, duration: str) -> str:
    return (
        f'[Factory alert · "{title}" ({session_id}) running with no progress for {duration}. '
        "Inspect with sys_session_get_history; nudge or cancel if wedged.]"
    )


@pytest.fixture(autouse=True)
def _no_cancel(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fail loudly if anything tries to stop or interrupt a session."""
    attempts: list[str] = []

    async def _refuse(*args: Any, **_kwargs: Any) -> bool:
        attempts.append(str(args[0]))
        raise AssertionError("the supervisor must never cancel or stop a session")

    for name in ("_stop_session_via_runner", "_stop_session_host_runner"):
        monkeypatch.setattr(delivery, name, _refuse)
    return attempts


async def test_a_stalled_member_alerts_its_supervisor_once_per_episode(
    supervision: Supervision, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, clock = supervision.world, supervision.clock
    pl, lead_b = w.id("pl"), w.id("lead_b")
    monkeypatch.setitem(_session_status_cache, pl, "running")

    await supervision.tick()
    assert supervision.deliver.texts_to(lead_b) == []

    clock.advance(DEFAULT_STUCK_S)
    await supervision.tick()
    clock.advance(600)
    await supervision.tick()

    assert supervision.deliver.texts_to(lead_b) == [_stuck("PL · api-version", pl, "1h30m")]
    # Alerts only: no restart flag on any delivery.
    assert all(restart is False for _, _, restart in supervision.deliver.calls)


async def test_a_stalled_sub_agent_alerts_its_parent(
    supervision: Supervision, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, clock = supervision.world, supervision.clock
    ic = w.id("ic")
    monkeypatch.setitem(_session_status_cache, ic, "running")

    clock.advance(DEFAULT_STUCK_S + 2 * 3600)
    await supervision.tick()

    alerts = [
        t for t in supervision.deliver.texts_to(w.id("pl")) if t.startswith("[Factory alert")
    ]
    assert len(alerts) == 1
    assert alerts[0].startswith(f'[Factory alert · "ic:task-1" ({ic}) running with no progress')
    assert supervision.deliver.texts_to(w.id("lead_b")) == []


async def test_a_member_without_a_supervisor_alerts_the_root(
    supervision: Supervision, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, clock = supervision.world, supervision.clock
    pl = w.id("pl")
    w.store.delete_label(pl, F.LABEL_SUPERVISOR)
    monkeypatch.setitem(_session_status_cache, pl, "running")

    clock.advance(DEFAULT_STUCK_S)
    await supervision.tick()

    assert [t for t in supervision.deliver.texts_to(w.id("lead_a")) if "no progress" in t] == [
        _stuck("PL · api-version", pl, "1h30m")
    ]


async def test_the_root_alerts_its_supervisor(
    supervision: Supervision, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, clock = supervision.world, supervision.clock
    lead_a = w.id("lead_a")
    monkeypatch.setitem(_session_status_cache, lead_a, "running")

    clock.advance(DEFAULT_STUCK_S)
    await supervision.tick()

    assert supervision.deliver.texts_to(w.id("lead_b")) == [_stuck("Atlas", lead_a, "1h30m")]


async def test_a_new_episode_alerts_again(
    supervision: Supervision, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, clock = supervision.world, supervision.clock
    pl, lead_b = w.id("pl"), w.id("lead_b")
    w.store.set_labels(pl, {F.LABEL_HEARTBEAT_S: "0"})
    monkeypatch.setitem(_session_status_cache, pl, "running")
    clock.advance(DEFAULT_STUCK_S)
    await supervision.tick()
    assert len(supervision.deliver.texts_to(lead_b)) == 1

    # The turn ends, then a new one stalls again.
    monkeypatch.setitem(_session_status_cache, pl, "idle")
    clock.advance(60)
    await supervision.tick()
    monkeypatch.setitem(_session_status_cache, pl, "running")
    clock.advance(60)
    await supervision.tick()

    assert len(supervision.deliver.texts_to(lead_b)) == 2


async def test_fresh_or_idle_sessions_are_not_stuck(
    supervision: Supervision, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, clock = supervision.world, supervision.clock
    monkeypatch.setitem(_session_status_cache, w.id("pl"), "running")
    # Idle for a long time is not stuck (and not a heartbeat target either).
    w.store.set_labels(w.id("lead_a"), {F.LABEL_HEARTBEAT_S: "0"})
    w.store.set_labels(w.id("pl"), {F.LABEL_HEARTBEAT_S: "0"})

    clock.advance(DEFAULT_STUCK_S - 60)
    await supervision.tick()

    assert supervision.deliver.calls == []


async def test_other_factories_and_plain_sessions_are_ignored(
    supervision: Supervision, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, clock = supervision.world, supervision.clock
    for key in ("other_lead", "plain"):
        monkeypatch.setitem(_session_status_cache, w.id(key), "running")
    for lead in ("lead_a", "pl"):
        w.store.set_labels(w.id(lead), {F.LABEL_HEARTBEAT_S: "0"})

    clock.advance(DEFAULT_STUCK_S * 2)
    await supervision.tick()

    # The other factory's root has no supervisor and nobody above it.
    assert supervision.deliver.calls == []


async def test_stuck_alerts_never_cancel(
    supervision: Supervision,
    monkeypatch: pytest.MonkeyPatch,
    _no_cancel: list[str],
) -> None:
    w, clock = supervision.world, supervision.clock
    for key in ("lead_a", "lead_b", "pl", "ic", "reviewer"):
        monkeypatch.setitem(_session_status_cache, w.id(key), "running")

    clock.advance(DEFAULT_STUCK_S * 3)
    for _ in range(3):
        await supervision.tick()
        clock.advance(60)

    assert _no_cancel == []
    assert len(supervision.deliver.calls) == 5
    assert all(restart is False for _, _, restart in supervision.deliver.calls)
    # Statuses are reported, never changed.
    assert _session_status_cache[w.id("pl")] == "running"


async def test_the_newest_sub_agents_are_checked_first(
    supervision: Supervision, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.server.factory import supervisor as supervisor_module

    w, clock = supervision.world, supervision.clock
    # A long-lived PL past the per-member page: only the newest child fits.
    monkeypatch.setattr(supervisor_module, "_MAX_CHILDREN_PER_MEMBER", 1)
    redispatch = w.make_session(
        "ic_r2", agent="pl", title="ic:task-1-r2", parent="pl", sub_agent_name="ic"
    )
    for key in ("ic", "ic_r2"):
        monkeypatch.setitem(_session_status_cache, w.id(key), "running")
    for lead in ("lead_a", "pl"):
        w.store.set_labels(w.id(lead), {F.LABEL_HEARTBEAT_S: "0"})

    clock.advance(DEFAULT_STUCK_S)
    await supervision.tick()

    assert supervision.deliver.texts_to(w.id("pl")) == [
        _stuck("ic:task-1-r2", redispatch.id, "1h30m")
    ]
