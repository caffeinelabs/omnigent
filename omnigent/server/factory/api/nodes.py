"""
The cockpit's node pages: ``GET /v1/factory/factories/{fid}/n/{ref}`` (factory readers).

One node of the level tree at a time (:mod:`omnigent.server.factory.nodes`):
the node, its breadcrumb, its state (the status line is the state for now),
its roll-up, its children in order columns (``children_page``: the cursor
of the next 60), grandchildren as state counts, the levels below it and its
results. ``ref`` is any node ref (``product``, ``I-1``, ``<slug>``,
``T-16``, ``PR-9876``, ``fx:health`` …); an implicit parent with one child
answers as that child. Projects the viewer cannot read are left out with
everything under them. ``seen`` is accepted and ignored until last-look
marks exist. 404 ``unknown_node`` for a ref the factory has no node for;
400 ``bad_cursor`` for a cursor no page handed out.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from omnigent.server.auth import LEVEL_EDIT
from omnigent.server.factory import node_views, nodes, people
from omnigent.server.factory.api.agents import fleet_context
from omnigent.server.factory.api.chat import _reader, _unread

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
    tree = await nodes.tree_for(ctx.deps, ctx.root, ctx.raw.body, hidden=ctx.hidden)
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


def _thread_unread(deps: Any, factory_id: str, thread: str, user_id: str | None) -> int:
    """This viewer's unread messages in the node's thread (0 without one)."""
    store = deps.factory_store
    stat = next((s for s in store.chat_thread_stats(factory_id) if s.thread == thread), None)
    if stat is None:
        return 0
    reads = {r.thread: r.last_seq for r in store.list_chat_reads(factory_id, _reader(user_id))}
    return int(_unread(deps, factory_id, thread, reads.get(thread, 0), stat, user_id)["unread"])
