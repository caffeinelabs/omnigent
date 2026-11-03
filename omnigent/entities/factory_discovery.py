"""Discovery ledger entities: rows of the ``factory_signals`` family of tables.

Plain frozen dataclasses, as in :mod:`omnigent.entities.factory`: enum-like
columns carry their names (the store owns the codes), JSON columns carry
decoded values, and ``to_dict()`` gives a JSON-ready copy.

- :class:`FactorySignal` (``S-n``): one piece of evidence. Only the server's
  ledger writes one; its evidence (source, pointer, excerpt, account) never
  changes. Labels are the only later writes, and retention or a withdrawn
  hunch clears the excerpt while the row and its counts stay.
- :class:`FactorySignalLink`: a signal attached to an opportunity (or
  detached from it, kept for history and Undo).
- :class:`FactoryOpportunity` (``O-n``): a user need backed by signals. Its
  status, evidence and score are computed by the server, never stored.
- :class:`FactoryScoreSnapshot`: the inputs and outputs of one score at a
  decision point, for later backtests.
- :class:`FactorySourceState`: a source's cursor, health and today's usage.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from typing import Any


def _plain(obj: Any) -> dict[str, Any]:
    return copy.deepcopy(asdict(obj))


@dataclass(frozen=True)
class FactorySignal:
    """
    One signal ``S-<number>`` (``factory_signals``).

    :param source: The source id (a ``discovery.sources`` slug, ``team`` or ``factory``).
    :param source_kind: ``repo`` / ``team`` / ``web`` / … / ``factory`` (server-written).
    :param kind: ``request`` / ``pain`` / … / ``hunch`` / ``idea`` / ``simulation``;
        ``None`` until a user-text signal is labelled.
    :param say_do: ``say`` (what people tell us) or ``do`` (an aggregate of what they did).
    :param excerpt: The redacted, clipped text; ``None`` once expired or withdrawn.
    :param excerpt_sha: sha256 of the excerpt as stored (kept after expiry).
    :param account: A salted hash of the account (per factory), or ``None``.
    :param redactions: Placeholders written per kind, e.g. ``{"email": 1}``.
    :param injection: Injection score 0-100 at ingest.
    :param flags: e.g. ``injection_suspect``, ``team_author``, ``duplicate_of:S-7``.
    :param author: For hunches and ideas: the person's user id or the agent's session id.
    :param meta: Server-written structure, e.g. a readout's ``verdict`` or a behaviour ``reach``.
    """

    factory_id: str
    number: int
    source: str
    source_kind: str
    say_do: str = "say"
    kind: str | None = None
    excerpt: str | None = None
    excerpt_sha: str | None = None
    pointer: str | None = None
    query_event: str | None = None
    observed_at: int | None = None
    ingested_at: int = 0
    account: str | None = None
    segments: list[str] = field(default_factory=list)
    redactions: dict[str, int] = field(default_factory=dict)
    privacy: str = "public"
    expires_at: int | None = None
    injection: int = 0
    flags: list[str] = field(default_factory=list)
    author: str | None = None
    author_kind: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    paraphrase: str | None = None
    intensity: str | None = None
    force: str | None = None
    labelled_by: str | None = None
    labelled_at: int | None = None
    withdrawn_at: int | None = None
    withdrawn_by: str | None = None
    expired_at: int | None = None
    workspace_id: int = 0

    @property
    def ref(self) -> str:
        """:returns: e.g. ``"S-1042"``."""
        return f"S-{self.number}"

    @property
    def seen_at(self) -> int:
        """:returns: When it was said or measured (when stored, if unknown)."""
        return self.observed_at or self.ingested_at

    @property
    def gone(self) -> bool:
        """:returns: Whether its text is gone (expired or withdrawn)."""
        return self.withdrawn_at is not None or self.expired_at is not None

    def to_dict(self) -> dict[str, Any]:
        """:returns: A JSON-ready copy of the row."""
        return _plain(self)


@dataclass(frozen=True)
class FactorySignalLink:
    """
    Signal ``S-<signal>`` attached to opportunity ``O-<opportunity>`` (``factory_signal_links``).

    :param counter: The signal argues against the opportunity.
    :param confidence: How sure the attacher is, 0-100 (``None``: not said).
    :param via: Provenance of a link the server moved, e.g. ``merge:O-3``.
    :param detached_at: Set when someone said "not about this" (kept for history).
    """

    factory_id: str
    signal: int
    opportunity: int
    attached_by: str
    attached_kind: str = "human"
    attached_at: int = 0
    counter: bool = False
    confidence: int | None = None
    via: str | None = None
    detached_at: int | None = None
    detached_by: str | None = None
    detach_reason: str | None = None
    workspace_id: int = 0

    @property
    def active(self) -> bool:
        """:returns: Whether the link counts (it is not detached)."""
        return self.detached_at is None

    def to_dict(self) -> dict[str, Any]:
        """:returns: A JSON-ready copy of the row."""
        return _plain(self)


@dataclass(frozen=True)
class FactoryOpportunity:
    """
    One opportunity ``O-<number>`` (``factory_opportunities``).

    :param statement: The need in users' words, in job form (≤ 200 chars).
    :param goal: The goal it sits under (``G-n``), or ``None`` (unassigned).
    :param non_goal: The strategy non-goal it was moved under (it is then parked).
    :param alternatives: At least two different solutions (shows a need, not a solution).
    :param bet_note: Scout's draft: ideas, the riskiest assumption, the cheapest test.
    :param votes: ``{user id: +1 | -1}``; votes never change the score.
    :param park: ``{until?, reason?, by, at}`` while snoozed.
    :param kill: ``{reason, note?, by, at, level}`` (``level``: the evidence level at the kill).
    :param research: The open "research this" ask, ``{question, budget_usd, due, by, at, state}``.
    :param plan_request: The last "plan this", ``{by, at, chat?, bet_anyway}``.
    :param declines: A lead's declines, ``[{reason, by, at}]``.
    :param origin: Where it came from, e.g. ``{"hunch": "S-12"}`` or ``{"split": "O-3"}``.
    :param result: Written by readouts (later): ``{measured_at?, decision?: keep|drop}``.
    """

    factory_id: str
    number: int
    title: str
    statement: str
    created_by: str
    created_kind: str = "agent"
    problem: str | None = None
    goal: str | None = None
    non_goal: str | None = None
    segments: list[str] = field(default_factory=list)
    area: str | None = None
    alternatives: list[str] = field(default_factory=list)
    bet_note: dict[str, Any] | None = None
    votes: dict[str, int] = field(default_factory=dict)
    pinned_by: str | None = None
    pinned_at: int | None = None
    park: dict[str, Any] | None = None
    kill: dict[str, Any] | None = None
    merged_into: int | None = None
    split_from: int | None = None
    research: dict[str, Any] | None = None
    plan_request: dict[str, Any] | None = None
    declines: list[dict[str, Any]] = field(default_factory=list)
    origin: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    created_at: int = 0
    updated_at: int = 0
    workspace_id: int = 0

    @property
    def ref(self) -> str:
        """:returns: e.g. ``"O-12"``."""
        return f"O-{self.number}"

    def to_dict(self) -> dict[str, Any]:
        """:returns: A JSON-ready copy of the row."""
        return _plain(self)


@dataclass(frozen=True)
class FactoryScoreSnapshot:
    """
    One opportunity's score at a decision point (``factory_score_snapshots``).

    :param reason: What the snapshot marks, e.g. ``plan``, ``kill``, ``daily``.
    :param def_version: The definition version whose rubric scored it.
    :param inputs: The facts the score read (accounts, source types, intensity labels…).
    :param parts: ``{reach_s, intensity, fit, confidence}``.
    """

    id: str
    factory_id: str
    opportunity: int
    at: int
    reason: str
    rank: float
    band: str
    level: str
    def_version: int | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    parts: dict[str, Any] = field(default_factory=dict)
    workspace_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        """:returns: A JSON-ready copy of the row."""
        return _plain(self)


@dataclass(frozen=True)
class FactorySourceState:
    """
    One source's running state (``factory_source_state``).

    :param cursor: Where the next sync resumes (opaque to everyone but the source).
    :param usage_day: The UTC day ``calls_today`` counts (``YYYY-MM-DD``).
    :param data: Anything else the source keeps between runs.
    """

    factory_id: str
    source: str
    cursor: str | None = None
    last_ok_at: int | None = None
    last_error_at: int | None = None
    last_error: str | None = None
    failures: int = 0
    usage_day: str | None = None
    calls_today: int = 0
    signals_total: int = 0
    data: dict[str, Any] = field(default_factory=dict)
    updated_at: int = 0
    workspace_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        """:returns: A JSON-ready copy of the row."""
        return _plain(self)


__all__ = [
    "FactoryOpportunity",
    "FactoryScoreSnapshot",
    "FactorySignal",
    "FactorySignalLink",
    "FactorySourceState",
]
