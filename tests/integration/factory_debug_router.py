"""Test-only debug route that forces one FactorySupervisor pass.

Mounted into a live test server through the server config's
``debug_router_modules`` key (never in production). The supervisor's clock
is wall time and heartbeats need a member idle for at least two minutes, so
the route runs one pass on a clock shifted ``advance_s`` seconds ahead.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request

from omnigent.server.factory.supervisor import FactorySupervisor, get_supervisor

router = APIRouter()


@router.post("/factory/tick")
async def force_factory_tick(request: Request, advance_s: float = 0.0) -> dict[str, object]:
    """
    Run one supervisor pass ``advance_s`` seconds in the future.

    :param request: The request; its app carries the running supervisor.
    :param advance_s: How far ahead of wall time the pass runs, e.g. ``1200``.
    :returns: ``{"ticked": true}`` once the pass and its heartbeats are dispatched.
    :raises HTTPException: 409 when factory supervision is not running.
    """
    running = get_supervisor(request.app.state)
    if running is None:
        raise HTTPException(status_code=409, detail="factory supervision is not running")
    supervisor = FactorySupervisor(
        request.app.state,
        config=running.config,
        clock=lambda: time.time() + advance_s,
    )
    await supervisor.tick()
    await supervisor.drain()
    return {"ticked": True}


DEBUG_ROUTERS = [(router, "/debug", ["debug"])]
