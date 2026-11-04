"""Who answers a Room message: the routing table of the Room's threads.

:func:`routes` is a pure function of the thread, the message's mentions and
the factory's routable members (:class:`Members`, built by
:func:`omnigent.server.factory.chat.room_members`):

========================  ==============================================
``factory``               Atlas (the factory root)
``improvements``          Sentinel (the non-root lead), else Atlas
``discovery``             Scout (the discovery seat), else Atlas
``project:<slug>``        that project's PL; Atlas while it has none
``pr:<id>``               the PL of the PR's project; Atlas while it has none
``need:<ref>``            the need level's responder (Scout by default)
``feature:<ref>``         the feature level's (Atlas by default)
``study:R-<n>``           the study kind's (Scout by default)
``task:<ref>``            the task level's (the task's project lead by
                          default: the message names the task)
========================  ==============================================

The four node threads answer by the definition's ``structure.responders``
(:attr:`Members.responders`, a seat per level): ``atlas``, ``sentinel``,
``scout`` (Atlas while discovery is off, as Scout is not seated then) or
``lead`` (the PL of the node's project, :attr:`Members.node_projects`; Atlas
for a node with no project). A level whose responder is ``null`` routes a
plain message to nobody, with a note saying how to reach an agent.

- ``@atlas``, ``@sentinel``, ``@scout`` and ``@pl:<slug>`` pick the responders
  instead of the thread's default (mention the default too to add to it);
  ``@scout`` routes only while discovery runs for the factory (and Scout,
  which posts only in ``#discovery``, may answer such a message where it was
  written); while Scout is over its daily budget Atlas answers instead.
- ``@<person>`` only notifies; it never routes.
- A message an agent wrote reaches other agents only through such a mention,
  never through a thread default, so agents talking in the Room never loop.
- A responder that is paused or gone is replaced by Atlas, with a system line
  saying so. When Atlas cannot answer either, the line says the message will
  not be delivered (it is never queued for a resume).

:func:`team_signal` says which of a person's messages discovery keeps as a
hunch (the ``team`` source): ``hunch: …`` anywhere, and in ``#factory`` and
``#discovery`` "I think … because …" and requests like "we need …".
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from omnigent.factory import (
    THREAD_DISCOVERY,
    THREAD_FACTORY,
    THREAD_IMPROVEMENTS,
    THREAD_NODE_PREFIXES,
    THREAD_PR_PREFIX,
    THREAD_PROJECT_PREFIX,
    thread_kind,
)

STATE_LIVE: Final = "live"
STATE_PAUSED: Final = "paused"
STATE_DONE: Final = "done"
STATE_DEAD: Final = "dead"
STATE_GONE: Final = "gone"

VIA_DEFAULT: Final = "default"
VIA_MENTION: Final = "mention"
VIA_FALLBACK: Final = "fallback"

MENTION_ATLAS: Final = "@atlas"
MENTION_SENTINEL: Final = "@sentinel"
MENTION_SCOUT: Final = "@scout"
MENTION_PL_PREFIX: Final = "@pl:"
MAX_MENTIONS: Final = 20

NODE_KINDS: Final = tuple(prefix[:-1] for prefix in THREAD_NODE_PREFIXES)
"""Thread kinds of the cockpit's node threads: ``need``, ``feature``, ``study``, ``task``."""
SEAT_ATLAS: Final = "atlas"
SEAT_SENTINEL: Final = "sentinel"
SEAT_SCOUT: Final = "scout"
SEAT_LEAD: Final = "lead"
DEFAULT_RESPONDERS: Final[dict[str, str | None]] = {
    "need": SEAT_SCOUT,
    "feature": SEAT_ATLAS,
    "study": SEAT_SCOUT,
    "task": SEAT_LEAD,
}
"""Who answers each node thread when the definition says nothing (``structure.responders``)."""

_PROJECT: Final = THREAD_PROJECT_PREFIX[:-1]
_PR: Final = THREAD_PR_PREFIX[:-1]
_SLUG = r"[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?"
_EMAIL = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
_MENTION_RE: Final = re.compile(
    rf"(?<![\w@./-])@(atlas|sentinel|scout|pl:{_SLUG}|{_EMAIL})(?![\w@-])", re.IGNORECASE
)
_ONE_MENTION_RE: Final = re.compile(
    rf"^@?(atlas|sentinel|scout|pl:{_SLUG}|{_EMAIL})$", re.IGNORECASE
)


@dataclass(frozen=True)
class Agent:
    """
    A member that can answer in the Room.

    :param session_id: Its session id.
    :param name: How notes name it, e.g. ``"Atlas"`` or ``"PL · db-version"``.
    :param state: ``live``, ``paused``, ``done``, ``dead`` or ``gone``.
    """

    session_id: str
    name: str
    state: str = STATE_LIVE

    @property
    def live(self) -> bool:
        """:returns: Whether a message to it gets an answer."""
        return self.state == STATE_LIVE


@dataclass(frozen=True)
class Members:
    """
    The factory's routable members, as far as one message needs them.

    :param atlas: The factory root.
    :param sentinel: The other lead, if one is up.
    :param pls: Project slug -> its current PL (a slug without one is absent).
    :param pr_projects: PR thread key (``pr:<id>``) -> its project slug.
    :param pr_numbers: PR thread key -> PR number, for notes.
    :param scout: The discovery seat, if one is seated and may be woken.
    :param discovery: Whether discovery runs for the factory (``@scout`` routes only then).
    :param scout_away: Why Scout cannot answer now (e.g. over today's budget), for the note.
    :param responders: Node thread kind -> the seat that answers it
        (``structure.responders``; ``None``: nobody), :data:`DEFAULT_RESPONDERS` when unset.
    :param node_projects: Node thread key -> the slug of its node's project (a task's).
    :param node_labels: Node thread key -> how notes name its node, e.g. ``"T-16"``.
    """

    atlas: Agent | None
    sentinel: Agent | None = None
    pls: Mapping[str, Agent] = field(default_factory=dict)
    pr_projects: Mapping[str, str] = field(default_factory=dict)
    pr_numbers: Mapping[str, int] = field(default_factory=dict)
    scout: Agent | None = None
    discovery: bool = False
    scout_away: str | None = None
    responders: Mapping[str, str | None] = field(default_factory=lambda: dict(DEFAULT_RESPONDERS))
    node_projects: Mapping[str, str] = field(default_factory=dict)
    node_labels: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Target:
    """
    One responder of a message.

    :param via: ``default`` (the thread's), ``mention`` or ``fallback`` (Atlas
        standing in for a member that cannot answer).
    """

    session_id: str
    name: str
    via: str


@dataclass(frozen=True)
class Route:
    """
    Where a message goes.

    :param targets: The agents it is delivered to, in order, without repeats.
    :param notes: System lines for the thread, e.g.
        ``"PL · db-version is paused; Atlas will answer"``.
    :param unroutable: Names of the members it could not reach.
    :param people: ``@<person>`` mentions (notified, never routed).
    """

    targets: tuple[Target, ...] = ()
    notes: tuple[str, ...] = ()
    unroutable: tuple[str, ...] = ()
    people: tuple[str, ...] = ()


def normalize_mention(value: object) -> str | None:
    """
    :param value: e.g. ``"@PL:db-version"``, ``"sentinel"``, ``"Scout"`` or ``"@mia@example.com"``.
    :returns: ``"@pl:db-version"`` / ``"@sentinel"`` / ``"@scout"`` / ``"@mia@example.com"``,
        or ``None``.
    """
    if not isinstance(value, str):
        return None
    match = _ONE_MENTION_RE.match(value.strip())
    if match is None:
        return None
    name = match.group(1)
    return f"@{name.lower()}" if "@" not in name else f"@{name}"


def parse_mentions(text: str, extra: Iterable[object] = ()) -> list[str]:
    """
    Every mention in *text* plus *extra*, normalized, in order, without repeats.

    :param text: A message body, e.g. ``"@pl:db-version use knex"``.
    :param extra: Mentions the client sent alongside the body.
    :returns: At most :data:`MAX_MENTIONS` mentions.
    """
    found: list[str] = []
    for raw in [*extra, *(f"@{m.group(1)}" for m in _MENTION_RE.finditer(text))]:
        mention = normalize_mention(raw)
        if mention is not None and mention not in found:
            found.append(mention)
    return found[:MAX_MENTIONS]


def is_agent_mention(mention: str) -> bool:
    """:returns: Whether *mention* names an agent (``@atlas``, ``@sentinel``, ``@scout``, a PL)."""
    return mention in (MENTION_ATLAS, MENTION_SENTINEL, MENTION_SCOUT) or mention.startswith(
        MENTION_PL_PREFIX
    )


def _pl_name(slug: str) -> str:
    return f"PL · {slug}"


def _pr_label(thread: str, members: Members) -> str:
    number = members.pr_numbers.get(thread)
    return f"!{number}" if number is not None else "this PR"


def _default(thread: str, members: Members) -> tuple[Agent | None, str, str | None]:
    """:returns: ``(responder, its name, why Atlas answers instead)``."""
    kind = thread_kind(thread)
    if kind == THREAD_FACTORY:
        return members.atlas, "Atlas", None
    if kind == THREAD_IMPROVEMENTS:
        if members.sentinel is None:
            return None, "Sentinel", "Sentinel is not up; Atlas will answer"
        return members.sentinel, members.sentinel.name, None
    if kind == THREAD_DISCOVERY:
        return _scout(members)
    if kind == _PROJECT:
        slug = thread[len(THREAD_PROJECT_PREFIX) :]
        pl = members.pls.get(slug)
        return pl, _pl_name(slug), None if pl else f"{_pl_name(slug)} not up yet; Atlas has it"
    if kind == _PR:
        slug = members.pr_projects.get(thread)
        pl = members.pls.get(slug) if slug else None
        if pl is not None:
            return pl, pl.name, None
        if slug:
            return None, _pl_name(slug), f"{_pl_name(slug)} not up yet; Atlas has it"
        label = _pr_label(thread, members)
        return None, label, f"{label} has no project yet; Atlas has it"
    if kind in NODE_KINDS:
        seat = members.responders.get(kind, DEFAULT_RESPONDERS[kind])
        return _seat(seat, thread, members)
    return None, "nobody", None


def _seat(seat: str | None, thread: str, members: Members) -> tuple[Agent | None, str, str | None]:
    """:returns: ``(responder, its name, why Atlas answers instead)`` of a node thread's seat."""
    if seat == SEAT_SENTINEL:
        if members.sentinel is None:
            return None, "Sentinel", "Sentinel is not up; Atlas will answer"
        return members.sentinel, members.sentinel.name, None
    if seat == SEAT_SCOUT and members.discovery:
        return _scout(members)
    if seat == SEAT_LEAD:
        slug = members.node_projects.get(thread)
        pl = members.pls.get(slug) if slug else None
        if slug and pl is None:
            return None, _pl_name(slug), f"{_pl_name(slug)} not up yet; Atlas has it"
        if pl is not None:
            return pl, pl.name, None
    # Atlas: its own seat, Scout's while discovery is off, a lead's on a node with no project.
    return members.atlas, "Atlas", None


def _mentioned(mention: str, members: Members) -> tuple[Agent | None, str, str | None]:
    """:returns: ``(agent, its name, why Atlas answers instead)`` for an agent mention."""
    if mention == MENTION_ATLAS:
        return members.atlas, "Atlas", None
    if mention == MENTION_SENTINEL:
        if members.sentinel is None:
            return None, "Sentinel", "Sentinel is not up; Atlas will answer"
        return members.sentinel, members.sentinel.name, None
    if mention == MENTION_SCOUT:
        return _scout(members)
    slug = mention[len(MENTION_PL_PREFIX) :]
    pl = members.pls.get(slug)
    return pl, _pl_name(slug), None if pl else f"{_pl_name(slug)} not up yet; Atlas has it"


def _scout(members: Members) -> tuple[Agent | None, str, str | None]:
    if members.scout is None:
        return None, "Scout", members.scout_away or "Scout is not seated; Atlas will answer"
    return members.scout, members.scout.name, None


def _back(agent: Agent | None, atlas: Agent | None) -> str:
    return "Atlas" if agent is None or agent is atlas else f"{agent.name} or Atlas"


def routes(
    thread: str,
    mentions: Sequence[str],
    members: Members,
    *,
    author_kind: str = "human",
    author_session: str | None = None,
) -> Route:
    """
    Route one Room message.

    :param thread: Its thread key, e.g. ``"project:db-version"``.
    :param mentions: Its normalized mentions (:func:`parse_mentions`).
    :param members: Who can answer.
    :param author_kind: ``human``, ``agent`` or ``system`` (system rows never route).
    :param author_session: The authoring agent, never routed its own message.
    :returns: The responders, the system lines to post and the people to notify.
    """
    people = tuple(m for m in mentions if not is_agent_mention(m))
    if author_kind == "system" or thread_kind(thread) is None:
        return Route(people=people)
    wanted: list[tuple[Agent | None, str, str | None, str]] = []
    for mention in mentions:
        if mention == MENTION_SCOUT and not members.discovery:
            continue  # No discovery here: "@scout" is plain text.
        if is_agent_mention(mention):
            wanted.append((*_mentioned(mention, members), VIA_MENTION))
    notes: list[str] = []
    if not wanted and author_kind == "human":
        kind = thread_kind(thread)
        if kind in NODE_KINDS and members.responders.get(kind, "") is None:
            label = members.node_labels.get(thread) or thread
            notes.append(f"Nobody answers {label}'s thread; @mention an agent to ask one")
        else:
            wanted.append((*_default(thread, members), VIA_DEFAULT))
    targets: list[Target] = []
    unroutable: list[str] = []
    atlas = members.atlas

    def add(session_id: str, name: str, via: str) -> None:
        if session_id == author_session or any(t.session_id == session_id for t in targets):
            return
        targets.append(Target(session_id, name, via))

    for agent, name, missing, via in wanted:
        if agent is not None and agent.live:
            add(agent.session_id, agent.name, via)
            continue
        reason = missing or f"{name} is {agent.state if agent else STATE_GONE}; Atlas will answer"
        if agent is not None:
            unroutable.append(name)
        if atlas is None or not atlas.live:
            state = atlas.state if atlas is not None else STATE_GONE
            who = (
                f"Atlas is {state}"
                if agent is not None and agent is atlas
                else f"{reason.split(';')[0]} and Atlas is {state} too"
            )
            notes.append(f"{who}: not delivered, post again once {_back(agent, atlas)} is back")
            continue
        if atlas.session_id == author_session:
            notes.append(reason.split(";")[0])
            continue
        notes.append(reason)
        add(atlas.session_id, atlas.name, VIA_FALLBACK)
    return Route(
        targets=tuple(targets),
        notes=tuple(dict.fromkeys(notes)),
        unroutable=tuple(dict.fromkeys(unroutable)),
        people=people,
    )


_HUNCH_RE: Final = re.compile(r"^\s*hunch\s*:\s*(\S.*)$", re.IGNORECASE | re.DOTALL)
_THINK_RE: Final = re.compile(r"^\s*i\s+think\b.+\bbecause\b", re.IGNORECASE | re.DOTALL)
_REQUEST_RE: Final = re.compile(
    r"^\s*we\s+(?:really\s+)?(?:need|should\s+(?:have|add|build))\b", re.IGNORECASE
)
_LEADING_MENTIONS_RE: Final = re.compile(
    rf"^(?:\s*@(?:atlas|sentinel|scout|pl:{_SLUG})\b[,:]?)+", re.I
)


def team_signal(thread: str, body: str) -> tuple[str, str] | None:
    """
    Whether a person's Room message is a hunch the ``team`` source keeps.

    In a project or PR thread people debug and instruct, so only an explicit
    ``hunch: …`` counts there.

    :returns: ``("hunch", text)`` for ``hunch: …`` (anywhere) or "I think …
        because …" in ``#factory`` or ``#discovery``; ``("request", text)`` for "we
        need …" and "we should have|add|build …" there; ``None`` otherwise.
    """
    text = _LEADING_MENTIONS_RE.sub("", body or "").strip()
    match = _HUNCH_RE.match(text)
    if match is not None:
        return "hunch", match.group(1).strip()
    if thread_kind(thread) not in (THREAD_FACTORY, THREAD_DISCOVERY):
        return None
    if _THINK_RE.match(text):
        return "hunch", text
    if _REQUEST_RE.match(text):
        return "request", text
    return None


__all__ = [
    "DEFAULT_RESPONDERS",
    "MAX_MENTIONS",
    "MENTION_SCOUT",
    "NODE_KINDS",
    "STATE_DEAD",
    "STATE_DONE",
    "STATE_GONE",
    "STATE_LIVE",
    "STATE_PAUSED",
    "VIA_DEFAULT",
    "VIA_FALLBACK",
    "VIA_MENTION",
    "Agent",
    "Members",
    "Route",
    "Target",
    "is_agent_mention",
    "normalize_mention",
    "parse_mentions",
    "routes",
    "team_signal",
]
