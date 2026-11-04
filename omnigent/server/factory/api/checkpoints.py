"""
Checkpoints: the decisions the factory and people made, and changing direction from one.

- ``GET /v1/factory/factories/{fid}/n/{ref}/decisions?cursor=&asked=1``: the
  node's Decisions lens: every checkpoint at or under it, newest first, one
  line each (``{ref, line, about, at, status, by, node}``); asks a person
  must answer stay in Your move unless ``asked=1``. READ.
- ``GET /v1/factory/factories/{fid}/checkpoints/{cref}``: one checkpoint's
  details (the peek): question, options, choice, why, confidence, effects,
  the policy's verdict, what it overrides or was changed by. ``cref`` is
  ``K-n``, ``D-n`` or ``C-n``. READ.
- ``POST /v1/factory/factories/{fid}/checkpoints/{cref}/preview``
  ``{option_id? | direction?}``: what changing direction from it would take
  back, who re-plans, the cost, a ``preview_id``. People only.
- ``POST …/checkpoints/{cref}/apply`` ``{…, preview_id}``: changes direction
  (:mod:`omnigent.server.factory.change_direction`); Undo through
  ``POST …/undo/{token}``. EDIT; people only.

See :mod:`omnigent.server.factory.checkpoints` for what a checkpoint is.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from omnigent.server.auth import LEVEL_EDIT
from omnigent.server.factory import change_direction, checkpoints, node_verbs, nodes
from omnigent.server.factory import checkpoint_views as views
from omnigent.server.factory.api.agents import fleet_context
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.origin import HUMAN_ORIGIN

router = APIRouter(prefix="/v1/factory", tags=["factory"])

_T = TypeVar("_T")


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


async def _guard(call: Callable[[], Awaitable[_T]]) -> _T | JSONResponse:
    try:
        return await call()
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())


@router.get("/factories/{factory_id}/n/{ref}/decisions", response_model=None)
async def get_decisions(
    request: Request,
    factory_id: str,
    ref: str,
    cursor: str | None = None,
    asked: int | None = None,
) -> dict[str, Any] | JSONResponse:
    """The node's Decisions lens: its subtree's checkpoints, newest first, one line each."""
    ctx = await fleet_context(request, factory_id)
    tree = await nodes.tree_for(
        ctx.deps, ctx.root, ctx.raw, ctx.readable, app_state=request.app.state
    )
    found = tree.lookup(ref)
    if found is None:
        return _error(404, "unknown_node", f"no node {ref!r} in this factory")
    node = tree.fold(found)
    book = await asyncio.to_thread(checkpoints.load, ctx.deps, ctx.root.id)
    try:
        view = checkpoints.list_view(
            tree, book, node.ref, cursor=cursor, include_asked=bool(asked)
        )
    except ValueError as exc:
        return _error(400, "bad_cursor", str(exc))
    return views.validate(views.DecisionsView, view)


@router.get("/factories/{factory_id}/checkpoints/{cref}", response_model=None)
async def get_checkpoint(
    request: Request, factory_id: str, cref: str
) -> dict[str, Any] | JSONResponse:
    """One checkpoint's details (the peek)."""
    from omnigent.server.factory import people

    ref = checkpoints.parse_ref(cref)
    if ref is None:
        return _error(400, "bad_ref", "a checkpoint is K-n, D-n or C-n")
    ctx = await fleet_context(request, factory_id)
    tree = await nodes.tree_for(
        ctx.deps, ctx.root, ctx.raw, ctx.readable, app_state=request.app.state
    )
    book = await asyncio.to_thread(checkpoints.load, ctx.deps, ctx.root.id, now=time.time())
    item = book.get(ref)
    if item is None:
        return _error(404, "unknown_checkpoint", f"no checkpoint {ref!r} in this factory")
    item.nodes = checkpoints.place(tree, item)
    if not item.nodes:
        return _error(404, "unknown_checkpoint", f"no checkpoint {ref!r} in this factory")
    level = await asyncio.to_thread(people.viewer_level, ctx.deps, ctx.root.id, ctx.user_id)
    view = checkpoints.detail_view(item, can_change=level >= LEVEL_EDIT)
    return views.validate(views.CheckpointView, view)


async def _book_for(env: node_verbs.Env) -> checkpoints.Book:
    tree = await env.tree()
    book = await asyncio.to_thread(checkpoints.load, env.deps, env.fid)
    for item in book.items:
        item.nodes = checkpoints.place(tree, item)
    # Only what this person may read is theirs to change.
    book.items = [i for i in book.items if i.nodes]
    return book


@router.post(
    "/factories/{factory_id}/checkpoints/{cref}/preview",
    response_model=None,
    dependencies=HUMAN_ORIGIN,
)
async def preview_direction(
    request: Request, factory_id: str, cref: str, payload: Annotated[Any, Body()] = None
) -> dict[str, Any] | JSONResponse:
    """What changing direction from a checkpoint would take back, and who re-plans."""

    async def run() -> dict[str, Any]:
        from omnigent.server.factory.start import parse_body

        ref = checkpoints.parse_ref(cref)
        if ref is None:
            raise CockpitError(400, "bad_ref", "a checkpoint is K-n, D-n or C-n")
        env = await node_verbs.env_for(request, factory_id)
        body = parse_body(change_direction.DirectionIn, payload if payload is not None else {})
        plan = await change_direction.plan_change(env, await _book_for(env), ref, body)
        return views.validate(views.DirectionPreviewView, change_direction.preview_view(env, plan))

    return await _guard(run)


@router.post(
    "/factories/{factory_id}/checkpoints/{cref}/apply",
    response_model=None,
    dependencies=HUMAN_ORIGIN,
)
async def apply_direction(
    request: Request, factory_id: str, cref: str, payload: Annotated[Any, Body()] = None
) -> dict[str, Any] | JSONResponse:
    """Change direction from a checkpoint, as previewed; Undo for 10 minutes."""

    async def run() -> dict[str, Any]:
        ref = checkpoints.parse_ref(cref)
        if ref is None:
            raise CockpitError(400, "bad_ref", "a checkpoint is K-n, D-n or C-n")
        env = await node_verbs.env_for(request, factory_id)
        out = await change_direction.apply(env, await _book_for(env), ref, payload)
        return views.validate(views.DirectionAppliedView, out)

    return await _guard(run)
