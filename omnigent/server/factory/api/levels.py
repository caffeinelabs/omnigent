"""
The cockpit's levels: ``GET /v1/factory/factories/{fid}/levels`` (anyone who reads the factory).

Both trees (``product`` and ``factory``) from the definition's ``structure``
block: each level's name, plural, visibility (``hidden`` when a level shown
``when_any`` has no node in this factory) and authority, who answers per
level, the verbs per level, kind names (a kind named after its level reads
the level's name), the authority of kinds changed apart from their level (a
study's), the order column titles, ``tiles_max``, ``fold_single_child`` and
the definition ``version``. Owners rename levels with ``PUT …/definition``
(``structure.levels.<key>.name``); a definition frame tells clients to refetch.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from omnigent.server.factory import control, levels
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.routes._auth_helpers import get_user_id

router = APIRouter(prefix="/v1/factory", tags=["factory"])


@router.get("/factories/{factory_id}/levels", response_model=None)
async def get_levels(request: Request, factory_id: str) -> dict[str, Any] | JSONResponse:
    """The ladder of both trees, with what is hidden in this factory now."""
    deps = factory_deps(request.app.state)
    try:
        user_id = get_user_id(request, deps.auth_provider)
        root = await control.load_factory_root(deps, user_id, factory_id)
        return await asyncio.to_thread(levels.for_factory, deps, root.id)
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())
