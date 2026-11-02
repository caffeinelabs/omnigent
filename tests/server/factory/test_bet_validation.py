"""What the server checks when a lead files a bet on a projects proposal (``mechanism.md`` §5.4).

Each bet kind has its prerequisite, the plan must be complete and measurable,
quoted words must match a stored excerpt, and charter fields carry no quotes.
Opportunities come from the discovery ledger; without it none is known.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio

from omnigent.server.factory import bets
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.definition_blocks import DISCOVERY_ENV
from tests.server.factory.bets_helpers import (
    FLAGS,
    GUARDRAIL,
    FakeLedger,
    bet,
    bet_cockpit,
    cosign,
    file_bet,
    human_chat,
    set_strategy,
)
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import project, propose


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in bet_cockpit(world, db_uri, monkeypatch):
        yield value


@pytest.fixture()
def ledger() -> Iterator[FakeLedger]:
    fake = FakeLedger()
    fake.install()
    try:
        yield fake
    finally:
        fake.uninstall()


def _row(cockpit: Cockpit, number: int = 1):
    found = cockpit.store.get_proposal_by_number(cockpit.fid, number)
    assert found is not None
    return found


# ── Accepted bets ──────────────────────────────────────────────


async def test_an_enabler_on_a_goal_is_stored_with_the_strategy_added(cockpit: Cockpit) -> None:
    resp = await file_bet(cockpit)

    assert resp.status_code == 200, resp.text
    assert resp.json()["bet"] is True
    stored = _row(cockpit).bet
    assert stored is not None
    assert stored["kind"] == "enabler" and stored["goal"] == "G-1"
    # The strategy's guardrails join the bet's; the product's flag convention fills the flag.
    assert stored["guardrails"] == ["no new prompts for users (sample)", GUARDRAIL]
    assert stored["flag"] == {"key": "funnel-events", **FLAGS}
    assert stored["measurement"]["readout_days"] == [7, 28]
    assert cockpit.events("bet.filed")


async def test_a_hunch_bet_needs_an_owner(cockpit: Cockpit) -> None:
    resp = await file_bet(cockpit, bet=bet(kind="hunch", goal=None))

    assert resp.status_code == 200, resp.text
    row = _row(cockpit)
    assert row.approval == "owner"
    assert any(w.startswith("hunch_bet") for w in row.warnings)


async def test_a_metric_binding_allows_a_stronger_method(cockpit: Cockpit) -> None:
    bound = bet(
        metric="first_week_success", measurement={"method": "before_after", "readout_days": [28]}
    )
    unbound = bet(
        metric="made_up_metric", measurement={"method": "before_after", "readout_days": [28]}
    )

    ok = await file_bet(cockpit, bet=bound)
    refused = await file_bet(cockpit, {"op": "create", "project": project("other")}, bet=unbound)

    assert ok.status_code == 200, ok.text
    assert (refused.status_code, error_code(refused)) == (422, "metric_not_measurable")
    assert refused.json()["field"] == "bet.metric"


async def test_requested_work_from_an_opportunity_needs_no_bet(
    cockpit: Cockpit, ledger: FakeLedger
) -> None:
    ledger.add("O-4", status="idea")

    own = await file_bet(cockpit, opportunity="O-4", bet=None)
    asked = await file_bet(cockpit, opportunity="O-4", bet=None, reply_to=human_chat(cockpit))

    assert (own.status_code, error_code(own)) == (422, "bet_required")
    assert asked.status_code == 200, asked.text
    row = _row(cockpit)
    assert (row.opportunity, row.bet) == ("O-4", None)


# ── Prerequisites per kind ─────────────────────────────────────


async def test_outcome_and_test_bets_wait_for_the_discovery_ledger(cockpit: Cockpit) -> None:
    no_ref = await file_bet(cockpit, bet=bet(kind="outcome"))
    unknown = await file_bet(cockpit, bet=bet(kind="test"), opportunity="O-1")

    assert (no_ref.status_code, error_code(no_ref)) == (422, "bet_prerequisite")
    assert no_ref.json()["field"] == "opportunity"
    assert (unknown.status_code, error_code(unknown)) == (404, "unknown_opportunity")
    assert "discovery is off" in unknown.json()["message"]
    assert cockpit.store.list_proposals(cockpit.fid) == []


async def test_an_outcome_bet_needs_a_validated_opportunity(
    cockpit: Cockpit, ledger: FakeLedger
) -> None:
    ledger.add("O-2", status="idea")
    ledger.add("O-3", status="validated", goal="G-1")

    idea = await file_bet(cockpit, bet=bet(kind="outcome", goal=None), opportunity="O-2")
    validated = await file_bet(cockpit, bet=bet(kind="outcome", goal=None), opportunity="O-3")

    assert (idea.status_code, error_code(idea)) == (422, "bet_prerequisite")
    assert "file a test" in idea.json()["message"]
    assert validated.status_code == 200, validated.text
    row = _row(cockpit)
    assert row.opportunity == "O-3"
    # The opportunity's goal fills a bet that named none.
    assert row.bet is not None and row.bet["goal"] == "G-1"


async def test_a_test_may_target_an_idea_but_not_a_parked_opportunity(
    cockpit: Cockpit, ledger: FakeLedger
) -> None:
    ledger.add("O-5", status="idea")
    ledger.add("O-6", status="parked")

    idea = await file_bet(cockpit, bet=bet(kind="test"), opportunity="O-5")
    parked = await file_bet(
        cockpit,
        {"op": "create", "project": project("parked-test")},
        bet=bet(kind="test"),
        opportunity="O-6",
    )

    assert idea.status_code == 200, idea.text
    assert (parked.status_code, error_code(parked)) == (422, "bet_prerequisite")


async def test_an_enabler_attaches_to_a_goal_of_the_strategy(cockpit: Cockpit) -> None:
    no_goal = await file_bet(cockpit, bet=bet(goal=None))
    unknown = await file_bet(cockpit, bet=bet(goal="G-9"))

    assert (no_goal.status_code, error_code(no_goal)) == (422, "bet_prerequisite")
    assert no_goal.json()["field"] == "bet.goal"
    assert (unknown.status_code, error_code(unknown)) == (422, "unknown_goal")


# ── A complete plan ────────────────────────────────────────────


async def test_an_incomplete_plan_names_what_is_missing(cockpit: Cockpit) -> None:
    resp = await file_bet(
        cockpit,
        bet=bet(kill=None, measurement={"method": "signal_trend"}),
    )

    assert (resp.status_code, error_code(resp)) == (422, "bet_incomplete")
    assert resp.json()["missing"] == ["kill", "measurement.readout_days"]


@pytest.mark.parametrize(
    ("over", "where"),
    [
        ({"expected": {"direction": "up", "low": 5, "high": 1, "unit": "pp"}}, "bet.expected"),
        ({"flag": {"key": "a", "none": "no flags yet (sample)"}}, "bet.flag"),
        ({"measurement": {"method": "guess", "readout_days": [7]}}, "bet.measurement.method"),
        (
            {"measurement": {"method": "signal_trend", "readout_days": [400]}},
            "bet.measurement.readout_days",
        ),
        ({"outcome": "x" * 141}, "bet.outcome"),
        ({"kind": "moonshot"}, "bet.kind"),
        ({"goal": "goal-1"}, "bet.goal"),
        # low and high are the signed change: a rise ends above 0, a drop starts below it.
        ({"expected": {"direction": "up", "low": -4, "high": -1, "unit": "pp"}}, "bet.expected"),
        ({"expected": {"direction": "down", "low": 1, "high": 4, "unit": "pp"}}, "bet.expected"),
        # The flag's convention and provider are the owner's, never the lead's.
        ({"flag": {"key": "a", "convention": "mine (sample)"}}, "bet.flag.convention"),
        ({"flag": {"key": "a", "provider": "mine (sample)"}}, "bet.flag.provider"),
    ],
)
async def test_bad_bet_fields_name_their_path(cockpit: Cockpit, over: dict, where: str) -> None:
    resp = await file_bet(cockpit, bet=bet(**over))

    assert (resp.status_code, error_code(resp)) == (400, "invalid_body")
    assert resp.json()["field"] == where


async def test_a_drop_is_a_negative_range(cockpit: Cockpit) -> None:
    down = bet(expected={"direction": "down", "low": -4, "high": -1, "unit": "pp"})

    resp = await file_bet(cockpit, bet=down)

    assert resp.status_code == 200, resp.text
    stored = _row(cockpit).bet
    assert stored is not None
    assert bets.expected_text(stored["expected"]) == "down -4 to -1 pp"
    assert bets.expected_text(bet()["expected"]) == "up +1 to +4 pp"


async def test_a_bet_starts_a_project(cockpit: Cockpit) -> None:
    resp = await file_bet(
        cockpit, {"op": "update", "slug": "api-version", "patch": {"priority": 1}}
    )

    assert (resp.status_code, error_code(resp)) == (400, "invalid_body")
    assert resp.json()["field"] == "bet"


async def test_only_a_lead_files_a_bet(cockpit: Cockpit) -> None:
    resp = await file_bet(
        cockpit,
        {"op": "update", "slug": "api-version", "patch": {"priority": 1}},
        key="pl",
    )

    assert (resp.status_code, error_code(resp)) == (403, "not_allowed")


# ── Quotes ─────────────────────────────────────────────────────


async def test_an_unverified_quote_is_refused_and_logged(cockpit: Cockpit) -> None:
    quoted = 'New users say "I do not want it live before it works" (S-3).'

    resp = await file_bet(cockpit, bet=bet(hypothesis=quoted))

    assert (resp.status_code, error_code(resp)) == (422, "quote_unverified")
    assert resp.json()["field"] == "bet.hypothesis"
    assert resp.json()["quotes"] == ["I do not want it live before it works"]
    [logged] = cockpit.events("bet.quote_refused")
    assert logged.data["field"] == "bet.hypothesis"
    assert cockpit.store.list_proposals(cockpit.fid) == []


async def test_a_quote_matching_a_cited_excerpt_passes(
    cockpit: Cockpit, ledger: FakeLedger
) -> None:
    ledger.excerpts["S-3"] = "honestly I do not want it live before it works (sample)"
    quoted = 'New users say "I do not want it live before it works" (S-3).'
    uncited = 'New users say "I do not want it live before it works".'

    ok = await file_bet(cockpit, bet=bet(hypothesis=quoted))
    refused = await file_bet(
        cockpit, {"op": "create", "project": project("other")}, bet=bet(hypothesis=uncited)
    )

    assert ok.status_code == 200, ok.text
    assert (refused.status_code, error_code(refused)) == (422, "quote_unverified")


async def test_charter_fields_carry_no_user_quotes(cockpit: Cockpit) -> None:
    quoted_outcome = bet(outcome='Users can say "this is not ready to show yet" (sample)')
    quoted_goal = project("draft-share", goal='Fix "I lose my work every single time" (sample).')
    label = project("labelled", goal='Add a "Share draft" button (sample).')

    outcome = await file_bet(cockpit, bet=quoted_outcome)
    goal = await file_bet(cockpit, {"op": "create", "project": quoted_goal})
    short = await file_bet(cockpit, {"op": "create", "project": label})

    assert (outcome.status_code, error_code(outcome)) == (422, "quote_in_charter")
    assert outcome.json()["field"] == "bet.outcome"
    assert (goal.status_code, error_code(goal)) == (422, "quote_in_charter")
    assert goal.json()["field"] == "changes[0].project.goal"
    # A short quoted label is not someone's words.
    assert short.status_code == 200, short.text


@pytest.mark.parametrize(
    ("over", "where"),
    [
        ({"metric": 'signal_trend "users never find the export"'}, "bet.metric"),
        (
            {"kill": {"state": "no lift (sample)", "date": 'when "we stop losing drafts" holds'}},
            "bet.kill.date",
        ),
        (
            {"expected": {"direction": "up", "low": 1, "high": 2, "unit": '"a b c d"'}},
            "bet.expected.unit",
        ),
    ],
)
async def test_quotes_in_charter_only_bet_fields_are_refused(
    cockpit: Cockpit, ledger: FakeLedger, over: dict, where: str
) -> None:
    # Even words a signal holds stay out of these: nothing strips them later.
    ledger.excerpts["S-3"] = "users never find the export; we stop losing drafts; a b c d"

    resp = await file_bet(cockpit, bet=bet(**over))

    assert (resp.status_code, error_code(resp)) == (422, "quote_in_charter")
    assert resp.json()["field"] == where
    assert cockpit.store.list_proposals(cockpit.fid) == []


async def test_the_rationale_of_a_bet_quotes_only_verified_words(
    cockpit: Cockpit, ledger: FakeLedger
) -> None:
    ledger.excerpts["S-3"] = "honestly I lose my drafts every single day (sample)"
    unverified = await file_bet(
        cockpit, rationale='One user said "nobody here uses drafts at all".'
    )
    verified = await file_bet(
        cockpit, rationale='Users say "I lose my drafts every single day" (S-3).'
    )
    plain = await propose(
        cockpit,
        {"op": "create", "project": project("plain")},
        rationale='The owner said "ship the export before the trip" (sample).',
    )

    assert (unverified.status_code, error_code(unverified)) == (422, "quote_unverified")
    assert unverified.json()["field"] == "rationale"
    assert cockpit.events("bet.quote_refused")
    assert verified.status_code == 200, verified.text
    # Work without a bet or an opportunity keeps today's freedom.
    assert plain.status_code == 200, plain.text


# ── Dark while discovery is off ────────────────────────────────


@pytest.mark.parametrize("off", ["env", "flag"])
async def test_bets_stay_dark_while_discovery_is_off(
    cockpit: Cockpit, ledger: FakeLedger, monkeypatch: pytest.MonkeyPatch, off: str
) -> None:
    ledger.add("O-4", status="idea")
    if off == "env":
        monkeypatch.delenv(DISCOVERY_ENV)
    else:
        set_strategy(cockpit, discovery=False)

    filed = await file_bet(cockpit)
    requested = await file_bet(cockpit, opportunity="O-4", bet=None, reply_to=human_chat(cockpit))
    co = await cosign(cockpit)
    back = await cosign(cockpit, op="send_back")
    declined = await cockpit.client.post(
        "/v1/factory/agent/proposals",
        json={
            "from_session_id": cockpit.world.id("lead_a"),
            "kind": "projects",
            "op": "decline",
            "opportunity": "O-4",
            "reason": "off strategy (sample)",
        },
        headers={"X-Test-User": "alice"},
    )
    plain = await propose(cockpit, {"op": "create", "project": project("plain")})

    for resp in (filed, requested, co, back, declined):
        assert (resp.status_code, error_code(resp)) == (404, "discovery_off"), resp.text
    assert cockpit.events("bet.filed") == [] and cockpit.events("bet.declined") == []
    assert cockpit.mailer.texts_to(cockpit.world.id("lead_b")) == []
    # A plain create is unchanged: no bet, no co-sign, no wait.
    assert plain.status_code == 200, plain.text
    row = _row(cockpit)
    assert row.status != "awaiting_cosign" and row.bet is None


async def test_work_without_a_bet_keeps_its_quotes_out_of_the_outcome(
    cockpit: Cockpit,
) -> None:
    quoted = project("plain", outcome='People "never find the export button anywhere" (sample)')
    plain = project("plain-goal", goal='Fix "I lose my work every single time" (sample).')

    outcome = await propose(cockpit, {"op": "create", "project": quoted})
    goal = await propose(cockpit, {"op": "create", "project": plain})

    assert (outcome.status_code, error_code(outcome)) == (422, "quote_in_charter")
    # Ordinary work keeps today's freedom in its other fields.
    assert goal.status_code == 200, goal.text


# ── Unit checks ────────────────────────────────────────────────


def test_find_and_strip_quotes() -> None:
    text = 'They said “losing drafts” and «it breaks every time I share», see "Share".'

    assert bets.find_quotes(text) == ["it breaks every time I share"]
    assert bets.find_quotes("“we lose our drafts daily”") == ["we lose our drafts daily"]
    assert bets.strip_quotes(text) == (
        f'They said “losing drafts” and {bets.OMITTED}, see "Share".'
    )


def test_case_against_lines() -> None:
    assert bets.parse_case_against("- one (sample)\n\n* two (sample)") == [
        "one (sample)",
        "two (sample)",
    ]
    with pytest.raises(CockpitError):
        bets.parse_case_against(["a", "b", "c", "d"])
    with pytest.raises(CockpitError):
        bets.parse_case_against(None)
    with pytest.raises(CockpitError):
        bets.parse_case_against(["x" * 301])
