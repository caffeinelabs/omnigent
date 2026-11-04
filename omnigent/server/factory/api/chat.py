"""The Room API: threads, messages, read marks, the status digest and agent posts.

People (user JWT; writes carry :data:`~omnigent.server.factory.origin.HUMAN_ORIGIN`):

- ``GET /v1/factory/factories/{fid}/chat/threads?nodes=``: every thread with its
  responder, last ``seq``, the caller's unread count and ``node_ref`` (the node
  it is about: ``product``, a project's slug, ``PR-<n>``, ``T-16`` …; ``null``
  for ``#discovery``). ``nodes=1`` adds the node threads (``need:``,
  ``feature:``, ``study:``, ``task:``) that have messages, the newest 50. READ.
- ``GET /v1/factory/factories/{fid}/chat?thread=&before_seq=&after_seq=&limit=``:
  a page of messages, oldest first. READ. ``feed=conversation`` (one thread;
  cockpit v3's node chat): only people's and agents' words and the live cards
  this reader acts on; ``feed=activity``: only the system's news (base moves,
  spawns, incidents, closed or answered items); ``feed=all``: every row, each
  marked with its ``lane``. Each with ``unread``,
  ``activity_since`` (after the reader's read mark) and ``before_seq`` /
  ``after_seq`` to read on (:mod:`.chat_feed`).

Both leave out the threads of projects the caller's snapshot hides (a PL it
cannot read), as the status digest does.
- ``POST /v1/factory/factories/{fid}/chat`` ``{thread, body, reply_to?, mentions?}``:
  post as yourself; routed to the thread's agent. EDIT, 20 posts per 10 min.
- ``PUT /v1/factory/factories/{fid}/chat/reads`` ``{thread, last_seq}``: move
  your read mark forward. READ.
- ``GET /v1/factory/factories/{fid}/status-digest?slug=``: a markdown digest
  of the snapshot (``/status [slug]``). READ.

Agents (runner tunnel, ``from_session_id``):

- ``POST /v1/factory/agent/chat`` ``{thread, message, reply_to?, mentions?, refs?}``
  (``sys_factory_chat``): leads anywhere; a project lead in ``#factory`` and its
  own project's threads.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Annotated, Any, Final, Literal, TypeVar

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from omnigent.entities import Conversation
from omnigent.factory import (
    CHAT_MESSAGE_MAX_CHARS,
    FIXED_THREADS,
    PROJECT_KIND_INTEGRATION,
    THREAD_DISCOVERY,
    THREAD_PR_PREFIX,
    THREAD_PROJECT_PREFIX,
    thread_kind,
)
from omnigent.server.auth import LEVEL_EDIT, RESERVED_USER_LOCAL, runner_origin
from omnigent.server.factory import (
    chat,
    chat_cards,
    chat_feed,
    chat_routing,
    control,
    live,
    node_refs,
    people,
)
from omnigent.server.factory.agent_scope import authorize_agent
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.origin import HUMAN_ORIGIN
from omnigent.server.factory.snapshot import filter_snapshot, get_snapshot, readable_for
from omnigent.server.factory.start import parse_body
from omnigent.server.factory.your_move import Viewer
from omnigent.server.routes._auth_helpers import get_user_id
from omnigent.stores.factory_store import norm_id

router = APIRouter(prefix="/v1/factory", tags=["factory"])

PAGE_MAX: Final = 200
UNREAD_CAP: Final = 99
MENTION_PREVIEW_CHARS: Final = 200
NODE_THREADS_MAX: Final = 50
"""Node threads ``?nodes=1`` lists at most (the newest): each costs a lookup and an unread read."""
_T = TypeVar("_T")

# Factory events become cards in the Room.
chat_cards.register()


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PostBody(_Body):
    """``POST …/chat``."""

    thread: str = Field(min_length=1, max_length=96)
    body: str = Field(min_length=1, max_length=CHAT_MESSAGE_MAX_CHARS)
    reply_to: str | None = Field(None, max_length=64)
    mentions: list[str] = Field(default_factory=list, max_length=20)


class ReadsBody(_Body):
    """``PUT …/chat/reads``."""

    thread: str = Field(min_length=1, max_length=96)
    last_seq: int = Field(ge=0)


async def _guard(call: Callable[[], Awaitable[_T]]) -> _T | JSONResponse:
    try:
        return await call()
    except CockpitError as exc:
        headers = (
            {"Retry-After": str(exc.extra["retry_after"])} if "retry_after" in exc.extra else None
        )
        return JSONResponse(status_code=exc.status_code, content=exc.body(), headers=headers)


async def _root(request: Request, factory_id: str) -> tuple[FactoryDeps, str | None, Conversation]:
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)
    root = await control.load_factory_root(deps, user_id, factory_id)
    return deps, user_id, root


def _reader(user_id: str | None) -> str:
    return user_id or RESERVED_USER_LOCAL


async def _hidden_projects(
    request: Request, deps: FactoryDeps, root: Conversation, user_id: str | None
) -> frozenset[str]:
    """:returns: The slugs of projects whose PL the caller cannot read."""
    raw = await get_snapshot(request.app.state, root)
    readable = await readable_for(user_id, raw, deps)
    return frozenset(
        s for s, pl in raw.project_pl.items() if pl is not None and pl not in readable
    )


def _thread_project(deps: FactoryDeps, factory_id: str, key: str) -> str | None:
    if key.startswith(THREAD_PROJECT_PREFIX):
        return key[len(THREAD_PROJECT_PREFIX) :]
    if key.startswith(THREAD_PR_PREFIX):
        try:
            pr = deps.factory_store.get_pr(key[len(THREAD_PR_PREFIX) :])
        except ValueError:
            return None
        return pr.project_slug if pr is not None else None
    if thread_kind(key) == "task":
        return _task_thread_project(deps, factory_id, key)
    return None


def _task_thread_project(deps: FactoryDeps, factory_id: str, key: str) -> str | None:
    """A task thread's project (``task:T-16``; a PR's implicit task's: its PR's project)."""
    ref = node_refs.parse(node_refs.thread_ref(key) or "")
    store = deps.factory_store
    if ref is not None and ref.kind == "task":
        task = store.get_task_by_number(factory_id, int(ref.id))
        return task.project_slug if task is not None else None
    if ref is not None and ref.kind == "adhoc":
        try:
            pr = store.get_pr(ref.id)
        except ValueError:
            return None
        return pr.project_slug if pr is not None else None
    return None


def _visible(
    deps: FactoryDeps, factory_id: str, rows: Sequence[Any], hidden: frozenset[str]
) -> list[Any]:
    """:returns: *rows* without those of hidden projects' threads."""
    keys = {r.thread for r in rows}
    projects = {key: _thread_project(deps, factory_id, key) for key in keys}
    return [r for r in rows if projects[r.thread] not in hidden]


# ── Threads ────────────────────────────────────────────────────


def _node_ref(thread: chat.Thread) -> str | None:
    """The node a thread is about (``node_ref``)."""
    if thread.node:
        return thread.node
    if thread.pr is not None:
        return f"PR-{thread.pr.number}"
    if thread.kind == "project" and thread.project:
        return node_refs.Ref("project", thread.project).text
    return node_refs.thread_ref(thread.key)


def thread_views(
    deps: FactoryDeps,
    root: Conversation,
    user_id: str | None,
    hidden: frozenset[str] = frozenset(),
    *,
    nodes: bool = False,
) -> list[dict]:
    """
    Every thread of the Room: the fixed ones, one per project, and each PR thread with messages.

    :param hidden: Project slugs left out, with their PR threads (and their tasks' threads).
    :param nodes: Also the node threads (``need:``, ``feature:``, ``study:``, ``task:``)
        that have messages, newest first, after the PRs (the newest
        :data:`NODE_THREADS_MAX`: each costs a lookup and an unread read).
    :returns: ``ThreadView`` JSON, fixed threads first, then projects, then PRs (newest first).
    """
    store = deps.factory_store
    stats = {s.thread: s for s in store.chat_thread_stats(root.id)}
    reads = {r.thread: r.last_seq for r in store.list_chat_reads(root.id, _reader(user_id))}
    projects = store.list_projects(root.id)
    # ``#discovery`` resolves only while discovery runs; otherwise it is skipped below.
    keys: list[str] = [*FIXED_THREADS, THREAD_DISCOVERY]
    keys += [f"{THREAD_PROJECT_PREFIX}{p.slug}" for p in projects]
    pr_keys = [k for k in stats if k.startswith(THREAD_PR_PREFIX)]
    pr_keys.sort(key=lambda k: -stats[k].last_seq)
    keys += [k for k in pr_keys if k not in keys]
    if nodes:
        node_keys = [k for k in stats if thread_kind(k) in chat_routing.NODE_KINDS]
        node_keys.sort(key=lambda k: -stats[k].last_seq)
        keys += [k for k in node_keys[:NODE_THREADS_MAX] if k not in keys]
    threads: list[chat.Thread] = []
    for key in keys:
        try:
            thread = chat.resolve_thread(deps, root.id, key)
        except CockpitError:
            continue
        if thread.project not in hidden:
            threads.append(thread)
    members = chat.members_of(deps, root, threads)
    views: list[dict[str, Any]] = []
    for thread in threads:
        key = thread.key
        stat = stats.get(key)
        answer = chat.responder(deps, root, thread, members)
        views.append(
            {
                "key": thread.key,
                "kind": thread.kind,
                "title": thread.title,
                "project_slug": thread.project,
                "pr_id": norm_id(thread.pr.id) if thread.pr is not None else None,
                "pr_number": thread.pr.number if thread.pr is not None else None,
                "node_ref": _node_ref(thread),
                "responder_session_id": norm_id(answer.session_id) if answer else None,
                "responder_title": answer.name if answer else None,
                "last_seq": stat.last_seq if stat else 0,
                "last_at": stat.last_at if stat else None,
                "messages": stat.messages if stat else 0,
                **_unread(deps, root.id, key, reads.get(key, 0), stat, user_id),
            }
        )
    return views


def _unread(
    deps: FactoryDeps, factory_id: str, thread: str, last_read: int, stat: Any, user_id: str | None
) -> dict[str, Any]:
    """:returns: ``{unread, mention}``: the count and the newest unread @mention of the reader."""
    if stat is None or stat.last_seq <= last_read:
        return {"unread": 0, "mention": None}
    rows = deps.factory_store.list_chat(
        factory_id, thread, after_seq=last_read, limit=UNREAD_CAP + 1
    )
    others = [r for r in rows if r.author != user_id]
    me = f"@{user_id}".casefold() if user_id else None
    named = [r for r in others if me and any(m.casefold() == me for m in r.mentions or ())]
    newest = max(named, key=lambda r: r.seq, default=None)
    mention = (
        {"seq": newest.seq, "body": newest.body[:MENTION_PREVIEW_CHARS]}
        if newest is not None
        else None
    )
    return {"unread": min(UNREAD_CAP, len(others)), "mention": mention}


@router.get("/factories/{factory_id}/chat/threads", response_model=None)
async def list_threads(
    request: Request, factory_id: str, nodes: bool = Query(False)
) -> dict[str, Any] | JSONResponse:
    """The Room's threads with responders, unread counts and the node each is about."""

    async def run() -> dict[str, Any]:
        deps, user_id, root = await _root(request, factory_id)
        hidden = await _hidden_projects(request, deps, root, user_id)
        views = await asyncio.to_thread(
            lambda: thread_views(deps, root, user_id, hidden, nodes=nodes)
        )
        return {"data": views}

    return await _guard(run)


@router.get("/factories/{factory_id}/chat", response_model=None)
async def list_messages(
    request: Request,
    factory_id: str,
    thread: str | None = Query(None),
    before_seq: int | None = Query(None, ge=1),
    after_seq: int | None = Query(None, ge=0),
    limit: int = Query(50, ge=1, le=PAGE_MAX),
    feed: Literal["conversation", "activity", "all"] | None = Query(None),
) -> dict[str, Any] | JSONResponse:
    """A page of messages, oldest first (the latest page unless ``after_seq``)."""

    async def run() -> dict[str, Any]:
        deps, user_id, root = await _root(request, factory_id)
        if feed is not None and thread is None:
            raise CockpitError(400, "invalid_query", "a feed reads one thread", field="thread")
        hidden = await _hidden_projects(request, deps, root, user_id)
        key = None
        if thread is not None:
            resolved = await asyncio.to_thread(chat.resolve_thread, deps, root.id, thread)
            if resolved.project in hidden:
                raise CockpitError(404, "unknown_project", "no such project in this factory")
            key = resolved.key
        if feed is not None and key is not None:
            level = await asyncio.to_thread(people.viewer_level, deps, root.id, user_id)
            return await asyncio.to_thread(
                lambda: chat_feed.feed_page(
                    deps,
                    root.id,
                    key,
                    Viewer(user_id, level),
                    feed,
                    reader=_reader(user_id),
                    views=lambda rows: chat.message_views(deps, rows),
                    before_seq=before_seq,
                    after_seq=after_seq,
                    limit=limit,
                    now=time.time(),
                )
            )
        rows = await asyncio.to_thread(
            lambda: deps.factory_store.list_chat(
                root.id, key, before_seq=before_seq, after_seq=after_seq, limit=limit + 1
            )
        )
        more = len(rows) > limit
        rows = rows[:limit] if after_seq is not None else rows[-limit:]
        if hidden and key is None:
            rows = await asyncio.to_thread(_visible, deps, root.id, rows, hidden)
        views = await asyncio.to_thread(chat.message_views, deps, rows)
        return {"data": views, "has_more": more, "thread": key}

    return await _guard(run)


@router.post(
    "/factories/{factory_id}/chat",
    status_code=201,
    response_model=None,
    dependencies=HUMAN_ORIGIN,
)
async def post_message(
    request: Request, factory_id: str, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """Post in a thread as yourself; the thread's agent (or the mentioned ones) answer."""

    async def run() -> dict[str, Any]:
        deps, user_id, root = await _root(request, factory_id)
        await control.require(deps, user_id, root.id, LEVEL_EDIT)
        body = parse_body(PostBody, payload)
        posted = await chat.post_human(request.app.state, root, user_id, body.model_dump())
        rows = [posted.message, *posted.notes]
        views = await asyncio.to_thread(chat.message_views, deps, rows)
        return {
            "message": views[0],
            "notes": views[1:],
            "routed": [t.name for t in posted.route.targets],
        }

    return await _guard(run)


@router.put("/factories/{factory_id}/chat/reads", response_model=None, dependencies=HUMAN_ORIGIN)
async def put_read(
    request: Request, factory_id: str, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """Move the caller's read mark in one thread forward (never back)."""

    async def run() -> dict[str, Any]:
        deps, user_id, root = await _root(request, factory_id)
        body = parse_body(ReadsBody, payload)
        thread = await asyncio.to_thread(chat.resolve_thread, deps, root.id, body.thread)
        row = await asyncio.to_thread(
            deps.factory_store.upsert_chat_read,
            root.id,
            _reader(user_id),
            thread.key,
            body.last_seq,
        )
        # The reader's other tabs and devices refresh their unread counts (only when it moved).
        if row.last_seq == body.last_seq:
            live.announce_factory_changed(request.app.state, root.id, ["reads"])
        return {"thread": row.thread, "last_seq": row.last_seq}

    return await _guard(run)


# ── Status digest ──────────────────────────────────────────────


def _usd(value: Any) -> str | None:
    return f"${value:,.2f}" if isinstance(value, int | float) else None


def _pr_line(pr: Mapping[str, Any]) -> str:
    gates = pr.get("gates") or {}
    known = [f"{n} {gates[n]}" for n in chat.GATE_ORDER if gates.get(n) not in (None, "unknown")]
    state = pr.get("state") or "open"
    if state == "open":
        state = "ready for human merge" if pr.get("ready") else "open"
    demo = (pr.get("demo") or {}).get("status")
    parts = [f"!{pr.get('number')}", state, *known]
    if demo:
        parts.append(f"demo {demo}")
    return " · ".join(str(p) for p in parts)


def _project_line(project: Mapping[str, Any], prs: Sequence[Mapping[str, Any]]) -> str:
    stage = project.get("stage") or "queued"
    overlay = project.get("overlay")
    if overlay:
        reason = project.get("overlay_reason")
        stage += f" ({overlay}: {reason})" if reason else f" ({overlay})"
    pl = project.get("pl") or {}
    who = f"{pl.get('title')} {pl.get('status')}" if pl else "no PL yet"
    line = f"- `{project.get('slug')}` {stage} · {who}"
    mine = [
        p for p in prs if p.get("project_slug") == project.get("slug") and p.get("state") == "open"
    ]
    if mine:
        line += " · " + "; ".join(_pr_line(p) for p in mine[:3])
    return line


def render_digest(body: Mapping[str, Any], slug: str | None = None) -> str:
    """
    A markdown digest of a (filtered) snapshot.

    :param slug: One project's detail instead of the whole factory.
    :raises CockpitError: 404 ``unknown_project`` for a slug the snapshot lacks.
    """
    factory = body.get("factory") or {}
    projects = [p for p in body.get("projects") or [] if p.get("status") in (None, "active")]
    prs = body.get("prs") or []
    decisions = [d for d in body.get("decisions") or [] if d.get("status") == "open"]
    if slug:
        project = next((p for p in body.get("projects") or [] if p.get("slug") == slug), None)
        if project is None:
            raise CockpitError(404, "unknown_project", f"no project {slug!r} in this factory")
        lines = [f"**#{slug}** · {project.get('title')} · P{project.get('priority')}"]
        lines.append(_project_line(project, prs).removeprefix("- "))
        for pr in [p for p in prs if p.get("project_slug") == slug]:
            lines.append(f"- {_pr_line(pr)} · {pr.get('url')}")
        waiting = project.get("depends_on") or []
        if waiting and project.get("overlay") == "waiting":
            lines.append(f"Waiting on: {', '.join(waiting)}")
        mine = [d for d in decisions if d.get("project_slug") == slug]
    else:
        totals = body.get("totals") or {}
        work = [
            p
            for p in projects
            if ((p.get("charter") or {}).get("kind")) != PROJECT_KIND_INTEGRATION
        ]
        hidden = body.get("hidden_projects") or 0
        head = [
            f"**{factory.get('name')}** · {factory.get('state')}",
            f"{len(work)} projects" + (f" ({hidden} hidden)" if hidden else ""),
            f"{totals.get('prs_ready', 0)} ready for human merge",
            f"{totals.get('decisions_open', 0)} decisions open",
        ]
        if (cost := _usd(totals.get("cost_usd"))) is not None:
            head.append(cost)
        lines = [" · ".join(head)]
        lines += [_project_line(p, prs) for p in projects]
        mine = decisions
    if mine:
        asks = [
            f"{d.get('ref')}{' (blocking)' if d.get('blocking') else ''} "
            f"{chat.one_line(str(d.get('question')), 120)}"
            for d in mine[:5]
        ]
        lines.append("Needs you: " + " · ".join(asks))
    return "\n".join(lines)


@router.get("/factories/{factory_id}/status-digest", response_model=None)
async def status_digest(
    request: Request, factory_id: str, slug: str | None = Query(None, max_length=64)
) -> dict[str, Any] | JSONResponse:
    """The ``/status [slug]`` digest: markdown rendered from the caller's snapshot."""

    async def run() -> dict[str, Any]:
        deps, user_id, root = await _root(request, factory_id)
        raw = await get_snapshot(request.app.state, root)
        readable = await readable_for(user_id, raw, deps)
        body = filter_snapshot(raw, readable)
        return {"markdown": render_digest(body, slug), "slug": slug}

    return await _guard(run)


# ── Agents ─────────────────────────────────────────────────────


@router.post("/agent/chat", response_model=None)
async def post_agent_message(
    request: Request, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """``sys_factory_chat``: answer or post in a Room thread."""

    async def run() -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise CockpitError(400, "invalid_body", "the body must be a JSON object", field="body")
        sender = payload.get("from_session_id")
        if not isinstance(sender, str) or not sender:
            raise CockpitError(400, "invalid_body", "from_session_id is required", field="body")
        caller = await authorize_agent(request, sender)
        # A person's own credential posting as an agent is recorded on the event.
        human = runner_origin(request, caller.deps.auth_provider) is None
        via = caller.user_id if human and caller.user_id != RESERVED_USER_LOCAL else None
        return await chat.post_agent(request.app.state, caller, payload, via_user=via)

    return await _guard(run)


__all__ = ["render_digest", "router", "thread_views"]
