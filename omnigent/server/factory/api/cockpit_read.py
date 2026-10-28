"""Cockpit read routes: preflight, the factory list, one factory's snapshot,
its activity feed, its recorded demos (all, or one project's grouped by PR)
and the open decisions across factories.

Humans call these with their own credentials. Viewing a factory needs READ on
its root (anything else is a 404); inside it, members the viewer cannot read
are filtered out with their projects, events, decisions and demos.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Final

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

from omnigent.entities import Conversation
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.factory import LABEL_NAME, factory_enabled
from omnigent.server.auth import LEVEL_READ, RESERVED_USER_LOCAL
from omnigent.server.factory import decision_routing, people, schemas, tier2
from omnigent.server.factory.api.presence import entries_for
from omnigent.server.factory.charter import server_default_base
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.envelope import display_title
from omnigent.server.factory.membership import resolve_role
from omnigent.server.factory.routes import FactoryRouteError
from omnigent.server.factory.snapshot import (
    bare_id,
    decision_view,
    filter_snapshot,
    get_snapshot,
    is_factory_root,
    list_factory_roots,
    project_demo_view,
    readable_for,
    readable_set,
    snapshot_etag,
    summary_from,
)
from omnigent.server.factory.start import max_projects
from omnigent.server.routes._auth_helpers import require_access, require_user

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/factory", tags=["factory"])

EVENTS_MAX_LIMIT: Final = 500
DEMOS_MAX_LIMIT: Final = 200
_EVENT_SCAN_PAGES: Final = 5
_DECISION_STATUSES: Final = frozenset({"open", "answered", "withdrawn", "expired"})
_DELIVERY_LOOKUPS: Final = 50


def _not_found() -> FactoryRouteError:
    return FactoryRouteError(404, "not_found", "factory not found")


async def _root(user_id: str | None, factory_id: str, deps: FactoryDeps) -> Conversation:
    """The factory root *factory_id*, readable by *user_id*; anything else is a 404."""
    try:
        await require_access(
            user_id, factory_id, LEVEL_READ, deps.permission_store, deps.conversation_store
        )
    except OmnigentError as exc:
        if exc.code in (ErrorCode.NOT_FOUND, ErrorCode.FORBIDDEN):
            raise _not_found() from exc
        raise
    conv = await asyncio.to_thread(deps.conversation_store.get_conversation, factory_id)
    if conv is None or not is_factory_root(conv):
        raise _not_found()
    return conv


async def _view(
    request: Request, user_id: str | None, root: Conversation, *, viewer: bool = True
) -> dict[str, Any]:
    deps = factory_deps(request.app.state)
    raw = await get_snapshot(request.app.state, root)
    readable = await readable_for(user_id, raw, deps)
    body = filter_snapshot(raw, readable)
    if viewer:
        # Per viewer and never cached: role, levers, roster size and who is here.
        block, count = await asyncio.to_thread(_viewer_fields, deps, root.id, user_id)
        body["viewer"] = block
        body["people_count"] = count
        body["presence"] = entries_for(root.id, raw.top_level_of, readable)
    return body


def _viewer_fields(
    deps: FactoryDeps, factory_id: str, user_id: str | None
) -> tuple[dict[str, Any], int]:
    block = schemas.ViewerBlock.model_validate(people.viewer_block(deps, factory_id, user_id))
    return block.model_dump(mode="json"), people.people_count(deps, factory_id)


def _etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    if header.strip() == "*":
        return True
    wanted = etag.removeprefix("W/")
    return any(tag.strip().removeprefix("W/") == wanted for tag in header.split(","))


def _factory_name(root: Conversation) -> str:
    return root.labels.get(LABEL_NAME) or display_title(root.title, root.id)


@router.get("/preflight")
async def get_preflight(request: Request) -> dict[str, Any]:
    """Can this user start a factory here (GitHub, agents, host, flag), and its defaults."""
    deps = factory_deps(request.app.state)
    user_id = require_user(request, deps.auth_provider)
    state = request.app.state
    connected, login = False, None
    github_store = getattr(state, "github_store", None)
    if github_store is not None:
        try:
            connection = await asyncio.to_thread(
                github_store.get, user_id if user_id is not None else RESERVED_USER_LOCAL
            )
        except Exception:  # noqa: BLE001 - shown as not connected
            _logger.warning(
                "factory preflight: could not read the GitHub connection", exc_info=True
            )
            connection = None
        if connection is not None:
            connected, login = True, connection.github_login or None

    def _agent(name: str) -> bool:
        try:
            return deps.agent_store.get_by_name(name) is not None
        except Exception:  # noqa: BLE001 - shown as missing
            return False

    lead, pl = await asyncio.to_thread(lambda: (_agent("factory-lead"), _agent("factory-pl")))
    default_repo, default_base = server_default_base()
    return schemas.Preflight(
        github=schemas.PreflightGithub(connected=connected, login=login),
        agents=schemas.PreflightAgents(factory_lead=lead, factory_pl=pl),
        sandbox_host=getattr(state, "sandbox_config", None) is not None,
        factory_enabled=factory_enabled(),
        default_repo=default_repo,
        default_base=default_base or ("main" if default_repo else None),
        max_projects=max_projects(),
        tier2=schemas.Tier2Status.model_validate(tier2.status(state)),
    ).model_dump(mode="json")


@router.get("/factories")
async def list_factories(request: Request) -> dict[str, Any]:
    """Every factory the caller can read, newest first."""
    deps = factory_deps(request.app.state)
    user_id = require_user(request, deps.auth_provider)
    roots = await asyncio.to_thread(list_factory_roots, deps.conversation_store)
    readable = await asyncio.to_thread(readable_set, user_id, [r.id for r in roots], deps)
    summaries = []
    for root in roots:
        if root.id not in readable:
            continue
        try:
            body = await _view(request, user_id, root, viewer=False)
            extras = await asyncio.to_thread(_summary_extras, deps, root.id, user_id, body)
            summaries.append({**summary_from(body), **extras})
        except Exception:  # one broken factory must not hide the others
            _logger.exception("factory: could not summarise factory %s", root.id)
    summaries.sort(key=lambda s: s["started_at"] or 0, reverse=True)
    return schemas.FactoryListResponse.model_validate({"data": summaries}).model_dump(mode="json")


def _summary_extras(
    deps: FactoryDeps, factory_id: str, user_id: str | None, body: dict[str, Any]
) -> dict[str, Any]:
    """The index card's per-viewer fields: role, who shared it, roster size, mine, $/h."""
    level = people.viewer_level(deps, factory_id, user_id)
    creator = people.creator_of(deps, factory_id) if deps.permission_store is not None else None
    is_creator = user_id is None or creator == user_id
    open_decisions = [d for d in body["decisions"] if d["status"] == "open"]
    return {
        "viewer_role": people.level_role(level, is_creator=is_creator),
        "shared_by": None if is_creator else creator,
        "people_count": people.people_count(deps, factory_id),
        "mine": sum(
            1
            for d in open_decisions
            if decision_routing.is_mine_by(d.get("assignee"), d.get("claimed_by"), user_id, level)
        ),
        "usd_per_h": body["totals"].get("usd_per_h"),
    }


@router.get("/factories/{factory_id}")
async def get_factory(request: Request, factory_id: str) -> Response:
    """The factory's snapshot as this viewer may see it (weak ETag; 304 on a match)."""
    deps = factory_deps(request.app.state)
    user_id = require_user(request, deps.auth_provider)
    root = await _root(user_id, factory_id, deps)
    body = await _view(request, user_id, root)
    etag = snapshot_etag(body)
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return JSONResponse(content=body, headers=headers)


def _event_view(event: Any, titles: dict[str, str], readable: set[str]) -> dict[str, Any]:
    data = dict(event.data) if event.data else None
    if data is not None:
        # The full enveloped text is kept for mail replay; the feed shows the excerpt.
        data.pop("text", None)
        counts = data.get("counts")
        if isinstance(counts, dict):
            # Per-member tallies (heartbeat.skipped) name only members the viewer may read.
            data["counts"] = {sid: n for sid, n in counts.items() if sid in readable}
        elif isinstance(counts, list):
            # Per-person numbers (metrics.prompts) are for owners, through the metrics API.
            data["counts"] = [
                {k: v for k, v in row.items() if k != "person"}
                for row in counts
                if isinstance(row, dict)
            ]
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


@router.get("/factories/{factory_id}/events")
async def list_factory_events(
    request: Request,
    factory_id: str,
    before: int | None = Query(None, description="Only events before this epoch ms."),
    before_id: str | None = Query(None, description="Tie-breaker for events in the same ms."),
    limit: int = Query(100, ge=1, le=EVENTS_MAX_LIMIT),
    project: str | None = Query(None),
    kinds: str | None = Query(None, description="Comma-separated kinds or prefix.* patterns."),
    member: str | None = Query(None, description="Only events from or to this session."),
) -> dict[str, Any]:
    """A page of the factory's activity, newest first, filtered to what the viewer may read."""
    deps = factory_deps(request.app.state)
    user_id = require_user(request, deps.auth_provider)
    root = await _root(user_id, factory_id, deps)
    raw = await get_snapshot(request.app.state, root)
    readable = await readable_for(user_id, raw, deps)
    hidden_projects = {
        slug for slug, pl in raw.project_pl.items() if pl is not None and pl not in readable
    }
    kind_list = [k.strip() for k in (kinds or "").split(",") if k.strip()] or None
    outsiders: dict[str, bool] = {}

    async def visible(event: Any) -> bool:
        if event.project_slug in hidden_projects:
            return False
        for sid in (event.from_id, event.to_id):
            if sid is None or sid in readable:
                continue
            if sid in raw.top_level_of:
                return False
            if sid not in outsiders:
                outsiders[sid] = bool(await asyncio.to_thread(readable_set, user_id, [sid], deps))
            if not outsiders[sid]:
                return False
        return True

    store = deps.factory_store
    kept: list[Any] = []
    cursor_ms, cursor_id = before, before_id
    next_cursor: tuple[int, str] | None = None
    scanned: tuple[int, str] | None = None
    for _ in range(_EVENT_SCAN_PAGES):
        page = await asyncio.to_thread(
            store.list_events,
            root.id,
            cursor_ms,
            limit + 1,
            kind_list,
            project,
            [member] if member else None,
            before_id=cursor_id,
        )
        for event in page:
            if await visible(event):
                kept.append(event)
        if len(kept) > limit:
            next_cursor = (kept[limit - 1].ts, kept[limit - 1].id)
            break
        if len(page) <= limit:
            break
        cursor_ms, cursor_id = page[-1].ts, page[-1].id
        scanned = (cursor_ms, cursor_id)
    else:
        # The scan stopped on hidden events: resume after the last one scanned.
        next_cursor = scanned
    data = [_event_view(e, raw.titles, readable) for e in kept[:limit]]
    return schemas.EventPage.model_validate(
        {
            "data": data,
            "has_more": next_cursor is not None,
            "next_before": next_cursor[0] if next_cursor else None,
            "next_before_id": next_cursor[1] if next_cursor else None,
        }
    ).model_dump(mode="json")


@router.get("/factories/{factory_id}/demos")
async def list_factory_demos(
    request: Request,
    factory_id: str,
    project: str | None = Query(None, description="Only this project's demos."),
    pr: int | None = Query(None, ge=1, description="Only demos of this PR number."),
    limit: int = Query(50, ge=1, le=DEMOS_MAX_LIMIT),
) -> dict[str, Any]:
    """The factory's recorded demos, newest first, filtered to what the viewer may read.

    `limit` caps the rows read before that filter, so a viewer who can't see
    some projects may get fewer than `limit` even when more exist.
    """
    deps = factory_deps(request.app.state)
    user_id = require_user(request, deps.auth_provider)
    root = await _root(user_id, factory_id, deps)
    raw = await get_snapshot(request.app.state, root)
    readable = await readable_for(user_id, raw, deps)
    hidden_projects = {
        slug for slug, pl in raw.project_pl.items() if pl is not None and pl not in readable
    }
    rows = await asyncio.to_thread(
        lambda: deps.factory_store.list_demos(root.id, pr, project_slug=project, limit=limit)
    )
    # A demo's files belong to the PL that published it (a closed one is not a member).
    owners = {d.session_id for d in rows if d.session_id not in readable}
    readable_owners = (
        await asyncio.to_thread(readable_set, user_id, owners, deps) if owners else set()
    )
    heads = {(p["repo"], p["number"]): p["head_sha"] for p in raw.body["prs"]}
    data: list[dict[str, Any]] = []
    for demo in rows:
        if demo.project_slug in hidden_projects:
            continue
        if demo.session_id not in readable and demo.session_id not in readable_owners:
            continue
        head = heads.get((demo.repo, demo.pr_number), "")
        view = project_demo_view(demo, head, raw.titles)
        if view is not None:
            data.append(view)
    return schemas.DemoListResponse.model_validate({"data": data}).model_dump(mode="json")


@router.get("/factories/{factory_id}/projects/{slug}/demos")
async def list_project_demos(
    request: Request,
    factory_id: str,
    slug: str,
    limit: int = Query(DEMOS_MAX_LIMIT, ge=1, le=DEMOS_MAX_LIMIT),
) -> dict[str, Any]:
    """Every recorded demo of one project's PRs, grouped by PR, newest first.

    Groups are ordered by their newest run and each group lists every run
    (newest first) with its files, steps, status, head sha and time. A project
    whose PL the viewer cannot read is a 404, like an unknown slug.
    """
    deps = factory_deps(request.app.state)
    user_id = require_user(request, deps.auth_provider)
    root = await _root(user_id, factory_id, deps)
    raw = await get_snapshot(request.app.state, root)
    readable = await readable_for(user_id, raw, deps)
    pl = raw.project_pl.get(slug, "")
    if slug not in raw.project_pl or (pl is not None and pl not in readable):
        raise FactoryRouteError(404, "project_not_found", f"no project {slug!r} in this factory")
    project_prs = {
        (p["repo"], p["number"]): p for p in raw.body["prs"] if p.get("project_slug") == slug
    }
    rows = await asyncio.to_thread(_project_demo_rows, deps, root.id, slug, project_prs, limit)
    owners = {d.session_id for d in rows if d.session_id not in readable}
    readable_owners = (
        await asyncio.to_thread(readable_set, user_id, owners, deps) if owners else set()
    )
    groups: dict[tuple[str, int], dict[str, Any]] = {}
    count = 0
    for demo in rows:
        if demo.session_id not in readable and demo.session_id not in readable_owners:
            continue
        pr = project_prs.get((demo.repo, demo.pr_number))
        head = pr["head_sha"] if pr else ""
        view = project_demo_view(demo, head, raw.titles)
        if view is None:
            continue
        key = (demo.repo, demo.pr_number)
        if key not in groups:
            groups[key] = {
                "repo": demo.repo,
                "pr_number": demo.pr_number,
                "pr_id": pr["id"] if pr else demo.pr_id,
                "head_sha": head,
                "runs": [],
            }
        groups[key]["runs"].append(view)
        count += 1
    return schemas.ProjectDemosResponse.model_validate(
        {"data": list(groups.values()), "count": count}
    ).model_dump(mode="json")


def _project_demo_rows(
    deps: FactoryDeps,
    factory_id: str,
    slug: str,
    project_prs: dict[tuple[str, int], dict[str, Any]],
    limit: int,
) -> list[Any]:
    """The project's demos plus unlabelled demos of its PRs, newest first, at most *limit*."""
    store = deps.factory_store
    rows = {d.id: d for d in store.list_demos(factory_id, project_slug=slug, limit=limit)}
    for repo, number in project_prs:
        for demo in store.list_demos(factory_id, number, repo=repo, limit=limit):
            if not demo.project_slug or demo.project_slug == slug:
                rows.setdefault(demo.id, demo)
    ordered = sorted(rows.values(), key=lambda d: (d.created_at, d.id), reverse=True)
    return ordered[:limit]


def _decision_titles(
    deps: FactoryDeps, asker_ids: set[str]
) -> tuple[dict[str, str], dict[str, str | None]]:
    rows = deps.conversation_store.get_conversations(list(asker_ids))
    titles = {sid: display_title(conv.title, conv.id) for sid, conv in rows.items()}
    roles = {sid: resolve_role(conv, deps) for sid, conv in rows.items()}
    return titles, roles


def _delivered_targets(deps: FactoryDeps, decisions: list[Any]) -> dict[str, str]:
    """Mail id -> delivered-to session for answered decisions (bounded lookups)."""
    out: dict[str, str] = {}
    for decision in [d for d in decisions if d.delivered_mail_id][:_DELIVERY_LOOKUPS]:
        since_ms = max(0, int(decision.answered_at or decision.created_at) - 60) * 1000
        found = deps.factory_store.find_events(
            decision.factory_id,
            "message.delivered",
            since_ms,
            f"mail:{decision.delivered_mail_id}",
            limit=1,
        )
        if found and found[0].to_id:
            out[decision.delivered_mail_id] = found[0].to_id
    return out


@router.get("/decisions")
async def list_open_decisions(
    request: Request,
    status: str = Query("open", description="A status or a comma-separated list."),
    count_only: bool = Query(False),
    assigned: str = Query("all", description="'me': only decisions for me or my role."),
) -> dict[str, Any]:
    """Decisions across every factory the caller can read (the Inbox mirror and its count)."""
    deps = factory_deps(request.app.state)
    user_id = require_user(request, deps.auth_provider)
    statuses = [s.strip() for s in status.split(",") if s.strip()]
    if not statuses or any(s not in _DECISION_STATUSES for s in statuses):
        raise FactoryRouteError(
            400, "invalid_status", f"status must be among {sorted(_DECISION_STATUSES)}"
        )
    if assigned not in ("me", "all"):
        raise FactoryRouteError(400, "invalid_assigned", "assigned must be 'me' or 'all'")
    roots = await asyncio.to_thread(list_factory_roots, deps.conversation_store)
    readable_roots = await asyncio.to_thread(readable_set, user_id, [r.id for r in roots], deps)
    by_id = {bare_id(r.id): r for r in roots if r.id in readable_roots}
    if not by_id:
        return schemas.OpenDecisionsResponse(count=0).model_dump(mode="json")
    rows = await asyncio.to_thread(
        lambda: deps.factory_store.list_decisions(
            factory_ids=list(by_id), status=statuses, limit=500
        )
    )
    askers = {d.asked_by_session_id for d in rows if d.asked_by_session_id}
    readable_askers = await asyncio.to_thread(readable_set, user_id, askers, deps)
    visible = [
        d for d in rows if not d.asked_by_session_id or d.asked_by_session_id in readable_askers
    ]
    levels = await asyncio.to_thread(
        lambda: {fid: people.viewer_level(deps, by_id[fid].id, user_id) for fid in by_id}
    )
    mine = {
        d.id
        for d in visible
        if decision_routing.is_mine(d, user_id, levels.get(bare_id(d.factory_id), 0))
    }
    if assigned == "me":
        visible = [d for d in visible if d.id in mine]
    if count_only:
        return schemas.OpenDecisionsResponse(count=len(visible)).model_dump(mode="json")
    titles, roles = await asyncio.to_thread(_decision_titles, deps, readable_askers)
    delivered = await asyncio.to_thread(_delivered_targets, deps, visible)
    if delivered:
        more, _ = await asyncio.to_thread(_decision_titles, deps, set(delivered.values()))
        titles.update(more)
    items = []
    for decision in visible:
        root = by_id.get(bare_id(decision.factory_id))
        if root is None:
            continue
        items.append(
            {
                "factory": {"id": bare_id(root.id), "name": _factory_name(root)},
                "decision": decision_view(decision, titles, roles, delivered),
                "mine": decision.id in mine,
            }
        )
    return schemas.OpenDecisionsResponse.model_validate(
        {"data": items, "count": len(visible)}
    ).model_dump(mode="json")


__all__ = ["router"]
