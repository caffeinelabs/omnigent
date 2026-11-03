"""Discovery for agents: Scout's reads and writes, and the leads' reads and ideas.

Runner tunnel routes, scoped by :mod:`..agent_scope` (the factory and the seat
come from the caller's own session labels, never from the body):

- ``POST /v1/factory/agent/discovery/read`` ``{from_session_id, kind, …}``
  (``sys_discovery_read``; Scout and the leads);
- ``POST /v1/factory/agent/discovery/code`` ``{from_session_id, label?, attach?, detach?}``
  (``sys_discovery_code``; Scout);
- ``POST /v1/factory/agent/discovery/opportunity`` ``{from_session_id, op, …}``
  (``sys_discovery_opportunity``; Scout, and the leads' ``op="idea"``).

``sys_discovery_query`` is ``POST /v1/factory/agent/discovery/query``
(:mod:`.discovery_sources`). Everything answers ``404 discovery_off`` unless the
server env ``OMNIGENT_FACTORY_DISCOVERY`` and the factory's ``discovery.enabled`` are on.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from omnigent.server.factory.agent_scope import authorize_agent
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.discovery import agent

router = APIRouter(prefix="/v1/factory", tags=["factory"])

Handler = Callable[[Any, Any, Any], Awaitable[dict[str, Any]]]


async def _call(request: Request, payload: Any, handler: Handler) -> dict[str, Any] | JSONResponse:
    try:
        sender = payload.get("from_session_id") if isinstance(payload, dict) else None
        if not isinstance(sender, str) or not sender:
            raise CockpitError(400, "invalid_body", "from_session_id is required")
        caller = await authorize_agent(request, sender)
        return await handler(request.app.state, caller, payload)
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())


@router.post("/agent/discovery/read", response_model=None)
async def agent_read(
    request: Request, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """``sys_discovery_read``: the profile, strategy, opportunities, signals, …"""
    return await _call(request, payload, agent.read)


@router.post("/agent/discovery/code", response_model=None)
async def agent_code(
    request: Request, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """``sys_discovery_code``: label, attach and detach signals (Scout)."""
    return await _call(request, payload, agent.code)


@router.post("/agent/discovery/opportunity", response_model=None)
async def agent_opportunity(
    request: Request, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """``sys_discovery_opportunity``: shape opportunities (Scout) or file an idea."""
    return await _call(request, payload, agent.opportunity)


__all__ = ["router"]
