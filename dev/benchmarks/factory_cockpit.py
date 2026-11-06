"""
The factory cockpit's read latency on a factory the size of a live one.

Seeds a SQLite factory (``tests/server/factory/perf_world.py``: 20 projects,
~50 members, 5k events, 600 decisions and proposals, 30 demos, 2k chat
messages), serves the factory routes with uvicorn on a thread, and measures:

- **cold**: each endpoint right after every cache is dropped;
- **warm**: each endpoint read again and again;
- **load**: two open cockpit tabs polling as the web does while agents write
  events (~2/s, each one a write the cockpit must see), with a ``/ping`` probe
  that shows how long any other request waits for the event loop.

Run::

    OMNIGENT_SKIP_WEB_UI=true uv run python -m dev.benchmarks.factory_cockpit
    uv run python -m dev.benchmarks.factory_cockpit --profile snapshot   # cProfile one path
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx

ENDPOINTS: dict[str, str] = {
    "snapshot": "/v1/factory/factories/{fid}",
    "node_product": "/v1/factory/factories/{fid}/n/product",
    "node_factory": "/v1/factory/factories/{fid}/n/factory",
    "needs_you": "/v1/factory/factories/{fid}/needs-you",
    "chat": "/v1/factory/factories/{fid}/chat?thread=factory",
    "status_summary": "/v1/factory/factories/{fid}/status-summary",
    "preflight": "/v1/factory/preflight",
    "levels": "/v1/factory/factories/{fid}/levels",
    "decisions": "/v1/factory/decisions",
    "events": "/v1/factory/factories/{fid}/events",
    "factories": "/v1/factory/factories",
    "definition": "/v1/factory/factories/{fid}/definition",
    "people": "/v1/factory/factories/{fid}/people",
}

POLLERS: dict[str, float] = {
    "snapshot": 5.0,
    "node_product": 5.0,
    "decisions": 10.0,
    "events": 5.0,
    "factories": 15.0,
    "definition": 30.0,
    "people": 30.0,
    "needs_you": 10.0,
    "chat": 5.0,
}
"""Endpoint -> seconds between a tab's reads (what the cockpit polls, before this change)."""


class Queries:
    """Counts SQL statements (and adds a round trip's latency to each, like Postgres)."""

    count = 0
    latency_s = 0.0
    _lock = threading.Lock()

    @classmethod
    def install(cls, db_uri: str, latency_ms: float) -> None:
        from sqlalchemy import event

        from omnigent.db.utils import get_or_create_engine

        cls.latency_s = latency_ms / 1000

        def before(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
            if statement.startswith("PRAGMA"):
                return  # SQLite's per-connection setup: no round trip on Postgres
            with cls._lock:
                cls.count += 1
            if cls.latency_s:
                time.sleep(cls.latency_s)

        event.listen(get_or_create_engine(db_uri), "before_cursor_execute", before)


def reset_caches() -> None:
    """Drop every cockpit read cache in this process."""
    from omnigent.server.factory import nodes, snapshot, status

    snapshot.reset_cache()
    nodes.reset_cache()
    status.reset()
    reset = getattr(nodes, "reset_names", None)
    if reset is not None:
        reset()


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = min(len(ordered) - 1, max(0, round(p / 100 * (len(ordered) - 1))))
    return ordered[k]


def summary(values: list[float]) -> dict[str, float]:
    return {
        "n": len(values),
        "p50": round(pct(values, 50) * 1000, 1),
        "p95": round(pct(values, 95) * 1000, 1),
        "max": round(max(values, default=0) * 1000, 1),
        "mean": round(statistics.fmean(values) * 1000, 1) if values else 0.0,
    }


class Served:
    """The app on a uvicorn thread."""

    def __init__(self, app: Any) -> None:
        import socket

        import uvicorn

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 20
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("uvicorn did not start")
            time.sleep(0.05)
        self.base_url = f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def build(scale_name: str, tmp: Path) -> tuple[Any, str, str]:
    """:returns: ``(app, factory id, db uri)`` of a freshly seeded factory."""
    from tests.server.factory import perf_world

    db_uri = f"sqlite:///{tmp / 'bench.db'}"
    app = perf_world.build_app(db_uri, tmp)
    scale = perf_world.SMALL if scale_name == "small" else perf_world.Scale()
    started = time.monotonic()
    fid = perf_world.seed_live_factory(db_uri, app, scale)
    print(f"seeded {scale_name} factory {fid} in {time.monotonic() - started:.1f}s")
    _add_bench_routes(app, fid)
    return app, fid, db_uri


def _add_bench_routes(app: Any, fid: str) -> None:
    from omnigent.server.factory import events

    @app.post("/bench/reset")
    async def bench_reset() -> dict[str, bool]:
        reset_caches()
        return {"ok": True}

    @app.post("/bench/event")
    async def bench_event() -> dict[str, bool]:
        # What an agent's report does: one recorded event the cockpit must see.
        await events.emit(
            app.state, fid, "task.state", project="assistant-core", summary="bench write"
        )
        return {"ok": True}


async def _get(client: httpx.AsyncClient, path: str, headers: dict[str, str]) -> float:
    started = time.perf_counter()
    resp = await client.get(path, headers=headers)
    took = time.perf_counter() - started
    if resp.status_code not in (200, 304):
        raise RuntimeError(f"GET {path}: {resp.status_code} {resp.text[:300]}")
    return took


async def measure(base_url: str, fid: str, *, warm_n: int, load_s: float) -> dict[str, Any]:
    from tests.server.factory.perf_world import OWNER

    headers = {"X-Forwarded-Email": OWNER}
    out: dict[str, Any] = {"cold": {}, "warm": {}, "load": {}, "sizes": {}}
    limits = httpx.Limits(max_connections=64)
    async with httpx.AsyncClient(base_url=base_url, timeout=120, limits=limits) as client:
        for name, template in ENDPOINTS.items():
            path = template.format(fid=fid)
            await client.post("/bench/reset")
            before = Queries.count
            out["cold"][name] = round(await _get(client, path, headers) * 1000, 1)
            out.setdefault("queries", {})[name] = Queries.count - before
            resp = await client.get(path, headers=headers)
            out["sizes"][name] = len(resp.content)
            times = [await _get(client, path, headers) for _ in range(warm_n)]
            out["warm"][name] = summary(times)
            print(
                f"  {name:16s} cold {out['cold'][name]:8.1f} ms "
                f"({out['queries'][name]:5d} q, {out['sizes'][name] // 1024:4d} KB)"
                f"   warm {out['warm'][name]}"
            )
        if load_s > 0:
            out["load"] = await _load(client, fid, headers, load_s)
    return out


async def _load(
    client: httpx.AsyncClient, fid: str, headers: dict[str, str], seconds: float
) -> dict[str, Any]:
    """Two tabs polling, agents writing ~2 events/s, and a /ping probe at 10/s."""
    stop = time.monotonic() + seconds
    times: dict[str, list[float]] = {name: [] for name in POLLERS}
    pings: list[float] = []
    writes: list[float] = []
    etags: dict[tuple[int, str], str] = {}

    async def poller(tab: int, name: str, every: float) -> None:
        path = ENDPOINTS[name].format(fid=fid)
        await asyncio.sleep(every * tab / 2)
        while time.monotonic() < stop:
            extra = dict(headers)
            if (tab, name) in etags:
                extra["If-None-Match"] = etags[(tab, name)]
            started = time.perf_counter()
            resp = await client.get(path, headers=extra)
            times[name].append(time.perf_counter() - started)
            if tag := resp.headers.get("etag"):
                etags[(tab, name)] = tag
            await asyncio.sleep(every)

    async def writer() -> None:
        while time.monotonic() < stop:
            started = time.perf_counter()
            await client.post("/bench/event")
            writes.append(time.perf_counter() - started)
            await asyncio.sleep(0.5)

    async def pinger() -> None:
        while time.monotonic() < stop:
            started = time.perf_counter()
            await client.get("/ping")
            pings.append(time.perf_counter() - started)
            await asyncio.sleep(0.1)

    tasks = [poller(tab, name, every) for tab in (0, 1) for name, every in POLLERS.items()]
    await asyncio.gather(*tasks, writer(), pinger())
    result = {name: summary(values) for name, values in times.items()}
    result["agent_write"] = summary(writes)
    result["ping"] = summary(pings)
    for name, row in result.items():
        print(f"  load {name:14s} {row}")
    return result


def profile(app: Any, fid: str, which: str, out: Path) -> None:
    """cProfile one read path in process (cold caches) and print the top of it."""
    import cProfile
    import pstats

    from fastapi.testclient import TestClient

    from tests.server.factory.perf_world import OWNER

    path = ENDPOINTS[which].format(fid=fid)
    client = TestClient(app)
    client.get(path, headers={"X-Forwarded-Email": OWNER})
    reset_caches()
    prof = cProfile.Profile()
    prof.enable()
    resp = client.get(path, headers={"X-Forwarded-Email": OWNER})
    prof.disable()
    print(f"{which}: {resp.status_code}, {len(resp.content)} bytes")
    prof.dump_stats(str(out))
    stats = pstats.Stats(prof)
    stats.sort_stats("cumulative").print_stats(60)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--scale", choices=["live", "small"], default="live")
    parser.add_argument("--warm", type=int, default=15, help="warm reads per endpoint")
    parser.add_argument("--load", type=float, default=30.0, help="seconds of mixed load")
    parser.add_argument("--profile", choices=list(ENDPOINTS), help="cProfile one endpoint")
    parser.add_argument("--json", type=Path, help="write the numbers here")
    parser.add_argument(
        "--db-latency-ms", type=float, default=1.0, help="added to each SQL statement"
    )
    args = parser.parse_args(argv)
    import logging

    logging.basicConfig(level=logging.ERROR)
    with tempfile.TemporaryDirectory(prefix="factory-bench-") as tmp:
        app, fid, db_uri = build(args.scale, Path(tmp))
        Queries.install(db_uri, args.db_latency_ms)
        if args.profile:
            profile(app, fid, args.profile, Path(tmp) / "out.prof")
            return
        served = Served(app)
        try:
            numbers = asyncio.run(
                measure(served.base_url, fid, warm_n=args.warm, load_s=args.load)
            )
        finally:
            served.stop()
    if args.json:
        args.json.write_text(json.dumps(numbers, indent=2))


if __name__ == "__main__":
    main()
