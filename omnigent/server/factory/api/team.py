"""The team catalog: what a project's team can be made of (people; read only).

- ``GET /v1/factory/factories/{fid}/catalog[?project=<slug>]``: the PL bundle's
  roles with the vendor · models each runs, the gate each is needed by, $/h
  estimates from the cost ledger, the templates, and the roles no bundle covers
  yet; with ``project``, its template locks and the members it cannot remove.

Team changes themselves go through ``PUT …/definition`` and its preview
(paths ``team.projects.<slug>…`` and ``team.templates``).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from omnigent.factory import PROJECT_SLUG_RE
from omnigent.server.factory import control, team
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.routes._auth_helpers import get_user_id

router = APIRouter(prefix="/v1/factory", tags=["factory"])


@router.get("/factories/{factory_id}/catalog", response_model=None)
async def get_catalog(
    request: Request,
    factory_id: str,
    project: str | None = Query(None, max_length=48),
) -> dict[str, Any] | JSONResponse:
    """Roles, vendors, models, gates, rates and templates a team is built from."""
    try:
        deps = factory_deps(request.app.state)
        user_id = get_user_id(request, deps.auth_provider)
        root = await control.load_factory_root(deps, user_id, factory_id)
        if project is not None and not PROJECT_SLUG_RE.match(project):
            raise CockpitError(400, "invalid_project", f"{project!r} is not a project slug")
        if project is not None:
            row = await asyncio.to_thread(deps.factory_store.get_project, root.id, project)
            if row is None:
                raise CockpitError(404, "unknown_project", f"no project {project}")
        return await asyncio.to_thread(team.catalog, deps, root.id, project)
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())


__all__ = ["router"]
