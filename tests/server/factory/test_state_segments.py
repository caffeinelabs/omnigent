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
def _fresh() -> Iterator[None]:
    snapshot.reset_cache()
    issues.reset()
    yield
    snapshot.reset_cache()
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
    assert body["partial"] is True and body["recorded_since"] == pytest.approx(now - 5 * 3600, 2)


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
    assert row["stuck"] is True and "stuck" in row["flags"]
    others = [m for m in snap.json()["members"] if m["session_id"] != ic]
    assert not any(m["stuck"] for m in others)

    await issues.clear_issue(cockpit.app.state, fid, "agent.repeat", f"member:{ic}")
    snapshot.reset_cache()
    again = await cockpit.client.get(
        f"/v1/factory/factories/{fid}", headers={"X-Test-User": "alice"}
    )
    member = next(m for m in again.json()["members"] if m["session_id"] == ic)
    assert member["stuck"] is False and member["stuck_reason"] is None
