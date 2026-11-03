"""The one "Needs you" list: ``GET /v1/factory/factories/{fid}/asks``.

Any reader of the factory (READ on its root; anything else is a 404) gets
the rows that wait on them, one per subject and costliest first, built by
:mod:`omnigent.server.factory.asks` from the cached snapshot, cut to what
they may see. The new cockpit's Home, its top bar and the Slack digest of
someone on the new cockpit read the same list.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from omnigent.server.factory.api.agents import fleet_context
from omnigent.server.factory.asks import asks_for_viewer

router = APIRouter(prefix="/v1/factory", tags=["factory"])


@router.get("/factories/{factory_id}/asks")
async def get_asks(request: Request, factory_id: str) -> dict[str, Any]:
    """What needs the caller, one row per subject, with what the factory handled itself."""
    ctx = await fleet_context(request, factory_id)
    return await asks_for_viewer(ctx.deps, ctx.root.id, ctx.raw, ctx.readable, ctx.user_id)


__all__ = ["router"]
