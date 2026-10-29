"""The span recorder: factory events in, spans out.

Registered on the event log by :func:`.spans.install`, it runs after every
stored event and writes the span the event opens, extends or closes:

- ``project`` (``factory.started``, ``project.added``; closed by a terminal
  stage, ``project.cancelled`` or ``project.merged``) and ``stage`` episodes;
- ``ready_wait`` (``pr.ready`` until ``pr.not_ready``/``pr.merged``/``pr.closed``);
- ``decision`` (``decision.opened`` until answered, expired or withdrawn);
- ``task`` (``dispatch`` until ``task.done``/``task.failed``; the supervisor
  adds the cost);
- ``env`` and ``preview`` state episodes, ``demo`` runs, ``incident``
  (``needs_human``, ``issue.*``), zero-length ``intervention`` (controls) and
  ``proposal`` spans.

Handlers are synchronous and run in a worker thread; a failure is logged with
the event id (and the span column, for a value that is not a row id) and heal
rebuilds the span. Ids read from event data or a decision's subject go through
:func:`pr_id_of` / :func:`pr_for_ref`, so a PR number never reaches an id column.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from omnigent.entities.factory import FactoryEvent, FactoryPr, FactorySpan
from omnigent.factory import parse_pr_url
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.telemetry.spans import (
    ABANDONED,
    CANCELLED,
    FAIL,
    NO_CONTEXT,
    OK,
    OPEN,
    SUPERSEDED,
    TERMINAL_STAGES,
    TIMEOUT,
    SpanContext,
    SpanFieldError,
    close_open,
    decision_key,
    demo_key,
    env_key,
    incident_key,
    intervention_key,
    merged_attrs,
    pr_key,
    preview_key,
    project_key,
    proposal_key,
    ready_wait_key,
    session_start_ms,
    span_context,
    span_ids,
    stage_key,
    task_key,
    write_span,
)
from omnigent.stores.factory_store import norm_id

if TYPE_CHECKING:
    from omnigent.stores.factory_store import FactoryStore

_logger = logging.getLogger(__name__)

_KNOWN_MAX: Final = 4096
_PR_REF_RE: Final = re.compile(r"(?:(?P<repo>[\w.-]+/[\w.-]+)\s*)?[#!]?\s*(?P<number>\d{1,9})")
"""A PR by number: ``9724``, ``#9724``, ``!9724`` or ``owner/repo#9724``."""

_known: OrderedDict[tuple[str, str], None] = OrderedDict()
"""Spans this process already ensured exist (``(factory_id, dedupe_key)``)."""


def _known_span(factory_id: str, key: str) -> bool:
    """:returns: Whether this process already wrote the span under *key*."""
    entry = (factory_id, key)
    if entry in _known:
        _known.move_to_end(entry)
        return True
    return False


def _remember(factory_id: str, key: str) -> None:
    _known[(factory_id, key)] = None
    if len(_known) > _KNOWN_MAX:
        _known.popitem(last=False)


@dataclass
class Recorder:
    """Writes the spans of one stored event (synchronous; run in a worker thread)."""

    deps: FactoryDeps
    factory_id: str
    ctx: SpanContext = NO_CONTEXT
    keep_closed_after: int | None = None
    """Heal's read time (epoch ms); see :func:`.spans.write_span`."""

    @property
    def store(self) -> FactoryStore:
        return self.deps.factory_store

    def span(self, **kwargs: Any) -> FactorySpan:
        return write_span(
            self.store,
            self.factory_id,
            ctx=self.ctx,
            keep_closed_after=self.keep_closed_after,
            **kwargs,
        )

    def ensure_project(self, slug: str | None, ts: int) -> None:
        if not slug or _known_span(self.factory_id, project_key(slug)):
            return
        row = self.store.get_project(self.factory_id, slug)
        started = row.created_at * 1000 if row is not None and row.created_at else ts
        self.span(
            kind="project",
            name=slug,
            dedupe_key=project_key(slug),
            started_at=min(started, ts),
            project_slug=slug,
        )
        _remember(self.factory_id, project_key(slug))

    def episode(
        self, kind: str, state: str, key: str, ts: int, attrs: dict[str, Any], **subject: Any
    ) -> None:
        """Extend the open *kind* episode of a subject in *state*, or close it and start one."""
        project = subject.get("project_slug")
        pr_id = span_ids({"pr_id": subject.get("pr_id")})["pr_id"]
        current = self.store.list_open_spans(
            self.factory_id, kind, project_slug=project, pr_id=pr_id
        )
        same = next((s for s in current if s.name == state and s.started_at <= ts), None)
        if same is not None:
            merged = merged_attrs(same.attrs, attrs, ())
            if merged is not None:
                self.store.update_span(same.id, attrs=merged)
            return
        close_open(
            self.store, self.factory_id, kind, ended_at=ts, project_slug=project, pr_id=pr_id
        )
        self.span(kind=kind, name=state, dedupe_key=key, started_at=ts, attrs=attrs, **subject)


def _data(event: FactoryEvent) -> dict[str, Any]:
    return event.data if isinstance(event.data, dict) else {}


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _title(summary: str) -> str:
    return summary.split(" · ", 1)[0].strip()


def row_id(value: Any) -> str | None:
    """:returns: *value* as a bare-hex row id, or ``None`` when it is no uuid."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return norm_id(value)
    except ValueError:
        return None


def pr_for_ref(
    store: FactoryStore, factory_id: str, ref: Any, *, project: str | None = None
) -> FactoryPr | None:
    """
    The factory PR a reference names, or ``None``.

    :param ref: The ``factory_prs`` row id, or the PR as ``9724``, ``#9724``,
        ``!9724``, ``owner/repo#9724`` or its URL; a trailing ``@<sha>`` is ignored.
    :param project: When a bare number matches PRs of several repos, prefer this project's.
    """
    if isinstance(ref, int) and not isinstance(ref, bool):
        ref = str(ref)
    if not isinstance(ref, str):
        return None
    text = ref.partition("@")[0].strip()
    fid = norm_id(factory_id)
    pr_id = row_id(text)
    if pr_id is not None:
        pr = store.get_pr(pr_id)
        return pr if pr is not None and norm_id(pr.factory_id) == fid else None
    parsed = parse_pr_url(text)
    if parsed is None:
        match = _PR_REF_RE.fullmatch(text)
        if match is None:
            return None
        parsed = (match["repo"] or "", int(match["number"]))
    repo, number = parsed[0].lower(), parsed[1]
    found = [
        p
        for p in store.list_prs(fid)
        if p.number == number and (not repo or p.repo.lower() == repo)
    ]
    mine = [p for p in found if project and p.project_slug == project]
    pool = mine or found
    return max(pool, key=lambda p: p.created_at) if pool else None


def pr_id_of(rec: Recorder, value: Any, project: str | None = None) -> str | None:
    """
    The ``factory_prs`` row id an event's ``pr_id`` names.

    A row id passes as is (no read); a number or ``owner/repo#n`` is looked up.
    """
    found = row_id(value)
    if found is not None:
        return found
    pr = pr_for_ref(rec.store, rec.factory_id, value, project=project)
    return pr.id if pr is not None else None


def _on_factory_started(rec: Recorder, e: FactoryEvent) -> None:
    for slug in _data(e).get("projects") or []:
        if isinstance(slug, str):
            rec.ensure_project(slug, e.ts)


def _on_project_added(rec: Recorder, e: FactoryEvent) -> None:
    rec.ensure_project(e.project_slug, e.ts)


def _on_stage(rec: Recorder, e: FactoryEvent) -> None:
    slug, data = e.project_slug, _data(e)
    to, before = _str(data.get("to")), _str(data.get("from"))
    if not slug or not to:
        return
    rec.ensure_project(slug, e.ts)
    rec.episode("stage", to, stage_key(slug, e.ts), e.ts, {"from": before}, project_slug=slug)
    if to in TERMINAL_STAGES:
        outcome, end = OK, e.ts
    elif before in TERMINAL_STAGES:
        outcome, end = OPEN, None
    else:
        return
    rec.span(
        kind="project",
        name=slug,
        dedupe_key=project_key(slug),
        started_at=e.ts,
        ended_at=end,
        outcome=outcome,
        reopen=True,
        project_slug=slug,
    )


def _on_project_closed(outcome: str) -> Callable[[Recorder, FactoryEvent], None]:
    def handle(rec: Recorder, e: FactoryEvent) -> None:
        slug = e.project_slug
        if not slug:
            return
        rec.ensure_project(slug, e.ts)
        close_open(
            rec.store, rec.factory_id, "stage", ended_at=e.ts, outcome=outcome, project_slug=slug
        )
        rec.span(
            kind="project",
            name=slug,
            dedupe_key=project_key(slug),
            started_at=e.ts,
            ended_at=e.ts,
            outcome=outcome,
            project_slug=slug,
        )

    return handle


def _pr_fields(rec: Recorder, e: FactoryEvent) -> tuple[str, dict[str, Any]] | None:
    pr_id = pr_id_of(rec, _data(e).get("pr_id"), e.project_slug)
    if pr_id is None:
        return None
    parent = rec.store.get_span_by_dedupe(rec.factory_id, pr_key(pr_id))
    return pr_id, {
        "pr_id": pr_id,
        "project_slug": e.project_slug,
        "parent_id": parent.id if parent else None,
    }


def _on_pr_ready(rec: Recorder, e: FactoryEvent) -> None:
    found = _pr_fields(rec, e)
    if found is None:
        return
    pr_id, fields = found
    row = rec.store.get_pr(pr_id)
    head = row.head_sha if row is not None and row.head_sha else None
    close_open(
        rec.store, rec.factory_id, "ready_wait", ended_at=e.ts, outcome=SUPERSEDED, pr_id=pr_id
    )
    rec.span(
        kind="ready_wait",
        name="ready",
        dedupe_key=ready_wait_key(pr_id, e.ts),
        started_at=e.ts,
        head_sha=head,
        **fields,
    )


def _on_pr_unready(outcome: str) -> Callable[[Recorder, FactoryEvent], None]:
    def handle(rec: Recorder, e: FactoryEvent) -> None:
        pr_id = pr_id_of(rec, _data(e).get("pr_id"), e.project_slug)
        if pr_id:
            close_open(
                rec.store,
                rec.factory_id,
                "ready_wait",
                ended_at=e.ts,
                outcome=outcome,
                pr_id=pr_id,
            )

    return handle


def _decision_row(rec: Recorder, e: FactoryEvent) -> Any:
    """The decision an event is about: ``data.decision_id``, else its ``decision:D-n`` subject."""
    decision_id = row_id(_data(e).get("decision_id"))
    if decision_id is not None:
        return rec.store.get_decision(decision_id)
    ref = (e.subject or "").removeprefix("decision:")
    if ref.startswith("D-") and ref[2:].isdigit():
        return rec.store.get_decision_by_number(rec.factory_id, int(ref[2:]))
    return None


def decision_span(
    rec: Recorder,
    row: Any,
    *,
    started_at: int,
    ended_at: int | None = None,
    outcome: str | None = None,
    by: str | None = None,
    pr_id: Any = None,
) -> None:
    """
    Write the span of one decision (``factory_decisions`` row).

    :param pr_id: The PR an event names (``data.pr_id``); else the PR is
        resolved from the decision (:func:`decision_pr_id`).
    """
    closed = outcome is not None and outcome != OPEN
    linked = pr_id_of(rec, pr_id, row.project_slug) if pr_id else None
    rec.span(
        kind="decision",
        name=row.ref,
        dedupe_key=decision_key(row.id),
        started_at=started_at,
        ended_at=ended_at,
        outcome=outcome,
        project_slug=row.project_slug,
        pr_id=linked or decision_pr_id(rec, row),
        session_id=row.asked_by_session_id,
        actor=by if outcome == OK else None,
        attrs={
            "source": row.source,
            "blocking": row.blocking,
            **({"subject": row.subject} if row.subject else {}),
            **({"answered_by": by} if closed and by else {}),
        },
    )


def decision_pr_id(rec: Recorder, row: Any) -> str | None:
    """
    The ``factory_prs`` row id a decision is about, or ``None``.

    From its ``pr:<ref>`` subject (agents write a number as often as a row id),
    else the PR a ``pr_closed`` decision asks about. A subject naming no PR
    of the factory links nothing; the span keeps the subject in its attrs.
    """
    subject = (row.subject or "").strip()
    if subject[:3].lower() == "pr:":
        pr = pr_for_ref(rec.store, rec.factory_id, subject[3:], project=row.project_slug)
        return pr.id if pr is not None else None
    key = row.dedupe_key or ""
    if row.source == "pr_closed" and key.startswith("pr_closed:"):
        return row_id(key.removeprefix("pr_closed:"))
    return None


def _on_decision_opened(rec: Recorder, e: FactoryEvent) -> None:
    row = _decision_row(rec, e)
    if row is not None:
        decision_span(rec, row, started_at=e.ts, pr_id=_data(e).get("pr_id"))


def _on_decision_closed(outcome: str) -> Callable[[Recorder, FactoryEvent], None]:
    def handle(rec: Recorder, e: FactoryEvent) -> None:
        row = _decision_row(rec, e)
        if row is None:
            return
        started = row.created_at * 1000 if row.created_at else e.ts
        by = _str(_data(e).get("by")) or row.answered_by
        decision_span(
            rec, row, started_at=min(started, e.ts), ended_at=e.ts, outcome=outcome, by=by
        )

    return handle


def _child_start(rec: Recorder, session_id: str, ts: int) -> int:
    conv = rec.deps.conversation_store.get_conversation(session_id)
    created = session_start_ms(conv) if conv is not None else None
    return min(created, ts) if created is not None else ts


def _on_dispatch(rec: Recorder, e: FactoryEvent) -> None:
    child = e.to_id
    if not child:
        return
    from omnigent.server.factory.telemetry import cost

    conv = rec.deps.conversation_store.get_conversation(child)
    who = cost.attribution(rec.deps, conv) if conv is not None else None
    data = _data(e)
    rec.span(
        kind="task",
        name=_title(e.summary) or child,
        dedupe_key=task_key(child),
        started_at=e.ts,
        project_slug=e.project_slug,
        session_id=child,
        role=who.role if who is not None else None,
        agent_name=_str(data.get("agent")),
        attrs={"parent_session_id": e.from_id},
    )


def _on_task_closed(outcome: str) -> Callable[[Recorder, FactoryEvent], None]:
    def handle(rec: Recorder, e: FactoryEvent) -> None:
        child = e.to_id
        if not child:
            return
        title = _title(e.summary).removesuffix(" finished").removesuffix(" failed")
        rec.span(
            kind="task",
            name=title or child,
            dedupe_key=task_key(child),
            started_at=_child_start(rec, child, e.ts),
            ended_at=e.ts,
            outcome=outcome,
            force=True,
            project_slug=e.project_slug,
            session_id=child,
        )

    return handle


def state_attrs(data: Mapping[str, Any]) -> dict[str, Any]:
    """:returns: The attributes an env or preview state episode keeps from its report."""
    return {"env_id": _str(data.get("env_id")), "sha": _str(data.get("sha"))}


def _on_env(rec: Recorder, e: FactoryEvent) -> None:
    slug, data = e.project_slug, _data(e)
    state = _str(data.get("state"))
    if not slug or not state:
        return
    rec.episode("env", state, env_key(slug, e.ts), e.ts, state_attrs(data), project_slug=slug)


def _on_preview(rec: Recorder, e: FactoryEvent) -> None:
    data = _data(e)
    pr_id, state = pr_id_of(rec, data.get("pr_id"), e.project_slug), _str(data.get("state"))
    if not pr_id or not state:
        return
    attrs = state_attrs(data)
    rec.episode(
        "preview",
        state,
        preview_key(pr_id, e.ts),
        e.ts,
        attrs,
        pr_id=pr_id,
        project_slug=e.project_slug,
    )


def demo_span(rec: Recorder, demo: Any, published_ms: int) -> None:
    """Write the span of one recorded demo (``factory_demos`` row)."""
    manifest = demo.manifest if isinstance(demo.manifest, dict) else {}
    duration = manifest.get("duration_s")
    run_ms = (
        int(float(duration) * 1000) if isinstance(duration, int | float) and duration > 0 else 0
    )
    rec.span(
        kind="demo",
        name=f"demo #{demo.pr_number}",
        dedupe_key=demo_key(demo.id),
        started_at=published_ms - run_ms,
        ended_at=published_ms,
        outcome=OK if demo.status in ("PASS", "PARTIAL") else FAIL,
        project_slug=demo.project_slug or None,
        pr_id=demo.pr_id,
        head_sha=demo.head_sha or None,
        session_id=demo.created_by_session or None,
        attrs={"status": demo.status, "demo_id": demo.id},
    )


def _on_demo(rec: Recorder, e: FactoryEvent) -> None:
    demo_id = row_id(_data(e).get("demo_id"))
    demo = rec.store.get_demo(demo_id) if demo_id else None
    if demo is not None:
        demo_span(rec, demo, e.ts)


def _on_needs_human(rec: Recorder, e: FactoryEvent) -> None:
    rec.span(
        kind="incident",
        name="needs_human",
        dedupe_key=incident_key(e.id),
        started_at=e.ts,
        project_slug=e.project_slug,
        session_id=e.from_id,
        attrs={"rule": "needs_human", "reason": _str(_data(e).get("reason"))},
    )


def _on_needs_human_cleared(rec: Recorder, e: FactoryEvent) -> None:
    if e.from_id:
        close_open(
            rec.store,
            rec.factory_id,
            "incident",
            ended_at=e.ts,
            name="needs_human",
            session_id=e.from_id,
        )


def _issue_ref(data: Mapping[str, Any]) -> str | None:
    return _str(data.get("issue_id")) or _str(data.get("id"))


def _on_issue(rec: Recorder, e: FactoryEvent) -> None:
    data = _data(e)
    ref = _issue_ref(data)
    if ref is None or data.get("class") not in (None, "incident"):
        return
    resolved = e.kind == "issue.resolved"
    rule = _str(data.get("rule")) or "incident"
    rec.span(
        kind="incident",
        name=rule,
        dedupe_key=incident_key(ref),
        started_at=e.ts,
        ended_at=e.ts if resolved else None,
        outcome=OK if resolved else OPEN,
        project_slug=e.project_slug,
        attrs={"rule": rule, "issue_id": ref},
    )


def _on_control(rec: Recorder, e: FactoryEvent) -> None:
    data = _data(e)
    action = e.kind.removeprefix("control.")
    rec.span(
        kind="intervention",
        name=action,
        dedupe_key=intervention_key(e.id),
        started_at=e.ts,
        ended_at=e.ts,
        outcome=OK,
        project_slug=e.project_slug,
        session_id=e.to_id,
        actor=_str(data.get("by")) or _str(data.get("from")),
        attrs={"action": action, "target": _str(data.get("target"))},
    )


PROPOSAL_OUTCOMES: Final[dict[str, str]] = {
    "applied": OK,
    "kept": OK,
    "trial_ended": OK,
    "reverted": FAIL,
    "rejected": CANCELLED,
    "withdrawn": CANCELLED,
    "refused": CANCELLED,
    "undone": CANCELLED,
    "expired": TIMEOUT,
}


def _on_proposal(rec: Recorder, e: FactoryEvent) -> None:
    data = _data(e)
    ref = _str(data.get("proposal_id")) or (str(data["number"]) if data.get("number") else None)
    if ref is None:
        return
    status = e.kind.removeprefix("proposal.")
    outcome = PROPOSAL_OUTCOMES.get(status)
    if status != "opened" and outcome is None:
        return
    number = data.get("number")
    rec.span(
        kind="proposal",
        name=f"C-{number}" if number else "proposal",
        dedupe_key=proposal_key(ref),
        started_at=e.ts,
        ended_at=e.ts if outcome else None,
        outcome=outcome or OPEN,
        force=outcome is not None,
        project_slug=e.project_slug,
        actor=_str(data.get("by")),
        attrs={"status": status, "proposal_id": ref},
    )


Handler = Callable[[Recorder, FactoryEvent], None]

_HANDLERS: Final[dict[str, Handler]] = {
    "factory.started": _on_factory_started,
    "project.added": _on_project_added,
    "stage.changed": _on_stage,
    "project.cancelled": _on_project_closed(CANCELLED),
    "project.merged": _on_project_closed(SUPERSEDED),
    "pr.ready": _on_pr_ready,
    "pr.not_ready": _on_pr_unready(SUPERSEDED),
    "pr.merged": _on_pr_unready(OK),
    "pr.closed": _on_pr_unready(ABANDONED),
    "decision.opened": _on_decision_opened,
    "decision.answered": _on_decision_closed(OK),
    "decision.expired": _on_decision_closed(TIMEOUT),
    "decision.withdrawn": _on_decision_closed(CANCELLED),
    "dispatch": _on_dispatch,
    "task.done": _on_task_closed(OK),
    "task.failed": _on_task_closed(FAIL),
    "env.reported": _on_env,
    "preview.reported": _on_preview,
    "preview.parked": _on_preview,
    "demo.published": _on_demo,
    "needs_human": _on_needs_human,
    "needs_human.cleared": _on_needs_human_cleared,
    "issue.opened": _on_issue,
    "issue.resolved": _on_issue,
}


def handler_for(kind: str) -> Handler | None:
    """:returns: The span writer for an event kind, or ``None`` when it makes no span."""
    found = _HANDLERS.get(kind)
    if found is not None:
        return found
    if kind.startswith("control.") and kind != "control.ack":
        return _on_control
    if kind.startswith("proposal."):
        return _on_proposal
    return None


def record_sync(deps: FactoryDeps, event: FactoryEvent, ctx: SpanContext | None = None) -> None:
    """Write the spans of one event (synchronous; heal replays through here too)."""
    handler = handler_for(event.kind)
    if handler is None:
        return
    context = ctx if ctx is not None else span_context(deps, event.factory_id)
    handler(Recorder(deps, event.factory_id, context), event)


async def record_event(app_state: Any, event: FactoryEvent) -> None:
    """The ``events.add_listener`` recorder; a failure is logged and swallowed."""
    if handler_for(event.kind) is None:
        return
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps):
        return
    try:
        await asyncio.to_thread(record_sync, deps, event)
    except SpanFieldError as exc:
        _logger.warning(
            "factory spans: could not record %s (event %s): %s=%r is not a row id",
            event.kind,
            event.id,
            exc.field,
            exc.value,
            exc_info=True,
        )
    except Exception:  # noqa: BLE001 - heal rebuilds what a failed write missed
        _logger.warning(
            "factory spans: could not record %s (event %s)", event.kind, event.id, exc_info=True
        )


def reset() -> None:
    """Forget which project spans this process already ensured (tests)."""
    _known.clear()


__all__ = [
    "PROPOSAL_OUTCOMES",
    "Recorder",
    "decision_pr_id",
    "decision_span",
    "demo_span",
    "handler_for",
    "pr_for_ref",
    "pr_id_of",
    "record_event",
    "record_sync",
    "reset",
    "row_id",
    "state_attrs",
]
