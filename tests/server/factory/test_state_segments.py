"""Durable state segments per agent, the strip they build, and the timeline route.

Status edges open and close ``turn`` (working) and ``wait`` (waiting on a
person) spans on the agent's session; an idle edge closes only after a quiet
spell. The timeline overlays stuck incidents and its PRs' gates, and adds tool
calls and spend per bucket.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.entities.conversation import NewConversationItem, parse_item_data
from omnigent.server.factory import issues, notify, snapshot
from omnigent.server.factory.telemetry import spans, states
from omnigent.server.factory.telemetry.states import Segment, StateRecorder
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit
from tests.server.factory.conftest import FactoryWorld

H_MS = 3_600_000


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(states.STATE_SEGMENTS_ENV, "1")
    snapshot.reset_cache()
    states.reset_cache()
    issues.reset()
    yield
    snapshot.reset_cache()
    states.reset_cache()
    issues.reset()


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in build_cockpit(world, db_uri, monkeypatch):
        yield value


class Clock:
    """A settable wall clock in epoch seconds."""

    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _segments(world: FactoryWorld, key: str, store: Any = None) -> list[Any]:
    fstore = store or world.deps.factory_store
    rows = fstore.list_spans(
        world.id("lead_a"), kinds=list(states.SEGMENT_KINDS), session_id=world.id(key)
    )
    return [r for r in rows if (r.dedupe_key or "").startswith("state:")]


# ── The recorder ────────────────────────────────────────────────


async def test_a_turn_opens_on_running_and_closes_on_the_turn_end(world: FactoryWorld) -> None:
    clock = Clock(1_000_000.0)
    rec = StateRecorder(world.app_state, clock=clock, debounce_s=0.0)
    world.store.set_session_usage(world.id("ic"), {"total_cost_usd": 1.0})

    rec.on_relayed(world.id("ic"), "running", None)
    await rec.drain()
    world.store.set_session_usage(world.id("ic"), {"total_cost_usd": 1.75})
    clock.now += 120
    rec.on_status_edge(world.get("ic"), "idle", {})
    await rec.drain()

    [seg] = _segments(world, "ic")
    assert (seg.kind, seg.name, seg.outcome) == ("turn", "working", "ok")
    assert (seg.started_at, seg.ended_at) == (1_000_000_000, 1_000_120_000)
    assert (seg.role, seg.project_slug) == ("ic", "api-version")
    assert seg.attrs["vendor"] == "claude" and seg.attrs["segment"] == "working"
    assert (seg.attrs["cost_start"], seg.attrs["cost_end"]) == (1.0, 1.75)


async def test_a_mid_turn_lull_does_not_split_the_turn(world: FactoryWorld) -> None:
    rec = StateRecorder(world.app_state, debounce_s=0.2)
    sid = world.id("ic")

    rec.on_relayed(sid, "running", None)
    rec.on_relayed(sid, "idle", None)
    await asyncio.sleep(0.01)
    rec.on_relayed(sid, "running", None)
    await asyncio.sleep(0.3)
    await rec.drain()

    [seg] = _segments(world, "ic")
    assert seg.outcome == "open" and rec.current(sid) == "working"


async def test_a_parked_prompt_is_waiting_on_a_person(world: FactoryWorld) -> None:
    clock = Clock(2_000_000.0)
    rec = StateRecorder(world.app_state, clock=clock, debounce_s=0.0)
    sid = world.id("pl")

    rec.on_relayed(sid, "running", None)
    clock.now += 60
    rec.on_relayed(sid, "running", "Approve: Bash(git push)")
    clock.now += 300
    rec.on_relayed(sid, "running", None)
    clock.now += 60
    rec.on_status_edge(world.get("pl"), "failed", {})
    await rec.drain()

    rows = _segments(world, "pl")
    assert [(r.kind, r.name, r.outcome) for r in rows] == [
        ("turn", "working", "ok"),
        ("wait", "person", "ok"),
        ("turn", "working", "fail"),
    ]
    assert rows[1].attrs["on"] == "person"
    assert rows[1].ended_at - rows[1].started_at == 300_000


async def test_non_members_are_ignored(world: FactoryWorld) -> None:
    rec = StateRecorder(world.app_state, debounce_s=0.0)

    rec.on_relayed(world.id("plain"), "running", None)
    await rec.drain()
    rec.on_relayed(world.id("plain"), "idle", None)
    rec.on_status_edge(world.get("plain"), "running", {})
    await rec.drain()

    assert world.deps.factory_store.list_spans(world.id("plain"), kinds=["turn"]) == []
    assert rec.current(world.id("plain")) is None


async def test_recording_is_dark_unless_switched_on(
    world: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = world.id("ic")
    monkeypatch.delenv(states.STATE_SEGMENTS_ENV)
    monkeypatch.delenv(F.COCKPIT_V2_ENV, raising=False)
    rec = StateRecorder(world.app_state, debounce_s=0.0)

    rec.on_relayed(sid, "running", None)
    await rec.drain()
    assert _segments(world, "ic") == [] and rec.current(sid) is None

    # The new cockpit turns it on, unless the segments' own switch says no.
    monkeypatch.setenv(F.COCKPIT_V2_ENV, "1")
    monkeypatch.setenv(states.STATE_SEGMENTS_ENV, "0")
    rec.on_relayed(sid, "running", None)
    await rec.drain()
    assert _segments(world, "ic") == []

    monkeypatch.delenv(states.STATE_SEGMENTS_ENV)
    rec.on_relayed(sid, "running", None)
    await rec.drain()
    assert len(_segments(world, "ic")) == 1


def _item(world: FactoryWorld, key: str, role: str, at_s: int, monkeypatch: Any) -> None:
    from omnigent.stores.conversation_store import sqlalchemy_store

    with monkeypatch.context() as patched:
        patched.setattr(sqlalchemy_store, "now_epoch", lambda: at_s)
        world.store.append(
            world.id(key),
            [
                NewConversationItem(
                    type="message",
                    response_id=f"r{at_s}",
                    data=parse_item_data(
                        "message",
                        {
                            "role": role,
                            "content": [{"type": "input_text", "text": "(sample)"}],
                            **({"agent": "ic"} if role == "assistant" else {}),
                        },
                    ),
                )
            ],
        )


async def test_a_busy_edge_ends_a_stale_segment_at_the_last_activity(
    world: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    fid, sid = world.id("lead_a"), world.id("ic")
    t0 = 1_000_000
    spans.write_span(
        world.deps.factory_store,
        fid,
        kind="turn",
        name="working",
        dedupe_key=spans.state_key(sid, t0 * 1000),
        started_at=t0 * 1000,
        session_id=sid,
        attrs={"segment": "working"},
    )
    _item(world, "ic", "assistant", t0 + 60, monkeypatch)
    # Four hours later a new message starts the next turn; the server restarted in between.
    _item(world, "ic", "user", t0 + 4 * 3600 - 2, monkeypatch)
    rec = StateRecorder(world.app_state, clock=Clock(t0 + 4 * 3600), debounce_s=0.0)

    rec.on_relayed(sid, "running", None)
    await rec.drain()

    stale, fresh = _segments(world, "ic")
    assert (stale.outcome, stale.ended_at) == ("ok", (t0 + 60) * 1000)
    assert stale.attrs["closed_late"] is True
    assert (fresh.outcome, fresh.started_at) == ("open", (t0 + 4 * 3600) * 1000)


async def test_shutdown_writes_pending_closes_at_their_turn_end(world: FactoryWorld) -> None:
    clock = Clock(3_000_000.0)
    rec = StateRecorder(world.app_state, clock=clock, debounce_s=3600.0)
    sid = world.id("ic")
    setattr(world.app_state, states.RECORDER_STATE_ATTR, rec)

    rec.on_relayed(sid, "running", None)
    await rec.drain(timeout_s=0.2)
    clock.now += 90
    rec.on_relayed(sid, "idle", None)
    clock.now += 600
    await states.flush(world.app_state)

    [seg] = _segments(world, "ic")
    assert (seg.outcome, seg.ended_at) == ("ok", 3_000_090_000)
    assert rec.current(sid) is None and not rec._closing


async def test_old_segments_are_pruned(world: FactoryWorld) -> None:
    fid, sid = world.id("lead_a"), world.id("ic")
    store = world.deps.factory_store
    day = 86_400_000
    now = 100 * day
    for start, end in ((now - 40 * day, now - 39 * day), (now - 2 * day, now - day)):
        spans.write_span(
            store,
            fid,
            kind="turn",
            name="working",
            dedupe_key=spans.state_key(sid, start),
            started_at=start,
            ended_at=end,
            outcome="ok",
            session_id=sid,
        )
    store.insert_span(
        fid,
        kind="turn",
        name="other",
        trace_key="other",
        started_at=now - 50 * day,
        ended_at=now - 49 * day,
        outcome="ok",
    )

    assert states.prune(store, now) == 1
    assert [s.started_at for s in _segments(world, "ic")] == [now - 2 * day]
    assert len(store.list_spans(fid, kinds=["turn"])) == 2


def test_coverage_counts_only_the_time_an_agent_existed() -> None:
    since = 10 * H_MS
    # Recorded since before the window: complete.
    assert states.coverage(5 * H_MS, 1 * H_MS, since) == (5 * H_MS, False)
    # Recording began inside the window, after the agent was born: partial.
    assert states.coverage(12 * H_MS, 1 * H_MS, since) == (12 * H_MS, True)
    # The agent was born inside the window, after recording began: complete.
    assert states.coverage(5 * H_MS, 15 * H_MS, since) == (15 * H_MS, False)
    # Nothing recorded in the factory yet.
    assert states.coverage(None, 15 * H_MS, since) == (None, True)


async def test_a_segment_a_roll_left_open_closes_at_the_first_turn_end(
    world: FactoryWorld,
) -> None:
    fid, sid = world.id("lead_a"), world.id("ic")
    spans.write_span(
        world.deps.factory_store,
        fid,
        kind="turn",
        name="working",
        dedupe_key=spans.state_key(sid, 5_000),
        started_at=5_000,
        session_id=sid,
        attrs={"segment": "working"},
    )
    rec = StateRecorder(world.app_state, clock=Clock(60.0), debounce_s=0.0)

    rec.on_status_edge(world.get("ic"), "idle", {})
    await rec.drain()

    [seg] = _segments(world, "ic")
    assert (seg.outcome, seg.ended_at) == ("ok", 60_000)


async def test_status_edges_reach_the_recorder(
    world: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(F.FACTORY_ENABLED_ENV, "1")
    rec = states.get_recorder(world.app_state)
    notifier = notify.get_notifier(world.app_state)

    notifier.relayed_edge(world.id("ic"), "running")
    await rec.drain()

    assert rec.current(world.id("ic")) == "working"
    [seg] = _segments(world, "ic")
    assert seg.outcome == "open"
    notify.schedule_factory_status_edge(world.app_state, world.get("ic"), "idle", {})
    assert world.id("ic") in rec._closing
    rec.close()
    await notifier.close()


# ── The strip ───────────────────────────────────────────────────


def test_compose_strip_lets_the_strongest_state_win() -> None:
    pieces = states.compose_strip(
        0,
        100,
        {
            "working": [(10, 60)],
            "waiting": [(20, 30)],
            "stuck": [(40, 70)],
            "checks": [(65, 90), (95, 200)],
        },
    )

    assert [(p["state"], p["start"], p["end"]) for p in pieces] == [
        ("idle", 0, 10),
        ("working", 10, 20),
        ("waiting", 20, 30),
        ("working", 30, 40),
        ("stuck", 40, 70),
        ("checks", 70, 90),
        ("idle", 90, 95),
        ("checks", 95, 100),
    ]
    totals = states.totals([{"state": "working", "start": 0, "end": 4000}])
    assert totals["working"] == 4 and totals["idle"] == 0


def test_spend_series_spreads_growth_and_books_late_costs() -> None:
    found = [
        Segment("working", 0, 2 * H_MS, "ok", cost_start=1.0, cost_end=3.0),
        Segment("working", 3 * H_MS, 4 * H_MS, "ok", cost_start=3.5, cost_end=3.5),
    ]

    series = states.spend_series(found, 0, 4 * H_MS, H_MS)

    assert series == [1.0, 1.0, 0.0, 0.5]


def test_clip_open_ends_a_segment_the_session_no_longer_runs() -> None:
    open_seg = Segment("working", 1_000, None, "open")

    assert states.clip_open([open_seg], True, 5_000)[0].end is None
    assert states.clip_open([open_seg], False, 5_000)[0].end == 5_000


# ── The timeline route ─────────────────────────────────────────


async def test_the_timeline_strip_overlays_stuck_and_checks(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.stores.factory_store import sqlalchemy_store

    w, store, fid = cockpit.world, cockpit.store, cockpit.fid
    ic = w.id("ic")
    w.store.set_labels(ic, {F.LABEL_HANDLE: "rex"})
    now = float(int(time.time()))
    clock = Clock(now - 5 * 3600)
    rec = StateRecorder(cockpit.app.state, clock=clock, debounce_s=0.0)
    rec.on_relayed(ic, "running", None)
    await rec.drain()
    clock.now = now - 3 * 3600
    rec.on_status_edge(w.get("ic"), "idle", {})
    await rec.drain()
    pr, _ = store.upsert_pr(fid, "acme/widgets", 7, state="open", project_slug="api-version")
    store.upsert_task(fid, "api-version", "route", pr_id=pr.id, session_ids=[ic])
    store.insert_span(
        fid,
        kind="gate",
        name="checks",
        trace_key=f"pr:{pr.id}",
        started_at=int((now - 2.5 * 3600) * 1000),
        ended_at=int((now - 2 * 3600) * 1000),
        pr_id=pr.id,
        outcome="ok",
    )
    with monkeypatch.context() as patched:
        patched.setattr(sqlalchemy_store, "now_s", lambda: int(now - 4 * 3600))
        issue = await issues.raise_issue(
            cockpit.app.state,
            fid,
            rule="agent.quiet",
            target=f"member:{ic}",
            summary="(sample) quiet",
            evidence={"idle_s": 1200},
        )
    assert issue is not None and issue.first_seen == int(now - 4 * 3600)
    with monkeypatch.context() as patched:
        patched.setattr(issues, "_now", lambda: int(now - 3 * 3600))
        await issues.clear_issue(cockpit.app.state, fid, "agent.quiet", f"member:{ic}")
    w.store.append(
        ic,
        [
            NewConversationItem(
                type="function_call",
                response_id="r1",
                data=parse_item_data(
                    "function_call",
                    {"agent": "ic", "call_id": "c1", "name": "Bash", "arguments": "{}"},
                ),
            )
        ],
    )

    resp = await cockpit.client.get(
        f"/v1/factory/factories/{fid}/agents/rex/timeline?window=24h",
        headers={"X-Test-User": "carol"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_id"] == ic and body["handle"] == "rex" and body["window"] == "24h"
    states_seen = [p["state"] for p in body["segments"]]
    assert states_seen == ["idle", "working", "stuck", "idle", "checks", "idle"]
    assert body["totals"]["working"] == pytest.approx(3600, abs=5)
    assert body["totals"]["stuck"] == pytest.approx(3600, abs=5)
    assert body["totals"]["checks"] == pytest.approx(1800, abs=5)
    assert body["bucket_s"] == 3600 and len(body["series"]) == 24
    assert sum(b["tool_calls"] for b in body["series"]) == 1
    # The session was created just now, after its sample segments: nothing is missing.
    assert body["partial"] is False


async def test_a_snoozed_stuck_incident_ends_when_last_seen(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.server.factory.api import agent_timeline
    from omnigent.stores.factory_store import sqlalchemy_store

    fid, ic = cockpit.fid, cockpit.world.id("ic")
    now = int(time.time())
    with monkeypatch.context() as patched:
        patched.setattr(sqlalchemy_store, "now_s", lambda: now - 3600)
        issue = await issues.raise_issue(
            cockpit.app.state,
            fid,
            rule="agent.repeat",
            target=f"member:{ic}",
            summary="(sample) repeat",
            evidence={"count": 3},
        )
    assert issue is not None
    until_ms = now * 1000
    deps = cockpit.deps

    cockpit.store.update_issue(issue.id, status="snoozed", snoozed_until=now + 3600)
    snoozed = agent_timeline._stuck_intervals(deps, fid, ic, until_ms)
    cockpit.store.update_issue(issue.id, status="acked")
    acked = agent_timeline._stuck_intervals(deps, fid, ic, until_ms)

    start = (now - 3600) * 1000
    assert snoozed == [(start, issue.last_seen * 1000)] and issue.last_seen * 1000 < until_ms
    assert acked == [(start, until_ms)]


async def test_timeline_window_and_visibility(cockpit: Cockpit) -> None:
    w, fid = cockpit.world, cockpit.fid
    base = f"/v1/factory/factories/{fid}/agents"
    headers = {"X-Test-User": "carol"}

    six = await cockpit.client.get(f"{base}/{w.id('ic')}/timeline?window=6h", headers=headers)
    too_long = await cockpit.client.get(f"{base}/{w.id('ic')}/timeline?window=8d", headers=headers)
    hidden = await cockpit.client.get(f"{base}/{w.id('lead_b')}/timeline", headers=headers)

    assert six.status_code == 200 and six.json()["bucket_s"] == 900
    assert len(six.json()["series"]) == 24 and six.json()["segments"][0]["state"] == "idle"
    assert too_long.status_code == 400
    assert hidden.status_code == 404


# ── Stuck as a member field ─────────────────────────────────────


async def test_a_repeat_incident_marks_its_member_stuck(cockpit: Cockpit) -> None:
    w, fid = cockpit.world, cockpit.fid
    ic = w.id("ic")
    await issues.raise_issue(
        cockpit.app.state,
        fid,
        rule="agent.repeat",
        target=f"member:{ic}",
        summary="(sample) repeating",
        evidence={"tool": "Bash", "arg": "pnpm test", "count": 3},
    )
    snapshot.reset_cache()

    snap = await cockpit.client.get(
        f"/v1/factory/factories/{fid}", headers={"X-Test-User": "alice"}
    )
    rows = await cockpit.client.get(
        f"/v1/factory/factories/{fid}/agents", headers={"X-Test-User": "alice"}
    )

    member = next(m for m in snap.json()["members"] if m["session_id"] == ic)
    assert member["stuck"] is True
    assert member["stuck_reason"] == "same failing Bash call 3 times"
    assert member["stuck_issue"] == "INC-1" and member["stuck_since"]
    row = next(r for r in rows.json()["data"] if r["session_id"] == ic)
    assert row["stuck"] is True and "stuck" not in row["flags"]
    others = [m for m in snap.json()["members"] if m["session_id"] != ic]
    assert not any(m["stuck"] for m in others)

    await issues.clear_issue(cockpit.app.state, fid, "agent.repeat", f"member:{ic}")
    snapshot.reset_cache()
    again = await cockpit.client.get(
        f"/v1/factory/factories/{fid}", headers={"X-Test-User": "alice"}
    )
    member = next(m for m in again.json()["members"] if m["session_id"] == ic)
    assert member["stuck"] is False and member["stuck_reason"] is None
