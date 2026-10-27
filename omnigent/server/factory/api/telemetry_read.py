"""Telemetry read routes: spans, a PR's timeline, the cost ledger and exports.

Humans call these with their own credentials. Every route needs READ on the
factory root (anything else is a 404) and drops what the cockpit hides: the
projects whose PL the viewer cannot read, with their PRs, spans, cost,
demos and events. Exports stream and never include transcript content.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any, Final

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from omnigent.entities import Conversation
from omnigent.entities.factory import FactorySpan
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.factory import SPAN_KINDS
from omnigent.server.auth import LEVEL_READ
from omnigent.server.factory import schemas
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.prs import pr_subject
from omnigent.server.factory.routes import FactoryRouteError
from omnigent.server.factory.snapshot import (
    get_snapshot,
    is_factory_root,
    readable_for,
    readable_set,
)
from omnigent.server.factory.telemetry import export
from omnigent.server.routes._auth_helpers import require_access, require_user
from omnigent.stores.factory_store import norm_id

router = APIRouter(prefix="/v1/factory", tags=["factory"])

SPANS_MAX_LIMIT: Final = 2000
TIMELINE_MAX: Final = 2000
GROUP_KEYS: Final[dict[str, str]] = {
    "day": "day_utc",
    "project": "project_slug",
    "role": "role",
    "agent": "agent_name",
    "model": "model",
}
"""``group_by`` names → ``factory_cost_daily`` columns."""
_DAY_RE: Final = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class _Viewer:
    """A readable factory and what its viewer may see in it."""

    deps: FactoryDeps
    root: Conversation
    vis: export.Visibility


def _not_found(what: str = "factory") -> FactoryRouteError:
    return FactoryRouteError(404, "not_found", f"{what} not found")


async def _viewer(request: Request, factory_id: str) -> _Viewer:
    deps = factory_deps(request.app.state)
    user_id = require_user(request, deps.auth_provider)
    try:
        await require_access(
            user_id, factory_id, LEVEL_READ, deps.permission_store, deps.conversation_store
        )
    except OmnigentError as exc:
        if exc.code in (ErrorCode.NOT_FOUND, ErrorCode.FORBIDDEN):
            raise _not_found() from exc
        raise
    root = await asyncio.to_thread(deps.conversation_store.get_conversation, factory_id)
    if root is None or not is_factory_root(root):
        raise _not_found()
    raw = await get_snapshot(request.app.state, root)
    readable = await readable_for(user_id, raw, deps)
    hidden = {slug for slug, pl in raw.project_pl.items() if pl is not None and pl not in readable}
    vis = export.Visibility(
        hidden_projects=frozenset(hidden),
        members=frozenset(raw.top_level_of),
        readable=frozenset(readable),
        # Sessions outside the factory: readable_set, as the activity feed checks them.
        check=lambda ids: readable_set(user_id, ids, deps),
    )
    return _Viewer(deps=deps, root=root, vis=vis)


def _id(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    try:
        return norm_id(value)
    except ValueError:
        raise FactoryRouteError(400, f"invalid_{name}", f"{name} is not an id") from None


async def _span_views(spans: list[FactorySpan], vis: export.Visibility) -> list[dict[str, Any]]:
    await asyncio.to_thread(vis.resolve, [s.session_id for s in spans])
    return [
        schemas.SpanView.model_validate(export.span_row(s)).model_dump(mode="json")
        for s in spans
        if vis.project(s.project_slug) and vis.session(s.session_id)
    ]


@router.get("/factories/{factory_id}/spans")
async def list_factory_spans(
    request: Request,
    factory_id: str,
    kind: str | None = Query(None, description="Comma-separated span kinds."),
    project: str | None = Query(None),
    pr_id: str | None = Query(None),
    task_id: str | None = Query(None),
    since: int | None = Query(None, ge=0, description="Spans started at or after (epoch ms)."),
    until: int | None = Query(None, ge=0, description="Spans started before (epoch ms)."),
    limit: int = Query(500, ge=1, le=SPANS_MAX_LIMIT),
) -> dict[str, Any]:
    """The factory's spans, oldest first, filtered to what the viewer may read."""
    kinds = [k.strip() for k in (kind or "").split(",") if k.strip()] or None
    unknown = sorted(set(kinds or ()) - set(SPAN_KINDS))
    if unknown:
        raise FactoryRouteError(400, "invalid_kind", f"unknown span kind(s): {', '.join(unknown)}")
    pr_key, task_key = _id(pr_id, "pr_id"), _id(task_id, "task_id")
    viewer = await _viewer(request, factory_id)
    rows = await asyncio.to_thread(
        lambda: viewer.deps.factory_store.list_spans(
            viewer.root.id,
            kinds=kinds,
            since_ms=since,
            until_ms=until,
            project_slug=project,
            pr_id=pr_key,
            task_id=task_key,
            limit=limit + 1,
        )
    )
    more = len(rows) > limit
    return {
        "data": await _span_views(rows[:limit], viewer.vis),
        "has_more": more,
        "next_since": rows[limit].started_at if more else None,
    }


@router.get("/prs/{pr_id}/timeline")
async def pr_timeline(request: Request, pr_id: str) -> dict[str, Any]:
    """One PR's spans (PR, rounds, gates, ready waits, previews, demos, decisions) and events."""
    key = _id(pr_id, "pr_id")
    deps = factory_deps(request.app.state)
    pr = await asyncio.to_thread(deps.factory_store.get_pr, key) if key else None
    if pr is None:
        raise _not_found("PR")
    try:
        viewer = await _viewer(request, pr.factory_id)
    except FactoryRouteError as exc:
        raise _not_found("PR") from exc
    if not viewer.vis.project(pr.project_slug):
        raise _not_found("PR")
    store = deps.factory_store
    spans = await asyncio.to_thread(
        lambda: store.list_spans(pr.factory_id, pr_id=pr.id, limit=TIMELINE_MAX)
    )
    found = await asyncio.to_thread(
        lambda: store.find_events(
            pr.factory_id, "*", 0, pr_subject(pr.repo, pr.number), limit=TIMELINE_MAX
        )
    )
    vis = viewer.vis
    await asyncio.to_thread(vis.resolve, [sid for e in found for sid in (e.from_id, e.to_id)])
    return {
        "pr": export.pr_row(pr),
        "spans": await _span_views(spans, vis),
        "events": [
            export.event_row(e) for e in found if vis.session(e.from_id) and vis.session(e.to_id)
        ],
    }


def _day(value: str | None, name: str) -> str | None:
    if value is not None and not _DAY_RE.fullmatch(value):
        raise FactoryRouteError(400, f"invalid_{name}", f"{name} must be YYYY-MM-DD")
    return value


@router.get("/factories/{factory_id}/cost")
async def factory_cost(
    request: Request,
    factory_id: str,
    group_by: str = Query("day", description="Comma-separated: day, project, role, agent, model."),
    since: str | None = Query(None, description="First day (YYYY-MM-DD, UTC)."),
    until: str | None = Query(None, description="Last day (YYYY-MM-DD, UTC)."),
    project: str | None = Query(None),
) -> dict[str, Any]:
    """The cost ledger summed by *group_by* (display cost; ``priced`` false when nothing was)."""
    keys = [k.strip() for k in group_by.split(",") if k.strip()] or ["day"]
    unknown = sorted(set(keys) - set(GROUP_KEYS))
    if unknown:
        raise FactoryRouteError(
            400, "invalid_group_by", f"group_by must be among {', '.join(GROUP_KEYS)}"
        )
    keys = list(dict.fromkeys(keys))
    since_day, until_day = _day(since, "since"), _day(until, "until")
    viewer = await _viewer(request, factory_id)
    cells = await asyncio.to_thread(
        lambda: viewer.deps.factory_store.list_cost_daily(
            viewer.root.id, since_day=since_day, until_day=until_day, project_slug=project
        )
    )
    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    totals = {"cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0, "turns": 0}
    for cell in cells:
        if not viewer.vis.project(cell.project_slug):
            continue
        group_key = tuple(getattr(cell, GROUP_KEYS[k]) for k in keys)
        row = groups.setdefault(
            group_key,
            {**dict(zip(keys, group_key, strict=True)), **dict.fromkeys(totals, 0)},
        )
        for name in totals:
            row[name] += getattr(cell, name)
            totals[name] += getattr(cell, name)
    data = [groups[k] for k in sorted(groups)]
    for row in (*data, totals):
        row["cost_usd"] = round(float(row["cost_usd"]), 6)
        row["priced"] = row["cost_usd"] > 0
    return {"group_by": keys, "data": data, "totals": totals}


@router.get("/factories/{factory_id}/export")
async def export_factory(
    request: Request,
    factory_id: str,
    tables: str = Query(",".join(export.TABLES), description="Comma-separated tables."),
    fmt: str = Query("ndjson", alias="format", description="ndjson or csv."),
    since: int = Query(0, ge=0, description="Spans and events from (epoch ms)."),
) -> StreamingResponse:
    """Stream the factory's telemetry tables (never transcripts) as NDJSON or CSV."""
    names = [t.strip() for t in tables.split(",") if t.strip()]
    unknown = sorted(set(names) - set(export.TABLES))
    if unknown or not names:
        raise FactoryRouteError(
            400, "invalid_tables", f"tables must be among {', '.join(export.TABLES)}"
        )
    if fmt not in export.FORMATS:
        raise FactoryRouteError(400, "invalid_format", "format must be ndjson or csv")
    viewer = await _viewer(request, factory_id)
    stamp = time.strftime("%Y%m%d", time.gmtime())
    filename = f"factory-{norm_id(viewer.root.id)[:8]}-{stamp}.{fmt}"
    return StreamingResponse(
        export.stream_export(
            viewer.deps.factory_store,
            viewer.root.id,
            list(dict.fromkeys(names)),
            viewer.vis,
            fmt=fmt,
            since_ms=since,
        ),
        media_type="application/x-ndjson" if fmt == "ndjson" else "text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
