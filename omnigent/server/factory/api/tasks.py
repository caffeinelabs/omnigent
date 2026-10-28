"""Work-graph routes: tasks for agents (``sys_factory_task``) and for people.

Agent routes (runner tunnel; the caller is ``from_session_id``):

- ``PUT /v1/factory/agent/tasks`` ``{tasks: [...]}``: upsert (the project lead,
  for its own project).
- ``GET /v1/factory/agent/tasks?view=list|ready|get&key=&project=``: the
  project lead reads its own project; leads read any project (``ready`` is
  the PL's).
- ``POST /v1/factory/agent/tasks/{key}/{op}``: ``start``, ``done``, ``fail``,
  ``cancel`` (the PL) and ``report`` (the worker bound to the task).

Human routes (user credentials):

- ``GET /v1/factory/factories/{fid}/tasks?project=``: READ on the root;
  tasks of projects whose PL the viewer cannot read are left out.
- ``POST /v1/factory/factories/{fid}/tasks`` and
  ``PATCH /v1/factory/factories/{fid}/tasks/{id or T-n}``: EDIT on the root,
  human origin only; the PL is mailed ``[Factory control · TASK ADD|TASK EDIT · by …]``.

Refusals render as ``{"error": {"code", "message"}, ...extra}`` (e.g. ``path``
for ``cycle``, ``waiting_on`` for ``not_ready``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from omnigent.factory import ROLE_IC, ROLE_LEAD, ROLE_PROJECT_LEAD
from omnigent.server.auth import LEVEL_EDIT
from omnigent.server.factory import binder, control, people, tasks
from omnigent.server.factory.actor import Actor
from omnigent.server.factory.agent_scope import AgentCaller, authorize_agent
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.origin import HUMAN_ORIGIN
from omnigent.server.factory.routes import FactoryRouteError
from omnigent.server.factory.snapshot import get_snapshot, readable_for
from omnigent.server.factory.start import parse_body
from omnigent.server.routes._auth_helpers import get_user_id

router = APIRouter(prefix="/v1/factory", tags=["factory"])

_T = TypeVar("_T")
PL_OPS = frozenset({"start", "done", "fail", "cancel"})


class UpsertBody(BaseModel):
    """``PUT /v1/factory/agent/tasks``; each task is checked with its index in errors."""

    model_config = ConfigDict(extra="forbid")

    from_session_id: str
    tasks: list[Any] = Field(default_factory=list)


class OpBody(BaseModel):
    """``POST /v1/factory/agent/tasks/{key}/{op}``."""

    model_config = ConfigDict(extra="forbid")

    from_session_id: str
    harness: str | None = None
    result: str | None = None
    note: str | None = Field(None, max_length=1000)


async def _run(call: Awaitable[_T]) -> _T | JSONResponse:
    try:
        return await call
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())


def _agent_actor(caller: AgentCaller) -> Actor:
    return Actor.agent(caller.session.id, caller.membership.role, title=caller.title)


def _own_project(caller: AgentCaller, project: str | None = None) -> str:
    """The PL's own project (403 for anyone else, or for another project)."""
    membership = caller.membership
    slug = membership.project
    if membership.role != ROLE_PROJECT_LEAD or not membership.is_top_level or not slug:
        raise FactoryRouteError(
            403, "not_allowed", "only a project lead writes its project's tasks"
        )
    if project and project.strip() != slug:
        raise FactoryRouteError(
            403, "not_your_project", f"you lead {slug}; tasks of {project} are its PL's"
        )
    return slug


# ── Agent routes ───────────────────────────────────────────────


@router.put("/agent/tasks", response_model=None)
async def agent_upsert(request: Request, body: UpsertBody) -> dict[str, Any] | JSONResponse:
    """Create or update the caller's project tasks; answers the computed graph."""
    caller = await authorize_agent(request, body.from_session_id)
    slug = _own_project(caller)

    async def run() -> dict[str, Any]:
        items = [
            parse_body(tasks.TaskIn, item, prefix=f"tasks[{i}]")
            for i, item in enumerate(body.tasks)
        ]
        return await tasks.upsert_tasks(
            request.app.state, caller.factory_id, slug, items, _agent_actor(caller)
        )

    return await _run(run())


@router.get("/agent/tasks", response_model=None)
async def agent_read(
    request: Request,
    from_session_id: str = Query(...),
    view: str = Query("list"),
    key: str | None = Query(None),
    project: str | None = Query(None),
) -> dict[str, Any] | JSONResponse:
    """The PL's own tasks (``list``, ``ready``, ``get``), or any project's for a lead."""
    caller = await authorize_agent(request, from_session_id)
    role = caller.membership.role
    if role == ROLE_LEAD and caller.membership.is_top_level:
        if view == "ready":
            raise FactoryRouteError(403, "not_allowed", "only the project's PL dispatches")
        slug = project.strip() if project else None
    else:
        slug = _own_project(caller, project)
    deps = factory_deps(request.app.state)
    fid = caller.factory_id

    async def run() -> dict[str, Any]:
        if view == "ready":
            assert slug is not None
            return await tasks.ready_view(request.app.state, fid, slug)
        if view == "get" or key:
            if not key or not slug:
                raise CockpitError(400, "invalid_body", "get needs a key (and a lead a project)")
            return await asyncio.to_thread(tasks.get_view, deps, fid, slug, key.strip())
        if view != "list":
            raise CockpitError(400, "invalid_view", "view is list, ready or get")
        return await asyncio.to_thread(tasks.list_view, deps, fid, slug)

    return await _run(run())


@router.post("/agent/tasks/{key}/{op}", response_model=None)
async def agent_op(
    request: Request, key: str, op: str, body: OpBody
) -> dict[str, Any] | JSONResponse:
    """Start, close or report a task (``report`` is for the worker bound to it)."""
    caller = await authorize_agent(request, body.from_session_id)
    app_state = request.app.state
    actor = _agent_actor(caller)
    fid = caller.factory_id
    if op == "report":
        if caller.membership.role != ROLE_IC or not caller.membership.project:
            raise FactoryRouteError(403, "not_allowed", "report is for the task's worker")
        slug = caller.membership.project
        # The binder may not have seen this worker yet: bind it now.
        await binder.bind_session(app_state, caller.session)
        return await _run(
            tasks.report_task(
                app_state, fid, slug, key, actor, result=body.result or "", note=body.note
            )
        )
    if op not in PL_OPS:
        raise FactoryRouteError(400, "invalid_op", "op is start, done, fail, cancel or report")
    slug = _own_project(caller)
    if op == "start":
        return await _run(tasks.start_task(app_state, fid, slug, key, actor, harness=body.harness))
    return await _run(tasks.close_task(app_state, fid, slug, key, op, actor, note=body.note))


# ── Human routes ───────────────────────────────────────────────


async def _hidden(request: Request, deps: FactoryDeps, user_id: str | None, root: Any) -> set[str]:
    """Projects whose PL the viewer cannot read."""
    raw = await get_snapshot(request.app.state, root)
    readable = await readable_for(user_id, raw, deps)
    return {slug for slug, pl in raw.project_pl.items() if pl is not None and pl not in readable}


async def human_actor(
    deps: FactoryDeps, factory_id: str, user_id: str | None
) -> tuple[Actor, str]:
    """:returns: ``(the person as an actor, how server texts name them)``."""
    role = await asyncio.to_thread(people.role_of, deps, factory_id, user_id)
    by = await control.human_label(deps, factory_id, user_id)
    return Actor.human(user_id or "human", role, name=control.human_name(user_id)), by


def filter_hidden(body: dict[str, Any], hidden: set[str]) -> dict[str, Any]:
    """Drop the tasks and project graphs of hidden projects from a list view."""
    if not hidden:
        return body
    return {
        "tasks": [t for t in body["tasks"] if t["project_slug"] not in hidden],
        "projects": [p for p in body["projects"] if p["project"] not in hidden],
    }


@router.get("/factories/{factory_id}/tasks", response_model=None)
async def list_tasks(
    request: Request, factory_id: str, project: str | None = Query(None)
) -> dict[str, Any] | JSONResponse:
    """The factory's tasks with their computed graph, as this viewer may see them."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)

    async def run() -> dict[str, Any]:
        root = await control.load_factory_root(deps, user_id, factory_id)
        hidden = await _hidden(request, deps, user_id, root)
        slug = project.strip() if project else None
        if slug in hidden:
            raise CockpitError(404, "unknown_project", f"no project {slug!r} in this factory")
        body = await asyncio.to_thread(tasks.list_view, deps, root.id, slug)
        return filter_hidden(body, hidden)

    return await _run(run())


@router.post(
    "/factories/{factory_id}/tasks",
    status_code=201,
    response_model=None,
    dependencies=HUMAN_ORIGIN,
)
async def add_task(
    request: Request, factory_id: str, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """Add a task to a project; its PL gets a TASK ADD with the card."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)

    async def run() -> dict[str, Any]:
        body = parse_body(tasks.TaskAddIn, payload)
        root = await control.load_factory_root(deps, user_id, factory_id)
        await control.require(deps, user_id, root.id, LEVEL_EDIT)
        if body.project.strip() in await _hidden(request, deps, user_id, root):
            raise CockpitError(404, "unknown_project", f"no project {body.project!r}")
        actor, by = await human_actor(deps, root.id, user_id)
        return await tasks.add_task(request.app.state, root.id, body, actor, by)

    return await _run(run())


@router.patch(
    "/factories/{factory_id}/tasks/{task_ref}", response_model=None, dependencies=HUMAN_ORIGIN
)
async def edit_task(
    request: Request, factory_id: str, task_ref: str, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """Edit a task (priority, deps, budget, card) or cancel it; its PL gets a TASK EDIT."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)

    async def run() -> dict[str, Any]:
        body = parse_body(tasks.TaskEditIn, payload)
        root = await control.load_factory_root(deps, user_id, factory_id)
        await control.require(deps, user_id, root.id, LEVEL_EDIT)
        task = await asyncio.to_thread(tasks.resolve_task_ref, deps, root.id, task_ref)
        if task.project_slug in await _hidden(request, deps, user_id, root):
            raise CockpitError(404, "unknown_task", f"no task {task_ref!r} in this factory")
        actor, by = await human_actor(deps, root.id, user_id)
        return await tasks.edit_task(request.app.state, root.id, task.id, body, actor, by)

    return await _run(run())


__all__ = ["filter_hidden", "human_actor", "router"]
