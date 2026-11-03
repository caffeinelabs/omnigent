"""⌘K's search: ``GET /v1/factory/factories/{fid}/search?q=&limit=``.

Any reader of the factory (READ on its root; anything else is a 404) gets
the hits of ``q`` in what they may read, best first, built by
:mod:`omnigent.server.factory.search`: every id, title, member (with vendor,
model and state), source, definition field name, definition version and Room
thread. Discovery's opportunities, signals and sources answer only while
discovery runs for the factory. A blank ``q`` finds nothing (the palette
shows Needs you and its verbs instead).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from omnigent.server.factory.api.agents import fleet_context
from omnigent.server.factory.search import LIMIT_DEFAULT, LIMIT_MAX, Q_MAX, search_for_viewer

router = APIRouter(prefix="/v1/factory", tags=["factory"])


@router.get("/factories/{factory_id}/search")
async def search_factory(
    request: Request,
    factory_id: str,
    q: str = Query("", max_length=Q_MAX),
    limit: int = Query(LIMIT_DEFAULT, ge=1, le=LIMIT_MAX),
) -> dict[str, Any]:
    """The hits of ``q`` in what the caller may read: ``{q, discovery, results, total}``."""
    ctx = await fleet_context(request, factory_id)
    return await search_for_viewer(ctx, q, limit)


__all__ = ["router"]
