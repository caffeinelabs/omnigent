"""Factory store — the software factory's durable state.

Owns the ``factory_*`` tables: projects, tracked PRs, decisions for the human,
the typed event log and recorded demos (the cockpit), plus the round-3 tables:
the people roster, the Room, proposals, the work graph, loops, issues, spans,
the cost ledger, platform versions, factory definitions and feedback. Every
method is synchronous
(async callers use :func:`asyncio.to_thread`) and scoped to the active
workspace (:func:`~omnigent.db.db_models.current_workspace_id`).

:class:`InMemoryFactoryStore` is the fallback when no database store is wired
(tests, embedded apps); ``sqlalchemy_store.SqlAlchemyFactoryStore`` is the
production store.
"""

from __future__ import annotations

import builtins
import copy
import hashlib
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
    FACTORY_ACTOR_KIND,
    FACTORY_CHAT_AUTHOR_KIND,
    FACTORY_DECISION_SOURCE,
    FACTORY_DECISION_STATUS,
    FACTORY_DEFINITION_VIA,
    FACTORY_DEMO_STATUS,
    FACTORY_EVENT_SEVERITY,
    FACTORY_FEEDBACK_KIND,
    FACTORY_FEEDBACK_TARGET,
    FACTORY_ISSUE_CLASS,
    FACTORY_ISSUE_SOURCE,
    FACTORY_ISSUE_STATUS,
    FACTORY_LOOP_KIND,
    FACTORY_LOOP_STATE,
    FACTORY_PERSON_ROLE,
    FACTORY_PR_STATE,
    FACTORY_PROJECT_STAGE,
    FACTORY_PROJECT_STATUS,
    FACTORY_PROPOSAL_APPROVAL,
    FACTORY_PROPOSAL_CLASS,
    FACTORY_PROPOSAL_KIND,
    FACTORY_PROPOSAL_STATUS,
    FACTORY_SPAN_KIND,
    FACTORY_SPAN_OUTCOME,
    FACTORY_TASK_KIND,
    FACTORY_TASK_STATE,
)
from omnigent.entities.factory import (
    ChatThreadStats,
    EventDraft,
    FactoryChatMessage,
    FactoryChatRead,
    FactoryCostDaily,
    FactoryDecision,
    FactoryDefinition,
    FactoryDemo,
    FactoryEvent,
    FactoryFeedback,
    FactoryIssue,
    FactoryLoop,
    FactoryPerson,
    FactoryPr,
    FactoryProject,
    FactoryProposal,
    FactorySpan,
    FactoryTask,
    FactoryVersion,
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
        "status",
        "parent_slug",
        "closed_at",
        "def_version",
        "version_id",
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
        "opened_at",
        "first_ready_at",
        "task_id",
        "version_id",
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
        "assignee",
        "thread",
        "subject",
    }
)
"""Fields ``create_decision`` accepts besides ``dedupe_key``."""

DECISION_UPDATE_FIELDS = frozenset(
    {
        "delivered_mail_id",
        "delivered_at",
        "context",
        "options",
        "assignee",
        "claimed_by",
        "claimed_at",
        "thread",
        "subject",
    }
)
"""Fields ``update_decision`` accepts (status changes go through the CAS methods)."""

PERSON_UPDATE_FIELDS = frozenset({"role", "last_seen_at", "notify"})
"""Fields ``update_person`` accepts."""

CHAT_UPDATE_FIELDS = frozenset({"routed", "card"})
"""Fields ``update_chat_message`` accepts (messages are otherwise append-only)."""

PROPOSAL_FIELDS = frozenset(
    {
        "status",
        "approval",
        "class_",
        "title",
        "ops",
        "before",
        "after",
        "rationale",
        "analysis",
        "evidence",
        "expected_effect",
        "warnings",
        "author_role",
        "on_behalf_of",
        "cosigned_by",
        "thread",
        "reply_to_chat",
        "decision_id",
        "based_on",
        "trial_until",
        "undo_until",
        "result",
        "def_version_from",
        "def_version_to",
        "decided_by",
        "decided_at",
        "applied_at",
        "reverted_at",
        "reverted_by",
    }
)
"""Fields ``create_proposal`` / ``update_proposal`` accept."""

TASK_FIELDS = frozenset(
    {
        "title",
        "kind",
        "state",
        "card",
        "deps",
        "scope",
        "priority",
        "estimate_min",
        "vendor",
        "session_ids",
        "pr_id",
        "attempts",
        "budget",
        "cost_usd",
        "created_by",
        "created_via",
        "def_version",
        "started_at",
        "closed_at",
    }
)
"""Fields ``upsert_task`` / ``update_task`` accept."""

LOOP_FIELDS = frozenset(
    {
        "project_slug",
        "task_id",
        "pr_id",
        "state",
        "iteration",
        "spec",
        "spent",
        "last_verdict",
        "last_reason",
        "decision_id",
        "def_version",
        "started_at",
        "ended_at",
    }
)
"""Fields ``upsert_loop`` / ``update_loop`` accept."""

ISSUE_FIELDS = frozenset(
    {
        "rule",
        "target",
        "summary",
        "category",
        "severity",
        "source",
        "project_slug",
        "body",
        "evidence",
        "status",
        "assignee",
        "snoozed_until",
        "acked_by",
        "resolved_by",
        "resolved_at",
        "proposal_id",
        "version_id",
        "def_version",
    }
)
"""Fields ``upsert_issue`` / ``update_issue`` accept."""

ISSUE_REOPENABLE = frozenset({"resolved"})
"""Statuses a re-raised dedupe key reopens (``wontfix`` stays closed)."""

SPAN_FIELDS = frozenset(
    {
        "name",
        "parent_id",
        "project_slug",
        "pr_id",
        "task_id",
        "loop_id",
        "head_sha",
        "session_id",
        "role",
        "agent_name",
        "actor",
        "ended_at",
        "outcome",
        "attempt",
        "cost_usd",
        "tokens_in",
        "tokens_out",
        "version_id",
        "def_version",
        "attrs",
    }
)
"""Fields ``insert_span`` / ``update_span`` accept besides the identity columns."""

_ID_FIELDS = frozenset(
    {
        "pl_session_id",
        "version_id",
        "task_id",
        "pr_id",
        "loop_id",
        "parent_id",
        "session_id",
        "decision_id",
        "reply_to_chat",
        "reply_to",
        "proposal_id",
    }
)
"""Uuid16 columns: values are normalised to bare hex on the way in."""

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


class ProposalStateConflict(FactoryStoreError):
    """
    A compare-and-set on a proposal's status lost.

    :param proposal: The proposal as it is now.
    """

    def __init__(self, proposal: FactoryProposal) -> None:
        super().__init__(f"proposal {proposal.ref} is {proposal.status}")
        self.proposal = proposal


class DefinitionVersionExists(FactoryStoreError):
    """
    ``put_definition`` was given a version the factory already has.

    :param definition: The stored version.
    """

    def __init__(self, definition: FactoryDefinition) -> None:
        super().__init__(f"definition v{definition.version} already exists")
        self.definition = definition


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
    if "status" in fields:
        check_name(FACTORY_PROJECT_STATUS, fields["status"], "factory_projects.status")


def validate_pr_fields(fields: Mapping[str, Any]) -> None:
    """Value checks the table's CHECK constraints would make."""
    if "state" in fields:
        check_name(FACTORY_PR_STATE, fields["state"], "factory_prs.state")
    if "source" in fields and fields["source"] not in PR_SOURCES:
        raise ValueError(f"unknown factory_prs.source value: {fields['source']!r}")


_ENUM_FIELDS: dict[str, dict[str, tuple[dict[str, int], str]]] = {
    "project": {
        "status": (FACTORY_PROJECT_STATUS, "factory_projects.status"),
    },
    "person": {
        "role": (FACTORY_PERSON_ROLE, "factory_people.role"),
    },
    "chat": {
        "author_kind": (FACTORY_CHAT_AUTHOR_KIND, "factory_chat_messages.author_kind"),
    },
    "proposal": {
        "kind": (FACTORY_PROPOSAL_KIND, "factory_proposals.kind"),
        "status": (FACTORY_PROPOSAL_STATUS, "factory_proposals.status"),
        "approval": (FACTORY_PROPOSAL_APPROVAL, "factory_proposals.approval"),
        "class_": (FACTORY_PROPOSAL_CLASS, "factory_proposals.class"),
        "author_kind": (FACTORY_ACTOR_KIND, "factory_proposals.author_kind"),
    },
    "task": {
        "kind": (FACTORY_TASK_KIND, "factory_tasks.kind"),
        "state": (FACTORY_TASK_STATE, "factory_tasks.state"),
        "created_via": (FACTORY_ACTOR_KIND, "factory_tasks.created_via"),
    },
    "loop": {
        "kind": (FACTORY_LOOP_KIND, "factory_loops.kind"),
        "state": (FACTORY_LOOP_STATE, "factory_loops.state"),
    },
    "issue": {
        "class_": (FACTORY_ISSUE_CLASS, "factory_issues.class"),
        "severity": (FACTORY_EVENT_SEVERITY, "factory_issues.severity"),
        "source": (FACTORY_ISSUE_SOURCE, "factory_issues.source"),
        "status": (FACTORY_ISSUE_STATUS, "factory_issues.status"),
    },
    "span": {
        "kind": (FACTORY_SPAN_KIND, "factory_spans.kind"),
        "outcome": (FACTORY_SPAN_OUTCOME, "factory_spans.outcome"),
    },
    "definition": {
        "created_via": (FACTORY_DEFINITION_VIA, "factory_definitions.created_via"),
    },
    "feedback": {
        "target_kind": (FACTORY_FEEDBACK_TARGET, "factory_feedback.target_kind"),
        "author_kind": (FACTORY_ACTOR_KIND, "factory_feedback.author_kind"),
        "kind": (FACTORY_FEEDBACK_KIND, "factory_feedback.kind"),
    },
}


_NULLABLE_ENUMS = frozenset({("proposal", "class_")})


def validate_enums(entity: str, fields: Mapping[str, Any]) -> None:
    """
    Check every enum-valued field of *entity* that *fields* carries.

    Nullable enums (a proposal's ``class_``) accept ``None``.

    :param entity: A key of the enum table, e.g. ``"task"``.
    :raises ValueError: On an unknown enum name, or a priority outside 0-3.
    """
    for name, (table, label) in _ENUM_FIELDS[entity].items():
        if name not in fields or (fields[name] is None and (entity, name) in _NULLABLE_ENUMS):
            continue
        check_name(table, fields[name], label)
    if "priority" in fields:
        check_priority(fields["priority"])


# VARCHAR bounds of the free-text columns. Longer values are cut to fit, since
# PostgreSQL raises DataError where SQLite would store them whole.
TEXT_BOUNDS: dict[str, dict[str, int]] = {
    "proposal": {"title": 256},
    "task": {"title": 256},
    "loop": {"last_verdict": 32, "last_reason": 300},
    "issue": {"rule": 48, "category": 48, "target": 128, "summary": 300},
    "span": {"name": 64, "agent_name": 64},
    "cost": {"agent_name": 64, "model": 64},
    "version": {"image_tag": 64, "note": 256},
}


def clip_value(entity: str, name: str, value: str) -> str:
    """:returns: *value* cut to the column bound of ``entity.name``."""
    return value[: TEXT_BOUNDS[entity][name]]


def clip_text(entity: str, fields: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of *fields* with each free-text value of *entity* cut to its column bound."""
    out = dict(fields)
    for name in TEXT_BOUNDS.get(entity, {}).keys() & out.keys():
        if isinstance(out[name], str):
            out[name] = clip_value(entity, name, out[name])
    return out


def normalise_ids(fields: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of *fields* with every Uuid16 column value as bare hex (``None`` passes)."""
    out = dict(fields)
    for key in _ID_FIELDS & out.keys():
        out[key] = opt_id(out[key])
    if "session_ids" in out and out["session_ids"] is not None:
        out["session_ids"] = [norm_id(s) for s in out["session_ids"]]
    return out


def canonical_hash(value: Any) -> str:
    """:returns: sha256 hex of *value*'s canonical JSON (sorted keys, no spaces)."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def version_content_hash(
    *,
    bundles: Mapping[str, Any],
    knobs: Mapping[str, Any],
    image_tag: str | None,
    server_sha: str | None,
) -> str:
    """:returns: The ``factory_versions.content_hash`` of a platform version."""
    return canonical_hash(
        {
            "image_tag": image_tag,
            "server_sha": server_sha,
            "bundles": dict(bundles),
            "knobs": dict(knobs),
        }
    )


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

    @abstractmethod
    def delete_project(self, project_id: str) -> bool:
        """
        Drop a project row (an undone proposal's project whose PL never spawned).

        :returns: Whether a row was deleted.
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
    def route_decision(
        self,
        decision_id: str,
        *,
        claimable_by: str | None = None,
        assignee_was: str | None = None,
        **fields: Any,
    ) -> FactoryDecision:
        """
        Update an open decision's routing fields (compare-and-set on ``status=open``).

        :param claimable_by: Also require it unclaimed or claimed by this user.
        :param assignee_was: Also require this assignee (``None`` = any).
        :param fields: Any of :data:`DECISION_UPDATE_FIELDS`, e.g. ``assignee``.
        :returns: The updated row.
        :raises DecisionNotOpen: When a condition fails (carries the current
            row: still ``open`` means the claim or assignee changed).
        :raises DecisionNotFound: When the id is unknown.
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
        project_slug: str | None = None,
        limit: int = 100,
    ) -> builtins.list[FactoryDemo]:
        """:returns: The factory's demos (optionally of one PR or project), newest first."""

    # ── People (R3-06) ──────────────────────────────────────────

    @abstractmethod
    def upsert_person(
        self,
        factory_id: str,
        user_id: str,
        *,
        role: str,
        invited_by: str,
        is_creator: bool = False,
        invited_at: int | None = None,
        notify: Mapping[str, Any] | None = None,
    ) -> tuple[FactoryPerson, bool]:
        """
        Put a human on the roster, or change the role of one already on it.

        An existing row keeps ``invited_by``, ``invited_at`` and ``is_creator``;
        ``notify`` is replaced only when given. Consumer: R3-06 ``people.set_person``.

        :param role: ``viewer`` / ``operator`` / ``owner``.
        :param invited_at: Epoch seconds, defaults to now.
        :returns: ``(row, created)``.
        :raises ValueError: On an unknown role.
        """

    @abstractmethod
    def get_person(self, factory_id: str, user_id: str) -> FactoryPerson | None:
        """:returns: The roster row, or ``None``. Consumer: R3-06 access checks."""

    @abstractmethod
    def list_people(self, factory_id: str) -> builtins.list[FactoryPerson]:
        """:returns: The factory's roster, oldest invite first. Consumer: R3-06 People API."""

    @abstractmethod
    def list_person_factories(self, user_id: str) -> builtins.list[FactoryPerson]:
        """
        :returns: The user's roster rows across factories, oldest invite first.
            Consumer: R3-06 factory index ("Shared with you") and notifications.
        """

    @abstractmethod
    def update_person(self, factory_id: str, user_id: str, **fields: Any) -> FactoryPerson | None:
        """
        Update any of :data:`PERSON_UPDATE_FIELDS`. Consumer: R3-06 role changes, presence.

        :returns: The updated row, or ``None`` when the person is not on the roster.
        """

    @abstractmethod
    def delete_person(self, factory_id: str, user_id: str) -> bool:
        """:returns: Whether a row was removed. Consumer: R3-06 ``people.remove_person``."""

    # ── Room (R3-08) ────────────────────────────────────────────

    @abstractmethod
    def append_chat(
        self,
        factory_id: str,
        *,
        thread: str,
        author_kind: str,
        author: str,
        body: str,
        author_role: str | None = None,
        mentions: Sequence[str] | None = None,
        refs: Sequence[str] | None = None,
        reply_to: str | None = None,
        card: Mapping[str, Any] | None = None,
        routed: Sequence[Mapping[str, Any]] | None = None,
        auto_reply: bool = False,
        created_at: int | None = None,
        message_id: str | None = None,
    ) -> FactoryChatMessage:
        """
        Append a message numbered ``max(seq)+1`` within the factory.

        Concurrent appends that collide on ``seq`` retry. Consumer: R3-08 ``chat.py``
        (human posts, ``sys_factory_chat``, cards, auto-replies).

        :param author_kind: ``human`` / ``agent`` / ``system``.
        :param message_id: The id to store the message under. When a message with that
            id exists, it is returned unchanged (a replayed append adds nothing).
        :raises ValueError: On an unknown author kind.
        """

    @abstractmethod
    def next_chat_seq(self, factory_id: str) -> int:
        """:returns: The ``seq`` the next message would get. Consumer: R3-08 envelopes."""

    @abstractmethod
    def get_chat_message(self, message_id: str) -> FactoryChatMessage | None:
        """:returns: The message, or ``None``. Consumer: R3-08 ``reply_to`` resolution."""

    @abstractmethod
    def list_chat(
        self,
        factory_id: str,
        thread: str | None = None,
        *,
        before_seq: int | None = None,
        after_seq: int | None = None,
        limit: int = 50,
    ) -> builtins.list[FactoryChatMessage]:
        """
        A page of messages in ascending ``seq``. Consumer: R3-08 ``GET …/chat``.

        With *after_seq*, the first *limit* messages after it; otherwise the
        latest *limit* messages (below *before_seq* when given).

        :param thread: One thread, or ``None`` for all of them.
        """

    @abstractmethod
    def update_chat_message(self, message_id: str, **fields: Any) -> FactoryChatMessage | None:
        """
        Update delivery state (any of :data:`CHAT_UPDATE_FIELDS`).

        Consumer: R3-08 delivery and the reply fallback (``routed``).

        :returns: The updated row, or ``None`` when the id is unknown.
        """

    @abstractmethod
    def chat_thread_stats(self, factory_id: str) -> builtins.list[ChatThreadStats]:
        """
        :returns: Per-thread last ``seq``, last time and count, newest thread first.
            Consumer: R3-08 ``GET …/chat/threads``.
        """

    @abstractmethod
    def upsert_chat_read(
        self, factory_id: str, user_id: str, thread: str, last_seq: int
    ) -> FactoryChatRead:
        """
        Move a user's read mark forward (never backwards). Consumer: R3-08 ``PUT …/chat/reads``.

        :returns: The stored mark.
        """

    @abstractmethod
    def list_chat_reads(self, factory_id: str, user_id: str) -> builtins.list[FactoryChatRead]:
        """:returns: The user's read marks in the factory. Consumer: R3-08 unread badges."""

    # ── Proposals (R3-07, R3-12) ────────────────────────────────

    @abstractmethod
    def create_proposal(
        self,
        factory_id: str,
        *,
        kind: str,
        title: str,
        author_kind: str,
        author: str,
        dedupe_key: str | None = None,
        **fields: Any,
    ) -> tuple[FactoryProposal, bool]:
        """
        File a proposal numbered ``max(number)+1`` within the factory (``C-n``).

        A proposal with the same *dedupe_key* is returned instead, whatever its
        status. Consumers: R3-07 ``proposals.py`` (projects), R3-12 ``tuning.py``.

        :param fields: Any of :data:`PROPOSAL_FIELDS`; ``approval`` defaults to
            ``operator`` and ``status`` to ``pending``.
        :returns: ``(row, deduped)``.
        :raises ValueError: On an unknown kind, status, approval, class or author kind.
        """

    @abstractmethod
    def get_proposal(self, proposal_id: str) -> FactoryProposal | None:
        """:returns: The proposal with this row id, or ``None``. Consumers: R3-07, R3-12."""

    @abstractmethod
    def get_proposal_by_number(self, factory_id: str, number: int) -> FactoryProposal | None:
        """:returns: Proposal ``C-<number>``, or ``None``. Consumer: R3-07 ``/proposals/{n}``."""

    @abstractmethod
    def list_proposals(
        self,
        factory_id: str,
        *,
        status: str | Sequence[str] | None = None,
        kind: str | Sequence[str] | None = None,
        limit: int = 100,
        newest_first: bool = True,
    ) -> builtins.list[FactoryProposal]:
        """
        :returns: Proposals ordered by number. Consumers: R3-07/R3-12
            ``GET …/proposals``, the snapshot's ``proposals_pending`` and ``on_trial``.
        """

    @abstractmethod
    def update_proposal(
        self,
        proposal_id: str,
        *,
        expect_status: str | Sequence[str] | None = None,
        **fields: Any,
    ) -> FactoryProposal | None:
        """
        Update any of :data:`PROPOSAL_FIELDS`, optionally as a compare-and-set.

        Consumers: R3-07 apply/undo/expire, R3-12 cosign/trial/keep/revert.

        :param expect_status: Only update while the status is one of these.
        :returns: The updated row, or ``None`` when the id is unknown.
        :raises ProposalStateConflict: When *expect_status* does not match (carries the row).
        """

    @abstractmethod
    def next_proposal_number(self, factory_id: str) -> int:
        """:returns: The number the next proposal would get. Consumer: R3-07 previews."""

    # ── Work graph (R3-09) ──────────────────────────────────────

    @abstractmethod
    def upsert_task(
        self, factory_id: str, project_slug: str, key: str, **fields: Any
    ) -> tuple[FactoryTask, bool]:
        """
        Insert task ``(project_slug, key)`` numbered ``max(number)+1`` (``T-n``), or update it.

        A new row defaults ``title`` to *key*, ``kind`` to ``implement``,
        ``state`` to ``planned``, ``created_by`` and ``created_via`` to ``server``.
        Consumers: R3-09 ``sys_factory_task(upsert)``, human task routes, the binder.

        :param fields: Any of :data:`TASK_FIELDS`.
        :returns: ``(row, created)``.
        :raises ValueError: On an unknown kind, state or creator kind, or a bad priority.
        """

    @abstractmethod
    def get_task(self, task_id: str) -> FactoryTask | None:
        """:returns: The task with this row id, or ``None``. Consumer: R3-09 task routes."""

    @abstractmethod
    def get_task_by_key(self, factory_id: str, project_slug: str, key: str) -> FactoryTask | None:
        """:returns: The task, or ``None``. Consumer: R3-09 binder (titles and branches)."""

    @abstractmethod
    def get_task_by_number(self, factory_id: str, number: int) -> FactoryTask | None:
        """:returns: Task ``T-<number>``, or ``None``. Consumer: R3-09 ref resolution."""

    @abstractmethod
    def list_tasks(
        self,
        factory_id: str,
        *,
        project_slug: str | None = None,
        states: Sequence[str] | None = None,
    ) -> builtins.list[FactoryTask]:
        """:returns: Tasks ordered by number. Consumer: R3-09 graph computations and APIs."""

    @abstractmethod
    def update_task(self, task_id: str, **fields: Any) -> FactoryTask | None:
        """
        Update any of :data:`TASK_FIELDS`. Consumers: R3-09 state moves, R3-10 budgets.

        :returns: The updated row, or ``None`` when the id is unknown.
        """

    @abstractmethod
    def increment_task_attempts(self, task_id: str, by: int = 1) -> FactoryTask | None:
        """
        Atomically add *by* to ``attempts``. Consumer: R3-09 ``start``, R3-10 binder.

        :returns: The updated row, or ``None`` when the id is unknown.
        """

    @abstractmethod
    def next_task_number(self, factory_id: str) -> int:
        """:returns: The number the next task would get. Consumer: R3-09 upsert previews."""

    # ── Loops (R3-10) ───────────────────────────────────────────

    @abstractmethod
    def upsert_loop(
        self, factory_id: str, kind: str, subject: str, **fields: Any
    ) -> tuple[FactoryLoop, bool]:
        """
        Insert the loop ``(kind, subject)`` or update it. Consumer: R3-10 ``loops.py``.

        A new row defaults ``state`` to ``running`` and ``started_at`` to now.

        :param fields: Any of :data:`LOOP_FIELDS`.
        :returns: ``(row, created)``.
        :raises ValueError: On an unknown kind or state.
        """

    @abstractmethod
    def get_loop(self, loop_id: str) -> FactoryLoop | None:
        """:returns: The loop with this row id, or ``None``. Consumer: R3-10 loop decisions."""

    @abstractmethod
    def get_loop_by_subject(self, factory_id: str, kind: str, subject: str) -> FactoryLoop | None:
        """:returns: The loop ``(kind, subject)``, or ``None``. Consumer: R3-10 ``loops.py``."""

    @abstractmethod
    def list_loops(
        self,
        factory_id: str,
        *,
        states: Sequence[str] | None = None,
        kinds: Sequence[str] | None = None,
        project_slug: str | None = None,
        task_id: str | None = None,
        pr_id: str | None = None,
    ) -> builtins.list[FactoryLoop]:
        """:returns: Loops, oldest first. Consumer: R3-10 ``GET …/loops``, NEXT hints."""

    @abstractmethod
    def update_loop(self, loop_id: str, **fields: Any) -> FactoryLoop | None:
        """
        Update any of :data:`LOOP_FIELDS`. Consumer: R3-10 state machine.

        :returns: The updated row, or ``None`` when the id is unknown.
        """

    # ── Issues (R3-11) ──────────────────────────────────────────

    @abstractmethod
    def upsert_issue(
        self,
        factory_id: str,
        *,
        class_: str,
        rule: str,
        target: str,
        summary: str,
        dedupe_key: str | None = None,
        reopen: bool = True,
        **fields: Any,
    ) -> tuple[FactoryIssue, bool]:
        """
        Raise an issue numbered per class (``INC-n``, ``F-n``), or re-raise one.

        With a *dedupe_key* already stored, that row's ``count`` goes up by one,
        ``last_seen`` moves to now, the given fields are written, and a
        ``resolved`` row reopens when *reopen* (``wontfix`` stays closed).
        Consumers: R3-11 ``issues.py`` and ``detectors.py``, R3-12 retros.

        :param fields: Any of :data:`ISSUE_FIELDS`; ``source`` defaults to
            ``detector`` and ``severity`` to ``notice``.
        :returns: ``(row, created)``.
        :raises ValueError: On an unknown class, severity, source or status.
        """

    @abstractmethod
    def get_issue(self, issue_id: str) -> FactoryIssue | None:
        """:returns: The issue with this row id, or ``None``. Consumer: R3-11 issue routes."""

    @abstractmethod
    def get_issue_by_number(
        self, factory_id: str, class_: str, number: int
    ) -> FactoryIssue | None:
        """:returns: e.g. ``INC-13`` as ``("incident", 13)``, or ``None``. Consumer: R3-11."""

    @abstractmethod
    def get_issue_by_dedupe(self, factory_id: str, dedupe_key: str) -> FactoryIssue | None:
        """:returns: The issue stored under *dedupe_key* (any status), or ``None``."""

    @abstractmethod
    def list_issues(
        self,
        factory_id: str,
        *,
        class_: str | Sequence[str] | None = None,
        statuses: Sequence[str] | None = None,
        project_slug: str | None = None,
        limit: int = 200,
    ) -> builtins.list[FactoryIssue]:
        """:returns: Issues by ``first_seen`` then number, newest first. Consumer: R3-11."""

    @abstractmethod
    def update_issue(self, issue_id: str, **fields: Any) -> FactoryIssue | None:
        """
        Update any of :data:`ISSUE_FIELDS`. Consumer: R3-11 ack/assign/snooze/resolve.

        :returns: The updated row, or ``None`` when the id is unknown.
        """

    @abstractmethod
    def next_issue_number(self, factory_id: str, class_: str) -> int:
        """:returns: The number the next issue of *class_* would get. Consumer: R3-11."""

    # ── Spans (R3-05) ───────────────────────────────────────────

    @abstractmethod
    def insert_span(
        self,
        factory_id: str,
        *,
        kind: str,
        name: str,
        trace_key: str,
        started_at: int | None = None,
        dedupe_key: str | None = None,
        span_id: str | None = None,
        **fields: Any,
    ) -> tuple[FactorySpan, bool]:
        """
        Insert a span, or return the one already stored under *dedupe_key*.

        Consumers: R3-05 ``telemetry/spans.py`` and ``heal.py``, R3-09 to R3-12 writers.

        :param started_at: Epoch milliseconds, defaults to now.
        :param span_id: Explicit uuid; defaults to a fresh uuid7.
        :param fields: Any of :data:`SPAN_FIELDS`; ``outcome`` defaults to ``open``.
        :returns: ``(row, created)``.
        :raises ValueError: On an unknown kind or outcome.
        """

    @abstractmethod
    def get_span(self, span_id: str) -> FactorySpan | None:
        """:returns: The span with this id, or ``None``. Consumer: R3-05 span writer."""

    @abstractmethod
    def get_span_by_dedupe(self, factory_id: str, dedupe_key: str) -> FactorySpan | None:
        """:returns: The span stored under *dedupe_key*, or ``None``. Consumer: R3-05 heal."""

    @abstractmethod
    def update_span(self, span_id: str, **fields: Any) -> FactorySpan | None:
        """
        Update any of :data:`SPAN_FIELDS` (``attrs`` is replaced, not merged).

        Consumer: R3-05 span close and cost attachment.

        :returns: The updated row, or ``None`` when the id is unknown.
        """

    @abstractmethod
    def list_open_spans(
        self,
        factory_id: str,
        kind: str | None = None,
        *,
        project_slug: str | None = None,
        pr_id: str | None = None,
        task_id: str | None = None,
        loop_id: str | None = None,
        session_id: str | None = None,
        trace_key: str | None = None,
        limit: int = 500,
    ) -> builtins.list[FactorySpan]:
        """
        Spans with no ``ended_at``, oldest first, optionally of one kind and subject.

        Consumer: R3-05 recorder (close the open ``stage``/``pr_round``/``wait`` span).
        """

    @abstractmethod
    def list_spans(
        self,
        factory_id: str,
        *,
        kinds: Sequence[str] | None = None,
        since_ms: int | None = None,
        until_ms: int | None = None,
        project_slug: str | None = None,
        pr_id: str | None = None,
        task_id: str | None = None,
        loop_id: str | None = None,
        trace_key: str | None = None,
        limit: int = 1000,
    ) -> builtins.list[FactorySpan]:
        """
        Spans with ``since_ms <= started_at < until_ms``, oldest first.

        Consumers: R3-05 ``GET …/spans`` and PR timelines, R3-12 metrics.
        """

    # ── Cost ledger (R3-05) ─────────────────────────────────────

    @abstractmethod
    def add_cost_daily(
        self,
        factory_id: str,
        *,
        project_slug: str,
        role: str,
        agent_name: str,
        model: str,
        day_utc: str,
        cost_usd: float = 0.0,
        tokens_in: int = 0,
        tokens_out: int = 0,
        turns: int = 1,
    ) -> FactoryCostDaily:
        """
        Add one turn's cost and tokens to its ledger cell (insert or increment).

        Consumer: R3-05 ``telemetry/cost.record`` (the ``_record_daily_cost`` hook).

        :param project_slug: ``""`` for the leads.
        :param day_utc: ``YYYY-MM-DD``.
        :returns: The cell after the increment.
        """

    @abstractmethod
    def list_cost_daily(
        self,
        factory_id: str,
        *,
        since_day: str | None = None,
        until_day: str | None = None,
        project_slug: str | None = None,
    ) -> builtins.list[FactoryCostDaily]:
        """
        Ledger cells with ``since_day <= day_utc <= until_day``, ordered by day.

        Consumers: R3-05 ``GET …/cost``, R3-10 budgets, R3-12 metrics.
        """

    # ── Platform versions (R3-05) ───────────────────────────────

    @abstractmethod
    def upsert_version(
        self,
        *,
        bundles: Mapping[str, Any],
        knobs: Mapping[str, Any],
        image_tag: str | None = None,
        server_sha: str | None = None,
        note: str | None = None,
    ) -> tuple[FactoryVersion, bool]:
        """
        Record a platform version, deduped by :func:`version_content_hash`.

        Consumer: R3-05 ``telemetry/versions.py`` (server start and bundle seed).

        :returns: ``(row, created)``.
        """

    @abstractmethod
    def get_version(self, version_id: str) -> FactoryVersion | None:
        """:returns: The version with this id, or ``None``. Consumer: R3-12 bundle drift."""

    @abstractmethod
    def list_versions(self, limit: int = 50) -> builtins.list[FactoryVersion]:
        """:returns: Platform versions, newest first. Consumer: R3-12 version comparison."""

    # ── Definitions (R3-12) ─────────────────────────────────────

    @abstractmethod
    def put_definition(
        self,
        factory_id: str,
        *,
        doc: Mapping[str, Any],
        created_by: str,
        created_via: str,
        version: int | None = None,
        parent_version: int | None = None,
        proposal_id: str | None = None,
    ) -> FactoryDefinition:
        """
        Store a new definition version (``max(version)+1`` unless *version* is given).

        Versions are immutable. Consumer: R3-12 ``definition.py`` (seed, human edits,
        applied proposals, reverts).

        :param created_via: ``seed`` / ``human`` / ``proposal`` / ``revert``.
        :raises DefinitionVersionExists: When *version* is already stored.
        :raises ValueError: On an unknown ``created_via``.
        """

    @abstractmethod
    def get_definition(self, factory_id: str, version: int) -> FactoryDefinition | None:
        """:returns: That version, or ``None``. Consumer: R3-12 resolver (pinned version)."""

    @abstractmethod
    def latest_definition(self, factory_id: str) -> FactoryDefinition | None:
        """:returns: The highest version, or ``None`` before the seed. Consumer: R3-12 seeding."""

    @abstractmethod
    def list_definitions(
        self, factory_id: str, limit: int = 50
    ) -> builtins.list[FactoryDefinition]:
        """:returns: Versions, newest first. Consumer: R3-12 ``…/definition/versions``."""

    # ── Feedback (R3-12) ────────────────────────────────────────

    @abstractmethod
    def add_feedback(
        self,
        factory_id: str,
        *,
        target_kind: str,
        target_id: str,
        author_kind: str,
        author: str,
        kind: str,
        project_slug: str | None = None,
        head_sha: str | None = None,
        rating: int | None = None,
        tags: Sequence[str] | None = None,
        body: str | None = None,
        def_version: int | None = None,
    ) -> FactoryFeedback:
        """
        Record one piece of feedback. Consumer: R3-12 ``POST /feedback``.

        :raises ValueError: On an unknown target kind, author kind or kind.
        """

    @abstractmethod
    def list_feedback(
        self,
        factory_id: str,
        *,
        target_kind: str | None = None,
        target_id: str | None = None,
        kinds: Sequence[str] | None = None,
        since: int | None = None,
        limit: int = 200,
    ) -> builtins.list[FactoryFeedback]:
        """
        Feedback with ``created_at >= since``, newest first.

        Consumers: R3-11 ``ask.unneeded`` detector, R3-12 metrics and retros.
        """


# ── In-memory implementation ───────────────────────────────────


def _stage_stamp(current: FactoryProject | None, fields: dict[str, Any], now: int) -> None:
    stage = fields.get("stage")
    if stage is None or "stage_changed_at" in fields:
        return
    if current is None or current.stage != stage:
        fields["stage_changed_at"] = now


def _proposal_defaults(values: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    """Map ``None`` list blobs of a proposal to ``[]`` (and fill a new row's rationale)."""
    out = dict(values)
    for key in ("ops", "evidence", "warnings"):
        if (key in out or not partial) and out.get(key) is None:
            out[key] = []
    if not partial and out.get("rationale") is None:
        out["rationale"] = ""
    return out


def _task_defaults(values: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    """Map ``None`` JSON blobs of a task to their empty value (NOT NULL columns)."""
    out = dict(values)
    for key, empty in (("card", {}), ("deps", []), ("scope", []), ("session_ids", [])):
        if (key in out or not partial) and out.get(key) is None:
            out[key] = copy.copy(empty)
    return out


def _loop_defaults(values: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    """Map ``None`` JSON blobs of a loop to their empty value (NOT NULL columns)."""
    out = dict(values)
    for key in ("spec", "spent"):
        if (key in out or not partial) and out.get(key) is None:
            out[key] = {}
    return out


def _cost_order(row: FactoryCostDaily) -> tuple[str, str, str, str, str]:
    return (row.day_utc, row.project_slug, row.role, row.agent_name, row.model)


_R = TypeVar("_R")


def _detach(row: _R) -> _R:
    """A deep copy, so a caller never aliases a stored row's dicts or lists."""
    return copy.deepcopy(row)


def _normalise_pr_fields(fields: dict[str, Any]) -> dict[str, Any]:
    out = normalise_ids(fields)
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
        self._people: dict[tuple[int, str, str], FactoryPerson] = {}
        self._chat: dict[tuple[int, str], FactoryChatMessage] = {}
        self._chat_reads: dict[tuple[int, str, str, str], FactoryChatRead] = {}
        self._proposals: dict[tuple[int, str], FactoryProposal] = {}
        self._tasks: dict[tuple[int, str], FactoryTask] = {}
        self._loops: dict[tuple[int, str], FactoryLoop] = {}
        self._issues: dict[tuple[int, str], FactoryIssue] = {}
        self._spans: dict[tuple[int, str], FactorySpan] = {}
        self._cost: dict[tuple[Any, ...], FactoryCostDaily] = {}
        self._versions: dict[tuple[int, str], FactoryVersion] = {}
        self._definitions: dict[tuple[int, str, int], FactoryDefinition] = {}
        self._feedback: dict[tuple[int, str], FactoryFeedback] = {}

    @staticmethod
    def _ws() -> int:
        return current_workspace_id()

    def _rows(self, table: Mapping[tuple[Any, ...], Any]) -> builtins.list[Any]:
        ws = self._ws()
        return [row for key, row in table.items() if key[0] == ws]

    # ── Projects ────────────────────────────────────────────────

    def upsert_project(self, factory_id: str, slug: str, **fields: Any) -> FactoryProject:
        check_fields(fields, PROJECT_FIELDS, "upsert_project")
        validate_project_fields(fields)
        fid = norm_id(factory_id)
        now = now_s()
        with self._lock:
            current = self.get_project(fid, slug)
            values = copy.deepcopy(normalise_ids(fields))
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
            values = copy.deepcopy(normalise_ids(fields))
            _stage_stamp(current, values, now)
            row = replace(current, **values, updated_at=now)
            self._projects[(self._ws(), row.id)] = row
            return _detach(row)

    def delete_project(self, project_id: str) -> bool:
        with self._lock:
            return self._projects.pop((self._ws(), norm_id(project_id)), None) is not None

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

    def route_decision(
        self,
        decision_id: str,
        *,
        claimable_by: str | None = None,
        assignee_was: str | None = None,
        **fields: Any,
    ) -> FactoryDecision:
        check_fields(fields, DECISION_UPDATE_FIELDS, "route_decision")
        with self._lock:
            current = self.get_decision(decision_id)
            if current is None:
                raise DecisionNotFound(decision_id)
            if (
                current.status != "open"
                or (claimable_by is not None and current.claimed_by not in (None, claimable_by))
                or (assignee_was is not None and current.assignee != assignee_was)
            ):
                raise DecisionNotOpen(current)
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
        project_slug: str | None = None,
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
                and (project_slug is None or r.project_slug == project_slug)
            ]
        rows.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        return [_detach(r) for r in rows[:limit]]

    # ── People ──────────────────────────────────────────────────

    def upsert_person(
        self,
        factory_id: str,
        user_id: str,
        *,
        role: str,
        invited_by: str,
        is_creator: bool = False,
        invited_at: int | None = None,
        notify: Mapping[str, Any] | None = None,
    ) -> tuple[FactoryPerson, bool]:
        validate_enums("person", {"role": role})
        key = (self._ws(), norm_id(factory_id), user_id)
        with self._lock:
            current = self._people.get(key)
            if current is not None:
                values: dict[str, Any] = {"role": role}
                if notify is not None:
                    values["notify"] = copy.deepcopy(dict(notify))
                row = replace(current, **values)
                created = False
            else:
                row = FactoryPerson(
                    factory_id=key[1],
                    user_id=user_id,
                    role=role,
                    is_creator=bool(is_creator),
                    invited_by=invited_by,
                    invited_at=now_s() if invited_at is None else invited_at,
                    notify=copy.deepcopy(dict(notify)) if notify is not None else None,
                    workspace_id=key[0],
                )
                created = True
            self._people[key] = row
            return _detach(row), created

    def get_person(self, factory_id: str, user_id: str) -> FactoryPerson | None:
        with self._lock:
            return _detach(self._people.get((self._ws(), norm_id(factory_id), user_id)))

    def list_people(self, factory_id: str) -> builtins.list[FactoryPerson]:
        fid = norm_id(factory_id)
        with self._lock:
            rows = [_detach(r) for r in self._rows(self._people) if r.factory_id == fid]
        return sorted(rows, key=lambda r: (r.invited_at, r.user_id))

    def list_person_factories(self, user_id: str) -> builtins.list[FactoryPerson]:
        with self._lock:
            rows = [_detach(r) for r in self._rows(self._people) if r.user_id == user_id]
        return sorted(rows, key=lambda r: (r.invited_at, r.factory_id))

    def update_person(self, factory_id: str, user_id: str, **fields: Any) -> FactoryPerson | None:
        check_fields(fields, PERSON_UPDATE_FIELDS, "update_person")
        validate_enums("person", fields)
        key = (self._ws(), norm_id(factory_id), user_id)
        with self._lock:
            current = self._people.get(key)
            if current is None:
                return None
            row = replace(current, **copy.deepcopy(fields))
            self._people[key] = row
            return _detach(row)

    def delete_person(self, factory_id: str, user_id: str) -> bool:
        with self._lock:
            return self._people.pop((self._ws(), norm_id(factory_id), user_id), None) is not None

    # ── Room ────────────────────────────────────────────────────

    def append_chat(
        self,
        factory_id: str,
        *,
        thread: str,
        author_kind: str,
        author: str,
        body: str,
        author_role: str | None = None,
        mentions: Sequence[str] | None = None,
        refs: Sequence[str] | None = None,
        reply_to: str | None = None,
        card: Mapping[str, Any] | None = None,
        routed: Sequence[Mapping[str, Any]] | None = None,
        auto_reply: bool = False,
        created_at: int | None = None,
        message_id: str | None = None,
    ) -> FactoryChatMessage:
        validate_enums("chat", {"author_kind": author_kind})
        fid = norm_id(factory_id)
        with self._lock:
            if message_id and (self._ws(), norm_id(message_id)) in self._chat:
                return _detach(self._chat[(self._ws(), norm_id(message_id))])
            row = FactoryChatMessage(
                id=norm_id(message_id) if message_id else new_id(),
                factory_id=fid,
                thread=thread,
                seq=self.next_chat_seq(fid),
                author_kind=author_kind,
                author=author,
                body=body,
                author_role=author_role,
                mentions=list(mentions or []),
                refs=list(refs or []),
                reply_to=opt_id(reply_to),
                card=copy.deepcopy(dict(card)) if card is not None else None,
                routed=copy.deepcopy([dict(r) for r in routed or []]),
                auto_reply=bool(auto_reply),
                created_at=now_s() if created_at is None else created_at,
                workspace_id=self._ws(),
            )
            self._chat[(self._ws(), row.id)] = row
            return _detach(row)

    def next_chat_seq(self, factory_id: str) -> int:
        fid = norm_id(factory_id)
        with self._lock:
            seqs = [r.seq for r in self._rows(self._chat) if r.factory_id == fid]
        return max(seqs, default=0) + 1

    def get_chat_message(self, message_id: str) -> FactoryChatMessage | None:
        with self._lock:
            return _detach(self._chat.get((self._ws(), norm_id(message_id))))

    def list_chat(
        self,
        factory_id: str,
        thread: str | None = None,
        *,
        before_seq: int | None = None,
        after_seq: int | None = None,
        limit: int = 50,
    ) -> builtins.list[FactoryChatMessage]:
        fid = norm_id(factory_id)
        with self._lock:
            rows = [
                r
                for r in self._rows(self._chat)
                if r.factory_id == fid
                and (thread is None or r.thread == thread)
                and (after_seq is None or r.seq > after_seq)
                and (before_seq is None or r.seq < before_seq)
            ]
        rows.sort(key=lambda r: r.seq)
        page = rows[:limit] if after_seq is not None else rows[-limit:] if limit > 0 else []
        return [_detach(r) for r in page]

    def update_chat_message(self, message_id: str, **fields: Any) -> FactoryChatMessage | None:
        check_fields(fields, CHAT_UPDATE_FIELDS, "update_chat_message")
        values = copy.deepcopy(dict(fields))
        if "routed" in values:
            values["routed"] = [dict(r) for r in values["routed"] or []]
        with self._lock:
            current = self._chat.get((self._ws(), norm_id(message_id)))
            if current is None:
                return None
            row = replace(current, **values)
            self._chat[(self._ws(), row.id)] = row
            return _detach(row)

    def chat_thread_stats(self, factory_id: str) -> builtins.list[ChatThreadStats]:
        fid = norm_id(factory_id)
        stats: dict[str, ChatThreadStats] = {}
        with self._lock:
            rows = [r for r in self._rows(self._chat) if r.factory_id == fid]
        for row in sorted(rows, key=lambda r: r.seq):
            prev = stats.get(row.thread)
            stats[row.thread] = ChatThreadStats(
                thread=row.thread,
                last_seq=row.seq,
                last_at=row.created_at,
                messages=(prev.messages if prev else 0) + 1,
            )
        return sorted(stats.values(), key=lambda t: t.last_seq, reverse=True)

    def upsert_chat_read(
        self, factory_id: str, user_id: str, thread: str, last_seq: int
    ) -> FactoryChatRead:
        key = (self._ws(), norm_id(factory_id), user_id, thread)
        with self._lock:
            current = self._chat_reads.get(key)
            if current is not None and current.last_seq >= last_seq:
                return _detach(current)
            row = FactoryChatRead(
                factory_id=key[1],
                user_id=user_id,
                thread=thread,
                last_seq=last_seq,
                updated_at=now_s(),
                workspace_id=key[0],
            )
            self._chat_reads[key] = row
            return _detach(row)

    def list_chat_reads(self, factory_id: str, user_id: str) -> builtins.list[FactoryChatRead]:
        fid = norm_id(factory_id)
        with self._lock:
            rows = [
                _detach(r)
                for r in self._rows(self._chat_reads)
                if r.factory_id == fid and r.user_id == user_id
            ]
        return sorted(rows, key=lambda r: r.thread)

    # ── Proposals ───────────────────────────────────────────────

    def create_proposal(
        self,
        factory_id: str,
        *,
        kind: str,
        title: str,
        author_kind: str,
        author: str,
        dedupe_key: str | None = None,
        **fields: Any,
    ) -> tuple[FactoryProposal, bool]:
        check_fields(fields, PROPOSAL_FIELDS - {"title"}, "create_proposal")
        values = clip_text("proposal", copy.deepcopy(normalise_ids(fields)))
        values.setdefault("approval", "operator")
        validate_enums("proposal", {**values, "kind": kind, "author_kind": author_kind})
        fid = norm_id(factory_id)
        now = now_s()
        with self._lock:
            if dedupe_key is not None:
                for row in self._rows(self._proposals):
                    if row.factory_id == fid and row.dedupe_key == dedupe_key:
                        return _detach(row), True
            row = FactoryProposal(
                id=new_id(),
                factory_id=fid,
                number=self.next_proposal_number(fid),
                kind=kind,
                title=clip_value("proposal", "title", title),
                author_kind=author_kind,
                author=author,
                dedupe_key=dedupe_key,
                created_at=now,
                updated_at=now,
                workspace_id=self._ws(),
                **_proposal_defaults(values),
            )
            self._proposals[(self._ws(), row.id)] = row
            return _detach(row), False

    def get_proposal(self, proposal_id: str) -> FactoryProposal | None:
        with self._lock:
            return _detach(self._proposals.get((self._ws(), norm_id(proposal_id))))

    def get_proposal_by_number(self, factory_id: str, number: int) -> FactoryProposal | None:
        fid = norm_id(factory_id)
        with self._lock:
            for row in self._rows(self._proposals):
                if row.factory_id == fid and row.number == number:
                    return _detach(row)
        return None

    def list_proposals(
        self,
        factory_id: str,
        *,
        status: str | Sequence[str] | None = None,
        kind: str | Sequence[str] | None = None,
        limit: int = 100,
        newest_first: bool = True,
    ) -> builtins.list[FactoryProposal]:
        fid = norm_id(factory_id)
        statuses = as_list(status)
        kinds = as_list(kind)
        with self._lock:
            rows = [
                _detach(r)
                for r in self._rows(self._proposals)
                if r.factory_id == fid
                and (statuses is None or r.status in statuses)
                and (kinds is None or r.kind in kinds)
            ]
        rows.sort(key=lambda r: r.number, reverse=newest_first)
        return rows[:limit]

    def update_proposal(
        self,
        proposal_id: str,
        *,
        expect_status: str | Sequence[str] | None = None,
        **fields: Any,
    ) -> FactoryProposal | None:
        check_fields(fields, PROPOSAL_FIELDS, "update_proposal")
        validate_enums("proposal", fields)
        expected = as_list(expect_status)
        values = clip_text("proposal", copy.deepcopy(normalise_ids(fields)))
        with self._lock:
            current = self._proposals.get((self._ws(), norm_id(proposal_id)))
            if current is None:
                return None
            if expected is not None and current.status not in expected:
                raise ProposalStateConflict(_detach(current))
            row = replace(current, **_proposal_defaults(values, partial=True), updated_at=now_s())
            self._proposals[(self._ws(), row.id)] = row
            return _detach(row)

    def next_proposal_number(self, factory_id: str) -> int:
        fid = norm_id(factory_id)
        with self._lock:
            numbers = [r.number for r in self._rows(self._proposals) if r.factory_id == fid]
        return max(numbers, default=0) + 1

    # ── Work graph ──────────────────────────────────────────────

    def upsert_task(
        self, factory_id: str, project_slug: str, key: str, **fields: Any
    ) -> tuple[FactoryTask, bool]:
        check_fields(fields, TASK_FIELDS, "upsert_task")
        validate_enums("task", fields)
        values = clip_text("task", copy.deepcopy(normalise_ids(fields)))
        fid = norm_id(factory_id)
        now = now_s()
        with self._lock:
            current = self.get_task_by_key(fid, project_slug, key)
            if current is not None:
                row = replace(current, **values, updated_at=now)
                created = False
            else:
                values.setdefault("title", key)
                values.setdefault("created_by", "server")
                values.setdefault("created_via", "server")
                row = FactoryTask(
                    id=new_id(),
                    factory_id=fid,
                    number=self.next_task_number(fid),
                    project_slug=project_slug,
                    key=key,
                    created_at=now,
                    updated_at=now,
                    workspace_id=self._ws(),
                    **_task_defaults(values),
                )
                created = True
            self._tasks[(self._ws(), row.id)] = row
            return _detach(row), created

    def get_task(self, task_id: str) -> FactoryTask | None:
        with self._lock:
            return _detach(self._tasks.get((self._ws(), norm_id(task_id))))

    def get_task_by_key(self, factory_id: str, project_slug: str, key: str) -> FactoryTask | None:
        fid = norm_id(factory_id)
        with self._lock:
            for row in self._rows(self._tasks):
                if row.factory_id == fid and row.project_slug == project_slug and row.key == key:
                    return _detach(row)
        return None

    def get_task_by_number(self, factory_id: str, number: int) -> FactoryTask | None:
        fid = norm_id(factory_id)
        with self._lock:
            for row in self._rows(self._tasks):
                if row.factory_id == fid and row.number == number:
                    return _detach(row)
        return None

    def list_tasks(
        self,
        factory_id: str,
        *,
        project_slug: str | None = None,
        states: Sequence[str] | None = None,
    ) -> builtins.list[FactoryTask]:
        fid = norm_id(factory_id)
        with self._lock:
            rows = [
                _detach(r)
                for r in self._rows(self._tasks)
                if r.factory_id == fid
                and (project_slug is None or r.project_slug == project_slug)
                and (states is None or r.state in states)
            ]
        return sorted(rows, key=lambda r: r.number)

    def update_task(self, task_id: str, **fields: Any) -> FactoryTask | None:
        check_fields(fields, TASK_FIELDS, "update_task")
        validate_enums("task", fields)
        values = clip_text("task", copy.deepcopy(normalise_ids(fields)))
        with self._lock:
            current = self._tasks.get((self._ws(), norm_id(task_id)))
            if current is None:
                return None
            row = replace(current, **_task_defaults(values, partial=True), updated_at=now_s())
            self._tasks[(self._ws(), row.id)] = row
            return _detach(row)

    def increment_task_attempts(self, task_id: str, by: int = 1) -> FactoryTask | None:
        with self._lock:
            current = self._tasks.get((self._ws(), norm_id(task_id)))
            if current is None:
                return None
            row = replace(current, attempts=current.attempts + by, updated_at=now_s())
            self._tasks[(self._ws(), row.id)] = row
            return _detach(row)

    def next_task_number(self, factory_id: str) -> int:
        fid = norm_id(factory_id)
        with self._lock:
            numbers = [r.number for r in self._rows(self._tasks) if r.factory_id == fid]
        return max(numbers, default=0) + 1

    # ── Loops ───────────────────────────────────────────────────

    def upsert_loop(
        self, factory_id: str, kind: str, subject: str, **fields: Any
    ) -> tuple[FactoryLoop, bool]:
        check_fields(fields, LOOP_FIELDS, "upsert_loop")
        validate_enums("loop", {**fields, "kind": kind})
        values = clip_text("loop", copy.deepcopy(normalise_ids(fields)))
        fid = norm_id(factory_id)
        now = now_s()
        with self._lock:
            current = self.get_loop_by_subject(fid, kind, subject)
            if current is not None:
                row = replace(current, **_loop_defaults(values, partial=True), updated_at=now)
                created = False
            else:
                values.setdefault("started_at", now)
                row = FactoryLoop(
                    id=new_id(),
                    factory_id=fid,
                    kind=kind,
                    subject=subject,
                    updated_at=now,
                    workspace_id=self._ws(),
                    **_loop_defaults(values),
                )
                created = True
            self._loops[(self._ws(), row.id)] = row
            return _detach(row), created

    def get_loop(self, loop_id: str) -> FactoryLoop | None:
        with self._lock:
            return _detach(self._loops.get((self._ws(), norm_id(loop_id))))

    def get_loop_by_subject(self, factory_id: str, kind: str, subject: str) -> FactoryLoop | None:
        fid = norm_id(factory_id)
        with self._lock:
            for row in self._rows(self._loops):
                if row.factory_id == fid and row.kind == kind and row.subject == subject:
                    return _detach(row)
        return None

    def list_loops(
        self,
        factory_id: str,
        *,
        states: Sequence[str] | None = None,
        kinds: Sequence[str] | None = None,
        project_slug: str | None = None,
        task_id: str | None = None,
        pr_id: str | None = None,
    ) -> builtins.list[FactoryLoop]:
        fid = norm_id(factory_id)
        tid, pid = opt_id(task_id), opt_id(pr_id)
        with self._lock:
            rows = [
                _detach(r)
                for r in self._rows(self._loops)
                if r.factory_id == fid
                and (states is None or r.state in states)
                and (kinds is None or r.kind in kinds)
                and (project_slug is None or r.project_slug == project_slug)
                and (tid is None or r.task_id == tid)
                and (pid is None or r.pr_id == pid)
            ]
        return sorted(rows, key=lambda r: (r.started_at, r.id))

    def update_loop(self, loop_id: str, **fields: Any) -> FactoryLoop | None:
        check_fields(fields, LOOP_FIELDS, "update_loop")
        validate_enums("loop", fields)
        values = clip_text("loop", copy.deepcopy(normalise_ids(fields)))
        with self._lock:
            current = self._loops.get((self._ws(), norm_id(loop_id)))
            if current is None:
                return None
            row = replace(current, **_loop_defaults(values, partial=True), updated_at=now_s())
            self._loops[(self._ws(), row.id)] = row
            return _detach(row)

    # ── Issues ──────────────────────────────────────────────────

    def upsert_issue(
        self,
        factory_id: str,
        *,
        class_: str,
        rule: str,
        target: str,
        summary: str,
        dedupe_key: str | None = None,
        reopen: bool = True,
        **fields: Any,
    ) -> tuple[FactoryIssue, bool]:
        check_fields(fields, ISSUE_FIELDS - {"rule", "target", "summary"}, "upsert_issue")
        validate_enums("issue", {**fields, "class_": class_})
        values = clip_text("issue", copy.deepcopy(normalise_ids(fields)))
        text = clip_text("issue", {"rule": rule, "target": target, "summary": summary})
        fid = norm_id(factory_id)
        now = now_s()
        with self._lock:
            if dedupe_key is not None:
                for current in self._rows(self._issues):
                    if current.factory_id == fid and current.dedupe_key == dedupe_key:
                        changes = {**values, **text}
                        changes.update(count=current.count + 1, last_seen=now)
                        if (
                            reopen
                            and current.status in ISSUE_REOPENABLE
                            and "status" not in values
                        ):
                            changes.update(status="open", resolved_at=None, resolved_by=None)
                        row = replace(current, **changes)
                        self._issues[(self._ws(), row.id)] = row
                        return _detach(row), False
            values.setdefault("source", "detector")
            row = FactoryIssue(
                id=new_id(),
                factory_id=fid,
                class_=class_,
                number=self.next_issue_number(fid, class_),
                **text,
                dedupe_key=dedupe_key,
                first_seen=now,
                last_seen=now,
                workspace_id=self._ws(),
                **values,
            )
            self._issues[(self._ws(), row.id)] = row
            return _detach(row), True

    def get_issue(self, issue_id: str) -> FactoryIssue | None:
        with self._lock:
            return _detach(self._issues.get((self._ws(), norm_id(issue_id))))

    def get_issue_by_number(
        self, factory_id: str, class_: str, number: int
    ) -> FactoryIssue | None:
        fid = norm_id(factory_id)
        with self._lock:
            for row in self._rows(self._issues):
                if row.factory_id == fid and row.class_ == class_ and row.number == number:
                    return _detach(row)
        return None

    def get_issue_by_dedupe(self, factory_id: str, dedupe_key: str) -> FactoryIssue | None:
        fid = norm_id(factory_id)
        with self._lock:
            for row in self._rows(self._issues):
                if row.factory_id == fid and row.dedupe_key == dedupe_key:
                    return _detach(row)
        return None

    def list_issues(
        self,
        factory_id: str,
        *,
        class_: str | Sequence[str] | None = None,
        statuses: Sequence[str] | None = None,
        project_slug: str | None = None,
        limit: int = 200,
    ) -> builtins.list[FactoryIssue]:
        fid = norm_id(factory_id)
        classes = as_list(class_)
        with self._lock:
            rows = [
                _detach(r)
                for r in self._rows(self._issues)
                if r.factory_id == fid
                and (classes is None or r.class_ in classes)
                and (statuses is None or r.status in statuses)
                and (project_slug is None or r.project_slug == project_slug)
            ]
        rows.sort(key=lambda r: (r.first_seen, r.number, r.id), reverse=True)
        return rows[:limit]

    def update_issue(self, issue_id: str, **fields: Any) -> FactoryIssue | None:
        check_fields(fields, ISSUE_FIELDS, "update_issue")
        validate_enums("issue", fields)
        values = clip_text("issue", copy.deepcopy(normalise_ids(fields)))
        with self._lock:
            current = self._issues.get((self._ws(), norm_id(issue_id)))
            if current is None:
                return None
            row = replace(current, **values)
            self._issues[(self._ws(), row.id)] = row
            return _detach(row)

    def next_issue_number(self, factory_id: str, class_: str) -> int:
        fid = norm_id(factory_id)
        with self._lock:
            numbers = [
                r.number
                for r in self._rows(self._issues)
                if r.factory_id == fid and r.class_ == class_
            ]
        return max(numbers, default=0) + 1

    # ── Spans ───────────────────────────────────────────────────

    def insert_span(
        self,
        factory_id: str,
        *,
        kind: str,
        name: str,
        trace_key: str,
        started_at: int | None = None,
        dedupe_key: str | None = None,
        span_id: str | None = None,
        **fields: Any,
    ) -> tuple[FactorySpan, bool]:
        check_fields(fields, SPAN_FIELDS - {"name"}, "insert_span")
        validate_enums("span", {**fields, "kind": kind})
        values = clip_text("span", copy.deepcopy(normalise_ids(fields)))
        fid = norm_id(factory_id)
        start = now_ms() if started_at is None else started_at
        with self._lock:
            if dedupe_key is not None:
                existing = self.get_span_by_dedupe(fid, dedupe_key)
                if existing is not None:
                    return existing, False
            row = FactorySpan(
                id=norm_id(span_id) if span_id else new_event_id(start),
                factory_id=fid,
                trace_key=trace_key,
                kind=kind,
                name=clip_value("span", "name", name),
                started_at=start,
                dedupe_key=dedupe_key,
                workspace_id=self._ws(),
                **values,
            )
            self._spans[(self._ws(), row.id)] = row
            return _detach(row), True

    def get_span(self, span_id: str) -> FactorySpan | None:
        with self._lock:
            return _detach(self._spans.get((self._ws(), norm_id(span_id))))

    def get_span_by_dedupe(self, factory_id: str, dedupe_key: str) -> FactorySpan | None:
        fid = norm_id(factory_id)
        with self._lock:
            for row in self._rows(self._spans):
                if row.factory_id == fid and row.dedupe_key == dedupe_key:
                    return _detach(row)
        return None

    def update_span(self, span_id: str, **fields: Any) -> FactorySpan | None:
        check_fields(fields, SPAN_FIELDS, "update_span")
        validate_enums("span", fields)
        values = clip_text("span", copy.deepcopy(normalise_ids(fields)))
        with self._lock:
            current = self._spans.get((self._ws(), norm_id(span_id)))
            if current is None:
                return None
            row = replace(current, **values)
            self._spans[(self._ws(), row.id)] = row
            return _detach(row)

    def _span_rows(
        self, factory_id: str, filters: Mapping[str, str | None]
    ) -> builtins.list[FactorySpan]:
        fid = norm_id(factory_id)
        wanted = {k: (opt_id(v) if k in _ID_FIELDS else v) for k, v in filters.items()}
        with self._lock:
            return [
                r
                for r in self._rows(self._spans)
                if r.factory_id == fid
                and all(v is None or getattr(r, k) == v for k, v in wanted.items())
            ]

    def list_open_spans(
        self,
        factory_id: str,
        kind: str | None = None,
        *,
        project_slug: str | None = None,
        pr_id: str | None = None,
        task_id: str | None = None,
        loop_id: str | None = None,
        session_id: str | None = None,
        trace_key: str | None = None,
        limit: int = 500,
    ) -> builtins.list[FactorySpan]:
        filters = {
            "kind": kind,
            "project_slug": project_slug,
            "pr_id": pr_id,
            "task_id": task_id,
            "loop_id": loop_id,
            "session_id": session_id,
            "trace_key": trace_key,
        }
        rows = [r for r in self._span_rows(factory_id, filters) if r.ended_at is None]
        rows.sort(key=lambda r: (r.started_at, r.id))
        return [_detach(r) for r in rows[:limit]]

    def list_spans(
        self,
        factory_id: str,
        *,
        kinds: Sequence[str] | None = None,
        since_ms: int | None = None,
        until_ms: int | None = None,
        project_slug: str | None = None,
        pr_id: str | None = None,
        task_id: str | None = None,
        loop_id: str | None = None,
        trace_key: str | None = None,
        limit: int = 1000,
    ) -> builtins.list[FactorySpan]:
        filters = {
            "project_slug": project_slug,
            "pr_id": pr_id,
            "task_id": task_id,
            "loop_id": loop_id,
            "trace_key": trace_key,
        }
        rows = [
            r
            for r in self._span_rows(factory_id, filters)
            if (kinds is None or r.kind in kinds)
            and (since_ms is None or r.started_at >= since_ms)
            and (until_ms is None or r.started_at < until_ms)
        ]
        rows.sort(key=lambda r: (r.started_at, r.id))
        return [_detach(r) for r in rows[:limit]]

    # ── Cost ledger ─────────────────────────────────────────────

    def add_cost_daily(
        self,
        factory_id: str,
        *,
        project_slug: str,
        role: str,
        agent_name: str,
        model: str,
        day_utc: str,
        cost_usd: float = 0.0,
        tokens_in: int = 0,
        tokens_out: int = 0,
        turns: int = 1,
    ) -> FactoryCostDaily:
        fid = norm_id(factory_id)
        agent_name = clip_value("cost", "agent_name", agent_name)
        model = clip_value("cost", "model", model)
        key = (self._ws(), fid, project_slug, role, agent_name, model, day_utc)
        with self._lock:
            current = self._cost.get(key) or FactoryCostDaily(
                factory_id=fid,
                project_slug=project_slug,
                role=role,
                agent_name=agent_name,
                model=model,
                day_utc=day_utc,
                workspace_id=self._ws(),
            )
            row = replace(
                current,
                cost_usd=current.cost_usd + float(cost_usd),
                tokens_in=current.tokens_in + int(tokens_in),
                tokens_out=current.tokens_out + int(tokens_out),
                turns=current.turns + int(turns),
                updated_at=now_s(),
            )
            self._cost[key] = row
            return _detach(row)

    def list_cost_daily(
        self,
        factory_id: str,
        *,
        since_day: str | None = None,
        until_day: str | None = None,
        project_slug: str | None = None,
    ) -> builtins.list[FactoryCostDaily]:
        fid = norm_id(factory_id)
        with self._lock:
            rows = [
                _detach(r)
                for r in self._rows(self._cost)
                if r.factory_id == fid
                and (since_day is None or r.day_utc >= since_day)
                and (until_day is None or r.day_utc <= until_day)
                and (project_slug is None or r.project_slug == project_slug)
            ]
        return sorted(rows, key=_cost_order)

    # ── Platform versions ───────────────────────────────────────

    def upsert_version(
        self,
        *,
        bundles: Mapping[str, Any],
        knobs: Mapping[str, Any],
        image_tag: str | None = None,
        server_sha: str | None = None,
        note: str | None = None,
    ) -> tuple[FactoryVersion, bool]:
        clipped = clip_text("version", {"image_tag": image_tag, "note": note})
        image_tag, note = clipped["image_tag"], clipped["note"]
        content_hash = version_content_hash(
            bundles=bundles, knobs=knobs, image_tag=image_tag, server_sha=server_sha
        )
        with self._lock:
            for row in self._rows(self._versions):
                if row.content_hash == content_hash:
                    return _detach(row), False
            row = FactoryVersion(
                id=new_id(),
                content_hash=content_hash,
                bundles=copy.deepcopy(dict(bundles)),
                knobs=copy.deepcopy(dict(knobs)),
                image_tag=image_tag,
                server_sha=server_sha,
                note=note,
                created_at=now_s(),
                workspace_id=self._ws(),
            )
            self._versions[(self._ws(), row.id)] = row
            return _detach(row), True

    def get_version(self, version_id: str) -> FactoryVersion | None:
        with self._lock:
            return _detach(self._versions.get((self._ws(), norm_id(version_id))))

    def list_versions(self, limit: int = 50) -> builtins.list[FactoryVersion]:
        with self._lock:
            rows = [_detach(r) for r in self._rows(self._versions)]
        rows.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        return rows[:limit]

    # ── Definitions ─────────────────────────────────────────────

    def put_definition(
        self,
        factory_id: str,
        *,
        doc: Mapping[str, Any],
        created_by: str,
        created_via: str,
        version: int | None = None,
        parent_version: int | None = None,
        proposal_id: str | None = None,
    ) -> FactoryDefinition:
        validate_enums("definition", {"created_via": created_via})
        fid = norm_id(factory_id)
        with self._lock:
            if version is None:
                latest = self.latest_definition(fid)
                version = 1 if latest is None else latest.version + 1
            existing = self.get_definition(fid, version)
            if existing is not None:
                raise DefinitionVersionExists(existing)
            row = FactoryDefinition(
                factory_id=fid,
                version=version,
                doc=copy.deepcopy(dict(doc)),
                hash=canonical_hash(dict(doc)),
                created_by=created_by,
                created_via=created_via,
                parent_version=parent_version,
                proposal_id=opt_id(proposal_id),
                created_at=now_s(),
                workspace_id=self._ws(),
            )
            self._definitions[(self._ws(), fid, version)] = row
            return _detach(row)

    def get_definition(self, factory_id: str, version: int) -> FactoryDefinition | None:
        with self._lock:
            return _detach(self._definitions.get((self._ws(), norm_id(factory_id), version)))

    def latest_definition(self, factory_id: str) -> FactoryDefinition | None:
        rows = self.list_definitions(factory_id, limit=1)
        return rows[0] if rows else None

    def list_definitions(
        self, factory_id: str, limit: int = 50
    ) -> builtins.list[FactoryDefinition]:
        fid = norm_id(factory_id)
        with self._lock:
            rows = [_detach(r) for r in self._rows(self._definitions) if r.factory_id == fid]
        rows.sort(key=lambda r: r.version, reverse=True)
        return rows[:limit]

    # ── Feedback ────────────────────────────────────────────────

    def add_feedback(
        self,
        factory_id: str,
        *,
        target_kind: str,
        target_id: str,
        author_kind: str,
        author: str,
        kind: str,
        project_slug: str | None = None,
        head_sha: str | None = None,
        rating: int | None = None,
        tags: Sequence[str] | None = None,
        body: str | None = None,
        def_version: int | None = None,
    ) -> FactoryFeedback:
        validate_enums(
            "feedback", {"target_kind": target_kind, "author_kind": author_kind, "kind": kind}
        )
        row = FactoryFeedback(
            id=new_id(),
            factory_id=norm_id(factory_id),
            target_kind=target_kind,
            target_id=target_id,
            author_kind=author_kind,
            author=author,
            kind=kind,
            project_slug=project_slug,
            head_sha=head_sha,
            rating=rating,
            tags=list(tags or []),
            body=body,
            def_version=def_version,
            created_at=now_s(),
            workspace_id=self._ws(),
        )
        with self._lock:
            self._feedback[(self._ws(), row.id)] = row
        return _detach(row)

    def list_feedback(
        self,
        factory_id: str,
        *,
        target_kind: str | None = None,
        target_id: str | None = None,
        kinds: Sequence[str] | None = None,
        since: int | None = None,
        limit: int = 200,
    ) -> builtins.list[FactoryFeedback]:
        fid = norm_id(factory_id)
        with self._lock:
            rows = [
                _detach(r)
                for r in self._rows(self._feedback)
                if r.factory_id == fid
                and (target_kind is None or r.target_kind == target_kind)
                and (target_id is None or r.target_id == target_id)
                and (kinds is None or r.kind in kinds)
                and (since is None or r.created_at >= since)
            ]
        rows.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        return rows[:limit]


__all__ = [
    "DecisionNotFound",
    "DecisionNotOpen",
    "DefinitionVersionExists",
    "FactoryStore",
    "FactoryStoreError",
    "InMemoryFactoryStore",
    "ProposalStateConflict",
]
