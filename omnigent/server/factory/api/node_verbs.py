"""
The cockpit's structural verbs at a node (people only; human origin).

- ``POST /v1/factory/factories/{fid}/n/{ref}/preview`` ``{verb, args, based_on}``:
  what the verb would do (effects, the parent's columns with the change as a
  ghost, the roll-up before and after, who approves) and a ``preview_id``.
  Stores nothing. READ.
- ``POST /v1/factory/factories/{fid}/n/{ref}/apply`` ``{verb, args, based_on,
  preview_id}``: applies exactly that preview (409 ``stale`` when what it
  touches changed since), returns ``{result: NodePage, undo, filed?}``. EDIT
  (an owner for owner changes: 403 ``authority`` with ``who``).
- ``POST /v1/factory/factories/{fid}/undo/{token}``: takes it back within 10
  minutes, once. EDIT (the person who applied it, or an owner).

See :mod:`omnigent.server.factory.node_verbs` for the verbs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from omnigent.server.factory import node_verbs
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.origin import HUMAN_ORIGIN

router = APIRouter(prefix="/v1/factory", tags=["factory"])

_T = TypeVar("_T")


async def _guard(call: Callable[[], Awaitable[_T]]) -> _T | JSONResponse:
    try:
        return await call()
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())


@router.post(
    "/factories/{factory_id}/n/{ref}/preview", response_model=None, dependencies=HUMAN_ORIGIN
)
async def preview_verb(
    request: Request, factory_id: str, ref: str, payload: Annotated[Any, Body()] = None
) -> dict[str, Any] | JSONResponse:
    """What a verb would do at the node, before anything changes."""

    async def run() -> dict[str, Any]:
        env = await node_verbs.env_for(request, factory_id)
        return await node_verbs.preview(env, ref, payload)

    return await _guard(run)


@router.post(
    "/factories/{factory_id}/n/{ref}/apply", response_model=None, dependencies=HUMAN_ORIGIN
)
async def apply_verb(
    request: Request, factory_id: str, ref: str, payload: Annotated[Any, Body()] = None
) -> dict[str, Any] | JSONResponse:
    """Apply a previewed verb; Undo for 10 minutes."""

    async def run() -> dict[str, Any]:
        env = await node_verbs.env_for(request, factory_id)
        return await node_verbs.apply(env, ref, payload)

    return await _guard(run)


@router.post(
    "/factories/{factory_id}/undo/{token}", response_model=None, dependencies=HUMAN_ORIGIN
)
async def undo_verb(
    request: Request, factory_id: str, token: str
) -> dict[str, Any] | JSONResponse:
    """Take an applied verb back (once, within its window)."""

    async def run() -> dict[str, Any]:
        env = await node_verbs.env_for(request, factory_id)
        return await node_verbs.undo(env, token)

    return await _guard(run)
