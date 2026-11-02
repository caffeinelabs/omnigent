"""The Overview's "Now": ``GET /v1/factory/factories/{fid}/status-summary``.

Any reader of the factory (READ on its root; anything else is a 404) gets the
plain-language status built by :mod:`omnigent.server.factory.status_summary`
from the cached snapshot, cut to what they may see.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from omnigent.server.factory.api.agents import fleet_context
from omnigent.server.factory.status_summary import summary_for_viewer

router = APIRouter(prefix="/v1/factory", tags=["factory"])


@router.get("/factories/{factory_id}/status-summary")
async def get_status_summary(request: Request, factory_id: str) -> dict[str, Any]:
    """What the factory is doing, what waits on people, spend, next and health."""
    ctx = await fleet_context(request, factory_id)
    return await summary_for_viewer(ctx.deps, ctx.root.id, ctx.raw, ctx.readable, ctx.user_id)


__all__ = ["router"]
