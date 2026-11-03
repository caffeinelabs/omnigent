"""The prediction ledger: how well the factory forecasts what its bets do.

Each bet's latest readout scores it: inside or outside its predicted range,
won or not by the evidence level it was filed at, which sources were in its
evidence, and where it came from (discovered, requested, a hunch). Every rate
carries n, a level is a rate only from five readouts, and everything groups by
the definition version that ranked the bet. Every product word is sample data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.server.factory import events
from omnigent.server.factory.discovery import readouts
from omnigent.server.factory.telemetry import metrics
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.discovery_helpers import DAY, discovery_cockpit
from tests.server.factory.readout_helpers import (
    base,
    file_bet,
    later_signals,
    now,
    read_sources,
    release,
    seeded,
)

_T = now() - 90 * DAY


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in discovery_cockpit(world, db_uri, monkeypatch):
        yield value


def written(
    cockpit: Cockpit,
    proposal: int,
    day: int,
    verdict: str,
    *,
    predicted: str | None = "inside",
    level: str = "moderate",
    version: int = 3,
    sources: tuple[str, ...] = ("repo-issues",),
    origin: str = "discovered",
    status: str = "written",
) -> None:
    """A written readout of C-*proposal* with the stamps the ledger reads (sample)."""
    cockpit.store.add_readout(
        cockpit.fid,
        proposal=proposal,
        day=day,
        status=status,
        method="signal_trend",
        released_at=_T,
        due_at=_T + day * DAY,
        written_at=_T + day * DAY,
        verdict=verdict,
        recommendation="keep",
        result={"predicted_vs_actual": predicted},
        stamp={
            "evidence_level": level,
            "def_version": version,
            "sources": list(sources),
            "origin": origin,
        },
    )


def ledger(cockpit: Cockpit) -> dict[str, Any]:
    return readouts.prediction_ledger(cockpit.deps, cockpit.fid)


def test_each_bet_is_scored_once_by_its_latest_readout(cockpit: Cockpit) -> None:
    written(cockpit, 1, 7, "flat", predicted="above")
    written(cockpit, 1, 28, "won", predicted="inside")
    written(cockpit, 2, 7, "lost", predicted="below", level="weak")
    written(cockpit, 3, 7, "inconclusive", predicted=None)
    cockpit.store.add_readout(
        cockpit.fid,
        proposal=4,
        day=7,
        status="scheduled",
        method="signal_trend",
        released_at=_T,
        due_at=_T + 7 * DAY,
    )

    found = ledger(cockpit)

    assert found["readouts"] == 3
    assert found["inside"] == {"n": 1, "of": 2, "share": 0.5}
    # Inconclusive readouts are not wins or losses.
    assert found["win_rate"] == {"value": 0.5, "n": 2}
    assert found["win_rate_by_level"]["moderate"]["value"] == 1.0
    assert found["win_rate_by_level"]["weak"] == {"value": 0.0, "n": 1, "enough": False}


def test_a_level_is_a_rate_only_from_five_readouts(cockpit: Cockpit) -> None:
    for number, verdict in enumerate(["won", "won", "flat", "won", "lost"], start=1):
        written(cockpit, number, 7, verdict, level="strong")
    written(cockpit, 6, 7, "won", level="weak")

    found = ledger(cockpit)

    assert found["win_rate_by_level"]["strong"] == {"value": 0.6, "n": 5, "enough": True}
    assert found["win_rate_by_level"]["weak"]["enough"] is False
    assert found["note"] is None
    assert found["min_per_level"] == metrics.WIN_RATE_MIN_PER_LEVEL


def test_too_few_readouts_say_not_enough_data(cockpit: Cockpit) -> None:
    written(cockpit, 1, 7, "won")

    assert ledger(cockpit)["note"] == "not enough data: a rate needs 5 readouts per evidence level"


def test_yield_per_source_and_hit_rate_by_origin(cockpit: Cockpit) -> None:
    written(cockpit, 1, 7, "won", sources=("repo-issues", "web"))
    written(cockpit, 2, 7, "lost", sources=("web",))
    written(cockpit, 3, 7, "won", origin="hunch", sources=())
    written(cockpit, 4, 7, "flat", origin="requested", sources=())
    cockpit.store.upsert_project(
        cockpit.fid, "asked-for", title="Asked for (sample)", charter={"goal": "x"}, stage="merged"
    )

    found = ledger(cockpit)

    assert found["by_source"]["repo-issues"] == {"value": 1.0, "n": 1}
    assert found["by_source"]["web"] == {"value": 0.5, "n": 2}
    assert found["by_origin"] == {
        "discovered": {"value": 0.5, "n": 2},
        "hunch": {"value": 1.0, "n": 1},
        "requested": {"value": 0.0, "n": 1},
    }
    # Requested work without a plan ships and is never counted as a success.
    assert found["requested"] == {"shipped": 1, "measured": 0}


def test_everything_groups_by_the_definition_version_that_ranked_the_bet(
    cockpit: Cockpit,
) -> None:
    written(cockpit, 1, 7, "won", version=3)
    written(cockpit, 2, 7, "lost", version=3, predicted="below")
    written(cockpit, 3, 7, "won", version=4)

    found = ledger(cockpit)

    assert found["by_def_version"] == {
        "3": {"won": 1, "n": 2, "inside": 1},
        "4": {"won": 1, "n": 1, "inside": 1},
    }


async def test_a_filed_bet_stamps_its_evidence_level_and_rubric_version(
    cockpit: Cockpit,
) -> None:
    seeded(cockpit, before_days=10)
    later_signals(cockpit, 2, age_days=4)
    row, _ = file_bet(cockpit, on_behalf_of="alice")
    # The ledger reads the opportunity's score at filing (the listener snapshots it).
    await events.emit(
        cockpit.app.state,
        cockpit.fid,
        "bet.filed",
        subject=f"proposal:{row.ref}",
        summary="Atlas filed a bet (sample)",
        data={"opportunity": "O-1", "proposal_id": row.id},
    )
    snaps = cockpit.store.list_score_snapshots(cockpit.fid, opportunity=1, reason="bet")
    assert len(snaps) == 1

    [day7, _day28] = release(cockpit, now() - 8 * DAY)

    assert day7.stamp["evidence_level"] == "moderate"
    assert day7.stamp["def_version"] == snaps[0].def_version
    assert day7.stamp["origin"] == "requested"
    assert day7.stamp["sources"] == ["repo-issues", "web"]


async def test_the_discovery_summary_carries_readouts_and_the_ledger(cockpit: Cockpit) -> None:
    seeded(cockpit, before_days=10)
    later_signals(cockpit, 2, age_days=4)
    file_bet(cockpit)
    release(cockpit, now() - 8 * DAY)
    read_sources(cockpit, now())
    await readouts.write_due(cockpit.app.state, cockpit.fid, now=now())

    resp = await cockpit.client.get(base(cockpit, "/discovery"), headers={"X-Test-User": "alice"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["readouts"]["written"] == 1 and body["readouts"]["scheduled"] == 1
    assert body["readouts"]["won"] == 1 and body["readouts"]["release"] == "merged"
    assert body["readouts"]["latest"][0]["key"] == "C-1-d7"
    assert body["ledger"]["readouts"] == 1 and body["ledger"]["inside"]["of"] == 1
    assert body["chain"]["measured"] == 1
    opp = await cockpit.client.get(
        base(cockpit, "/opportunities/O-1"), headers={"X-Test-User": "alice"}
    )
    readout_box = next(b for b in opp.json()["why"] if b["kind"] == "readout")
    assert readout_box == {
        "kind": "readout",
        "ref": "C-1-d7",
        "label": "day 7 · won",
        "missing": False,
    }
    listed = await cockpit.client.get(base(cockpit, "/readouts"), headers={"X-Test-User": "alice"})
    assert listed.json()["pending"] == []
