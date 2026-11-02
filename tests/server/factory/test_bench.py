"""The bench: role × vendor · model with n, quality, rounds and cost per unit; low n flagged."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.server.factory import snapshot
from omnigent.server.factory.api import bench as bench_api
from omnigent.server.factory.telemetry import bench, metrics
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.test_metrics_by_model import REPO, Seeded, seed


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    bench_api.reset_cache()
    snapshot.reset_cache()
    yield
    bench_api.reset_cache()
    snapshot.reset_cache()


@pytest.fixture()
def seeded(world: FactoryWorld) -> Seeded:
    return seed(world)


def _bench(seeded: Seeded, filters: metrics.Filters | None = None) -> dict[str, Any]:
    window = metrics.parse_window("30d")
    facts = metrics.load_facts(seeded.world.deps, seeded.fid, window)
    return bench.compute_bench(facts, window, filters)


def _row(body: dict[str, Any], role: str, vendor: str | None, model: str | None) -> dict[str, Any]:
    return next(
        r for r in body["rows"] if (r["role"], r["vendor"], r["model"]) == (role, vendor, model)
    )


def test_builders_are_compared_by_vendor_and_model(seeded: Seeded) -> None:
    body = _bench(seeded)

    sonnet = _row(body, "ic", "claude", "sonnet")
    codex = _row(body, "ic", "codex", "gpt-5-codex")
    assert sonnet["quality"] == {"label": "first-pass review", "value": 0.6667, "n": 3}
    assert codex["quality"] == {"label": "first-pass review", "value": 1.0, "n": 2}
    assert sonnet["rounds"] == {"value": 0.67, "n": 3}
    assert sonnet["cost"] == {"usd": 6.0, "unit": "merged PR", "units": 3, "per_unit": 2.0}
    assert codex["cost"]["per_unit"] == 2.0
    assert body["quality_ready"] is True and body["note"] is None


def test_low_n_rows_are_flagged(seeded: Seeded) -> None:
    body = _bench(seeded)

    assert body["low_n"] == bench.LOW_N
    assert all(r["low_n"] == (r["n"] < bench.LOW_N) for r in body["rows"])
    assert _row(body, "ic", "claude", "sonnet")["low_n"] is True


def test_reviewers_demo_and_leads_get_their_own_measure(seeded: Seeded) -> None:
    body = _bench(seeded)

    reviewer = _row(body, "reviewer", "codex", "gpt-5-codex")
    assert reviewer["quality"]["label"] == "no later change requests"
    assert reviewer["quality"]["n"] == 3
    assert reviewer["cost"]["unit"] == "reviewed PR" and reviewer["cost"]["units"] == 3
    lead = _row(body, "project_lead", "claude", "opus")
    assert lead["quality"] is None and lead["cost"]["unit"] == "active day"
    assert lead["cost"] == {"usd": 9.0, "unit": "active day", "units": 1, "per_unit": 9.0}
    roles = [r["role"] for r in body["rows"]]
    assert roles.index("project_lead") < roles.index("ic") < roles.index("reviewer")


def test_without_stamped_work_the_bench_shows_cost_by_model_only(world: FactoryWorld) -> None:
    store, fid, now = world.deps.factory_store, world.id("lead_a"), time.time()
    store.add_cost_daily(
        fid,
        project_slug="api-version",
        role="ic",
        agent_name="ic",
        model="claude-sonnet-4-5",
        day_utc=metrics._day(now),
        cost_usd=3.0,
    )
    window = metrics.parse_window("30d")

    body = bench.compute_bench(metrics.load_facts(world.deps, fid, window), window)

    assert body["quality_ready"] is False and body["note"] == bench.NOT_READY_NOTE
    [row] = body["rows"]
    assert (row["role"], row["vendor"], row["model"], row["quality"]) == (
        "ic",
        "claude",
        "sonnet",
        None,
    )
    assert row["cost"]["usd"] == 3.0 and row["low_n"] is True


def test_hidden_projects_leave_the_bench(seeded: Seeded) -> None:
    body = _bench(seeded, metrics.Filters(hidden_projects=frozenset({"api-version"})))

    assert body["rows"] == []


# ── Through the route ──────────────────────────────────────────


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in build_cockpit(world, db_uri, monkeypatch):
        yield value


async def test_the_bench_route(cockpit: Cockpit) -> None:
    store = cockpit.store
    store.upsert_project(cockpit.fid, "api-version", title="(sample) API", repo=REPO)
    store.add_cost_daily(
        cockpit.fid,
        project_slug="",
        role="lead",
        agent_name="Atlas",
        model="opus",
        day_utc=metrics._day(time.time()),
        cost_usd=2.0,
    )

    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/bench", headers={"X-Test-User": "carol"}
    )
    bad = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/bench?window=1y", headers={"X-Test-User": "carol"}
    )
    stranger = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/bench", headers={"X-Test-User": "bob"}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["window"]["label"] == "30d"
    assert [(r["role"], r["vendor"], r["model"]) for r in body["rows"]] == [
        ("lead", "claude", "opus")
    ]
    assert bad.status_code == 400
    assert stranger.status_code in (403, 404)
    assert F.ROLE_IC in bench.QUALITY_LABELS
