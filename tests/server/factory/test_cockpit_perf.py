"""
The cockpit's reads on a factory shaped like a live one stay cheap.

Seeds :data:`perf_world.SMALL` (``dev/benchmarks/factory_cockpit.py`` runs the
live size) and reads each cockpit page cold (every cache dropped) and warm.
SQL statements are the budget that does not flake: on Postgres each one is a
network round trip, and an N+1 read grows them with every member and project.
The time budgets are loose, for a slow CI box.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import event

from omnigent.db.utils import get_or_create_engine
from omnigent.server.factory import nodes, snapshot, status
from tests.server.factory import perf_world

PAGES: dict[str, str] = {
    "snapshot": "/v1/factory/factories/{fid}",
    "node_product": "/v1/factory/factories/{fid}/n/product",
    "needs_you": "/v1/factory/factories/{fid}/needs-you",
    "chat": "/v1/factory/factories/{fid}/chat?thread=factory",
    "status_summary": "/v1/factory/factories/{fid}/status-summary",
    "events": "/v1/factory/factories/{fid}/events",
    "factories": "/v1/factory/factories",
    "decisions": "/v1/factory/decisions",
    "preflight": "/v1/factory/preflight",
}

COLD_QUERIES: dict[str, int] = {
    "snapshot": 105,
    "node_product": 125,
    "needs_you": 115,
    "chat": 105,
    "status_summary": 105,
    "events": 100,
    "factories": 105,
    "decisions": 35,
    "preflight": 5,
}
"""SQL statements a cold read may take (after the fixes: about half of each)."""

WARM_P95_S = 0.3
COLD_MAX_S = 3.0


def _reset() -> None:
    snapshot.reset_cache()
    nodes.reset_cache()
    status.reset()


@pytest.fixture(autouse=True)
def _fresh_caches() -> Iterator[None]:
    _reset()
    yield
    _reset()


class _Counter:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self, _conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        if not statement.startswith("PRAGMA"):  # SQLite's per-connection setup
            self.count += 1


@pytest_asyncio.fixture()
async def live_like(db_uri: str, tmp_path: Path) -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    app = perf_world.build_app(db_uri, tmp_path)
    fid = perf_world.seed_live_factory(db_uri, app, perf_world.SMALL)
    transport = httpx.ASGITransport(app=app)
    headers = {"X-Forwarded-Email": perf_world.OWNER}
    async with httpx.AsyncClient(transport=transport, base_url="http://t", headers=headers) as c:
        yield c, fid


async def test_cockpit_reads_stay_within_their_query_and_time_budgets(
    live_like: tuple[httpx.AsyncClient, str], db_uri: str
) -> None:
    client, fid = live_like
    counter = _Counter()
    engine = get_or_create_engine(db_uri)
    event.listen(engine, "before_cursor_execute", counter)
    try:
        # Process-wide warm-ups (definition seed, agent bundles) are not a page's cost.
        for path in PAGES.values():
            assert (await client.get(path.format(fid=fid))).status_code == 200
        over: list[str] = []
        for name, path in PAGES.items():
            url = path.format(fid=fid)
            _reset()
            before, started = counter.count, time.perf_counter()
            resp = await client.get(url)
            cold_s, queries = time.perf_counter() - started, counter.count - before
            assert resp.status_code == 200, (name, resp.text[:300])
            warm = []
            for _ in range(8):
                started = time.perf_counter()
                assert (await client.get(url)).status_code == 200
                warm.append(time.perf_counter() - started)
            p95 = sorted(warm)[-1]
            if queries > COLD_QUERIES[name]:
                over.append(f"{name}: {queries} SQL statements cold (budget {COLD_QUERIES[name]})")
            if cold_s > COLD_MAX_S:
                over.append(f"{name}: {cold_s:.2f}s cold (budget {COLD_MAX_S}s)")
            if p95 > WARM_P95_S:
                over.append(f"{name}: {p95 * 1000:.0f} ms warm p95 (budget {WARM_P95_S}s)")
        assert not over, "\n".join(over)
    finally:
        event.remove(engine, "before_cursor_execute", counter)


async def test_an_unchanged_snapshot_answers_304(live_like: tuple[httpx.AsyncClient, str]) -> None:
    client, fid = live_like
    first = await client.get(f"/v1/factory/factories/{fid}")
    again = await client.get(
        f"/v1/factory/factories/{fid}", headers={"If-None-Match": first.headers["etag"]}
    )
    assert again.status_code == 304
