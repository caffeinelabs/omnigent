"""Who answers a Room message: the routing table of the Room's threads.

:func:`routes` is a pure function of the thread, the message's mentions and
the factory's routable members (:class:`Members`, built by
:func:`omnigent.server.factory.chat.room_members`):

========================  ==============================================
``factory``               Atlas (the factory root)
``improvements``          Sentinel (the non-root lead), else Atlas
``project:<slug>``        that project's PL; Atlas while it has none
``pr:<id>``               the PL of the PR's project; Atlas while it has none
========================  ==============================================

- ``@atlas``, ``@sentinel`` and ``@pl:<slug>`` pick the responders instead of
  the thread's default (mention the default too to add to it).
- ``@<person>`` only notifies; it never routes.
- A message an agent wrote reaches other agents only through such a mention,
  never through a thread default, so agents talking in the Room never loop.
- A responder that is paused or gone is replaced by Atlas, with a system line
  saying so. When Atlas cannot answer either, the line says the message will
  not be delivered (it is never queued for a resume).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from omnigent.factory import (
    THREAD_FACTORY,
    THREAD_IMPROVEMENTS,
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
MENTION_PL_PREFIX: Final = "@pl:"
MAX_MENTIONS: Final = 20

_PROJECT: Final = THREAD_PROJECT_PREFIX[:-1]
_PR: Final = THREAD_PR_PREFIX[:-1]
_SLUG = r"[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?"
_EMAIL = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
_MENTION_RE: Final = re.compile(
    rf"(?<![\w@./-])@(atlas|sentinel|pl:{_SLUG}|{_EMAIL})(?![\w@-])", re.IGNORECASE
)
_ONE_MENTION_RE: Final = re.compile(rf"^@?(atlas|sentinel|pl:{_SLUG}|{_EMAIL})$", re.IGNORECASE)


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
    """

    atlas: Agent | None
    sentinel: Agent | None = None
    pls: Mapping[str, Agent] = field(default_factory=dict)
    pr_projects: Mapping[str, str] = field(default_factory=dict)
    pr_numbers: Mapping[str, int] = field(default_factory=dict)


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
    :param value: e.g. ``"@PL:db-version"``, ``"sentinel"`` or ``"@mia@caffeine.ai"``.
    :returns: ``"@pl:db-version"`` / ``"@sentinel"`` / ``"@mia@caffeine.ai"``, or ``None``.
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
    """:returns: Whether *mention* names an agent (``@atlas``, ``@sentinel``, ``@pl:<slug>``)."""
    return mention in (MENTION_ATLAS, MENTION_SENTINEL) or mention.startswith(MENTION_PL_PREFIX)


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
    return None, "nobody", None


def _mentioned(mention: str, members: Members) -> tuple[Agent | None, str, str | None]:
    """:returns: ``(agent, its name, why Atlas answers instead)`` for an agent mention."""
    if mention == MENTION_ATLAS:
        return members.atlas, "Atlas", None
    if mention == MENTION_SENTINEL:
        if members.sentinel is None:
            return None, "Sentinel", "Sentinel is not up; Atlas will answer"
        return members.sentinel, members.sentinel.name, None
    slug = mention[len(MENTION_PL_PREFIX) :]
    pl = members.pls.get(slug)
    return pl, _pl_name(slug), None if pl else f"{_pl_name(slug)} not up yet; Atlas has it"


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
        if is_agent_mention(mention):
            wanted.append((*_mentioned(mention, members), VIA_MENTION))
    if not wanted and author_kind == "human":
        wanted.append((*_default(thread, members), VIA_DEFAULT))
    targets: list[Target] = []
    notes: list[str] = []
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


__all__ = [
    "MAX_MENTIONS",
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
]
