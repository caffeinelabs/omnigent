"""Discovery trials: discovery's method trials on discovery guards, over 7 to 56 days.

On a server that runs discovery, a change to the rubric, the thresholds or
Scout's brief may run as a trial; its guards are discovery's own (quote
failures and privacy events stay at 0, accepted bets fall at most 10 pp, cost
per accepted bet rises at most 20 %), its window 7 to 56 days (14 by default)
and its n the bets people decided. Discovery's other settings apply as a
version, and the method never shares a trial with other changes. A server
without discovery keeps the old rule. Every product word is sample data.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.entities.factory import EventDraft
from omnigent.server.factory import definition, evaluate
from omnigent.server.factory.definition_blocks import DISCOVERY_ENV, DISCOVERY_SETTING
from omnigent.server.factory.telemetry import metrics
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.definition_helpers import answer, propose, put, row
from tests.server.factory.discovery_helpers import DAY, discovery_cockpit

H = 3600
RUBRIC = {"op": "replace", "path": "discovery.rubric.decay_half_life_days", "value": 45}
EFFECT = {"metric": "bet_acceptance", "direction": "up"}
WINDOW = 14 * DAY


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    metrics.reset_cache()
    async for value in discovery_cockpit(world, db_uri, monkeypatch):
        yield value


# ── Which changes trial, on which guards ───────────────────────


def test_without_discovery_the_method_applies_as_a_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DISCOVERY_ENV, raising=False)

    ok, reason = definition.trialable(["discovery.rubric.window_days"])

    assert not ok and reason is not None and "discovery guards" in reason
    assert definition.guard_set(["discovery.rubric.window_days"]) == "delivery"
    assert "briefs.scout" in definition.not_trialable_table()
    assert definition.trial_window_h(["briefs.scout"]) == (24, 48, 168)


def test_with_discovery_the_method_trials_on_its_own_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(DISCOVERY_ENV, "1")

    for paths in (
        ["discovery.rubric.bands.now"],
        ["discovery.thresholds.min_accounts"],
        ["briefs.scout"],
        ["discovery.rubric.ladder.weak", "briefs.scout"],
    ):
        assert definition.trialable(paths) == (True, None), paths
        assert definition.guard_set(paths) == "discovery"
        assert definition.trial_window_h(paths) == (168, 336, 1344)
    assert definition.guard_set(["briefs.ic"]) == "delivery"
    setting = definition.not_trialable(["discovery.enabled", "discovery.sources.web"])
    assert setting == {"discovery": DISCOVERY_SETTING}
    ok, mixed = definition.trialable(["discovery.rubric.bands.now", "loops.review.rounds"])
    assert not ok and mixed is not None and "split them" in mixed
    table = definition.not_trialable_table()
    assert "briefs.scout" not in table and table["discovery"] == DISCOVERY_SETTING
    assert table["strategy"] == "judged on outcomes"


def test_the_trial_window_follows_the_guard_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DISCOVERY_ENV, "1")
    rubric = ["discovery.rubric.bands.now"]

    assert definition.trial_hours(rubric, None) == 14 * 24
    assert definition.trial_hours(rubric, 56 * 24) == 56 * 24
    assert definition.trial_hours(["loops.review.rounds"], None) == 48
    for paths, hours in ((rubric, 100), (rubric, 57 * 24), (["loops.review.rounds"], 200)):
        with pytest.raises(Exception) as caught:
            definition.trial_hours(paths, hours)
        assert getattr(caught.value, "code", None) == "invalid_trial_window"


# ── Starting one ───────────────────────────────────────────────


async def test_an_owner_trials_a_rubric_change_for_fourteen_days(cockpit: Cockpit) -> None:
    short = await put(cockpit, RUBRIC, trial=True, trial_h=48, expected_effect=EFFECT)
    resp = await put(cockpit, RUBRIC, trial=True, expected_effect=EFFECT)

    assert short.status_code == 422 and error_code(short) == "invalid_trial_window"
    assert resp.status_code == 200, resp.text
    trial = row(cockpit)
    assert trial.status == "trial"
    assert trial.trial_until - trial.applied_at == 14 * 24 * H
    preview = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/definition/preview",
        {"changes": [{**RUBRIC, "value": 50}]},
    )
    assert preview.json()["trialable"]["code"] == "trial_overlap"


async def test_the_preview_says_which_guards_judge_it(cockpit: Cockpit) -> None:
    resp = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/definition/preview", {"changes": [RUBRIC]}
    )
    view = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/definition", headers={"X-Test-User": "alice"}
    )

    assert resp.json()["trialable"] == {
        "ok": True,
        "trial_h": 336,
        "guards": "discovery",
        "blocks": ["discovery"],
    }
    body = view.json()
    assert body["trial_windows_h"] == {"delivery": [24, 48, 168], "discovery": [168, 336, 1344]}
    assert body["not_trialable_why"]["discovery"] == DISCOVERY_SETTING


async def test_sentinel_proposes_a_rubric_trial_with_a_discovery_metric(cockpit: Cockpit) -> None:
    filed = await propose(cockpit, changes=[RUBRIC], expected_effect=EFFECT)

    assert filed.status_code == 200, filed.text
    decision = cockpit.store.get_decision(row(cockpit).decision_id)
    assert decision is not None and decision.options[0]["label"] == "Approve · trial 14 days"
    assert (await answer(cockpit, 1)).status_code == 200
    trial = row(cockpit)
    assert trial.status == "trial" and trial.trial_until - trial.applied_at == 336 * H
    assert trial.result["guards"] == "discovery"


async def test_an_agents_delivery_trial_length_becomes_the_discovery_default(
    cockpit: Cockpit,
) -> None:
    # An agent's tool bounds trial_h to 24-168 h; a rubric trial takes its own window.
    filed = await propose(cockpit, changes=[RUBRIC], expected_effect=EFFECT, trial_h=48)

    assert filed.status_code == 200, filed.text
    proposal = row(cockpit)
    assert proposal.result["trial_h"] == 336
    assert any(w.startswith("trial_h: a trial on discovery guards") for w in proposal.warnings)


async def test_a_factory_with_discovery_off_applies_the_method_as_a_version(
    cockpit: Cockpit,
) -> None:
    definition.set_values(
        cockpit.deps, cockpit.fid, {"discovery.enabled": False}, created_by="alice"
    )
    definition.invalidate(cockpit.fid)
    paths = ["discovery.rubric.decay_half_life_days"]

    ok, reason = definition.trialable(paths, factory_id=cockpit.fid)
    trial = await put(cockpit, RUBRIC, trial=True, expected_effect=EFFECT)

    assert not ok and reason is not None and "discovery is off" in reason
    assert definition.guard_set(paths, factory_id=cockpit.fid) == "delivery"
    assert definition.trial_window_h(paths, factory_id=cockpit.fid) == (24, 48, 168)
    assert trial.status_code == 422 and error_code(trial) == "not_trialable"
    version = await put(cockpit, RUBRIC)
    assert version.status_code == 200, version.text
    assert row(cockpit).status == "applied"


async def test_a_trial_keeps_the_guards_it_started_with(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    trial, applied = await _trial(cockpit)
    assert trial.result["guards"] == "discovery"
    for days in (1, 3, 5):
        _bet(cockpit, applied + days * DAY)
    # The server stops running discovery while it trials: it is still judged on its own guards.
    monkeypatch.delenv(DISCOVERY_ENV)

    ev = evaluate.evaluate_trial(cockpit.deps, row(cockpit), trial.trial_until + 1)

    assert {g.metric for g in ev.guards} == set(metrics.DISCOVERY_GUARDS)


# ── Judging one ────────────────────────────────────────────────


async def _trial(cockpit: Cockpit) -> tuple[Any, int]:
    """A rubric change on trial whose 14-day window ends an hour from now."""
    resp = await put(cockpit, RUBRIC, trial=True, expected_effect=EFFECT)
    assert resp.status_code == 200, resp.text
    applied = int(time.time()) - WINDOW + H
    trial = cockpit.store.update_proposal(
        row(cockpit).id, applied_at=applied, trial_until=applied + WINDOW
    )
    assert trial is not None
    return trial, applied


def _bet(cockpit: Cockpit, at: int, status: str = "applied") -> None:
    cockpit.store.create_proposal(
        cockpit.fid,
        kind="projects",
        title="a bet (sample)",
        author_kind="agent",
        author=cockpit.world.id("lead_a"),
        status=status,
        bet={"kind": "test", "outcome": "a test (sample)"},
        opportunity="O-1",
        decided_at=at,
        applied_at=at if status == "applied" else None,
    )


def _event(cockpit: Cockpit, kind: str, at: int, **data: Any) -> None:
    cockpit.store.append_event(
        EventDraft(
            factory_id=cockpit.fid, kind=kind, summary=f"{kind} (sample)", data=data, ts=at * 1000
        )
    )


def _scout_cost(cockpit: Cockpit, at: int, usd: float) -> None:
    cockpit.store.add_cost_daily(
        cockpit.fid,
        project_slug="",
        role="scout",
        agent_name="Scout",
        model="sonnet",
        day_utc=time.strftime("%Y-%m-%d", time.gmtime(at)),
        cost_usd=usd,
    )


def _guard(trial: Any, name: str) -> dict[str, Any]:
    return next(g for g in trial.result["evaluation"]["guards"] if g["metric"] == name)


async def test_accepted_bets_up_and_guards_held_keep_it(cockpit: Cockpit) -> None:
    trial, applied = await _trial(cockpit)
    _bet(cockpit, applied - 5 * DAY, "rejected")
    _bet(cockpit, applied - 4 * DAY)
    for days in (1, 3, 5):
        _bet(cockpit, applied + days * DAY)

    done = await evaluate.tick(cockpit.app.state, cockpit.fid, now=trial.trial_until + 1)

    assert done == ["C-1:kept"]
    ev = row(cockpit).result["evaluation"]
    assert (ev["n"], ev["min_n"]) == (3, evaluate.MIN_DECIDED_BETS)
    assert {g["metric"] for g in ev["guards"]} == set(metrics.DISCOVERY_GUARDS)
    assert ev["target"]["met"] is True and ev["activity"] == {"decided_bets": 3}


async def test_a_quote_failure_in_the_window_reverts_it(cockpit: Cockpit) -> None:
    trial, applied = await _trial(cockpit)
    for days in (1, 3, 5):
        _bet(cockpit, applied + days * DAY)
    _event(cockpit, "bet.quote_refused", applied + 2 * DAY, code="quote_unverified")

    done = await evaluate.tick(cockpit.app.state, cockpit.fid, now=trial.trial_until + 1)

    assert done == ["C-1:reverted"]
    quotes = _guard(row(cockpit), "quote_failures")
    assert (quotes["trial"], quotes["status"], quotes["allowed"]) == (1, "regressed", "≤ 0")


async def test_a_privacy_refusal_or_fewer_accepted_bets_revert_it(cockpit: Cockpit) -> None:
    trial, applied = await _trial(cockpit)
    for days in (1, 2, 3):
        _bet(cockpit, applied - days * DAY)
    _bet(cockpit, applied + DAY)
    _bet(cockpit, applied + 2 * DAY, "rejected")
    _bet(cockpit, applied + 3 * DAY, "rejected")
    _event(
        cockpit,
        "privacy.source_refused",
        applied + DAY,
        source="forum",
        error_code="source_not_allowed",
    )

    done = await evaluate.tick(cockpit.app.state, cockpit.fid, now=trial.trial_until + 1)

    assert done == ["C-1:reverted"]
    reverted = row(cockpit)
    assert _guard(reverted, "privacy_events")["status"] == "regressed"
    acceptance = _guard(reverted, "bet_acceptance")
    assert (acceptance["baseline"], acceptance["status"]) == (1.0, "regressed")


async def test_cost_per_accepted_bet_is_the_discovery_seats_spend(cockpit: Cockpit) -> None:
    trial, applied = await _trial(cockpit)
    _bet(cockpit, applied - 3 * DAY)
    _scout_cost(cockpit, applied - 3 * DAY, 10.0)
    for days in (1, 3, 5):
        _bet(cockpit, applied + days * DAY)
    _scout_cost(cockpit, applied + 2 * DAY, 60.0)

    await evaluate.tick(cockpit.app.state, cockpit.fid, now=trial.trial_until + 1)

    cost = _guard(row(cockpit), "cost_per_accepted_bet")
    assert (cost["baseline"], cost["trial"], cost["status"]) == (10.0, 20.0, "regressed")


async def test_too_few_decided_bets_go_to_the_owners_and_none_extends(cockpit: Cockpit) -> None:
    trial, _applied = await _trial(cockpit)

    first = await evaluate.tick(cockpit.app.state, cockpit.fid, now=trial.trial_until + 1)

    assert first == ["C-1:extended"]
    extended = [e for e in cockpit.events() if e.kind == evaluate.EXTENDED_KIND]
    assert "nobody decided a bet" in extended[0].summary

    trial = row(cockpit)
    _bet(cockpit, trial.trial_until - DAY)
    second = await evaluate.tick(cockpit.app.state, cockpit.fid, now=trial.trial_until + 1)

    assert second == ["C-1:inconclusive"]
    ev = row(cockpit).result["evaluation"]
    assert ev["n"] == 1 and "n=1 < 3" in ev["reason"]


async def test_a_busy_access_log_never_hides_a_guard_event(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    trial, applied = await _trial(cockpit)
    monkeypatch.setattr(metrics, "_DISCOVERY_EVENT_LIMIT", 20)
    # More access-log rows than the guard query reads, all before the refusal.
    for i in range(30):
        _event(cockpit, "source.sync", applied - DAY + i, source="web", outcome="ok")
    _event(cockpit, "bet.quote_refused", applied + 2 * DAY, code="quote_unverified")
    _event(cockpit, "privacy.source_refused", applied + 3 * DAY, source="web")
    window = metrics.Window(applied, trial.trial_until)

    found = metrics.compute(
        cockpit.deps,
        cockpit.fid,
        ["quote_failures", "privacy_events"],
        window=window,
        use_cache=False,
    )

    assert metrics.headline(found["quote_failures"]) == 1
    assert metrics.headline(found["privacy_events"]) == 1


async def test_every_refused_quote_counts_as_a_quote_failure(cockpit: Cockpit) -> None:
    trial, applied = await _trial(cockpit)
    # A bet, an opportunity and Scout's paraphrase while coding signals.
    for i, kind in enumerate(
        ("bet.quote_refused", "opportunity.quote_refused", "signal.quote_refused")
    ):
        _event(cockpit, kind, applied + (i + 1) * DAY, code="quote_unverified")
    window = metrics.Window(applied, trial.trial_until)

    found = metrics.compute(
        cockpit.deps, cockpit.fid, ["quote_failures"], window=window, use_cache=False
    )

    assert metrics.headline(found["quote_failures"]) == 3


async def test_delivery_metrics_never_read_discovery_facts(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> list[Any]:
        raise AssertionError("a delivery metric read the readouts")

    monkeypatch.setattr(cockpit.store, "list_readouts", boom)
    at = int(time.time())
    window = metrics.Window(at - WINDOW, at)

    found = metrics.compute(
        cockpit.deps, cockpit.fid, ["throughput"], window=window, use_cache=False
    )

    assert "throughput" in found


def test_the_discovery_family_stays_out_of_the_insights_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(DISCOVERY_ENV, "1")
    assert not set(metrics.DISCOVERY_CATALOG) & set(metrics.CATALOG)
    assert all(m.family == "discovery" for m in metrics.DISCOVERY_CATALOG.values())
    assert metrics.known("bet_acceptance") and metrics.known("merge_acceptance")
    assert not metrics.known("nothing_at_all")
    # With discovery off its metrics are unknown, as before.
    monkeypatch.delenv(DISCOVERY_ENV)
    assert not metrics.known("bet_acceptance") and metrics.known("merge_acceptance")
