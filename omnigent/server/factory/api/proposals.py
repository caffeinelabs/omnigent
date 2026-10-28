"""Project proposals and project lifecycle routes.

Agents (runner tunnel, ``from_session_id``):

- ``POST /v1/factory/agent/proposals``: file (or ``withdraw``) a projects
  proposal (``sys_factory_project``); leads for every project, a project lead
  for its own.
- ``GET /v1/factory/agent/projects``: the projects a caller may change.

People (user JWT; writes carry ``require_human_origin``):

- ``GET /v1/factory/factories/{fid}/proposals?kind=&status=``, ``GET …/proposals/{ref}``
- ``POST …/proposals/{ref}/apply`` (``{changes?}`` for Edit & apply), ``…/reject``, ``…/undo``
- ``POST /v1/factory/factories/{fid}/projects/{slug}/cancel|split|merge``

Refusals render as ``{"error": {code, message, field?}, ...}``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal, TypeVar

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from omnigent.factory import PROPOSAL_KINDS, PROPOSAL_STATUSES, ROLE_LEAD, ROLE_PROJECT_LEAD
from omnigent.server.auth import LEVEL_EDIT, LEVEL_MANAGE
from omnigent.server.factory import control, gating, projects, proposals, tuning
from omnigent.server.factory.agent_scope import authorize_agent
from omnigent.server.factory.charter import normalize_charter
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.origin import HUMAN_ORIGIN
from omnigent.server.factory.start import parse_body
from omnigent.server.routes._auth_helpers import get_user_id

router = APIRouter(prefix="/v1/factory", tags=["factory"])


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, populate_by_name=True)


_T = TypeVar("_T")
LIST_MAX: int = 200


async def _guard(call: Callable[[], Awaitable[_T]]) -> _T | JSONResponse:
    try:
        return await call()
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())


def _payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CockpitError(400, "invalid_body", "the body must be a JSON object", field="body")
    return payload


# ── Agents ─────────────────────────────────────────────────────


@router.post("/agent/proposals", response_model=None)
async def post_agent_proposal(
    request: Request, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """File or withdraw a projects proposal; it applies at once when autonomy allows."""

    async def run() -> dict[str, Any]:
        body = dict(_payload(payload))
        sender = body.pop("from_session_id", None)
        if not isinstance(sender, str) or not sender:
            raise CockpitError(400, "invalid_body", "from_session_id is required", field="body")
        caller = await authorize_agent(request, sender)
        if body.get("kind") in tuning.KINDS:
            return await tuning.handle(request.app.state, caller, body)
        return await proposals.propose(request.app.state, caller, body)

    return await _guard(run)


def _project_row(project: Any, by_slug: dict[str, Any], prs: list[Any]) -> dict[str, Any]:
    charter = normalize_charter(project.charter)
    return {
        "slug": project.slug,
        "title": project.title,
        "status": project.status,
        "stage": project.stage,
        "repo": project.repo,
        "base_branch": project.base_branch,
        "priority": project.priority,
        "goal": charter["goal"],
        "scope_paths": charter["scope_paths"],
        "budget_ics": charter["budget_ics"],
        "depends_on": charter.get("depends_on", []),
        "depends_mode": charter.get("depends_mode"),
        "waiting_on": gating.unmet_dependencies(project, by_slug, prs)
        if project.status == "active"
        else [],
        "parent_slug": project.parent_slug,
        "pl_session_id": project.pl_session_id,
        "updated_at": project.updated_at,
    }


@router.get("/agent/projects", response_model=None)
async def get_agent_projects(
    request: Request, from_session_id: str = Query(...)
) -> dict[str, Any] | JSONResponse:
    """The factory's projects for a lead; a project lead sees its own."""

    async def run() -> dict[str, Any]:
        caller = await authorize_agent(request, from_session_id)
        role = caller.membership.role
        if role not in (ROLE_LEAD, ROLE_PROJECT_LEAD):
            raise CockpitError(403, "not_allowed", "leads and project leads read projects")
        store = caller.deps.factory_store
        rows = await asyncio.to_thread(store.list_projects, caller.factory_id)
        prs = await asyncio.to_thread(store.list_prs, caller.factory_id)
        by_slug = {p.slug: p for p in rows}
        if role == ROLE_PROJECT_LEAD:
            rows = [p for p in rows if p.slug == caller.membership.project]
        pending = await asyncio.to_thread(
            lambda: store.list_proposals(
                caller.factory_id, status=["pending", "awaiting_cosign"], kind="projects"
            )
        )
        return {
            "projects": [_project_row(p, by_slug, prs) for p in rows],
            "pending_proposals": [
                {"proposal": f"C-{p.number}", "title": p.title, "approval": p.approval}
                for p in pending
            ],
        }

    return await _guard(run)


# ── People: proposals ──────────────────────────────────────────


class ApplyBody(_Body):
    """``POST …/proposals/{ref}/apply``: ``changes`` edits the proposal first."""

    changes: list[Any] | None = Field(None, max_length=proposals.MAX_CHANGES)


class RejectBody(_Body):
    """``POST …/proposals/{ref}/reject``."""

    note: str | None = Field(None, max_length=2000)


@router.get("/factories/{factory_id}/proposals", response_model=None)
async def list_proposals(
    request: Request,
    factory_id: str,
    kind: str | None = Query(None),
    status: str | None = Query(None, description="A status or a comma-separated list."),
    limit: int = Query(100, ge=1, le=LIST_MAX),
) -> dict[str, Any] | JSONResponse:
    """The factory's proposals, newest first."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)

    async def run() -> dict[str, Any]:
        root = await control.load_factory_root(deps, user_id, factory_id)
        statuses = [s.strip() for s in (status or "").split(",") if s.strip()] or None
        if statuses and any(s not in PROPOSAL_STATUSES for s in statuses):
            raise CockpitError(400, "invalid_status", "unknown status", field="status")
        if kind is not None and kind not in PROPOSAL_KINDS:
            raise CockpitError(400, "invalid_kind", "unknown kind", field="kind")
        rows = await asyncio.to_thread(
            lambda: deps.factory_store.list_proposals(
                root.id, status=statuses, kind=kind, limit=limit
            )
        )
        views = [await asyncio.to_thread(proposals.proposal_view, deps, r) for r in rows]
        return {"data": views}

    return await _guard(run)


@router.get("/factories/{factory_id}/proposals/{ref}", response_model=None)
async def get_proposal(
    request: Request, factory_id: str, ref: str
) -> dict[str, Any] | JSONResponse:
    """One proposal (``C-4``, ``4`` or its id)."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)

    async def run() -> dict[str, Any]:
        root = await control.load_factory_root(deps, user_id, factory_id)
        row = await asyncio.to_thread(proposals.find_proposal, deps, root.id, ref)
        if row is None:
            raise CockpitError(404, "unknown_proposal", f"no proposal {ref!r} in this factory")
        return {"proposal": await asyncio.to_thread(proposals.proposal_view, deps, row)}

    return await _guard(run)


@router.post(
    "/factories/{factory_id}/proposals/{ref}/apply",
    response_model=None,
    dependencies=HUMAN_ORIGIN,
)
async def apply_proposal(
    request: Request, factory_id: str, ref: str, payload: Annotated[Any, Body()] = None
) -> dict[str, Any] | JSONResponse:
    """Apply a pending proposal (Edit & apply with ``changes``); 409 when someone else decided."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)

    async def run() -> dict[str, Any]:
        body = parse_body(ApplyBody, payload if payload is not None else {})
        option = proposals.OPTION_EDIT if body.changes is not None else proposals.OPTION_APPLY
        return await proposals.answer(
            request.app.state,
            factory_id,
            ref,
            user_id=user_id,
            option_id=option,
            changes=body.changes,
        )

    return await _guard(run)


@router.post(
    "/factories/{factory_id}/proposals/{ref}/reject",
    response_model=None,
    dependencies=HUMAN_ORIGIN,
)
async def reject_proposal(
    request: Request, factory_id: str, ref: str, payload: Annotated[Any, Body()] = None
) -> dict[str, Any] | JSONResponse:
    """Reject a pending proposal; the proposer gets the note."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)

    async def run() -> dict[str, Any]:
        body = parse_body(RejectBody, payload if payload is not None else {})
        return await proposals.answer(
            request.app.state,
            factory_id,
            ref,
            user_id=user_id,
            option_id=proposals.OPTION_REJECT,
            text=body.note,
        )

    return await _guard(run)


@router.post(
    "/factories/{factory_id}/proposals/{ref}/undo",
    response_model=None,
    dependencies=HUMAN_ORIGIN,
)
async def undo_proposal(
    request: Request, factory_id: str, ref: str
) -> dict[str, Any] | JSONResponse:
    """Undo an applied proposal within its window (before its PLs spawn)."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)

    async def run() -> dict[str, Any]:
        view = await proposals.undo(request.app.state, factory_id, ref, user_id=user_id)
        return {"proposal": view}

    return await _guard(run)


# ── People: project lifecycle ──────────────────────────────────


class CancelBody(_Body):
    """``POST …/projects/{slug}/cancel``."""

    reason: str | None = Field(None, max_length=500)
    teardown_previews: bool = True


class SplitBody(_Body):
    """``POST …/projects/{slug}/split``."""

    into: list[dict[str, Any]] = Field(min_length=1, max_length=12)
    keep: Literal["cancel", "umbrella"] = "cancel"
    pr_map: dict[str, str] = Field(default_factory=dict)
    reason: str | None = Field(None, max_length=500)


class MergeBody(_Body):
    """``POST …/projects/{slug}/merge``: *slug* survives and absorbs ``from``."""

    from_: list[str] = Field(alias="from", min_length=1, max_length=12)
    patch: dict[str, Any] | None = None
    pr_map: dict[str, str] = Field(default_factory=dict)
    reason: str | None = Field(None, max_length=500)


async def _human(request: Request, factory_id: str, level: int = LEVEL_EDIT) -> tuple[Any, ...]:
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)
    root = await control.load_factory_root(deps, user_id, factory_id)
    await control.require(deps, user_id, root.id, level)
    actor = await asyncio.to_thread(projects.human_actor, deps, user_id, root.id)
    return deps, user_id, root, actor


def _row(row: Any) -> dict[str, Any]:
    out = row.to_dict()
    out.pop("workspace_id", None)
    return out


@router.post(
    "/factories/{factory_id}/projects/{slug}/cancel",
    response_model=None,
    dependencies=HUMAN_ORIGIN,
)
async def cancel_project(
    request: Request, factory_id: str, slug: str, payload: Annotated[Any, Body()] = None
) -> dict[str, Any] | JSONResponse:
    """Cancel a project: its PL stops; its PRs stay open for a human to close."""

    async def run() -> dict[str, Any]:
        body = parse_body(CancelBody, payload if payload is not None else {})
        _deps, user_id, root, actor = await _human(request, factory_id)
        row = await projects.cancel_project_core(
            request.app.state,
            root,
            slug,
            body.reason,
            actor,
            teardown_previews=body.teardown_previews,
            user_id=user_id,
        )
        return {"project": _row(row)}

    return await _guard(run)


async def _plan_one(request: Request, factory_id: str, change: dict[str, Any], reason: str | None):
    deps, user_id, root, actor = await _human(request, factory_id)
    plan = await asyncio.to_thread(proposals.validate, deps, root, [change], reason=reason)
    if plan.owner_reasons():
        await control.require(deps, user_id, root.id, LEVEL_MANAGE)
    return deps, user_id, root, actor, plan.changes[0]


@router.post(
    "/factories/{factory_id}/projects/{slug}/split",
    response_model=None,
    dependencies=HUMAN_ORIGIN,
)
async def split_project(
    request: Request, factory_id: str, slug: str, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """Split a project into new ones (their PLs spawn); PRs move only through pr_map."""

    async def run() -> dict[str, Any]:
        body = parse_body(SplitBody, payload)
        change = {"op": "split", "slug": slug, **body.model_dump(exclude={"reason"})}
        _deps, user_id, root, actor, planned = await _plan_one(
            request, factory_id, change, body.reason or "split from the cockpit"
        )
        result = await projects.split_project_core(
            request.app.state,
            root,
            slug,
            planned.creates,
            body.keep,
            planned.pr_map,
            actor,
            reason=body.reason,
            user_id=user_id,
        )
        return {
            "projects": [_row(c) for c in result.children],
            "started": {s: v.path for s, v in result.started.items()},
        }

    return await _guard(run)


@router.post(
    "/factories/{factory_id}/projects/{slug}/merge",
    response_model=None,
    dependencies=HUMAN_ORIGIN,
)
async def merge_projects(
    request: Request, factory_id: str, slug: str, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """Merge other projects into *slug*: their PLs stop, their open PRs move per pr_map."""

    async def run() -> dict[str, Any]:
        body = parse_body(MergeBody, payload)
        change = {
            "op": "merge",
            "into": slug,
            "from": body.from_,
            "patch": body.patch,
            "pr_map": body.pr_map,
        }
        _deps, user_id, root, actor, planned = await _plan_one(
            request, factory_id, change, body.reason or "merged from the cockpit"
        )
        row = await projects.merge_projects_core(
            request.app.state,
            root,
            slug,
            planned.absorbed,
            planned.patch,
            planned.pr_map,
            actor,
            reason=body.reason,
            user_id=user_id,
        )
        return {"project": _row(row)}

    return await _guard(run)


__all__ = ["router"]
