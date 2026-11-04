"""
The cockpit's node pages: ``GET /v1/factory/factories/{fid}/n/{ref}`` and one
level under a node, ``GET …/n/{ref}/level/{level}`` (factory readers).

One node of the level tree at a time (:mod:`omnigent.server.factory.nodes`):
the node, its breadcrumb, its state and status line,
its roll-up, its children in order columns (``children_page``: the cursor
of the next 60), grandchildren as state counts, the levels below it and its
results. The status line is the node's blocker path, ending at whoever must
act (``node_status``); the roll-up carries burn, budget, ETA (only from
observed durations), what is in the base env and, at the root, the alarms the
server closed. ``ref`` is any node ref (``product``, ``I-1``, ``<slug>``,
``T-16``, ``PR-9876``, ``fx:health`` …); an implicit parent with one child
answers as that child. The tree is built from the snapshot cut to the viewer
(``filter_snapshot``): projects whose lead they cannot read are left out with
everything under them, and so are the members, team chips, decisions, cost
and base env they may not see. ``seen`` is accepted and ignored until last-look
marks exist. 404 ``unknown_node`` for a ref the factory has no node for;
400 ``bad_cursor`` for a cursor no page handed out.

The level view lists the nodes on one level under a node, grouped under each
one's parent, all-done groups folded, 60 a group (``cursor``: a group's
``next``); 404 ``unknown_level`` when the level is not below the node.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from omnigent.server.auth import LEVEL_EDIT
from omnigent.server.factory import node_views, nodes, people
from omnigent.server.factory.api.agents import fleet_context
from omnigent.server.factory.api.chat import UNREAD_CAP, _reader

router = APIRouter(prefix="/v1/factory", tags=["factory"])


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@router.get("/factories/{factory_id}/n/{ref}", response_model=None)
async def get_node(
    request: Request,
    factory_id: str,
    ref: str,
    children_page: str | None = None,
    seen: int | None = None,  # noqa: ARG001 - marks this person's last look (later)
) -> dict[str, Any] | JSONResponse:
    """One node page: the node, breadcrumb, state, roll-up and its children (60 a page)."""
    ctx = await fleet_context(request, factory_id)
    tree = await nodes.tree_for(
        ctx.deps, ctx.root, ctx.raw, ctx.readable, app_state=request.app.state
    )
    level = await asyncio.to_thread(people.viewer_level, ctx.deps, ctx.root.id, ctx.user_id)
    try:
        page = tree.page(ref, cursor=children_page, can_edit=level >= LEVEL_EDIT)
    except ValueError as exc:
        return _error(400, "bad_cursor", str(exc))
    if page is None:
        return _error(404, "unknown_node", f"no node {ref!r} in this factory")
    page["thread"]["unread"] = await asyncio.to_thread(
        _thread_unread, ctx.deps, ctx.root.id, page["thread"]["key"], ctx.user_id
    )
    return node_views.validate_page(page)


@router.get("/factories/{factory_id}/n/{ref}/level/{level}", response_model=None)
async def get_level(
    request: Request, factory_id: str, ref: str, level: str, cursor: str | None = None
) -> dict[str, Any] | JSONResponse:
    """One level under a node, grouped under each item's parent (60 a group)."""
    ctx = await fleet_context(request, factory_id)
    tree = await nodes.tree_for(
        ctx.deps, ctx.root, ctx.raw, ctx.readable, app_state=request.app.state
    )
    if tree.lookup(ref) is None:
        return _error(404, "unknown_node", f"no node {ref!r} in this factory")
    try:
        view = tree.level_view(ref, level, cursor=cursor)
    except ValueError as exc:
        return _error(400, "bad_cursor", str(exc))
    if view is None:
        return _error(404, "unknown_level", f"no level {level!r} below {ref!r}")
    return node_views.validate_level(view)


def _thread_unread(deps: Any, factory_id: str, thread: str, user_id: str | None) -> int:
    """This viewer's unread messages in the node's one thread (others' messages, capped)."""
    store = deps.factory_store
    reader = _reader(user_id)
    marks = store.list_chat_reads(factory_id, reader)
    last = next((r.last_seq for r in marks if r.thread == thread), 0)
    rows = store.list_chat(factory_id, thread, after_seq=last, limit=UNREAD_CAP + 1)
    return min(UNREAD_CAP, sum(1 for r in rows if r.author != user_id))
