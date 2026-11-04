"""Initiatives in the cockpit: one outcome a person asked for, across projects.

People (user JWT; writes carry ``require_human_origin``):

- ``GET /v1/factory/factories/{fid}/initiatives``: ``{initiatives: [InitiativeLine]}``,
  newest first (finished ones for three days), as the viewer may see them.
- ``GET …/initiatives/{ref}``: the full view: the ask verbatim, who asked and
  when, the acceptance steps with their last result and evidence links, the
  projects as a small flow (stage, role, wave, dependencies with their
  reasons, what each waits on), the runs, the next wave and the cost so far.
- ``PATCH …/initiatives/{ref}`` (EDIT): rename, rewrite the acceptance steps,
  close (``done``), cancel or reopen; every change is an event with before and after.
- ``POST …/initiatives/{ref}/acceptance`` (EDIT): run the acceptance now.

Agents never write here: a lead starts or extends an initiative through a
projects proposal (``analysis.initiative``), and the demo agent reports a run
through ``sys_factory_demo``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from omnigent.server.auth import LEVEL_EDIT
from omnigent.server.factory import (
    acceptance,
    control,
    done_review,
    events,
    initiatives,
    projects,
)
from omnigent.server.factory.api.agents import FleetContext, fleet_context
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.origin import HUMAN_ORIGIN
from omnigent.server.factory.snapshot import filter_snapshot
from omnigent.server.factory.start import parse_body
from omnigent.stores.factory_store import now_s

router = APIRouter(prefix="/v1/factory", tags=["factory"])

_T = TypeVar("_T")


async def _guard(call: Callable[[], Awaitable[_T]]) -> _T | JSONResponse:
    try:
        return await call()
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())


def _body(ctx: FleetContext) -> dict[str, Any]:
    return filter_snapshot(ctx.raw, ctx.readable)


async def _row(ctx: FleetContext, ref: str) -> Any:
    row = await asyncio.to_thread(initiatives.find, ctx.deps, ctx.root.id, ref)
    if row is None:
        raise CockpitError(404, "unknown_initiative", f"no initiative {ref!r} in this factory")
    return row


@router.get("/factories/{factory_id}/initiatives", response_model=None)
async def list_initiatives(request: Request, factory_id: str) -> dict[str, Any]:
    """The initiatives the viewer sees, as one line each (newest first)."""
    ctx = await fleet_context(request, factory_id)
    rows = await asyncio.to_thread(initiatives.load_rows, ctx.deps, ctx.root.id, 50)
    body = _body(ctx)
    return {"initiatives": initiatives.lines_for(rows, body, now=time.time())}


@router.get("/factories/{factory_id}/initiatives/{ref}", response_model=None)
async def get_initiative(
    request: Request, factory_id: str, ref: str
) -> dict[str, Any] | JSONResponse:
    """One initiative in full: ask, acceptance with evidence, projects, runs, next wave."""
    ctx = await fleet_context(request, factory_id)

    async def run() -> dict[str, Any]:
        row = await _row(ctx, ref)
        view = initiatives.initiative_view(row, _body(ctx), now=time.time())
        view["line"] = initiatives.line_text(view)
        return view

    return await _guard(run)


@router.patch(
    "/factories/{factory_id}/initiatives/{ref}", response_model=None, dependencies=HUMAN_ORIGIN
)
async def patch_initiative(
    request: Request, factory_id: str, ref: str, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """A person renames it, rewrites its acceptance steps, closes, cancels or reopens it."""
    ctx = await fleet_context(request, factory_id)

    async def run() -> dict[str, Any]:
        await control.require(ctx.deps, ctx.user_id, ctx.root.id, LEVEL_EDIT)
        patch = parse_body(initiatives.InitiativePatch, payload)
        row = await _row(ctx, ref)
        actor = await asyncio.to_thread(projects.human_actor, ctx.deps, ctx.user_id, ctx.root.id)
        by = projects.actor_name(actor)
        stored, before, after = await asyncio.to_thread(
            lambda: initiatives.apply_patch(ctx.deps, row, patch, by=by, now=now_s())
        )
        if after:
            kind = "initiative.edited"
            if "status" in after:
                kind = "initiative.reopened" if after["status"] == "open" else "initiative.closed"
            await events.emit(
                request.app.state,
                ctx.root.id,
                kind,
                severity="notice",
                subject=f"initiative:{stored.ref}",
                summary=f"{by} changed {stored.ref} ({', '.join(sorted(after))})",
                data={
                    **actor.event_data(),
                    "initiative_id": stored.id,
                    "ref": stored.ref,
                    "before": before,
                    "after": after,
                    "by": by,
                },
            )
            if before.get("status") == "done" and after.get("status") == "open":
                await done_review.reopened(
                    request.app.state, ctx.root.id, stored, why=f"{by} reopened it"
                )
        view = initiatives.initiative_view(stored, _body(ctx), now=time.time())
        view["line"] = initiatives.line_text(view)
        return view

    return await _guard(run)


@router.post(
    "/factories/{factory_id}/initiatives/{ref}/acceptance",
    response_model=None,
    dependencies=HUMAN_ORIGIN,
)
async def run_acceptance(
    request: Request, factory_id: str, ref: str
) -> dict[str, Any] | JSONResponse:
    """Run the acceptance steps on the base env now (a person's ask)."""
    ctx = await fleet_context(request, factory_id)

    async def run() -> dict[str, Any]:
        await control.require(ctx.deps, ctx.user_id, ctx.root.id, LEVEL_EDIT)
        row = await _row(ctx, ref)
        if row.status != "open":
            raise CockpitError(409, "initiative_closed", f"{row.ref} is {row.status}")
        actor = await asyncio.to_thread(projects.human_actor, ctx.deps, ctx.user_id, ctx.root.id)
        stored = await acceptance.run_now(request.app.state, ctx.root.id, row, actor)
        view = initiatives.initiative_view(stored, _body(ctx), now=time.time())
        view["line"] = initiatives.line_text(view)
        return view

    return await _guard(run)


__all__ = ["router"]
