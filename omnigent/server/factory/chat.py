"""The Room: the factory's shared, durable chat, delivered to agents through the mailer.

Threads are ``factory`` (Atlas), ``improvements`` (Sentinel), ``project:<slug>``
(its PL) and ``pr:<id>`` (the owning PL). Every message is a
``factory_chat_messages`` row; :mod:`~omnigent.server.factory.chat_routing`
decides who answers it.

Delivery to an agent:

- Each responder gets a ``MailItem(kind="chat")`` through the mailer (retry,
  bounce, replay from the event log). ``chat`` is not a pending-reply kind, so
  the mailer's single-slot reply registry is never touched.
- While a responder is busy (running, or a chat delivery to it has not
  finished its turn yet), its messages wait in a per-target buffer. They go
  out as ONE delivery when its turn ends, or after :data:`COALESCE_MAX_S`.
- The envelope (:func:`render_envelope`) names the thread and author, quotes
  the body (so nobody can forge a server header), adds the thread since the
  responder's last reply, the refs' live state and the reply call.
- A delivery records ``chat.pending[target] = [(thread, chat_id, requested_at)]``,
  ``requested_at`` being when the delivering attempt began. When the turn it
  started ends without a ``sys_factory_chat`` to the thread, the turn's output
  is posted for it (``auto_reply``). Status edges (:func:`on_status_edge`, in
  arrival order) tell which turn that is: a turn that was already running
  when the mail landed leaves the entry for the next turn end.
- Buffered messages survive a restart: a message whose routed entry has no
  mail id yet is buffered again by :meth:`Room.replay`; one already handed to
  the mailer is replayed by the mailer itself, so nothing is delivered twice.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import (
    CHAT_MESSAGE_MAX_CHARS,
    FIXED_THREADS,
    HEALTH_DEAD,
    LABEL_HEALTH,
    LABEL_ID,
    LABEL_ROLE,
    LABEL_STATE,
    PROJECT_SLUG_RE,
    ROLE_LEAD,
    ROLE_PROJECT_LEAD,
    STATE_DONE,
    STATE_PAUSED,
    THREAD_FACTORY,
    THREAD_IMPROVEMENTS,
    THREAD_PR_PREFIX,
    THREAD_PROJECT_PREFIX,
)
from omnigent.server.factory import chat_routing, events, live
from omnigent.server.factory.actor import Actor, find_refs, parse_ref
from omnigent.server.factory.chat_routing import Agent, Members, Route
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps, factory_deps
from omnigent.server.factory.control import CockpitError, find_project_pl, human_name, is_closed
from omnigent.server.factory.envelope import display_title, quote_spoofed_lines
from omnigent.server.factory.mailer import MailItem, get_mailer, register_kind
from omnigent.server.factory.membership import factory_members, is_session_id
from omnigent.stores.factory_store import norm_id

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactoryChatMessage, FactoryEvent, FactoryPr
    from omnigent.server.factory.agent_scope import AgentCaller

_logger = logging.getLogger(__name__)

CHAT_KIND: Final = "chat"
"""The mail kind of Room deliveries."""
CHAT_PREFIX: Final = "chat_"
ROOM_STATE_ATTR: Final = "factory_room"

COALESCE_MAX_S: Final = 120.0
"""A busy responder's buffered messages go out after this long at the latest."""
IN_FLIGHT_MAX_S: Final = 600.0
"""How long a chat delivery counts as keeping its target busy without a turn end."""
PENDING_TTL_S: Final = 3 * 3600.0
"""A reply fallback older than this is dropped (its turn end never came)."""
TURN_ENDS_KEPT: Final = 4
"""Turn ends per session waiting for :meth:`Room.turn_ended` (the status hook's backlog)."""
TRACKED_SESSIONS_MAX: Final = 4096
REPLAY_PAGE: Final = 200
REPLAY_PAGES_MAX: Final = 25
REPLAY_WINDOW_S: Final = 2 * 3600
CONTEXT_LINES: Final = 8
CONTEXT_SCAN: Final = 60
REFS_MAX: Final = 6
LINE_MAX: Final = 200
HUMAN_POSTS_PER_WINDOW: Final = 20
HUMAN_WINDOW_S: Final = 600.0

KIND_POSTED: Final = "chat.posted"
KIND_REPLIED: Final = "chat.replied"
KIND_AUTO_REPLY: Final = "chat.auto_reply"
KIND_UNROUTABLE: Final = "chat.unroutable"
SCOPE: Final = "chat"

_IDS_RE: Final = re.compile(r" · ids?=((?:chat_[0-9a-f]{32},?)+)\]$")
GATE_ORDER: Final = ("base", "checks", "review", "verify", "demo", "mergeable")
_PR_NUMBER_RE: Final = re.compile(r"^[1-9][0-9]{0,8}$")
_CARD_NAMESPACE: Final = uuid.UUID("8c7a3c1e-5a43-4f2e-9d0b-3f1c2b6e7a10")


# ── Ids ─────────────────────────────────────────────────────────


def chat_ref(message_id: str) -> str:
    """:returns: The id agents see and answer with, e.g. ``"chat_<32 hex>"``."""
    return f"{CHAT_PREFIX}{norm_id(message_id)}"


def parse_chat_ref(value: object) -> str | None:
    """:returns: The row id of ``"chat_<id>"`` (or a bare id), or ``None`` when malformed."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return norm_id(value.strip().removeprefix(CHAT_PREFIX))
    except ValueError:
        return None


def derived_id(*parts: str) -> str:
    """:returns: A stable row id for a server-written message (a replayed write adds nothing)."""
    return uuid.uuid5(_CARD_NAMESPACE, "|".join(parts)).hex


def mail_id_for(last_message_id: str, target_id: str) -> str:
    """:returns: The mail id of a delivery, unique per (newest message, responder)."""
    return f"{chat_ref(last_message_id)}_{_key(target_id)[:12]}"


def envelope_ids(text: str) -> list[str]:
    """:returns: The row ids an envelope's header line carries (``id=`` or ``ids=``)."""
    match = _IDS_RE.search(text.split("\n", 1)[0])
    if match is None:
        return []
    return [i.removeprefix(CHAT_PREFIX) for i in match.group(1).split(",") if i]


def _key(session_id: str) -> str:
    try:
        return norm_id(session_id)
    except ValueError:
        return session_id


# ── Threads ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Thread:
    """
    One resolved Room thread.

    :param key: ``factory`` / ``improvements`` / ``project:<slug>`` / ``pr:<id>``.
    :param kind: ``factory`` / ``improvements`` / ``project`` / ``pr``.
    :param title: e.g. ``"#factory"``, ``"#db-version"`` or ``"!9631"``.
    :param project: The project slug (a PR thread's project when known).
    :param pr: The PR of a ``pr`` thread.
    """

    key: str
    kind: str
    title: str
    project: str | None = None
    pr: FactoryPr | None = None


def thread_title(key: str, pr_number: int | None = None) -> str:
    """:returns: How a thread is shown, e.g. ``"#db-version"`` or ``"!9631"``."""
    if key in FIXED_THREADS:
        return f"#{key}"
    if key.startswith(THREAD_PROJECT_PREFIX):
        return f"#{key[len(THREAD_PROJECT_PREFIX) :]}"
    if pr_number is not None:
        return f"!{pr_number}"
    return key


def _spelled(raw: str) -> str:
    """Accept ``#factory``, ``#<slug>``, ``p:<slug>`` and ``!<n>`` for the canonical keys."""
    value = raw.strip()
    if value.startswith("#"):
        name = value[1:]
        return name if name in FIXED_THREADS else f"{THREAD_PROJECT_PREFIX}{name}"
    if value.startswith("p:"):
        return f"{THREAD_PROJECT_PREFIX}{value[2:]}"
    if value.startswith("!"):
        return f"{THREAD_PR_PREFIX}{value[1:]}"
    return value


def resolve_thread(deps: FactoryDeps, factory_id: str, raw: object) -> Thread:
    """
    Resolve a thread a caller names to its canonical key.

    :param raw: ``"factory"``, ``"improvements"``, ``"project:<slug>"`` or
        ``"pr:<id or number>"`` (``#…``, ``p:<slug>`` and ``!<n>`` too).
    :raises CockpitError: 400 ``invalid_thread``, 404 ``unknown_project`` /
        ``unknown_pr``, 409 ``ambiguous_pr`` (one number in several repos).
    """
    if not isinstance(raw, str) or not raw.strip():
        raise CockpitError(400, "invalid_thread", "thread is required", field="thread")
    key = _spelled(raw)
    store = deps.factory_store
    if key in (THREAD_FACTORY, THREAD_IMPROVEMENTS):
        return Thread(key, key, thread_title(key))
    if key.startswith(THREAD_PROJECT_PREFIX):
        slug = key[len(THREAD_PROJECT_PREFIX) :]
        if not PROJECT_SLUG_RE.match(slug):
            raise CockpitError(400, "invalid_thread", f"{raw!r} is no thread", field="thread")
        if store.get_project(factory_id, slug) is None:
            raise CockpitError(404, "unknown_project", f"no project {slug!r} in this factory")
        return Thread(key, "project", thread_title(key), project=slug)
    if key.startswith(THREAD_PR_PREFIX):
        pr = _find_pr(deps, factory_id, key[len(THREAD_PR_PREFIX) :], raw)
        canonical = f"{THREAD_PR_PREFIX}{norm_id(pr.id)}"
        title = thread_title(canonical, pr.number)
        return Thread(canonical, "pr", title, project=pr.project_slug, pr=pr)
    raise CockpitError(
        400,
        "invalid_thread",
        f"{raw!r} is no thread: use factory, improvements, project:<slug> or pr:<number>",
        field="thread",
    )


def _find_pr(deps: FactoryDeps, factory_id: str, ref: str, raw: str) -> FactoryPr:
    store = deps.factory_store
    if _PR_NUMBER_RE.match(ref):
        found = [p for p in store.list_prs(factory_id) if p.number == int(ref)]
        if len(found) > 1:
            raise CockpitError(
                409, "ambiguous_pr", f"several PRs are #{ref}; use pr:<id>", field="thread"
            )
        if found:
            return found[0]
    else:
        try:
            pr = store.get_pr(ref)
        except ValueError:
            pr = None
        if pr is not None and norm_id(pr.factory_id) == norm_id(factory_id):
            return pr
    raise CockpitError(404, "unknown_pr", f"no PR {raw!r} in this factory", field="thread")


# ── Members ─────────────────────────────────────────────────────


def member_state(conv: Conversation | None) -> str:
    """:returns: ``live`` / ``paused`` / ``done`` / ``dead`` / ``gone`` for routing."""
    if conv is None or is_closed(conv):
        return chat_routing.STATE_GONE
    if conv.labels.get(LABEL_STATE) == STATE_DONE:
        return chat_routing.STATE_DONE
    if conv.labels.get(LABEL_HEALTH) == HEALTH_DEAD:
        return chat_routing.STATE_DEAD
    if conv.labels.get(LABEL_STATE) == STATE_PAUSED:
        return chat_routing.STATE_PAUSED
    return chat_routing.STATE_LIVE


def _agent(conv: Conversation, fallback: str) -> Agent:
    return Agent(conv.id, display_title(conv.title, fallback), member_state(conv))


def _sentinel(deps: FactoryDeps, root: Conversation) -> Conversation | None:
    leads = [
        m
        for m in factory_members(root.id, deps.conversation_store)
        if m.labels.get(LABEL_ROLE) == ROLE_LEAD and _key(m.id) != _key(root.id)
    ]
    leads.sort(key=lambda c: (member_state(c) != chat_routing.STATE_LIVE, -c.created_at))
    return leads[0] if leads else None


def room_members(
    deps: FactoryDeps, root: Conversation, thread: Thread, mentions: Sequence[str] = ()
) -> Members:
    """The members a message in *thread* with *mentions* may reach (store reads)."""
    slugs = {thread.project} if thread.project else set()
    slugs |= {
        m[len(chat_routing.MENTION_PL_PREFIX) :]
        for m in mentions
        if m.startswith(chat_routing.MENTION_PL_PREFIX)
    }
    return members_of(deps, root, [thread], slugs)


def members_of(
    deps: FactoryDeps, root: Conversation, threads: Sequence[Thread], slugs: Iterable[str] = ()
) -> Members:
    """The members every one of *threads* (and the PLs of *slugs*) may reach, read once."""
    wanted = {t.project for t in threads if t.project} | set(slugs)
    pls: dict[str, Agent] = {}
    for slug in sorted(s for s in wanted if s):
        pl = find_project_pl(deps, root.id, slug, include_done=True)
        if pl is not None:
            pls[slug] = _agent(pl, f"PL · {slug}")
    sentinel = _sentinel(deps, root)
    prs = [t for t in threads if t.pr is not None]
    return Members(
        atlas=_agent(root, "Atlas"),
        sentinel=_agent(sentinel, "Sentinel") if sentinel is not None else None,
        pls=pls,
        pr_projects={t.key: t.pr.project_slug for t in prs if t.pr and t.pr.project_slug},
        pr_numbers={t.key: t.pr.number for t in prs if t.pr},
    )


def responder(
    deps: FactoryDeps, root: Conversation, thread: Thread, members: Members | None = None
) -> Agent | None:
    """
    :param members: Prebuilt by :func:`members_of` (one read for many threads).
    :returns: Who answers a plain human message in *thread* (after any fallback).
    """
    if members is None:
        members = room_members(deps, root, thread)
    route = chat_routing.routes(thread.key, [], members)
    if not route.targets:
        return None
    target = route.targets[0]
    return Agent(target.session_id, target.name)


# ── Envelope ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Line:
    """
    One message as an envelope shows it.

    :param author: e.g. ``"alice@caffeine.ai (operator)"``, ``"PL · db-version (project_lead)"``
        or ``"system"``.
    """

    id: str
    at: int
    author: str
    body: str


def _hhmm(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%H:%M")


def one_line(text: str, limit: int = LINE_MAX) -> str:
    """:returns: *text* on one line, cut to *limit* chars."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _quoted(body: str) -> list[str]:
    """The body as a block quote; a forged server line is quoted twice."""
    return [f"> {line}" if line else ">" for line in quote_spoofed_lines(body).split("\n")]


def _short(author: str) -> str:
    return author.split(" (", 1)[0]


def render_envelope(
    *,
    thread: str,
    title: str,
    messages: Sequence[Line],
    context: Sequence[Line] = (),
    earlier: int = 0,
    refs: Sequence[str] = (),
    header: str | None = None,
) -> str:
    """
    The text one delivery puts into a responder's session.

    :param thread: The thread key the reply goes to, e.g. ``"factory"``.
    :param title: e.g. ``"#factory"``.
    :param messages: The messages delivered, oldest first (one, or a coalesced batch).
    :param context: The thread since the responder's last reply, oldest first.
    :param earlier: Context messages left out before *context*.
    :param refs: Live states of the refs the messages cite, e.g. ``"!9653 (open · checks fail)"``.
    :param header: A PR thread's PR line (number, head, gates).
    :returns: ``[Factory chat · <title> · from <author> · id=chat_<id>]`` and the body.
    """
    ids = [chat_ref(m.id) for m in messages]
    if len(messages) == 1:
        head = f"[Factory chat · {title} · from {messages[0].author} · id={ids[0]}]"
    else:
        names = ", ".join(dict.fromkeys(_short(m.author) for m in messages))
        head = f"[Factory chat · {title} · {len(messages)} new from {names} · ids={','.join(ids)}]"
    lines = [head]
    if header:
        lines.append(header)
    for message in messages:
        if len(messages) > 1:
            lines.append(f"[{_hhmm(message.at)}] {message.author}:")
        lines += _quoted(message.body)
    if context:
        lines.append(f"--- thread since your last reply ({len(context) + earlier} more) ---")
        if earlier:
            lines.append(f"… {earlier} earlier")
        lines += [f"[{_hhmm(c.at)}] {c.author}: {one_line(c.body)}" for c in context]
    if refs:
        lines.append("Refs: " + " · ".join(refs))
    once = "once " if len(messages) > 1 else ""
    lines.append(
        f'Answer {once}with sys_factory_chat(thread="{thread}", reply_to="{ids[-1]}", message=…)'
    )
    return "\n".join(lines)


def gate_summary(pr: FactoryPr) -> str:
    """:returns: e.g. ``"checks pass · review pending · demo fail"`` (known gates only)."""
    gates = pr.gates or {}
    parts = [
        f"{name} {gates[name]}" for name in GATE_ORDER if gates.get(name) not in (None, "unknown")
    ]
    return " · ".join(parts) or "gates unknown"


def pr_state(pr: FactoryPr) -> str:
    """:returns: ``merged`` / ``closed`` / ``ready for human merge`` / ``open``."""
    if pr.state in ("merged", "closed"):
        return pr.state
    return "ready for human merge" if pr.ready else "open"


def pr_header(pr: FactoryPr) -> str:
    """:returns: The PR line a PR thread's deliveries carry."""
    head = pr.head_sha[:7] if pr.head_sha else "unknown"
    return f"PR !{pr.number} {pr.repo} · head {head} · {pr_state(pr)} · {gate_summary(pr)}"


def ref_states(deps: FactoryDeps, factory_id: str, refs: Iterable[str]) -> list[str]:
    """:returns: ``"<ref> (<live state>)"`` per ref the store knows (at most :data:`REFS_MAX`)."""
    store = deps.factory_store
    out: list[str] = []
    for text in refs:
        ref = parse_ref(text)
        state: str | None = None
        if ref is None:
            continue
        if ref.kind == "project" and ref.slug:
            project = store.get_project(factory_id, ref.slug)
            if project is not None:
                state = project.stage if project.status == "active" else project.status
        elif ref.kind == "pr" and ref.number is not None:
            prs = [p for p in store.list_prs(factory_id) if p.number == ref.number]
            if len(prs) == 1:
                state = f"{pr_state(prs[0])} · {gate_summary(prs[0])}"
        elif ref.kind == "decision" and ref.number is not None:
            decision = store.get_decision_by_number(factory_id, ref.number)
            state = decision.status if decision is not None else None
        elif ref.kind == "proposal" and ref.number is not None:
            proposal = store.get_proposal_by_number(factory_id, ref.number)
            state = proposal.status if proposal is not None else None
        if state:
            out.append(f"{ref.text} ({state})")
        if len(out) >= REFS_MAX:
            break
    return out


def author_label(row: FactoryChatMessage, titles: Mapping[str, str]) -> str:
    """:returns: How an envelope names a message's author."""
    if row.author_kind == "system":
        return "system"
    if row.author_kind == "human":
        return human_name(row.author, row.author_role)
    name = titles.get(_key(row.author)) or row.author
    return f"{name} ({row.author_role})" if row.author_role else name


def _titles(deps: FactoryDeps, session_ids: Iterable[str]) -> dict[str, str]:
    ids = sorted({_key(s) for s in session_ids if s and is_session_id(s)})
    if not ids:
        return {}
    rows = deps.conversation_store.get_conversations(ids)
    return {_key(sid): display_title(conv.title, sid) for sid, conv in rows.items()}


def build_delivery(
    deps: FactoryDeps, factory_id: str, target_id: str, thread_key: str, message_ids: Sequence[str]
) -> MailItem | None:
    """
    The mail that delivers *message_ids* (one thread) to *target_id* (store reads).

    :returns: The item, or ``None`` when none of the messages exist any more.
    """
    store = deps.factory_store
    rows = [r for r in (store.get_chat_message(i) for i in message_ids) if r is not None]
    if not rows:
        return None
    rows.sort(key=lambda r: r.seq)
    recent = store.list_chat(factory_id, thread_key, limit=CONTEXT_SCAN)
    target = _key(target_id)
    own = [r.seq for r in recent if r.author_kind == "agent" and _key(r.author) == target]
    batch = {r.id for r in rows}
    since = max(own, default=0)
    context_rows = [r for r in recent if r.seq > since and r.id not in batch]
    shown = context_rows[-CONTEXT_LINES:]
    titles = _titles(
        deps, [target_id, *(r.author for r in [*rows, *shown] if r.author_kind == "agent")]
    )
    pr: FactoryPr | None = None
    if thread_key.startswith(THREAD_PR_PREFIX):
        try:
            pr = store.get_pr(thread_key[len(THREAD_PR_PREFIX) :])
        except ValueError:
            pr = None
    refs = list(dict.fromkeys(r for row in rows for r in row.refs))
    text = render_envelope(
        thread=thread_key,
        title=thread_title(thread_key, pr.number if pr is not None else None),
        messages=[Line(r.id, r.created_at, author_label(r, titles), r.body) for r in rows],
        context=[Line(r.id, r.created_at, author_label(r, titles), r.body) for r in shown],
        earlier=len(context_rows) - len(shown),
        refs=ref_states(deps, factory_id, refs),
        header=pr_header(pr) if pr is not None else None,
    )
    humans = [r.author for r in rows if r.author_kind == "human"]
    project = (
        thread_key[len(THREAD_PROJECT_PREFIX) :]
        if thread_key.startswith(THREAD_PROJECT_PREFIX)
        else (pr.project_slug if pr is not None else None)
    )
    return MailItem(
        mail_id=mail_id_for(rows[-1].id, target_id),
        sender_id=None,
        target_id=target_id,
        target_title=titles.get(target),
        text=text,
        kind=CHAT_KIND,
        factory_id=factory_id,
        project=project,
        created_by=humans[-1] if humans else None,
        thread=thread_key,
    )


# ── Views ──────────────────────────────────────────────────────


def message_views(deps: FactoryDeps, rows: Sequence[FactoryChatMessage]) -> list[dict[str, Any]]:
    """:returns: ``ChatMessageView`` JSON for *rows* (one batched title read)."""
    sessions = [r.author for r in rows if r.author_kind == "agent"]
    sessions += [str(e.get("session_id")) for r in rows for e in r.routed if e.get("session_id")]
    titles = _titles(deps, sessions)
    out: list[dict[str, Any]] = []
    for row in rows:
        if row.author_kind == "human":
            author_title: str | None = human_name(row.author)
        elif row.author_kind == "agent":
            author_title = titles.get(_key(row.author))
        else:
            author_title = None
        card = row.card or None
        out.append(
            {
                "id": norm_id(row.id),
                "factory_id": norm_id(row.factory_id),
                "thread": row.thread,
                "seq": row.seq,
                "author_kind": row.author_kind,
                "author": row.author,
                "author_title": author_title,
                "author_role": row.author_role,
                "body": row.body,
                "mentions": list(row.mentions),
                "refs": list(row.refs),
                "reply_to": norm_id(row.reply_to) if row.reply_to else None,
                "card": {k: card.get(k) for k in ("type", "ref", "id")} if card else None,
                "routed": [
                    {
                        "session_id": _key(str(e.get("session_id"))),
                        "title": titles.get(_key(str(e.get("session_id")))),
                        "mail_id": e.get("mail_id"),
                        "delivered_at": e.get("delivered_at"),
                        "replied_at": e.get("replied_at"),
                    }
                    for e in row.routed
                    if e.get("session_id")
                ],
                "auto_reply": bool(row.auto_reply),
                "created_at": row.created_at,
            }
        )
    return out


# ── The Room ───────────────────────────────────────────────────


@dataclass
class _Batch:
    factory_id: str
    thread: str
    ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Posted:
    """
    A stored message and what routing did with it.

    :param message: The row.
    :param route: Its route (empty for system rows).
    :param notes: The system rows routing added (e.g. "PL · x is paused; Atlas will answer").
    """

    message: FactoryChatMessage
    route: Route
    notes: list[FactoryChatMessage] = field(default_factory=list)


Busy = Callable[[str], Awaitable[bool]]
Sleep = Callable[[float], Awaitable[Any]]


class Room:
    """
    Per-process Room state: buffers, in-flight deliveries, the reply fallback map.

    Tasks (flush timers, routed updates) are held in a strong-ref set and
    cancelled by :meth:`close`.
    """

    def __init__(
        self,
        app_state: Any,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Sleep = asyncio.sleep,
        busy: Busy | None = None,
    ) -> None:
        """
        :param app_state: ``app.state`` carrying the factory deps and mailer.
        :param clock: Wall clock in epoch seconds (injectable for tests).
        :param sleep: Awaitable sleep for the coalescing timer (injectable for tests).
        :param busy: Whether a session is running; defaults to the relay status cache.
        """
        self._app_state = app_state
        self._clock = clock
        self._sleep = sleep
        self._busy_fn = busy
        self._buffers: dict[str, dict[str, _Batch]] = {}
        self._timers: dict[str, asyncio.Task[None]] = {}
        self._in_flight: dict[str, float] = {}
        self.pending: dict[str, list[tuple[str, str, float]]] = {}
        """Responder -> ``[(thread, chat row id, requested_at)]`` awaiting its answer."""
        self._turn_open: dict[str, float] = {}
        self._turn_ends: dict[str, deque[float]] = {}
        self._turn_seen: set[str] = set()
        self._last_post: dict[tuple[str, str], float] = {}
        self._human_posts: dict[str, deque[float]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._routed_lock: asyncio.Lock | None = None

    # ── rate limit (humans) ─────────────────────────────────────

    def take_human_slot(self, user_id: str | None) -> float | None:
        """
        Count one human post (:data:`HUMAN_POSTS_PER_WINDOW` per :data:`HUMAN_WINDOW_S`).

        :returns: ``None`` when allowed, else the seconds until the next slot.
        """
        now = self._clock()
        window = self._human_posts.setdefault(user_id or "", deque())
        while window and window[0] <= now - HUMAN_WINDOW_S:
            window.popleft()
        if len(window) >= HUMAN_POSTS_PER_WINDOW:
            return max(1.0, window[0] + HUMAN_WINDOW_S - now)
        window.append(now)
        return None

    # ── buffering and delivery ──────────────────────────────────

    def _spawn(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro, name="factory-room")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def is_busy(self, target_id: str) -> bool:
        """:returns: Whether *target_id* is running or has a chat delivery mid-turn."""
        key = _key(target_id)
        since = self._in_flight.get(key)
        if since is not None and self._clock() - since < IN_FLIGHT_MAX_S:
            return True
        if self._busy_fn is not None:
            return await self._busy_fn(key)
        return await asyncio.to_thread(_running, factory_deps(self._app_state), key)

    def buffered(self, target_id: str) -> list[str]:
        """:returns: The message ids waiting for *target_id*, oldest batch first."""
        return [i for b in self._buffers.get(_key(target_id), {}).values() for i in b.ids]

    async def enqueue(self, factory_id: str, target_id: str, thread: str, message_id: str) -> None:
        """Buffer one message for *target_id*; deliver at once unless the target is busy."""
        key = _key(target_id)
        batch = self._buffers.setdefault(key, {}).setdefault(thread, _Batch(factory_id, thread))
        if norm_id(message_id) not in batch.ids:
            batch.ids.append(norm_id(message_id))
        if await self.is_busy(key):
            if key not in self._timers:
                self._timers[key] = self._spawn(self._flush_later(key))
            return
        await self.flush(key)

    async def _flush_later(self, key: str) -> None:
        await self._sleep(COALESCE_MAX_S)
        self._timers.pop(key, None)
        await self.flush(key)

    async def flush(self, target_id: str) -> int:
        """
        Deliver everything buffered for *target_id* (one mail per thread).

        :returns: How many mails were queued.
        """
        key = _key(target_id)
        timer = self._timers.pop(key, None)
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()
        batches = self._buffers.pop(key, {})
        sent = 0
        for batch in batches.values():
            try:
                sent += await self._deliver(key, batch)
            except Exception:  # noqa: BLE001 - one bad batch must not drop the others
                _logger.warning("factory room: delivery to %s failed", key, exc_info=True)
        return sent

    async def _deliver(self, key: str, batch: _Batch) -> int:
        deps = factory_deps(self._app_state)
        item = await asyncio.to_thread(
            build_delivery, deps, batch.factory_id, key, batch.thread, batch.ids
        )
        if item is None:
            return 0
        # The mail id is stored before the mailer logs the mail, so a restart replays it once.
        await self.set_routed(batch.ids, key, mail_id=item.mail_id)
        try:
            get_mailer(self._app_state).submit(item)
        except RuntimeError:
            # Shutting down: the next process queues these again (no mail id).
            await self.set_routed(batch.ids, key, mail_id=None)
            return 0
        self._in_flight[key] = self._clock()
        return 1

    def delivered(self, item: MailItem, attempted_at: float | None = None) -> None:
        """
        Mailer hook: a chat mail reached its responder (register the reply fallback).

        :param attempted_at: When the delivering attempt began; the turn it
            starts begins no earlier.
        """
        ids = envelope_ids(item.text)
        if not ids or not item.thread:
            return
        key = _key(item.target_id)
        now = self._clock()
        self._in_flight[key] = now
        requested_at = min(now, attempted_at) if attempted_at is not None else now
        self.pending.setdefault(key, []).append((item.thread, ids[-1], requested_at))
        try:
            self._spawn(self.set_routed(ids, key, delivered_at=int(now)))
        except RuntimeError:
            _logger.debug("factory room: no loop to record the delivery of %s", item.mail_id)

    def expect_reply(self, target_id: str, thread: str, message_id: str) -> None:
        """Register a reply fallback without a chat delivery (the start brief)."""
        key = _key(target_id)
        self.pending.setdefault(key, []).append((thread, norm_id(message_id), self._clock()))

    def answering(self, session_id: str, thread: str) -> list[str]:
        """:returns: The pending message ids of *thread* a post by *session_id* answers."""
        return [cid for t, cid, _ in self.pending.get(_key(session_id), []) if t == thread]

    def note_post(self, session_id: str, thread: str) -> None:
        """Record that *session_id* answered in *thread* (after its row is stored)."""
        self._last_post[(_key(session_id), thread)] = self._clock()

    def turn_edge(self, session_id: str, status: str) -> None:
        """
        A status edge of a member, in arrival order (sync, no I/O).

        ``running`` opens a turn; ``idle``/``failed`` queue its start for
        :meth:`turn_ended`, which the status hook runs later.
        """
        key = _key(session_id)
        if status == "running":
            if len(self._turn_seen) >= TRACKED_SESSIONS_MAX and key not in self._turn_seen:
                self._turn_seen.clear()
                self._turn_open.clear()
            self._turn_seen.add(key)
            self._turn_open[key] = self._clock()
        elif status in ("idle", "failed"):
            start = self._turn_open.pop(key, None)
            if start is None:
                # No turn opened: a repeated end, or a harness without running edges.
                start = -math.inf if key in self._turn_seen else math.inf
            ends = self._turn_ends.setdefault(key, deque(maxlen=TURN_ENDS_KEPT))
            ends.append(start)

    def _turn_start(self, key: str) -> float:
        ends = self._turn_ends.get(key)
        if not ends:
            return math.inf
        start = ends.popleft()
        if not ends:
            self._turn_ends.pop(key, None)
        return start

    async def turn_ended(
        self, conv: Conversation, factory_id: str, data: Mapping[str, Any], *, failed: bool = False
    ) -> list[str]:
        """
        A responder's turn ended: post its output where it did not answer, then
        deliver what waited for it.

        Only deliveries that reached it before this turn began count; one that
        landed mid-turn waits for the turn it starts.

        :returns: The threads that got an auto-reply.
        """
        key = _key(conv.id)
        started = self._turn_start(key)
        now = self._clock()
        fresh = [e for e in self.pending.pop(key, []) if now - e[2] < PENDING_TTL_S]
        pending = [e for e in fresh if e[2] <= started]
        later = [e for e in fresh if e[2] > started]
        if later:
            self.pending[key] = later
            self._in_flight[key] = max(e[2] for e in later)
        else:
            self._in_flight.pop(key, None)
        unanswered: dict[str, str] = {}
        for thread, chat_id, requested_at in pending:
            last = self._last_post.get((key, thread))
            if last is None or last < requested_at:
                unanswered[thread] = chat_id
        output = data.get("output")
        posted: list[str] = []
        if not failed and isinstance(output, str) and output.strip():
            for thread, chat_id in unanswered.items():
                await self._auto_reply(conv, factory_id, thread, chat_id, output)
                posted.append(thread)
        if key in self._buffers:
            await self.flush(key)
        return posted

    async def _auto_reply(
        self, conv: Conversation, factory_id: str, thread: str, chat_id: str, output: str
    ) -> None:
        deps = factory_deps(self._app_state)
        body = output.strip()
        if len(body) > CHAT_MESSAGE_MAX_CHARS:
            body = body[: CHAT_MESSAGE_MAX_CHARS - 1] + "…"
        role = conv.labels.get(LABEL_ROLE)
        row = await asyncio.to_thread(
            lambda: deps.factory_store.append_chat(
                factory_id,
                thread=thread,
                author_kind="agent",
                author=_key(conv.id),
                author_role=role,
                body=body,
                refs=[r.text for r in find_refs(body)][:REFS_MAX],
                reply_to=chat_id,
                auto_reply=True,
            )
        )
        self._last_post[(_key(conv.id), thread)] = self._clock()
        await self.set_routed([chat_id], _key(conv.id), replied_at=row.created_at)
        await events.emit(
            self._app_state,
            factory_id,
            KIND_AUTO_REPLY,
            from_id=conv.id,
            subject=f"chat:{norm_id(row.id)}",
            summary=f"{display_title(conv.title, conv.id)} · turn summary in "
            f"{thread_title(thread)}",
            data={
                **Actor.agent(conv.id, role).event_data(),
                "message_id": norm_id(row.id),
                "thread": thread,
                "reply_to": chat_id,
            },
        )

    async def set_routed(self, ids: Iterable[str], session_id: str, **fields: Any) -> None:
        """
        Merge *fields* (``mail_id``, ``delivered_at``, ``replied_at``) into the
        routed entry of *session_id* on each message; others are left alone.
        """
        if self._routed_lock is None:
            self._routed_lock = asyncio.Lock()
        deps = factory_deps(self._app_state)
        key = _key(session_id)
        factories: set[str] = set()

        def write() -> None:
            store = deps.factory_store
            for message_id in ids:
                row = store.get_chat_message(message_id)
                if row is None:
                    continue
                routed = [dict(e) for e in row.routed]
                mine = [e for e in routed if _key(str(e.get("session_id"))) == key]
                if not mine:
                    continue
                mine[0].update(fields)
                store.update_chat_message(row.id, routed=routed)
                factories.add(row.factory_id)

        try:
            async with self._routed_lock:
                await asyncio.to_thread(write)
        except Exception:  # noqa: BLE001 - a lost receipt only costs a tick in the UI
            _logger.warning("factory room: could not record delivery state", exc_info=True)
            return
        for factory_id in factories:
            live.announce_factory_changed(self._app_state, factory_id, [SCOPE])

    # ── restart ────────────────────────────────────────────────

    async def replay(self, *, now: float | None = None) -> int:
        """
        Buffer again what a previous process had not handed to the mailer yet.

        :returns: How many (message, responder) pairs were queued again.
        """
        from omnigent.server.factory.snapshot import list_factory_roots

        deps = getattr(self._app_state, FACTORY_STATE_ATTR, None)
        if not isinstance(deps, FactoryDeps):
            return 0
        since = (now if now is not None else self._clock()) - REPLAY_WINDOW_S

        def window(factory_id: str) -> list[FactoryChatMessage]:
            """The factory's rows of the replay window, oldest first, paging back past cards."""
            pages: list[list[FactoryChatMessage]] = []
            before: int | None = None
            for _ in range(REPLAY_PAGES_MAX):
                page = deps.factory_store.list_chat(
                    factory_id, before_seq=before, limit=REPLAY_PAGE
                )
                pages.insert(0, [r for r in page if r.created_at >= since])
                if len(page) < REPLAY_PAGE or page[0].created_at < since:
                    break
                before = page[0].seq
            return [r for page in pages for r in page]

        def scan() -> list[tuple[str, str, str, str]]:
            found: list[tuple[str, str, str, str]] = []
            for root in list_factory_roots(deps.conversation_store):
                for row in window(root.id):
                    if row.author_kind == "system":
                        continue
                    for entry in row.routed:
                        sid = entry.get("session_id")
                        if sid and not entry.get("mail_id") and not entry.get("delivered_at"):
                            found.append((root.id, str(sid), row.thread, row.id))
            return found

        try:
            queued = await asyncio.to_thread(scan)
        except Exception:  # noqa: BLE001 - a failed replay must not stop the server starting
            _logger.warning("factory room: could not scan the Room for replay", exc_info=True)
            return 0
        for factory_id, target, thread, message_id in queued:
            try:
                await self.enqueue(factory_id, target, thread, message_id)
            except Exception:  # noqa: BLE001 - one bad message must not stop the server starting
                _logger.warning(
                    "factory room: could not queue %s again", message_id, exc_info=True
                )
        if queued:
            _logger.info("factory room: queued %d undelivered chat message(s) again", len(queued))
        return len(queued)

    async def drain(self) -> None:
        """Wait for routed updates (tests); flush timers keep waiting."""
        while True:
            busy = [t for t in self._tasks if t not in self._timers.values() and not t.done()]
            if not busy:
                return
            await asyncio.gather(*busy, return_exceptions=True)

    async def close(self) -> None:
        """Cancel timers and in-flight writes."""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._timers.clear()


def _running(deps: FactoryDeps, session_id: str) -> bool:
    from omnigent.server.routes._sessions.helpers import _session_status_from_cache

    conv = deps.conversation_store.get_conversation(session_id)
    return conv is not None and _session_status_from_cache(conv.id, conv.live_status) == "running"


def get_room(app_state: Any) -> Room:
    """:returns: The app's Room, created on first use."""
    room = getattr(app_state, ROOM_STATE_ATTR, None)
    if not isinstance(room, Room):
        room = Room(app_state)
        setattr(app_state, ROOM_STATE_ATTR, room)
    return room


def _on_chat_delivered(app_state: Any, item: MailItem, attempted_at: float) -> None:
    get_room(app_state).delivered(item, attempted_at)


def on_status_edge(app_state: Any, conv: Conversation, status: str) -> None:
    """Status-route hook (sync, arrival order): feed a member's turn edges to the Room."""
    try:
        if conv.labels.get(LABEL_ID):
            get_room(app_state).turn_edge(conv.id, status)
    except Exception:  # noqa: BLE001 - the Room must never break the status route
        _logger.warning("factory room: could not record a status edge", exc_info=True)


register_kind(CHAT_KIND, _on_chat_delivered)


async def _brief_delivered(app_state: Any, event: FactoryEvent) -> None:
    """The kickoff carries the start brief: its delivery is the brief message's receipt."""
    data = event.data or {}
    if event.kind != "message.delivered" or data.get("kind") != "brief":
        return
    if not event.to_id or _key(event.to_id) != _key(event.factory_id):
        return
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps):
        return
    message_id = derived_id(norm_id(event.factory_id), "brief")
    row = await asyncio.to_thread(deps.factory_store.get_chat_message, message_id)
    if row is not None and any(e.get("mail_id") == data.get("mail_id") for e in row.routed):
        delivered = int(event.ts // 1000)
        await get_room(app_state).set_routed([row.id], event.factory_id, delivered_at=delivered)


if _brief_delivered not in events._listeners:
    events.add_listener(_brief_delivered)


# ── Posting ────────────────────────────────────────────────────


def _routed_entries(route: Route) -> list[dict[str, Any]]:
    return [
        {
            "session_id": _key(t.session_id),
            "mail_id": None,
            "delivered_at": None,
            "replied_at": None,
        }
        for t in route.targets
    ]


def _check_body(body: object) -> str:
    if not isinstance(body, str) or not body.strip():
        raise CockpitError(400, "empty_message", "message must not be empty", field="body")
    if len(body) > CHAT_MESSAGE_MAX_CHARS:
        raise CockpitError(
            413,
            "message_too_large",
            f"message exceeds {CHAT_MESSAGE_MAX_CHARS} chars; put bulk in a file or a PR",
            field="body",
        )
    return body.strip()


def _strings(value: object) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _reply_target(deps: FactoryDeps, factory_id: str, thread: str, reply_to: object) -> str | None:
    """:returns: The replied row id; ``None`` for a message of another thread (not a reply)."""
    if reply_to is None or reply_to == "":
        return None
    message_id = parse_chat_ref(reply_to)
    row = deps.factory_store.get_chat_message(message_id) if message_id else None
    if row is None or norm_id(row.factory_id) != norm_id(factory_id):
        raise CockpitError(
            404, "unknown_message", f"no message {reply_to!r} in this factory", field="reply_to"
        )
    return norm_id(row.id) if row.thread == thread else None


async def post_system(
    app_state: Any,
    factory_id: str,
    thread: str,
    body: str,
    *,
    card: Mapping[str, Any] | None = None,
    refs: Sequence[str] = (),
    message_id: str | None = None,
    announce: bool = True,
) -> FactoryChatMessage:
    """
    Write a system row (a card or a one-line receipt). System rows never reach agents.

    :param message_id: A stable id, so a repeated write adds nothing.
    """
    deps = factory_deps(app_state)
    row = await asyncio.to_thread(
        lambda: deps.factory_store.append_chat(
            factory_id,
            thread=thread,
            author_kind="system",
            author="server",
            body=one_line(body, 600),
            card=dict(card) if card else {"type": "system", "ref": None, "id": None},
            refs=list(refs),
            message_id=message_id,
        )
    )
    if announce:
        live.announce_factory_changed(app_state, factory_id, [SCOPE])
    return row


async def _route_and_store(
    app_state: Any,
    root: Conversation,
    thread: Thread,
    *,
    author_kind: str,
    author: str,
    author_role: str | None,
    body: str,
    mentions: Sequence[str],
    reply_to: str | None,
    actor: Actor,
    event_kind: str,
    author_session: str | None = None,
    extra_refs: Sequence[str] = (),
    extra_data: Mapping[str, Any] | None = None,
) -> Posted:
    deps = factory_deps(app_state)
    members = await asyncio.to_thread(room_members, deps, root, thread, mentions)
    route = chat_routing.routes(
        thread.key, mentions, members, author_kind=author_kind, author_session=author_session
    )
    refs = list(dict.fromkeys([*(r.text for r in find_refs(body)), *extra_refs]))[:REFS_MAX]
    row = await asyncio.to_thread(
        lambda: deps.factory_store.append_chat(
            root.id,
            thread=thread.key,
            author_kind=author_kind,
            author=author,
            author_role=author_role,
            body=body,
            mentions=list(mentions),
            refs=refs,
            reply_to=reply_to,
            routed=_routed_entries(route),
        )
    )
    notes = [
        await post_system(
            app_state,
            root.id,
            thread.key,
            note,
            message_id=derived_id(row.id, "note", note),
            announce=False,
        )
        for note in route.notes
    ]
    data = {
        **actor.event_data(),
        "message_id": norm_id(row.id),
        "thread": thread.key,
        "seq": row.seq,
        "targets": [_key(t.session_id) for t in route.targets],
        "mentions": list(mentions),
        **({"reply_to": reply_to} if reply_to else {}),
        **(extra_data or {}),
    }
    await events.emit(
        app_state,
        root.id,
        event_kind,
        project=thread.project,
        from_id=author_session,
        subject=f"chat:{norm_id(row.id)}",
        summary=f"{actor.title} in {thread.title}: {one_line(body, 160)}",
        data=data,
    )
    if route.unroutable:
        await events.emit(
            app_state,
            root.id,
            KIND_UNROUTABLE,
            severity="notice",
            project=thread.project,
            subject=f"chat:{norm_id(row.id)}",
            summary="; ".join(route.notes) or f"{', '.join(route.unroutable)} cannot answer",
            data={"message_id": norm_id(row.id), "unroutable": list(route.unroutable)},
        )
    room = get_room(app_state)
    for target in route.targets:
        await room.enqueue(root.id, target.session_id, thread.key, row.id)
    return Posted(message=row, route=route, notes=notes)


async def post_human(
    app_state: Any,
    root: Conversation,
    user_id: str | None,
    payload: Mapping[str, Any],
) -> Posted:
    """
    Post a person's message (``POST /v1/factory/factories/{fid}/chat``); the caller checked EDIT.

    :param payload: ``{thread, body, reply_to?, mentions?}``.
    :raises CockpitError: 400/404/413 on a bad body, 429 ``rate_limited``.
    """
    from omnigent.server.factory import people

    deps = factory_deps(app_state)
    thread = await asyncio.to_thread(resolve_thread, deps, root.id, payload.get("thread"))
    body = _check_body(payload.get("body"))
    reply_to = await asyncio.to_thread(
        _reply_target, deps, root.id, thread.key, payload.get("reply_to")
    )
    raw_mentions = payload.get("mentions") or []
    if not isinstance(raw_mentions, list):
        raise CockpitError(400, "invalid_body", "mentions must be a list", field="mentions")
    mentions = chat_routing.parse_mentions(body, raw_mentions)
    wait = get_room(app_state).take_human_slot(user_id)
    if wait is not None:
        raise CockpitError(
            429,
            "rate_limited",
            f"{HUMAN_POSTS_PER_WINDOW} posts per {int(HUMAN_WINDOW_S // 60)} minutes; "
            f"try again in {int(wait)} s",
            retry_after=int(wait),
        )
    role = await asyncio.to_thread(people.role_of, deps, root.id, user_id)
    author = user_id or "human"
    actor = Actor.human(author, role, name=human_name(user_id))
    return await _route_and_store(
        app_state,
        root,
        thread,
        author_kind="human",
        author=author,
        author_role=role,
        body=body,
        mentions=mentions,
        reply_to=reply_to,
        actor=actor,
        event_kind=KIND_POSTED,
    )


def allowed_thread(role: str | None, own_project: str | None, thread: Thread) -> bool:
    """
    Whether an agent of *role* may post in *thread*.

    Leads post anywhere; a project lead in ``#factory``, its project's thread
    and its project's PR threads.
    """
    if role == ROLE_LEAD:
        return True
    if role != ROLE_PROJECT_LEAD or not own_project:
        return False
    if thread.kind == THREAD_FACTORY:
        return True
    return thread.kind in ("project", "pr") and thread.project == own_project


async def post_agent(
    app_state: Any,
    caller: AgentCaller,
    payload: Mapping[str, Any],
    *,
    via_user: str | None = None,
) -> dict:
    """
    Post an agent's message (``sys_factory_chat`` → ``POST /v1/factory/agent/chat``).

    :param payload: ``{thread, message, reply_to?, mentions?, refs?}``.
    :param via_user: The person whose credential posted it, when no runner did.
    :returns: ``{id: "chat_…", seq, thread, routed: [names], notes}``.
    :raises CockpitError: 403 ``not_allowed`` / ``thread_not_allowed``, 400/404/413 on a
        bad body, 429 ``rate_limited``.
    """
    membership = caller.membership
    if membership.role not in (ROLE_LEAD, ROLE_PROJECT_LEAD) or not membership.is_top_level:
        raise CockpitError(403, "not_allowed", "only leads and project leads post in the Room")
    deps = caller.deps
    from omnigent.server.factory.control import get_conv

    root = await get_conv(deps, caller.factory_id)
    if root is None:
        raise CockpitError(404, "not_a_factory", "the factory root is gone")
    thread = await asyncio.to_thread(resolve_thread, deps, root.id, payload.get("thread"))
    if not allowed_thread(membership.role, membership.project, thread):
        raise CockpitError(
            403,
            "thread_not_allowed",
            f"a project lead posts in #factory and its own project's threads, not {thread.title}",
            field="thread",
        )
    body = _check_body(payload.get("message"))
    reply_to = await asyncio.to_thread(
        _reply_target, deps, root.id, thread.key, payload.get("reply_to")
    )
    raw_mentions = _strings(payload.get("mentions"))[: chat_routing.MAX_MENTIONS]
    mentions = chat_routing.parse_mentions(body, raw_mentions)
    raw_refs = _strings(payload.get("refs"))[: chat_routing.MAX_MENTIONS]
    extra_refs = [r.text for r in map(parse_ref, raw_refs) if r is not None]
    sender = f"chat:{_key(caller.session.id)}"
    limit = get_mailer(app_state).try_acquire(sender, thread.key, factory_id=root.id)
    if limit is not None:
        raise CockpitError(429, "rate_limited", f"rate limit reached: {limit}")
    room = get_room(app_state)
    answered = room.answering(caller.session.id, thread.key)
    actor = Actor.agent(caller.session.id, membership.role, title=caller.title)
    posted = await _route_and_store(
        app_state,
        root,
        thread,
        author_kind="agent",
        author=_key(caller.session.id),
        author_role=membership.role,
        body=body,
        mentions=mentions,
        reply_to=reply_to,
        actor=actor,
        event_kind=KIND_REPLIED if reply_to or answered else KIND_POSTED,
        author_session=caller.session.id,
        extra_refs=extra_refs,
        extra_data={"via_user": via_user} if via_user else None,
    )
    # Only a stored answer suppresses the turn's fallback post.
    room.note_post(caller.session.id, thread.key)
    replied = list(dict.fromkeys([*([reply_to] if reply_to else []), *answered]))
    if replied:
        await room.set_routed(replied, caller.session.id, replied_at=posted.message.created_at)
    return {
        "id": chat_ref(posted.message.id),
        "seq": posted.message.seq,
        "thread": thread.key,
        "routed": [t.name for t in posted.route.targets],
        "notes": list(posted.route.notes),
    }


async def store_brief(
    app_state: Any,
    root: Conversation,
    user_id: str | None,
    brief: str,
    mail_id: str,
) -> FactoryChatMessage | None:
    """
    Store a factory's start brief as ``#factory`` message #1 (the kickoff delivers it).

    Atlas's kickoff turn answers it: a turn that ends without a
    ``sys_factory_chat`` to ``#factory`` is posted there for it. Never raises.

    :returns: The message, or ``None`` when it could not be stored.
    """
    from omnigent.server.factory import people

    try:
        deps = factory_deps(app_state)
        role = await asyncio.to_thread(people.role_of, deps, root.id, user_id)
        body = brief.strip()[:CHAT_MESSAGE_MAX_CHARS]
        row = await asyncio.to_thread(
            lambda: deps.factory_store.append_chat(
                root.id,
                thread=THREAD_FACTORY,
                author_kind="human",
                author=user_id or "human",
                author_role=role,
                body=body,
                refs=[r.text for r in find_refs(body)][:REFS_MAX],
                routed=[
                    {
                        "session_id": _key(root.id),
                        "mail_id": mail_id,
                        "delivered_at": None,
                        "replied_at": None,
                    }
                ],
                message_id=derived_id(norm_id(root.id), "brief"),
            )
        )
    except Exception:  # noqa: BLE001 - the kickoff still carries the brief
        _logger.warning(
            "factory room: could not store the start brief of %s", root.id, exc_info=True
        )
        return None
    get_room(app_state).expect_reply(root.id, THREAD_FACTORY, row.id)
    live.announce_factory_changed(app_state, root.id, [SCOPE])
    return row


def brief_room_line(message: FactoryChatMessage) -> str:
    """:returns: The kickoff's line telling Atlas where the brief lives in the Room."""
    ref = chat_ref(message.id)
    return (
        f"Room: this brief is #factory message {ref}. Answer the humans there: "
        f'sys_factory_chat(thread="factory", reply_to="{ref}", '
        "message=<one line: the plan or C-n>)."
    )


async def on_turn_end(
    app_state: Any,
    conv: Conversation,
    factory_id: str,
    data: Mapping[str, Any],
    *,
    failed: bool = False,
) -> None:
    """Status hook: a member's turn ended (never raises)."""
    try:
        await get_room(app_state).turn_ended(conv, factory_id, data, failed=failed)
    except Exception:  # noqa: BLE001 - the Room must never break the status edge
        _logger.warning(
            "factory room: turn end of %s failed", getattr(conv, "id", None), exc_info=True
        )


async def start(app_state: Any, *, replay: bool = True) -> Callable[[], Awaitable[None]]:
    """
    Start the Room for one server lifespan: queue again what a restart left behind.

    :param replay: Queue again the messages a previous process never handed to the mailer.
    :returns: An async callable that stops it.
    """
    room = get_room(app_state)
    if replay:
        await room.replay()

    async def _stop() -> None:
        await room.close()
        if getattr(app_state, ROOM_STATE_ATTR, None) is room:
            setattr(app_state, ROOM_STATE_ATTR, None)

    return _stop


__all__ = [
    "CHAT_KIND",
    "CHAT_PREFIX",
    "COALESCE_MAX_S",
    "GATE_ORDER",
    "KIND_AUTO_REPLY",
    "KIND_POSTED",
    "KIND_REPLIED",
    "KIND_UNROUTABLE",
    "ROOM_STATE_ATTR",
    "Line",
    "Posted",
    "Room",
    "Thread",
    "allowed_thread",
    "author_label",
    "brief_room_line",
    "build_delivery",
    "chat_ref",
    "derived_id",
    "envelope_ids",
    "gate_summary",
    "get_room",
    "mail_id_for",
    "member_state",
    "members_of",
    "message_views",
    "on_status_edge",
    "on_turn_end",
    "one_line",
    "parse_chat_ref",
    "post_agent",
    "post_human",
    "post_system",
    "pr_header",
    "pr_state",
    "ref_states",
    "render_envelope",
    "resolve_thread",
    "responder",
    "room_members",
    "start",
    "store_brief",
    "thread_title",
]
