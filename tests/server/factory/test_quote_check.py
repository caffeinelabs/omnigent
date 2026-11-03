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

REAL = "I don't want to publish it until my cofounder has seen it, but there's no private link."


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
