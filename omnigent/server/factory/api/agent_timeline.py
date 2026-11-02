"""One agent's last hours as a state strip: ``GET …/agents/{sid}/timeline?window=24h``.

The strip is built from the durable state segments the status edges write
(:mod:`omnigent.server.factory.telemetry.states`), the stuck incidents that
targeted the agent, and the gates and preview deploys of its PRs while it
was otherwise idle. Next to it: tool calls and spend per bucket (15 min for
6 h, an hour for 24 h, 6 h for 7 days).

Humans call it with their own credentials: READ on the root, and only an agent
the viewer may read (a handle works in place of the session id).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Final

from fastapi import APIRouter, Query, Request

from omnigent.server.factory import stuck
from omnigent.server.factory.api.agents import _not_found, _same, fleet_context, resolve_agent
from omnigent.server.factory.context import FactoryDeps
from omnigent.server.factory.routes import FactoryRouteError
from omnigent.server.factory.telemetry import states
from omnigent.server.factory.telemetry.metrics import parse_window

router = APIRouter(prefix="/v1/factory", tags=["factory"])

MAX_WINDOW_S: Final = 7 * 86400
TOOL_SCAN_MAX: Final = 3000
_CHECK_SPANS: Final = ("gate", "preview")
_PREVIEW_DEPLOYING: Final = frozenset({"deploying", "starting"})
_BUSY: Final = frozenset({"running", "waiting"})


def bucket_seconds(window_s: int) -> int:
    """:returns: The sparkline bucket for a window: 15 min ≤ 6 h, 1 h ≤ 24 h, else 6 h."""
    if window_s <= 6 * 3600:
        return 900
    if window_s <= 86400:
        return 3600
    return 6 * 3600


def _stuck_intervals(
    deps: FactoryDeps, factory_id: str, sid: str, until_ms: int
) -> list[states.Interval]:
    """Stuck stretches: open and acked incidents run to now; snoozed ones end when last seen."""
    target = f"member:{sid.replace('-', '').lower()}"
    rows = deps.factory_store.list_issues(factory_id, class_="incident", limit=2000)
    out: list[states.Interval] = []
    for issue in rows:
        if issue.rule not in stuck.STUCK_RULES:
            continue
        if issue.target.replace("-", "").lower() != target:
            continue
        if issue.resolved_at:
            end = issue.resolved_at * 1000
        elif issue.status in stuck.OPEN_STATUSES:
            end = until_ms
        else:
            end = (issue.last_seen or issue.first_seen) * 1000
        out.append((issue.first_seen * 1000, end))
    return out


def _pr_ids(deps: FactoryDeps, factory_id: str, sid: str, member: dict[str, Any]) -> set[str]:
    """The PRs whose gates the agent waits on: its tasks' (a PL: its project's)."""
    store = deps.factory_store
    if member.get("is_top_level") and member.get("role") == "project_lead" and member["project"]:
        return {p.id for p in store.list_prs(factory_id) if p.project_slug == member["project"]}
    tasks = store.list_tasks(factory_id, project_slug=member.get("project"))
    return {t.pr_id for t in tasks if t.pr_id and any(_same(sid, s) for s in t.session_ids)}


def _check_intervals(
    deps: FactoryDeps, factory_id: str, prs: set[str], since_ms: int, until_ms: int
) -> list[states.Interval]:
    if not prs:
        return []
    keys = {p.replace("-", "").lower() for p in prs}
    rows = deps.factory_store.list_spans(
        factory_id,
        kinds=list(_CHECK_SPANS),
        since_ms=since_ms - 86_400_000,
        until_ms=until_ms,
        limit=20000,
    )
    out: list[states.Interval] = []
    for span in rows:
        if not span.pr_id or span.pr_id.replace("-", "").lower() not in keys:
            continue
        if span.kind == "preview" and span.name not in _PREVIEW_DEPLOYING:
            continue
        out.append((span.started_at, span.ended_at if span.ended_at is not None else until_ms))
    return out


def _tool_counts(
    deps: FactoryDeps, sid: str, since_ms: int, bucket_ms: int, n: int
) -> tuple[list[int], bool]:
    """Tool calls per bucket, newest items first; ``True`` when the scan stopped short."""
    page = deps.conversation_store.list_items(
        sid, limit=TOOL_SCAN_MAX, order="desc", type="function_call"
    )
    counts = [0] * n
    reached = False
    for item in page.data:
        at_ms = float(item.created_at) * 1000
        if at_ms < since_ms:
            reached = True
            break
        index = states.bucket_index(at_ms, since_ms, bucket_ms, n)
        if index is not None:
            counts[index] += 1
    partial = not reached and len(page.data) >= TOOL_SCAN_MAX
    return counts, partial


def build_timeline(
    deps: FactoryDeps,
    factory_id: str,
    sid: str,
    member: dict[str, Any],
    *,
    since_ms: int,
    until_ms: int,
) -> dict[str, Any]:
    """The strip, totals and series of one agent (synchronous)."""
    live = (member.get("now") or {}).get("status") or member.get("status")
    last = (member.get("now") or {}).get("last_event_at") or member.get("last_activity_at")
    found = states.segments(deps, factory_id, sid, since_ms, until_ms)
    found = states.clip_open(found, live in _BUSY, int(last * 1000) if last else None)
    conv = deps.conversation_store.get_conversation(sid)
    born_ms = int(conv.created_at * 1000) if conv is not None and conv.created_at else None
    recorded_since, partial = states.coverage(
        states.recording_start(deps, factory_id), born_ms, since_ms
    )
    layers: dict[str, list[states.Interval]] = {states.WORKING: [], states.WAITING: []}
    for seg in found:
        layers.setdefault(seg.state, []).append((seg.start, seg.end or until_ms))
    layers[states.STUCK] = _stuck_intervals(deps, factory_id, sid, until_ms)
    layers[states.CHECKS] = _check_intervals(
        deps, factory_id, _pr_ids(deps, factory_id, sid, member), since_ms, until_ms
    )
    pieces = states.compose_strip(since_ms, until_ms, layers)
    bucket_ms = bucket_seconds((until_ms - since_ms) // 1000) * 1000
    spend = states.spend_series(found, since_ms, until_ms, bucket_ms)
    tools, tools_partial = _tool_counts(deps, sid, since_ms, bucket_ms, len(spend))
    return {
        "session_id": sid,
        "handle": member.get("handle"),
        "since": since_ms // 1000,
        "until": until_ms // 1000,
        "bucket_s": bucket_ms // 1000,
        "segments": pieces,
        "totals": states.totals(pieces),
        "series": [
            {"at": (since_ms + i * bucket_ms) // 1000, "tool_calls": tools[i], "cost_usd": cost}
            for i, cost in enumerate(spend)
        ],
        "recorded_since": recorded_since // 1000 if recorded_since else None,
        "partial": partial,
        "tool_calls_partial": tools_partial,
    }


@router.get("/factories/{factory_id}/agents/{session_id}/timeline")
async def get_agent_timeline(
    request: Request,
    factory_id: str,
    session_id: str,
    window: str = Query("24h", description="How far back, e.g. 6h, 24h or 7d (at most 7d)."),
) -> dict[str, Any]:
    """One agent's state strip, totals, tool calls and spend over *window*."""
    try:
        span = parse_window(window)
    except ValueError as exc:
        raise FactoryRouteError(400, "invalid_window", str(exc)) from None
    if span.seconds > MAX_WINDOW_S:
        raise FactoryRouteError(400, "invalid_window", "window is at most 7d")
    ctx = await fleet_context(request, factory_id)
    sid = resolve_agent(ctx, session_id)
    member = next((m for m in ctx.raw.body["members"] if m["session_id"] == sid), None)
    if sid is None or member is None:
        raise _not_found("agent")
    until_ms = int(time.time() * 1000)
    since_ms = until_ms - span.seconds * 1000
    body = await asyncio.to_thread(
        build_timeline,
        ctx.deps,
        ctx.root.id,
        sid,
        member,
        since_ms=since_ms,
        until_ms=until_ms,
    )
    body["window"] = window
    return body


__all__ = ["bucket_seconds", "build_timeline", "router"]
