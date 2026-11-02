"""The bench: ``GET /v1/factory/factories/{fid}/bench?window=30d``.

Each role's track record by vendor and model (see
:mod:`omnigent.server.factory.telemetry.bench`), always with n. People call it
with their own credentials: READ on the root; projects whose PL the viewer
cannot read are left out of every number.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Final

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from omnigent.server.factory import control
from omnigent.server.factory.api.insights import _guard, _hidden, _window, _window_view
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.telemetry import bench, metrics
from omnigent.server.routes._auth_helpers import get_user_id

router = APIRouter(prefix="/v1/factory", tags=["factory"])

CACHE_S: Final = 300.0
_cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}


def _compute(
    deps: Any, factory_id: str, window: metrics.Window, filters: metrics.Filters
) -> dict[str, Any]:
    key = (factory_id, window.seconds, window.until // int(CACHE_S), filters)
    hit = _cache.get(key)
    if hit is not None and time.monotonic() - hit[0] < CACHE_S:
        return hit[1]
    facts = metrics.load_facts(deps, factory_id, window)
    body = bench.compute_bench(facts, window, filters)
    if len(_cache) > 256:
        _cache.clear()
    _cache[key] = (time.monotonic(), body)
    return body


def reset_cache() -> None:
    """Forget cached benches (tests)."""
    _cache.clear()


@router.get("/factories/{factory_id}/bench", response_model=None)
async def get_bench(
    request: Request,
    factory_id: str,
    window: str | None = Query(None, description="e.g. 7d or 30d (default 30d, at most 90d)."),
) -> dict[str, Any] | JSONResponse:
    """Track record by role × vendor · model with n, quality, rounds and cost per unit."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)
    label = window or bench.DEFAULT_WINDOW

    async def run() -> dict[str, Any]:
        root = await control.load_factory_root(deps, user_id, factory_id)
        win = _window(label)
        hidden = await _hidden(request.app.state, user_id, root)
        filters = metrics.Filters(hidden_projects=hidden)
        body = await asyncio.to_thread(_compute, deps, root.id, win, filters)
        return {"window": _window_view(win, label), **body}

    return await _guard(run)


__all__ = ["reset_cache", "router"]
