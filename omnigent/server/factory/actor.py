"""Who acted, and the short refs every factory object is mentioned by.

:class:`Actor` is the attribution every round-3 state change carries: a human
(user id and roster role), an agent (session id and factory role) or the server.

Refs are the ids humans and agents type and the cockpit renders as chips:

========================  ==============================================
``T-12``                  task 12 of the factory
``C-4``                   proposal (or definition change) 4
``D-7``                   decision 7
``INC-13`` / ``F-7``      incident 13 / finding 7
``!9630``                 PR number 9630
``#db-version``           project ``db-version`` (``#factory`` and
                          ``#improvements`` are the fixed Room threads)
``p:db-version``          project ``db-version`` (the explicit form)
``task:db-version/route`` task ``route`` of project ``db-version``
========================  ==============================================
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final, Literal

from omnigent.factory import FIXED_THREADS, PROJECT_SLUG_RE, TASK_KEY_RE

ActorKind = Literal["human", "agent", "server"]
RefKind = Literal[
    "task", "proposal", "decision", "incident", "finding", "pr", "project", "thread", "task_key"
]

SERVER_ID: Final = "server"


@dataclass(frozen=True)
class Actor:
    """
    The author of a state change.

    :param kind: ``human``, ``agent`` or ``server``.
    :param user_id: The human's user id; ``None`` for agents and the server.
    :param session_id: The agent's session id.
    :param role: Roster role of a human (``viewer`` … ``owner``, or ``creator``)
        or factory role of an agent (``lead``, ``project_lead``, …).
    :param title: Display text, e.g. ``"alice@caffeine.ai (operator)"`` or ``"Atlas"``.
    :param on_behalf_of: The human whose chat message an agent's act answers.
    """

    kind: ActorKind
    user_id: str | None
    session_id: str | None
    role: str | None
    title: str
    on_behalf_of: str | None = None

    @classmethod
    def human(cls, user_id: str, role: str | None = None, *, name: str | None = None) -> Actor:
        """:returns: A human actor titled ``"<name> (<role>)"``."""
        label = name or user_id
        return cls("human", user_id, None, role, f"{label} ({role})" if role else label)

    @classmethod
    def agent(
        cls,
        session_id: str,
        role: str | None = None,
        *,
        title: str | None = None,
        on_behalf_of: str | None = None,
    ) -> Actor:
        """:returns: An agent actor (a factory member or sub-agent session)."""
        return cls("agent", None, session_id, role, title or session_id, on_behalf_of)

    @classmethod
    def server(cls) -> Actor:
        """:returns: The server itself (detectors, the binder, expiry, auto-reverts)."""
        return cls("server", None, None, None, SERVER_ID)

    @property
    def id(self) -> str:
        """:returns: The value stored in ``author`` / ``created_by`` columns."""
        return self.user_id or self.session_id or SERVER_ID

    def event_data(self) -> dict[str, Any]:
        """:returns: The attribution keys every round-3 event's ``data`` carries."""
        return {
            "by": self.id,
            "by_kind": self.kind,
            "by_role": self.role,
            "on_behalf_of": self.on_behalf_of,
        }


@dataclass(frozen=True)
class Ref:
    """
    One parsed ref.

    :param kind: What the ref names.
    :param number: ``T-``/``C-``/``D-``/``INC-``/``F-`` number or the PR number.
    :param slug: Project slug (``project``, ``task_key``) or thread key (``thread``).
    :param key: Task key (``task_key``).
    """

    kind: RefKind
    number: int | None = None
    slug: str | None = None
    key: str | None = None

    @property
    def text(self) -> str:
        """:returns: The canonical spelling, e.g. ``"INC-13"`` or ``"#db-version"``."""
        return format_ref(self)


_NUMBERED: Final[dict[str, RefKind]] = {
    "T": "task",
    "C": "proposal",
    "D": "decision",
    "INC": "incident",
    "F": "finding",
}
_PREFIX: Final[dict[str, str]] = {kind: prefix for prefix, kind in _NUMBERED.items()}

_NUMBER = r"[1-9][0-9]{0,8}"
_SLUG = r"[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?"
_KEY = r"[a-z0-9][a-z0-9-]{0,47}"
_NUMBERED_RE = re.compile(rf"^(INC|T|C|D|F)-({_NUMBER})$")
_PR_RE = re.compile(rf"^!({_NUMBER})$")
_HASH_RE = re.compile(rf"^#({_SLUG})$")
_PROJECT_RE = re.compile(rf"^p:({_SLUG})$")
_TASK_KEY_RE = re.compile(rf"^task:({_SLUG})/({_KEY})$")
_FIND_RE = re.compile(
    rf"(?<![\w!#/:-])(?:(?:INC|T|C|D|F)-{_NUMBER}|!{_NUMBER}|#{_SLUG}"
    rf"|p:{_SLUG}|task:{_SLUG}/{_KEY})(?![\w/-])"
)


def parse_ref(text: object) -> Ref | None:
    """
    Parse one ref (surrounding whitespace allowed).

    :param text: e.g. ``"T-12"``, ``"!9630"``, ``"#db-version"`` or ``"task:db-version/route"``.
    :returns: The ref, or ``None`` when *text* is not one.
    """
    if not isinstance(text, str):
        return None
    value = text.strip()
    if match := _NUMBERED_RE.match(value):
        return Ref(_NUMBERED[match.group(1)], number=int(match.group(2)))
    if match := _PR_RE.match(value):
        return Ref("pr", number=int(match.group(1)))
    if match := _HASH_RE.match(value):
        slug = match.group(1)
        return Ref("thread" if slug in FIXED_THREADS else "project", slug=slug)
    if match := _PROJECT_RE.match(value):
        return Ref("project", slug=match.group(1))
    if match := _TASK_KEY_RE.match(value):
        return Ref("task_key", slug=match.group(1), key=match.group(2))
    return None


def find_refs(text: str) -> list[Ref]:
    """
    Every ref in free text, in order, without duplicates.

    :param text: A chat message or summary, e.g. ``"T-4 blocks !9631 (see D-7)"``.
    """
    seen: set[Ref] = set()
    out: list[Ref] = []
    for match in _FIND_RE.finditer(text):
        ref = parse_ref(match.group(0))
        if ref is not None and ref not in seen:
            seen.add(ref)
            out.append(ref)
    return out


def format_ref(ref: Ref) -> str:
    """
    The canonical spelling of *ref*.

    :raises ValueError: When the ref is missing the part its kind needs.
    """
    if ref.kind in _PREFIX and ref.number is not None:
        return f"{_PREFIX[ref.kind]}-{ref.number}"
    if ref.kind == "pr" and ref.number is not None:
        return f"!{ref.number}"
    if ref.kind in ("project", "thread") and ref.slug:
        return f"#{ref.slug}"
    if ref.kind == "task_key" and ref.slug and ref.key:
        return f"task:{ref.slug}/{ref.key}"
    raise ValueError(f"incomplete {ref.kind} ref: {ref!r}")


def task_ref(number: int) -> str:
    """:returns: e.g. ``"T-12"``."""
    return format_ref(Ref("task", number=number))


def proposal_ref(number: int) -> str:
    """:returns: e.g. ``"C-4"``."""
    return format_ref(Ref("proposal", number=number))


def decision_ref(number: int) -> str:
    """:returns: e.g. ``"D-7"``."""
    return format_ref(Ref("decision", number=number))


def incident_ref(number: int) -> str:
    """:returns: e.g. ``"INC-13"``."""
    return format_ref(Ref("incident", number=number))


def finding_ref(number: int) -> str:
    """:returns: e.g. ``"F-7"``."""
    return format_ref(Ref("finding", number=number))


def pr_ref(number: int) -> str:
    """:returns: e.g. ``"!9630"``."""
    return format_ref(Ref("pr", number=number))


def project_ref(slug: str) -> str:
    """
    :returns: e.g. ``"#db-version"``.
    :raises ValueError: When *slug* is not a project slug.
    """
    if not PROJECT_SLUG_RE.match(slug):
        raise ValueError(f"not a project slug: {slug!r}")
    return format_ref(Ref("project", slug=slug))


def task_key_ref(slug: str, key: str) -> str:
    """
    :returns: The intervention target of a task, e.g. ``"task:db-version/route"``.
    :raises ValueError: When *slug* or *key* is malformed.
    """
    if not PROJECT_SLUG_RE.match(slug) or not TASK_KEY_RE.match(key):
        raise ValueError(f"not a task target: {slug!r}/{key!r}")
    return format_ref(Ref("task_key", slug=slug, key=key))


def project_thread(slug: str) -> str:
    """:returns: The Room thread of a project, e.g. ``"project:db-version"``."""
    return f"project:{slug}"


def pr_thread(pr_id: str) -> str:
    """:returns: The Room thread of a PR (keyed by its row id), e.g. ``"pr:<id>"``."""
    return f"pr:{pr_id}"


__all__ = [
    "Actor",
    "Ref",
    "decision_ref",
    "find_refs",
    "finding_ref",
    "format_ref",
    "incident_ref",
    "parse_ref",
    "pr_ref",
    "pr_thread",
    "project_ref",
    "project_thread",
    "proposal_ref",
    "task_key_ref",
    "task_ref",
]
