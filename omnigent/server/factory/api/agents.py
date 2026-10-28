"""The fleet API: every agent of a factory, sub-agents included, with its live "now".

- ``GET /v1/factory/factories/{fid}/agents``: one row per agent (role, harness,
  model, project, task and loop, status and ``now``, own and subtree cost, open
  mail in and out, pending decisions, flags, open issues) plus the newest
  "Live now" lines.
- ``GET /v1/factory/factories/{fid}/agents/{sid}``: the row plus recent tool
  calls, activity lines, mail in and out, tasks, open issues and a daily cost
  series from the ledger.

Humans call these with their own credentials: READ on the root (anything else
is a 404), and only the agents the viewer may read.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Final

from fastapi import APIRouter, Query, Request

from omnigent.entities import Conversation
from omnigent.entities.factory import FactoryEvent, FactoryIssue, FactoryTask
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.factory import ISSUE_OPEN_STATUSES, LOOP_ENDED_STATES
from omnigent.server.auth import LEVEL_READ
from omnigent.server.factory import activity_now, detectors
from omnigent.server.factory.actor import task_ref
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.routes import FactoryRouteError
from omnigent.server.factory.snapshot import (
    RawSnapshot,
    get_snapshot,
    is_factory_root,
    readable_for,
)
from omnigent.server.factory.telemetry import cost as cost_ledger
from omnigent.server.routes._auth_helpers import require_access, require_user

router = APIRouter(prefix="/v1/factory", tags=["factory"])

MAIL_WINDOW_S: Final = 6 * 3600
DETAIL_ITEMS: Final = 60
DETAIL_MAIL: Final = 100
COST_DAYS: Final = 14
LINES_MAX: Final = activity_now.RING_MAX
_MAIL_KINDS: Final = ("message.queued", "message.delivered", "message.bounced")


@dataclass(frozen=True)
class FleetContext:
    """One readable factory and what its viewer may see."""

    deps: FactoryDeps
    user_id: str | None
    root: Conversation
    raw: RawSnapshot
    readable: set[str]
    hidden: set[str]


def _not_found(what: str = "factory") -> FactoryRouteError:
    return FactoryRouteError(404, "not_found", f"{what} not found")


async def fleet_context(request: Request, factory_id: str) -> FleetContext:
    """The factory *factory_id* as the caller may read it; anything else is a 404."""
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
    return FleetContext(deps, user_id, root, raw, readable, hidden)


# ── Loading ────────────────────────────────────────────────────


@dataclass
class _Extras:
    convs: dict[str, Conversation]
    tasks: list[FactoryTask]
    loops: list[Any]
    decisions: list[Any]
    mail: list[FactoryEvent]
    issues: list[FactoryIssue]


def _load_extras(deps: FactoryDeps, factory_id: str, ids: list[str], now: float) -> _Extras:
    fstore = deps.factory_store
    return _Extras(
        convs=deps.conversation_store.get_conversations(ids) if ids else {},
        tasks=fstore.list_tasks(factory_id),
        loops=fstore.list_loops(factory_id),
        decisions=fstore.list_decisions(factory_id=factory_id, status="open", limit=500),
        mail=fstore.find_events(
            factory_id, list(_MAIL_KINDS), int((now - MAIL_WINDOW_S) * 1000), limit=5000
        ),
        issues=fstore.list_issues(factory_id, statuses=sorted(ISSUE_OPEN_STATUSES), limit=500),
    )


def _open_mail(mail: list[FactoryEvent]) -> list[FactoryEvent]:
    """Queued messages with no delivered or bounced event yet."""
    closed = {e.subject for e in mail if e.kind != "message.queued"}
    return [e for e in mail if e.kind == "message.queued" and e.subject not in closed]


def _same(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return a.replace("-", "").lower() == b.replace("-", "").lower()


def _task_of(sid: str, tasks: list[FactoryTask]) -> FactoryTask | None:
    bound = [t for t in tasks if any(_same(sid, s) for s in t.session_ids)]
    return max(bound, key=lambda t: t.updated_at) if bound else None


def task_view(task: FactoryTask) -> dict[str, Any]:
    """:returns: The compact task of an agent row."""
    return {
        "id": task.id,
        "ref": task_ref(task.number),
        "key": task.key,
        "title": task.title,
        "state": task.state,
        "attempts": task.attempts,
        "pr_id": task.pr_id,
    }


def _loop_chip(task: FactoryTask | None, loops: list[Any]) -> dict[str, Any] | None:
    if task is None:
        return None
    own = [lp for lp in loops if lp.task_id and _same(lp.task_id, task.id)]
    if not own:
        return None
    live = [lp for lp in own if lp.state not in LOOP_ENDED_STATES] or own
    loop = max(live, key=lambda lp: lp.updated_at)
    limit = (loop.spec or {}).get("attempts") or (loop.spec or {}).get("rounds")
    return {
        "id": loop.id,
        "kind": loop.kind,
        "state": loop.state,
        "iteration": loop.iteration,
        "limit": limit if isinstance(limit, int) else None,
    }


def _member_issues(sid: str, issues: list[FactoryIssue]) -> list[FactoryIssue]:
    target = f"member:{sid.replace('-', '').lower()}"
    return [i for i in issues if i.target.replace("-", "").lower() == target.replace("-", "")]


def agent_rows(ctx: FleetContext, extras: _Extras) -> list[dict[str, Any]]:
    """:returns: One row per agent the viewer may read, in snapshot order."""
    states = activity_now.now_states(ctx.root.id)
    open_mail = _open_mail(extras.mail)
    rows: list[dict[str, Any]] = []
    for member in ctx.raw.body["members"]:
        sid = member["session_id"]
        if sid not in ctx.readable or member.get("project") in ctx.hidden:
            continue
        conv = extras.convs.get(sid)
        task = _task_of(sid, extras.tasks)
        now = states.get(sid) or member.get("now")
        flags = list((now or {}).get("flags") or [])
        if member.get("health") == "dead":
            flags.append("dead")
        own = activity_now.own_cost(conv) if conv is not None else member.get("cost_usd")
        mine = _member_issues(sid, extras.issues)
        rows.append(
            {
                "session_id": sid,
                "title": member["title"],
                "role": member["role"],
                "harness": member["harness"],
                "model": (conv.reported_model or conv.model_override) if conv else None,
                "project": member["project"],
                "parent_session_id": member["parent_session_id"],
                "is_top_level": member["is_top_level"],
                "status": (now or {}).get("status") or member["status"],
                "state": member["state"],
                "health": member["health"],
                "summary": member["summary"],
                "task": task_view(task) if task is not None else None,
                "loop": _loop_chip(task, extras.loops),
                "now": now,
                "cost_usd": own,
                "subtree_cost_usd": member["cost_usd"],
                "mail_in": sum(1 for e in open_mail if _same(e.to_id, sid)),
                "mail_out": sum(1 for e in open_mail if _same(e.from_id, sid)),
                "decisions_pending": sum(
                    1 for d in extras.decisions if _same(d.asked_by_session_id, sid)
                ),
                "flags": flags,
                "issues": [i.ref for i in mine if i.ref],
                "last_activity_at": (now or {}).get("last_event_at") or member["last_activity_at"],
                "url": member["url"],
            }
        )
    return rows


def visible_lines(ctx: FleetContext, limit: int = LINES_MAX) -> list[dict[str, Any]]:
    """:returns: The newest "Live now" lines of agents the viewer may read, oldest first."""
    lines = activity_now.registry.lines(ctx.root.id)
    return [
        line
        for line in lines
        if line["session_id"] in ctx.readable and line.get("project") not in ctx.hidden
    ][-limit:]


def _live_block(ctx: FleetContext) -> dict[str, Any]:
    state = activity_now.registry.peek(ctx.root.id)
    polled = state.polled_at if state is not None else None
    return {
        "polled_at": int(polled) if polled else None,
        "interval_s": activity_now.POLL_S,
        "fresh": polled is not None and time.time() - polled < activity_now.POLL_S * 3,
        "usd_per_h": detectors.spend_rate(ctx.root.id)
        if len(ctx.readable) >= len(ctx.raw.top_level_of)
        else None,
    }


@router.get("/factories/{factory_id}/agents")
async def list_agents(
    request: Request,
    factory_id: str,
    project: str | None = Query(None, description="Only this project's agents."),
    role: str | None = Query(None, description="Only agents of this role."),
) -> dict[str, Any]:
    """Every agent of the factory the viewer may read, with its live "now"."""
    ctx = await fleet_context(request, factory_id)
    ids = [m["session_id"] for m in ctx.raw.body["members"] if m["session_id"] in ctx.readable]
    extras = await asyncio.to_thread(_load_extras, ctx.deps, ctx.root.id, ids, time.time())
    rows = [
        r
        for r in agent_rows(ctx, extras)
        if (project is None or r["project"] == project) and (role is None or r["role"] == role)
    ]
    return {"data": rows, "lines": visible_lines(ctx), "live": _live_block(ctx)}


# ── One agent ──────────────────────────────────────────────────


def _mail_rows(ctx: FleetContext, sid: str, mail: list[FactoryEvent]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in reversed(mail):
        if not (_same(e.to_id, sid) or _same(e.from_id, sid)):
            continue
        incoming = _same(e.to_id, sid)
        peer = e.from_id if incoming else e.to_id
        out.append(
            {
                "id": e.id,
                "ts": e.ts,
                "kind": e.kind,
                "direction": "in" if incoming else "out",
                "peer_id": peer,
                "peer_title": ctx.raw.titles.get(peer) if peer else None,
                "mail_kind": (e.data or {}).get("kind"),
                "mail_id": (e.data or {}).get("mail_id"),
                "summary": e.summary,
            }
        )
        if len(out) >= DETAIL_MAIL:
            break
    return out


def _tool_rows(items: list[Any]) -> list[dict[str, Any]]:
    calls = activity_now.parse_calls(items)
    return [
        {
            "call_id": c.call_id,
            "tool": c.name,
            "arg": c.arg,
            "started_at": c.started_at,
            "ended_at": c.ended_at,
            "duration_s": (c.ended_at - c.started_at) if c.ended_at is not None else None,
            "failed": c.failed,
            "output_head": c.output_head,
        }
        for c in reversed(calls)
    ]


def _cost_series(deps: FactoryDeps, factory_id: str, conv: Conversation) -> list[dict[str, Any]]:
    who = cost_ledger.attribution(deps, conv)
    if who is None:
        return []
    since = cost_ledger.utc_day(time.time() - COST_DAYS * 86400)
    cells = deps.factory_store.list_cost_daily(
        factory_id, since_day=since, project_slug=who.project
    )
    days: dict[str, dict[str, Any]] = {}
    for cell in cells:
        if cell.role != who.role or cell.agent_name != who.agent_name:
            continue
        day = days.setdefault(
            cell.day_utc,
            {"day": cell.day_utc, "cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0, "turns": 0},
        )
        day["cost_usd"] = round(day["cost_usd"] + (cell.cost_usd or 0.0), 4)
        day["tokens_in"] += cell.tokens_in or 0
        day["tokens_out"] += cell.tokens_out or 0
        day["turns"] += cell.turns or 0
    return [days[d] for d in sorted(days)]


@router.get("/factories/{factory_id}/agents/{session_id}")
async def get_agent(request: Request, factory_id: str, session_id: str) -> dict[str, Any]:
    """One agent: its row plus tool calls, activity, mail, tasks, issues and cost by day."""
    from omnigent.server.factory.api.issues import issue_view

    ctx = await fleet_context(request, factory_id)
    sid = next((s for s in ctx.raw.top_level_of if _same(s, session_id)), None)
    if sid is None or sid not in ctx.readable:
        raise _not_found("agent")
    deps = ctx.deps
    now = time.time()
    extras = await asyncio.to_thread(_load_extras, deps, ctx.root.id, [sid], now)
    rows = [r for r in agent_rows(ctx, extras) if r["session_id"] == sid]
    if not rows:
        raise _not_found("agent")
    row = rows[0]
    conv = extras.convs.get(sid)
    store = deps.conversation_store

    def _detail() -> tuple[list[Any], list[dict[str, Any]], list[FactoryEvent]]:
        items = list(reversed(store.list_items(sid, limit=DETAIL_ITEMS, order="desc").data))
        series = _cost_series(deps, ctx.root.id, conv) if conv is not None else []
        mail = deps.factory_store.find_events(
            ctx.root.id, list(_MAIL_KINDS), int((now - 7 * 86400) * 1000), limit=5000
        )
        return items, series, mail

    items, series, mail = await asyncio.to_thread(_detail)
    if row["is_top_level"] and row["role"] == "project_lead" and row["project"]:
        tasks = [t for t in extras.tasks if t.project_slug == row["project"]]
    else:
        tasks = [t for t in extras.tasks if any(_same(sid, s) for s in t.session_ids)]
    lines = activity_now.activity_lines(
        items,
        session_id=sid,
        title=row["title"],
        role=row["role"],
        project=row["project"],
    )
    issues = [issue_view(i, ctx.raw.titles) for i in _member_issues(sid, extras.issues)]
    return {
        "agent": row,
        "tools": _tool_rows(items),
        "lines": lines,
        "mail": _mail_rows(ctx, sid, mail),
        "tasks": [task_view(t) for t in sorted(tasks, key=lambda t: t.number)],
        "issues": issues,
        "cost_series": series,
    }


__all__ = ["agent_rows", "fleet_context", "router", "task_view", "visible_lines"]
