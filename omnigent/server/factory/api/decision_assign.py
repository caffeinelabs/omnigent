"""Decision claims and assignment: who is on a decision, and who it is for.

- ``POST /v1/factory/decisions/{id}/claim`` ``{release?}``: take an open
  decision you may answer (or release your claim). EDIT on the root.
- ``POST /v1/factory/decisions/{id}/assign`` ``{to}``: route it to ``owners``,
  ``operators`` or a person on the roster (clears any claim). EDIT on the
  root; MANAGE to move a decision away from the owners.

Both carry :data:`~omnigent.server.factory.origin.HUMAN_ORIGIN` and emit
``decision.claimed`` / ``decision.assigned``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from omnigent.entities.factory import FactoryDecision
from omnigent.server.auth import LEVEL_EDIT, LEVEL_MANAGE
from omnigent.server.factory import control, decision_routing, decisions, events, people
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.origin import HUMAN_ORIGIN
from omnigent.server.routes._auth_helpers import get_user_id

router = APIRouter(prefix="/v1/factory", tags=["factory"])

_T = TypeVar("_T")


class ClaimBody(BaseModel):
    """``POST /v1/factory/decisions/{id}/claim``."""

    model_config = ConfigDict(extra="forbid")

    release: bool = False


class AssignBody(BaseModel):
    """``POST /v1/factory/decisions/{id}/assign``."""

    model_config = ConfigDict(extra="forbid")

    to: str


async def _run(call: Callable[[], Awaitable[_T]]) -> _T | JSONResponse:
    try:
        return await call()
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())


async def _load_open(
    deps: FactoryDeps, user_id: str | None, decision_id: str
) -> tuple[FactoryDecision, int]:
    """The open decision and the caller's level on its root (EDIT required)."""
    try:
        decision = await asyncio.to_thread(deps.factory_store.get_decision, decision_id)
    except ValueError:
        decision = None
    if decision is None:
        raise CockpitError(404, "unknown_decision", f"no decision {decision_id!r}")
    await control.load_factory_root(deps, user_id, decision.factory_id)
    await control.require(deps, user_id, decision.factory_id, LEVEL_EDIT)
    if decision.status != "open":
        raise await decisions.not_open_error(decision, deps)
    level = await asyncio.to_thread(people.viewer_level, deps, decision.factory_id, user_id)
    return decision, level


async def _view(deps: FactoryDeps, decision: FactoryDecision) -> dict[str, Any]:
    return {"decision": await asyncio.to_thread(decisions.decision_view, deps, decision)}


@router.post("/decisions/{decision_id}/claim", response_model=None, dependencies=HUMAN_ORIGIN)
async def claim_decision(
    request: Request, decision_id: str, body: ClaimBody | None = None
) -> dict[str, Any] | JSONResponse:
    """Claim an open decision (409 ``already_claimed`` when someone else holds it)."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)
    release = body.release if body is not None else False

    async def run() -> dict[str, Any]:
        decision, level = await _load_open(deps, user_id, decision_id)
        if release:
            if decision.claimed_by not in (None, user_id) and level < LEVEL_MANAGE:
                raise CockpitError(403, "not_your_claim", f"{decision.claimed_by} holds it")
            updated = await asyncio.to_thread(
                deps.factory_store.update_decision, decision.id, claimed_by=None, claimed_at=None
            )
            return await _view(deps, updated or decision)
        await decisions.require_assigned(deps, user_id, decision)
        if decision.claimed_by and decision.claimed_by != user_id:
            raise CockpitError(
                409,
                "already_claimed",
                f"{decision.ref} is claimed by {decision.claimed_by}",
                claimed_by=decision.claimed_by,
                claimed_at=decision.claimed_at,
            )
        updated = await asyncio.to_thread(
            deps.factory_store.update_decision,
            decision.id,
            claimed_by=user_id,
            claimed_at=int(time.time()),
        )
        row = updated or decision
        by = await control.human_label(deps, decision.factory_id, user_id)
        await events.emit(
            request.app.state,
            decision.factory_id,
            "decision.claimed",
            project=decision.project_slug,
            subject=f"decision:{decision.ref}",
            summary=f"{by} claimed {decision.ref}",
            data={"decision_id": decision.id, "by": by, "by_kind": "human", "user": user_id},
        )
        return await _view(deps, row)

    return await _run(run)


@router.post("/decisions/{decision_id}/assign", response_model=None, dependencies=HUMAN_ORIGIN)
async def assign_decision(
    request: Request, decision_id: str, body: AssignBody
) -> dict[str, Any] | JSONResponse:
    """Route an open decision to owners, operators or one person on the roster."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)

    async def run() -> dict[str, Any]:
        decision, level = await _load_open(deps, user_id, decision_id)
        wanted = body.to.strip()
        assignee, warning = await asyncio.to_thread(
            decision_routing.resolve_for, deps, decision.factory_id, wanted
        )
        if assignee is None or warning is not None:
            raise CockpitError(
                400, "unknown_assignee", warning or "name owners, operators or a person"
            )
        current = decision_routing.assignee_of(decision)
        if current == decision_routing.ASSIGNEE_OWNERS and level < LEVEL_MANAGE:
            raise CockpitError(
                403, "owners_only", f"{decision.ref} is with the owners; an owner can move it"
            )
        updated = await asyncio.to_thread(
            deps.factory_store.update_decision,
            decision.id,
            assignee=assignee,
            claimed_by=None,
            claimed_at=None,
        )
        by = await control.human_label(deps, decision.factory_id, user_id)
        await events.emit(
            request.app.state,
            decision.factory_id,
            "decision.assigned",
            project=decision.project_slug,
            subject=f"decision:{decision.ref}",
            summary=f"{by} assigned {decision.ref} to {assignee}",
            data={
                "decision_id": decision.id,
                "from": current,
                "to": assignee,
                "by": by,
                "by_kind": "human",
            },
        )
        return await _view(deps, updated or decision)

    return await _run(run)


__all__ = ["router"]
