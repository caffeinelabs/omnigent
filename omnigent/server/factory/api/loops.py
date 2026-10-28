"""Loop routes: ``GET /v1/factory/factories/{fid}/loops`` (READ on the root).

Each loop (implement, review, verify, demo, ci, claim, plan, intake,
improve) as a :class:`~omnigent.server.factory.schemas.LoopView`, newest
activity first. Loops of projects whose PL the viewer cannot read are left
out. Filters: ``state`` (repeatable or comma-separated), ``project``,
``kind`` and ``pr`` (a PR row id).
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from omnigent.factory import LOOP_KINDS, LOOP_STATES
from omnigent.server.factory import control, loops
from omnigent.server.factory.api.tasks import _hidden
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.routes._auth_helpers import get_user_id

router = APIRouter(prefix="/v1/factory", tags=["factory"])


def _values(raw: list[str] | None, allowed: tuple[str, ...], name: str) -> list[str] | None:
    """Split repeated or comma-separated filter values and check them."""
    if not raw:
        return None
    found = [v.strip() for item in raw for v in item.split(",") if v.strip()]
    unknown = [v for v in found if v not in allowed]
    if unknown:
        raise CockpitError(
            400, f"invalid_{name}", f"{name} must be one of {list(allowed)}", field=name
        )
    return found or None


@router.get("/factories/{factory_id}/loops", response_model=None)
async def list_loops(
    request: Request,
    factory_id: str,
    state: Annotated[list[str] | None, Query()] = None,
    project: str | None = Query(None),
    kind: Annotated[list[str] | None, Query()] = None,
    pr: str | None = Query(None),
) -> dict[str, Any] | JSONResponse:
    """The factory's loops, as this viewer may see them."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)
    try:
        states = _values(state, LOOP_STATES, "state")
        kinds = _values(kind, LOOP_KINDS, "kind")
        root = await control.load_factory_root(deps, user_id, factory_id)
        hidden = await _hidden(request, deps, user_id, root)
        slug = project.strip() if project else None
        if slug in hidden:
            raise CockpitError(404, "unknown_project", f"no project {slug!r} in this factory")
        rows = await asyncio.to_thread(
            loops.list_view,
            deps,
            root.id,
            states=states,
            project=slug,
            kinds=kinds,
            pr_id=pr.strip() if pr else None,
        )
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "invalid_pr", "message": "pr is a PR row id"}},
        )
    return {
        "data": [r for r in rows if r.get("project_slug") not in hidden],
        "engine_mode": await asyncio.to_thread(loops.engine_mode, deps, root.id),
    }


__all__ = ["router"]
