"""Quiet heartbeats: skip a due beat nothing needs, deliver one with a delta when something did."""

from __future__ import annotations

from typing import Any

import pytest

from omnigent import factory as F
from omnigent.entities.factory import EventDraft, FactoryEvent
from omnigent.server.factory import quiet_beats
from omnigent.server.factory.notify import iso_utc
from omnigent.server.factory.supervisor import FactorySupervisor, SupervisorConfig
from omnigent.server.routes._sessions.common import _session_status_cache
from tests.server.factory.conftest import Supervision

PL_HEARTBEAT_S = 600
INSTRUCTION = 'Run load_skill("factory-heartbeat") and follow it. Reply only if something changed.'


@pytest.fixture()
def quiet(supervision: Supervision) -> Supervision:
    """The seeded world with quiet beats on and only the PL beating."""
    w = supervision.world
    for lead in ("lead_a", "lead_b"):
        w.store.set_labels(w.id(lead), {F.LABEL_HEARTBEAT_S: "0"})
    supervision.supervisor = FactorySupervisor(
        w.app_state,
        config=SupervisorConfig(quiet_beats=True, stuck_s=2000),
        deliver=supervision.deliver,
        clock=supervision.clock,
    )
    return supervision


def _plain(n: int, at: float) -> str:
    return f"[Factory heartbeat #{n} · {iso_utc(at)}] {INSTRUCTION}"


def _event(s: Supervision, summary: str, **fields: Any) -> FactoryEvent:
    store = s.world.app_state.factory.factory_store
    return store.append_event(
        EventDraft(
            factory_id=s.world.id("lead_a"),
            kind=fields.pop("kind", "pr.ready"),
            summary=summary,
            ts=int(s.clock.now * 1000),
            **fields,
        )
    )


async def _first_beat(s: Supervision) -> float:
    """Warm up (sub-agent dispatches get logged), then deliver beat #1."""
    await s.tick()
    at = s.clock.advance(PL_HEARTBEAT_S)
    await s.tick()
    assert s.deliver.texts_to(s.world.id("pl")) == [_plain(1, at)]
    return at


async def test_a_due_beat_is_skipped_when_nothing_happened(quiet: Supervision) -> None:
    s, w = quiet, quiet.world
    first = await _first_beat(s)
    labels = w.get("pl").labels

    s.clock.advance(PL_HEARTBEAT_S)
    await s.tick()
    s.clock.advance(60)
    await s.tick()

    assert s.deliver.texts_to(w.id("pl")) == [_plain(1, first)]
    # A skip writes nothing to the labels.
    after = w.get("pl").labels
    assert after[F.LABEL_LAST_BEAT] == labels[F.LABEL_LAST_BEAT] == iso_utc(first)
    assert after[F.LABEL_BEAT_N] == "1"


async def test_an_event_touching_the_member_delivers_a_beat_with_its_delta(
    quiet: Supervision,
) -> None:
    s, w = quiet, quiet.world
    await _first_beat(s)
    s.clock.advance(30)
    _event(s, "PR #9630 ready for human merge • 6/6 gates", to_id=w.id("pl"))

    at = s.clock.advance(PL_HEARTBEAT_S)
    await s.tick()

    assert s.deliver.texts_to(w.id("pl"))[-1] == (
        f"[Factory heartbeat #2 · {iso_utc(at)}] 1 change since #1: "
        "• PR #9630 ready for human merge · 6/6 gates\n" + INSTRUCTION
    )
    assert w.get("pl").labels[F.LABEL_BEAT_N] == "2"


async def test_a_project_event_counts_for_its_pl(quiet: Supervision) -> None:
    s, w = quiet, quiet.world
    await _first_beat(s)
    s.clock.advance(30)
    _event(s, "checks failed on #9630", project_slug="api-version", kind="gate.changed")

    s.clock.advance(PL_HEARTBEAT_S)
    await s.tick()

    assert "1 change since #1: • checks failed on #9630" in s.deliver.texts_to(w.id("pl"))[-1]


async def test_the_backstop_delivers_every_third_interval(quiet: Supervision) -> None:
    s, w = quiet, quiet.world
    await _first_beat(s)

    s.clock.advance(PL_HEARTBEAT_S)
    await s.tick()
    s.clock.advance(PL_HEARTBEAT_S)
    await s.tick()
    assert len(s.deliver.texts_to(w.id("pl"))) == 1

    at = s.clock.advance(PL_HEARTBEAT_S)
    await s.tick()
    # Nothing changed, so the backstop beat carries no delta.
    assert s.deliver.texts_to(w.id("pl"))[-1] == _plain(2, at)


async def test_a_long_running_child_forces_a_beat(
    quiet: Supervision, monkeypatch: pytest.MonkeyPatch
) -> None:
    s, w = quiet, quiet.world
    await _first_beat(s)
    monkeypatch.setitem(_session_status_cache, w.id("ic"), "running")

    # The IC has run without progress for more than half the stuck threshold (1000 s).
    at = s.clock.advance(PL_HEARTBEAT_S)
    assert at - w.get("ic").updated_at >= 1000
    await s.tick()

    assert s.deliver.texts_to(w.id("pl"))[-1] == _plain(2, at)


async def test_the_delta_is_capped_at_five_bullets(quiet: Supervision) -> None:
    s, w = quiet, quiet.world
    await _first_beat(s)
    for i in range(1, 8):
        s.clock.advance(1)
        _event(s, f"change {i}", to_id=w.id("pl"))

    s.clock.advance(PL_HEARTBEAT_S)
    await s.tick()

    header = s.deliver.texts_to(w.id("pl"))[-1].splitlines()[0]
    assert header.endswith(
        "] 7 changes since #1: • change 3 • change 4 • change 5 • change 6 • change 7"
    )


async def test_skips_are_reported_hourly_per_factory(quiet: Supervision) -> None:
    s, w = quiet, quiet.world
    await _first_beat(s)
    store = w.app_state.factory.factory_store

    s.clock.advance(PL_HEARTBEAT_S)  # skipped
    await s.tick()
    s.clock.advance(PL_HEARTBEAT_S)  # skipped
    await s.tick()
    s.clock.advance(PL_HEARTBEAT_S)  # backstop beat
    await s.tick()
    assert store.find_events(w.id("lead_a"), "heartbeat.skipped", 0) == []

    s.clock.advance(3600)
    await s.tick()
    [event] = store.find_events(w.id("lead_a"), "heartbeat.skipped", 0)
    assert event.summary.startswith("2 heartbeats skipped")
    assert event.data is not None and event.data["counts"] == {w.id("pl"): 2}


async def test_quiet_beats_are_off_by_config_and_env(
    supervision: Supervision, monkeypatch: pytest.MonkeyPatch
) -> None:
    s, w = supervision, supervision.world
    await s.tick()
    s.clock.advance(PL_HEARTBEAT_S)
    await s.tick()
    s.clock.advance(PL_HEARTBEAT_S)
    await s.tick()
    # The fixture's supervisor beats every interval, nothing skipped.
    assert len(s.deliver.texts_to(w.id("pl"))) == 2

    monkeypatch.setenv(quiet_beats.QUIET_BEATS_ENV, "0")
    assert SupervisorConfig.from_env().quiet_beats is False
    monkeypatch.setenv(quiet_beats.QUIET_BEATS_ENV, "1")
    assert SupervisorConfig.from_env().quiet_beats is True


# ── which events count ─────────────────────────────────────────


def _ev(kind: str = "pr.ready", **fields: Any) -> FactoryEvent:
    defaults: dict[str, Any] = {"id": "e", "factory_id": "f", "ts": 1, "summary": "s"}
    return FactoryEvent(kind=kind, **{**defaults, **fields})


def test_relevance_rules(supervision: Supervision) -> None:
    w = supervision.world
    pl, lead = w.get("pl"), w.get("lead_a")
    events = [
        _ev(to_id=pl.id, summary="to the PL"),
        _ev(project_slug="api-version", summary="in its project"),
        _ev(project_slug="db-version", summary="another project"),
        _ev("heartbeat.delivered", to_id=pl.id, summary="its own beat"),
        _ev("message.delivered", from_id=pl.id, summary="its own post"),
        _ev("message.queued", to_id=pl.id, summary="still queued"),
        _ev(severity="warning", summary="a warning elsewhere"),
    ]

    assert [e.summary for e in quiet_beats.relevant_events(pl, events)] == [
        "to the PL",
        "in its project",
    ]
    assert [e.summary for e in quiet_beats.relevant_events(lead, events)] == [
        "a warning elsewhere"
    ]


def test_plan_beat_rules() -> None:
    plan = quiet_beats.plan_beat
    ev = [_ev(summary="x")]
    assert plan(
        now=100, last_beat=None, interval=60, beat_n=0, relevant=[], stalled_child=False
    ) == quiet_beats.BeatPlan(skip=False)
    assert plan(
        now=100, last_beat=50, interval=60, beat_n=3, relevant=[], stalled_child=False
    ).skip
    assert plan(
        now=100, last_beat=50, interval=60, beat_n=3, relevant=ev, stalled_child=False
    ) == quiet_beats.BeatPlan(skip=False, delta="1 change since #3: • x")
    assert not plan(
        now=100, last_beat=50, interval=60, beat_n=3, relevant=[], stalled_child=True
    ).skip
    assert not plan(
        now=230, last_beat=50, interval=60, beat_n=3, relevant=[], stalled_child=False
    ).skip
