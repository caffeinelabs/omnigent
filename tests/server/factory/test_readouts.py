"""Readouts: the server measures every bet after its release and writes the result back.

Fixed rules set the verdict (won, flat, lost, inconclusive) and the
recommendation; a broken guardrail makes a readout lost; the plan is the one
frozen at apply; the result reaches the opportunity only through the ledger
(a ``test_result`` signal of the server's own source); people keep, drop,
iterate or extend, and going against the recommendation needs an owner, a
named reviewer and a reason. Every product word is sample data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from fastapi.routing import APIRoute

from omnigent import factory as F
from omnigent.runner.identity import RUNNER_TUNNEL_TOKEN_HEADER
from omnigent.server.auth import LEVEL_READ
from omnigent.server.factory import definition
from omnigent.server.factory.api import discovery_readouts as readouts_api
from omnigent.server.factory.definition_blocks import DISCOVERY_ENV
from omnigent.server.factory.discovery import opportunities, readouts
from omnigent.server.factory.discovery.readouts import judge
from omnigent.server.factory.origin import require_human_origin
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.discovery_helpers import (
    DAY,
    discovery_cockpit,
    ingest,
    make_opportunity,
)
from tests.server.factory.readout_helpers import (
    PLAN,
    base,
    file_bet,
    later_signals,
    now,
    readout,
    release,
    seeded,
)

DOWN = {"direction": "down", "low": -60.0, "high": -20.0, "unit": "%"}
UP = {"direction": "up", "low": 0.01, "high": 0.04, "unit": "share"}
SPAN = {"direction": "up", "low": -0.01, "high": 0.04, "unit": "share"}
HELD = [{"name": "support load (sample)", "state": "held"}]


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in discovery_cockpit(world, db_uri, monkeypatch):
        yield value


def status(cockpit: Cockpit) -> str:
    found = opportunities.evaluate_one(cockpit.deps, cockpit.fid, 1)
    assert found is not None
    return found.status


async def write(cockpit: Cockpit, at: int) -> list[Any]:
    return await readouts.write_due(cockpit.app.state, cockpit.fid, now=at)


async def decide(cockpit: Cockpit, ref: str, body: dict[str, Any], user: str = "alice") -> Any:
    return await cockpit.post(base(cockpit, f"/readouts/{ref}/decide"), body, user=user)


async def won_day_7(cockpit: Cockpit) -> int:
    """O-1: 5 accounts before the release, 2 after: −60 % against −60 to −20 % (won)."""
    seeded(cockpit, before_days=10)
    later_signals(cockpit, 2, age_days=4)
    file_bet(cockpit)
    released = now() - 8 * DAY
    release(cockpit, released)
    await write(cockpit, now())
    return released


# ── The fixed rules ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("expected", "effect", "verdict", "recommendation", "predicted"),
    [
        (DOWN, -30.0, "won", "keep", "inside"),
        (DOWN, -75.0, "won", "keep", "below"),
        (DOWN, -20.0, "won", "keep", "inside"),
        (DOWN, -10.0, "flat", "iterate", "above"),
        (DOWN, 0.0, "flat", "iterate", "above"),
        # A move the wrong way smaller than the near end is noise.
        (DOWN, 15.0, "flat", "iterate", "above"),
        (DOWN, 25.0, "lost", "roll_back", "above"),
        (UP, 0.02, "won", "keep", "inside"),
        (UP, 0.05, "won", "keep", "above"),
        (UP, 0.005, "flat", "iterate", "below"),
        (UP, -0.02, "lost", "roll_back", "below"),
        # A range that spans zero promises only the direction.
        (SPAN, 0.001, "won", "keep", "inside"),
        (SPAN, -0.001, "lost", "roll_back", "inside"),
    ],
)
def test_the_verdict_rules_are_fixed(
    expected: dict[str, Any], effect: float, verdict: str, recommendation: str, predicted: str
) -> None:
    found = judge(expected=expected, effect=effect, n=10, min_n=5, guardrails=HELD)

    assert (found.verdict, found.recommendation, found.predicted) == (
        verdict,
        recommendation,
        predicted,
    )


def test_a_broken_guardrail_makes_it_lost_whatever_the_metric_did() -> None:
    broken = [{"name": "support load (sample)", "state": "broken"}]

    found = judge(expected=DOWN, effect=-40.0, n=10, min_n=5, guardrails=broken)

    assert (found.verdict, found.recommendation) == ("lost", "roll_back")
    assert found.predicted == "inside" and "support load" in found.why


def test_too_little_data_or_a_failed_check_is_inconclusive_and_extends_once() -> None:
    small = judge(expected=DOWN, effect=-40.0, n=3, min_n=5)
    check = judge(
        expected=DOWN,
        effect=None,
        n=0,
        min_n=5,
        checks=[{"name": "baseline", "ok": False, "note": "no baseline (sample)"}],
    )
    again = judge(expected=DOWN, effect=-40.0, n=3, min_n=5, extended=True)

    assert (small.verdict, small.recommendation) == ("inconclusive", "extend")
    assert "n=3 < 5" in small.why
    assert (check.verdict, check.recommendation, check.why) == (
        "inconclusive",
        "extend",
        "no baseline (sample)",
    )
    assert (again.verdict, again.recommendation) == ("inconclusive", "iterate")


def test_a_persons_verdict_and_what_follows_each_recommendation() -> None:
    answers = {
        a: judge(expected=DOWN, effect=None, n=1, min_n=1, answer=a)
        for a in ("helped", "no_change", "hurt")
    }

    assert {a: (j.verdict, j.recommendation) for a, j in answers.items()} == {
        "helped": ("won", "keep"),
        "no_change": ("flat", "iterate"),
        "hurt": ("lost", "roll_back"),
    }
    assert readouts.aligned("keep", "keep") and not readouts.aligned("keep", "drop")
    assert readouts.aligned("roll_back", "drop") and readouts.aligned("roll_back", "iterate")
    assert readouts.aligned("iterate", "drop") and not readouts.aligned("iterate", "keep")
    assert readouts.aligned("extend", "extend") and not readouts.aligned("extend", "keep")


# ── Measuring and writing back ─────────────────────────────────


async def test_a_signal_trend_readout_writes_back_through_the_ledger(cockpit: Cockpit) -> None:
    await won_day_7(cockpit)

    day7, day28 = readout(cockpit, 7), readout(cockpit, 28)
    assert (day7.status, day7.verdict, day7.recommendation) == ("written", "won", "keep")
    assert day7.result["effect"] == -60.0 and day7.result["predicted_vs_actual"] == "inside"
    assert (day7.result["n"], day7.result["inputs"]["accounts_before"]) == (7, 5)
    assert day7.result["guardrails"] == [
        {
            "name": "support load (sample)",
            "state": "unmeasured",
            "note": "no source measures it yet",
        }
    ]
    assert day28.status == "scheduled"
    signal = cockpit.store.get_signal(cockpit.fid, day7.signal)
    assert signal is not None
    assert (signal.source, signal.source_kind, signal.kind, signal.say_do) == (
        "factory",
        "factory",
        "test_result",
        "do",
    )
    assert signal.meta["verdict"] == "won" and signal.meta["reaches"] == "moderate"
    assert signal.meta["readout"] == "C-1-d7"
    links = cockpit.store.list_links(cockpit.fid, opportunities=[1])
    assert any(link.signal == signal.number and link.attached_kind == "server" for link in links)
    found = opportunities.evaluate_one(cockpit.deps, cockpit.fid, 1)
    assert found is not None and found.status == "measured"
    # A signal trend shows moderate at most, and a readout is no user's voice.
    assert (found.meter.level, found.meter.accounts) == ("moderate", 7)
    assert found.row.result["verdict"] == "won" and found.row.result["readout"] == "C-1-d7"
    [written] = cockpit.events("readout.written")
    assert written.data["verdict"] == "won" and written.data["final"] is False
    # Only the last readout, or a lost one, waits on a person.
    assert not [
        d for d in cockpit.store.list_decisions(factory_id=cockpit.fid) if d.source == "readout"
    ]
    room = [m for m in cockpit.store.list_chat(cockpit.fid, limit=50) if "C-1 · day 7" in m.body]
    assert room and room[0].thread == F.THREAD_FACTORY


async def test_the_frozen_plan_is_what_is_measured(cockpit: Cockpit) -> None:
    seeded(cockpit, before_days=10)
    later_signals(cockpit, 2, age_days=4)
    row, _projects = file_bet(cockpit)
    release(cockpit, now() - 8 * DAY)
    # Moving the goalposts after apply changes nothing the readout reads.
    cockpit.store.update_proposal(
        row.id, bet={**PLAN, "expected": {"direction": "up", "low": 1, "high": 2, "unit": "%"}}
    )

    await write(cockpit, now())

    day7 = readout(cockpit, 7)
    assert day7.verdict == "won" and day7.plan["expected"] == DOWN
    assert day7.plan["frozen_by"] == "alice (sample)" and day7.plan_version == 1


async def test_signals_against_and_server_signals_are_not_counted(cockpit: Cockpit) -> None:
    seeded(cockpit, before_days=10)
    against = ingest(cockpit, "web", ["I never need a private draft (sample)"], age_days=4)
    cockpit.store.put_link(
        cockpit.fid, against[0].number, 1, attached_by="scout", attached_kind="agent", counter=True
    )
    file_bet(cockpit)
    release(cockpit, now() - 8 * DAY)

    await write(cockpit, now())

    day7 = readout(cockpit, 7)
    assert day7.result["inputs"]["accounts_after"] == 0
    assert (day7.verdict, day7.result["effect"]) == ("won", -100.0)


async def test_a_unit_it_cannot_read_or_a_missing_baseline_is_inconclusive(
    cockpit: Cockpit,
) -> None:
    seeded(cockpit, before_days=2)  # after the release: nothing before it
    file_bet(cockpit, plan={"expected": {"direction": "down", "low": -5, "high": -1, "unit": "%"}})
    file_bet(
        cockpit,
        plan={"expected": {"direction": "up", "low": 1, "high": 3, "unit": "usd"}},
        slugs=("draft-cost",),
    )
    release(cockpit, now() - 8 * DAY, number=1)
    release(cockpit, now() - 8 * DAY, number=2)

    await write(cockpit, now())

    first, second = readout(cockpit, 7, 1), readout(cockpit, 7, 2)
    assert (first.verdict, first.recommendation) == ("inconclusive", "extend")
    assert first.result["data_checks"][0]["name"] == "baseline"
    assert second.verdict == "inconclusive" and "not usd" in second.result["why"]


async def test_methods_without_a_source_say_so(cockpit: Cockpit) -> None:
    seeded(cockpit)
    file_bet(cockpit, plan={"measurement": {"method": "before_after", "readout_days": [7]}})
    release(cockpit, now() - 8 * DAY)

    await write(cockpit, now())

    day7 = readout(cockpit, 7)
    assert (day7.method, day7.verdict) == ("before_after", "inconclusive")
    assert "needs a metric binding" in day7.result["why"]


async def test_a_hooked_method_measures(cockpit: Cockpit) -> None:
    def adoption(ctx: readouts.MethodContext) -> readouts.Measured:
        return readouts.Measured(effect=0.03, n=500, min_n=100, unit="share")

    readouts.register_method("adoption", adoption)
    try:
        seeded(cockpit)
        file_bet(
            cockpit,
            plan={"expected": UP, "measurement": {"method": "adoption", "readout_days": [7]}},
        )
        release(cockpit, now() - 8 * DAY)
        await write(cockpit, now())
    finally:
        readouts.register_method("adoption", None)

    day7 = readout(cockpit, 7)
    assert (day7.verdict, day7.result["predicted_vs_actual"]) == ("won", "inside")
    signal = cockpit.store.get_signal(cockpit.fid, day7.signal)
    assert signal is not None and signal.meta["reaches"] == "strong"


# ── People decide ──────────────────────────────────────────────


async def test_a_reported_broken_guardrail_makes_it_lost_and_asks_a_person(
    cockpit: Cockpit,
) -> None:
    await won_day_7(cockpit)
    first = readout(cockpit, 7).signal

    resp = await decide(cockpit, "C-1-d7", {"guardrails_broken": ["support load (sample)"]})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["verdict"], body["recommendation"]) == ("lost", "roll_back")
    assert body["guardrails"][0]["state"] == "broken"
    day7 = readout(cockpit, 7)
    assert day7.signal != first
    links = {link.signal for link in cockpit.store.list_links(cockpit.fid, opportunities=[1])}
    assert first not in links and day7.signal in links
    found = opportunities.evaluate_one(cockpit.deps, cockpit.fid, 1)
    assert found is not None and found.meter.level == "weak"
    [asked] = [
        d for d in cockpit.store.list_decisions(factory_id=cockpit.fid) if d.source == "readout"
    ]
    assert asked.question.startswith("Keep or drop C-1?") and asked.status == "open"
    assert [o["id"] for o in asked.options] == ["drop", "iterate", "other"]
    unknown = await decide(cockpit, "C-1-d7", {"guardrails_broken": ["page speed (sample)"]})
    assert unknown.status_code == 422 and error_code(unknown) == "unknown_guardrail"


async def test_the_last_readout_waits_in_needs_you_and_one_click_keeps_it(
    cockpit: Cockpit,
) -> None:
    released = await won_day_7(cockpit)
    later_signals(cockpit, 1, age_days=1, tag="late")

    await write(cockpit, released + 29 * DAY)

    day28 = readout(cockpit, 28)
    assert day28.status == "written" and day28.decision_id is not None
    decision = cockpit.store.get_decision(day28.decision_id)
    assert decision is not None and decision.source == "readout"
    assert decision.options[0] == {"id": "keep", "label": "Keep it", "recommended": True}
    asks = await cockpit.client.get(base(cockpit, "/asks"), headers={"X-Test-User": "carol"})
    [row] = [a for a in asks.json()["asks"] if a["kind"] == "readout"]
    assert row["primary_action"] == {
        "kind": "answer",
        "label": "Keep it",
        "href": None,
        "option_id": "keep",
        "peek": None,
    }

    resp = await cockpit.post(
        f"/v1/factory/decisions/{decision.id}/answer", {"option_id": "keep"}, user="carol"
    )

    assert resp.status_code == 200, resp.text
    day28 = readout(cockpit, 28)
    assert (day28.status, day28.decision, day28.review["via"]) == (
        "decided",
        "keep",
        f"decision {decision.ref}",
    )
    assert status(cockpit) == "kept"
    assert cockpit.events("readout.decided")[0].data["against_recommendation"] is False


async def test_going_against_the_recommendation_needs_an_owner_a_reviewer_and_a_reason(
    cockpit: Cockpit,
) -> None:
    await won_day_7(cockpit)

    carol = await decide(cockpit, "C-1-d7", {"decision": "drop", "reason": "x"}, user="carol")
    no_reviewer = await decide(
        cockpit, "C-1-d7", {"decision": "drop", "reason": "costly (sample)"}
    )
    no_reason = await decide(cockpit, "C-1-d7", {"decision": "drop", "reviewer": "ana (sample)"})
    resp = await decide(
        cockpit,
        "C-1-d7",
        {"decision": "drop", "reviewer": "ana (sample)", "reason": "it costs too much (sample)"},
    )

    assert carol.status_code == 403 and error_code(carol) == "owners_only"
    assert no_reviewer.status_code == 422 and error_code(no_reviewer) == "reviewer_required"
    assert no_reason.status_code == 422 and error_code(no_reason) == "reason_required"
    assert resp.status_code == 200, resp.text
    decision = resp.json()["decision"]
    assert decision["against_recommendation"] is True
    assert (decision["reviewer"], decision["reason"]) == (
        "ana (sample)",
        "it costs too much (sample)",
    )
    assert readout(cockpit, 28).status == "cancelled"
    assert status(cockpit) == "dropped"
    again = await decide(cockpit, "C-1-d7", {"decision": "keep"})
    assert again.status_code == 409 and error_code(again) == "already_decided"
    room = [m.body for m in cockpit.store.list_chat(cockpit.fid, limit=50)]
    assert any("Turn the flag draft_share off" in b for b in room)


async def test_an_operator_follows_the_recommendation(cockpit: Cockpit) -> None:
    await won_day_7(cockpit)

    resp = await decide(cockpit, "C-1-d7", {"decision": "keep"}, user="carol")

    assert resp.status_code == 200, resp.text
    assert resp.json()["decision"]["by"].startswith("carol")
    assert status(cockpit) == "kept"


async def test_iterate_closes_the_bet_and_the_opportunity_falls_back_to_its_evidence(
    cockpit: Cockpit,
) -> None:
    seeded(cockpit, before_days=10)
    later_signals(cockpit, 5, age_days=4)  # as many after as before: flat
    file_bet(cockpit, plan={"measurement": {"method": "signal_trend", "readout_days": [7]}})
    release(cockpit, now() - 8 * DAY)
    await write(cockpit, now())
    assert (readout(cockpit, 7).verdict, status(cockpit)) == ("flat", "measured")

    resp = await decide(cockpit, "C-1-d7", {"decision": "iterate"})

    assert resp.status_code == 200, resp.text
    found = opportunities.evaluate_one(cockpit.deps, cockpit.fid, 1)
    assert found is not None and found.status == "validated"
    assert found.row.result["iterated"] == ["C-1"]


async def test_extend_adds_one_later_readout_once(cockpit: Cockpit) -> None:
    seeded(cockpit, before_days=10)
    plan = {"measurement": {"method": "signal_trend", "readout_days": [7], "min_n": 50}}
    file_bet(cockpit, plan=plan)
    released = now() - 8 * DAY
    release(cockpit, released)
    await write(cockpit, now())
    assert readout(cockpit, 7).recommendation == "extend"

    resp = await decide(cockpit, "C-1-d7", {"decision": "extend"})

    assert resp.status_code == 200, resp.text
    day14 = readout(cockpit, 14)
    assert (day14.status, day14.due_at, day14.plan["extension"]) == (
        "scheduled",
        released + 14 * DAY,
        True,
    )
    await write(cockpit, released + 15 * DAY)
    day14 = readout(cockpit, 14)
    assert (day14.verdict, day14.recommendation) == ("inconclusive", "iterate")
    again = await decide(
        cockpit, "C-1-d14", {"decision": "extend", "reviewer": "ana (sample)", "reason": "x"}
    )
    assert again.status_code == 409 and error_code(again) == "already_extended"


async def test_a_persons_verdict_waits_for_an_answer_with_a_reason(cockpit: Cockpit) -> None:
    seeded(cockpit)
    file_bet(cockpit, plan={"measurement": {"method": "human_verdict", "readout_days": [7]}})
    release(cockpit, now() - 8 * DAY)
    await write(cockpit, now())

    day7 = readout(cockpit, 7)
    assert (day7.status, day7.verdict) == ("awaiting_answer", None)
    question = cockpit.store.get_decision(day7.decision_id)
    assert question is not None and question.question.startswith("Did C-1 help?")
    first = await decide(cockpit, "C-1-d7", {"decision": "keep"})
    no_reason = await decide(cockpit, "C-1-d7", {"answer": "helped"})
    resp = await decide(
        cockpit, "C-1-d7", {"answer": "helped", "reason": "three teams use it (sample)"}
    )

    assert first.status_code == 409 and error_code(first) == "answer_first"
    assert no_reason.status_code == 422 and error_code(no_reason) == "reason_required"
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["verdict"], body["recommendation"], body["reaches"]) == ("won", "keep", "weak")
    assert body["answer"]["reason"] == "three teams use it (sample)"
    closed = cockpit.store.get_decision(question.id)
    assert closed is not None and closed.status == "expired"
    signal = cockpit.store.get_signal(cockpit.fid, readout(cockpit, 7).signal)
    assert signal is not None and signal.meta["reaches"] == "weak"
    twice = await decide(cockpit, "C-1-d7", {"answer": "hurt", "reason": "x"})
    assert twice.status_code == 409 and error_code(twice) == "already_answered"


async def test_an_eval_factory_reads_out_in_minutes(cockpit: Cockpit) -> None:
    cockpit.world.store.set_labels(cockpit.fid, {F.LABEL_MODE: F.MODE_EVAL})
    t = now()
    before = ingest(
        cockpit, "repo-issues", [f"before {i} (sample)" for i in range(3)], age_days=800 / DAY
    )
    make_opportunity(cockpit, before)
    later_signals(cockpit, 1, age_days=300 / DAY)
    plan = {
        "expected": {"direction": "down", "low": -5, "high": -1, "unit": "signals/week"},
        "measurement": {"method": "signal_trend", "readout_days": [7], "min_n": 3},
    }
    file_bet(cockpit, plan=plan)
    released = t - 600
    [row] = release(cockpit, released)
    assert row.due_at == released + 7 * readouts.EVAL_DAY_S

    done = await readouts.tick(cockpit.app.state, cockpit.fid, now=t)

    assert done["written"] == 1
    day7 = readout(cockpit, 7)
    assert (day7.verdict, day7.result["effect"]) == ("won", -2.0)
    assert status(cockpit) == "measured"
    ledger = readouts.prediction_ledger(cockpit.deps, cockpit.fid)
    assert ledger["readouts"] == 1 and ledger["inside"] == {"n": 1, "of": 1, "share": 1.0}


# ── Routes ─────────────────────────────────────────────────────


def test_every_readout_write_route_requires_human_origin() -> None:
    writes = [
        route
        for route in readouts_api.router.routes
        if isinstance(route, APIRoute) and route.methods & {"POST", "PUT", "PATCH", "DELETE"}
    ]
    assert len(writes) == 2
    for route in writes:
        calls = [dep.call for dep in route.dependant.dependencies]
        assert require_human_origin in calls, route.path


async def test_readouts_are_dark_unless_discovery_runs(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    definition.set_values(
        cockpit.deps, cockpit.fid, {"discovery.enabled": False}, created_by="alice"
    )
    definition.invalidate(cockpit.fid)
    off = await cockpit.client.get(base(cockpit, "/readouts"), headers={"X-Test-User": "alice"})
    assert off.status_code == 404 and error_code(off) == "discovery_off"
    assert (await readouts.tick(cockpit.app.state, cockpit.fid))["written"] == 0
    monkeypatch.delenv(DISCOVERY_ENV)
    decide_off = await decide(cockpit, "C-1-d7", {"decision": "keep"})
    assert decide_off.status_code == 404 and error_code(decide_off) == "discovery_off"


async def test_viewers_read_agents_never_decide(cockpit: Cockpit) -> None:
    await won_day_7(cockpit)
    stranger = await cockpit.client.get(base(cockpit, "/readouts"), headers={"X-Test-User": "bob"})
    cockpit.permissions.grant("bob", cockpit.fid, LEVEL_READ)
    agent = {"X-Test-User": "alice", RUNNER_TUNNEL_TOKEN_HEADER: "tok"}

    listed = await cockpit.client.get(base(cockpit, "/readouts"), headers={"X-Test-User": "bob"})
    one = await cockpit.client.get(
        base(cockpit, "/readouts/C-1-d7"), headers={"X-Test-User": "bob"}
    )
    viewer = await decide(cockpit, "C-1-d7", {"decision": "keep"}, user="bob")
    robot = await cockpit.client.post(
        base(cockpit, "/readouts/C-1-d7/decide"), json={"decision": "keep"}, headers=agent
    )

    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert [r["key"] for r in body["data"]] == ["C-1-d7", "C-1-d28"]
    assert body["release"] == "merged" and body["ledger"]["readouts"] == 1
    assert one.status_code == 200 and one.json()["verdict"] == "won"
    assert [o["decision"] for o in one.json()["options"] if not o["against_recommendation"]] == [
        "keep"
    ]
    assert viewer.status_code == 403
    assert robot.status_code == 403 and error_code(robot) == "human_only"
    assert stranger.status_code in (403, 404)
    missing = await cockpit.client.get(
        base(cockpit, "/readouts/C-9-d7"), headers={"X-Test-User": "alice"}
    )
    assert missing.status_code == 404 and error_code(missing) == "unknown_readout"
    early = await decide(cockpit, "C-1-d28", {"decision": "keep"})
    assert early.status_code == 409 and error_code(early) == "not_due"
