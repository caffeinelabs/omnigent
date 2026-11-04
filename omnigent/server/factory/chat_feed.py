"""
A node's chat as a conversation, with the system's activity apart.

Cockpit v3's node chat shows what people and agents say, plus the live cards
that need the person reading it (an open ask they answer, a merge they make,
an offer, the "why" they asked for, a verb they can still undo). Everything
else the Room mirrors from events is activity: base moves, members spawned,
incidents, loops, controls, PR and demo news, closed alarms, and every
decision or proposal that is answered, applied, closed or someone else's.
Activity never shows inline; it folds into one "Activity · n since you
looked" line that opens it as a list. Resolved incidents and closed or
applied proposals are never conversation.

:func:`classify` sorts one row by the live state of what its card shows (the
open decisions, proposals and PRs read once per request: :class:`Live`). :func:`feed_page` is
``GET …/chat?thread=&feed=conversation`` (the chat: people, agents and the
live cards for this reader) and ``feed=activity`` (the list the "Activity"
line opens): ``limit`` rows of that feed with ``before_seq`` (or ``after_seq``)
to read on, ``unread`` (conversation from others) and ``activity_since`` after
this reader's read mark in the thread. :func:`since` is the node page's
``thread.unread`` and ``thread.activity``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal

from omnigent.factory import PROPOSAL_OPEN_STATUSES

if TYPE_CHECKING:
    from omnigent.entities.factory import (
        FactoryChatMessage,
        FactoryDecision,
        FactoryIssue,
        FactoryPr,
        FactoryProposal,
    )
    from omnigent.server.factory.context import FactoryDeps
    from omnigent.server.factory.your_move import Viewer

Lane = Literal["conversation", "activity"]
Feed = Literal["conversation", "activity", "all"]
CONVERSATION: Final = "conversation"
ACTIVITY: Final = "activity"
ALL: Final = "all"
SCAN_BATCH: Final = 200
"""Rows read per store call while a page fills."""
SCAN_MAX: Final = 1000
"""Rows one page reads at most (a thread of activity only answers in steps)."""
SINCE_CAP: Final = 99
"""Counts since the read mark stop here (the cockpit shows "99+")."""
_ASKING_CARDS: Final = frozenset({"why", "offer"})
"""Cards that answer or ask the reader directly: conversation while they stand."""
_CLOSED_ISSUES: Final = frozenset({"resolved", "wontfix"})


@dataclass(frozen=True)
class Sorted:
    """
    Where one row goes.

    :param kind: What it is (``message``, ``note``, a card type, or its meta
        kind such as ``base.moved``).
    :param needs_you: A live card this reader acts on.
    :param ask: The ask it carries (``D-45``, ``merge:main``): the node page's
        Your move may already show it.
    :param closed: The object it shows is answered, applied, closed or resolved.
    """

    feed: Lane
    kind: str
    needs_you: bool = False
    ask: str | None = None
    closed: bool = False


@dataclass
class Live:
    """
    The live state of what one request's cards show: the open decisions,
    proposals and PRs in one read each (a card whose object is not open is
    closed), and an incident per id only when a row's ``closed`` is shown.
    """

    deps: FactoryDeps
    factory_id: str
    viewer: Viewer
    now: float
    _decisions: dict[str, FactoryDecision] | None = None
    _by_proposal: dict[str, FactoryDecision] = field(default_factory=dict)
    _proposals: dict[str, FactoryProposal] | None = None
    _prs: dict[str, FactoryPr] | None = None
    _issues: dict[str, FactoryIssue | None] = field(default_factory=dict)

    def _open_decisions(self) -> dict[str, FactoryDecision]:
        if self._decisions is None:
            rows = self.deps.factory_store.list_decisions(
                factory_id=self.factory_id, status="open", limit=1000
            )
            self._decisions = {_key(d.id): d for d in rows}
            self._by_proposal = {
                str(d.subject).removeprefix("proposal:"): d
                for d in rows
                if d.source == "proposal" and str(d.subject or "").startswith("proposal:")
            }
        return self._decisions

    def decision(self, row_id: str | None) -> FactoryDecision | None:
        """The open decision *row_id* names (``None``: answered, withdrawn, expired or unknown)."""
        return self._open_decisions().get(_key(row_id))

    def proposal_decision(self, ref: str) -> FactoryDecision | None:
        """The open decision that asks a person to apply the proposal *ref* (``C-16``)."""
        self._open_decisions()
        return self._by_proposal.get(ref)

    def proposal(self, row_id: str | None) -> FactoryProposal | None:
        """The open proposal *row_id* names (``None``: applied, closed or unknown)."""
        if self._proposals is None:
            rows = self.deps.factory_store.list_proposals(
                self.factory_id, status=sorted(PROPOSAL_OPEN_STATUSES), limit=1000
            )
            self._proposals = {_key(p.id): p for p in rows}
        return self._proposals.get(_key(row_id))

    def pr(self, row_id: str | None) -> FactoryPr | None:
        """The open PR *row_id* names (``None``: merged, closed or unknown)."""
        if self._prs is None:
            rows = self.deps.factory_store.list_prs(self.factory_id, ["open"])
            self._prs = {_key(p.id): p for p in rows}
        return self._prs.get(_key(row_id))

    def issue(self, row_id: str | None) -> FactoryIssue | None:
        key = _key(row_id)
        if key and key not in self._issues:
            try:
                self._issues[key] = self.deps.factory_store.get_issue(key)
            except ValueError:
                self._issues[key] = None
        return self._issues.get(key)


def _key(row_id: Any) -> str:
    from omnigent.stores.factory_store import norm_id

    try:
        return norm_id(str(row_id)) if row_id else ""
    except ValueError:
        return ""


def _card(row: FactoryChatMessage) -> Mapping[str, Any]:
    card = row.card
    return card if isinstance(card, Mapping) else {}


def _meta(card: Mapping[str, Any]) -> Mapping[str, Any]:
    meta = card.get("meta")
    return meta if isinstance(meta, Mapping) else {}


def _merge_ask(pr: FactoryPr) -> str:
    return f"merge:{pr.base_ref}" if pr.base_ref else "merge"


def classify(row: FactoryChatMessage, live: Live, *, closed: bool = True) -> Sorted:
    """
    Where *row* goes for this reader: the conversation, or the activity.

    :param closed: Read whether an incident is resolved (``Sorted.closed``); a
        count of lanes leaves it out (incidents are activity either way).
    """
    card = _card(row)
    kind = str(card.get("type") or "")
    if row.author_kind in ("human", "agent"):
        return Sorted(CONVERSATION, "message")
    if not kind:
        # A routing note answers a message ("x is paused; Atlas will answer"); others are news.
        return Sorted(CONVERSATION if row.reply_to else ACTIVITY, "note")
    if kind == "decision":
        decision = live.decision(card.get("id"))
        if decision is None:
            return Sorted(ACTIVITY, kind, closed=True)
        if live.viewer.answers(decision):
            return Sorted(CONVERSATION, kind, needs_you=True, ask=decision.ref)
        return Sorted(ACTIVITY, kind, ask=decision.ref)
    if kind == "proposal":
        proposal = live.proposal(card.get("id"))
        if proposal is None:
            return Sorted(ACTIVITY, kind, closed=True)
        decision = live.proposal_decision(proposal.ref)
        if decision is not None and live.viewer.answers(decision):
            return Sorted(CONVERSATION, kind, needs_you=True, ask=decision.ref)
        return Sorted(ACTIVITY, kind, ask=decision.ref if decision is not None else None)
    if kind == "pr":
        pr = live.pr(card.get("id"))
        if pr is None:
            return Sorted(ACTIVITY, kind, closed=True)
        if pr.ready and live.viewer.answers(None):
            return Sorted(CONVERSATION, kind, needs_you=True, ask=_merge_ask(pr))
        return Sorted(ACTIVITY, kind)
    if kind == "issue":
        if not closed:
            return Sorted(ACTIVITY, kind)
        issue = live.issue(card.get("id"))
        return Sorted(ACTIVITY, kind, closed=issue is None or issue.status in _CLOSED_ISSUES)
    if kind in _ASKING_CARDS:
        return Sorted(CONVERSATION, kind, needs_you=kind == "offer")
    if kind == "preview_applied":
        meta = _meta(card)
        until = meta.get("undo_until")
        undoable = (
            not meta.get("undone")
            and isinstance(until, int)
            and until > live.now
            and meta.get("by_user") in (None, live.viewer.user_id)
        )
        return Sorted(CONVERSATION if undoable else ACTIVITY, kind)
    if kind == "closed_alarm":
        return Sorted(ACTIVITY, kind, closed=True)
    return Sorted(ACTIVITY, str(_meta(card).get("kind") or kind))


def view(message: dict[str, Any], found: Sorted) -> dict[str, Any]:
    """*message* (a ``ChatMessageView``) with its ``lane`` and why it went there."""
    out = {**message, "lane": found.feed}
    if found.feed == ACTIVITY:
        out["activity"] = {"kind": found.kind, "closed": found.closed}
    if found.needs_you:
        out["needs_you"] = True
    if found.ask:
        out["ask"] = found.ask
    return out


@dataclass
class Window:
    """One feed's rows of a page (ascending ``seq``) and where to read on."""

    rows: list[tuple[FactoryChatMessage, Sorted]] = field(default_factory=list)
    more: bool = False
    cursor: int | None = None


def read_window(
    deps: FactoryDeps,
    factory_id: str,
    thread: str | None,
    live: Live,
    feed: Feed,
    *,
    before_seq: int | None = None,
    after_seq: int | None = None,
    limit: int = 50,
) -> Window:
    """
    Read back from *before_seq* (or forward from *after_seq*) until *limit* rows
    of *feed* or :data:`SCAN_MAX` rows read; ``cursor`` is the last ``seq`` read
    (the next page's ``before_seq`` / ``after_seq``), ``more`` whether rows lie beyond.
    """
    store = deps.factory_store
    out = Window()
    scanned = 0
    forward = after_seq is not None
    cursor = after_seq if forward else before_seq
    while True:
        if forward:
            rows = store.list_chat(factory_id, thread, after_seq=cursor, limit=SCAN_BATCH + 1)
            batch: Sequence[FactoryChatMessage] = rows[:SCAN_BATCH]
        else:
            rows = store.list_chat(factory_id, thread, before_seq=cursor, limit=SCAN_BATCH + 1)
            batch = list(reversed(rows[-SCAN_BATCH:]))
        beyond = len(rows) > SCAN_BATCH
        full = False
        for index, row in enumerate(batch):
            found = classify(row, live)
            if feed == ALL or found.feed == feed:
                out.rows.append((row, found))
            cursor = row.seq
            scanned += 1
            if len(out.rows) >= limit:
                full = True
                beyond = beyond or index + 1 < len(batch)
                break
        if full or not beyond or scanned >= SCAN_MAX:
            out.more = beyond
            break
    if not forward:
        out.rows.reverse()
    out.cursor = cursor
    return out


def feed_page(
    deps: FactoryDeps,
    factory_id: str,
    thread: str,
    viewer: Viewer,
    feed: Feed,
    *,
    reader: str,
    views: Callable[[list[FactoryChatMessage]], list[dict[str, Any]]],
    before_seq: int | None = None,
    after_seq: int | None = None,
    limit: int = 50,
    now: float,
) -> dict[str, Any]:
    """
    ``GET …/chat?thread=&feed=conversation|activity|all``: one page of one feed
    (``all``: every row, each with its ``lane``).

    :param reader: Whose read mark ``unread`` and ``activity_since`` count from.
    :param views: ``chat.message_views`` over the store's rows.
    :returns: ``{data, feed, unread, activity_since, last_read_seq, has_more,
        before_seq | after_seq, thread}``.
    """
    from omnigent.stores.factory_store import norm_id

    live = Live(deps, factory_id, viewer, now)
    window = read_window(
        deps,
        factory_id,
        thread,
        live,
        feed,
        before_seq=before_seq,
        after_seq=after_seq,
        limit=limit,
    )
    by_id = {m["id"]: m for m in views([r for r, _ in window.rows])}
    last_read, unread, activity_since = since(deps, factory_id, thread, reader, viewer, live=live)
    out: dict[str, Any] = {
        "data": [view(by_id[norm_id(r.id)], s) for r, s in window.rows if norm_id(r.id) in by_id],
        "feed": feed,
        "unread": unread,
        "activity_since": activity_since,
        "last_read_seq": last_read,
        "has_more": window.more,
        "thread": thread,
    }
    out["after_seq" if after_seq is not None else "before_seq"] = window.cursor
    return out


def since(
    deps: FactoryDeps,
    factory_id: str,
    thread: str,
    reader: str,
    viewer: Viewer,
    *,
    live: Live | None = None,
    now: float | None = None,
) -> tuple[int, int, int]:
    """
    :returns: ``(read mark, conversation rows from others since it, activity rows
        since it)``, both counts capped at :data:`SINCE_CAP`.
    """
    import time

    store = deps.factory_store
    marks = store.list_chat_reads(factory_id, reader)
    last = next((r.last_seq for r in marks if r.thread == thread), 0)
    live = live or Live(deps, factory_id, viewer, time.time() if now is None else now)
    said = moved = scanned = 0
    cursor: int | None = None
    # Newest first, down to the mark: a mark far behind still counts what is recent.
    while scanned < SCAN_MAX and (said < SINCE_CAP or moved < SINCE_CAP):
        rows = store.list_chat(factory_id, thread, before_seq=cursor, limit=SCAN_BATCH)
        fresh = [r for r in rows if r.seq > last]
        for row in fresh:
            if classify(row, live, closed=False).feed == CONVERSATION:
                said += row.author != viewer.user_id
            else:
                moved += 1
        scanned += len(fresh)
        if len(rows) < SCAN_BATCH or len(fresh) < len(rows) or not rows:
            break
        cursor = rows[0].seq
    return last, min(SINCE_CAP, said), min(SINCE_CAP, moved)


__all__ = [
    "ACTIVITY",
    "CONVERSATION",
    "Live",
    "Sorted",
    "classify",
    "feed_page",
    "read_window",
    "since",
    "view",
]
