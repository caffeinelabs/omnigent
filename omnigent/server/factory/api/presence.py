"""Factory presence routes: the cockpit's heartbeat and who is here.

- ``POST /v1/factory/factories/{fid}/presence`` ``{location, idle, leave?}``:
  a heartbeat every 20 s (60 s TTL); ``leave`` drops the caller at once.
- ``GET /v1/factory/factories/{fid}/presence``: cockpit viewers merged with who
  has a member transcript open.

READ on the root. A join, leave, move or idle flip publishes the ``presence``
frame scope; ``factory_people.last_seen_at`` is written at most once a minute.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from omnigent.server.factory import control, live, schemas
from omnigent.server.factory import presence as factory_presence
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.origin import HUMAN_ORIGIN
from omnigent.server.factory.snapshot import get_snapshot, readable_for
from omnigent.server.routes._auth_helpers import get_user_id

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/factory", tags=["factory"])

PRESENCE_SCOPES = ("presence",)


class PresenceBody(BaseModel):
    """``POST /v1/factory/factories/{fid}/presence``."""

    model_config = ConfigDict(extra="forbid")

    location: str | None = None
    idle: bool = False
    leave: bool = False


def _timing() -> dict[str, int]:
    return {"heartbeat_s": factory_presence.HEARTBEAT_S, "ttl_s": factory_presence.TTL_S}


async def present_entries(
    app_state: Any, root_id: str, user_id: str | None, deps: FactoryDeps
) -> list[dict[str, Any]]:
    """:returns: ``PresenceEntry`` JSON for the members *user_id* may read."""
    root = await asyncio.to_thread(deps.conversation_store.get_conversation, root_id)
    if root is None:
        return []
    raw = await get_snapshot(app_state, root)
    readable = await readable_for(user_id, raw, deps)
    return entries_for(root_id, raw.top_level_of, readable)


def entries_for(
    factory_id: str, top_level_of: dict[str, str], readable: set[str]
) -> list[dict[str, Any]]:
    """:returns: Validated ``PresenceEntry`` JSON over the readable top-level members."""
    tops = sorted({top for top in top_level_of.values() if top in readable})
    rows = factory_presence.merged_entries(factory_id, tops)
    return [
        schemas.PresenceEntry.model_validate({**row, "name": row["user_id"]}).model_dump(
            mode="json"
        )
        for row in rows
    ]


@router.post("/factories/{factory_id}/presence", response_model=None, dependencies=HUMAN_ORIGIN)
async def post_presence(request: Request, factory_id: str, body: PresenceBody) -> Any:
    """Record the caller's cockpit heartbeat; announce joins, leaves and moves."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)
    try:
        root = await control.load_factory_root(deps, user_id, factory_id)
        if not factory_presence.valid_location(body.location):
            raise CockpitError(400, "invalid_location", "unknown location", field="location")
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())
    registry = factory_presence.registry
    changed = bool(registry.sweep(root.id))
    if user_id is not None:
        if body.leave:
            changed = registry.leave(root.id, user_id) or changed
        else:
            changed = registry.heartbeat(root.id, user_id, body.location, body.idle) or changed
            if registry.claim_last_seen_write(root.id, user_id):
                await _write_last_seen(deps, root.id, user_id)
    if changed:
        live.announce_factory_changed(request.app.state, root.id, PRESENCE_SCOPES)
    return {"present": registry.count(root.id), **_timing()}


async def _write_last_seen(deps: FactoryDeps, factory_id: str, user_id: str) -> None:
    try:
        await asyncio.to_thread(
            deps.factory_store.update_person, factory_id, user_id, last_seen_at=int(time.time())
        )
    except Exception:  # noqa: BLE001 - presence still works from memory
        _logger.warning("factory: could not record last_seen_at of %s", user_id, exc_info=True)


@router.get("/factories/{factory_id}/presence", response_model=None)
async def get_presence(request: Request, factory_id: str) -> Any:
    """Who is in the factory: cockpit pages and member transcripts."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)
    try:
        root = await control.load_factory_root(deps, user_id, factory_id)
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())
    if factory_presence.registry.sweep(root.id):
        live.announce_factory_changed(request.app.state, root.id, PRESENCE_SCOPES)
    data = await present_entries(request.app.state, root.id, user_id, deps)
    return {"data": data, **_timing()}


__all__ = ["entries_for", "present_entries", "router"]
