"""The ledger's upkeep: retention drops expired text, and each factory gets a daily score snapshot.

Score snapshots are also taken at decision points (a kill, a park, a plan),
so a later backtest can re-score the past without later data. Every text is
sample data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from omnigent.server.factory import definition
from omnigent.server.factory.background import discovery_ledger
from omnigent.server.factory.definition_blocks import DISCOVERY_ENV
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.discovery_helpers import (
    base,
    discovery_cockpit,
    ingest,
    make_opportunity,
)


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in discovery_cockpit(world, db_uri, monkeypatch):
        yield value


def test_a_pass_expires_text_and_snapshots_each_factory_once_a_day(cockpit: Cockpit) -> None:
    signals = ingest(cockpit, "web", ["first (sample)", "second (sample)"])
    make_opportunity(cockpit, signals)
    later = signals[0].expires_at + 10

    first = discovery_ledger.run_once(cockpit.deps, now=later)
    again = discovery_ledger.run_once(cockpit.deps, now=later + 60)

    assert first == {"expired": 2, "snapshots": 1}
    assert again == {"expired": 0, "snapshots": 0}
    [snap] = cockpit.store.list_score_snapshots(cockpit.fid)
    assert (snap.opportunity, snap.reason) == (1, "daily")
    assert snap.inputs["status"] in ("idea", "validated")


def test_many_decision_snapshots_never_hide_the_days_daily_one(cockpit: Cockpit) -> None:
    make_opportunity(cockpit, ingest(cockpit, "web", ["words (sample)"]))
    start = 1_800_000_000 - 1_800_000_000 % 86400
    assert discovery_ledger.run_once(cockpit.deps, now=start + 60)["snapshots"] == 1
    cockpit.store.add_score_snapshots(
        cockpit.fid,
        [
            {
                "opportunity": 1,
                "at": start + 120 + i,
                "reason": "parked",
                "rank": 0.1,
                "band": "next",
                "level": "weak",
            }
            for i in range(600)
        ],
    )

    later = discovery_ledger.run_once(cockpit.deps, now=start + 3600)

    assert later["snapshots"] == 0
    assert len(cockpit.store.list_score_snapshots(cockpit.fid, reason="daily")) == 1


def test_a_factory_without_discovery_gets_no_snapshot(cockpit: Cockpit) -> None:
    make_opportunity(cockpit, ingest(cockpit, "web", ["words (sample)"]))
    definition.set_values(
        cockpit.deps, cockpit.fid, {"discovery.enabled": False}, created_by="alice"
    )
    definition.invalidate(cockpit.fid)

    assert discovery_ledger.run_once(cockpit.deps)["snapshots"] == 0


async def test_the_service_stays_off_without_the_server_env(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(DISCOVERY_ENV)

    stop = await discovery_ledger.start(cockpit.app.state)

    await stop()  # a no-op: nothing was started


async def test_decision_points_snapshot_the_score(cockpit: Cockpit) -> None:
    make_opportunity(cockpit, ingest(cockpit, "web", ["words (sample)"]))

    resp = await cockpit.patch(
        base(cockpit, "/opportunities/O-1"), {"op": "kill", "reason": "harmful"}
    )

    assert resp.status_code == 200, resp.text
    [snap] = cockpit.store.list_score_snapshots(cockpit.fid)
    assert (snap.reason, snap.band, snap.inputs["status"]) == ("killed", "dropped", "dropped")
