"""Readouts for people: what each bet did after its release, and keeping or dropping it.

Prefix ``/v1/factory/factories/{fid}``. Reads need READ (viewers too); writes
carry the human-origin check and need EDIT (going against a readout's
recommendation needs an owner, a named reviewer and a reason). Everything
answers ``404 discovery_off`` unless the server env ``OMNIGENT_FACTORY_DISCOVERY``
and the factory's ``discovery.enabled`` are on.

- ``GET /readouts`` (``?status=&proposal=&opportunity=&limit=``): readouts,
  newest first; ``pending`` (applied bets not measured yet: the band only) and
  the prediction ``ledger``;
- ``GET /readouts/{id}``: one readout (``id`` or its key, e.g. ``C-31-d28``);
- ``POST /readouts/{id}/decide`` ``{decision?: keep|drop|iterate|extend,
  reason?, reviewer?, answer?: helped|no_change|hurt, guardrails_broken?}``;
- ``POST /readouts/{id}/reopen``: take a keep, drop or iterate back (EDIT
  within 10 minutes of the decision, an owner after that);
- ``POST /readouts/release`` ``{proposal | project, at?, note?}``: a person
  says a bet is out (the ``manual`` release; any mode may use it).

Agents never write here; Scout hears of each readout by mail.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal, TypeVar

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from omnigent.server.factory import control
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.discovery import readouts, require_on
from omnigent.server.factory.origin import HUMAN_ORIGIN
from omnigent.server.routes._auth_helpers import get_user_id
from omnigent.stores.factory_store import now_s

router = APIRouter(prefix="/v1/factory", tags=["factory"])

_T = TypeVar("_T")
_BASE = "/factories/{factory_id}/readouts"
LIST_MAX = 500


async def _guard(call: Callable[[], Awaitable[_T]]) -> _T | JSONResponse:
    try:
        return await call()
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())


async def _root(request: Request, factory_id: str) -> tuple[Any, str | None, Any]:
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)
    root = await control.load_factory_root(deps, user_id, factory_id)
    await asyncio.to_thread(require_on, deps, root.id)
    return deps, user_id, root


def _number(ref: str | None, prefix: str, field: str) -> int | None:
    if ref is None:
        return None
    flat = ref.strip().upper()
    if not flat.startswith(prefix) or not flat[len(prefix) :].isdigit():
        raise CockpitError(400, "invalid_query", f"{ref!r} is no {prefix}<n>", field=field)
    return int(flat[len(prefix) :])


@router.get(_BASE, response_model=None)
async def list_readouts(
    request: Request,
    factory_id: str,
    status: Literal["scheduled", "awaiting_answer", "written", "decided", "cancelled"]
    | None = Query(None),
    proposal: str | None = Query(None, max_length=16),
    opportunity: str | None = Query(None, max_length=16),
    limit: int = Query(200, ge=1, le=LIST_MAX),
) -> dict[str, Any] | JSONResponse:
    """Readouts (written first, newest first), the bets not measured yet, and the ledger."""

    async def run() -> dict[str, Any]:
        deps, _user, root = await _root(request, factory_id)
        bet = _number(proposal, "C-", "proposal")
        opp = _number(opportunity, "O-", "opportunity")

        def read() -> dict[str, Any]:
            now = now_s()
            every = deps.factory_store.list_readouts(root.id, limit=5000)
            rows = [
                r
                for r in every
                if (status is None or r.status == status)
                and (bet is None or r.proposal == bet)
                and (opp is None or r.opportunity == opp)
            ]
            rows.sort(key=lambda r: (r.written_at or 0, r.due_at, r.proposal, r.day), reverse=True)
            return {
                "data": [readouts.readout_view(r, now=now, siblings=every) for r in rows[:limit]],
                "total": len(rows),
                "pending": readouts.pending_bets(deps, root.id),
                "ledger": readouts.prediction_ledger(deps, root.id, rows=every),
                "release": readouts.release_mode(deps, root.id),
                "day_s": readouts.day_seconds(deps, root.id),
                "generated_at": now,
            }

        return await asyncio.to_thread(read)

    return await _guard(run)


@router.get(f"{_BASE}/{{ref}}", response_model=None)
async def get_readout(
    request: Request, factory_id: str, ref: str
) -> dict[str, Any] | JSONResponse:
    """One readout (its id, or its key such as ``C-31-d28``)."""

    async def run() -> dict[str, Any]:
        deps, _user, root = await _root(request, factory_id)
        row = await asyncio.to_thread(readouts.find, deps, root.id, ref)
        return await asyncio.to_thread(readouts.view_of, deps, root.id, row, now=now_s())

    return await _guard(run)


@router.post(f"{_BASE}/release", response_model=None, dependencies=HUMAN_ORIGIN)
async def release_bet(
    request: Request, factory_id: str, payload: Annotated[Any, Body()] = None
) -> dict[str, Any] | JSONResponse:
    """A person says a bet is out: its readouts count from then."""

    async def run() -> dict[str, Any]:
        _deps, user_id, root = await _root(request, factory_id)
        return await readouts.mark_released(request.app.state, root, user_id, payload)

    return await _guard(run)


@router.post(f"{_BASE}/{{ref}}/decide", response_model=None, dependencies=HUMAN_ORIGIN)
async def decide_readout(
    request: Request, factory_id: str, ref: str, payload: Annotated[Any, Body()] = None
) -> dict[str, Any] | JSONResponse:
    """Answer "did it help?", report a broken guardrail, or keep, drop, iterate or extend."""

    async def run() -> dict[str, Any]:
        _deps, user_id, root = await _root(request, factory_id)
        return await readouts.decide(request.app.state, root, user_id, ref, payload)

    return await _guard(run)


@router.post(f"{_BASE}/{{ref}}/reopen", response_model=None, dependencies=HUMAN_ORIGIN)
async def reopen_readout(
    request: Request, factory_id: str, ref: str
) -> dict[str, Any] | JSONResponse:
    """Take a readout's keep, drop or iterate back: it waits for a decision again."""

    async def run() -> dict[str, Any]:
        _deps, user_id, root = await _root(request, factory_id)
        return await readouts.reopen(request.app.state, root, user_id, ref)

    return await _guard(run)


__all__ = ["router"]
