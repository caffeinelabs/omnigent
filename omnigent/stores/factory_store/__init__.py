"""Factory store — the software-factory cockpit's durable read model.

Owns the five ``factory_*`` tables: projects, tracked PRs, decisions for the
human, the typed event log and recorded demos. Every method is synchronous
(async callers use :func:`asyncio.to_thread`) and scoped to the active
workspace (:func:`~omnigent.db.db_models.current_workspace_id`).

:class:`InMemoryFactoryStore` is the fallback when no database store is wired
(tests, embedded apps); ``sqlalchemy_store.SqlAlchemyFactoryStore`` is the
production store.
"""

from __future__ import annotations

import builtins
import copy
import json
import os
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from typing import Any, TypeVar

from omnigent.db.db_models import current_workspace_id, uuid_to_bytes
from omnigent.db.enum_codecs import (
    FACTORY_DECISION_SOURCE,
    FACTORY_DECISION_STATUS,
    FACTORY_DEMO_STATUS,
    FACTORY_EVENT_SEVERITY,
    FACTORY_PR_STATE,
    FACTORY_PROJECT_STAGE,
)
from omnigent.entities.factory import (
    EventDraft,
    FactoryDecision,
    FactoryDemo,
    FactoryEvent,
    FactoryPr,
    FactoryProject,
)

PROJECT_FIELDS = frozenset(
    {
        "title",
        "repo",
        "base_branch",
        "charter",
        "priority",
        "stage",
        "stage_changed_at",
        "agent_stage",
        "agent_stage_at",
        "pl_session_id",
        "env",
        "created_by",
    }
)
"""Fields ``upsert_project`` / ``update_project`` accept."""

PR_FIELDS = frozenset(
    {
        "project_slug",
        "url",
        "title",
        "head_ref",
        "base_ref",
        "head_sha",
        "state",
        "is_draft",
        "task",
        "additions",
        "deletions",
        "changed_files",
        "gates",
        "ready",
        "ready_at",
        "sentinel",
        "human_review",
        "preview",
        "merged_sha",
        "merged_by",
        "merged_at",
        "closed_at",
        "source",
        "last_polled_at",
        "poll_error",
    }
)
"""Fields ``upsert_pr`` / ``update_pr`` accept."""

DECISION_FIELDS = frozenset(
    {
        "question",
        "source",
        "options",
        "blocking",
        "allow_text",
        "project_slug",
        "asked_by_session_id",
        "context",
    }
)
"""Fields ``create_decision`` accepts besides ``dedupe_key``."""

DECISION_UPDATE_FIELDS = frozenset({"delivered_mail_id", "delivered_at", "context", "options"})
"""Fields ``update_decision`` accepts (status changes go through the CAS methods)."""

PR_SOURCES = frozenset({"tool", "discovered"})

DEFAULT_PR_POLL_INTERVAL_S = 60
TERMINAL_PR_WINDOW_S = 600
"""A merged/closed PR stays due for this long so the poller sees it settle."""


class FactoryStoreError(Exception):
    """Base class for factory store errors."""


class DecisionNotFound(FactoryStoreError, LookupError):
    """No decision with the given id exists in this workspace."""


class DecisionNotOpen(FactoryStoreError):
    """
    A compare-and-set on an open decision lost: it is no longer open.

    :param decision: The decision as it is now (answered, withdrawn, ...).
    """

    def __init__(self, decision: FactoryDecision) -> None:
        super().__init__(f"decision {decision.ref} is {decision.status}")
        self.decision = decision


# ── Shared helpers ─────────────────────────────────────────────


def now_s() -> int:
    """:returns: Epoch seconds."""
    return int(time.time())


def now_ms() -> int:
    """:returns: Epoch milliseconds."""
    return time.time_ns() // 1_000_000


def new_id() -> str:
    """:returns: A fresh row id (bare 32-char hex uuid4)."""
    return uuid.uuid4().hex


_uuid7_lock = threading.Lock()
_uuid7_last = [0, 0]  # [ms, counter]


def new_event_id(ts_ms: int | None = None) -> str:
    """
    Return a uuid7 (RFC 9562) hex id: time-ordered, monotonic within this process.

    :param ts_ms: Epoch milliseconds to embed, defaults to now.
    :returns: A bare 32-char hex uuid.
    """
    ts = now_ms() if ts_ms is None else ts_ms
    with _uuid7_lock:
        if ts == _uuid7_last[0]:
            _uuid7_last[1] = (_uuid7_last[1] + 1) & 0xFFF
        else:
            _uuid7_last[0] = ts
            _uuid7_last[1] = int.from_bytes(os.urandom(2)) & 0x7FF
        counter = _uuid7_last[1]
    rand_b = int.from_bytes(os.urandom(8)) & ((1 << 62) - 1)
    value = ((ts & ((1 << 48) - 1)) << 80) | (0x7 << 76) | (counter << 64) | (0b10 << 62) | rand_b
    return f"{value:032x}"


def norm_id(value: str) -> str:
    """
    Normalise a session/row id to bare hex, as a ``Uuid16`` column would.

    :raises InvalidUuidError: When *value* is not a uuid.
    """
    return uuid_to_bytes(value).hex()


def opt_id(value: str | None) -> str | None:
    """:func:`norm_id` that passes ``None`` through."""
    return None if value is None else norm_id(value)


def check_fields(fields: Mapping[str, Any], allowed: frozenset[str], where: str) -> None:
    """:raises TypeError: When *fields* holds a key outside *allowed*."""
    unknown = sorted(set(fields) - allowed)
    if unknown:
        raise TypeError(f"{where}() got unexpected field(s): {', '.join(unknown)}")


def check_name(table: dict[str, int], name: str, field: str) -> str:
    """:raises ValueError: When *name* is not a known enum value."""
    if name not in table:
        raise ValueError(f"unknown {field} value: {name!r}")
    return name


def check_priority(value: int) -> int:
    """:raises ValueError: When *value* is outside 0-3."""
    if not isinstance(value, int) or value not in (0, 1, 2, 3):
        raise ValueError(f"priority must be 0-3, got {value!r}")
    return value


def validate_project_fields(fields: Mapping[str, Any]) -> None:
    """Value checks the table's CHECK constraints would make."""
    if "priority" in fields:
        check_priority(fields["priority"])
    if "stage" in fields:
        check_name(FACTORY_PROJECT_STAGE, fields["stage"], "factory_projects.stage")


def validate_pr_fields(fields: Mapping[str, Any]) -> None:
    """Value checks the table's CHECK constraints would make."""
    if "state" in fields:
        check_name(FACTORY_PR_STATE, fields["state"], "factory_prs.state")
    if "source" in fields and fields["source"] not in PR_SOURCES:
        raise ValueError(f"unknown factory_prs.source value: {fields['source']!r}")


def kind_matches(kind: str, patterns: Iterable[str]) -> bool:
    """
    Match an event kind against exact kinds or ``prefix.*`` patterns.

    :param kind: e.g. ``"heartbeat.skipped"``.
    :param patterns: e.g. ``["heartbeat.*", "pr.ready"]``.
    """
    for pattern in patterns:
        if pattern.endswith("*"):
            if kind.startswith(pattern[:-1]):
                return True
        elif kind == pattern:
            return True
    return False


def as_list(value: str | Sequence[str] | None) -> list[str] | None:
    """A single string or a sequence of strings, as a list (``None`` passes)."""
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    return list(value)


def dump_json(value: Any) -> str | None:
    """JSON for a CompressedText column (``None`` stays NULL)."""
    return None if value is None else json.dumps(value, separators=(",", ":"))


def load_json(value: str | None) -> Any:
    """Inverse of :func:`dump_json`."""
    return None if value is None else json.loads(value)


def default_pr_url(repo: str, number: int) -> str:
    """:returns: The GitHub web URL of a PR."""
    return f"https://github.com/{repo}/pull/{number}"


# ── The store contract ─────────────────────────────────────────


class FactoryStore(ABC):
    """
    Abstract base for the factory cockpit's persistence.

    Ids accept bare, dashed or legacy-prefixed uuids and come back as bare hex.
    Enum-like values are their string names (stage ``"building"``, PR state
    ``"open"``, decision status ``"open"``, severity ``"info"``, demo status
    ``"PASS"``); JSON columns take and return plain Python values.
    """

    # ── Projects ────────────────────────────────────────────────

    @abstractmethod
    def upsert_project(self, factory_id: str, slug: str, **fields: Any) -> FactoryProject:
        """
        Insert the project ``(factory_id, slug)`` or update the given fields.

        A new row needs ``title`` (defaults to *slug*); ``stage`` defaults to
        ``queued``. Setting ``stage`` to a new value stamps ``stage_changed_at``
        unless it is passed too.

        :param factory_id: Root lead session id.
        :param slug: Project slug, unique within the factory.
        :param fields: Any of :data:`PROJECT_FIELDS`.
        :returns: The stored row.
        :raises TypeError: On an unknown field.
        :raises ValueError: On an unknown stage or a priority outside 0-3.
        """

    @abstractmethod
    def get_project(self, factory_id: str, slug: str) -> FactoryProject | None:
        """:returns: The project ``(factory_id, slug)``, or ``None``."""

    @abstractmethod
    def get_project_by_id(self, project_id: str) -> FactoryProject | None:
        """:returns: The project with this row id, or ``None``."""

    @abstractmethod
    def list_projects(self, factory_id: str) -> builtins.list[FactoryProject]:
        """:returns: The factory's projects, oldest first."""

    @abstractmethod
    def update_project(self, project_id: str, **fields: Any) -> FactoryProject | None:
        """
        Update fields of one project (same rules as :meth:`upsert_project`).

        :returns: The updated row, or ``None`` when the id is unknown.
        """

    # ── PRs ─────────────────────────────────────────────────────

    @abstractmethod
    def upsert_pr(
        self, factory_id: str, repo: str, number: int, **fields: Any
    ) -> tuple[FactoryPr, bool]:
        """
        Insert the PR ``(factory_id, repo, number)`` or update the given fields.

        A new row defaults ``url`` to the GitHub URL, ``source`` to ``tool``,
        ``state`` to ``open`` and ``gates`` to ``{}``.

        :param fields: Any of :data:`PR_FIELDS`.
        :returns: ``(row, created)``.
        :raises TypeError: On an unknown field.
        :raises ValueError: On an unknown state or source.
        """

    @abstractmethod
    def get_pr(self, pr_id: str) -> FactoryPr | None:
        """:returns: The PR with this row id, or ``None``."""

    @abstractmethod
    def get_pr_by_number(self, factory_id: str, repo: str, number: int) -> FactoryPr | None:
        """:returns: The PR ``(factory_id, repo, number)``, or ``None``."""

    @abstractmethod
    def list_prs(
        self, factory_id: str, states: Sequence[str] | None = None
    ) -> builtins.list[FactoryPr]:
        """
        :param states: Only these states (e.g. ``["open"]``); ``None`` for all.
        :returns: The factory's PRs, oldest first.
        """

    @abstractmethod
    def list_prs_due(
        self,
        now: int,
        limit: int,
        *,
        min_interval_s: int = DEFAULT_PR_POLL_INTERVAL_S,
        terminal_window_s: int = TERMINAL_PR_WINDOW_S,
    ) -> builtins.list[FactoryPr]:
        """
        PRs the GitHub poller should refresh now, across every factory.

        Due = never polled (``last_polled_at`` NULL, which is how "refresh
        now" is requested) or polled at least *min_interval_s* ago, and either
        open or merged/closed within *terminal_window_s*. Least recently polled
        first.

        :param now: Epoch seconds.
        :param limit: Maximum rows.
        """

    @abstractmethod
    def update_pr(self, pr_id: str, **fields: Any) -> FactoryPr | None:
        """
        Update fields of one PR (any of :data:`PR_FIELDS`).

        :returns: The updated row, or ``None`` when the id is unknown.
        """

    # ── Decisions ───────────────────────────────────────────────

    @abstractmethod
    def create_decision(
        self, factory_id: str, *, dedupe_key: str, **fields: Any
    ) -> tuple[FactoryDecision, bool]:
        """
        Open a decision numbered ``max(number)+1`` within the factory.

        While a decision with the same *dedupe_key* is open, that row is
        returned instead. Concurrent creates that collide on the number retry
        up to 3 times.

        :param dedupe_key: ≤ 64 chars.
        :param fields: ``question`` (required) and any of :data:`DECISION_FIELDS`.
        :returns: ``(row, deduped)``.
        """

    @abstractmethod
    def get_decision(self, decision_id: str) -> FactoryDecision | None:
        """:returns: The decision with this row id, or ``None``."""

    @abstractmethod
    def get_decision_by_number(self, factory_id: str, number: int) -> FactoryDecision | None:
        """:returns: Decision ``D-<number>`` of the factory, or ``None``."""

    @abstractmethod
    def answer_decision(
        self, decision_id: str, *, option_id: str | None, text: str | None, by: str
    ) -> FactoryDecision:
        """
        Answer an open decision (compare-and-set on ``status=open``).

        :returns: The answered row.
        :raises DecisionNotOpen: When it is no longer open (carries the row).
        :raises DecisionNotFound: When the id is unknown.
        """

    @abstractmethod
    def set_decision_status(
        self, decision_id: str, status: str, *, only_if_open: bool = True
    ) -> FactoryDecision:
        """
        Move a decision to *status* (e.g. ``withdrawn`` or ``expired``).

        :param only_if_open: Compare-and-set on ``status=open``.
        :raises DecisionNotOpen: When *only_if_open* and it is not open.
        :raises DecisionNotFound: When the id is unknown.
        """

    @abstractmethod
    def update_decision(self, decision_id: str, **fields: Any) -> FactoryDecision | None:
        """
        Update delivery fields (any of :data:`DECISION_UPDATE_FIELDS`).

        :returns: The updated row, or ``None`` when the id is unknown.
        """

    @abstractmethod
    def list_decisions(
        self,
        factory_ids: Sequence[str] | None = None,
        status: str | Sequence[str] | None = None,
        factory_id: str | None = None,
        limit: int = 100,
        *,
        newest_first: bool = True,
    ) -> builtins.list[FactoryDecision]:
        """
        :param factory_ids: Only these factories (``None`` = no filter).
        :param status: One status name or several.
        :param factory_id: Only this factory (combined with *factory_ids*).
        :param limit: Maximum rows.
        :returns: Decisions ordered by ``created_at``.
        """

    @abstractmethod
    def count_open(self, factory_ids: Sequence[str]) -> dict[str, int]:
        """:returns: Open decisions per requested factory id (0 included)."""

    # ── Events ──────────────────────────────────────────────────

    @abstractmethod
    def append_event(self, draft: EventDraft) -> FactoryEvent:
        """
        Append one event; the id is a uuid7 and ``ts`` defaults to now (ms).

        :raises ValueError: On an unknown severity.
        """

    @abstractmethod
    def list_events(
        self,
        factory_id: str,
        before_ms: int | None = None,
        limit: int = 100,
        kinds: Sequence[str] | None = None,
        project: str | None = None,
        member_ids: Sequence[str] | None = None,
        *,
        before_id: str | None = None,
    ) -> builtins.list[FactoryEvent]:
        """
        A page of the factory's events, newest first.

        :param before_ms: Only events with ``ts < before_ms`` (or, with
            *before_id*, the same ms and a smaller id).
        :param kinds: Exact kinds or ``prefix.*`` patterns.
        :param project: Only this project slug.
        :param member_ids: Only events from or to these sessions.
        :param before_id: Tie-breaker cursor for events in the same ms.
        """

    @abstractmethod
    def find_events(
        self,
        factory_id: str | None,
        kind: str | Sequence[str],
        since_ms: int,
        subject: str | None = None,
        *,
        limit: int = 1000,
    ) -> builtins.list[FactoryEvent]:
        """
        Events of the given kind(s) with ``ts >= since_ms``, oldest first.

        :param factory_id: One factory, or ``None`` for all of them.
        :param kind: Exact kinds or ``prefix.*`` patterns.
        :param subject: Only this subject.
        """

    @abstractmethod
    def prune_events(self, older_than_ms: int, kinds: Sequence[str] | None = None) -> int:
        """
        Delete events with ``ts < older_than_ms`` (only *kinds* when given).

        :returns: Rows deleted.
        """

    # ── Demos ───────────────────────────────────────────────────

    @abstractmethod
    def create_demo(
        self,
        *,
        factory_id: str,
        project_slug: str,
        repo: str,
        pr_number: int,
        head_sha: str,
        session_id: str,
        created_by_session: str,
        status: str = "PASS",
        title: str = "",
        summary: str = "",
        files: Sequence[Mapping[str, Any]] | None = None,
        manifest: Mapping[str, Any] | None = None,
        pr_id: str | None = None,
        preview_url: str | None = None,
        demo_id: str | None = None,
    ) -> FactoryDemo:
        """
        Record a demo.

        :raises ValueError: On an unknown status.
        """

    @abstractmethod
    def get_demo(self, demo_id: str) -> FactoryDemo | None:
        """:returns: The demo with this id, or ``None``."""

    @abstractmethod
    def list_demos(
        self,
        factory_id: str,
        pr_number: int | None = None,
        *,
        repo: str | None = None,
        limit: int = 100,
    ) -> builtins.list[FactoryDemo]:
        """:returns: The factory's demos (optionally of one PR), newest first."""


# ── In-memory implementation ───────────────────────────────────


def _stage_stamp(current: FactoryProject | None, fields: dict[str, Any], now: int) -> None:
    stage = fields.get("stage")
    if stage is None or "stage_changed_at" in fields:
        return
    if current is None or current.stage != stage:
        fields["stage_changed_at"] = now


_R = TypeVar("_R")


def _detach(row: _R) -> _R:
    """A deep copy, so a caller never aliases a stored row's dicts or lists."""
    return copy.deepcopy(row)


def _normalise_pr_fields(fields: dict[str, Any]) -> dict[str, Any]:
    out = dict(fields)
    for key in ("is_draft", "ready"):
        if key in out:
            out[key] = bool(out[key])
    if "gates" in out and out["gates"] is None:
        out["gates"] = {}
    return out


class InMemoryFactoryStore(FactoryStore):
    """
    A process-local :class:`FactoryStore` with the same semantics as the SQL store.

    Used when no database-backed store is wired. Thread-safe; rows are kept per
    workspace and lost on restart. Rows are deep-copied in and out, so callers
    never share a stored row's dicts or lists (as with the SQL store).
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._projects: dict[tuple[int, str], FactoryProject] = {}
        self._prs: dict[tuple[int, str], FactoryPr] = {}
        self._decisions: dict[tuple[int, str], FactoryDecision] = {}
        self._events: dict[tuple[int, str], FactoryEvent] = {}
        self._demos: dict[tuple[int, str], FactoryDemo] = {}

    @staticmethod
    def _ws() -> int:
        return current_workspace_id()

    def _rows(self, table: dict[tuple[int, str], Any]) -> builtins.list[Any]:
        ws = self._ws()
        return [row for (row_ws, _), row in table.items() if row_ws == ws]

    # ── Projects ────────────────────────────────────────────────

    def upsert_project(self, factory_id: str, slug: str, **fields: Any) -> FactoryProject:
        check_fields(fields, PROJECT_FIELDS, "upsert_project")
        validate_project_fields(fields)
        fid = norm_id(factory_id)
        now = now_s()
        with self._lock:
            current = self.get_project(fid, slug)
            values = copy.deepcopy(dict(fields))
            if "pl_session_id" in values:
                values["pl_session_id"] = opt_id(values["pl_session_id"])
            _stage_stamp(current, values, now)
            if current is not None:
                row = replace(current, **values, updated_at=now)
            else:
                values.setdefault("title", slug)
                values.setdefault("stage_changed_at", now)
                values.setdefault("created_by", "server")
                row = FactoryProject(
                    id=new_id(),
                    factory_id=fid,
                    slug=slug,
                    created_at=now,
                    updated_at=now,
                    workspace_id=self._ws(),
                    **values,
                )
            self._projects[(self._ws(), row.id)] = row
            return _detach(row)

    def get_project(self, factory_id: str, slug: str) -> FactoryProject | None:
        fid = norm_id(factory_id)
        with self._lock:
            for row in self._rows(self._projects):
                if row.factory_id == fid and row.slug == slug:
                    return _detach(row)
        return None

    def get_project_by_id(self, project_id: str) -> FactoryProject | None:
        with self._lock:
            return _detach(self._projects.get((self._ws(), norm_id(project_id))))

    def list_projects(self, factory_id: str) -> builtins.list[FactoryProject]:
        fid = norm_id(factory_id)
        with self._lock:
            rows = [_detach(r) for r in self._rows(self._projects) if r.factory_id == fid]
        return sorted(rows, key=lambda r: (r.created_at, r.id))

    def update_project(self, project_id: str, **fields: Any) -> FactoryProject | None:
        check_fields(fields, PROJECT_FIELDS, "update_project")
        validate_project_fields(fields)
        now = now_s()
        with self._lock:
            current = self.get_project_by_id(project_id)
            if current is None:
                return None
            values = copy.deepcopy(dict(fields))
            if "pl_session_id" in values:
                values["pl_session_id"] = opt_id(values["pl_session_id"])
            _stage_stamp(current, values, now)
            row = replace(current, **values, updated_at=now)
            self._projects[(self._ws(), row.id)] = row
            return _detach(row)

    # ── PRs ─────────────────────────────────────────────────────

    def upsert_pr(
        self, factory_id: str, repo: str, number: int, **fields: Any
    ) -> tuple[FactoryPr, bool]:
        check_fields(fields, PR_FIELDS, "upsert_pr")
        validate_pr_fields(fields)
        fid = norm_id(factory_id)
        now = now_s()
        values = copy.deepcopy(_normalise_pr_fields(fields))
        with self._lock:
            current = self.get_pr_by_number(fid, repo, number)
            if current is not None:
                row = replace(current, **values, updated_at=now)
                created = False
            else:
                values.setdefault("url", default_pr_url(repo, number))
                row = FactoryPr(
                    id=new_id(),
                    factory_id=fid,
                    repo=repo,
                    number=number,
                    created_at=now,
                    updated_at=now,
                    workspace_id=self._ws(),
                    **values,
                )
                created = True
            self._prs[(self._ws(), row.id)] = row
            return _detach(row), created

    def get_pr(self, pr_id: str) -> FactoryPr | None:
        with self._lock:
            return _detach(self._prs.get((self._ws(), norm_id(pr_id))))

    def get_pr_by_number(self, factory_id: str, repo: str, number: int) -> FactoryPr | None:
        fid = norm_id(factory_id)
        with self._lock:
            for row in self._rows(self._prs):
                if row.factory_id == fid and row.repo == repo and row.number == number:
                    return _detach(row)
        return None

    def list_prs(
        self, factory_id: str, states: Sequence[str] | None = None
    ) -> builtins.list[FactoryPr]:
        fid = norm_id(factory_id)
        wanted = set(states) if states is not None else None
        with self._lock:
            rows = [
                _detach(r)
                for r in self._rows(self._prs)
                if r.factory_id == fid and (wanted is None or r.state in wanted)
            ]
        return sorted(rows, key=lambda r: (r.created_at, r.id))

    def list_prs_due(
        self,
        now: int,
        limit: int,
        *,
        min_interval_s: int = DEFAULT_PR_POLL_INTERVAL_S,
        terminal_window_s: int = TERMINAL_PR_WINDOW_S,
    ) -> builtins.list[FactoryPr]:
        with self._lock:
            rows = [_detach(r) for r in self._rows(self._prs)]
        due = []
        for row in rows:
            if row.last_polled_at is not None and row.last_polled_at > now - min_interval_s:
                continue
            ended = row.merged_at or row.closed_at or 0
            if row.state != "open" and ended < now - terminal_window_s:
                continue
            due.append(row)
        due.sort(key=lambda r: (r.last_polled_at or 0, r.id))
        return due[:limit]

    def update_pr(self, pr_id: str, **fields: Any) -> FactoryPr | None:
        check_fields(fields, PR_FIELDS, "update_pr")
        validate_pr_fields(fields)
        values = copy.deepcopy(_normalise_pr_fields(fields))
        with self._lock:
            current = self.get_pr(pr_id)
            if current is None:
                return None
            row = replace(current, **values, updated_at=now_s())
            self._prs[(self._ws(), row.id)] = row
            return _detach(row)

    # ── Decisions ───────────────────────────────────────────────

    def create_decision(
        self, factory_id: str, *, dedupe_key: str, **fields: Any
    ) -> tuple[FactoryDecision, bool]:
        check_fields(fields, DECISION_FIELDS, "create_decision")
        if "question" not in fields:
            raise TypeError("create_decision() missing required field: question")
        source = fields.get("source", "agent")
        check_name(FACTORY_DECISION_SOURCE, source, "factory_decisions.source")
        fid = norm_id(factory_id)
        now = now_s()
        with self._lock:
            rows = [r for r in self._rows(self._decisions) if r.factory_id == fid]
            for row in rows:
                if row.dedupe_key == dedupe_key and row.status == "open":
                    return _detach(row), True
            number = max((r.number for r in rows), default=0) + 1
            values = copy.deepcopy(dict(fields))
            values["blocking"] = bool(values.get("blocking", True))
            values["allow_text"] = bool(values.get("allow_text", True))
            values["options"] = list(values.get("options") or [])
            values["asked_by_session_id"] = opt_id(values.get("asked_by_session_id"))
            row = FactoryDecision(
                id=new_id(),
                factory_id=fid,
                number=number,
                dedupe_key=dedupe_key,
                created_at=now,
                updated_at=now,
                workspace_id=self._ws(),
                **values,
            )
            self._decisions[(self._ws(), row.id)] = row
            return _detach(row), False

    def get_decision(self, decision_id: str) -> FactoryDecision | None:
        with self._lock:
            return _detach(self._decisions.get((self._ws(), norm_id(decision_id))))

    def get_decision_by_number(self, factory_id: str, number: int) -> FactoryDecision | None:
        fid = norm_id(factory_id)
        with self._lock:
            for row in self._rows(self._decisions):
                if row.factory_id == fid and row.number == number:
                    return _detach(row)
        return None

    def _transition(
        self, decision_id: str, *, only_if_open: bool, **values: Any
    ) -> FactoryDecision:
        with self._lock:
            current = self.get_decision(decision_id)
            if current is None:
                raise DecisionNotFound(decision_id)
            if only_if_open and current.status != "open":
                raise DecisionNotOpen(current)
            row = replace(current, **values, updated_at=now_s())
            self._decisions[(self._ws(), row.id)] = row
            return _detach(row)

    def answer_decision(
        self, decision_id: str, *, option_id: str | None, text: str | None, by: str
    ) -> FactoryDecision:
        return self._transition(
            decision_id,
            only_if_open=True,
            status="answered",
            answer_option_id=option_id,
            answer_text=text,
            answered_by=by,
            answered_at=now_s(),
        )

    def set_decision_status(
        self, decision_id: str, status: str, *, only_if_open: bool = True
    ) -> FactoryDecision:
        check_name(FACTORY_DECISION_STATUS, status, "factory_decisions.status")
        return self._transition(decision_id, only_if_open=only_if_open, status=status)

    def update_decision(self, decision_id: str, **fields: Any) -> FactoryDecision | None:
        check_fields(fields, DECISION_UPDATE_FIELDS, "update_decision")
        with self._lock:
            current = self.get_decision(decision_id)
            if current is None:
                return None
            row = replace(current, **copy.deepcopy(fields), updated_at=now_s())
            self._decisions[(self._ws(), row.id)] = row
            return _detach(row)

    def list_decisions(
        self,
        factory_ids: Sequence[str] | None = None,
        status: str | Sequence[str] | None = None,
        factory_id: str | None = None,
        limit: int = 100,
        *,
        newest_first: bool = True,
    ) -> builtins.list[FactoryDecision]:
        ids = {norm_id(f) for f in factory_ids} if factory_ids is not None else None
        one = opt_id(factory_id)
        statuses = as_list(status)
        with self._lock:
            rows = [
                _detach(r)
                for r in self._rows(self._decisions)
                if (ids is None or r.factory_id in ids)
                and (one is None or r.factory_id == one)
                and (statuses is None or r.status in statuses)
            ]
        rows.sort(key=lambda r: (r.created_at, r.number, r.id), reverse=newest_first)
        return rows[:limit]

    def count_open(self, factory_ids: Sequence[str]) -> dict[str, int]:
        counts = {norm_id(f): 0 for f in factory_ids}
        with self._lock:
            for row in self._rows(self._decisions):
                if row.status == "open" and row.factory_id in counts:
                    counts[row.factory_id] += 1
        return counts

    # ── Events ──────────────────────────────────────────────────

    def append_event(self, draft: EventDraft) -> FactoryEvent:
        check_name(FACTORY_EVENT_SEVERITY, draft.severity, "factory_events.severity")
        ts = now_ms() if draft.ts is None else draft.ts
        row = FactoryEvent(
            id=new_event_id(ts),
            factory_id=norm_id(draft.factory_id),
            ts=ts,
            kind=draft.kind,
            summary=draft.summary,
            severity=draft.severity,
            project_slug=draft.project_slug,
            from_id=opt_id(draft.from_id),
            to_id=opt_id(draft.to_id),
            subject=draft.subject,
            data=copy.deepcopy(draft.data),
            workspace_id=self._ws(),
        )
        with self._lock:
            self._events[(self._ws(), row.id)] = row
        return _detach(row)

    def list_events(
        self,
        factory_id: str,
        before_ms: int | None = None,
        limit: int = 100,
        kinds: Sequence[str] | None = None,
        project: str | None = None,
        member_ids: Sequence[str] | None = None,
        *,
        before_id: str | None = None,
    ) -> builtins.list[FactoryEvent]:
        fid = norm_id(factory_id)
        members = {norm_id(m) for m in member_ids} if member_ids is not None else None
        cursor_id = opt_id(before_id)
        with self._lock:
            rows = list(self._rows(self._events))
        out = []
        for row in rows:
            if row.factory_id != fid:
                continue
            if before_ms is not None:
                if cursor_id is None and row.ts >= before_ms:
                    continue
                if cursor_id is not None and (row.ts, row.id) >= (before_ms, cursor_id):
                    continue
            if kinds is not None and not kind_matches(row.kind, kinds):
                continue
            if project is not None and row.project_slug != project:
                continue
            if members is not None and row.from_id not in members and row.to_id not in members:
                continue
            out.append(row)
        out.sort(key=lambda r: (r.ts, r.id), reverse=True)
        return [_detach(r) for r in out[:limit]]

    def find_events(
        self,
        factory_id: str | None,
        kind: str | Sequence[str],
        since_ms: int,
        subject: str | None = None,
        *,
        limit: int = 1000,
    ) -> builtins.list[FactoryEvent]:
        fid = opt_id(factory_id)
        patterns = as_list(kind) or []
        with self._lock:
            rows = [
                r
                for r in self._rows(self._events)
                if (fid is None or r.factory_id == fid)
                and r.ts >= since_ms
                and kind_matches(r.kind, patterns)
                and (subject is None or r.subject == subject)
            ]
        rows.sort(key=lambda r: (r.ts, r.id))
        return [_detach(r) for r in rows[:limit]]

    def prune_events(self, older_than_ms: int, kinds: Sequence[str] | None = None) -> int:
        ws = self._ws()
        with self._lock:
            doomed = [
                key
                for key, row in self._events.items()
                if key[0] == ws
                and row.ts < older_than_ms
                and (kinds is None or kind_matches(row.kind, kinds))
            ]
            for key in doomed:
                del self._events[key]
        return len(doomed)

    # ── Demos ───────────────────────────────────────────────────

    def create_demo(
        self,
        *,
        factory_id: str,
        project_slug: str,
        repo: str,
        pr_number: int,
        head_sha: str,
        session_id: str,
        created_by_session: str,
        status: str = "PASS",
        title: str = "",
        summary: str = "",
        files: Sequence[Mapping[str, Any]] | None = None,
        manifest: Mapping[str, Any] | None = None,
        pr_id: str | None = None,
        preview_url: str | None = None,
        demo_id: str | None = None,
    ) -> FactoryDemo:
        check_name(FACTORY_DEMO_STATUS, status, "factory_demos.status")
        row = FactoryDemo(
            id=norm_id(demo_id) if demo_id else new_id(),
            factory_id=norm_id(factory_id),
            project_slug=project_slug,
            repo=repo,
            pr_number=pr_number,
            head_sha=head_sha,
            session_id=norm_id(session_id),
            created_by_session=norm_id(created_by_session),
            status=status,
            title=title,
            summary=summary,
            files=copy.deepcopy([dict(f) for f in files or []]),
            manifest=copy.deepcopy(dict(manifest)) if manifest is not None else None,
            pr_id=opt_id(pr_id),
            preview_url=preview_url,
            created_at=now_s(),
            workspace_id=self._ws(),
        )
        with self._lock:
            self._demos[(self._ws(), row.id)] = row
        return _detach(row)

    def get_demo(self, demo_id: str) -> FactoryDemo | None:
        with self._lock:
            return _detach(self._demos.get((self._ws(), norm_id(demo_id))))

    def list_demos(
        self,
        factory_id: str,
        pr_number: int | None = None,
        *,
        repo: str | None = None,
        limit: int = 100,
    ) -> builtins.list[FactoryDemo]:
        fid = norm_id(factory_id)
        with self._lock:
            rows = [
                r
                for r in self._rows(self._demos)
                if r.factory_id == fid
                and (pr_number is None or r.pr_number == pr_number)
                and (repo is None or r.repo == repo)
            ]
        rows.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        return [_detach(r) for r in rows[:limit]]


__all__ = [
    "DecisionNotFound",
    "DecisionNotOpen",
    "FactoryStore",
    "FactoryStoreError",
    "InMemoryFactoryStore",
]
