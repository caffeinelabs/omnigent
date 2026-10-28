"""Factory spans: the writer, the event recorder and the PR refresh hook.

The headline test replays the 2026-10-26 live run (caffeinelabs/app#9630 and
#9631) through the real poller and event log and checks the spans it leaves:
one ``pr`` per PR, one ``pr_round`` per head, ``gate`` spans per head with
``first_pass``, and the ``ready_wait`` episodes.
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from omnigent.entities.factory import EventDraft
from omnigent.server.factory import events, live
from omnigent.server.factory.telemetry import recorder, spans
from omnigent.server.factory.telemetry.spans import write_span
from omnigent.stores.factory_store import InMemoryFactoryStore
from tests.server.factory.conftest import FactoryWorld, FakeDeliver
from tests.server.factory.github_fakes import iso
from tests.server.factory.telemetry_replay import Replay, make_replay, strict_widths

REPO = "caffeinelabs/app"


@pytest.fixture()
def replay(
    world: FactoryWorld,
    fake_deliver: FakeDeliver,
    no_sleep: Callable[[float], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> Replay:
    return make_replay(world, fake_deliver, no_sleep, monkeypatch)


def _gates(r: Replay, number: int, head: str) -> dict[str, tuple[str, Any]]:
    sha = r.head(head)
    return {
        s.name: (s.outcome, (s.attrs or {}).get("first_pass"))
        for s in r.spans("gate", pr_id=r.pr_id(number))
        if s.head_sha == sha
    }


# ── The live run ─────────────────────────────────────────────────


async def test_the_live_run_replays_into_pr_round_gate_and_ready_wait_spans(
    replay: Replay,
) -> None:
    r = replay
    await r.run()
    p30, p31 = r.pr_id(9630), r.pr_id(9631)

    # #9630: three heads, merged.
    (pr30,) = r.spans("pr", pr_id=p30)
    assert (pr30.name, pr30.outcome, pr30.ended_at) == ("#9630", "ok", r.ms(4000))
    assert pr30.started_at == r.ms(1500)
    rounds = r.spans("pr_round", pr_id=p30)
    assert [(s.head_sha, s.outcome, s.started_at, s.ended_at) for s in rounds] == [
        (r.head("a"), "superseded", r.ms(1500), r.ms(1790)),
        (r.head("b"), "superseded", r.ms(1790), r.ms(2490)),
        (r.head("c"), "ok", r.ms(2490), r.ms(4000)),
    ]
    assert [(s.attrs or {}).get("round") for s in rounds] == [1, 2, 3]
    assert {s.parent_id for s in rounds} == {pr30.id}
    assert _gates(r, 9630, "a") == {
        "checks": ("fail", False),
        "review": ("superseded", None),
        "verify": ("superseded", None),
        "demo": ("superseded", None),
    }
    assert _gates(r, 9630, "b") == {
        "checks": ("ok", True),
        "review": ("fail", False),
        "verify": ("superseded", None),
        "demo": ("superseded", None),
    }
    assert _gates(r, 9630, "c") == {
        "checks": ("ok", True),
        "review": ("ok", True),
        "verify": ("ok", True),
        "demo": ("ok", True),
    }
    review_c = next(
        s for s in r.spans("gate", pr_id=p30) if s.name == "review" and s.head_sha == r.head("c")
    )
    # A gate runs from its head's push to its verdict, under that head's round.
    assert (review_c.started_at, review_c.ended_at) == (r.ms(2490), r.ms(2800))
    assert review_c.parent_id == rounds[2].id
    waits = r.spans("ready_wait", pr_id=p30)
    assert [(w.outcome, w.started_at, w.ended_at, w.parent_id) for w in waits] == [
        ("ok", r.ms(3100), r.ms(4000), pr30.id)
    ]

    # #9631: ready on its first head, lost READY to a new head, ready again.
    (pr31,) = r.spans("pr", pr_id=p31)
    assert pr31.outcome == "open"
    rounds = r.spans("pr_round", pr_id=p31)
    assert [(s.head_sha, s.outcome) for s in rounds] == [
        (r.head("d"), "superseded"),
        (r.head("e"), "open"),
    ]
    for head in ("d", "e"):
        assert _gates(r, 9631, head) == {
            "checks": ("ok", True),
            "review": ("ok", True),
            "verify": ("ok", True),
        }
    waits = r.spans("ready_wait", pr_id=p31)
    assert [(w.outcome, w.started_at, w.ended_at) for w in waits] == [
        ("superseded", r.ms(3300), r.ms(4300)),
        ("open", r.ms(4600), None),
    ]

    # PR facts: GitHub's createdAt and the first time the PR was ready.
    row30, row31 = r.store.get_pr(p30), r.store.get_pr(p31)
    assert row30 is not None and row31 is not None
    assert (row30.opened_at, row30.first_ready_at) == (r.s(0) + 1500, r.s(0) + 3100)
    assert (row31.opened_at, row31.first_ready_at) == (r.s(0) + 1600, r.s(0) + 3300)
    assert row31.ready_at == r.s(0) + 4600


async def test_the_live_run_records_project_stage_task_env_decision_and_control_spans(
    replay: Replay,
) -> None:
    r = replay
    await r.run()

    projects = {s.name: s for s in r.spans("project")}
    assert set(projects) == {"api-version", "db-version"}
    # #9630's merge moved api-version to the merged stage, which ends its project span.
    assert projects["api-version"].outcome == "ok"
    assert projects["api-version"].ended_at == r.ms(4000)
    assert projects["db-version"].outcome == "open"
    stages = [(s.name, s.outcome) for s in r.spans("stage", project_slug="api-version")]
    assert stages[0] == ("planning", "ok")
    assert stages[-1][1] == "open"
    assert all(outcome == "ok" for _, outcome in stages[:-1])

    (task,) = r.spans("task")
    assert (task.name, task.outcome) == ("ic_codex:version-endpoint", "ok")
    assert (task.started_at, task.ended_at) == (r.ms(300), r.ms(1560))
    assert (task.session_id, task.role, task.agent_name) == (r.world.id("ic"), "ic", "ic")

    envs = r.spans("env", project_slug="api-version")
    assert [(s.name, s.outcome, s.ended_at) for s in envs] == [
        ("starting", "ok", r.ms(900)),
        ("ready", "open", None),
    ]

    (decision,) = r.spans("decision")
    assert (decision.name, decision.outcome, decision.actor) == ("D-1", "ok", "luka")
    assert (decision.started_at, decision.ended_at) == (r.ms(3400), r.ms(3600))
    assert decision.project_slug == "db-version"

    (control,) = r.spans("intervention")
    assert (control.name, control.actor, control.started_at, control.ended_at) == (
        "pause",
        "luka",
        r.ms(3500),
        r.ms(3500),
    )


async def test_marker_shas_that_were_never_a_head_open_no_round(
    replay: Replay, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pasted sha256, a typo or an intermediate commit in a marker: no round, no failed write."""
    r = replay
    refused = strict_widths(r.store, monkeypatch)
    await r.run(until=2200)
    pr = r.prs[9630]
    now = r.clock.now
    intermediate = "1" * 40
    pr.commits.append((intermediate, iso(now - 30)))
    pr.comment(f"<!-- factory:review sha={'ab' * 32} verdict=APPROVE -->", now, "rv")
    pr.comment("<!-- factory:review sha=deadbee verdict=APPROVE -->", now, "rv")
    pr.comment(f"<!-- factory:verify sha={intermediate} result=PASS env=x -->", now)
    for step in r.fixture["steps"]:
        if step["at"] > 2200:
            await r.step(step)

    assert refused == []
    p30 = r.pr_id(9630)
    (pr30,) = r.spans("pr", pr_id=p30)
    # The refresh kept recording: the merge closed the PR.
    assert (pr30.outcome, pr30.ended_at) == ("ok", r.ms(4000))
    rounds = r.spans("pr_round", pr_id=p30)
    assert [(s.head_sha, s.outcome, (s.attrs or {}).get("round")) for s in rounds] == [
        (r.head("a"), "superseded", 1),
        (r.head("b"), "superseded", 2),
        (r.head("c"), "ok", 3),
    ]
    assert all(len(s.head_sha or "") <= 40 for s in r.spans())


async def test_rerunning_a_refresh_writes_nothing_new(replay: Replay) -> None:
    r = replay
    await r.run()
    before = [(s.id, s.outcome, s.ended_at, s.attrs) for s in r.spans()]

    r.clock.advance(120)
    await r.poller.run_once()

    after = [(s.id, s.outcome, s.ended_at, s.attrs) for s in r.spans()]
    assert after == before


async def test_spans_carry_the_platform_version_and_the_roots_def_version(
    replay: Replay, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent import factory as F
    from omnigent.server.factory.telemetry import versions

    monkeypatch.setattr(versions, "current_version_id", lambda: "a" * 32)
    r = replay
    r.world.store.set_labels(r.fid, {F.LABEL_DEF_VERSION: "7"})
    await r.run(until=1500)

    (pr,) = r.spans("pr")
    assert (pr.version_id, pr.def_version) == ("a" * 32, 7)
    # Before this server recorded a version, the root's version label stands in.
    monkeypatch.setattr(versions, "current_version_id", lambda: None)
    r.world.store.set_labels(r.fid, {F.LABEL_VERSION: "b" * 32})
    spans.reset()
    await r.step(
        {"at": 1501, "emit": {"kind": "project.added", "project": "new-one", "summary": "x"}}
    )
    assert r.spans("project", project_slug="new-one")[0].version_id == "b" * 32


# ── The writer ───────────────────────────────────────────────────


def test_write_span_is_idempotent_on_its_dedupe_key() -> None:
    store = InMemoryFactoryStore()
    fid = "f" * 32
    kwargs: dict[str, Any] = {
        "kind": "gate",
        "name": "review",
        "dedupe_key": "gate:x:y:review",
        "started_at": 1000,
        "attrs": {"first_pass": False},
        "set_once": ("first_pass",),
    }
    first = write_span(store, fid, **kwargs)
    again = write_span(store, fid, **kwargs)
    assert again.id == first.id and again.outcome == "open"

    closed = write_span(
        store, fid, **{**kwargs, "attrs": {"first_pass": True}}, ended_at=900, outcome="ok"
    )
    # Closed (clamped to its start); first_pass keeps the first value.
    assert (closed.outcome, closed.ended_at, closed.attrs) == ("ok", 1000, {"first_pass": False})
    # A closed span changes only with force, and reopens only when asked.
    assert write_span(store, fid, **kwargs, ended_at=5000, outcome="fail").outcome == "ok"
    assert (
        write_span(store, fid, **kwargs, ended_at=5000, outcome="fail", force=True).ended_at
        == 5000
    )
    assert write_span(store, fid, **kwargs, outcome="open").outcome == "fail"
    assert write_span(store, fid, **kwargs, outcome="open", reopen=True).ended_at is None
    assert len(store.list_spans(fid)) == 1


def test_write_span_fills_missing_fields_and_overwrites_only_named_ones() -> None:
    store = InMemoryFactoryStore()
    fid = "f" * 32
    base: dict[str, Any] = {"kind": "task", "name": "ic", "dedupe_key": "task:s", "started_at": 1}
    write_span(store, fid, **base, role="ic", cost_usd=1.0)
    span = write_span(store, fid, **base, role="reviewer", agent_name="ic", cost_usd=2.5)
    assert (span.role, span.agent_name, span.cost_usd) == ("ic", "ic", 1.0)
    span = write_span(store, fid, **base, cost_usd=2.5, overwrite=("cost_usd",))
    assert span.cost_usd == 2.5


def test_write_span_clips_free_text_columns() -> None:
    store = InMemoryFactoryStore()
    span = write_span(
        store,
        "f" * 32,
        kind="task",
        name="n" * 100,
        dedupe_key="task:s",
        started_at=1,
        role="r" * 40,
        agent_name="a" * 100,
        actor="h" * 200,
    )
    assert (len(span.name), len(span.role or ""), len(span.agent_name or "")) == (64, 16, 64)
    assert len(span.actor or "") == 128


def test_a_close_after_heal_read_its_facts_is_kept() -> None:
    store = InMemoryFactoryStore()
    fid = "f" * 32
    base: dict[str, Any] = {"kind": "env", "name": "starting", "dedupe_key": "env:x:1"}
    write_span(store, fid, **base, started_at=1000, ended_at=3000, outcome="ok")
    # Heal read the events at 2000, before the close landed: it neither reopens nor rewrites.
    kept = write_span(
        store, fid, **base, started_at=1000, outcome="open", reopen=True, keep_closed_after=2000
    )
    assert (kept.outcome, kept.ended_at) == ("ok", 3000)
    kept = write_span(
        store,
        fid,
        **base,
        started_at=1000,
        ended_at=1500,
        outcome="fail",
        force=True,
        keep_closed_after=2000,
    )
    assert (kept.outcome, kept.ended_at) == ("ok", 3000)
    # A close heal already knew about is still its to fix.
    healed = write_span(
        store, fid, **base, started_at=1000, outcome="open", reopen=True, keep_closed_after=4000
    )
    assert (healed.outcome, healed.ended_at) == ("open", None)


# ── The recorder ─────────────────────────────────────────────────


@pytest.fixture()
def recorded(world: FactoryWorld, monkeypatch: pytest.MonkeyPatch) -> FactoryWorld:
    monkeypatch.setattr(live, "announce_factory_changed", lambda *_a, **_k: None)
    monkeypatch.setattr(events, "_listeners", [*events._listeners])
    spans.reset()
    recorder.reset()
    spans.install()
    return world


async def _emit(world: FactoryWorld, kind: str, ts: int, **kwargs: Any) -> None:
    kwargs.setdefault("summary", kind)
    await events.emit(world.app_state, world.id("lead_a"), kind, ts=ts, **kwargs)


def _spans(world: FactoryWorld, kind: str) -> list[Any]:
    return world.deps.factory_store.list_spans(world.id("lead_a"), kinds=[kind])


async def test_stage_changes_are_episodes_and_a_terminal_stage_closes_the_project(
    recorded: FactoryWorld,
) -> None:
    w = recorded
    for ts, before, to in ((10, "queued", "building"), (20, "building", "merged")):
        await _emit(w, "stage.changed", ts, project="p1", data={"from": before, "to": to})
    assert [(s.name, s.outcome, s.ended_at) for s in _spans(w, "stage")] == [
        ("building", "ok", 20),
        ("merged", "open", None),
    ]
    (project,) = _spans(w, "project")
    assert (project.outcome, project.ended_at) == ("ok", 20)

    await _emit(w, "stage.changed", 30, project="p1", data={"from": "merged", "to": "building"})
    (project,) = _spans(w, "project")
    assert (project.outcome, project.ended_at) == ("open", None)


async def test_needs_human_opens_an_incident_its_clear_closes(recorded: FactoryWorld) -> None:
    w = recorded
    pl = w.id("pl")
    await _emit(w, "needs_human", 100, from_id=pl, data={"reason": "blocked"})
    await _emit(w, "needs_human.cleared", 250, from_id=pl, data={"blocked_on": "approval"})
    (incident,) = _spans(w, "incident")
    assert (incident.name, incident.outcome, incident.started_at, incident.ended_at) == (
        "needs_human",
        "ok",
        100,
        250,
    )


async def test_proposal_spans_follow_their_status_events(recorded: FactoryWorld) -> None:
    w = recorded
    await _emit(w, "proposal.opened", 1, data={"proposal_id": "c" * 32, "number": 4})
    await _emit(w, "proposal.applied", 5, data={"proposal_id": "c" * 32, "number": 4})
    await _emit(w, "proposal.reverted", 9, data={"proposal_id": "c" * 32, "number": 4})
    (proposal,) = _spans(w, "proposal")
    assert (proposal.name, proposal.outcome, proposal.ended_at) == ("C-4", "fail", 9)


async def test_preview_states_are_episodes_per_pr(recorded: FactoryWorld) -> None:
    w = recorded
    for ts, state in ((1, "deploying"), (2, "ready"), (3, "ready"), (4, "parked")):
        await _emit(
            w, "preview.reported", ts, data={"pr_id": "a" * 32, "state": state, "sha": f"s{ts}"}
        )
    previews = _spans(w, "preview")
    assert [(s.name, s.started_at, s.ended_at) for s in previews] == [
        ("deploying", 1, 2),
        ("ready", 2, 4),
        ("parked", 4, None),
    ]
    # Same-state reports extend the episode and refresh its attributes.
    assert previews[1].attrs == {"env_id": None, "sha": "s3"}


async def test_the_supervisor_puts_the_runs_cost_on_its_task_span(recorded: FactoryWorld) -> None:
    w = recorded
    ic = w.get("ic")
    w.store.set_session_usage(
        ic.id, {"total_cost_usd": 0.42, "input_tokens": 1000, "output_tokens": 200}
    )
    await _emit(w, "dispatch", 1, project="api-version", from_id=w.id("pl"), to_id=ic.id)
    await spans.attach_task_cost(w.app_state, w.id("lead_a"), w.get("ic"))
    (task,) = _spans(w, "task")
    assert (task.cost_usd, task.tokens_in, task.tokens_out) == (0.42, 1000, 200)
    assert task.started_at == 1

    w.store.set_session_usage(ic.id, {"total_cost_usd": 0.9, "output_tokens": 300})
    await spans.attach_task_cost(w.app_state, w.id("lead_a"), w.get("ic"))
    (task,) = _spans(w, "task")
    assert (task.cost_usd, task.tokens_out) == (0.9, 300)


async def test_a_failing_recorder_never_breaks_emit(
    recorded: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("store down")

    monkeypatch.setattr(recorder, "record_sync", boom)
    event = await events.emit(recorded.app_state, recorded.id("lead_a"), "dispatch", summary="x")
    assert event is not None


async def test_events_without_spans_are_ignored() -> None:
    state = SimpleNamespace()
    event = EventDraft(factory_id="f" * 32, kind="message.queued", summary="x")
    assert recorder.handler_for(event.kind) is None
    await recorder.record_event(state, InMemoryFactoryStore().append_event(event))


async def test_spans_seed_and_carry_the_definition_before_anything_resolved_it(
    replay: Replay,
) -> None:
    from omnigent import factory as F

    r = replay
    assert F.LABEL_DEF_VERSION not in r.world.get("lead_a").labels
    spans.reset()
    await r.run(until=1500)

    (pr,) = r.spans("pr")
    assert pr.def_version == 1
    assert r.world.get("lead_a").labels[F.LABEL_DEF_VERSION] == "1"
