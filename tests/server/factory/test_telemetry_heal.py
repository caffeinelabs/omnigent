"""The nightly span heal: idempotent rebuilds from tables and events, and the first backfill."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from omnigent.server.factory import events, prs
from omnigent.server.factory.background import prune
from omnigent.server.factory.telemetry import heal, pr_rounds
from tests.server.factory.conftest import FactoryWorld, FakeDeliver
from tests.server.factory.telemetry_replay import Replay, make_replay, span_table, strict_widths


def _make(
    world: FactoryWorld,
    deliver: FakeDeliver,
    no_sleep: Callable[[float], Any],
    monkeypatch: pytest.MonkeyPatch,
    *,
    record: bool,
) -> Replay:
    replay = make_replay(world, deliver, no_sleep, monkeypatch, record=record)
    if not record:

        async def _no_hook(*_a: Any, **_k: Any) -> None:
            return None

        monkeypatch.setattr(prs, "record_refresh", _no_hook)
        monkeypatch.setattr(events, "_listeners", [*events._listeners])
    return replay


def _heal(r: Replay) -> None:
    heal.heal_factory(r.deps, r.fid, now=int(r.clock.now))


def _view(r: Replay, kinds: tuple[str, ...]) -> list[tuple[Any, ...]]:
    return [row[:-1] for row in span_table([s for s in r.spans() if s.kind in kinds])]


async def test_heal_run_twice_yields_identical_rows(
    world: FactoryWorld,
    fake_deliver: FakeDeliver,
    no_sleep: Callable[[float], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r = _make(world, fake_deliver, no_sleep, monkeypatch, record=True)
    await r.run()
    live = span_table(r.spans())

    _heal(r)
    once = span_table(r.spans())
    _heal(r)
    twice = span_table(r.spans())

    assert twice == once
    # Healing what the live writers recorded changes none of their rows.
    kinds = ("pr", "pr_round", "gate", "ready_wait", "stage", "project", "decision", "env")
    healed_live = [row for row in once if row[1] in kinds]
    assert healed_live == [row for row in live if row[1] in kinds]


async def test_the_first_heal_backfills_from_the_events_that_remain(
    world: FactoryWorld,
    fake_deliver: FakeDeliver,
    no_sleep: Callable[[float], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r = _make(world, fake_deliver, no_sleep, monkeypatch, record=False)
    await r.run()
    assert r.spans() == []
    for number in (9630, 9631):
        # Rows tracked before PR facts existed.
        r.store.update_pr(r.pr_id(number), first_ready_at=None)

    _heal(r)

    p30, p31 = r.pr_id(9630), r.pr_id(9631)
    assert [s.outcome for s in r.spans("pr", pr_id=p30)] == ["ok"]
    rounds = r.spans("pr_round", pr_id=p30)
    assert [(s.head_sha, s.outcome) for s in rounds] == [
        (r.head("a"), "superseded"),
        (r.head("b"), "superseded"),
        (r.head("c"), "ok"),
    ]

    def gates(pr_id: str, head: str) -> dict[str, tuple[str, Any]]:
        return {
            s.name: (s.outcome, (s.attrs or {}).get("first_pass"))
            for s in r.spans("gate", pr_id=pr_id)
            if s.head_sha == r.head(head)
        }

    assert gates(p30, "a") == {"checks": ("fail", False)}
    assert gates(p30, "b") == {"checks": ("ok", True), "review": ("fail", False)}
    assert gates(p30, "c") == {
        "checks": ("ok", True),
        "review": ("ok", True),
        "verify": ("ok", True),
        "demo": ("ok", True),
    }
    assert [(w.outcome, w.started_at, w.ended_at) for w in r.spans("ready_wait", pr_id=p30)] == [
        ("ok", r.ms(3100), r.ms(4000))
    ]
    assert [(s.head_sha, s.outcome) for s in r.spans("pr_round", pr_id=p31)] == [
        (r.head("d"), "superseded"),
        (r.head("e"), "open"),
    ]
    assert gates(p31, "e") == {
        "checks": ("ok", True),
        "review": ("ok", True),
        "verify": ("ok", True),
    }
    assert [w.outcome for w in r.spans("ready_wait", pr_id=p31)] == ["superseded", "open"]
    row30 = r.store.get_pr(p30)
    assert row30 is not None and row30.first_ready_at == r.s(3100)

    assert [(s.name, s.outcome) for s in r.spans("decision")] == [("D-1", "ok")]
    assert [(s.name, s.outcome, s.ended_at) for s in r.spans("task")] == [
        ("ic_codex:version-endpoint", "ok", r.ms(1560))
    ]
    assert [s.name for s in r.spans("intervention")] == ["pause"]
    assert [(s.name, s.outcome) for s in r.spans("env")] == [("starting", "ok"), ("ready", "open")]
    projects = {s.name: s.outcome for s in r.spans("project")}
    assert projects == {"api-version": "ok", "db-version": "open"}

    # And a second run over the backfill changes nothing.
    first = span_table(r.spans())
    _heal(r)
    assert span_table(r.spans()) == first


async def test_heal_closes_a_task_whose_done_event_was_lost(
    world: FactoryWorld,
    fake_deliver: FakeDeliver,
    no_sleep: Callable[[float], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r = _make(world, fake_deliver, no_sleep, monkeypatch, record=True)
    reviewer = r.world.get("reviewer")
    await events.emit(
        r.world.app_state,
        r.fid,
        "dispatch",
        project="api-version",
        from_id=r.world.id("pl"),
        to_id=reviewer.id,
        summary="reviewer:pr-1 · review #9630",
        data={"agent": "reviewer"},
        ts=int(reviewer.created_at * 1000),
    )
    r.world.store.set_session_usage(reviewer.id, {"total_cost_usd": 1.25, "output_tokens": 70})
    (task,) = r.spans("task")
    assert task.outcome == "open"

    reviewer = r.world.get("reviewer")
    now = int(reviewer.updated_at) + heal.STALE_TASK_S + 1
    heal.heal_factory(r.deps, r.fid, now=now)

    (task,) = r.spans("task")
    assert (task.outcome, task.ended_at) == ("ok", int(reviewer.updated_at * 1000))
    assert (task.cost_usd, task.tokens_out, task.role) == (1.25, 70, "reviewer")


async def test_an_oversized_evidence_sha_does_not_stop_the_heal(
    world: FactoryWorld,
    fake_deliver: FakeDeliver,
    no_sleep: Callable[[float], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r = _make(world, fake_deliver, no_sleep, monkeypatch, record=False)
    await r.run()
    p30 = r.pr_id(9630)
    row = r.store.get_pr(p30)
    assert row is not None
    # A review marker naming a pasted sha256 is what the gate shows when none matches the head.
    gates = dict(row.gates or {})
    evidence = dict(gates.get("evidence") or {})
    evidence["review"] = {"verdict": "APPROVE", "sha": "ab" * 32, "at": r.s(2900)}
    r.store.update_pr(p30, gates={**gates, "evidence": evidence})
    refused = strict_widths(r.store, monkeypatch)
    failures: list[str] = []

    heal.heal_factory(r.deps, r.fid, now=int(r.clock.now), failures=failures)

    assert failures == [] and refused == []
    rounds = r.spans("pr_round", pr_id=p30)
    assert [s.head_sha for s in rounds] == [r.head("a"), r.head("b"), r.head("c")]
    assert [s.name for s in r.spans("decision")] == ["D-1"]


async def test_one_failing_pr_skips_only_itself(
    world: FactoryWorld,
    fake_deliver: FakeDeliver,
    no_sleep: Callable[[float], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r = _make(world, fake_deliver, no_sleep, monkeypatch, record=False)
    await r.run()
    p30, p31 = r.pr_id(9630), r.pr_id(9631)
    real = pr_rounds.apply_facts

    def broken(store: Any, facts: pr_rounds.PrFacts, *args: Any, **kwargs: Any) -> None:
        if facts.pr.id == p30:
            raise RuntimeError("a bad row")
        real(store, facts, *args, **kwargs)

    monkeypatch.setattr(pr_rounds, "apply_facts", broken)

    report = await heal.heal_once(r.world.app_state, now=int(r.clock.now))

    assert report.failed == [] and report.partial == [f"{r.fid}: pr {p30}"]
    assert r.spans("pr_round", pr_id=p30) == []
    assert [s.head_sha for s in r.spans("pr_round", pr_id=p31)] == [r.head("d"), r.head("e")]
    # The sections after the PRs still healed.
    assert [s.name for s in r.spans("decision")] == ["D-1"]
    assert [s.name for s in r.spans("task")] == ["ic_codex:version-endpoint"]
    assert [s.name for s in r.spans("intervention")] == ["pause"]


async def test_heal_keeps_a_close_that_landed_after_it_read_the_events(
    world: FactoryWorld,
    fake_deliver: FakeDeliver,
    no_sleep: Callable[[float], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r = _make(world, fake_deliver, no_sleep, monkeypatch, record=True)
    await r.run()
    # Heal read the log at t=600; the env became ready (closing "starting") at t=900.
    read = [e for e in heal.iter_events(r.store, r.fid) if e.ts <= r.ms(600)]
    monkeypatch.setattr(heal, "iter_events", lambda *_a, **_k: read)

    heal.heal_factory(r.deps, r.fid, now=r.s(600))

    assert [(s.name, s.outcome, s.ended_at) for s in r.spans("env")] == [
        ("starting", "ok", r.ms(900)),
        ("ready", "open", None),
    ]


async def test_heal_once_heals_every_factory(
    world: FactoryWorld,
    fake_deliver: FakeDeliver,
    no_sleep: Callable[[float], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r = _make(world, fake_deliver, no_sleep, monkeypatch, record=False)
    await r.run(until=1600)

    report = await heal.heal_once(r.world.app_state, now=int(r.clock.now))

    assert report.factories == 2 and report.failed == []
    assert report.events > 0
    assert {s.name for s in r.spans("project")} == {"api-version", "db-version"}


async def test_the_nightly_loop_heals_before_it_prunes(
    world: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def fake_heal(_state: Any, **_k: Any) -> heal.HealReport:
        calls.append("heal")
        return heal.HealReport()

    async def fake_prune(_state: Any, **_k: Any) -> int:
        calls.append("prune")
        return 0

    monkeypatch.setattr(heal, "heal_once", fake_heal)
    monkeypatch.setattr(prune, "prune_once", fake_prune)
    stop = await prune.start(world.app_state)
    for _ in range(50):
        if len(calls) >= 2:
            break
        await asyncio.sleep(0.01)
    await stop()
    assert calls[:2] == ["heal", "prune"]
