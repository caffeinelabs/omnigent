"""
``sys_factory_node``: one node of the cockpit's work tree, read by a factory member.

``GET /v1/factory/agent/node?from_session_id=&ref=&children_page=`` (runner
tunnel) returns the same ``NodePage`` people read at ``GET …/n/{ref}``, as the
runner's owner may read the factory (projects whose lead they cannot read are
left out). Its verbs are disabled: agents change the structure through
proposals. 404 ``unknown_node`` for a ref the factory has no node for, 400
``bad_cursor`` for a ``children_page`` no page handed out; a caller outside the
factory gets the agent routes' 403s.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from omnigent.server.factory import node_context
from omnigent.server.factory.agent_scope import authorize_agent
from omnigent.server.factory.control import CockpitError, get_conv
from omnigent.server.factory.node_views import validate_page
from omnigent.server.factory.snapshot import get_snapshot, readable_for

router = APIRouter(prefix="/v1/factory", tags=["factory"])


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@router.get("/agent/node", response_model=None)
async def read_node(
    request: Request,
    from_session_id: str = Query(...),
    ref: str = Query(..., min_length=1, max_length=200),
    children_page: str | None = Query(None, max_length=32),
) -> dict[str, Any] | JSONResponse:
    """``sys_factory_node(op="read", ref)``: the node page, as data."""
    try:
        caller = await authorize_agent(request, from_session_id)
        root = await get_conv(caller.deps, caller.factory_id)
        if root is None:
            return _error(404, "not_a_factory", "the factory root is gone")
        raw = await get_snapshot(request.app.state, root)
        readable = await readable_for(caller.user_id, raw, caller.deps)
        page = await node_context.agent_page(
            request.app.state, root, readable, ref.strip(), cursor=children_page
        )
    except ValueError as exc:
        return _error(400, "bad_cursor", str(exc))
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())
    if page is None:
        return _error(404, "unknown_node", f"no node {ref!r} in this factory")
    return validate_page(page)
