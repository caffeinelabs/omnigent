"""The binder: the work graph stays true even when agents forget to report.

It ties what the server observes to tasks:

- **Sub-agents** of a project lead, by title (on ``dispatch``, ``task.done`` and
  ``task.failed`` observations and on every supervisor tick):

  ======================== ==================================================
  ``<key>`` / ``<key>-r<k>`` implement attempt ``k + 1`` of task ``<key>``
  ``review-<key>[-r<k>]``   the review loop of the task's PR
  ``demo-<key>``            the demo loop of the task's PR
  ``env`` and team names    a team member, bound to no task
  anything else             an **ad-hoc task** (``kind=adhoc``, created by the
                            server) plus the warning ``task.unplanned``
  ======================== ==================================================

- **PRs** by branch ``factory/<slug>/<key>`` into ``factory_prs.task_id``; the
  task follows its PR: a draft → ``running`` (the IC still holds its slot),
  opened or out of draft → ``review``, merged → ``done`` (a human merge closes
  the task with no PL action).

A bound session joins the task's ``session_ids`` and its sub-agent span gets
the task id. Events: ``task.bound``, ``task.unplanned`` (plus the
``task.created`` / ``task.state`` of :mod:`.tasks`).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal

from omnigent.factory import LABEL_ID, LABEL_PROJECT, LABEL_ROLE, ROLE_PROJECT_LEAD, TASK_KEY_RE
from omnigent.server.factory import events, tasks
from omnigent.server.factory.actor import Actor
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.prs import parse_factory_branch
from omnigent.server.factory.telemetry.spans import (
    session_start_ms,
    span_context,
    task_cost_fields,
    task_key,
    write_span,
)
from omnigent.stores.factory_store import norm_id
from omnigent.util.session_lifecycle import title_without_closed_marker

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactoryEvent, FactoryPr, FactoryTask

_logger = logging.getLogger(__name__)

Loop = Literal["implement", "review", "demo", "team"]

TEAM_TITLES: Final = frozenset({"env", "env_keeper", "demo", "review", "reviewer", "planner"})
"""Titles of fixed team members, bound to no task."""
TEAM_AGENTS: Final = frozenset({"env_keeper", "planner"})
"""Sub-agents that are always team members, whatever their title."""
_RETRY_RE: Final = re.compile(r"^(?P<key>.+)-r(?P<k>[1-9][0-9]?)$")
_SLUG_CHARS: Final = re.compile(r"[^a-z0-9-]+")
_REFRESH_ON: Final = frozenset(
    {"decision.answered", "decision.withdrawn", "pr.merged", "stage.changed", "project.cancelled"}
)
_SEEN_MAX: Final = 20000
_DRAFT_FROM: Final = frozenset({"planned", "ready", "failed", "review"})
"""States a draft PR moves to ``running``: its IC still codes and holds a slot."""

_seen: dict[str, None] = {}
"""Children this process already bound or found to be team members."""


@dataclass(frozen=True)
class Binding:
    """
    What a sub-agent's title says it works on.

    :param loop: ``implement`` / ``review`` / ``demo``, or ``team`` (no task).
    :param key: The task key (an ad-hoc key when *planned* is false).
    :param attempt: The implement attempt (1-based).
    :param planned: The key names an existing task.
    """

    loop: Loop
    key: str | None = None
    attempt: int = 1
    planned: bool = True


def task_title(title: str | None, agent: str | None = None) -> str:
    """
    The task part of a sub-agent's stored title.

    :param title: e.g. ``"ic:route-r1"`` (``<agent>:<title>``, maybe with the closed marker).
    :param agent: The sub-agent name, e.g. ``"ic"``.
    :returns: e.g. ``"route-r1"``, lowercased and trimmed.
    """
    text = (title_without_closed_marker(title) or "").strip()
    prefix, sep, rest = text.partition(":")
    if sep and (agent is None or prefix == agent):
        text = rest
    return text.strip().lower()


def adhoc_key(text: str) -> str | None:
    """:returns: *text* as a task key (kebab-case, ≤ 48 chars), or ``None`` if nothing is left."""
    slug = _SLUG_CHARS.sub("-", text.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:48].strip("-")
    return slug if slug and TASK_KEY_RE.match(slug) else None


def _split_retry(text: str) -> tuple[str, int]:
    match = _RETRY_RE.match(text)
    return (match.group("key"), int(match.group("k")) + 1) if match else (text, 1)


def parse_title(title: str | None, keys: Collection[str], *, agent: str | None = None) -> Binding:
    """
    Map a sub-agent's title to a task binding (the binder table).

    :param keys: The project's task keys.
    :param agent: The sub-agent name; ``env_keeper`` is always a team member.
    """
    text = task_title(title, agent)
    if agent in TEAM_AGENTS or text in TEAM_TITLES:
        return Binding("team")
    if text in keys:
        return Binding("implement", text)
    base, attempt = _split_retry(text)
    if base in keys:
        return Binding("implement", base, attempt)
    for prefix, loop in (("review-", "review"), ("demo-", "demo")):
        if text.startswith(prefix) and len(text) > len(prefix):
            rest = text[len(prefix) :]
            target = rest if rest in keys else _split_retry(rest)[0]
            if target in keys:
                return Binding(loop, target)  # type: ignore[arg-type]
            return Binding(loop, adhoc_key(target), planned=False)  # type: ignore[arg-type]
    return Binding("implement", adhoc_key(base), attempt, planned=False)


# ── Sub-agents ─────────────────────────────────────────────────


def _project_lead(deps: FactoryDeps, child: Conversation) -> Conversation | None:
    parent_id = child.parent_conversation_id
    if not parent_id:
        return None
    parent = deps.conversation_store.get_conversation(parent_id)
    if parent is None or parent.labels.get(LABEL_ROLE) != ROLE_PROJECT_LEAD:
        return None
    if not parent.labels.get(LABEL_ID) or not parent.labels.get(LABEL_PROJECT):
        return None
    return parent


def _attach_span(
    deps: FactoryDeps, factory_id: str, child: Conversation, task: FactoryTask, binding: Binding
) -> None:
    """Stamp the task on the sub-agent's run span (written by the event recorder)."""
    try:
        write_span(
            deps.factory_store,
            factory_id,
            kind="task",
            name=(child.title or child.id)[:64],
            dedupe_key=task_key(child.id),
            started_at=session_start_ms(child) or int(time.time() * 1000),
            attrs={
                "task_ref": task.ref,
                "task_key": task.key,
                "task_kind": task.kind,
                "loop": binding.loop,
                "attempt": binding.attempt if binding.loop == "implement" else None,
            },
            ctx=span_context(deps, factory_id),
            project_slug=task.project_slug,
            session_id=child.id,
            task_id=task.id,
            attempt=binding.attempt if binding.loop == "implement" else None,
        )
    except Exception:  # noqa: BLE001 - heal rebuilds sub-agent spans
        _logger.warning("factory binder: no span for %s", child.id, exc_info=True)


def _bound_event(task: FactoryTask, child: Conversation, binding: Binding) -> tasks.TaskEvent:
    what = {
        "implement": f"attempt {binding.attempt}",
        "review": "review",
        "demo": "demo",
    }.get(binding.loop, binding.loop)
    return tasks.TaskEvent(
        "task.bound",
        task,
        f"{task.ref} {task.key}: {task_title(child.title, child.sub_agent_name)} ({what})",
        {
            "session_id": child.id,
            "agent": child.sub_agent_name,
            "loop": binding.loop,
            "attempt": binding.attempt if binding.loop == "implement" else None,
            **Actor.server().event_data(),
        },
        from_id=child.parent_conversation_id,
    )


def bind_sync(
    deps: FactoryDeps, factory_id: str, slug: str, child: Conversation
) -> tuple[FactoryTask | None, list[tasks.TaskEvent]]:
    """
    Bind one sub-agent of a project lead to its task (synchronous, idempotent).

    :returns: ``(task or None for a team member or an already bound session, events)``.
    """
    store = deps.factory_store
    rows = store.list_tasks(factory_id, project_slug=slug)
    if any(norm_id(child.id) in {norm_id(s) for s in t.session_ids or []} for t in rows):
        return None, []
    by_key = {t.key: t for t in rows}
    binding = parse_title(child.title, by_key, agent=child.sub_agent_name)
    if binding.loop == "team":
        return None, []
    server = Actor.server()
    vendor = tasks.VENDORS.get(child.sub_agent_name or "")
    out: list[tasks.TaskEvent] = []
    task = by_key.get(binding.key or "") if binding.planned else None
    if task is None:
        key = binding.key or adhoc_key(f"adhoc-{child.id[:8]}") or "adhoc"
        if key in by_key:
            task = by_key[key]
        else:
            raw = task_title(child.title, child.sub_agent_name) or key
            implement = binding.loop == "implement"
            task, created = store.upsert_task(
                factory_id,
                slug,
                key,
                title=raw[:256],
                kind="adhoc",
                state="running" if implement else "review",
                created_by=server.id,
                created_via="server",
                started_at=int(child.created_at) or int(time.time()),
                attempts=binding.attempt if implement else 0,
                vendor=vendor if implement else None,
                card={"harness": child.sub_agent_name}
                if child.sub_agent_name in tasks.HARNESSES
                else {},
                def_version=span_context(deps, factory_id).def_version,
            )
            if created:
                out.append(
                    tasks.TaskEvent(
                        "task.created",
                        task,
                        f"{task.ref} {key} added to {slug} by the server (unplanned dispatch)",
                        {"kind": "adhoc", "via": "server", **server.event_data()},
                    )
                )
                out.append(
                    tasks.TaskEvent(
                        "task.unplanned",
                        task,
                        f"{raw} runs outside the work graph; tracked as {task.ref} {key}",
                        {
                            "session_id": child.id,
                            "title": raw,
                            "agent": child.sub_agent_name,
                            "loop": binding.loop,
                            **server.event_data(),
                        },
                        severity="warning",
                    )
                )
    sessions = [*(task.session_ids or []), child.id]
    fields: dict[str, Any] = {"session_ids": sessions}
    to = task.state
    if binding.loop == "implement":
        fields["attempts"] = max(task.attempts, binding.attempt)
        if binding.attempt > task.attempts:
            tasks.note_bound_attempt(task.id, binding.attempt)
        if vendor:
            fields["vendor"] = vendor
        if task.state in ("planned", "ready", "failed"):
            to = "running"
    task, moved = tasks.move(
        deps, task, to, server, reason=f"worker {task_title(child.title)} dispatched", **fields
    )
    if moved is not None:
        out.append(moved)
    out.append(_bound_event(task, child, binding))
    _attach_span(deps, factory_id, child, task, binding)
    return task, out


async def bind_session(
    app_state: Any, child: Conversation, *, parent: Conversation | None = None
) -> FactoryTask | None:
    """
    Bind a sub-agent of a project lead to its task and emit what changed. Never raises.

    :param parent: The child's PL when the caller already has it.
    :returns: The task it was bound to now, or ``None``.
    """
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps):
        return None
    try:
        pl = parent or await asyncio.to_thread(_project_lead, deps, child)
        if pl is None or pl.labels.get(LABEL_ROLE) != ROLE_PROJECT_LEAD:
            return None
        factory_id, slug = pl.labels.get(LABEL_ID), pl.labels.get(LABEL_PROJECT)
        if not factory_id or not slug:
            return None
        async with tasks.lock(factory_id):
            task, evs = await asyncio.to_thread(bind_sync, deps, factory_id, slug, child)
        _remember(child.id)
        await tasks.emit_all(app_state, factory_id, evs)
        return task
    except Exception:  # noqa: BLE001 - the next tick binds it
        _logger.warning("factory binder: could not bind %s", child.id, exc_info=True)
        return None


def _remember(session_id: str) -> None:
    _seen[session_id] = None
    if len(_seen) > _SEEN_MAX:
        for key in list(_seen)[: _SEEN_MAX // 2]:
            _seen.pop(key, None)


def _update_cost_sync(deps: FactoryDeps, child: Conversation) -> None:
    """Re-total a task's spend from its bound sessions' usage."""
    store = deps.factory_store
    pl = _project_lead(deps, child)
    if pl is None:
        return
    factory_id, slug = pl.labels[LABEL_ID], pl.labels[LABEL_PROJECT]
    sid = norm_id(child.id)
    task = next(
        (
            t
            for t in store.list_tasks(factory_id, project_slug=slug)
            if sid in {norm_id(s) for s in t.session_ids or []}
        ),
        None,
    )
    if task is None:
        return
    total = 0.0
    priced = False
    for session_id in task.session_ids or []:
        conv = deps.conversation_store.get_conversation(session_id)
        cost = task_cost_fields(conv).get("cost_usd") if conv is not None else None
        if cost is not None:
            total += cost
            priced = True
    if priced and round(total, 4) != round(task.cost_usd or 0.0, 4):
        store.update_task(task.id, cost_usd=round(total, 4))


async def on_event(app_state: Any, event: FactoryEvent) -> None:
    """
    The ``events.add_listener`` hook: bind on dispatch and run ends, re-derive readiness.

    Never raises (the event log swallows listener errors too).
    """
    kind = event.kind
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps):
        return
    if kind in ("dispatch", "task.done", "task.failed") and event.to_id:
        child = await asyncio.to_thread(deps.conversation_store.get_conversation, event.to_id)
        if child is None:
            return
        await bind_session(app_state, child)
        if kind != "dispatch":
            try:
                async with tasks.lock(event.factory_id):
                    await asyncio.to_thread(_update_cost_sync, deps, child)
            except Exception:  # noqa: BLE001 - the next run end re-totals it
                _logger.warning("factory binder: no cost for %s", child.id, exc_info=True)
    elif kind in _REFRESH_ON:
        await tasks.refresh_ready(app_state, event.factory_id)


async def on_tick(
    app_state: Any, members: Sequence[Conversation], children: Sequence[Conversation]
) -> None:
    """
    The supervisor's per-tick hook: bind new sub-agents and re-derive readiness. Never raises.

    :param members: Top-level members of every factory.
    :param children: Their open sub-agents.
    """
    try:
        pls = {
            m.id: m
            for m in members
            if m.labels.get(LABEL_ROLE) == ROLE_PROJECT_LEAD and m.labels.get(LABEL_PROJECT)
        }
        for child in children:
            parent = pls.get(child.parent_conversation_id or "")
            if parent is not None and child.id not in _seen:
                await bind_session(app_state, child, parent=parent)
        for factory_id in sorted({m.labels[LABEL_ID] for m in members if m.labels.get(LABEL_ID)}):
            await tasks.refresh_ready(app_state, factory_id)
    except Exception:  # noqa: BLE001 - supervision goes on without the binder
        _logger.warning("factory binder: tick failed", exc_info=True)


# ── PRs ────────────────────────────────────────────────────────


def _task_for_pr(
    deps: FactoryDeps, factory_id: str, pr: FactoryPr
) -> tuple[FactoryTask | None, str | None, str | None]:
    """:returns: ``(linked or matching task, project slug, branch key)``."""
    store = deps.factory_store
    if pr.task_id:
        found = store.get_task(pr.task_id)
        if found is not None:
            return found, found.project_slug, None
    parsed = parse_factory_branch(pr.head_ref)
    slug = pr.project_slug or (parsed[0] if parsed else None)
    if slug is None:
        return None, None, None
    key = parsed[1].lower() if parsed and parsed[0] == slug else None
    candidates = [key, pr.task.lower() if pr.task else None]
    for raw in candidates:
        if not raw:
            continue
        for candidate in (raw, _split_retry(raw)[0]):
            found = store.get_task_by_key(factory_id, slug, candidate)
            if found is not None:
                return found, slug, key
    return None, slug, key


def link_pr_sync(
    deps: FactoryDeps, factory_id: str, before: FactoryPr | None, pr: FactoryPr
) -> list[tasks.TaskEvent]:
    """
    Link a PR to its task and move the task with the PR (synchronous).

    A newly linked, reopened or ready-for-review open PR moves its task to
    ``review``; a draft only marks its task ``running`` (the IC still codes and
    holds its slot). A merge moves it to ``done``. A factory-branch PR with no
    task becomes an ad-hoc task (open PRs only), with ``task.unplanned``.
    """
    store = deps.factory_store
    task, slug, key = _task_for_pr(deps, factory_id, pr)
    server = Actor.server()
    out: list[tasks.TaskEvent] = []
    newly = False
    if task is None:
        name = adhoc_key(key or "") if key else None
        if slug is None or name is None or pr.state != "open":
            return []
        task, created = store.upsert_task(
            factory_id,
            slug,
            name,
            title=(pr.title or name)[:256],
            kind="adhoc",
            state="running" if pr.is_draft else "review",
            created_by=server.id,
            created_via="server",
            started_at=pr.opened_at or pr.created_at or int(time.time()),
            pr_id=pr.id,
            def_version=span_context(deps, factory_id).def_version,
        )
        if created:
            out.append(
                tasks.TaskEvent(
                    "task.created",
                    task,
                    f"{task.ref} {name} added to {slug} by the server (PR #{pr.number})",
                    {"kind": "adhoc", "via": "server", "pr_id": pr.id, **server.event_data()},
                )
            )
            out.append(
                tasks.TaskEvent(
                    "task.unplanned",
                    task,
                    f"PR #{pr.number} ({pr.head_ref}) has no task; tracked as {task.ref} {name}",
                    {"pr_id": pr.id, "pr_number": pr.number, **server.event_data()},
                    severity="warning",
                )
            )
    if not pr.task_id or norm_id(pr.task_id) != norm_id(task.id):
        store.update_pr(pr.id, task_id=task.id)
        newly = True
        out.append(
            tasks.TaskEvent(
                "task.bound",
                task,
                f"{task.ref} {task.key}: PR #{pr.number}",
                {"pr_id": pr.id, "pr_number": pr.number, "loop": "pr", **server.event_data()},
            )
        )
    fields: dict[str, Any] = {}
    if task.pr_id is None or norm_id(task.pr_id) != norm_id(pr.id):
        fields["pr_id"] = pr.id
    to, reason = task.state, None
    reopened = before is not None and before.state != "open"
    undrafted = before is not None and before.is_draft and not pr.is_draft
    redrafted = before is not None and not before.is_draft and pr.is_draft
    if (
        pr.state == "open"
        and pr.is_draft
        and (newly or reopened or redrafted)
        and task.state in _DRAFT_FROM
    ):
        to, reason = "running", f"PR #{pr.number} is a draft"
    elif (
        pr.state == "open"
        and not pr.is_draft
        and (newly or reopened or undrafted)
        and task.state not in ("done", "cancelled", "review")
    ):
        to, reason = "review", f"PR #{pr.number} open"
    merged_now = before is None or before.state != "merged" or newly
    if pr.state == "merged" and merged_now and task.state not in ("done", "cancelled"):
        to, reason = "done", f"PR #{pr.number} merged by @{pr.merged_by or 'unknown'}"
        fields["closed_at"] = pr.merged_at or int(time.time())
    if to != task.state or fields:
        task, moved = tasks.move(deps, task, to, server, reason=reason, **fields)
        if moved is not None:
            moved.data.update({"pr_id": pr.id, "pr_number": pr.number})
            if pr.state == "merged":
                moved.data["merged_by"] = pr.merged_by
            out.append(moved)
    if any(e.kind == "task.state" and e.task.state == "done" for e in out):
        ctx = tasks.load_ctx(deps, factory_id, task.project_slug)
        out += tasks.refresh_states(deps, ctx)
    return out


async def on_pr(app_state: Any, before: FactoryPr | None, pr: FactoryPr | None) -> None:
    """
    The PR refresh and discovery hook: link the PR to its task, move the task. Never raises.

    :param before: The row before this refresh (``None`` when just discovered).
    """
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if pr is None or not isinstance(deps, FactoryDeps):
        return
    try:
        async with tasks.lock(pr.factory_id):
            evs = await asyncio.to_thread(link_pr_sync, deps, pr.factory_id, before, pr)
        await tasks.emit_all(app_state, pr.factory_id, evs)
    except Exception:  # noqa: BLE001 - the next refresh links it
        _logger.warning("factory binder: could not link PR %s", pr.id, exc_info=True)


def install() -> Any:
    """Register :func:`on_event` on the event log. :returns: The uninstaller."""
    return events.add_listener(on_event)


def reset() -> None:
    """Forget which sessions this process bound (tests)."""
    _seen.clear()


__all__ = [
    "TEAM_AGENTS",
    "TEAM_TITLES",
    "Binding",
    "adhoc_key",
    "bind_session",
    "bind_sync",
    "install",
    "link_pr_sync",
    "on_event",
    "on_pr",
    "on_tick",
    "parse_title",
    "reset",
    "task_title",
]
