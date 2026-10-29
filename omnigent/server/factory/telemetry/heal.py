"""Nightly span heal: rebuild and close spans from the tables and the event log.

The live writers can miss facts: ``events.emit`` swallows store errors, the
supervisor's sub-agent status lives in memory, and spans only exist from the
deploy that started recording them. Heal recomputes each span from its
source of truth (``factory_projects``, ``factory_prs``, ``factory_decisions``,
``factory_demos``, the sessions and the remaining events) under the live
writers' dedupe keys, so a second run changes nothing and the first run
backfills whatever events still exist. Spans heal creates carry no version:
the platform that ran them is unknown. A span a live writer closed after heal
read the events keeps that close. Each section, PR and replayed event heals
on its own, so one bad row only skips itself. It runs from the nightly prune
loop, before the prune.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Final

from omnigent.entities.factory import FactoryEvent, FactoryPr, FactoryProject
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.telemetry import pr_rounds
from omnigent.server.factory.telemetry.recorder import (
    Recorder,
    decision_span,
    demo_span,
    handler_for,
    pr_id_of,
    state_attrs,
)
from omnigent.server.factory.telemetry.spans import (
    ABANDONED,
    CANCELLED,
    FAIL,
    NAME_MAX,
    NO_CONTEXT,
    OK,
    OPEN,
    SUPERSEDED,
    TERMINAL_STAGES,
    TIMEOUT,
    env_key,
    pr_key,
    preview_key,
    project_key,
    ready_wait_key,
    session_start_ms,
    stage_key,
    task_cost_fields,
    task_key,
)

_logger = logging.getLogger(__name__)

HEAL_KINDS: Final[tuple[str, ...]] = (
    "factory.started",
    "project.*",
    "stage.changed",
    "pr.*",
    "gate.changed",
    "decision.*",
    "dispatch",
    "task.*",
    "env.reported",
    "preview.reported",
    "preview.parked",
    "demo.published",
    "needs_human",
    "needs_human.cleared",
    "issue.*",
    "control.*",
    "proposal.*",
)
"""Event kinds heal reads."""
REPLAYED_PREFIXES: Final[tuple[str, ...]] = (
    "needs_human",
    "issue.",
    "control.",
    "proposal.",
    "factory.started",
    "project.added",
)
"""Kinds whose recorder writes are idempotent in order, so heal replays them as is."""
STALE_TASK_S: Final = 600
"""A sub-agent idle this long without a ``task.done`` is closed from its session."""
_PAGE: Final = 1000
_MAX_ROWS: Final = 100_000
_MAX_FAILED_IN_A_ROW: Final = 10
"""Decisions failing back to back: the cause is not the row (the store is down), stop."""
_PROJECT_CLOSE: Final = {"project.cancelled": CANCELLED, "project.merged": SUPERSEDED}
_STATUS_CLOSE: Final = {"cancelled": CANCELLED, "merged": SUPERSEDED, "done": OK}
_DECISION_CLOSE: Final = {
    "answered": ("decision.answered", OK),
    "expired": ("decision.expired", TIMEOUT),
    "withdrawn": ("decision.withdrawn", CANCELLED),
}


@dataclass
class HealReport:
    """
    What one heal pass covered.

    :param failed: Factories whose heal could not run at all.
    :param partial: ``"<factory_id>: <part>"`` of each section, PR or event that failed.
    """

    factories: int = 0
    events: int = 0
    failed: list[str] = field(default_factory=list)
    partial: list[str] = field(default_factory=list)


_Guarded = Callable[[str, Callable[[], object]], bool]
"""Runs one part of a heal; logs and records a failure. :returns: Whether it succeeded."""


@dataclass
class _Episode:
    start: int
    state: str
    attrs: dict[str, Any]


def iter_events(
    store: Any, factory_id: str, kinds: Sequence[str] = HEAL_KINDS
) -> list[FactoryEvent]:
    """:returns: Every stored event of *kinds* in the factory, oldest first."""
    found: list[FactoryEvent] = []
    seen: set[str] = set()
    since = 0
    while True:
        page = store.find_events(factory_id, list(kinds), since, limit=_PAGE)
        fresh = [e for e in page if e.id not in seen]
        if not fresh:
            break
        for event in fresh:
            seen.add(event.id)
            found.append(event)
        if len(page) < _PAGE:
            break
        since = page[-1].ts
    found.sort(key=lambda e: (e.ts, e.id))
    return found


def _data(event: FactoryEvent) -> dict[str, Any]:
    return event.data if isinstance(event.data, dict) else {}


def write_episodes(
    rec: Recorder,
    kind: str,
    key: Callable[[int], str],
    items: Iterable[tuple[int, str, dict[str, Any]]],
    *,
    final_end: int | None = None,
    final_outcome: str = OPEN,
    **subject: Any,
) -> None:
    """
    Write state episodes exactly: consecutive same states merge, each ends where the next begins.

    :param items: ``(ts, state, attrs)`` oldest first.
    :param final_end: When the last episode ended (``None`` leaves it open).
    """
    episodes: list[_Episode] = []
    for ts, state, attrs in items:
        if episodes and episodes[-1].state == state:
            episodes[-1].attrs = {**episodes[-1].attrs, **attrs}
            continue
        episodes.append(_Episode(ts, state, dict(attrs)))
    for index, ep in enumerate(episodes):
        if index + 1 < len(episodes):
            end, outcome = episodes[index + 1].start, OK
        else:
            end, outcome = final_end, final_outcome if final_end is not None else OPEN
        rec.span(
            kind=kind,
            name=ep.state,
            dedupe_key=key(ep.start),
            started_at=ep.start,
            ended_at=end,
            outcome=outcome,
            force=True,
            reopen=True,
            attrs=ep.attrs,
            **subject,
        )


# ── Projects and stages ──────────────────────────────────────────


def _heal_projects(
    rec: Recorder, projects: list[FactoryProject], evs: list[FactoryEvent], now: int
) -> None:
    rows = {p.slug: p for p in projects}
    stages: dict[str, list[FactoryEvent]] = defaultdict(list)
    closing: dict[str, tuple[int, str]] = {}
    first_seen: dict[str, int] = {}
    for e in evs:
        slug = e.project_slug
        if slug:
            first_seen.setdefault(slug, e.ts)
        if e.kind == "stage.changed" and slug and _data(e).get("to"):
            stages[slug].append(e)
        elif e.kind in _PROJECT_CLOSE and slug:
            closing[slug] = (e.ts, _PROJECT_CLOSE[e.kind])
    for slug in sorted(set(rows) | set(stages)):
        row = rows.get(slug)
        if row is not None and row.created_at:
            started = row.created_at * 1000
        else:
            started = first_seen.get(slug, now * 1000)
        items = [(e.ts, str(_data(e)["to"]), {"from": _data(e).get("from")}) for e in stages[slug]]
        final: tuple[int | None, str] = (None, OPEN)
        project: tuple[int | None, str] = (None, OPEN)
        if slug in closing:
            final = project = closing[slug]
        elif row is not None and row.status in _STATUS_CLOSE and row.closed_at:
            final = project = (row.closed_at * 1000, _STATUS_CLOSE[row.status])
        elif items and items[-1][1] in TERMINAL_STAGES:
            project = (items[-1][0], OK)
        elif not items and row is not None and row.stage in TERMINAL_STAGES:
            project = ((row.stage_changed_at or row.created_at) * 1000, OK)
        rec.span(
            kind="project",
            name=slug,
            dedupe_key=project_key(slug),
            started_at=min([started, *(i[0] for i in items[:1])]),
            ended_at=project[0],
            outcome=project[1],
            force=True,
            reopen=True,
            project_slug=slug,
        )
        write_episodes(
            rec,
            "stage",
            lambda ts, s=slug: stage_key(s, ts),
            items,
            final_end=final[0],
            final_outcome=final[1],
            project_slug=slug,
        )


# ── PRs: rounds, gates, ready waits, first-ready facts ───────────


def _ready_waits(pr: FactoryPr, evs: list[FactoryEvent]) -> list[tuple[int, int | None, str]]:
    waits: list[tuple[int, int | None, str]] = []
    start: int | None = None
    closes = {"pr.not_ready": SUPERSEDED, "pr.merged": OK, "pr.closed": ABANDONED}
    for e in evs:
        if e.kind == "pr.ready":
            if start is not None:
                waits.append((start, e.ts, SUPERSEDED))
            start = e.ts
        elif e.kind in closes and start is not None:
            waits.append((start, e.ts, closes[e.kind]))
            start = None
    if start is None and not waits and pr.ready_at:
        # No ready event survived: the row still knows when it last became ready.
        start = pr.ready_at * 1000
    if start is not None:
        if pr.state == "merged" and pr.merged_at:
            waits.append((start, pr.merged_at * 1000, OK))
        elif pr.state == "closed" and pr.closed_at:
            waits.append((start, pr.closed_at * 1000, ABANDONED))
        elif pr.ready:
            waits.append((start, None, OPEN))
        else:
            waits.append((start, start, SUPERSEDED))
    return waits


def _heal_pr(
    rec: Recorder, pr: FactoryPr, evs: list[FactoryEvent], demos: list[Any], now: int
) -> None:
    # The row as of now: a refresh may have landed since the PRs were listed.
    pr = rec.store.get_pr(pr.id) or pr
    demo_rows = [
        {"head_sha": d.head_sha, "status": d.status, "created_at": d.created_at} for d in demos
    ]
    facts = pr_rounds.facts_from_history(pr, evs, demo_rows, now=now)
    pr_rounds.apply_facts(rec.store, facts, NO_CONTEXT, keep_closed_after=rec.keep_closed_after)
    parent = rec.store.get_span_by_dedupe(rec.factory_id, pr_key(pr.id))
    for start, end, outcome in _ready_waits(pr, [e for e in evs if e.kind.startswith("pr.")]):
        rec.span(
            kind="ready_wait",
            name="ready",
            dedupe_key=ready_wait_key(pr.id, start),
            started_at=start,
            ended_at=end,
            outcome=outcome,
            force=True,
            reopen=True,
            pr_id=pr.id,
            project_slug=pr.project_slug,
            parent_id=parent.id if parent is not None else None,
        )
    ready = [e.ts // 1000 for e in evs if e.kind == "pr.ready"]
    first = min(ready) if ready else pr.ready_at
    if first is not None and (pr.first_ready_at is None or pr.first_ready_at > first):
        rec.store.update_pr(pr.id, first_ready_at=first)


# ── Decisions, tasks, envs, previews, demos ──────────────────────


def _heal_decision(rec: Recorder, row: Any, by_ref: dict[tuple[str, str], int]) -> None:
    subject = f"decision:{row.ref}"
    opened = by_ref.get(("decision.opened", subject), row.created_at * 1000)
    close = _DECISION_CLOSE.get(row.status)
    if close is None:
        decision_span(rec, row, started_at=opened)
        return
    kind, outcome = close
    fallback = (row.answered_at or row.updated_at or row.created_at) * 1000
    end = by_ref.get((kind, subject), fallback)
    decision_span(
        rec,
        row,
        started_at=min(opened, end),
        ended_at=end,
        outcome=outcome,
        by=row.answered_by,
    )


def _heal_decisions(rec: Recorder, evs: list[FactoryEvent], guarded: _Guarded) -> None:
    rows = rec.store.list_decisions(factory_id=rec.factory_id, limit=_MAX_ROWS, newest_first=False)
    by_ref: dict[tuple[str, str], int] = {}
    for e in evs:
        if e.kind.startswith("decision.") and e.subject:
            by_ref.setdefault((e.kind, e.subject), e.ts)
    in_a_row = 0
    for row in rows:
        # One decision that cannot be written must not skip the rest.
        if guarded(f"decision {row.ref} {row.id}", partial(_heal_decision, rec, row, by_ref)):
            in_a_row = 0
            continue
        in_a_row += 1
        if in_a_row >= _MAX_FAILED_IN_A_ROW:
            raise RuntimeError(f"{in_a_row} decisions failed in a row; skipping the rest")


def _heal_tasks(rec: Recorder, evs: list[FactoryEvent], now: int) -> None:
    from omnigent.server.factory.telemetry import cost
    from omnigent.server.routes._sessions.helpers import _session_status_from_cache

    by_child: dict[str, list[FactoryEvent]] = defaultdict(list)
    for e in evs:
        if e.kind in ("dispatch", "task.done", "task.failed") and e.to_id:
            by_child[e.to_id].append(e)
    for child_id, items in by_child.items():
        conv = rec.deps.conversation_store.get_conversation(child_id)
        dispatch = next((e for e in items if e.kind == "dispatch"), None)
        created = session_start_ms(conv) if conv is not None else None
        if dispatch is not None:
            start = dispatch.ts
        elif created is not None:
            start = created
        else:
            start = items[0].ts
        closes = [e for e in items if e.kind != "dispatch"]
        end: int | None = None
        outcome = OPEN
        if closes:
            end, outcome = closes[-1].ts, OK if closes[-1].kind == "task.done" else FAIL
        elif conv is not None and now - int(conv.updated_at) > STALE_TASK_S:
            status = _session_status_from_cache(conv.id, conv.live_status)
            if status != "running":
                end, outcome = int(conv.updated_at * 1000), FAIL if status == "failed" else OK
        name = dispatch.summary.split(" · ", 1)[0] if dispatch is not None else ""
        who = cost.attribution(rec.deps, conv) if conv is not None else None
        costs = {k: v for k, v in task_cost_fields(conv).items() if v is not None} if conv else {}
        rec.span(
            kind="task",
            name=(name or (conv.title if conv is not None else "") or child_id)[:NAME_MAX],
            dedupe_key=task_key(child_id),
            started_at=start,
            ended_at=end,
            outcome=outcome,
            force=True,
            reopen=True,
            overwrite=tuple(costs),
            project_slug=items[0].project_slug,
            session_id=child_id,
            role=who.role if who is not None else None,
            agent_name=_data(dispatch).get("agent") if dispatch is not None else None,
            attrs={"parent_session_id": dispatch.from_id} if dispatch is not None else None,
            **costs,
        )


def _heal_states(rec: Recorder, evs: list[FactoryEvent]) -> None:
    envs: dict[str, list[tuple[int, str, dict[str, Any]]]] = defaultdict(list)
    previews: dict[str, list[tuple[int, str, dict[str, Any]]]] = defaultdict(list)
    preview_project: dict[str, str | None] = {}
    for e in evs:
        data = _data(e)
        state = data.get("state")
        if not isinstance(state, str) or not state:
            continue
        attrs = state_attrs(data)
        if e.kind == "env.reported" and e.project_slug:
            envs[e.project_slug].append((e.ts, state, attrs))
        elif e.kind in ("preview.reported", "preview.parked"):
            pr_id = pr_id_of(rec, data.get("pr_id"), e.project_slug)
            if pr_id is None:
                continue
            previews[pr_id].append((e.ts, state, attrs))
            preview_project.setdefault(pr_id, e.project_slug)
    for slug, items in envs.items():
        write_episodes(rec, "env", lambda ts, s=slug: env_key(s, ts), items, project_slug=slug)
    for pr_id, items in previews.items():
        write_episodes(
            rec,
            "preview",
            lambda ts, p=pr_id: preview_key(p, ts),
            items,
            pr_id=pr_id,
            project_slug=preview_project.get(pr_id),
        )


def _heal_demos(rec: Recorder, demos: list[Any], evs: list[FactoryEvent]) -> None:
    published = {_data(e).get("demo_id"): e.ts for e in evs if e.kind == "demo.published"}
    for demo in demos:
        demo_span(rec, demo, published.get(demo.id, demo.created_at * 1000))


# ── Driver ───────────────────────────────────────────────────────


def _group_pr_events(
    rec: Recorder, evs: list[FactoryEvent], into: dict[str, list[FactoryEvent]]
) -> None:
    """
    Group the ``pr.*`` and ``gate.changed`` events by the PR row they name.

    ``data.pr_id`` is resolved as the live recorder resolves it (:func:`pr_id_of`),
    so an event naming ``9724`` heals onto the same PR (and ready wait) it was
    recorded on, not beside it from ``pr.ready_at``.
    """
    for e in evs:
        if e.kind.startswith("pr.") or e.kind == "gate.changed":
            pr_id = pr_id_of(rec, _data(e).get("pr_id"), e.project_slug)
            if pr_id is not None:
                into[pr_id].append(e)


def heal_factory(
    deps: FactoryDeps,
    factory_id: str,
    *,
    now: int | None = None,
    failures: list[str] | None = None,
) -> int:
    """
    Rebuild one factory's spans (synchronous, idempotent).

    A section, PR or replayed event that raises is logged and skipped; the rest still heals.

    :param now: Epoch seconds (tests); defaults to now.
    :param failures: Receives the name of each part that failed.
    :returns: How many events were read.
    """
    at = int(time.time()) if now is None else now
    failed = failures if failures is not None else []
    store = deps.factory_store
    evs = iter_events(store, factory_id)
    rec = Recorder(deps, factory_id, NO_CONTEXT, keep_closed_after=at * 1000)

    def guarded(part: str, run: Callable[[], object]) -> bool:
        try:
            run()
        except Exception:  # noqa: BLE001 - one bad row must not stop the rest of the heal
            failed.append(part)
            _logger.warning("factory heal: %s of %s failed", part, factory_id, exc_info=True)
            return False
        return True

    guarded("projects", lambda: _heal_projects(rec, store.list_projects(factory_id), evs, at))
    demos: list[Any] = []
    guarded("demo rows", lambda: demos.extend(store.list_demos(factory_id, limit=_MAX_ROWS)))
    prs: list[FactoryPr] = []
    if guarded("pr rows", lambda: prs.extend(store.list_prs(factory_id))):
        rec.know_prs(prs)
    pr_events: dict[str, list[FactoryEvent]] = defaultdict(list)
    if not guarded("pr events", partial(_group_pr_events, rec, evs, pr_events)):
        prs.clear()  # Without its events a PR would heal from its row alone, beside its spans.
    for pr in prs:
        mine = [
            d for d in demos if d.pr_id == pr.id or (d.repo, d.pr_number) == (pr.repo, pr.number)
        ]
        guarded(f"pr {pr.id}", partial(_heal_pr, rec, pr, pr_events.get(pr.id, []), mine, at))
    guarded("decisions", partial(_heal_decisions, rec, evs, guarded))
    guarded("tasks", partial(_heal_tasks, rec, evs, at))
    guarded("envs and previews", partial(_heal_states, rec, evs))
    guarded("demos", partial(_heal_demos, rec, demos, evs))
    for e in evs:
        handler = handler_for(e.kind)
        if handler is not None and e.kind.startswith(REPLAYED_PREFIXES):
            guarded(f"event {e.kind} {e.id}", partial(handler, rec, e))
    return len(evs)


async def heal_once(app_state: Any, *, now: int | None = None) -> HealReport:
    """
    Heal every factory on this server. Never raises.

    :param app_state: ``app.state`` carrying the factory deps.
    :param now: Epoch seconds (tests).
    """
    from omnigent.server.factory.snapshot import list_factory_roots

    report = HealReport()
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps):
        return report
    try:
        roots = await asyncio.to_thread(list_factory_roots, deps.conversation_store)
    except Exception:  # noqa: BLE001 - the next night retries
        _logger.warning("factory heal: could not list factories", exc_info=True)
        return report
    for root in roots:
        failures: list[str] = []
        try:
            report.events += await asyncio.to_thread(
                heal_factory, deps, root.id, now=now, failures=failures
            )
            report.factories += 1
        except Exception:  # noqa: BLE001 - one broken factory must not stop the others
            report.failed.append(root.id)
            _logger.warning("factory heal: %s failed", root.id, exc_info=True)
        report.partial.extend(f"{root.id}: {part}" for part in failures)
    if report.factories or report.failed:
        _logger.info(
            "factory heal: %d factory(ies), %d event(s) read, %d failed, %d part(s) skipped",
            report.factories,
            report.events,
            len(report.failed),
            len(report.partial),
        )
    return report


__all__ = [
    "HEAL_KINDS",
    "HealReport",
    "heal_factory",
    "heal_once",
    "iter_events",
    "write_episodes",
]
