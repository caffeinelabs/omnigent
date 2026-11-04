"""
The cockpit's node pages, lenses and sheets (factory readers).

``GET /v1/factory/factories/{fid}/n/{ref}`` is one node of the level tree at a
time (:mod:`omnigent.server.factory.nodes`): the node, its breadcrumb, its
state and status line, its roll-up, its children in order columns
(``children_page``: the cursor of the next 60), grandchildren as state
counts, the levels below it and its results. The status line is the node's
blocker path, ending at whoever must act (``node_status``); the roll-up
carries burn, budget, ETA (only from observed durations), what is in the base
env, at the root the alarms the server closed and, once this person has
looked at the node (or an ancestor) before, what changed since
(``rollup.since``, and ``changed`` on each child: :mod:`.last_look`).
``seen=1`` marks this look after the response is built (a person's request
only). ``ref`` is any node ref (``product``, ``I-1``, ``<slug>``, ``T-16``,
``PR-9876``, ``fx:health`` …); an implicit parent with one child answers as
that child. The tree is built from the snapshot cut to the viewer
(``filter_snapshot``): projects whose lead they cannot read are left out with
everything under them, and so are the members, team chips, decisions, cost
and base env they may not see. 404 ``unknown_node`` for a ref the factory has
no node for; 400 ``bad_cursor`` for a cursor no page handed out.

- ``PUT …/n/{ref}/seen``: this person's last look at the node is now (READ;
  people only).
- ``GET …/n/{ref}/level/{level}``: the nodes on one level under a node,
  grouped under each one's parent, all-done groups folded, 60 a group
  (``cursor``: a group's ``next``); ``state`` and ``q`` filter the nodes;
  404 ``unknown_level`` when the level is not below the node.
- ``GET …/n/{ref}/lineage``, ``…/timeline?from=&to=``, ``…/money``: the
  lenses (:mod:`.node_lenses`). ``from``/``to`` are epoch seconds or ISO
  instants from 2000 to a week from now, at most 7 days apart (400
  ``bad_window``); without ``from`` the timeline reads the last day.
- ``GET /factories/{fid}/needs-you?tree=product|factory``: every ask a person
  owes in one tree, ranked by what it holds (:mod:`.node_sheets`).
- ``GET /factories/{fid}/find?q=&limit=``: nodes by ref or title, with
  breadcrumbs.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any, Final, get_args

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from omnigent.server.auth import LEVEL_EDIT
from omnigent.server.factory import (
    last_look,
    node_lenses,
    node_sheets,
    node_views,
    nodes,
    people,
)
from omnigent.server.factory.api.agents import FleetContext, fleet_context
from omnigent.server.factory.api.chat import UNREAD_CAP, _reader
from omnigent.server.factory.origin import HUMAN_ORIGIN, runner_origin_reason

router = APIRouter(prefix="/v1/factory", tags=["factory"])

_STATES = frozenset(get_args(node_views.NodeState))


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


async def _tree(request: Request, ctx: FleetContext) -> nodes.Tree:
    return await nodes.tree_for(
        ctx.deps, ctx.root, ctx.raw, ctx.readable, app_state=request.app.state
    )


@router.get("/factories/{factory_id}/n/{ref}", response_model=None)
async def get_node(
    request: Request,
    factory_id: str,
    ref: str,
    children_page: str | None = None,
    seen: int | None = None,
) -> dict[str, Any] | JSONResponse:
    """One node page: the node, breadcrumb, state, roll-up and its children (60 a page)."""
    ctx = await fleet_context(request, factory_id)
    tree = await _tree(request, ctx)
    level = await asyncio.to_thread(people.viewer_level, ctx.deps, ctx.root.id, ctx.user_id)
    try:
        page = tree.page(ref, cursor=children_page, can_edit=level >= LEVEL_EDIT)
    except ValueError as exc:
        return _error(400, "bad_cursor", str(exc))
    if page is None:
        return _error(404, "unknown_node", f"no node {ref!r} in this factory")
    node = tree.nodes[page["node"]["ref"]]
    reader = _reader(ctx.user_id)
    page["thread"]["unread"] = await asyncio.to_thread(
        _thread_unread, ctx.deps, ctx.root.id, page["thread"]["key"], ctx.user_id
    )
    await asyncio.to_thread(_since, ctx, tree, node, page, reader)
    if seen and runner_origin_reason(request) is None:
        await asyncio.to_thread(
            ctx.deps.factory_store.mark_node_seen, ctx.root.id, reader, node.ref
        )
    return node_views.validate_page(page)


@router.put("/factories/{factory_id}/n/{ref}/seen", response_model=None, dependencies=HUMAN_ORIGIN)
async def put_seen(request: Request, factory_id: str, ref: str) -> dict[str, Any] | JSONResponse:
    """This person's last look at the node is now (only ever moves forward)."""
    ctx = await fleet_context(request, factory_id)
    tree = await _tree(request, ctx)
    found = tree.lookup(ref)
    if found is None:
        return _error(404, "unknown_node", f"no node {ref!r} in this factory")
    node = tree.fold(found)
    row = await asyncio.to_thread(
        ctx.deps.factory_store.mark_node_seen, ctx.root.id, _reader(ctx.user_id), node.ref
    )
    body = {"ref": node.ref, "seen_at": node_lenses.iso(row.seen_at)}
    return node_views.validate(node_views.SeenView, body)


@router.get("/factories/{factory_id}/n/{ref}/level/{level}", response_model=None)
async def get_level(
    request: Request,
    factory_id: str,
    ref: str,
    level: str,
    cursor: str | None = None,
    state: str | None = None,
    q: str | None = None,
) -> dict[str, Any] | JSONResponse:
    """One level under a node, grouped under each item's parent (60 a group)."""
    if state is not None and state not in _STATES:
        return _error(400, "bad_state", f"unknown state {state!r}")
    ctx = await fleet_context(request, factory_id)
    tree = await _tree(request, ctx)
    if tree.lookup(ref) is None:
        return _error(404, "unknown_node", f"no node {ref!r} in this factory")
    try:
        view = tree.level_view(ref, level, cursor=cursor, state=state, q=q)
    except ValueError as exc:
        return _error(400, "bad_cursor", str(exc))
    if view is None:
        return _error(404, "unknown_level", f"no level {level!r} below {ref!r}")
    return node_views.validate_level(view)


@router.get("/factories/{factory_id}/n/{ref}/lineage", response_model=None)
async def get_lineage(
    request: Request, factory_id: str, ref: str
) -> dict[str, Any] | JSONResponse:
    """Where the node came from (ancestors with their one line), what is below, its results."""
    ctx = await fleet_context(request, factory_id)
    tree = await _tree(request, ctx)
    view = node_lenses.lineage(tree, ref)
    if view is None:
        return _error(404, "unknown_node", f"no node {ref!r} in this factory")
    return node_views.validate(node_views.LineageView, view)


@router.get("/factories/{factory_id}/n/{ref}/money", response_model=None)
async def get_money(request: Request, factory_id: str, ref: str) -> dict[str, Any] | JSONResponse:
    """The children's cost against their budgets, most first."""
    ctx = await fleet_context(request, factory_id)
    tree = await _tree(request, ctx)
    view = node_lenses.money(tree, ref)
    if view is None:
        return _error(404, "unknown_node", f"no node {ref!r} in this factory")
    return node_views.validate(node_views.MoneyView, view)


@router.get("/factories/{factory_id}/n/{ref}/timeline", response_model=None)
async def get_timeline(
    request: Request, factory_id: str, ref: str
) -> dict[str, Any] | JSONResponse:
    """One lane per child over a window: work bars, waits (amber when a person is the cause)."""
    params = request.query_params
    try:
        start = _instant(params.get("from"), now=time.time())
        end = _instant(params.get("to"), now=time.time())
    except ValueError as exc:
        return _error(400, "bad_window", str(exc))
    if start is not None and end is not None and not 0 < end - start <= node_lenses.TIMELINE_MAX_S:
        return _error(400, "bad_window", "to must follow from, at most 7 days later")
    ctx = await fleet_context(request, factory_id)
    tree = await _tree(request, ctx)
    found = tree.lookup(ref)
    if found is None:
        return _error(404, "unknown_node", f"no node {ref!r} in this factory")
    node = tree.fold(found)
    stop = end if end is not None else int(tree.facts.now)
    begin = start if start is not None else stop - node_lenses.TIMELINE_DEFAULT_S
    if end is None and start is not None and not 0 < stop - begin <= node_lenses.TIMELINE_MAX_S:
        return _error(400, "bad_window", "from must be in the last 7 days")
    spans = await asyncio.to_thread(
        node_lenses.projection_spans, tree, ctx.deps.factory_store, ctx.root.id, begin, stop
    )
    look = await asyncio.to_thread(_last_look, ctx, tree, node, _reader(ctx.user_id))
    view = node_lenses.timeline(tree, ref, spans, start=start, end=end, last_look=look)
    assert view is not None
    return node_views.validate(node_views.TimelineView, view)


@router.get("/factories/{factory_id}/needs-you", response_model=None)
async def get_needs_you(
    request: Request, factory_id: str, tree: str = "product"
) -> dict[str, Any] | JSONResponse:
    """Every ask a person owes in one tree, ranked by what it holds."""
    if tree not in node_sheets.TREE_KEYS:
        return _error(400, "bad_tree", "tree is product or factory")
    ctx = await fleet_context(request, factory_id)
    built = await _tree(request, ctx)
    view = node_sheets.needs_you(built, "factory" if tree == "factory" else "product")
    return node_views.validate(node_views.NeedsYouView, view)


@router.get("/factories/{factory_id}/find", response_model=None)
async def get_find(
    request: Request, factory_id: str, q: str = "", limit: int = node_sheets.FIND_LIMIT
) -> dict[str, Any] | JSONResponse:
    """Nodes by ref (``T-16``, ``#9876``, ``C-16``) or title, each with its breadcrumb."""
    ctx = await fleet_context(request, factory_id)
    tree = await _tree(request, ctx)
    view = node_sheets.find(tree, q, limit=limit)
    return node_views.validate(node_views.FindView, view)


# ── Helpers ────────────────────────────────────────────────────


_EARLIEST: Final = 946684800
"""2000-01-01: no factory recorded anything before it."""


def _instant(value: str | None, *, now: float) -> int | None:
    """
    Epoch seconds from ``1793713200`` or ``2026-11-03T13:40:00Z``.

    :raises ValueError: On text that is no time, or one before 2000 or more
        than :data:`~.node_lenses.TIMELINE_MAX_S` after *now*.
    """
    if value is None or not value.strip():
        return None
    text = value.strip()
    outside = f"out of range: {value!r} (from 2000 to a week from now)"
    if text.isascii() and text.isdigit():
        if len(text) > 12:
            raise ValueError(outside)
        found = int(text)
    else:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"not a time: {value!r}") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        found = int(parsed.timestamp())
    if not _EARLIEST <= found <= now + node_lenses.TIMELINE_MAX_S:
        raise ValueError(outside)
    return found


def _last_look(ctx: FleetContext, tree: nodes.Tree, node: Any, reader: str) -> int | None:
    refs = last_look.mark_refs(tree, node)
    marks = {
        r.node_ref: r.seen_at
        for r in ctx.deps.factory_store.list_node_reads(ctx.root.id, reader, refs)
    }
    return last_look.last_look(tree, node, marks)


def _since(
    ctx: FleetContext, tree: nodes.Tree, node: Any, page: dict[str, Any], reader: str
) -> None:
    """``rollup.since`` and each child's ``changed`` since this person's last look."""
    seen_at = _last_look(ctx, tree, node, reader)
    if seen_at is None or seen_at >= int(time.time()):
        return
    events = last_look.read_events(ctx.deps.factory_store, ctx.root.id, seen_at)
    last_look.annotate(tree, page, events, seen_at)


def _thread_unread(deps: Any, factory_id: str, thread: str, user_id: str | None) -> int:
    """This viewer's unread messages in the node's one thread (others' messages, capped)."""
    store = deps.factory_store
    reader = _reader(user_id)
    marks = store.list_chat_reads(factory_id, reader)
    last = next((r.last_seq for r in marks if r.thread == thread), 0)
    rows = store.list_chat(factory_id, thread, after_seq=last, limit=UNREAD_CAP + 1)
    return min(UNREAD_CAP, sum(1 for r in rows if r.author != user_id))
