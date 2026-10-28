"""The learning loop's reads and the operators' "Propose change" (Insights, Improvements).

People (user JWT; READ on the root unless noted):

- ``GET /v1/factory/factories/{fid}/versions``: definition and platform
  version markers, oldest first;
- ``GET …/bundles``: each live member's agent bundle against the one the
  server seeds now (drift means an older prompt; relaunch picks up the new);
- ``GET …/proposals/{ref}/scorecard``: a trial's baseline vs trial, guards
  and target (live while on trial, the stored verdict afterwards);
- ``POST …/definition/proposals`` (EDIT, human origin): a person proposes a
  tuning or rules change; owners approve it as a trial.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from omnigent.server.factory import control, human_tuning, learn_view
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.origin import HUMAN_ORIGIN
from omnigent.server.factory.proposals import find_proposal
from omnigent.server.factory.snapshot import get_snapshot, readable_for
from omnigent.server.routes._auth_helpers import get_user_id

router = APIRouter(prefix="/v1/factory", tags=["factory"])

_T = TypeVar("_T")


async def _guard(call: Callable[[], Awaitable[_T]]) -> _T | JSONResponse:
    try:
        return await call()
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())


@router.get("/factories/{factory_id}/versions", response_model=None)
async def get_versions(request: Request, factory_id: str) -> dict[str, Any] | JSONResponse:
    """Definition versions and platform versions, each with when it started."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)

    async def run() -> dict[str, Any]:
        root = await control.load_factory_root(deps, user_id, factory_id)
        return await asyncio.to_thread(learn_view.version_markers, deps, root.id)

    return await _guard(run)


@router.get("/factories/{factory_id}/bundles", response_model=None)
async def get_bundles(request: Request, factory_id: str) -> dict[str, Any] | JSONResponse:
    """Members whose agent bundle differs from the one seeded now."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)

    async def run() -> dict[str, Any]:
        root = await control.load_factory_root(deps, user_id, factory_id)
        raw = await get_snapshot(request.app.state, root)
        readable = await readable_for(user_id, raw, deps)
        hidden = frozenset(s for s, pl in raw.project_pl.items() if pl and pl not in readable)
        return await asyncio.to_thread(learn_view.bundle_drift, deps, root.id, hidden)

    return await _guard(run)


@router.get("/factories/{factory_id}/proposals/{ref}/scorecard", response_model=None)
async def get_scorecard(
    request: Request, factory_id: str, ref: str
) -> dict[str, Any] | JSONResponse:
    """A trial's scorecard; ``scorecard`` is null for a change that never ran as a trial."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)

    async def run() -> dict[str, Any]:
        root = await control.load_factory_root(deps, user_id, factory_id)
        row = await asyncio.to_thread(find_proposal, deps, root.id, ref)
        if row is None:
            raise CockpitError(404, "unknown_proposal", f"no proposal {ref!r} in this factory")
        card = await asyncio.to_thread(learn_view.scorecard, deps, row, int(time.time()))
        return {"proposal": ref, "scorecard": card}

    return await _guard(run)


@router.post(
    "/factories/{factory_id}/definition/proposals",
    status_code=201,
    response_model=None,
    dependencies=HUMAN_ORIGIN,
)
async def post_definition_proposal(
    request: Request, factory_id: str, payload: Annotated[Any, Body()] = None
) -> dict[str, Any] | JSONResponse:
    """An operator proposes a tuning or rules change; owners approve it as a trial."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)

    async def run() -> dict[str, Any]:
        return await human_tuning.propose_change(request.app.state, factory_id, user_id, payload)

    return await _guard(run)


__all__ = ["router"]
