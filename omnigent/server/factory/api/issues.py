"""Issue routes: the Alerts list, the incident page, the lifecycle and alert routing.

- ``GET /v1/factory/factories/{fid}/issues?class=&status=&project=&assigned=&count_only=``
- ``POST /v1/factory/factories/{fid}/issues``: "Report a problem" (READ, human origin)
- ``GET /v1/factory/factories/{fid}/issues/{ref}``: the incident page (``ref`` is
  ``INC-n``, ``F-n`` or the row id) with the spans and events that preceded it
  on the target and its PL
- ``POST …/issues/{ref}/ack|assign|snooze|resolve``: EDIT on the root, human origin
- ``GET /v1/factory/factories/{fid}/alerts/routing`` and
  ``PUT …/alerts/slack``: whether a Slack webhook is set (owners set it; it is
  never returned)

Every read needs READ on the root (anything else is a 404) and drops the issues
of projects and agents the viewer cannot read.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final, TypeVar

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from omnigent.entities.factory import FactoryIssue
from omnigent.factory import ISSUE_CLASSES, ISSUE_STATUSES
from omnigent.server.factory import detectors, issues, people, schemas
from omnigent.server.factory.api.agents import FleetContext, fleet_context
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import CockpitError, find_project_pl
from omnigent.server.factory.origin import HUMAN_ORIGIN
from omnigent.server.factory.routes import FactoryRouteError
from omnigent.server.factory.telemetry import export
from omnigent.server.routes._auth_helpers import get_user_id
from omnigent.stores.factory_store import norm_id

router = APIRouter(prefix="/v1/factory", tags=["factory"])

LIST_MAX: Final = 500
PAGE_EVENTS: Final = 200
PAGE_SPANS: Final = 300
LOOKBACK_MS: Final = 2 * 3600 * 1000
"""How far before an incident its page looks for culprit steps."""

_T = TypeVar("_T")


class ReportBody(BaseModel):
    """``POST …/issues`` ("Report a problem")."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    summary: str
    body: str | None = None
    target: str | None = None
    class_: str = Field(default=issues.INCIDENT, alias="class")
    severity: str = "notice"
    project: str | None = None


class AssignBody(BaseModel):
    """``POST …/issues/{ref}/assign``: a roster user id, ``operators`` or ``owners``."""

    model_config = ConfigDict(extra="forbid")

    to: str | None = None


class SnoozeBody(BaseModel):
    """``POST …/issues/{ref}/snooze``: minutes, or an epoch second."""

    model_config = ConfigDict(extra="forbid")

    minutes: int | None = None
    until: int | None = None


class ResolveBody(BaseModel):
    """``POST …/issues/{ref}/resolve``."""

    model_config = ConfigDict(extra="forbid")

    note: str | None = None
    root_cause: str | None = None
    wontfix: bool = False


class SlackBody(BaseModel):
    """``PUT …/alerts/slack``: the webhook URL, or ``null`` to remove it."""

    model_config = ConfigDict(extra="forbid")

    url: str | None = None


async def _guard(call: Callable[[], Awaitable[_T]]) -> _T | JSONResponse:
    try:
        return await call()
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())


# ── Views and visibility ───────────────────────────────────────


def _target_title(issue: FactoryIssue, titles: Mapping[str, str]) -> str | None:
    kind, _, ident = issue.target.partition(":")
    if kind == "member":
        for sid, title in titles.items():
            if sid.replace("-", "").lower() == ident.replace("-", "").lower():
                return title
        return None
    if kind == "project":
        return f"#{ident}"
    if kind == "factory":
        return "the factory"
    return None


def issue_view(issue: FactoryIssue, titles: Mapping[str, str]) -> dict[str, Any]:
    """:returns: The ``IssueView`` JSON (with the rule's runbook)."""
    row = issue.to_dict()
    for key in ("workspace_id", "dedupe_key", "version_id", "factory_id"):
        row.pop(key, None)
    row["target_title"] = _target_title(issue, titles)
    row["runbook"] = detectors.runbook(issue.rule)
    row["proposal_ref"] = None
    return schemas.IssueView.model_validate(row).model_dump(mode="json")


def visible(ctx: FleetContext, issue: FactoryIssue) -> bool:
    """Whether the viewer may see *issue*: its project and its agent are readable."""
    return issues.visible_to(
        issue.project_slug,
        issue.target,
        hidden=ctx.hidden,
        top_level_of=ctx.raw.top_level_of,
        readable=ctx.readable,
    )


def _viewer_level(ctx: FleetContext) -> int:
    return people.viewer_level(ctx.deps, ctx.root.id, ctx.user_id)


@router.get("/factories/{factory_id}/issues")
async def list_issues(
    request: Request,
    factory_id: str,
    class_: str | None = Query(None, alias="class", description="incident, finding or retro."),
    status: str | None = Query(None, description="A status or a comma-separated list."),
    project: str | None = Query(None),
    assigned: str = Query("all", description="'me': only issues for me or my role."),
    count_only: bool = Query(False),
    limit: int = Query(200, ge=1, le=LIST_MAX),
) -> dict[str, Any]:
    """The factory's issues, newest first, filtered to what the viewer may read."""
    if class_ is not None and class_ not in ISSUE_CLASSES:
        raise FactoryRouteError(
            400, "invalid_class", f"class must be one of {list(ISSUE_CLASSES)}"
        )
    statuses = [s.strip() for s in (status or "").split(",") if s.strip()] or None
    if statuses and any(s not in ISSUE_STATUSES for s in statuses):
        raise FactoryRouteError(
            400, "invalid_status", f"status must be among {list(ISSUE_STATUSES)}"
        )
    if assigned not in ("me", "all"):
        raise FactoryRouteError(400, "invalid_assigned", "assigned must be 'me' or 'all'")
    ctx = await fleet_context(request, factory_id)
    deps = ctx.deps
    rows = await asyncio.to_thread(
        lambda: deps.factory_store.list_issues(
            ctx.root.id, class_=class_, statuses=statuses, project_slug=project, limit=LIST_MAX
        )
    )
    rows = [r for r in rows if visible(ctx, r)]
    level = await asyncio.to_thread(_viewer_level, ctx)
    mine = {r.id for r in rows if issues.is_mine(r, ctx.user_id, level)}
    if assigned == "me":
        rows = [r for r in rows if r.id in mine]
    if count_only:
        return {"count": len(rows), "mine": len(mine)}
    data = [issue_view(r, ctx.raw.titles) for r in rows[:limit]]
    return {"data": data, "count": len(rows), "mine": sorted(mine)}


@router.post(
    "/factories/{factory_id}/issues",
    status_code=201,
    response_model=None,
    dependencies=HUMAN_ORIGIN,
)
async def report_issue(
    request: Request, factory_id: str, body: ReportBody
) -> dict[str, Any] | JSONResponse:
    """Report a problem (anyone who can read the factory)."""
    ctx = await fleet_context(request, factory_id)

    async def run() -> dict[str, Any]:
        issue = await issues.report(
            request.app.state,
            ctx.root.id,
            user_id=ctx.user_id,
            summary=body.summary,
            body=body.body,
            target=body.target,
            class_=body.class_,
            severity=body.severity,
            project=body.project,
        )
        return {"issue": issue_view(issue, ctx.raw.titles)}

    return await _guard(run)


# ── The incident page ──────────────────────────────────────────


def _event_row(event: Any, titles: Mapping[str, str]) -> dict[str, Any]:
    data = dict(event.data) if event.data else None
    if data is not None:
        data.pop("text", None)
    return {
        "id": event.id,
        "ts": event.ts,
        "kind": event.kind,
        "severity": event.severity,
        "project_slug": event.project_slug,
        "from_id": event.from_id,
        "to_id": event.to_id,
        "from_title": titles.get(event.from_id) if event.from_id else None,
        "to_title": titles.get(event.to_id) if event.to_id else None,
        "subject": event.subject,
        "summary": event.summary,
        "data": data,
    }


def _focus(ctx: FleetContext, issue: FactoryIssue) -> tuple[list[str], str | None]:
    """:returns: ``(sessions to look at, project)``: the target agent and its PL."""
    deps = ctx.deps
    kind, _, ident = issue.target.partition(":")
    sessions: list[str] = []
    project = issue.project_slug
    if kind == "member":
        match = next((s for s in ctx.raw.top_level_of if norm_id(s) == _norm(ident)), None)
        if match is not None:
            sessions.append(match)
            top = ctx.raw.top_level_of[match]
            if top != match:
                sessions.append(top)
    if project:
        pl = find_project_pl(deps, ctx.root.id, project, include_done=True)
        if pl is not None and pl.id not in sessions:
            sessions.append(pl.id)
    return [s for s in sessions if s in ctx.readable], project


def _norm(value: str) -> str:
    try:
        return norm_id(value)
    except ValueError:
        return value


def _page(ctx: FleetContext, issue: FactoryIssue) -> dict[str, Any]:
    deps: FactoryDeps = ctx.deps
    sessions, project = _focus(ctx, issue)
    since_ms = issue.first_seen * 1000 - LOOKBACK_MS
    until_ms = (issue.resolved_at or issue.last_seen) * 1000 + 60_000
    store = deps.factory_store
    found = store.list_events(
        ctx.root.id,
        until_ms,
        PAGE_EVENTS,
        None,
        None if sessions else project,
        sessions or None,
    )
    evs = [
        e
        for e in found
        if e.ts >= since_ms and (not e.project_slug or e.project_slug not in ctx.hidden)
    ]
    spans = store.list_spans(
        ctx.root.id, since_ms=since_ms, until_ms=until_ms, project_slug=project, limit=2000
    )
    wanted = {_norm(s) for s in sessions}
    picked = [
        s
        for s in spans
        if (not wanted or (s.session_id and _norm(s.session_id) in wanted))
        and (not s.project_slug or s.project_slug not in ctx.hidden)
    ][-PAGE_SPANS:]
    return {
        "events": [_event_row(e, ctx.raw.titles) for e in reversed(evs)],
        "spans": [
            schemas.SpanView.model_validate(export.span_row(s)).model_dump(mode="json")
            for s in picked
        ],
        "sessions": [{"session_id": s, "title": ctx.raw.titles.get(s)} for s in sessions],
    }


async def _issue_for(ctx: FleetContext, ref: str) -> FactoryIssue:
    issue = await asyncio.to_thread(issues.find_issue, ctx.deps, ctx.root.id, ref)
    if issue is None or not visible(ctx, issue):
        raise FactoryRouteError(404, "not_found", "issue not found")
    return issue


@router.get("/factories/{factory_id}/issues/{ref}")
async def get_issue(request: Request, factory_id: str, ref: str) -> dict[str, Any]:
    """The incident page: the issue, the steps before it, and the people it is for."""
    ctx = await fleet_context(request, factory_id)
    issue = await _issue_for(ctx, ref)
    page = await asyncio.to_thread(_page, ctx, issue)
    level = await asyncio.to_thread(_viewer_level, ctx)
    return {
        "issue": issue_view(issue, ctx.raw.titles),
        "mine": issues.is_mine(issue, ctx.user_id, level),
        **page,
    }


# ── Lifecycle ──────────────────────────────────────────────────


async def _lifecycle(
    request: Request,
    factory_id: str,
    ref: str,
    act: Callable[[Any, str, str | None], Awaitable[FactoryIssue]],
) -> dict[str, Any] | JSONResponse:
    ctx = await fleet_context(request, factory_id)
    await _issue_for(ctx, ref)

    async def run() -> dict[str, Any]:
        issue = await act(request.app.state, ctx.root.id, ctx.user_id)
        return {"issue": issue_view(issue, ctx.raw.titles)}

    return await _guard(run)


@router.post(
    "/factories/{factory_id}/issues/{ref}/ack", response_model=None, dependencies=HUMAN_ORIGIN
)
async def ack_issue(request: Request, factory_id: str, ref: str) -> dict[str, Any] | JSONResponse:
    """Acknowledge an issue (operator)."""
    return await _lifecycle(
        request, factory_id, ref, lambda st, fid, uid: issues.ack(st, fid, ref, user_id=uid)
    )


@router.post(
    "/factories/{factory_id}/issues/{ref}/assign", response_model=None, dependencies=HUMAN_ORIGIN
)
async def assign_issue(
    request: Request, factory_id: str, ref: str, body: AssignBody
) -> dict[str, Any] | JSONResponse:
    """Assign an issue to a person, ``operators`` or ``owners`` (operator)."""
    return await _lifecycle(
        request,
        factory_id,
        ref,
        lambda st, fid, uid: issues.assign(st, fid, ref, user_id=uid, to=body.to),
    )


@router.post(
    "/factories/{factory_id}/issues/{ref}/snooze", response_model=None, dependencies=HUMAN_ORIGIN
)
async def snooze_issue(
    request: Request, factory_id: str, ref: str, body: SnoozeBody
) -> dict[str, Any] | JSONResponse:
    """Snooze an issue (operator)."""
    return await _lifecycle(
        request,
        factory_id,
        ref,
        lambda st, fid, uid: issues.snooze(
            st, fid, ref, user_id=uid, minutes=body.minutes, until=body.until
        ),
    )


@router.post(
    "/factories/{factory_id}/issues/{ref}/resolve", response_model=None, dependencies=HUMAN_ORIGIN
)
async def resolve_issue(
    request: Request, factory_id: str, ref: str, body: ResolveBody | None = None
) -> dict[str, Any] | JSONResponse:
    """Resolve an issue, optionally confirming its root cause (operator)."""
    resolve = body or ResolveBody()
    return await _lifecycle(
        request,
        factory_id,
        ref,
        lambda st, fid, uid: issues.resolve(
            st,
            fid,
            ref,
            user_id=uid,
            note=resolve.note,
            root_cause=resolve.root_cause,
            wontfix=resolve.wontfix,
        ),
    )


# ── Alert routing ──────────────────────────────────────────────


@router.get("/factories/{factory_id}/alerts/routing")
async def get_routing(request: Request, factory_id: str) -> dict[str, Any]:
    """Where incidents go: cockpit, browser, and whether a Slack webhook is set."""
    ctx = await fleet_context(request, factory_id)
    return await asyncio.to_thread(issues.routing_view, ctx.deps, ctx.root.id)


@router.put("/factories/{factory_id}/alerts/slack", response_model=None, dependencies=HUMAN_ORIGIN)
async def put_slack(
    request: Request, factory_id: str, body: SlackBody
) -> dict[str, Any] | JSONResponse:
    """Set or remove the Slack webhook (owners). The URL is never returned."""
    user_id = get_user_id(request, factory_deps(request.app.state).auth_provider)

    async def run() -> dict[str, Any]:
        configured = await issues.set_slack_webhook(
            request.app.state, factory_id, user_id=user_id, url=body.url
        )
        return {"slack": configured}

    return await _guard(run)


__all__ = ["issue_view", "router", "visible"]
