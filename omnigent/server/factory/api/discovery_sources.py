"""Discovery sources: the agents' gateway, and people's test, pause, resume and access log.

Agents (runner tunnel, scoped by :mod:`..agent_scope`: the factory and the
seat come from the caller's own session labels, never from the body):

- ``POST /v1/factory/agent/discovery/query`` ``{from_session_id, source, tool, args}``:
  one read of an allowed source through the gateway; the answer holds signal
  ids and their excerpts wrapped as untrusted text;
- ``GET /v1/factory/agent/discovery/sources?from_session_id=``: the sources the
  caller's seat may read, with their tools.

People (prefix ``/v1/factory/factories/{fid}``; writes carry the human-origin
check): ``POST /sources/{id}/test`` (EDIT; one call, nothing stored),
``POST /sources/{id}/pause`` (EDIT; a definition version, with Undo),
``POST /sources/{id}/resume`` (owners resume a paused source; EDIT lifts the
server's hold after failures), ``GET /sources/{id}/log`` (READ).

Everything answers ``404 discovery_off`` unless the server env
``OMNIGENT_FACTORY_DISCOVERY`` and the factory's ``discovery.enabled`` are on.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from omnigent.server.factory import control
from omnigent.server.factory.agent_scope import authorize_agent
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.discovery import gateway, require_on
from omnigent.server.factory.origin import HUMAN_ORIGIN
from omnigent.server.routes._auth_helpers import get_user_id

router = APIRouter(prefix="/v1/factory", tags=["factory"])

_T = TypeVar("_T")
_BASE = "/factories/{factory_id}/sources/{source_id}"
LOG_MAX = 200


async def _guard(call: Callable[[], Awaitable[_T]]) -> _T | JSONResponse:
    try:
        return await call()
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())


async def _root(request: Request, factory_id: str) -> tuple[str | None, Any]:
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)
    root = await control.load_factory_root(deps, user_id, factory_id)
    await asyncio.to_thread(require_on, deps, root.id)
    return user_id, root


# ── Agents ─────────────────────────────────────────────────────


@router.post("/agent/discovery/query", response_model=None)
async def agent_query(
    request: Request, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """``sys_discovery_query``: read one source the caller's seat may read."""

    async def run() -> dict[str, Any]:
        sender = payload.get("from_session_id") if isinstance(payload, dict) else None
        if not isinstance(sender, str) or not sender:
            raise CockpitError(400, "invalid_body", "from_session_id is required")
        caller = await authorize_agent(request, sender)
        return await gateway.query(request.app.state, caller, payload)

    return await _guard(run)


@router.get("/agent/discovery/sources", response_model=None)
async def agent_sources(
    request: Request, from_session_id: str = Query(...)
) -> dict[str, Any] | JSONResponse:
    """The sources the caller's seat may read, their tools, and whether each is usable now."""

    async def run() -> dict[str, Any]:
        caller = await authorize_agent(request, from_session_id)
        return await gateway.agent_sources(request.app.state, caller)

    return await _guard(run)


# ── People ─────────────────────────────────────────────────────


@router.post(f"{_BASE}/test", response_model=None, dependencies=HUMAN_ORIGIN)
async def test_source(
    request: Request, factory_id: str, source_id: str
) -> dict[str, Any] | JSONResponse:
    """Check the source answers (counts one call; stores nothing)."""

    async def run() -> dict[str, Any]:
        user_id, root = await _root(request, factory_id)
        return await gateway.test(request.app.state, root, user_id, source_id)

    return await _guard(run)


@router.post(f"{_BASE}/pause", response_model=None, dependencies=HUMAN_ORIGIN)
async def pause_source(
    request: Request, factory_id: str, source_id: str, payload: Annotated[Any, Body()] = None
) -> dict[str, Any] | JSONResponse:
    """Pause the source at once (a definition version; Undo pins the version before)."""

    async def run() -> dict[str, Any]:
        user_id, root = await _root(request, factory_id)
        return await gateway.pause(request.app.state, root, user_id, source_id, payload)

    return await _guard(run)


@router.post(f"{_BASE}/resume", response_model=None, dependencies=HUMAN_ORIGIN)
async def resume_source(
    request: Request, factory_id: str, source_id: str, payload: Annotated[Any, Body()] = None
) -> dict[str, Any] | JSONResponse:
    """Resume a paused source (owners), or lift the hold after repeated failures."""

    async def run() -> dict[str, Any]:
        user_id, root = await _root(request, factory_id)
        return await gateway.resume(request.app.state, root, user_id, source_id, payload)

    return await _guard(run)


@router.get(f"{_BASE}/log", response_model=None)
async def source_log(
    request: Request,
    factory_id: str,
    source_id: str,
    limit: int = Query(100, ge=1, le=LOG_MAX),
) -> dict[str, Any] | JSONResponse:
    """The source's status and its access log (every read, pause and resume), newest first."""

    async def run() -> dict[str, Any]:
        _user, root = await _root(request, factory_id)
        return await gateway.access_log(request.app.state, root, source_id, limit=limit)

    return await _guard(run)


__all__ = ["router"]
