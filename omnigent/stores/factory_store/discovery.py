"""The discovery ledger's part of the factory store.

:class:`DiscoveryStore` is the contract every factory store implements for
the ``factory_signals`` family of tables; :class:`InMemoryDiscoveryStore` is
the in-memory half (``sqlalchemy_discovery`` holds the SQL half).

The ledger is append-only by construction: :meth:`DiscoveryStore.add_signal`
is the only way a signal's evidence (source, pointer, excerpt, account) is
written, and nothing rewrites it. :meth:`~DiscoveryStore.label_signal` sets
only the label columns, and :meth:`~DiscoveryStore.clear_signal_text` only
drops the text (retention, or a person withdrawing their own hunch). The
server's ledger module is the only caller of the writers.

This module imports nothing from the package ``__init__`` (which imports it).
"""

from __future__ import annotations

import builtins
import copy
import hashlib
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Final, Literal

from omnigent.db.db_models import current_workspace_id, uuid_to_bytes
from omnigent.db.enum_codecs import (
    FACTORY_ACTOR_KIND,
    FACTORY_FORCE,
    FACTORY_INTENSITY,
    FACTORY_PRIVACY,
    FACTORY_READOUT_DECISION,
    FACTORY_READOUT_METHOD,
    FACTORY_READOUT_RECOMMENDATION,
    FACTORY_READOUT_STATUS,
    FACTORY_READOUT_VERDICT,
    FACTORY_SAY_DO,
    FACTORY_SIGNAL_KIND,
    FACTORY_SOURCE_KIND,
)
from omnigent.entities.factory_discovery import (
    FactoryOpportunity,
    FactoryReadout,
    FactoryScoreSnapshot,
    FactorySignal,
    FactorySignalLink,
    FactorySourceState,
)

if TYPE_CHECKING:
    import threading

SIGNAL_FIELDS: Final = frozenset(
    {
        "source",
        "source_kind",
        "kind",
        "say_do",
        "excerpt",
        "excerpt_sha",
        "pointer",
        "query_event",
        "observed_at",
        "ingested_at",
        "account",
        "segments",
        "redactions",
        "privacy",
        "expires_at",
        "injection",
        "flags",
        "author",
        "author_kind",
        "meta",
    }
)
"""What :meth:`DiscoveryStore.add_signal` writes (the evidence; never rewritten)."""
LABEL_FIELDS: Final = frozenset({"paraphrase", "kind", "intensity", "force", "flags"})
"""What :meth:`DiscoveryStore.label_signal` may set."""
OPPORTUNITY_FIELDS: Final = frozenset(
    {
        "title",
        "statement",
        "problem",
        "goal",
        "non_goal",
        "segments",
        "area",
        "alternatives",
        "bet_note",
        "votes",
        "pinned_by",
        "pinned_at",
        "park",
        "kill",
        "merged_into",
        "split_from",
        "research",
        "plan_request",
        "declines",
        "origin",
        "result",
    }
)
"""What :meth:`DiscoveryStore.create_opportunity` / ``update_opportunity`` accept."""
SOURCE_STATE_FIELDS: Final = frozenset(
    {
        "cursor",
        "last_ok_at",
        "last_error_at",
        "last_error",
        "failures",
        "usage_day",
        "calls_today",
        "signals_total",
        "data",
    }
)
SNAPSHOT_FIELDS: Final = frozenset(
    {"opportunity", "at", "reason", "def_version", "rank", "band", "level", "inputs", "parts"}
)
READOUT_FIELDS: Final = frozenset(
    {
        "status",
        "opportunity",
        "method",
        "plan_version",
        "released_at",
        "due_at",
        "written_at",
        "verdict",
        "recommendation",
        "decision",
        "decided_by",
        "decided_at",
        "signal",
        "decision_id",
        "plan",
        "result",
        "answer",
        "review",
        "stamp",
    }
)
"""What :meth:`DiscoveryStore.add_readout` / ``update_readout`` accept."""
READOUT_JSON: Final = frozenset({"plan", "result", "answer", "review", "stamp"})
READOUT_ENUMS: Final[dict[str, dict[str, int]]] = {
    "status": FACTORY_READOUT_STATUS,
    "method": FACTORY_READOUT_METHOD,
    "verdict": FACTORY_READOUT_VERDICT,
    "recommendation": FACTORY_READOUT_RECOMMENDATION,
    "decision": FACTORY_READOUT_DECISION,
}
TEXT_LIMITS: Final[dict[str, int]] = {
    "source": 32,
    "pointer": 500,
    "account": 64,
    "author": 128,
    "paraphrase": 200,
    "title": 120,
    "statement": 300,
    "problem": 200,
    "goal": 16,
    "non_goal": 200,
    "area": 32,
    "via": 32,
    "detach_reason": 200,
    "last_error": 300,
    "reason": 32,
    "decided_by": 128,
}
_ENUMS: Final[dict[str, dict[str, int]]] = {
    "source_kind": FACTORY_SOURCE_KIND,
    "kind": FACTORY_SIGNAL_KIND,
    "say_do": FACTORY_SAY_DO,
    "privacy": FACTORY_PRIVACY,
    "intensity": FACTORY_INTENSITY,
    "force": FACTORY_FORCE,
    "author_kind": FACTORY_ACTOR_KIND,
    "attached_kind": FACTORY_ACTOR_KIND,
    "created_kind": FACTORY_ACTOR_KIND,
}
SIGNAL_JSON: Final = frozenset({"segments", "redactions", "flags", "meta"})
OPPORTUNITY_JSON: Final = frozenset(
    {
        "segments",
        "alternatives",
        "bet_note",
        "votes",
        "park",
        "kill",
        "research",
        "plan_request",
        "declines",
        "origin",
        "result",
    }
)
EMPTY_JSON: Final[dict[str, Any]] = {
    "segments": [],
    "redactions": {},
    "flags": [],
    "meta": {},
    "alternatives": [],
    "votes": {},
    "declines": [],
    "data": {},
    "plan": {},
    "result": {},
    "stamp": {},
}
"""JSON columns stored as ``NULL`` when empty and read back as these."""


def now_s() -> int:
    """:returns: Epoch seconds."""
    return int(time.time())


def norm_id(value: str) -> str:
    """:returns: *value* as bare hex, as a ``Uuid16`` column stores it."""
    return uuid_to_bytes(value).hex()


def pointer_key(pointer: str | None) -> str | None:
    """:returns: The dedupe key of a signal's pointer (sha256 hex), or ``None``."""
    if not pointer:
        return None
    return hashlib.sha256(pointer.encode("utf-8")).hexdigest()


def check_fields(fields: Mapping[str, Any], allowed: frozenset[str], where: str) -> None:
    """:raises TypeError: When *fields* holds a key outside *allowed*."""
    unknown = sorted(set(fields) - allowed)
    if unknown:
        raise TypeError(f"{where}() got unexpected field(s): {', '.join(unknown)}")


def check_enums(fields: Mapping[str, Any]) -> None:
    """:raises ValueError: On an unknown enum name (``None`` passes)."""
    for name, table in _ENUMS.items():
        value = fields.get(name)
        if value is not None and value not in table:
            raise ValueError(f"unknown {name} value: {value!r}")


def check_readout_enums(fields: Mapping[str, Any]) -> None:
    """:raises ValueError: On an unknown readout enum name (``None`` passes)."""
    for name, table in READOUT_ENUMS.items():
        value = fields.get(name)
        if value is not None and value not in table:
            raise ValueError(f"unknown readout {name} value: {value!r}")


def clip(fields: Mapping[str, Any]) -> dict[str, Any]:
    """:returns: *fields* with free text cut to its column bound."""
    out = dict(fields)
    for name, limit in TEXT_LIMITS.items():
        value = out.get(name)
        if isinstance(value, str) and len(value) > limit:
            out[name] = value[:limit]
    return out


@dataclass(frozen=True)
class SignalFilter:
    """
    What :meth:`DiscoveryStore.list_signals` returns.

    :param kinds: Kind names; ``"unlabelled"`` matches a signal without a kind.
    :param before: Only numbers below this (paging, newest first).
    """

    sources: tuple[str, ...] | None = None
    kinds: tuple[str, ...] | None = None
    since: int | None = None
    before: int | None = None
    numbers: tuple[int, ...] | None = None

    def matches(self, row: FactorySignal) -> bool:
        """:returns: Whether *row* passes the filter."""
        if self.sources is not None and row.source not in self.sources:
            return False
        if self.kinds is not None:
            name = row.kind if row.kind is not None else "unlabelled"
            if name not in self.kinds:
                return False
        if self.since is not None and row.ingested_at < self.since:
            return False
        if self.before is not None and row.number >= self.before:
            return False
        return self.numbers is None or row.number in self.numbers


UNSORTED_SKIP: Final = ("hunch", "idea", "simulation")
"""Signal kinds never waiting to be sorted into an opportunity."""
HUNCH_KINDS: Final = ("hunch", "idea")


@dataclass(frozen=True)
class SignalCounts:
    """
    What :meth:`DiscoveryStore.signal_counts` counts (no row is decoded but segments).

    :param recent: Signals stored at or after ``since``; ``by_source`` splits them.
    :param segments: Signals per segment id stored at or after ``segments_since``.
    :param unsorted: Live user signals (not a hunch, idea, simulation or suspect,
        text not gone) with no attached link.
    :param flagged: Signals whose injection score reached ``suspect_at``.
    :param hunches: Hunches and ideas whose text is not gone.
    """

    total: int = 0
    recent: int = 0
    by_source: dict[str, int] = field(default_factory=dict)
    segments: dict[str, int] = field(default_factory=dict)
    unsorted: int = 0
    flagged: int = 0
    hunches: int = 0


def is_unsorted(row: FactorySignal, suspect_at: int) -> bool:
    """:returns: Whether *row* is a live user signal waiting to be sorted (links aside)."""
    return (
        row.kind not in UNSORTED_SKIP
        and row.injection < suspect_at
        and row.expired_at is None
        and row.withdrawn_at is None
    )


class DiscoveryStore(ABC):
    """The discovery ledger's store methods (synchronous; workspace-scoped)."""

    # ── Signals ─────────────────────────────────────────────────

    @abstractmethod
    def add_signal(self, factory_id: str, **fields: Any) -> tuple[FactorySignal, bool]:
        """
        Store one signal as the factory's next ``S-n``.

        A signal whose ``(source, pointer)`` is already stored is not stored again.

        :param fields: Of :data:`SIGNAL_FIELDS`; ``source``, ``source_kind``,
            ``privacy`` and ``ingested_at`` are required.
        :returns: ``(row, existed)``.
        :raises TypeError: On an unknown field. :raises ValueError: On an unknown enum name.
        """

    @abstractmethod
    def get_signal(self, factory_id: str, number: int) -> FactorySignal | None:
        """:returns: ``S-<number>`` of the factory."""

    @abstractmethod
    def list_signals(
        self, factory_id: str, query: SignalFilter | None = None, *, limit: int = 200
    ) -> builtins.list[FactorySignal]:
        """:returns: The factory's signals matching *query*, newest (highest number) first."""

    @abstractmethod
    def count_signals(self, factory_id: str, *, since: int | None = None) -> int:
        """:returns: How many signals the factory stored at or after *since*."""

    @abstractmethod
    def signal_counts(
        self, factory_id: str, *, since: int, segments_since: int, suspect_at: int
    ) -> SignalCounts:
        """:returns: The ledger's counts for the Discovery header, counted by the database."""

    @abstractmethod
    def label_signal(
        self, factory_id: str, number: int, *, labelled_by: str, **labels: Any
    ) -> FactorySignal | None:
        """
        Set a signal's labels (of :data:`LABEL_FIELDS`); its evidence stays as stored.

        :returns: The row, or ``None`` when it does not exist.
        """

    @abstractmethod
    def clear_signal_text(
        self,
        factory_id: str,
        number: int,
        *,
        reason: Literal["withdrawn", "expired"],
        by: str | None = None,
        at: int | None = None,
    ) -> FactorySignal | None:
        """
        Drop a signal's excerpt and paraphrase (the row and its links stay).

        :returns: The row, or ``None`` when it does not exist.
        """

    @abstractmethod
    def expire_signal_texts(self, now: int, *, limit: int = 500) -> int:
        """Drop the text of up to *limit* signals past ``expires_at`` (every factory)."""

    # ── Links ───────────────────────────────────────────────────

    @abstractmethod
    def put_link(
        self,
        factory_id: str,
        signal: int,
        opportunity: int,
        *,
        attached_by: str,
        attached_kind: str,
        counter: bool = False,
        confidence: int | None = None,
        via: str | None = None,
    ) -> FactorySignalLink:
        """Attach a signal to an opportunity (re-attaching a detached link)."""

    @abstractmethod
    def detach_link(
        self,
        factory_id: str,
        signal: int,
        opportunity: int,
        *,
        by: str,
        reason: str | None = None,
    ) -> FactorySignalLink | None:
        """:returns: The detached link, or ``None`` when none was attached."""

    @abstractmethod
    def list_links(
        self,
        factory_id: str,
        *,
        signals: Sequence[int] | None = None,
        opportunities: Sequence[int] | None = None,
        active_only: bool = True,
    ) -> builtins.list[FactorySignalLink]:
        """:returns: Links of the factory, by opportunity then signal."""

    # ── Opportunities ───────────────────────────────────────────

    @abstractmethod
    def create_opportunity(
        self,
        factory_id: str,
        *,
        title: str,
        statement: str,
        created_by: str,
        created_kind: str,
        **fields: Any,
    ) -> FactoryOpportunity:
        """Store a new opportunity as the factory's next ``O-n``."""

    @abstractmethod
    def get_opportunity(self, factory_id: str, number: int) -> FactoryOpportunity | None:
        """:returns: ``O-<number>`` of the factory."""

    @abstractmethod
    def list_opportunities(
        self, factory_id: str, *, limit: int = 1000
    ) -> builtins.list[FactoryOpportunity]:
        """:returns: The factory's opportunities, oldest first."""

    @abstractmethod
    def update_opportunity(
        self, factory_id: str, number: int, **fields: Any
    ) -> FactoryOpportunity | None:
        """:returns: The updated row, or ``None`` when it does not exist."""

    # ── Score snapshots ─────────────────────────────────────────

    @abstractmethod
    def add_score_snapshots(self, factory_id: str, rows: Sequence[Mapping[str, Any]]) -> int:
        """Store score snapshots (each of :data:`SNAPSHOT_FIELDS`). :returns: How many."""

    @abstractmethod
    def list_score_snapshots(
        self,
        factory_id: str,
        *,
        opportunity: int | None = None,
        since: int | None = None,
        reason: str | None = None,
        limit: int = 200,
    ) -> builtins.list[FactoryScoreSnapshot]:
        """:returns: Snapshots (of *reason*, when given), newest first."""

    # ── Readouts ────────────────────────────────────────────────

    @abstractmethod
    def add_readout(
        self, factory_id: str, *, proposal: int, day: int, **fields: Any
    ) -> tuple[FactoryReadout, bool]:
        """
        Store one bet's readout for one day (``factory_readouts``).

        A readout already stored for ``(proposal, day)`` is returned as it is.

        :param fields: Of :data:`READOUT_FIELDS`; ``status``, ``method``,
            ``released_at`` and ``due_at`` are required.
        :returns: ``(row, existed)``.
        :raises TypeError: On an unknown field. :raises ValueError: On an unknown enum name.
        """

    @abstractmethod
    def get_readout(self, factory_id: str, readout_id: str) -> FactoryReadout | None:
        """:returns: The factory's readout *readout_id*."""

    @abstractmethod
    def list_readouts(
        self,
        factory_id: str,
        *,
        proposal: int | None = None,
        opportunity: int | None = None,
        statuses: Sequence[str] | None = None,
        due_before: int | None = None,
        limit: int = 500,
    ) -> builtins.list[FactoryReadout]:
        """:returns: The factory's readouts, by bet then day."""

    @abstractmethod
    def update_readout(
        self,
        factory_id: str,
        readout_id: str,
        *,
        expect_status: str | Sequence[str] | None = None,
        **fields: Any,
    ) -> FactoryReadout | None:
        """
        Update a readout (compare-and-set on its status when *expect_status* is given).

        :returns: The updated row, or ``None`` when it does not exist or its
            status is no longer *expect_status*.
        """

    # ── Feedback a person took back ─────────────────────────────

    @abstractmethod
    def retract_feedback(self, factory_id: str, ids: Sequence[str]) -> int:
        """Delete feedback rows *ids* (an Undo takes its calibration back). :returns: How many."""

    # ── Source state ────────────────────────────────────────────

    @abstractmethod
    def get_source_state(self, factory_id: str, source: str) -> FactorySourceState | None:
        """:returns: The state of one source."""

    @abstractmethod
    def put_source_state(self, factory_id: str, source: str, **fields: Any) -> FactorySourceState:
        """Insert or update one source's state (of :data:`SOURCE_STATE_FIELDS`)."""

    @abstractmethod
    def list_source_states(self, factory_id: str) -> builtins.list[FactorySourceState]:
        """:returns: Every source's state, by source id."""

    @abstractmethod
    def charge_source_call(
        self, factory_id: str, source: str, *, day: str, limit: int, calls: int = 1
    ) -> tuple[FactorySourceState, bool]:
        """
        Count *calls* against a source's daily cap, atomically.

        ``calls_today`` starts again when ``usage_day`` is not *day*.

        :param day: Today, UTC (``YYYY-MM-DD``).
        :returns: ``(state, charged)``; nothing is written when the calls
            would pass *limit*.
        """


# ── In memory ──────────────────────────────────────────────────


@dataclass
class _Tables:
    signals: dict[tuple[int, str, int], FactorySignal] = field(default_factory=dict)
    links: dict[tuple[int, str, int, int], FactorySignalLink] = field(default_factory=dict)
    opportunities: dict[tuple[int, str, int], FactoryOpportunity] = field(default_factory=dict)
    snapshots: dict[tuple[int, str], FactoryScoreSnapshot] = field(default_factory=dict)
    sources: dict[tuple[int, str, str], FactorySourceState] = field(default_factory=dict)
    readouts: dict[tuple[int, str], FactoryReadout] = field(default_factory=dict)


def _copy(row: Any) -> Any:
    return copy.deepcopy(row)


def _json_defaults(fields: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(fields)
    for key, empty in EMPTY_JSON.items():
        if key in out and out[key] is None:
            out[key] = copy.deepcopy(empty)
    return out


class InMemoryDiscoveryStore:
    """The in-memory half of :class:`DiscoveryStore` (mixed into the in-memory factory store)."""

    _lock: threading.RLock

    def _tables(self) -> _Tables:
        tables = getattr(self, "_discovery", None)
        if tables is None:
            with self._lock:
                tables = getattr(self, "_discovery", None)
                if tables is None:
                    tables = _Tables()
                    self._discovery = tables
        return tables

    # ── Signals ─────────────────────────────────────────────────

    def add_signal(self, factory_id: str, **fields: Any) -> tuple[FactorySignal, bool]:
        check_fields(fields, SIGNAL_FIELDS, "add_signal")
        check_enums(fields)
        values = _json_defaults(clip(fields))
        fid = norm_id(factory_id)
        ws = current_workspace_id()
        key = pointer_key(values.get("pointer"))
        if values.get("query_event") is not None:
            values["query_event"] = norm_id(values["query_event"])
        tables = self._tables()
        with self._lock:
            mine = [r for k, r in tables.signals.items() if k[0] == ws and k[1] == fid]
            if key is not None:
                for row in mine:
                    if row.source == values["source"] and pointer_key(row.pointer) == key:
                        return _copy(row), True
            number = max((r.number for r in mine), default=0) + 1
            row = FactorySignal(factory_id=fid, number=number, workspace_id=ws, **values)
            tables.signals[(ws, fid, number)] = row
            return _copy(row), False

    def get_signal(self, factory_id: str, number: int) -> FactorySignal | None:
        with self._lock:
            row = self._tables().signals.get((current_workspace_id(), norm_id(factory_id), number))
            return _copy(row) if row is not None else None

    def list_signals(
        self, factory_id: str, query: SignalFilter | None = None, *, limit: int = 200
    ) -> builtins.list[FactorySignal]:
        fid = norm_id(factory_id)
        ws = current_workspace_id()
        query = query or SignalFilter()
        with self._lock:
            rows = [
                _copy(r)
                for k, r in self._tables().signals.items()
                if k[0] == ws and k[1] == fid and query.matches(r)
            ]
        rows.sort(key=lambda r: r.number, reverse=True)
        return rows[:limit]

    def count_signals(self, factory_id: str, *, since: int | None = None) -> int:
        fid = norm_id(factory_id)
        ws = current_workspace_id()
        with self._lock:
            return sum(
                1
                for k, r in self._tables().signals.items()
                if k[0] == ws and k[1] == fid and (since is None or r.ingested_at >= since)
            )

    def signal_counts(
        self, factory_id: str, *, since: int, segments_since: int, suspect_at: int
    ) -> SignalCounts:
        fid = norm_id(factory_id)
        ws = current_workspace_id()
        tables = self._tables()
        with self._lock:
            rows = [r for k, r in tables.signals.items() if k[0] == ws and k[1] == fid]
            linked = {
                k[2]
                for k, link in tables.links.items()
                if k[0] == ws and k[1] == fid and link.detached_at is None
            }
        by_source: dict[str, int] = {}
        segments: dict[str, int] = {}
        for row in rows:
            if row.ingested_at >= since:
                by_source[row.source] = by_source.get(row.source, 0) + 1
            if row.ingested_at >= segments_since:
                for segment in dict.fromkeys(row.segments):
                    segments[segment] = segments.get(segment, 0) + 1
        return SignalCounts(
            total=len(rows),
            recent=sum(by_source.values()),
            by_source=by_source,
            segments=segments,
            unsorted=sum(1 for r in rows if r.number not in linked and is_unsorted(r, suspect_at)),
            flagged=sum(1 for r in rows if r.injection >= suspect_at),
            hunches=sum(1 for r in rows if r.kind in HUNCH_KINDS and not r.gone),
        )

    def label_signal(
        self, factory_id: str, number: int, *, labelled_by: str, **labels: Any
    ) -> FactorySignal | None:
        check_fields(labels, LABEL_FIELDS, "label_signal")
        check_enums(labels)
        key = (current_workspace_id(), norm_id(factory_id), number)
        tables = self._tables()
        with self._lock:
            row = tables.signals.get(key)
            if row is None:
                return None
            values = _json_defaults(clip(labels))
            row = replace(row, **values, labelled_by=labelled_by[:128], labelled_at=now_s())
            tables.signals[key] = row
            return _copy(row)

    def clear_signal_text(
        self,
        factory_id: str,
        number: int,
        *,
        reason: Literal["withdrawn", "expired"],
        by: str | None = None,
        at: int | None = None,
    ) -> FactorySignal | None:
        key = (current_workspace_id(), norm_id(factory_id), number)
        when = at if at is not None else now_s()
        tables = self._tables()
        with self._lock:
            row = tables.signals.get(key)
            if row is None:
                return None
            row = replace(row, excerpt=None, paraphrase=None)
            if reason == "withdrawn":
                row = replace(row, withdrawn_at=when, withdrawn_by=by)
            else:
                row = replace(row, expired_at=when)
            tables.signals[key] = row
            return _copy(row)

    def expire_signal_texts(self, now: int, *, limit: int = 500) -> int:
        ws = current_workspace_id()
        tables = self._tables()
        done = 0
        with self._lock:
            for key, row in list(tables.signals.items()):
                if done >= limit:
                    break
                due = row.expires_at is not None and row.expires_at <= now
                if key[0] == ws and due and not row.gone:
                    tables.signals[key] = replace(
                        row, excerpt=None, paraphrase=None, expired_at=now
                    )
                    done += 1
        return done

    # ── Links ───────────────────────────────────────────────────

    def put_link(
        self,
        factory_id: str,
        signal: int,
        opportunity: int,
        *,
        attached_by: str,
        attached_kind: str,
        counter: bool = False,
        confidence: int | None = None,
        via: str | None = None,
    ) -> FactorySignalLink:
        check_enums({"attached_kind": attached_kind})
        fid = norm_id(factory_id)
        ws = current_workspace_id()
        row = FactorySignalLink(
            factory_id=fid,
            signal=signal,
            opportunity=opportunity,
            attached_by=attached_by[:128],
            attached_kind=attached_kind,
            attached_at=now_s(),
            counter=bool(counter),
            confidence=confidence,
            via=via[:32] if via else None,
            workspace_id=ws,
        )
        with self._lock:
            self._tables().links[(ws, fid, signal, opportunity)] = row
        return _copy(row)

    def detach_link(
        self,
        factory_id: str,
        signal: int,
        opportunity: int,
        *,
        by: str,
        reason: str | None = None,
    ) -> FactorySignalLink | None:
        key = (current_workspace_id(), norm_id(factory_id), signal, opportunity)
        tables = self._tables()
        with self._lock:
            row = tables.links.get(key)
            if row is None or row.detached_at is not None:
                return None
            row = replace(
                row,
                detached_at=now_s(),
                detached_by=by[:128],
                detach_reason=reason[:200] if reason else None,
            )
            tables.links[key] = row
            return _copy(row)

    def list_links(
        self,
        factory_id: str,
        *,
        signals: Sequence[int] | None = None,
        opportunities: Sequence[int] | None = None,
        active_only: bool = True,
    ) -> builtins.list[FactorySignalLink]:
        fid = norm_id(factory_id)
        ws = current_workspace_id()
        sig = set(signals) if signals is not None else None
        opp = set(opportunities) if opportunities is not None else None
        with self._lock:
            rows = [
                _copy(r)
                for k, r in self._tables().links.items()
                if k[0] == ws
                and k[1] == fid
                and (sig is None or r.signal in sig)
                and (opp is None or r.opportunity in opp)
                and (not active_only or r.detached_at is None)
            ]
        rows.sort(key=lambda r: (r.opportunity, r.signal))
        return rows

    # ── Opportunities ───────────────────────────────────────────

    def create_opportunity(
        self,
        factory_id: str,
        *,
        title: str,
        statement: str,
        created_by: str,
        created_kind: str,
        **fields: Any,
    ) -> FactoryOpportunity:
        check_fields(fields, OPPORTUNITY_FIELDS - {"title", "statement"}, "create_opportunity")
        check_enums({"created_kind": created_kind})
        fid = norm_id(factory_id)
        ws = current_workspace_id()
        values = _json_defaults(
            clip({"title": title, "statement": statement, "created_by": created_by, **fields})
        )
        tables = self._tables()
        with self._lock:
            number = (
                max(
                    (k[2] for k in tables.opportunities if k[0] == ws and k[1] == fid),
                    default=0,
                )
                + 1
            )
            now = now_s()
            row = FactoryOpportunity(
                factory_id=fid,
                number=number,
                created_kind=created_kind,
                created_at=now,
                updated_at=now,
                workspace_id=ws,
                **values,
            )
            tables.opportunities[(ws, fid, number)] = row
            return _copy(row)

    def get_opportunity(self, factory_id: str, number: int) -> FactoryOpportunity | None:
        with self._lock:
            row = self._tables().opportunities.get(
                (current_workspace_id(), norm_id(factory_id), number)
            )
            return _copy(row) if row is not None else None

    def list_opportunities(
        self, factory_id: str, *, limit: int = 1000
    ) -> builtins.list[FactoryOpportunity]:
        fid = norm_id(factory_id)
        ws = current_workspace_id()
        with self._lock:
            rows = [
                _copy(r)
                for k, r in self._tables().opportunities.items()
                if k[0] == ws and k[1] == fid
            ]
        rows.sort(key=lambda r: r.number)
        return rows[:limit]

    def update_opportunity(
        self, factory_id: str, number: int, **fields: Any
    ) -> FactoryOpportunity | None:
        check_fields(fields, OPPORTUNITY_FIELDS, "update_opportunity")
        key = (current_workspace_id(), norm_id(factory_id), number)
        tables = self._tables()
        with self._lock:
            row = tables.opportunities.get(key)
            if row is None:
                return None
            row = replace(row, **_json_defaults(clip(fields)), updated_at=now_s())
            tables.opportunities[key] = row
            return _copy(row)

    # ── Score snapshots ─────────────────────────────────────────

    def add_score_snapshots(self, factory_id: str, rows: Sequence[Mapping[str, Any]]) -> int:
        fid = norm_id(factory_id)
        ws = current_workspace_id()
        tables = self._tables()
        with self._lock:
            for raw in rows:
                check_fields(raw, SNAPSHOT_FIELDS, "add_score_snapshots")
                values: dict[str, Any] = clip(raw)
                values["inputs"] = values.get("inputs") or {}
                values["parts"] = values.get("parts") or {}
                row = FactoryScoreSnapshot(
                    id=uuid.uuid4().hex, factory_id=fid, workspace_id=ws, **values
                )
                tables.snapshots[(ws, row.id)] = row
        return len(rows)

    def list_score_snapshots(
        self,
        factory_id: str,
        *,
        opportunity: int | None = None,
        since: int | None = None,
        reason: str | None = None,
        limit: int = 200,
    ) -> builtins.list[FactoryScoreSnapshot]:
        fid = norm_id(factory_id)
        ws = current_workspace_id()
        with self._lock:
            rows = [
                _copy(r)
                for k, r in self._tables().snapshots.items()
                if k[0] == ws
                and r.factory_id == fid
                and (opportunity is None or r.opportunity == opportunity)
                and (since is None or r.at >= since)
                and (reason is None or r.reason == reason)
            ]
        rows.sort(key=lambda r: (r.at, r.id), reverse=True)
        return rows[:limit]

    # ── Readouts ────────────────────────────────────────────────

    def add_readout(
        self, factory_id: str, *, proposal: int, day: int, **fields: Any
    ) -> tuple[FactoryReadout, bool]:
        check_fields(fields, READOUT_FIELDS, "add_readout")
        check_readout_enums(fields)
        fid = norm_id(factory_id)
        ws = current_workspace_id()
        values = _json_defaults(clip(fields))
        if values.get("decision_id") is not None:
            values["decision_id"] = norm_id(values["decision_id"])
        tables = self._tables()
        with self._lock:
            for row in tables.readouts.values():
                if (
                    row.workspace_id == ws
                    and row.factory_id == fid
                    and (row.proposal, row.day) == (proposal, day)
                ):
                    return _copy(row), True
            now = now_s()
            row = FactoryReadout(
                id=uuid.uuid4().hex,
                factory_id=fid,
                proposal=proposal,
                day=day,
                created_at=now,
                updated_at=now,
                workspace_id=ws,
                **values,
            )
            tables.readouts[(ws, row.id)] = row
            return _copy(row), False

    def get_readout(self, factory_id: str, readout_id: str) -> FactoryReadout | None:
        try:
            key = (current_workspace_id(), norm_id(readout_id))
        except ValueError:
            return None
        with self._lock:
            row = self._tables().readouts.get(key)
            if row is None or row.factory_id != norm_id(factory_id):
                return None
            return _copy(row)

    def list_readouts(
        self,
        factory_id: str,
        *,
        proposal: int | None = None,
        opportunity: int | None = None,
        statuses: Sequence[str] | None = None,
        due_before: int | None = None,
        limit: int = 500,
    ) -> builtins.list[FactoryReadout]:
        fid = norm_id(factory_id)
        ws = current_workspace_id()
        wanted = set(statuses) if statuses is not None else None
        with self._lock:
            rows = [
                _copy(r)
                for k, r in self._tables().readouts.items()
                if k[0] == ws
                and r.factory_id == fid
                and (proposal is None or r.proposal == proposal)
                and (opportunity is None or r.opportunity == opportunity)
                and (wanted is None or r.status in wanted)
                and (due_before is None or r.due_at <= due_before)
            ]
        rows.sort(key=lambda r: (r.proposal, r.day))
        return rows[:limit]

    def update_readout(
        self,
        factory_id: str,
        readout_id: str,
        *,
        expect_status: str | Sequence[str] | None = None,
        **fields: Any,
    ) -> FactoryReadout | None:
        check_fields(fields, READOUT_FIELDS, "update_readout")
        check_readout_enums(fields)
        expected = {expect_status} if isinstance(expect_status, str) else set(expect_status or ())
        values = _json_defaults(clip(fields))
        if values.get("decision_id") is not None:
            values["decision_id"] = norm_id(values["decision_id"])
        try:
            key = (current_workspace_id(), norm_id(readout_id))
        except ValueError:
            return None
        tables = self._tables()
        with self._lock:
            row = tables.readouts.get(key)
            if row is None or row.factory_id != norm_id(factory_id):
                return None
            if expected and row.status not in expected:
                return None
            row = replace(row, **values, updated_at=now_s())
            tables.readouts[key] = row
            return _copy(row)

    def retract_feedback(self, factory_id: str, ids: Sequence[str]) -> int:
        fid = norm_id(factory_id)
        ws = current_workspace_id()
        wanted = set(ids)
        feedback: dict[tuple[int, str], Any] = getattr(self, "_feedback", {})
        with self._lock:
            gone = [
                key
                for key, row in feedback.items()
                if key[0] == ws and key[1] in wanted and row.factory_id == fid
            ]
            for key in gone:
                del feedback[key]
        return len(gone)

    # ── Source state ────────────────────────────────────────────

    def get_source_state(self, factory_id: str, source: str) -> FactorySourceState | None:
        with self._lock:
            row = self._tables().sources.get((current_workspace_id(), norm_id(factory_id), source))
            return _copy(row) if row is not None else None

    def put_source_state(self, factory_id: str, source: str, **fields: Any) -> FactorySourceState:
        check_fields(fields, SOURCE_STATE_FIELDS, "put_source_state")
        fid = norm_id(factory_id)
        ws = current_workspace_id()
        key = (ws, fid, source)
        tables = self._tables()
        with self._lock:
            row = tables.sources.get(key) or FactorySourceState(
                factory_id=fid, source=source, workspace_id=ws
            )
            row = replace(row, **_json_defaults(clip(fields)), updated_at=now_s())
            tables.sources[key] = row
            return _copy(row)

    def list_source_states(self, factory_id: str) -> builtins.list[FactorySourceState]:
        fid = norm_id(factory_id)
        ws = current_workspace_id()
        with self._lock:
            rows = [
                _copy(r) for k, r in self._tables().sources.items() if k[0] == ws and k[1] == fid
            ]
        rows.sort(key=lambda r: r.source)
        return rows

    def charge_source_call(
        self, factory_id: str, source: str, *, day: str, limit: int, calls: int = 1
    ) -> tuple[FactorySourceState, bool]:
        key = (current_workspace_id(), norm_id(factory_id), source)
        tables = self._tables()
        with self._lock:
            row = tables.sources.get(key) or FactorySourceState(
                factory_id=key[1], source=source, workspace_id=key[0]
            )
            used = row.calls_today if row.usage_day == day else 0
            if used + calls > limit:
                return _copy(row), False
            row = replace(row, usage_day=day, calls_today=used + calls, updated_at=now_s())
            tables.sources[key] = row
            return _copy(row), True


__all__ = [
    "EMPTY_JSON",
    "HUNCH_KINDS",
    "LABEL_FIELDS",
    "OPPORTUNITY_FIELDS",
    "OPPORTUNITY_JSON",
    "READOUT_ENUMS",
    "READOUT_FIELDS",
    "READOUT_JSON",
    "SIGNAL_FIELDS",
    "SIGNAL_JSON",
    "SNAPSHOT_FIELDS",
    "SOURCE_STATE_FIELDS",
    "UNSORTED_SKIP",
    "DiscoveryStore",
    "InMemoryDiscoveryStore",
    "SignalCounts",
    "SignalFilter",
    "check_enums",
    "check_fields",
    "check_readout_enums",
    "clip",
    "is_unsorted",
    "pointer_key",
]
