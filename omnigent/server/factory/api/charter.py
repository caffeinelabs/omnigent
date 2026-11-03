"""A project's charter versions (:mod:`..charter_versions`).

- ``GET /v1/factory/factories/{fid}/projects/{slug}/charter/versions`` (READ):
  every applied or undone change of the charter, newest first, with its fields
  before and after, who made it, and its Undo while open.
- ``POST /v1/factory/factories/{fid}/projects/{slug}/charter/revert``
  ``{proposal, note?}`` (EDIT, MANAGE for a budget raise; human origin): put the
  fields one version changed back as they were, as a new version.

Undo within 10 minutes is the version's own ``POST …/proposals/{ref}/undo``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from omnigent.server.factory import charter_versions
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.origin import HUMAN_ORIGIN
from omnigent.server.routes._auth_helpers import get_user_id

router = APIRouter(prefix="/v1/factory", tags=["factory"])


@router.get("/factories/{factory_id}/projects/{slug}/charter/versions", response_model=None)
async def get_charter_versions(
    request: Request, factory_id: str, slug: str
) -> dict[str, Any] | JSONResponse:
    """The project's charter history, newest first."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)
    try:
        return await charter_versions.history_view(
            request.app.state, factory_id, slug, user_id=user_id
        )
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())


@router.post(
    "/factories/{factory_id}/projects/{slug}/charter/revert",
    response_model=None,
    dependencies=HUMAN_ORIGIN,
)
async def post_charter_revert(
    request: Request, factory_id: str, slug: str, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """Revert one charter version as a new version (with its own Undo)."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)
    try:
        out = await charter_versions.revert(
            request.app.state, factory_id, slug, payload, user_id=user_id
        )
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())
    out["project"].pop("workspace_id", None)
    return out


__all__ = ["router"]
