"""``POST …/definition/preview`` returns the re-rank a strategy or rubric change causes.

The preview scores every open opportunity under the version in force and
under the change, and lists the ones whose band, position or rank moves,
in plain words ("O-2 … moves from Next to Now"). Nothing is stored. Every
text is sample data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from omnigent.server.factory import definition
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.definition_helpers import url, version
from tests.server.factory.discovery_helpers import (
    discovery_cockpit,
    ingest,
    label,
    make_opportunity,
)


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in discovery_cockpit(world, db_uri, monkeypatch):
        yield value


def seed(cockpit: Cockpit) -> None:
    """O-1 for new users (focus), O-2 for teams, both with moderate evidence (sample)."""
    new = ingest(cockpit, "repo-issues", [f"new user words {i} (sample)" for i in range(3)])
    new += ingest(cockpit, "web", [f"new web words {i} (sample)" for i in range(3)])
    label(cockpit, new, "blocker")
    make_opportunity(cockpit, new)
    teams = ingest(
        cockpit,
        "repo-issues",
        [f"team words {i} (sample)" for i in range(3)],
        accounts=[f"team-{i}" for i in range(3)],
        segments=("teams",),
    )
    teams += ingest(
        cockpit,
        "web",
        [f"team web {i} (sample)" for i in range(3)],
        accounts=[f"team-web-{i}" for i in range(3)],
        segments=("teams",),
    )
    label(cockpit, teams, "blocker")
    make_opportunity(cockpit, teams, title="Review a draft together (sample)", segments=["teams"])


async def preview(cockpit: Cockpit, *changes: dict[str, Any]) -> httpx.Response:
    return await cockpit.client.post(
        url(cockpit, "/preview"), json={"changes": list(changes)}, headers={"X-Test-User": "alice"}
    )


async def test_a_focus_change_shows_the_band_moves_and_stores_nothing(cockpit: Cockpit) -> None:
    seed(cockpit)
    before = version(cockpit)

    resp = await preview(
        cockpit, {"op": "replace", "path": "strategy.focus_segments", "value": ["teams"]}
    )

    assert resp.status_code == 200, resp.text
    moves = {m["opportunity"]: m for m in resp.json()["rerank"]}
    assert set(moves) == {"O-1", "O-2"}
    assert moves["O-2"]["after"]["position"] == 1 and moves["O-2"]["before"]["position"] == 2
    assert moves["O-2"]["after"]["rank"] > moves["O-2"]["before"]["rank"]
    assert moves["O-1"]["after"]["rank"] < moves["O-1"]["before"]["rank"]
    assert moves["O-2"]["line"].startswith("O-2 Review a draft together (sample)")
    assert version(cockpit) == before and cockpit.store.list_score_snapshots(cockpit.fid) == []


async def test_a_rubric_change_moves_bands(cockpit: Cockpit) -> None:
    seed(cockpit)

    resp = await preview(
        cockpit, {"op": "replace", "path": "discovery.rubric.bands.now", "value": 0.15}
    )

    moves = resp.json()["rerank"]
    assert [m["opportunity"] for m in moves] == ["O-1"]
    assert (moves[0]["before"]["band"], moves[0]["after"]["band"]) == ("next", "now")
    assert moves[0]["line"].endswith("moves from Next to Now")


async def test_changes_that_move_no_rank_and_a_dark_factory_rerank_nothing(
    cockpit: Cockpit,
) -> None:
    seed(cockpit)

    cadence = await preview(
        cockpit, {"op": "replace", "path": "cadence.demo_project_lead", "value": 1200}
    )
    assert cadence.json()["rerank"] == []

    definition.set_values(
        cockpit.deps, cockpit.fid, {"discovery.enabled": False}, created_by="alice"
    )
    definition.invalidate(cockpit.fid)
    dark = await preview(
        cockpit, {"op": "replace", "path": "strategy.focus_segments", "value": ["teams"]}
    )
    assert dark.status_code == 200 and dark.json()["rerank"] == []
