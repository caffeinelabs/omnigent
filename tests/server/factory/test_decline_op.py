"""A lead declines to plan an opportunity, with one line of reason (``op="decline"``).

The decline is recorded on the opportunity by the discovery ledger's hook and
logged as ``bet.declined``; without the ledger no opportunity is known.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from tests.server.factory.bets_helpers import FakeLedger, bet_cockpit
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld


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


async def _decline(cockpit: Cockpit, key: str = "lead_a", **body: Any) -> httpx.Response:
    payload = {
        "from_session_id": cockpit.world.id(key),
        "kind": "projects",
        "op": "decline",
        **body,
    }
    return await cockpit.client.post(
        "/v1/factory/agent/proposals", json=payload, headers={"X-Test-User": "alice"}
    )


async def test_a_decline_is_recorded_on_the_opportunity(
    cockpit: Cockpit, ledger: FakeLedger
) -> None:
    ledger.add("O-12")

    resp = await _decline(
        cockpit, opportunity="O-12", reason="  Solved by the export work\nalready (sample) "
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "declined": "O-12",
        "reason": "Solved by the export work already (sample)",
    }
    assert ledger.declined == [("O-12", "Solved by the export work already (sample)", "Atlas")]
    [event] = cockpit.events("bet.declined")
    assert event.data["opportunity"] == "O-12"
    assert event.data["reason"] == "Solved by the export work already (sample)"
    assert cockpit.store.list_proposals(cockpit.fid) == []


async def test_a_decline_needs_a_known_opportunity_and_a_reason(
    cockpit: Cockpit, ledger: FakeLedger
) -> None:
    ledger.add("O-12")

    no_reason = await _decline(cockpit, opportunity="O-12")
    long_reason = await _decline(cockpit, opportunity="O-12", reason="x" * 301)
    bad_ref = await _decline(cockpit, opportunity="12", reason="off strategy (sample)")
    unknown = await _decline(cockpit, opportunity="O-99", reason="off strategy (sample)")
    pl = await _decline(cockpit, key="pl", opportunity="O-12", reason="off strategy (sample)")
    sentinel = await _decline(
        cockpit, key="lead_b", opportunity="O-12", reason="off strategy (sample)"
    )

    assert (no_reason.status_code, no_reason.json()["field"]) == (400, "reason")
    assert (long_reason.status_code, long_reason.json()["field"]) == (400, "reason")
    assert (bad_ref.status_code, bad_ref.json()["field"]) == (400, "opportunity")
    assert (unknown.status_code, error_code(unknown)) == (404, "unknown_opportunity")
    assert (pl.status_code, error_code(pl)) == (403, "not_allowed")
    # Either top-level lead may decline; Atlas is the one that plans.
    assert sentinel.status_code == 200, sentinel.text
    assert len(ledger.declined) == 1


async def test_a_decline_reason_quotes_only_verified_words(
    cockpit: Cockpit, ledger: FakeLedger
) -> None:
    ledger.add("O-12")
    ledger.excerpts["S-4"] = "honestly we never share drafts with anyone (sample)"

    unverified = await _decline(
        cockpit, opportunity="O-12", reason='A user wrote "I never want this to go live" so skip'
    )
    verified = await _decline(
        cockpit, opportunity="O-12", reason='Users say "we never share drafts with anyone" (S-4)'
    )

    assert (unverified.status_code, error_code(unverified)) == (422, "quote_unverified")
    assert unverified.json()["field"] == "reason"
    [logged] = cockpit.events("bet.quote_refused")
    assert logged.data["field"] == "reason"
    assert verified.status_code == 200, verified.text
    assert len(ledger.declined) == 1


async def test_without_the_ledger_no_opportunity_is_known(cockpit: Cockpit) -> None:
    resp = await _decline(cockpit, opportunity="O-1", reason="off strategy (sample)")

    assert (resp.status_code, error_code(resp)) == (404, "unknown_opportunity")
    assert cockpit.events("bet.declined") == []
