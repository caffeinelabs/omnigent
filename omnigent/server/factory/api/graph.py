"""``GET /v1/factory/factories/{fid}/graph?kind=work|bus|flow|org&project=&window=``.

READ on the factory root; projects whose PL the viewer cannot read are left
out, with their tasks, members, messages and spans.

- ``work``: tasks per project with their waves, critical path and ready set,
  task edges (``dep`` and the implicit ``serial`` ones), edges to the
  projects and decisions tasks wait on, project ``depends_on`` edges and
  scope warnings.
- ``bus`` (default window 1 h): message edges aggregated from
  ``message.*`` events (count, kinds, p50 reply latency, bounces,
  unanswered requests), spawn and supervise edges, ``waits_on`` edges from
  open decisions and blocked members to people, and supervisor ``alert`` edges.
- ``flow`` (default window 7 d): the PR pipeline ``plan → … → merged`` with
  counts now, p50 dwell, dollars and loop back-edges from spans.
- ``org``: members and sub-agents at any depth, spawn and supervise edges,
  grouped by project and health.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import Counter
from collections.abc import Mapping
from typing import Any, Final

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from omnigent.entities import Conversation
from omnigent.factory import (
    LABEL_HEALTH,
    LABEL_PARENT,
    LABEL_PROJECT,
    LABEL_ROLE,
    LABEL_STATE,
    LABEL_SUPERVISOR,
)
from omnigent.server.factory import control, graph, tasks
from omnigent.server.factory.charter import project_depends_on
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.envelope import display_title
from omnigent.server.factory.gating import unmet_dependencies
from omnigent.server.factory.graph import MailFact, SpanFact
from omnigent.server.factory.membership import factory_members, resolve_role
from omnigent.server.factory.snapshot import RawSnapshot, get_snapshot, readable_for
from omnigent.server.routes._auth_helpers import get_user_id
from omnigent.util.session_lifecycle import is_session_closed

router = APIRouter(prefix="/v1/factory", tags=["factory"])

KINDS: Final = ("work", "bus", "flow", "org")
DEFAULT_WINDOWS: Final[dict[str, int]] = {"bus": 3600, "flow": 7 * 86400}
MAX_WINDOW_S: Final = 90 * 86400
ORG_MAX_NODES: Final = 2000
_CHILD_PAGE: Final = 200
_WINDOW_RE: Final = re.compile(r"^(\d{1,7})([smhd]?)$")
_UNITS: Final[dict[str, int]] = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
_FLOW_SPANS: Final = ["stage", "gate", "pr_round", "loop_iter", "task", "ready_wait"]
_GATE_ORDER: Final = (("checks", "build"), ("review", "review"), ("verify", "verify"))


def parse_window(raw: str | None, kind: str) -> int:
    """
    A window in seconds.

    :param raw: e.g. ``"1h"``, ``"30m"``, ``"7d"`` or ``"3600"``; ``None`` for the kind's default.
    :raises CockpitError: 400 ``invalid_window``.
    """
    if raw is None or not raw.strip():
        return DEFAULT_WINDOWS.get(kind, 3600)
    match = _WINDOW_RE.match(raw.strip().lower())
    if match is None:
        raise CockpitError(400, "invalid_window", "window is like 30m, 1h, 24h or 7d")
    seconds = int(match.group(1)) * _UNITS[match.group(2)]
    if not 0 < seconds <= MAX_WINDOW_S:
        raise CockpitError(400, "invalid_window", "window is between 1s and 90d")
    return seconds


# ── Work ───────────────────────────────────────────────────────


def _depends_on(project: Any) -> list[str]:
    return project_depends_on(project)[0] if project is not None else []


def work_graph(
    deps: FactoryDeps, factory_id: str, slug: str | None, hidden: set[str]
) -> dict[str, Any]:
    """The work graph of a factory (or one project), without hidden projects (synchronous)."""
    data = tasks.load_factory(deps, factory_id, slug)
    slugs = [slug] if slug else sorted(set(data.projects) | {t.project_slug for t in data.tasks})
    prs = data.prs or None
    out: dict[str, Any] = {
        "projects": [],
        "tasks": [],
        "layers": {},
        "edges": [],
        "external_edges": [],
        "project_edges": [],
        "warnings": [],
    }
    for name in slugs:
        if name in hidden:
            continue
        ctx = data.project_ctx(deps, name)
        analysis = tasks.analyze(ctx)
        project = ctx.project
        by_key = ctx.by_key()
        out["projects"].append(
            {
                "slug": name,
                "title": project.title if project is not None else name,
                "status": project.status if project is not None else None,
                "stage": project.stage if project is not None else None,
                "depends_on": _depends_on(project),
                **tasks.graph_view(ctx, analysis),
                "summary": tasks.summary(ctx, analysis),
            }
        )
        out["tasks"] += [tasks.task_view(t, analysis=analysis, prs=ctx.prs) for t in ctx.tasks]
        out["layers"].update({by_key[k].id: n for k, n in analysis.layers.items()})
        out["warnings"] += analysis.warnings
        for task in ctx.tasks:
            for dep in task.deps or []:
                if graph.is_external(dep):
                    edge: dict[str, Any] = {
                        "from": dep,
                        "to": task.id,
                        "kind": "project" if dep.startswith("project:") else "decision",
                        "met": ctx.external_met.get(dep, False),
                    }
                    decision = data.decisions.get(dep.removeprefix("decision:"))
                    if decision is not None:
                        edge["assignee"] = decision.assignee
                        edge["status"] = decision.status
                    out["external_edges"].append(edge)
                elif dep in by_key:
                    other = by_key[dep]
                    out["edges"].append(
                        {
                            "from": other.id,
                            "to": task.id,
                            "kind": "dep",
                            "met": other.state == "done",
                        }
                    )
        for earlier, later in analysis.plan.serial:
            out["edges"].append(
                {
                    "from": by_key[earlier].id,
                    "to": by_key[later].id,
                    "kind": "serial",
                    "met": False,
                }
            )
        if project is not None and _depends_on(project):
            prs = prs if prs is not None else deps.factory_store.list_prs(factory_id)
            unmet = set(unmet_dependencies(project, data.projects, prs))
            for dep in _depends_on(project):
                if dep not in hidden:
                    out["project_edges"].append(
                        {"from": dep, "to": name, "kind": "depends_on", "met": dep not in unmet}
                    )
    return out


# ── Bus ────────────────────────────────────────────────────────


def _visible(node: str | None, readable: set[str]) -> bool:
    return node is not None and (
        node == graph.SERVER_NODE or node.startswith("human:") or node in readable
    )


def bus_graph(
    deps: FactoryDeps, factory_id: str, raw: RawSnapshot, readable: set[str], window_s: int
) -> dict[str, Any]:
    """The communication graph over the last *window_s* seconds (synchronous)."""
    store = deps.factory_store
    now_ms = int(time.time() * 1000)
    since = now_ms - window_s * 1000
    mail = store.find_events(
        factory_id, ["message.queued", "message.delivered", "message.bounced"], since, limit=5000
    )
    facts = [
        MailFact(
            ts=e.ts,
            event=e.kind,
            sender=e.from_id,
            target=e.to_id,
            kind=(e.data or {}).get("kind"),
            mail_id=(e.data or {}).get("mail_id"),
        )
        for e in mail
    ]
    edges = [
        e
        for e in graph.bus_edges(facts)
        if _visible(e["from"], readable) and _visible(e["to"], readable)
    ]
    members = [m for m in raw.body.get("members", []) if m["session_id"] in readable]
    tops = {c.id: c for c in factory_members(factory_id, deps.conversation_store)}
    for member in members:
        parent = member.get("parent_session_id")
        if parent and parent in readable:
            edges.append({"from": parent, "to": member["session_id"], "kind": "spawn"})
        top = tops.get(member["session_id"])
        supervisor = top.labels.get(LABEL_SUPERVISOR) if top is not None else None
        if supervisor and supervisor in readable and supervisor != member["session_id"]:
            edges.append({"from": supervisor, "to": member["session_id"], "kind": "supervise"})
        if member.get("health") == "blocked":
            edges.append(
                {
                    "from": member["session_id"],
                    "to": "human:owners",
                    "kind": "waits_on",
                    "label": "blocked",
                }
            )
    for decision in raw.body.get("decisions", []):
        asker = decision.get("asked_by_session_id")
        if decision.get("status") != "open" or asker not in readable:
            continue
        who = decision.get("assignee") or "owners"
        edges.append(
            {
                "from": asker,
                "to": f"human:{who}",
                "kind": "waits_on",
                "label": decision.get("ref"),
                "blocking": decision.get("blocking"),
                "since": decision.get("created_at"),
            }
        )
    alerts: Counter[tuple[str, str]] = Counter()
    for event in store.find_events(factory_id, "alert.*", since, limit=2000):
        if _visible(event.from_id, readable) and _visible(event.to_id, readable):
            alerts[(str(event.from_id), str(event.to_id))] += 1
    edges += [
        {"from": a, "to": b, "kind": "alert", "count": n} for (a, b), n in sorted(alerts.items())
    ]
    by_id = {m["session_id"]: m for m in members}
    ids = {e["from"] for e in edges} | {e["to"] for e in edges} | set(by_id)
    nodes = []
    for node in sorted(ids):
        member = by_id.get(node)
        if member is not None:
            nodes.append(
                {
                    "id": node,
                    "kind": "agent",
                    "title": member["title"],
                    "role": member["role"],
                    "project": member["project"],
                    "health": member["health"],
                }
            )
        elif node.startswith("human:"):
            nodes.append({"id": node, "kind": "human", "title": node.removeprefix("human:")})
        elif node == graph.SERVER_NODE:
            nodes.append({"id": node, "kind": "server", "title": "server"})
        else:
            nodes.append({"id": node, "kind": "agent", "title": raw.titles.get(node, node)})
    return {"since_ms": since, "until_ms": now_ms, "nodes": nodes, "edges": edges}


# ── Flow ───────────────────────────────────────────────────────


_STAGE_NOW: Final = {"planning": "plan", "building": "build"}


def _now_counts(
    body: Mapping[str, Any], slug: str | None, since_s: int, hidden: set[str]
) -> dict[str, int]:
    """Where visible projects (plan, build) and their open PRs (review … ready) sit now."""
    counts: Counter[str] = Counter()
    for project in body.get("projects", []):
        if (slug and project["slug"] != slug) or project["slug"] in hidden:
            continue
        node = _STAGE_NOW.get(project.get("stage") or "")
        if node:
            counts[node] += 1
    for pr in body.get("prs", []):
        if (slug and pr.get("project_slug") != slug) or pr.get("project_slug") in hidden:
            continue
        if pr.get("state") == "merged" and ((pr.get("merged") or {}).get("at") or 0) >= since_s:
            counts["merged"] += 1
        if pr.get("state") != "open":
            continue
        gates = pr.get("gates") or {}
        if pr.get("ready"):
            counts["ready"] += 1
            continue
        node = next((n for g, n in _GATE_ORDER if gates.get(g) not in ("pass", "skip")), None)
        if node is None and gates.get("demo") not in ("pass", "skip", None):
            node = "demo"
        counts[node or "ready"] += 1
    return dict(counts)


def flow_graph(
    deps: FactoryDeps,
    factory_id: str,
    slug: str | None,
    hidden: set[str],
    body: Mapping[str, Any],
    window_s: int,
) -> dict[str, Any]:
    """The aggregated PR pipeline over the last *window_s* seconds (synchronous)."""
    now = time.time()
    since_ms = int((now - window_s) * 1000)
    spans = deps.factory_store.list_spans(
        factory_id, kinds=_FLOW_SPANS, since_ms=since_ms, project_slug=slug, limit=10000
    )
    facts = [
        SpanFact(
            kind=s.kind,
            name=s.name,
            started_at=s.started_at,
            ended_at=s.ended_at,
            outcome=s.outcome,
            project_slug=s.project_slug,
            pr_id=s.pr_id,
            role=s.role,
            cost_usd=s.cost_usd,
            attrs=s.attrs or {},
        )
        for s in spans
        if s.project_slug not in hidden
    ]
    counts = _now_counts(body, slug, int(now - window_s), hidden)
    return {"since_ms": since_ms, **graph.flow_graph(facts, now_counts=counts)}


# ── Org ────────────────────────────────────────────────────────


def org_graph(deps: FactoryDeps, factory_id: str, readable: set[str]) -> dict[str, Any]:
    """Every member and sub-agent (any depth) the viewer may read (synchronous)."""
    from omnigent.server.routes._sessions.helpers import _session_status_from_cache

    store = deps.conversation_store
    rows: list[dict[str, Any]] = []
    memo: dict[tuple[Any, ...], str | None] = {}

    def role_of(conv: Conversation) -> str | None:
        key = (conv.agent_id, conv.sub_agent_name, conv.parent_conversation_id is None)
        if conv.parent_conversation_id is None:
            return conv.labels.get(LABEL_ROLE)
        if key not in memo:
            memo[key] = resolve_role(conv, deps) or conv.sub_agent_name
        return memo[key]

    def row(conv: Conversation, top: Conversation, depth: int) -> dict[str, Any]:
        is_top = conv.parent_conversation_id is None
        labels = conv.labels
        return {
            "id": conv.id,
            "title": display_title(conv.title, conv.id),
            "role": role_of(conv),
            "project": top.labels.get(LABEL_PROJECT),
            "parent": labels.get(LABEL_PARENT) if is_top else conv.parent_conversation_id,
            "supervisor": labels.get(LABEL_SUPERVISOR) if is_top else None,
            "health": labels.get(LABEL_HEALTH),
            "status": _session_status_from_cache(conv.id, conv.live_status),
            "state": labels.get(LABEL_STATE) if is_top else None,
            "is_top_level": is_top,
            "depth": depth,
        }

    def children_of(top: Conversation) -> dict[str | None, list[Conversation]]:
        # Every sub-agent under one member's spawn tree, in a few paged reads.
        found: dict[str | None, list[Conversation]] = {}
        after: str | None = None
        for _ in range(ORG_MAX_NODES // _CHILD_PAGE + 1):
            page = store.list_conversations(
                limit=_CHILD_PAGE,
                after=after,
                kind="sub_agent",
                root_conversation_id=top.root_conversation_id or top.id,
                order="asc",
            )
            for child in page.data:
                if not is_session_closed(child.labels, child.title):
                    found.setdefault(child.parent_conversation_id, []).append(child)
            if not page.has_more or not page.data:
                break
            after = page.data[-1].id
        return found

    tops = [
        m
        for m in factory_members(factory_id, store)
        if not is_session_closed(m.labels, m.title) and m.id in readable
    ]
    queue: list[tuple[Conversation, Conversation, int]] = [(m, m, 0) for m in tops]
    trees: dict[str, dict[str | None, list[Conversation]]] = {}
    while queue and len(rows) < ORG_MAX_NODES:
        conv, top, depth = queue.pop(0)
        rows.append(row(conv, top, depth))
        tree = trees.get(top.id)
        if tree is None:
            tree = trees[top.id] = children_of(top)
        queue += [(child, top, depth + 1) for child in tree.get(conv.id, [])]
    return {"truncated": bool(queue), **graph.org_graph(rows)}


# ── Route ──────────────────────────────────────────────────────


@router.get("/factories/{factory_id}/graph", response_model=None)
async def get_graph(
    request: Request,
    factory_id: str,
    kind: str = Query("work"),
    project: str | None = Query(None),
    window: str | None = Query(None),
) -> dict[str, Any] | JSONResponse:
    """One of the factory's graphs, as this viewer may see it."""
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)
    try:
        if kind not in KINDS:
            raise CockpitError(400, "invalid_kind", f"kind is one of {list(KINDS)}")
        window_s = parse_window(window, kind)
        root = await control.load_factory_root(deps, user_id, factory_id)
        raw = await get_snapshot(request.app.state, root)
        readable = await readable_for(user_id, raw, deps)
        hidden = {s for s, pl in raw.project_pl.items() if pl is not None and pl not in readable}
        slug = project.strip() if project else None
        if slug and slug in hidden:
            raise CockpitError(404, "unknown_project", f"no project {slug!r} in this factory")
        head: dict[str, Any] = {
            "kind": kind,
            "factory_id": raw.factory_id,
            "project": slug,
            "generated_at": int(time.time()),
        }
        if kind == "work":
            body = await asyncio.to_thread(work_graph, deps, root.id, slug, hidden)
        elif kind == "bus":
            body = await asyncio.to_thread(bus_graph, deps, root.id, raw, readable, window_s)
            head["window_s"] = window_s
        elif kind == "flow":
            body = await asyncio.to_thread(
                flow_graph, deps, root.id, slug, hidden, raw.body, window_s
            )
            head["window_s"] = window_s
        else:
            body = await asyncio.to_thread(org_graph, deps, root.id, readable)
        return {**head, **body}
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())


__all__ = ["bus_graph", "flow_graph", "org_graph", "parse_window", "router", "work_graph"]
