"""Mirror factory events into the Room as cards.

An :func:`~omnigent.server.factory.events.add_listener` hook: each event below
writes a system row (``author_kind=system``) carrying ``card {type, ref, id}``
that the cockpit renders as a live card of that object. System rows are never
routed to agents; a responder only sees them as thread context.

==============================================  =============================  =========
Event                                           Thread                         Card
==============================================  =============================  =========
``decision.opened/answered/escalated``          its thread, else its project,  decision
                                                else ``factory``
``proposal.*``                                  its thread, else ``factory``   proposal
                                                (projects) / ``improvements``
``control.*``                                   its project, else ``factory``  control
``pr.discovered/ready/merged/closed/            ``pr:<id>`` plus a one-liner   pr
human_review``                                  in its project
``demo.published``                              ``pr:<id>``                    demo
``loop.exhausted/stalled/thrashing/escalated``  its project, else ``factory``  loop
``issue.opened`` (warning or worse; findings    its project, else ``factory``  issue
and retros always)                              (findings: ``improvements``)
``people.*``, ``base.moved``,                   ``factory`` (a spawn also in   system
``factory.started``, ``member.spawned``,        its project; project lifecycle
``project.*`` lifecycle                         only there)
==============================================  =============================  =========

A card row's id derives from the event id and thread, so a repeated event
delivery writes nothing twice.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from omnigent.factory import (
    ISSUE_SEVERITIES,
    THREAD_FACTORY,
    THREAD_IMPROVEMENTS,
    thread_kind,
)
from omnigent.server.factory import events, live
from omnigent.server.factory.actor import (
    decision_ref,
    pr_ref,
    pr_thread,
    project_thread,
    proposal_ref,
)
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.stores.factory_store import norm_id

_logger = logging.getLogger(__name__)

DECISION_KINDS: Final = frozenset({"decision.opened", "decision.answered", "decision.escalated"})
PR_KINDS: Final = frozenset(
    {"pr.discovered", "pr.ready", "pr.merged", "pr.closed", "pr.human_review"}
)
LOOP_KINDS: Final = frozenset(
    {"loop.exhausted", "loop.stalled", "loop.thrashing", "loop.escalated"}
)
FACTORY_LINE_KINDS: Final = frozenset({"base.moved", "factory.started", "member.spawned"})
PROJECT_LINE_KINDS: Final = frozenset(
    {
        "project.cancelled",
        "project.split",
        "project.merged",
        "project.held",
        "project.released",
        "member.spawned",
    }
)
_QUIET_KINDS: Final = frozenset({"people.drift_repaired"})
_WARNING: Final = ISSUE_SEVERITIES.index("warning")


@dataclass(frozen=True)
class CardRow:
    """
    One system row to write.

    :param thread: Thread key.
    :param body: One line, e.g. the event summary.
    :param card: ``{type, ref, id}``.
    """

    thread: str
    body: str
    card: dict[str, Any]


def _card(kind: str, ref: str | None = None, row_id: str | None = None) -> dict[str, Any]:
    return {"type": kind, "ref": ref, "id": norm_id(row_id) if row_id else None}


def _pr_of(deps: FactoryDeps, event: Any) -> Any:
    """The PR an event is about: ``data.pr_id``, else its ``pr:<repo>#<n>`` subject."""
    store = deps.factory_store
    pr_id = (event.data or {}).get("pr_id")
    if isinstance(pr_id, str) and pr_id:
        try:
            pr = store.get_pr(pr_id)
        except ValueError:
            pr = None
        if pr is not None:
            return pr
    subject = event.subject or ""
    if subject.startswith("pr:") and "#" in subject:
        repo, _, number = subject[3:].rpartition("#")
        if number.isdigit():
            return store.get_pr_by_number(event.factory_id, repo, int(number))
    return None


def _project_or_factory(slug: str | None) -> str:
    return project_thread(slug) if slug else THREAD_FACTORY


def _decision_rows(deps: FactoryDeps, event: Any) -> list[CardRow]:
    from omnigent.server.factory.proposals import is_mirror

    decision_id = (event.data or {}).get("decision_id")
    decision = deps.factory_store.get_decision(decision_id) if decision_id else None
    if decision is None or is_mirror(decision):
        # A proposal's decision shows as the proposal's own card.
        return []
    slug = decision.project_slug or event.project_slug
    thread = decision.thread if decision.thread and thread_kind(decision.thread) else None
    thread = thread or _project_or_factory(slug)
    ref = decision_ref(decision.number)
    return [CardRow(thread, event.summary, _card("decision", ref, decision.id))]


def _proposal_rows(deps: FactoryDeps, event: Any) -> list[CardRow]:
    from omnigent.server.factory.proposals import find_proposal

    store = deps.factory_store
    data = event.data or {}
    row = None
    if isinstance(data.get("proposal_id"), str):
        row = store.get_proposal(data["proposal_id"])
    if row is None and (event.subject or "").startswith("proposal:"):
        row = find_proposal(deps, event.factory_id, event.subject.removeprefix("proposal:"))
    if row is None:
        return []
    default = THREAD_FACTORY if row.kind == "projects" else THREAD_IMPROVEMENTS
    thread = row.thread if row.thread and thread_kind(row.thread) else default
    return [CardRow(thread, event.summary, _card("proposal", proposal_ref(row.number), row.id))]


def _pr_rows(deps: FactoryDeps, event: Any, card_type: str = "pr") -> list[CardRow]:
    pr = _pr_of(deps, event)
    if pr is None:
        return []
    ref = pr_ref(pr.number)
    row_id = (event.data or {}).get("demo_id") if card_type == "demo" else pr.id
    rows = [CardRow(pr_thread(norm_id(pr.id)), event.summary, _card(card_type, ref, row_id))]
    slug = pr.project_slug or event.project_slug
    if card_type == "pr" and slug:
        rows.append(CardRow(project_thread(slug), event.summary, _card("system", ref, pr.id)))
    return rows


def _issue_rows(event: Any) -> list[CardRow]:
    data = event.data or {}
    severity = data.get("severity", event.severity)
    rank = ISSUE_SEVERITIES.index(severity) if severity in ISSUE_SEVERITIES else 0
    issue_class = data.get("class")
    if issue_class in ("finding", "retro"):
        thread = THREAD_IMPROVEMENTS
    elif rank >= _WARNING:
        thread = _project_or_factory(event.project_slug)
    else:
        return []
    ref = data.get("ref") if isinstance(data.get("ref"), str) else None
    return [CardRow(thread, event.summary, _card("issue", ref, data.get("issue_id")))]


def plan_cards(deps: FactoryDeps, event: Any) -> list[CardRow]:
    """
    The rows one event mirrors into the Room (store reads only).

    :returns: ``[]`` for events the Room does not show.
    """
    kind = event.kind
    if kind.startswith("chat.") or kind in _QUIET_KINDS:
        return []
    if kind in DECISION_KINDS:
        return _decision_rows(deps, event)
    if kind.startswith("proposal."):
        return _proposal_rows(deps, event)
    if kind.startswith("control."):
        card = _card("control", None, event.id)
        return [CardRow(_project_or_factory(event.project_slug), event.summary, card)]
    if kind in PR_KINDS:
        return _pr_rows(deps, event)
    if kind == "demo.published":
        return _pr_rows(deps, event, "demo")
    if kind in LOOP_KINDS:
        data = event.data or {}
        ref = data.get("ref") if isinstance(data.get("ref"), str) else None
        card = _card("loop", ref, data.get("loop_id"))
        return [CardRow(_project_or_factory(event.project_slug), event.summary, card)]
    if kind == "issue.opened":
        return _issue_rows(event)
    rows: list[CardRow] = []
    if kind.startswith("people.") or kind in FACTORY_LINE_KINDS:
        rows.append(CardRow(THREAD_FACTORY, event.summary, _card("system")))
    if kind in PROJECT_LINE_KINDS and event.project_slug:
        rows.append(CardRow(project_thread(event.project_slug), event.summary, _card("system")))
    return rows


async def mirror_event(app_state: Any, event: Any) -> None:
    """Listener: write the event's card rows (never raises)."""
    from omnigent.server.factory import chat

    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps):
        return
    try:
        rows = await asyncio.to_thread(plan_cards, deps, event)
        for row in rows:
            await chat.post_system(
                app_state,
                event.factory_id,
                row.thread,
                row.body,
                card=row.card,
                message_id=chat.derived_id(event.id, row.thread),
                announce=False,
            )
    except Exception:  # noqa: BLE001 - a card is a mirror; the event itself is stored
        _logger.warning("factory room: could not mirror %s", event.kind, exc_info=True)
        return
    if rows:
        live.announce_factory_changed(app_state, event.factory_id, [chat.SCOPE])


def register() -> Callable[[], None] | None:
    """Mirror events into the Room from now on (idempotent)."""
    if mirror_event in events._listeners:
        return None
    return events.add_listener(mirror_event)


register()


__all__ = [
    "DECISION_KINDS",
    "LOOP_KINDS",
    "PR_KINDS",
    "CardRow",
    "mirror_event",
    "plan_cards",
    "register",
]
