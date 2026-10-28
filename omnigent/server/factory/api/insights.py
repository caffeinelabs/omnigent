"""Insights and metrics: the factory measuring itself, for people and for agents.

People (user JWT; READ on the root; projects whose PL the viewer cannot read
are left out of every number):

- ``GET /v1/factory/factories/{fid}/insights?window=&project=&version=&def_version=``:
  the KPI tiles with n and the delta against the previous window, top
  findings, open incidents, the latest retro per project, experiments, and
  (owners only) the per-person breakdown;
- ``GET …/metrics?names=&group_by=&window=…``: any catalog metric, grouped by
  ``day | project | role | agent | person (owners only) | version | def_version``;
- ``GET /v1/factory/insights?window=``: the fleet (every factory the caller reads).

Agents (runner tunnel): ``GET /v1/factory/agent/insights?kind=kpis|metrics|
findings|precedents|loops`` (leads, project leads scoped to their project, the
planner) and ``POST /v1/factory/agent/retros`` (``sys_factory_retro``).
"""

from __future__ import annotations

import asyncio
import functools
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Final, TypeVar

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from omnigent.factory import LABEL_NAME, ROLE_LEAD, ROLE_PLANNER, ROLE_PROJECT_LEAD
from omnigent.server.auth import LEVEL_MANAGE
from omnigent.server.factory import control, definition, people, retro
from omnigent.server.factory.agent_scope import authorize_agent
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.envelope import display_title
from omnigent.server.factory.snapshot import (
    get_snapshot,
    list_factory_roots,
    readable_for,
    readable_set,
)
from omnigent.server.factory.telemetry import metrics
from omnigent.server.routes._auth_helpers import get_user_id

router = APIRouter(prefix="/v1/factory", tags=["factory"])

_T = TypeVar("_T")
FINDINGS_MAX: Final = 5
PRECEDENTS_MAX: Final = 5
FLEET_KPIS: Final = (
    "merge_acceptance",
    "lead_time",
    "human_prompts_per_day",
    "cost_per_merged_pr",
    "incidents",
)
AGENT_ROLES: Final = frozenset({ROLE_LEAD, ROLE_PROJECT_LEAD, ROLE_PLANNER})


async def _guard(call: Callable[[], Awaitable[_T]]) -> _T | JSONResponse:
    try:
        return await call()
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())


def _window(raw: str | None) -> metrics.Window:
    try:
        return metrics.parse_window(raw)
    except ValueError as exc:
        raise CockpitError(400, "invalid_window", str(exc), field="window") from None


def _window_view(window: metrics.Window, raw: str | None) -> dict[str, Any]:
    return {"since": window.since, "until": window.until, "label": raw or metrics.DEFAULT_WINDOW}


async def _hidden(app_state: Any, user_id: str | None, root: Any) -> frozenset[str]:
    """Projects whose PL *user_id* cannot read (left out of every number)."""
    deps = factory_deps(app_state)
    raw = await get_snapshot(app_state, root)
    readable = await readable_for(user_id, raw, deps)
    return frozenset(slug for slug, pl in raw.project_pl.items() if pl and pl not in readable)


def _delta(result: dict[str, Any]) -> dict[str, Any]:
    now = metrics.headline(result)
    before = metrics.headline(result.get("previous") or {})
    result["delta"] = round(now - before, 4) if now is not None and before is not None else None
    return result


def _issue_row(issue: Any) -> dict[str, Any]:
    out = issue.to_dict()
    out.pop("workspace_id", None)
    out.pop("dedupe_key", None)
    return out


def _people(deps: FactoryDeps, factory_id: str, window: metrics.Window) -> dict[str, Any]:
    """Per-person numbers (owners only): decisions answered and their latency, posts, levers."""
    facts = metrics.load_facts(deps, factory_id, window)
    rows: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"decisions_answered": 0, "latencies": [], "chat_posts": 0, "interventions": 0}
    )
    for d in facts.decisions:
        if d.answered_by and d.answered_at and window.holds(d.answered_at):
            if d.answered_by in ("server", "eval-auto"):
                continue
            rows[d.answered_by]["decisions_answered"] += 1
            rows[d.answered_by]["latencies"].append(d.answered_at - d.created_at)
    for m in facts.chat:
        if m.author_kind == "human" and window.holds(m.created_at):
            rows[m.author]["chat_posts"] += 1
    for e in facts.kind("control.*", "pr.human_review"):
        by = (e.data or {}).get("by")
        if by and window.holds(e.ts / 1000) and (e.data or {}).get("by_kind", "human") == "human":
            rows[str(by)]["interventions"] += 1
    escalated: dict[str, int] = defaultdict(int)
    for e in facts.kind("decision.escalated"):
        source = (e.data or {}).get("from")
        if source and window.holds(e.ts / 1000):
            escalated[str(source)] += 1
    out = {}
    for person, row in sorted(rows.items()):
        lat = sorted(row.pop("latencies"))
        row["decision_p50_s"] = lat[len(lat) // 2] if lat else None
        row["escalated_away"] = escalated.get(person, 0)
        out[person] = row
    return out


def _insights(
    deps: FactoryDeps,
    root: Any,
    window: metrics.Window,
    filters: metrics.Filters,
    owner: bool,
) -> dict[str, Any]:
    kpis = metrics.compute(
        deps, root.id, list(metrics.KPIS), window=window, filters=filters, previous=True
    )
    store = deps.factory_store
    hidden = filters.hidden_projects
    visible = [
        i
        for i in store.list_issues(root.id, statuses=["open", "acked"], limit=500)
        if not (i.project_slug and i.project_slug in hidden)
    ]
    findings = sorted(
        (i for i in visible if i.class_ == "finding"), key=lambda i: (-i.count, -i.last_seen)
    )
    incidents = [i for i in visible if i.class_ == "incident"]
    retros: dict[str, Any] = {}
    for issue in store.list_issues(root.id, class_="retro", limit=200):
        key = issue.project_slug or ""
        if key in hidden or key in retros:
            continue
        retros[key] = _issue_row(issue)
    proposals = store.list_proposals(root.id, kind=["tuning", "rules"], limit=200)
    body: dict[str, Any] = {
        "def_version": definition.pinned_version(deps, root.id),
        "kpis": {name: _delta(value) for name, value in kpis.items()},
        "findings": [_issue_row(i) for i in findings[:FINDINGS_MAX]],
        "findings_open": len(findings),
        "incidents_open": len(incidents),
        "incidents": [_issue_row(i) for i in incidents[:3]],
        "retros": list(retros.values()),
        "experiments": {
            "on_trial": sum(1 for p in proposals if p.status == "trial"),
            "kept": sum(1 for p in proposals if p.status == "kept"),
            "reverted": sum(1 for p in proposals if p.status == "reverted"),
        },
        "catalog": [
            {
                "name": m.name,
                "family": m.family,
                "title": m.title,
                "unit": m.unit,
                "definition": m.definition,
            }
            for m in metrics.CATALOG.values()
        ],
    }
    if owner:
        body["people"] = _people(deps, root.id, window)
    return body


@router.get("/factories/{factory_id}/insights", response_model=None)
async def get_insights(
    request: Request,
    factory_id: str,
    window: str | None = Query(None),
    project: str | None = Query(None),
    version: str | None = Query(None),
    def_version: int | None = Query(None, ge=1),
) -> dict[str, Any] | JSONResponse:
    """The KPI block (with n and deltas), findings, incidents, retros, experiments."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)

    async def run() -> dict[str, Any]:
        root = await control.load_factory_root(deps, user_id, factory_id)
        win = _window(window)
        hidden = await _hidden(request.app.state, user_id, root)
        if project and project in hidden:
            raise CockpitError(404, "unknown_project", f"no project {project!r}")
        level = await asyncio.to_thread(people.viewer_level, deps, root.id, user_id)
        filters = metrics.Filters(project, version, def_version, hidden)
        body = await asyncio.to_thread(_insights, deps, root, win, filters, level >= LEVEL_MANAGE)
        return {"window": _window_view(win, window), **body}

    return await _guard(run)


@router.get("/factories/{factory_id}/metrics", response_model=None)
async def get_metrics(
    request: Request,
    factory_id: str,
    names: str | None = Query(None, description="Comma-separated catalog names; all when empty."),
    group_by: str | None = Query(None),
    window: str | None = Query(None),
    project: str | None = Query(None),
    version: str | None = Query(None),
    def_version: int | None = Query(None, ge=1),
) -> dict[str, Any] | JSONResponse:
    """Metric values (and series when grouped)."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)

    async def run() -> dict[str, Any]:
        root = await control.load_factory_root(deps, user_id, factory_id)
        wanted = [n.strip() for n in (names or "").split(",") if n.strip()] or None
        unknown = [n for n in wanted or [] if n not in metrics.CATALOG]
        if unknown:
            raise CockpitError(
                400, "unknown_metric", f"unknown: {', '.join(unknown)}", field="names"
            )
        if group_by is not None and group_by not in metrics.GROUP_KEYS:
            raise CockpitError(
                400, "invalid_group_by", f"group_by is one of {', '.join(metrics.GROUP_KEYS)}"
            )
        if group_by in metrics.OWNER_ONLY_GROUPS:
            level = await asyncio.to_thread(people.viewer_level, deps, root.id, user_id)
            if level < LEVEL_MANAGE:
                raise CockpitError(403, "owners_only", "per-person numbers are for owners")
        win = _window(window)
        hidden = await _hidden(request.app.state, user_id, root)
        filters = metrics.Filters(project, version, def_version, hidden)
        values = await asyncio.to_thread(
            lambda: metrics.compute(
                deps, root.id, wanted, window=win, group_by=group_by, filters=filters
            )
        )
        return {"window": _window_view(win, window), "group_by": group_by, "metrics": values}

    return await _guard(run)


@router.get("/insights", response_model=None)
async def get_fleet_insights(
    request: Request, window: str | None = Query(None)
) -> dict[str, Any] | JSONResponse:
    """The headline KPIs of every factory the caller can read."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)

    async def run() -> dict[str, Any]:
        win = _window(window)
        roots = await asyncio.to_thread(list_factory_roots, deps.conversation_store)
        readable = await asyncio.to_thread(readable_set, user_id, [r.id for r in roots], deps)
        rows = []
        for root in roots:
            if root.id not in readable:
                continue
            hidden = await _hidden(request.app.state, user_id, root)
            values = await asyncio.to_thread(
                functools.partial(
                    metrics.compute,
                    deps,
                    root.id,
                    list(FLEET_KPIS),
                    window=win,
                    filters=metrics.Filters(hidden_projects=hidden),
                )
            )
            rows.append(
                {
                    "factory_id": root.id,
                    "name": root.labels.get(LABEL_NAME) or display_title(root.title, root.id),
                    "kpis": values,
                }
            )
        return {"window": _window_view(win, window), "data": rows}

    return await _guard(run)


# ── Agents ─────────────────────────────────────────────────────


def _precedents(
    deps: FactoryDeps, factory_id: str, q: str | None, project: str | None
) -> list[dict[str, Any]]:
    needle = (q or "").strip().casefold()
    rows = deps.factory_store.list_decisions(status="answered", factory_id=factory_id, limit=500)
    if project is not None:
        rows = [r for r in rows if r.project_slug in (None, project)]
    out = []
    for d in sorted(rows, key=lambda r: r.answered_at or 0, reverse=True):
        option = next((o for o in d.options if o.get("id") == d.answer_option_id), None)
        answer = (option or {}).get("label") or d.answer_text or ""
        haystack = f"{d.question}\n{answer}\n{d.answer_text or ''}".casefold()
        if needle and needle not in haystack:
            continue
        out.append(
            {
                "decision": d.ref,
                "question": control.clip(d.question, 300),
                "answer": control.clip(str(answer), 300),
                "answered_by": d.answered_by,
                "answered_at": d.answered_at,
                "project": d.project_slug,
            }
        )
        if len(out) >= PRECEDENTS_MAX:
            break
    return out


def _agent_findings(deps: FactoryDeps, factory_id: str, project: str | None) -> dict[str, Any]:
    store = deps.factory_store
    rows = store.list_issues(
        factory_id, class_=["finding", "retro"], statuses=["open", "acked"], limit=200
    )
    if project is not None:
        rows = [r for r in rows if r.project_slug in (None, project)]
    calibration = [
        {"issue": f.target_id, "rating": f.rating, "tags": f.tags, "note": f.body}
        for f in store.list_feedback(factory_id, kinds=["calibration"], limit=20)
    ]
    return {
        "findings": [_issue_row(r) for r in rows if r.class_ == "finding"][:20],
        "retros": [_issue_row(r) for r in rows if r.class_ == "retro"][:10],
        "calibration": calibration,
    }


def _agent_loops(deps: FactoryDeps, factory_id: str, project: str | None) -> list[dict[str, Any]]:
    rows = deps.factory_store.list_loops(
        factory_id, states=["running", "stalled", "thrashing", "exhausted", "escalated"]
    )
    return [
        {
            "kind": r.kind,
            "subject": r.subject,
            "project": r.project_slug,
            "state": r.state,
            "iteration": r.iteration,
            "spec": r.spec,
            "last_reason": r.last_reason,
        }
        for r in rows
        if project is None or r.project_slug == project
    ][:50]


@router.get("/agent/insights", response_model=None)
async def get_agent_insights(
    request: Request,
    from_session_id: str = Query(...),
    kind: str = Query("kpis"),
    window: str | None = Query(None),
    names: str | None = Query(None),
    group_by: str | None = Query(None),
    q: str | None = Query(None, max_length=200),
) -> dict[str, Any] | JSONResponse:
    """``sys_factory_insights``: read-only numbers, findings, precedents and loops."""

    async def run() -> dict[str, Any]:
        caller = await authorize_agent(request, from_session_id)
        role = caller.membership.role
        if role not in AGENT_ROLES:
            raise CockpitError(
                403, "not_allowed", "leads, project leads and the planner read insights"
            )
        project = caller.membership.project if role == ROLE_PROJECT_LEAD else None
        deps, fid = caller.deps, caller.factory_id
        win = _window(window)
        base = {"kind": kind, "window": _window_view(win, window), "project": project}
        if kind == "kpis":
            factory = await asyncio.to_thread(
                lambda: metrics.compute(deps, fid, list(metrics.KPIS), window=win, previous=True)
            )
            out = {**base, "kpis": {n: _delta(v) for n, v in factory.items()}}
            if project:
                own = await asyncio.to_thread(
                    lambda: metrics.compute(
                        deps, fid, list(metrics.KPIS), window=win, filters=metrics.Filters(project)
                    )
                )
                out["project_kpis"] = own
            return out
        if kind == "metrics":
            wanted = [n.strip() for n in (names or "").split(",") if n.strip()] or None
            if group_by in metrics.OWNER_ONLY_GROUPS or (
                group_by is not None and group_by not in metrics.GROUP_KEYS
            ):
                raise CockpitError(
                    400, "invalid_group_by", "group_by day|project|role|agent|version|def_version"
                )
            try:
                values = await asyncio.to_thread(
                    lambda: metrics.compute(
                        deps,
                        fid,
                        wanted,
                        window=win,
                        group_by=group_by,
                        filters=metrics.Filters(project),
                    )
                )
            except KeyError as exc:
                raise CockpitError(400, "unknown_metric", f"unknown metric {exc}") from None
            return {**base, "metrics": values}
        if kind == "findings":
            return {**base, **await asyncio.to_thread(_agent_findings, deps, fid, project)}
        if kind == "precedents":
            found = await asyncio.to_thread(_precedents, deps, fid, q, project)
            return {**base, "precedents": found}
        if kind == "loops":
            return {**base, "loops": await asyncio.to_thread(_agent_loops, deps, fid, project)}
        raise CockpitError(
            400, "invalid_kind", "kind is kpis, metrics, findings, precedents or loops"
        )

    return await _guard(run)


@router.post("/agent/retros", response_model=None)
async def post_agent_retro(
    request: Request, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """``sys_factory_retro``: file a retro or a finding (with evidence)."""

    async def run() -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise CockpitError(400, "invalid_body", "the body must be a JSON object")
        body = dict(payload)
        sender = body.pop("from_session_id", None)
        if not isinstance(sender, str) or not sender:
            raise CockpitError(400, "invalid_body", "from_session_id is required")
        caller = await authorize_agent(request, sender)
        return await retro.file_retro(request.app.state, caller, body)

    return await _guard(run)


__all__ = ["router"]
