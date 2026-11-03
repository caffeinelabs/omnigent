"""Quotes are real (Tier 0): quoted words in agent text must match a cited signal's excerpt.

A planted fake quote is refused and logged, in an opportunity an agent
writes and in a bet a lead files; a real one passes. Every text is sample data.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from omnigent.server.factory import bets
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.discovery import quotes
from tests.server.factory.bets_helpers import bet, file_bet
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.discovery_helpers import discovery_cockpit, ingest, make_opportunity

REAL = (
    "I don't want to publish it until my cofounder has seen it, but there's no private link. "
    "(sample)"
)


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in discovery_cockpit(world, db_uri, monkeypatch):
        yield value


@pytest.mark.parametrize(
    ("quote", "ok"),
    [
        ("I don't want to publish it until my cofounder has seen it", True),
        ("I DON’T want to publish it   until my cofounder has seen it.", True),
        ("I don't want to publish it … there's no private link", True),
        ("there's no private link … I don't want to publish it", False),
        ("I want to publish it right away", False),
    ],
)
def test_matching_normalises_case_marks_and_ellipses(quote: str, ok: bool) -> None:
    assert quotes.matches(quote, REAL) is ok


@pytest.mark.parametrize(
    ("quote", "excerpt"),
    [
        # A prefix or a negation turns the words around: whole words only.
        (
            "happy with how the export flow works",
            "I am unhappy with how the export flow works today (sample)",
        ),
        ("ever use it again", "I would never use it again (sample)"),
        ("like the new editor a lot", "I dislike the new editor a lot (sample)"),
        ("love this product a lot", "I do not love this product a lot (sample)"),
        ("love this product a lot", "I don't love this product a lot (sample)"),
        # Spliced fragments: parts of three words or more, at most two ellipses.
        (
            "I … love … this … product",
            "I do not love the way this team handled the product launch (sample)",
        ),
        (
            "the whole team … handled the product launch",
            "the whole team never handled the product launch well (sample)",
        ),
        (
            "we really do … love this product",
            "we really do not love this product (sample)",
        ),
        (
            "export works fine … quick and fast … start on my phone … almost every day",
            "export works fine and is quick and fast to start on my phone almost every day "
            "(sample)",
        ),
    ],
)
def test_an_edited_quote_that_changes_the_meaning_is_refused(quote: str, excerpt: str) -> None:
    assert quotes.matches(quote, excerpt) is False


def test_whole_words_and_a_short_lead_in_still_match() -> None:
    excerpt = "Honestly I really love this product a lot, it saves me hours (sample)"

    assert quotes.matches("love this product a lot", excerpt) is True
    assert quotes.matches("… I really love this product … it saves me hours", excerpt) is True


def test_a_real_quote_citing_its_signal_passes(cockpit: Cockpit) -> None:
    signal = ingest(cockpit, "web", [REAL])[0]

    row = make_opportunity(
        cockpit,
        [signal],
        problem=(
            f'People say "I don\'t want to publish it until my cofounder has seen it" '
            f"({signal.ref})"
        ),
    )

    assert row.ref == "O-1"


def test_a_planted_fake_quote_is_refused_and_logged(
    cockpit: Cockpit, caplog: pytest.LogCaptureFixture
) -> None:
    signal = ingest(cockpit, "web", [REAL])[0]

    with caplog.at_level(logging.WARNING), pytest.raises(CockpitError) as exc:
        make_opportunity(
            cockpit,
            [signal],
            problem=f'One user wrote "this is the worst product I have ever used" ({signal.ref})',
        )

    assert exc.value.code == "quote_unverified" and exc.value.field == "problem"
    assert exc.value.extra["quotes"] == ["this is the worst product I have ever used"]
    assert any("unverified quote" in r.getMessage() for r in caplog.records)
    assert cockpit.store.list_opportunities(cockpit.fid) == []


@pytest.mark.parametrize(
    "problem",
    [
        "One user wrote 'this is the worst product I have ever used' ({ref})",
        "One user wrote ‘this is the worst product I have ever used’ ({ref})",
    ],
)
def test_a_fake_quote_in_single_quotes_is_refused(cockpit: Cockpit, problem: str) -> None:
    signal = ingest(cockpit, "web", [REAL])[0]

    with pytest.raises(CockpitError) as exc:
        make_opportunity(cockpit, [signal], problem=problem.format(ref=signal.ref))

    assert exc.value.code == "quote_unverified"
    assert exc.value.extra["quotes"] == ["this is the worst product I have ever used"]


def test_single_quotes_keep_contractions_and_skip_possessives() -> None:
    assert bets.find_quotes("they said 'I don't want to publish it yet' (sample)") == [
        "I don't want to publish it yet"
    ]
    assert bets.find_quotes("they said ‘I don’t want to publish it yet’ (sample)") == [
        "I don’t want to publish it yet"
    ]
    assert bets.find_quotes("the users' drafts and the admins' views stay (sample)") == []
    assert bets.find_quotes("we don't think it's the team's fault (sample)") == []


def test_a_quote_anywhere_in_the_bet_note_is_checked(cockpit: Cockpit) -> None:
    signal = ingest(cockpit, "web", [REAL])[0]

    with pytest.raises(CockpitError) as exc:
        make_opportunity(
            cockpit,
            [signal],
            bet_note={
                "solutions": [
                    "A private link (sample)",
                    'Because a user said "this is the worst product I have ever used"',
                ],
                "cheapest_test": "A fake door (sample)",
            },
        )

    assert exc.value.code == "quote_unverified"
    assert exc.value.field == "bet_note.solutions[1]"


def test_a_bet_note_takes_only_its_own_fields(cockpit: Cockpit) -> None:
    signal = ingest(cockpit, "web", [REAL])[0]
    nested = {"test": {"why": 'a user said "this is the worst product I have ever used"'}}

    with pytest.raises(CockpitError) as exc:
        make_opportunity(cockpit, [signal], bet_note=nested)

    assert exc.value.code == "invalid_body"
    row = make_opportunity(
        cockpit,
        [signal],
        bet_note={
            "solutions": ["A private link (sample)", "A password (sample)"],
            "riskiest_assumption": "People share drafts at all (sample)",
            "cheapest_test": "A fake door (sample)",
            "metric": "drafts shared (sample)",
        },
    )
    assert row.bet_note is not None and row.bet_note["cheapest_test"] == "A fake door (sample)"


def test_a_quote_without_a_cited_signal_or_of_a_withdrawn_one_is_refused(cockpit: Cockpit) -> None:
    signal = ingest(cockpit, "web", [REAL])[0]
    quote = "I don't want to publish it until my cofounder has seen it"

    assert quotes.unmatched(cockpit.deps, cockpit.fid, [quote], []) == [quote]
    assert quotes.unmatched(cockpit.deps, cockpit.fid, [quote], [signal.ref]) == []
    cockpit.store.clear_signal_text(cockpit.fid, signal.number, reason="expired")
    assert quotes.unmatched(cockpit.deps, cockpit.fid, [quote], [signal.ref]) == [quote]


async def test_a_bets_quotes_are_checked_against_the_ledger(cockpit: Cockpit) -> None:
    signal = ingest(cockpit, "web", [REAL])[0]
    fake = bet(
        hypothesis=f'New users stall because "nobody ever reads the docs here" ({signal.ref}).'
    )
    real = bet(
        hypothesis=(
            f"New users hold back because \"I don't want to publish it until my cofounder "
            f'has seen it" ({signal.ref}).'
        )
    )

    refused = await file_bet(cockpit, bet=fake)
    filed = await file_bet(cockpit, bet=real)

    assert (refused.status_code, error_code(refused)) == (422, "quote_unverified")
    assert refused.json()["quotes"] == ["nobody ever reads the docs here"]
    assert cockpit.events("bet.quote_refused")
    assert filed.status_code in (200, 201), filed.text


def test_the_bets_hook_is_the_ledger_once_installed(cockpit: Cockpit) -> None:
    signal = ingest(cockpit, "web", [REAL])[0]

    bets.verify_quotes(
        cockpit.deps,
        cockpit.fid,
        [
            (
                "bet.hypothesis",
                f'"I don\'t want to publish it until my cofounder has seen it" {signal.ref}',
            )
        ],
    )
    with pytest.raises(CockpitError):
        bets.verify_quotes(
            cockpit.deps,
            cockpit.fid,
            [("bet.hypothesis", f'"made up words of a user" {signal.ref}')],
        )
