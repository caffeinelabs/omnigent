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
``issue.opened/escalated`` (warning or worse;   its project, else ``factory``  issue
findings and retros always)                     (findings: ``improvements``)
``people.*``, ``base.moved``,                   ``factory`` (a spawn also in   system
``factory.started``, ``member.spawned``,        its project; project lifecycle
``project.*`` lifecycle                         only there)
==============================================  =============================  =========

``proposal.trial_extended`` is a plain line in the proposal's thread. A card
row's id derives from the event id and thread, so a repeated event delivery
writes nothing twice.

Two kinds update a row in place instead of adding one, so a long-running
incident or a busy base does not bury what people and agents say:

- An incident has one live card per thread (``issue.opened``,
  ``issue.escalated``). A comeback after the server cleared it less than
  :data:`REPOST_AFTER_S` ago is the same incident flapping: the card's line
  takes the latest value and its meta counts the comeback. The card is posted
  again when the severity rises above what it showed, when it comes back
  after a person resolved it or after staying clear that long, and for every
  person's report.
- ``base.moved`` joins the newest line of its base while nothing else was said
  after it, or while its first move is under :data:`BASE_BATCH_S` old: "base
  main moved 7 times since 13:12 UTC → 0c8a4e1", each move kept in the meta.
  Every move is still its own event (Activity), and the PLs on that base are
  mailed as before.

Neither changes the events themselves, so Slack, the digest and the Now card
read what they always read.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

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
from omnigent.server.factory.issues import ESCALATED
from omnigent.stores.factory_store import norm_id

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryChatMessage
    from omnigent.stores.factory_store import FactoryStore

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
PROPOSAL_LINE_KIND: Final = "proposal.trial_extended"
"""A proposal event the Room shows as a line (the proposal card is already in the thread)."""
ISSUE_KINDS: Final = frozenset({"issue.opened", ESCALATED})
BASE_MOVED: Final = "base.moved"
_WARNING: Final = ISSUE_SEVERITIES.index("warning")

REPOST_AFTER_S: Final = 3600
"""An incident the server cleared that comes back sooner is flapping: its card updates in place."""
BASE_BATCH_S: Final = 600
"""Moves of one base this soon after a line's first move join that line."""
BASE_BATCH_MAX: Final = 50
"""Moves one base line holds."""
BASE_SCAN: Final = 60
"""Newest rows of a thread searched for the line a base move joins."""
_EVENTS_KEPT: Final = 10
"""Event ids a live card remembers, so a repeated delivery changes nothing."""
_LOCKS_ATTR: Final = "factory_room_card_locks"


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


def card_meta(row: FactoryChatMessage) -> dict[str, Any]:
    """:returns: The ``meta`` a live card or batched line keeps in its card (``{}`` for others)."""
    meta = (row.card or {}).get("meta")
    return dict(meta) if isinstance(meta, Mapping) else {}


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


_CARD_KINDS: Final = (
    DECISION_KINDS | PR_KINDS | LOOP_KINDS | FACTORY_LINE_KINDS | PROJECT_LINE_KINDS | ISSUE_KINDS
) | {"demo.published"}
_CARD_PREFIXES: Final = ("proposal.", "control.", "people.")


def shows(kind: str) -> bool:
    """:returns: Whether events of *kind* may become cards (no I/O)."""
    if kind in _QUIET_KINDS:
        return False
    return kind in _CARD_KINDS or kind.startswith(_CARD_PREFIXES)


def plan_cards(deps: FactoryDeps, event: Any) -> list[CardRow]:
    """
    The rows one event mirrors into the Room (store reads only).

    :returns: ``[]`` for events the Room does not show.
    """
    kind = event.kind
    if not shows(kind):
        return []
    if kind in DECISION_KINDS:
        return _decision_rows(deps, event)
    if kind == PROPOSAL_LINE_KIND:
        # One quiet line in its thread, not another copy of the proposal card.
        return [
            replace(r, card=_card("system", r.card["ref"], r.card["id"]))
            for r in _proposal_rows(deps, event)
        ]
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
    if kind in ISSUE_KINDS:
        return _issue_rows(event)
    rows: list[CardRow] = []
    if kind.startswith("people.") or kind in FACTORY_LINE_KINDS:
        rows.append(CardRow(THREAD_FACTORY, event.summary, _card("system")))
    if kind in PROJECT_LINE_KINDS and event.project_slug:
        rows.append(CardRow(project_thread(event.project_slug), event.summary, _card("system")))
    return rows


# ── Rows updated in place ──────────────────────────────────────


def _lock(app_state: Any, factory_id: str, thread: str) -> asyncio.Lock:
    """The lock that serialises read-modify-writes of one thread's live rows."""
    locks = getattr(app_state, _LOCKS_ATTR, None)
    if not isinstance(locks, dict):
        locks = {}
        setattr(app_state, _LOCKS_ATTR, locks)
    return locks.setdefault((norm_id(factory_id), thread), asyncio.Lock())


def _rank(severity: object) -> int:
    return ISSUE_SEVERITIES.index(severity) if severity in ISSUE_SEVERITIES else 0


def issue_card_id(issue_id: str, thread: str, post: int = 0) -> str:
    """:returns: The row id of an incident's *post*-th card in *thread* (0: the first)."""
    from omnigent.server.factory.chat import derived_id

    parts = ("issue-card", norm_id(issue_id), thread)
    return derived_id(*parts) if post == 0 else derived_id(*parts, str(post))


def live_issue_card(
    store: FactoryStore, issue_id: str, thread: str
) -> tuple[FactoryChatMessage | None, FactoryChatMessage | None]:
    """
    An incident's cards in a thread (store reads only).

    The first card keeps ``posts``, how many times the card was posted again.

    :returns: ``(first card, live card)``; ``(None, None)`` before any.
    """
    first = store.get_chat_message(issue_card_id(issue_id, thread))
    if first is None:
        return None, None
    posts = card_meta(first).get("posts")
    if not isinstance(posts, int) or posts <= 0:
        return first, first
    return first, store.get_chat_message(issue_card_id(issue_id, thread, posts)) or first


def repost_reason(meta: Mapping[str, Any], event: Any) -> str | None:
    """
    Why an incident event posts its card again rather than updating the live one.

    :param meta: The live card's meta.
    :returns: ``"severity"``, ``"report"`` or ``"reopened"``; ``None`` to update in place.
    """
    data = event.data or {}
    if _rank(data.get("severity", event.severity)) > _rank(meta.get("severity")):
        return "severity"
    if data.get("by_kind") == "human" and event.kind == "issue.opened":
        return "report"
    if event.kind != "issue.opened" or not data.get("reopened"):
        return None
    by = data.get("was_resolved_by")
    if isinstance(by, str) and by and by != "server":
        return "reopened"
    at = data.get("was_resolved_at")
    if not isinstance(at, int) or event.ts // 1000 - at >= REPOST_AFTER_S:
        return "reopened"
    return None


def _remember(meta: Mapping[str, Any], event_id: str) -> list[str]:
    seen = [e for e in meta.get("events") or [] if isinstance(e, str)]
    return [*seen, event_id][-_EVENTS_KEPT:]


async def _write_issue_card(app_state: Any, deps: FactoryDeps, event: Any, row: CardRow) -> bool:
    """Post an incident's card or update its live one. :returns: Whether a row changed."""
    from omnigent.server.factory import chat

    store = deps.factory_store
    issue_id = str(row.card["id"])
    data = event.data or {}
    severity = data.get("severity", event.severity)
    async with _lock(app_state, event.factory_id, row.thread):
        first, current = await asyncio.to_thread(live_issue_card, store, issue_id, row.thread)
        meta = card_meta(current) if current is not None else {}
        if current is not None and event.id in (meta.get("events") or []):
            return False
        if current is not None and repost_reason(meta, event) is None:
            meta["events"] = _remember(meta, event.id)
            if data.get("reopened"):
                meta["reopened"] = int(meta.get("reopened") or 0) + 1
                meta["reopened_at"] = event.ts // 1000
            card = {**(current.card or {}), "meta": meta}
            await asyncio.to_thread(
                store.update_chat_message, current.id, body=row.body, card=card
            )
            return True
        post = 0
        if first is not None:
            posts = card_meta(first).get("posts")
            post = (posts if isinstance(posts, int) and posts > 0 else 0) + 1
        fresh = {
            "kind": "issue",
            "severity": severity if severity in ISSUE_SEVERITIES else "warning",
            "reopened": 0,
            "reopened_at": None,
            "events": [event.id],
        }
        await chat.post_system(
            app_state,
            event.factory_id,
            row.thread,
            row.body,
            card={**row.card, "meta": fresh},
            message_id=issue_card_id(issue_id, row.thread, post),
            announce=False,
        )
        if first is not None:
            first_meta = {**card_meta(first), "posts": post}
            await asyncio.to_thread(
                store.update_chat_message,
                first.id,
                card={**(first.card or {}), "meta": first_meta},
            )
        return True


def _sha(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def base_move(event: Any) -> dict[str, Any] | None:
    """:returns: One move as a base line keeps it, or ``None`` when the event lacks one."""
    data = event.data or {}
    repo, base, to = data.get("repo"), data.get("base"), _sha(data.get("to"))
    if not isinstance(repo, str) or not repo or not isinstance(base, str) or not base or not to:
        return None
    pr = data.get("pr")
    return {
        "event": event.id,
        "at": event.ts // 1000,
        "from_sha": _sha(data.get("from")),
        "to_sha": to,
        "pr": pr if isinstance(pr, int) and not isinstance(pr, bool) else None,
    }


def base_key(repo: str, base: str) -> str:
    """:returns: ``owner/name#branch``, the repo lowercased (one line per watched base)."""
    return f"{repo.lower()}#{base}"


def base_line_text(base: str, moves: Sequence[Mapping[str, Any]]) -> str:
    """
    The line of a batch of moves of *base*.

    :returns: ``base main moved 7f3e2a1 → 0c8a4e1 (#9630)`` for one move,
        ``base main moved 7 times since 13:12 UTC → 0c8a4e1`` for several.
    """
    last = moves[-1]
    to = str(last.get("to_sha") or "")[:7]
    if len(moves) == 1:
        before = str(last.get("from_sha") or "")[:7] or "?"
        via = f" (#{last['pr']})" if last.get("pr") else ""
        return f"base {base} moved {before} → {to}{via}"
    since = datetime.fromtimestamp(int(moves[0].get("at") or 0), UTC).strftime("%H:%M")
    return f"base {base} moved {len(moves)} times since {since} UTC → {to}"


def joinable_line(
    rows: Sequence[FactoryChatMessage], key: str, at: int
) -> FactoryChatMessage | None:
    """
    The base line a move of *key* at *at* joins.

    :param rows: The thread's newest rows, oldest first.
    :returns: The newest line of that base while nothing else was said after it
        (other bases' lines do not count) or its first move is under
        :data:`BASE_BATCH_S` old and it has room; else ``None`` (a new line).
    """
    others = False
    for row in reversed(rows):
        meta = card_meta(row)
        if meta.get("kind") != BASE_MOVED:
            others = True
            continue
        if meta.get("key") != key:
            continue
        moves = meta.get("moves") or []
        if not moves or len(moves) >= BASE_BATCH_MAX:
            return None
        first = moves[0].get("at") if isinstance(moves[0], Mapping) else None
        first_at = first if isinstance(first, int) else row.created_at
        return row if not others or at - first_at < BASE_BATCH_S else None
    return None


def _holds(row: FactoryChatMessage, key: str, event_id: str) -> bool:
    """Whether *row* is a line of *key* that already lists the move of *event_id*."""
    meta = card_meta(row)
    if meta.get("kind") != BASE_MOVED or meta.get("key") != key:
        return False
    moves = meta.get("moves") or []
    return any(isinstance(m, Mapping) and m.get("event") == event_id for m in moves)


async def _write_base_line(
    app_state: Any, deps: FactoryDeps, event: Any, row: CardRow, move: dict[str, Any]
) -> bool:
    """Add a base move to its line, or start a line. :returns: Whether a row changed."""
    from omnigent.server.factory import chat

    store = deps.factory_store
    data = event.data or {}
    repo, base = str(data["repo"]), str(data["base"])
    key = base_key(repo, base)
    async with _lock(app_state, event.factory_id, row.thread):
        recent = await asyncio.to_thread(
            lambda: store.list_chat(event.factory_id, row.thread, limit=BASE_SCAN)
        )
        if any(_holds(r, key, event.id) for r in recent):
            return False
        line = joinable_line(recent, key, move["at"])
        if line is not None:
            meta = card_meta(line)
            moves = [m for m in meta.get("moves") or [] if isinstance(m, Mapping)]
            moves.append(move)
            meta["moves"] = moves
            await asyncio.to_thread(
                store.update_chat_message,
                line.id,
                body=base_line_text(base, moves),
                card={**(line.card or {}), "meta": meta},
            )
            return True
        meta = {"kind": BASE_MOVED, "key": key, "repo": repo, "base": base, "moves": [move]}
        await chat.post_system(
            app_state,
            event.factory_id,
            row.thread,
            row.body,
            card={**row.card, "meta": meta},
            message_id=chat.derived_id(event.id, row.thread),
            announce=False,
        )
        return True


async def _write(app_state: Any, deps: FactoryDeps, event: Any, row: CardRow) -> bool:
    """Write one planned row (new, or merged into a live one). :returns: Whether a row changed."""
    from omnigent.server.factory import chat

    if event.kind in ISSUE_KINDS and row.card.get("type") == "issue" and row.card.get("id"):
        return await _write_issue_card(app_state, deps, event, row)
    if event.kind == BASE_MOVED:
        move = base_move(event)
        if move is not None:
            return await _write_base_line(app_state, deps, event, row, move)
    await chat.post_system(
        app_state,
        event.factory_id,
        row.thread,
        row.body,
        card=row.card,
        message_id=chat.derived_id(event.id, row.thread),
        announce=False,
    )
    return True


async def mirror_event(app_state: Any, event: Any) -> None:
    """Listener: write the event's card rows (never raises)."""
    from omnigent.server.factory import chat

    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps) or not shows(event.kind):
        return
    changed = False
    try:
        rows = await asyncio.to_thread(plan_cards, deps, event)
        for row in rows:
            changed = await _write(app_state, deps, event, row) or changed
    except Exception:  # noqa: BLE001 - a card is a mirror; the event itself is stored
        _logger.warning("factory room: could not mirror %s", event.kind, exc_info=True)
    if changed:
        live.announce_factory_changed(app_state, event.factory_id, [chat.SCOPE])


def register() -> Callable[[], None] | None:
    """Mirror events into the Room from now on (idempotent)."""
    if mirror_event in events._listeners:
        return None
    return events.add_listener(mirror_event)


register()


__all__ = [
    "BASE_BATCH_MAX",
    "BASE_BATCH_S",
    "DECISION_KINDS",
    "ISSUE_KINDS",
    "LOOP_KINDS",
    "PROPOSAL_LINE_KIND",
    "PR_KINDS",
    "REPOST_AFTER_S",
    "CardRow",
    "base_line_text",
    "base_move",
    "card_meta",
    "issue_card_id",
    "joinable_line",
    "live_issue_card",
    "mirror_event",
    "plan_cards",
    "register",
    "repost_reason",
    "shows",
]
