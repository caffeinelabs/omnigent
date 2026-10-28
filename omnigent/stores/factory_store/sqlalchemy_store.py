"""SQLAlchemy-backed factory store."""

from __future__ import annotations

import builtins
import random
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, TypeVar

from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from omnigent.db.db_models import (
    SqlFactoryChatMessage,
    SqlFactoryChatRead,
    SqlFactoryCostDaily,
    SqlFactoryDecision,
    SqlFactoryDefinition,
    SqlFactoryDemo,
    SqlFactoryEvent,
    SqlFactoryFeedback,
    SqlFactoryIssue,
    SqlFactoryLoop,
    SqlFactoryPerson,
    SqlFactoryPr,
    SqlFactoryProject,
    SqlFactoryProposal,
    SqlFactorySpan,
    SqlFactoryTask,
    SqlFactoryVersion,
    current_workspace_id,
)
from omnigent.db.enum_codecs import (
    decode_factory_actor_kind,
    decode_factory_chat_author_kind,
    decode_factory_decision_source,
    decode_factory_decision_status,
    decode_factory_definition_via,
    decode_factory_demo_status,
    decode_factory_event_severity,
    decode_factory_feedback_kind,
    decode_factory_feedback_target,
    decode_factory_issue_class,
    decode_factory_issue_source,
    decode_factory_issue_status,
    decode_factory_loop_kind,
    decode_factory_loop_state,
    decode_factory_person_role,
    decode_factory_pr_state,
    decode_factory_project_stage,
    decode_factory_project_status,
    decode_factory_proposal_approval,
    decode_factory_proposal_class,
    decode_factory_proposal_kind,
    decode_factory_proposal_status,
    decode_factory_span_kind,
    decode_factory_span_outcome,
    decode_factory_task_kind,
    decode_factory_task_state,
    encode_factory_actor_kind,
    encode_factory_chat_author_kind,
    encode_factory_decision_source,
    encode_factory_decision_status,
    encode_factory_definition_via,
    encode_factory_demo_status,
    encode_factory_event_severity,
    encode_factory_feedback_kind,
    encode_factory_feedback_target,
    encode_factory_issue_class,
    encode_factory_issue_source,
    encode_factory_issue_status,
    encode_factory_loop_kind,
    encode_factory_loop_state,
    encode_factory_person_role,
    encode_factory_pr_state,
    encode_factory_project_stage,
    encode_factory_project_status,
    encode_factory_proposal_approval,
    encode_factory_proposal_class,
    encode_factory_proposal_kind,
    encode_factory_proposal_status,
    encode_factory_span_kind,
    encode_factory_span_outcome,
    encode_factory_task_kind,
    encode_factory_task_state,
)
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    run_write_transaction,
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
from omnigent.stores.factory_store import (
    CHAT_UPDATE_FIELDS,
    DECISION_FIELDS,
    DECISION_UPDATE_FIELDS,
    DEFAULT_PR_POLL_INTERVAL_S,
    ISSUE_FIELDS,
    ISSUE_REOPENABLE,
    LOOP_FIELDS,
    PERSON_UPDATE_FIELDS,
    PR_FIELDS,
    PROJECT_FIELDS,
    PROPOSAL_FIELDS,
    SPAN_FIELDS,
    TASK_FIELDS,
    TERMINAL_PR_WINDOW_S,
    DecisionNotFound,
    DecisionNotOpen,
    DefinitionVersionExists,
    FactoryStore,
    ProposalStateConflict,
    as_list,
    canonical_hash,
    check_fields,
    clip_text,
    clip_value,
    default_pr_url,
    dump_json,
    load_json,
    new_event_id,
    new_id,
    norm_id,
    normalise_ids,
    now_ms,
    now_s,
    opt_id,
    validate_enums,
    validate_pr_fields,
    validate_project_fields,
    version_content_hash,
)

_T = TypeVar("_T")

# A collision on a UNIQUE key (decision number, project slug, PR number) or a
# lock conflict between concurrent writers is retried after a jittered,
# exponentially growing pause. Numbered sequences get the larger budget: every
# racer but one loses each round where nothing serializes them (CockroachDB).
_WRITE_RETRIES = 5
_SEQUENCE_RETRIES = 12
_RETRY_PAUSE_S = 0.02
_RETRY_PAUSE_MAX_S = 0.4

# Lock conflicts worth replaying: MySQL deadlock (1213) and lock wait timeout
# (1205); PostgreSQL serialization failure (40001) and deadlock (40P01).
_TRANSIENT_MYSQL_ERRNOS = frozenset({1205, 1213})
_TRANSIENT_SQLSTATES = frozenset({"40001", "40P01"})

# Sequence locks per dialect: a transaction advisory lock, or a named lock.
_SEQUENCE_LOCKS = {"postgresql": "advisory", "mysql": "named", "mariadb": "named"}

# Seconds a MySQL writer waits for a sequence's named lock before it goes
# ahead unserialized (the UNIQUE key and retries still keep numbers unique).
_MYSQL_LOCK_WAIT_S = 10

# Namespaces mixed into the per-factory PostgreSQL advisory lock keys, one per
# numbered sequence (decisions D-n, chat seq, proposals C-n, tasks T-n, issues,
# definition versions).
_DECISION_LOCK_NS = 0x66616374
_CHAT_LOCK_NS = 0x63686174
_PROPOSAL_LOCK_NS = 0x70726F70
_TASK_LOCK_NS = 0x7461736B
_ISSUE_LOCK_NS = 0x69737375
_DEFINITION_LOCK_NS = 0x64656673


def _lock_key(namespace: int, workspace_id: int, factory_id: str) -> int:
    """A signed 64-bit advisory-lock key for one workspace's factory sequence."""
    value = int.from_bytes(bytes.fromhex(factory_id)[:8], "big") ^ namespace
    value = (value ^ (workspace_id * 0x9E3779B97F4A7C15)) & ((1 << 64) - 1)
    return value - (1 << 64) if value >= (1 << 63) else value


def _is_transient(exc: DBAPIError) -> bool:
    """Whether *exc* is a write collision or lock conflict a replay can clear."""
    if isinstance(exc, IntegrityError):
        return True
    original = exc.orig
    state = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if state in _TRANSIENT_SQLSTATES:
        return True
    args = getattr(original, "args", ())
    return bool(args) and isinstance(args[0], int) and args[0] in _TRANSIENT_MYSQL_ERRNOS


def _decision_lock_key(workspace_id: int, factory_id: str) -> int:
    """A signed 64-bit advisory-lock key for one workspace's factory decision numbering."""
    return _lock_key(_DECISION_LOCK_NS, workspace_id, factory_id)


_PROJECT_JSON = frozenset({"charter", "env"})
_PR_JSON = frozenset({"gates", "sentinel", "human_review", "preview"})


def _project(row: SqlFactoryProject) -> FactoryProject:
    return FactoryProject(
        id=row.id,
        factory_id=row.factory_id,
        slug=row.slug,
        title=row.title,
        repo=row.repo,
        base_branch=row.base_branch,
        charter=load_json(row.charter),
        priority=row.priority,
        stage=decode_factory_project_stage(row.stage),
        stage_changed_at=row.stage_changed_at,
        agent_stage=row.agent_stage,
        agent_stage_at=row.agent_stage_at,
        pl_session_id=row.pl_session_id,
        env=load_json(row.env),
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        status=decode_factory_project_status(row.status),
        parent_slug=row.parent_slug,
        closed_at=row.closed_at,
        def_version=row.def_version,
        version_id=row.version_id,
        workspace_id=row.workspace_id,
    )


def _pr(row: SqlFactoryPr) -> FactoryPr:
    return FactoryPr(
        id=row.id,
        factory_id=row.factory_id,
        repo=row.repo,
        number=row.number,
        url=row.url,
        title=row.title,
        head_ref=row.head_ref,
        base_ref=row.base_ref,
        head_sha=row.head_sha,
        state=decode_factory_pr_state(row.state),
        is_draft=bool(row.is_draft),
        project_slug=row.project_slug,
        task=row.task,
        additions=row.additions,
        deletions=row.deletions,
        changed_files=row.changed_files,
        gates=load_json(row.gates) or {},
        ready=bool(row.ready),
        ready_at=row.ready_at,
        sentinel=load_json(row.sentinel),
        human_review=load_json(row.human_review),
        preview=load_json(row.preview),
        merged_sha=row.merged_sha,
        merged_by=row.merged_by,
        merged_at=row.merged_at,
        closed_at=row.closed_at,
        source=row.source,
        last_polled_at=row.last_polled_at,
        poll_error=row.poll_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
        opened_at=row.opened_at,
        first_ready_at=row.first_ready_at,
        task_id=row.task_id,
        version_id=row.version_id,
        workspace_id=row.workspace_id,
    )


def _decision(row: SqlFactoryDecision) -> FactoryDecision:
    return FactoryDecision(
        id=row.id,
        factory_id=row.factory_id,
        number=row.number,
        question=row.question,
        source=decode_factory_decision_source(row.source),
        status=decode_factory_decision_status(row.status),
        dedupe_key=row.dedupe_key,
        options=load_json(row.options) or [],
        blocking=bool(row.blocking),
        allow_text=bool(row.allow_text),
        project_slug=row.project_slug,
        asked_by_session_id=row.asked_by_session_id,
        context=row.context,
        answer_option_id=row.answer_option_id,
        answer_text=row.answer_text,
        answered_by=row.answered_by,
        answered_at=row.answered_at,
        delivered_mail_id=row.delivered_mail_id,
        delivered_at=row.delivered_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        assignee=row.assignee,
        claimed_by=row.claimed_by,
        claimed_at=row.claimed_at,
        thread=row.thread,
        subject=row.subject,
        workspace_id=row.workspace_id,
    )


def _event(row: SqlFactoryEvent) -> FactoryEvent:
    return FactoryEvent(
        id=row.id,
        factory_id=row.factory_id,
        ts=row.ts,
        kind=row.kind,
        summary=row.summary,
        severity=decode_factory_event_severity(row.severity),
        project_slug=row.project_slug,
        from_id=row.from_id,
        to_id=row.to_id,
        subject=row.subject,
        data=load_json(row.data),
        workspace_id=row.workspace_id,
    )


def _demo(row: SqlFactoryDemo) -> FactoryDemo:
    return FactoryDemo(
        id=row.id,
        factory_id=row.factory_id,
        project_slug=row.project_slug,
        repo=row.repo,
        pr_number=row.pr_number,
        head_sha=row.head_sha,
        session_id=row.session_id,
        created_by_session=row.created_by_session,
        status=decode_factory_demo_status(row.status),
        title=row.title,
        summary=row.summary,
        files=load_json(row.files) or [],
        manifest=load_json(row.manifest),
        pr_id=row.pr_id,
        preview_url=row.preview_url,
        created_at=row.created_at,
        workspace_id=row.workspace_id,
    )


def _project_columns(fields: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in normalise_ids(fields).items():
        if key in _PROJECT_JSON:
            out[key] = dump_json(value)
        elif key == "stage":
            out[key] = encode_factory_project_stage(value)
        elif key == "status":
            out[key] = encode_factory_project_status(value)
        else:
            out[key] = value
    return out


def _pr_columns(fields: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in normalise_ids(fields).items():
        if key in _PR_JSON:
            out[key] = dump_json({} if key == "gates" and value is None else value)
        elif key == "state":
            out[key] = encode_factory_pr_state(value)
        elif key in ("is_draft", "ready"):
            out[key] = 1 if value else 0
        else:
            out[key] = value
    return out


def _kind_filter(column: Any, patterns: Sequence[str]) -> ColumnElement[bool]:
    """``kind = x`` / ``kind LIKE 'prefix%'`` for exact kinds and ``prefix.*`` patterns."""
    clauses: list[ColumnElement[bool]] = []
    exact = [p for p in patterns if not p.endswith("*")]
    if exact:
        clauses.append(column.in_(exact))
    for pattern in patterns:
        if pattern.endswith("*"):
            clauses.append(column.startswith(pattern[:-1], autoescape=True))
    return or_(*clauses) if clauses else column.in_([])


# ── Round-3 row mappers ─────────────────────────────────────────

_Encoder = Callable[[str], int]

_ENCODERS: dict[str, dict[str, _Encoder]] = {
    "proposal": {
        "kind": encode_factory_proposal_kind,
        "status": encode_factory_proposal_status,
        "approval": encode_factory_proposal_approval,
        "class_": encode_factory_proposal_class,
        "author_kind": encode_factory_actor_kind,
    },
    "task": {
        "kind": encode_factory_task_kind,
        "state": encode_factory_task_state,
        "created_via": encode_factory_actor_kind,
    },
    "loop": {"kind": encode_factory_loop_kind, "state": encode_factory_loop_state},
    "issue": {
        "class_": encode_factory_issue_class,
        "severity": encode_factory_event_severity,
        "source": encode_factory_issue_source,
        "status": encode_factory_issue_status,
    },
    "span": {"kind": encode_factory_span_kind, "outcome": encode_factory_span_outcome},
    "person": {"role": encode_factory_person_role},
}

_JSON_COLUMNS: dict[str, frozenset[str]] = {
    "proposal": frozenset(
        {
            "ops",
            "before",
            "after",
            "analysis",
            "evidence",
            "expected_effect",
            "warnings",
            "based_on",
            "result",
        }
    ),
    "task": frozenset({"card", "deps", "scope", "session_ids", "budget"}),
    "loop": frozenset({"spec", "spent"}),
    "issue": frozenset({"evidence"}),
    "span": frozenset({"attrs"}),
    "person": frozenset({"notify"}),
    "chat": frozenset({"routed", "card"}),
}

_EMPTY_JSON: dict[str, dict[str, Any]] = {
    "proposal": {"ops": [], "evidence": [], "warnings": []},
    "task": {"card": {}, "deps": [], "scope": [], "session_ids": []},
    "loop": {"spec": {}, "spent": {}},
    "chat": {"routed": []},
}
"""JSON columns whose ``None`` means empty (NOT NULL or list-valued on the entity)."""


def _columns(entity: str, fields: Mapping[str, Any]) -> dict[str, Any]:
    """Entity field values as ORM attribute values (codes, JSON text, bare-hex ids)."""
    encoders = _ENCODERS.get(entity, {})
    json_columns = _JSON_COLUMNS.get(entity, frozenset())
    empty = _EMPTY_JSON.get(entity, {})
    out: dict[str, Any] = {}
    for key, value in normalise_ids(clip_text(entity, fields)).items():
        if key in json_columns:
            out[key] = dump_json(empty.get(key) if value is None and key in empty else value)
        elif key in encoders:
            out[key] = None if value is None else encoders[key](value)
        else:
            out[key] = value
    return out


def _person(row: SqlFactoryPerson) -> FactoryPerson:
    return FactoryPerson(
        factory_id=row.factory_id,
        user_id=row.user_id,
        role=decode_factory_person_role(row.role),
        is_creator=bool(row.is_creator),
        invited_by=row.invited_by,
        invited_at=row.invited_at,
        last_seen_at=row.last_seen_at,
        notify=load_json(row.notify),
        workspace_id=row.workspace_id,
    )


def _chat(row: SqlFactoryChatMessage) -> FactoryChatMessage:
    return FactoryChatMessage(
        id=row.id,
        factory_id=row.factory_id,
        thread=row.thread,
        seq=row.seq,
        author_kind=decode_factory_chat_author_kind(row.author_kind),
        author=row.author,
        body=row.body,
        author_role=row.author_role,
        mentions=load_json(row.mentions) or [],
        refs=load_json(row.refs) or [],
        reply_to=row.reply_to,
        card=load_json(row.card),
        routed=load_json(row.routed) or [],
        auto_reply=bool(row.auto_reply),
        created_at=row.created_at,
        workspace_id=row.workspace_id,
    )


def _chat_read(row: SqlFactoryChatRead) -> FactoryChatRead:
    return FactoryChatRead(
        factory_id=row.factory_id,
        user_id=row.user_id,
        thread=row.thread,
        last_seq=row.last_seq,
        updated_at=row.updated_at,
        workspace_id=row.workspace_id,
    )


def _proposal(row: SqlFactoryProposal) -> FactoryProposal:
    return FactoryProposal(
        id=row.id,
        factory_id=row.factory_id,
        number=row.number,
        kind=decode_factory_proposal_kind(row.kind),
        title=row.title,
        status=decode_factory_proposal_status(row.status),
        approval=decode_factory_proposal_approval(row.approval),
        class_=decode_factory_proposal_class(row.class_) if row.class_ is not None else None,
        ops=load_json(row.ops) or [],
        before=load_json(row.before),
        after=load_json(row.after),
        rationale=row.rationale,
        analysis=load_json(row.analysis),
        evidence=load_json(row.evidence) or [],
        expected_effect=load_json(row.expected_effect),
        warnings=load_json(row.warnings) or [],
        author_kind=decode_factory_actor_kind(row.author_kind),
        author=row.author,
        author_role=row.author_role,
        on_behalf_of=row.on_behalf_of,
        cosigned_by=row.cosigned_by,
        thread=row.thread,
        reply_to_chat=row.reply_to_chat,
        decision_id=row.decision_id,
        based_on=load_json(row.based_on),
        trial_until=row.trial_until,
        undo_until=row.undo_until,
        result=load_json(row.result),
        def_version_from=row.def_version_from,
        def_version_to=row.def_version_to,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        applied_at=row.applied_at,
        reverted_at=row.reverted_at,
        reverted_by=row.reverted_by,
        dedupe_key=row.dedupe_key,
        created_at=row.created_at,
        updated_at=row.updated_at,
        workspace_id=row.workspace_id,
    )


def _task(row: SqlFactoryTask) -> FactoryTask:
    return FactoryTask(
        id=row.id,
        factory_id=row.factory_id,
        number=row.number,
        project_slug=row.project_slug,
        key=row.key,
        title=row.title,
        kind=decode_factory_task_kind(row.kind),
        state=decode_factory_task_state(row.state),
        card=load_json(row.card) or {},
        deps=load_json(row.deps) or [],
        scope=load_json(row.scope) or [],
        priority=row.priority,
        estimate_min=row.estimate_min,
        vendor=row.vendor,
        session_ids=load_json(row.session_ids) or [],
        pr_id=row.pr_id,
        attempts=row.attempts,
        budget=load_json(row.budget),
        cost_usd=row.cost_usd,
        created_by=row.created_by,
        created_via=decode_factory_actor_kind(row.created_via),
        def_version=row.def_version,
        started_at=row.started_at,
        closed_at=row.closed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        workspace_id=row.workspace_id,
    )


def _loop(row: SqlFactoryLoop) -> FactoryLoop:
    return FactoryLoop(
        id=row.id,
        factory_id=row.factory_id,
        kind=decode_factory_loop_kind(row.kind),
        subject=row.subject,
        project_slug=row.project_slug,
        task_id=row.task_id,
        pr_id=row.pr_id,
        state=decode_factory_loop_state(row.state),
        iteration=row.iteration,
        spec=load_json(row.spec) or {},
        spent=load_json(row.spent) or {},
        last_verdict=row.last_verdict,
        last_reason=row.last_reason,
        decision_id=row.decision_id,
        def_version=row.def_version,
        started_at=row.started_at,
        updated_at=row.updated_at,
        ended_at=row.ended_at,
        workspace_id=row.workspace_id,
    )


def _issue(row: SqlFactoryIssue) -> FactoryIssue:
    return FactoryIssue(
        id=row.id,
        factory_id=row.factory_id,
        class_=decode_factory_issue_class(row.class_),
        number=row.number,
        rule=row.rule,
        target=row.target,
        summary=row.summary,
        category=row.category,
        severity=decode_factory_event_severity(row.severity),
        source=decode_factory_issue_source(row.source),
        project_slug=row.project_slug,
        body=row.body,
        evidence=load_json(row.evidence),
        status=decode_factory_issue_status(row.status),
        assignee=row.assignee,
        snoozed_until=row.snoozed_until,
        acked_by=row.acked_by,
        resolved_by=row.resolved_by,
        dedupe_key=row.dedupe_key,
        count=row.count,
        first_seen=row.first_seen,
        last_seen=row.last_seen,
        resolved_at=row.resolved_at,
        proposal_id=row.proposal_id,
        version_id=row.version_id,
        def_version=row.def_version,
        workspace_id=row.workspace_id,
    )


def _span(row: SqlFactorySpan) -> FactorySpan:
    return FactorySpan(
        id=row.id,
        factory_id=row.factory_id,
        trace_key=row.trace_key,
        kind=decode_factory_span_kind(row.kind),
        name=row.name,
        started_at=row.started_at,
        parent_id=row.parent_id,
        project_slug=row.project_slug,
        pr_id=row.pr_id,
        task_id=row.task_id,
        loop_id=row.loop_id,
        head_sha=row.head_sha,
        session_id=row.session_id,
        role=row.role,
        agent_name=row.agent_name,
        actor=row.actor,
        ended_at=row.ended_at,
        outcome=decode_factory_span_outcome(row.outcome),
        attempt=row.attempt,
        cost_usd=row.cost_usd,
        tokens_in=row.tokens_in,
        tokens_out=row.tokens_out,
        version_id=row.version_id,
        def_version=row.def_version,
        dedupe_key=row.dedupe_key,
        attrs=load_json(row.attrs),
        workspace_id=row.workspace_id,
    )


def _cost(row: SqlFactoryCostDaily) -> FactoryCostDaily:
    return FactoryCostDaily(
        factory_id=row.factory_id,
        project_slug=row.project_slug,
        role=row.role,
        agent_name=row.agent_name,
        model=row.model,
        day_utc=row.day_utc,
        cost_usd=float(row.cost_usd),
        tokens_in=int(row.tokens_in),
        tokens_out=int(row.tokens_out),
        turns=int(row.turns),
        updated_at=row.updated_at,
        workspace_id=row.workspace_id,
    )


def _version(row: SqlFactoryVersion) -> FactoryVersion:
    return FactoryVersion(
        id=row.id,
        content_hash=row.content_hash,
        bundles=load_json(row.bundles) or {},
        knobs=load_json(row.knobs) or {},
        image_tag=row.image_tag,
        server_sha=row.server_sha,
        note=row.note,
        created_at=row.created_at,
        workspace_id=row.workspace_id,
    )


def _definition(row: SqlFactoryDefinition) -> FactoryDefinition:
    return FactoryDefinition(
        factory_id=row.factory_id,
        version=row.version,
        doc=load_json(row.doc) or {},
        hash=row.hash,
        created_by=row.created_by,
        created_via=decode_factory_definition_via(row.created_via),
        parent_version=row.parent_version,
        proposal_id=row.proposal_id,
        created_at=row.created_at,
        workspace_id=row.workspace_id,
    )


def _feedback(row: SqlFactoryFeedback) -> FactoryFeedback:
    return FactoryFeedback(
        id=row.id,
        factory_id=row.factory_id,
        target_kind=decode_factory_feedback_target(row.target_kind),
        target_id=row.target_id,
        author_kind=decode_factory_actor_kind(row.author_kind),
        author=row.author,
        kind=decode_factory_feedback_kind(row.kind),
        project_slug=row.project_slug,
        head_sha=row.head_sha,
        rating=row.rating,
        tags=load_json(row.tags) or [],
        body=row.body,
        def_version=row.def_version,
        created_at=row.created_at,
        workspace_id=row.workspace_id,
    )


class SqlAlchemyFactoryStore(FactoryStore):
    """
    SQLAlchemy-backed implementation of :class:`FactoryStore`.

    :param storage_location: SQLAlchemy database URI, e.g. ``"sqlite:///chat.db"``.
    """

    def __init__(self, storage_location: str) -> None:
        self.storage_location = storage_location
        self._engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            self._engine, query_name_prefix="omnigent.factory_store"
        )
        self._session_immediate = make_named_managed_session_maker(
            self._engine, query_name_prefix="omnigent.factory_store", immediate=True
        )
        # How writers of one numbered sequence are serialized. SQLite's BEGIN
        # IMMEDIATE already does; CockroachDB relies on the UNIQUE key and replays.
        self._sequence_lock = _SEQUENCE_LOCKS.get(self._engine.dialect.name)

    @contextmanager
    def _mysql_sequence_lock(self, sequence: tuple[int, str] | None) -> Iterator[None]:
        """
        Hold a MySQL named lock on one factory sequence until the write commits.

        ``GET_LOCK`` belongs to a connection, not a transaction, so it is taken
        on a session of its own that outlives the write transaction.
        """
        if sequence is None or self._sequence_lock != "named":
            yield
            return
        name = f"omnigent_factory_{_lock_key(sequence[0], current_workspace_id(), sequence[1])}"
        with self._session("lock_factory_sequence") as session:
            held = session.execute(
                text("SELECT GET_LOCK(:name, :wait)"),
                {"name": name, "wait": _MYSQL_LOCK_WAIT_S},
            ).scalar()
            try:
                yield
            finally:
                if held == 1:
                    session.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": name})

    def _write(
        self,
        operation: str,
        callback: Callable[[Session], _T],
        *,
        sequence: tuple[int, str] | None = None,
    ) -> _T:
        """
        Run a write transaction, replaying collisions and lock conflicts between racing writers.

        :param sequence: ``(lock namespace, factory id)`` of the numbered sequence the
            write extends. Its writers are serialized where the backend can, and get a
            larger replay budget.
        """

        def locked(session: Session) -> _T:
            if sequence is not None and self._sequence_lock == "advisory":
                session.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"),
                    {"key": _lock_key(sequence[0], current_workspace_id(), sequence[1])},
                )
            return callback(session)

        budget = _WRITE_RETRIES if sequence is None else _SEQUENCE_RETRIES
        for attempt in range(budget + 1):
            try:
                with self._mysql_sequence_lock(sequence):
                    return run_write_transaction(self._session_immediate, operation, locked)
            except DBAPIError as exc:
                if attempt == budget or not _is_transient(exc):
                    raise
                pause = min(_RETRY_PAUSE_S * 2**attempt, _RETRY_PAUSE_MAX_S)
                time.sleep(pause * random.random())
        raise AssertionError("unreachable")

    # ── Projects ────────────────────────────────────────────────

    def _project_row(
        self, session: Session, factory_id: str, slug: str
    ) -> SqlFactoryProject | None:
        return session.execute(
            select(SqlFactoryProject).where(
                SqlFactoryProject.workspace_id == current_workspace_id(),
                SqlFactoryProject.factory_id == factory_id,
                SqlFactoryProject.slug == slug,
            )
        ).scalar_one_or_none()

    @staticmethod
    def _apply_project(row: SqlFactoryProject, fields: Mapping[str, Any], now: int) -> None:
        values = dict(fields)
        stage = values.get("stage")
        if (
            stage is not None
            and "stage_changed_at" not in values
            and row.stage != encode_factory_project_stage(stage)
        ):
            values["stage_changed_at"] = now
        for key, value in _project_columns(values).items():
            setattr(row, key, value)
        row.updated_at = now

    def upsert_project(self, factory_id: str, slug: str, **fields: Any) -> FactoryProject:
        check_fields(fields, PROJECT_FIELDS, "upsert_project")
        validate_project_fields(fields)
        fid = norm_id(factory_id)

        def write(session: Session) -> FactoryProject:
            now = now_s()
            row = self._project_row(session, fid, slug)
            if row is None:
                values = dict(fields)
                values.setdefault("title", slug)
                values.setdefault("stage", "queued")
                values.setdefault("status", "active")
                values.setdefault("stage_changed_at", now)
                values.setdefault("created_by", "server")
                row = SqlFactoryProject(
                    id=new_id(),
                    factory_id=fid,
                    slug=slug,
                    created_at=now,
                    updated_at=now,
                    **_project_columns(values),
                )
                session.add(row)
                session.flush()
            else:
                self._apply_project(row, fields, now)
            return _project(row)

        return self._write("upsert_project", write)

    def get_project(self, factory_id: str, slug: str) -> FactoryProject | None:
        fid = norm_id(factory_id)
        with self._session("get_project_by_slug") as session:
            row = self._project_row(session, fid, slug)
            return _project(row) if row is not None else None

    def get_project_by_id(self, project_id: str) -> FactoryProject | None:
        with self._session("get_project") as session:
            row = session.get(SqlFactoryProject, (current_workspace_id(), norm_id(project_id)))
            return _project(row) if row is not None else None

    def list_projects(self, factory_id: str) -> builtins.list[FactoryProject]:
        fid = norm_id(factory_id)
        with self._session("list_factory_projects") as session:
            rows = (
                session.execute(
                    select(SqlFactoryProject)
                    .where(
                        SqlFactoryProject.workspace_id == current_workspace_id(),
                        SqlFactoryProject.factory_id == fid,
                    )
                    .order_by(SqlFactoryProject.created_at, SqlFactoryProject.id)
                )
                .scalars()
                .all()
            )
            return [_project(r) for r in rows]

    def update_project(self, project_id: str, **fields: Any) -> FactoryProject | None:
        check_fields(fields, PROJECT_FIELDS, "update_project")
        validate_project_fields(fields)
        pid = norm_id(project_id)

        def write(session: Session) -> FactoryProject | None:
            row = session.get(SqlFactoryProject, (current_workspace_id(), pid))
            if row is None:
                return None
            self._apply_project(row, fields, now_s())
            return _project(row)

        return run_write_transaction(self._session_immediate, "update_project", write)

    def delete_project(self, project_id: str) -> bool:
        stmt = delete(SqlFactoryProject).where(
            SqlFactoryProject.workspace_id == current_workspace_id(),
            SqlFactoryProject.id == norm_id(project_id),
        )

        def write(session: Session) -> bool:
            result = session.execute(stmt.execution_options(synchronize_session=False))
            return bool(result.rowcount)  # type: ignore[attr-defined]

        return run_write_transaction(self._session_immediate, "delete_factory_project", write)

    # ── PRs ─────────────────────────────────────────────────────

    def _pr_row(
        self, session: Session, factory_id: str, repo: str, number: int
    ) -> SqlFactoryPr | None:
        return session.execute(
            select(SqlFactoryPr).where(
                SqlFactoryPr.workspace_id == current_workspace_id(),
                SqlFactoryPr.factory_id == factory_id,
                SqlFactoryPr.repo == repo,
                SqlFactoryPr.number == number,
            )
        ).scalar_one_or_none()

    def upsert_pr(
        self, factory_id: str, repo: str, number: int, **fields: Any
    ) -> tuple[FactoryPr, bool]:
        check_fields(fields, PR_FIELDS, "upsert_pr")
        validate_pr_fields(fields)
        fid = norm_id(factory_id)

        def write(session: Session) -> tuple[FactoryPr, bool]:
            now = now_s()
            row = self._pr_row(session, fid, repo, number)
            if row is not None:
                for key, value in _pr_columns(fields).items():
                    setattr(row, key, value)
                row.updated_at = now
                return _pr(row), False
            values = {
                "url": default_pr_url(repo, number),
                "title": "",
                "head_ref": "",
                "base_ref": "",
                "head_sha": "",
                "state": "open",
                "gates": {},
                "source": "tool",
                **fields,
            }
            row = SqlFactoryPr(
                id=new_id(),
                factory_id=fid,
                repo=repo,
                number=number,
                created_at=now,
                updated_at=now,
                **_pr_columns(values),
            )
            session.add(row)
            session.flush()
            return _pr(row), True

        return self._write("upsert_pr", write)

    def get_pr(self, pr_id: str) -> FactoryPr | None:
        with self._session("get_pr") as session:
            row = session.get(SqlFactoryPr, (current_workspace_id(), norm_id(pr_id)))
            return _pr(row) if row is not None else None

    def get_pr_by_number(self, factory_id: str, repo: str, number: int) -> FactoryPr | None:
        fid = norm_id(factory_id)
        with self._session("get_pr_by_number") as session:
            row = self._pr_row(session, fid, repo, number)
            return _pr(row) if row is not None else None

    def list_prs(
        self, factory_id: str, states: Sequence[str] | None = None
    ) -> builtins.list[FactoryPr]:
        fid = norm_id(factory_id)
        stmt = select(SqlFactoryPr).where(
            SqlFactoryPr.workspace_id == current_workspace_id(),
            SqlFactoryPr.factory_id == fid,
        )
        if states is not None:
            stmt = stmt.where(SqlFactoryPr.state.in_([encode_factory_pr_state(s) for s in states]))
        with self._session("list_factory_prs") as session:
            rows = (
                session.execute(stmt.order_by(SqlFactoryPr.created_at, SqlFactoryPr.id))
                .scalars()
                .all()
            )
            return [_pr(r) for r in rows]

    def list_prs_due(
        self,
        now: int,
        limit: int,
        *,
        min_interval_s: int = DEFAULT_PR_POLL_INTERVAL_S,
        terminal_window_s: int = TERMINAL_PR_WINDOW_S,
    ) -> builtins.list[FactoryPr]:
        open_code = encode_factory_pr_state("open")
        ended = func.coalesce(SqlFactoryPr.merged_at, SqlFactoryPr.closed_at, 0)
        stmt = (
            select(SqlFactoryPr)
            .where(
                SqlFactoryPr.workspace_id == current_workspace_id(),
                or_(
                    SqlFactoryPr.last_polled_at.is_(None),
                    SqlFactoryPr.last_polled_at <= now - min_interval_s,
                ),
                or_(
                    SqlFactoryPr.state == open_code,
                    ended >= now - terminal_window_s,
                ),
            )
            .order_by(func.coalesce(SqlFactoryPr.last_polled_at, 0), SqlFactoryPr.id)
            .limit(limit)
        )
        with self._session("list_prs_due") as session:
            return [_pr(r) for r in session.execute(stmt).scalars().all()]

    def update_pr(self, pr_id: str, **fields: Any) -> FactoryPr | None:
        check_fields(fields, PR_FIELDS, "update_pr")
        validate_pr_fields(fields)
        rid = norm_id(pr_id)

        def write(session: Session) -> FactoryPr | None:
            row = session.get(SqlFactoryPr, (current_workspace_id(), rid))
            if row is None:
                return None
            for key, value in _pr_columns(fields).items():
                setattr(row, key, value)
            row.updated_at = now_s()
            return _pr(row)

        return run_write_transaction(self._session_immediate, "update_pr", write)

    # ── Decisions ───────────────────────────────────────────────

    def create_decision(
        self, factory_id: str, *, dedupe_key: str, **fields: Any
    ) -> tuple[FactoryDecision, bool]:
        check_fields(fields, DECISION_FIELDS, "create_decision")
        if "question" not in fields:
            raise TypeError("create_decision() missing required field: question")
        fid = norm_id(factory_id)
        open_code = encode_factory_decision_status("open")
        source = encode_factory_decision_source(fields.get("source", "agent"))

        def write(session: Session) -> tuple[FactoryDecision, bool]:
            ws = current_workspace_id()
            existing = (
                session.execute(
                    select(SqlFactoryDecision)
                    .where(
                        SqlFactoryDecision.workspace_id == ws,
                        SqlFactoryDecision.factory_id == fid,
                        SqlFactoryDecision.dedupe_key == dedupe_key,
                        SqlFactoryDecision.status == open_code,
                    )
                    .order_by(SqlFactoryDecision.created_at.desc(), SqlFactoryDecision.id)
                    .limit(1)
                )
                .scalars()
                .first()
            )
            if existing is not None:
                return _decision(existing), True
            top = session.execute(
                select(func.max(SqlFactoryDecision.number)).where(
                    SqlFactoryDecision.workspace_id == ws,
                    SqlFactoryDecision.factory_id == fid,
                )
            ).scalar()
            now = now_s()
            row = SqlFactoryDecision(
                id=new_id(),
                factory_id=fid,
                number=(top or 0) + 1,
                project_slug=fields.get("project_slug"),
                assignee=fields.get("assignee"),
                thread=fields.get("thread"),
                subject=fields.get("subject"),
                asked_by_session_id=opt_id(fields.get("asked_by_session_id")),
                source=source,
                blocking=1 if fields.get("blocking", True) else 0,
                question=fields["question"],
                context=fields.get("context"),
                options=dump_json(list(fields.get("options") or [])),
                allow_text=1 if fields.get("allow_text", True) else 0,
                status=open_code,
                dedupe_key=dedupe_key,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            return _decision(row), False

        return self._write("open_decision", write, sequence=(_DECISION_LOCK_NS, fid))

    def get_decision(self, decision_id: str) -> FactoryDecision | None:
        with self._session("get_decision") as session:
            row = session.get(SqlFactoryDecision, (current_workspace_id(), norm_id(decision_id)))
            return _decision(row) if row is not None else None

    def get_decision_by_number(self, factory_id: str, number: int) -> FactoryDecision | None:
        fid = norm_id(factory_id)
        with self._session("get_decision_by_number") as session:
            row = session.execute(
                select(SqlFactoryDecision).where(
                    SqlFactoryDecision.workspace_id == current_workspace_id(),
                    SqlFactoryDecision.factory_id == fid,
                    SqlFactoryDecision.number == number,
                )
            ).scalar_one_or_none()
            return _decision(row) if row is not None else None

    def _transition(
        self, operation: str, decision_id: str, *, only_if_open: bool, values: dict[str, Any]
    ) -> FactoryDecision:
        """Compare-and-set a decision's columns; raise when the CAS loses."""
        did = norm_id(decision_id)
        open_code = encode_factory_decision_status("open")

        def write(session: Session) -> FactoryDecision:
            ws = current_workspace_id()
            conditions = [SqlFactoryDecision.workspace_id == ws, SqlFactoryDecision.id == did]
            if only_if_open:
                conditions.append(SqlFactoryDecision.status == open_code)
            result = session.execute(
                update(SqlFactoryDecision)
                .where(*conditions)
                .values(**values, updated_at=now_s())
                .execution_options(synchronize_session=False)
            )
            row = session.get(SqlFactoryDecision, (ws, did), populate_existing=True)
            if row is None:
                raise DecisionNotFound(decision_id)
            if result.rowcount != 1:  # type: ignore[attr-defined]
                raise DecisionNotOpen(_decision(row))
            return _decision(row)

        return run_write_transaction(self._session_immediate, operation, write)

    def answer_decision(
        self, decision_id: str, *, option_id: str | None, text: str | None, by: str
    ) -> FactoryDecision:
        values: dict[str, Any] = {
            "status": encode_factory_decision_status("answered"),
            "answer_option_id": option_id,
            "answer_text": text,
            "answered_by": by,
            "answered_at": now_s(),
        }
        return self._transition("answer_decision", decision_id, only_if_open=True, values=values)

    def set_decision_status(
        self, decision_id: str, status: str, *, only_if_open: bool = True
    ) -> FactoryDecision:
        values = {"status": encode_factory_decision_status(status)}
        return self._transition(
            "set_decision_status", decision_id, only_if_open=only_if_open, values=values
        )

    def update_decision(self, decision_id: str, **fields: Any) -> FactoryDecision | None:
        check_fields(fields, DECISION_UPDATE_FIELDS, "update_decision")
        did = norm_id(decision_id)

        def write(session: Session) -> FactoryDecision | None:
            row = session.get(SqlFactoryDecision, (current_workspace_id(), did))
            if row is None:
                return None
            for key, value in fields.items():
                setattr(row, key, dump_json(list(value or [])) if key == "options" else value)
            row.updated_at = now_s()
            return _decision(row)

        return run_write_transaction(self._session_immediate, "update_decision", write)

    def route_decision(
        self,
        decision_id: str,
        *,
        claimable_by: str | None = None,
        assignee_was: str | None = None,
        **fields: Any,
    ) -> FactoryDecision:
        check_fields(fields, DECISION_UPDATE_FIELDS, "route_decision")
        did = norm_id(decision_id)
        open_code = encode_factory_decision_status("open")
        values = {k: dump_json(list(v or [])) if k == "options" else v for k, v in fields.items()}

        def write(session: Session) -> FactoryDecision:
            ws = current_workspace_id()
            conditions = [
                SqlFactoryDecision.workspace_id == ws,
                SqlFactoryDecision.id == did,
                SqlFactoryDecision.status == open_code,
            ]
            if claimable_by is not None:
                conditions.append(
                    or_(
                        SqlFactoryDecision.claimed_by.is_(None),
                        SqlFactoryDecision.claimed_by == claimable_by,
                    )
                )
            if assignee_was is not None:
                conditions.append(SqlFactoryDecision.assignee == assignee_was)
            result = session.execute(
                update(SqlFactoryDecision)
                .where(*conditions)
                .values(**values, updated_at=now_s())
                .execution_options(synchronize_session=False)
            )
            row = session.get(SqlFactoryDecision, (ws, did), populate_existing=True)
            if row is None:
                raise DecisionNotFound(decision_id)
            if result.rowcount != 1:  # type: ignore[attr-defined]
                raise DecisionNotOpen(_decision(row))
            return _decision(row)

        return run_write_transaction(self._session_immediate, "route_decision", write)

    def list_decisions(
        self,
        factory_ids: Sequence[str] | None = None,
        status: str | Sequence[str] | None = None,
        factory_id: str | None = None,
        limit: int = 100,
        *,
        newest_first: bool = True,
    ) -> builtins.list[FactoryDecision]:
        stmt = select(SqlFactoryDecision).where(
            SqlFactoryDecision.workspace_id == current_workspace_id()
        )
        if factory_ids is not None:
            stmt = stmt.where(SqlFactoryDecision.factory_id.in_([norm_id(f) for f in factory_ids]))
        if factory_id is not None:
            stmt = stmt.where(SqlFactoryDecision.factory_id == norm_id(factory_id))
        statuses = as_list(status)
        if statuses is not None:
            stmt = stmt.where(
                SqlFactoryDecision.status.in_(
                    [encode_factory_decision_status(s) for s in statuses]
                )
            )
        columns = (SqlFactoryDecision.created_at, SqlFactoryDecision.number, SqlFactoryDecision.id)
        order = [c.desc() for c in columns] if newest_first else list(columns)
        with self._session("list_decisions") as session:
            rows = session.execute(stmt.order_by(*order).limit(limit)).scalars().all()
            return [_decision(r) for r in rows]

    def count_open(self, factory_ids: Sequence[str]) -> dict[str, int]:
        ids = [norm_id(f) for f in factory_ids]
        counts = dict.fromkeys(ids, 0)
        if not ids:
            return counts
        with self._session("count_open_decisions") as session:
            rows = session.execute(
                select(SqlFactoryDecision.factory_id, func.count())
                .where(
                    SqlFactoryDecision.workspace_id == current_workspace_id(),
                    SqlFactoryDecision.factory_id.in_(ids),
                    SqlFactoryDecision.status == encode_factory_decision_status("open"),
                )
                .group_by(SqlFactoryDecision.factory_id)
            ).all()
        for fid, count in rows:
            counts[fid] = int(count)
        return counts

    # ── Events ──────────────────────────────────────────────────

    def append_event(self, draft: EventDraft) -> FactoryEvent:
        severity = encode_factory_event_severity(draft.severity)
        ts = now_ms() if draft.ts is None else draft.ts

        def write(session: Session) -> FactoryEvent:
            row = SqlFactoryEvent(
                id=new_event_id(ts),
                factory_id=norm_id(draft.factory_id),
                ts=ts,
                kind=draft.kind,
                severity=severity,
                project_slug=draft.project_slug,
                from_id=opt_id(draft.from_id),
                to_id=opt_id(draft.to_id),
                subject=draft.subject,
                summary=draft.summary,
                data=dump_json(draft.data),
            )
            session.add(row)
            session.flush()
            return _event(row)

        return run_write_transaction(self._session_immediate, "append_event", write)

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
        stmt = select(SqlFactoryEvent).where(
            SqlFactoryEvent.workspace_id == current_workspace_id(),
            SqlFactoryEvent.factory_id == norm_id(factory_id),
        )
        if before_ms is not None:
            if before_id is None:
                stmt = stmt.where(SqlFactoryEvent.ts < before_ms)
            else:
                stmt = stmt.where(
                    or_(
                        SqlFactoryEvent.ts < before_ms,
                        and_(
                            SqlFactoryEvent.ts == before_ms,
                            SqlFactoryEvent.id < norm_id(before_id),
                        ),
                    )
                )
        if kinds is not None:
            stmt = stmt.where(_kind_filter(SqlFactoryEvent.kind, kinds))
        if project is not None:
            stmt = stmt.where(SqlFactoryEvent.project_slug == project)
        if member_ids is not None:
            ids = [norm_id(m) for m in member_ids]
            stmt = stmt.where(
                or_(SqlFactoryEvent.from_id.in_(ids), SqlFactoryEvent.to_id.in_(ids))
            )
        stmt = stmt.order_by(SqlFactoryEvent.ts.desc(), SqlFactoryEvent.id.desc()).limit(limit)
        with self._session("list_factory_events") as session:
            return [_event(r) for r in session.execute(stmt).scalars().all()]

    def find_events(
        self,
        factory_id: str | None,
        kind: str | Sequence[str],
        since_ms: int,
        subject: str | None = None,
        *,
        limit: int = 1000,
    ) -> builtins.list[FactoryEvent]:
        stmt = select(SqlFactoryEvent).where(
            SqlFactoryEvent.workspace_id == current_workspace_id(),
            _kind_filter(SqlFactoryEvent.kind, as_list(kind) or []),
            SqlFactoryEvent.ts >= since_ms,
        )
        if factory_id is not None:
            stmt = stmt.where(SqlFactoryEvent.factory_id == norm_id(factory_id))
        if subject is not None:
            stmt = stmt.where(SqlFactoryEvent.subject == subject)
        stmt = stmt.order_by(SqlFactoryEvent.ts, SqlFactoryEvent.id).limit(limit)
        with self._session("find_factory_events") as session:
            return [_event(r) for r in session.execute(stmt).scalars().all()]

    def prune_events(self, older_than_ms: int, kinds: Sequence[str] | None = None) -> int:
        stmt = delete(SqlFactoryEvent).where(
            SqlFactoryEvent.workspace_id == current_workspace_id(),
            SqlFactoryEvent.ts < older_than_ms,
        )
        if kinds is not None:
            stmt = stmt.where(_kind_filter(SqlFactoryEvent.kind, kinds))

        def write(session: Session) -> int:
            result = session.execute(stmt.execution_options(synchronize_session=False))
            return int(result.rowcount or 0)  # type: ignore[attr-defined]

        return run_write_transaction(self._session_immediate, "prune_factory_events", write)

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
        status_code = encode_factory_demo_status(status)

        def write(session: Session) -> FactoryDemo:
            row = SqlFactoryDemo(
                id=norm_id(demo_id) if demo_id else new_id(),
                factory_id=norm_id(factory_id),
                project_slug=project_slug,
                pr_id=opt_id(pr_id),
                repo=repo,
                pr_number=pr_number,
                head_sha=head_sha,
                session_id=norm_id(session_id),
                status=status_code,
                title=title,
                summary=summary,
                preview_url=preview_url,
                files=dump_json([dict(f) for f in files or []]),
                manifest=dump_json(dict(manifest)) if manifest is not None else None,
                created_by_session=norm_id(created_by_session),
                created_at=now_s(),
            )
            session.add(row)
            session.flush()
            return _demo(row)

        return run_write_transaction(self._session_immediate, "create_demo", write)

    def get_demo(self, demo_id: str) -> FactoryDemo | None:
        with self._session("get_demo") as session:
            row = session.get(SqlFactoryDemo, (current_workspace_id(), norm_id(demo_id)))
            return _demo(row) if row is not None else None

    def list_demos(
        self,
        factory_id: str,
        pr_number: int | None = None,
        *,
        repo: str | None = None,
        project_slug: str | None = None,
        limit: int = 100,
    ) -> builtins.list[FactoryDemo]:
        stmt = select(SqlFactoryDemo).where(
            SqlFactoryDemo.workspace_id == current_workspace_id(),
            SqlFactoryDemo.factory_id == norm_id(factory_id),
        )
        if pr_number is not None:
            stmt = stmt.where(SqlFactoryDemo.pr_number == pr_number)
        if repo is not None:
            stmt = stmt.where(SqlFactoryDemo.repo == repo)
        if project_slug is not None:
            stmt = stmt.where(SqlFactoryDemo.project_slug == project_slug)
        stmt = stmt.order_by(SqlFactoryDemo.created_at.desc(), SqlFactoryDemo.id.desc()).limit(
            limit
        )
        with self._session("list_demos") as session:
            return [_demo(r) for r in session.execute(stmt).scalars().all()]

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
        fid = norm_id(factory_id)

        def write(session: Session) -> tuple[FactoryPerson, bool]:
            row = session.get(SqlFactoryPerson, (current_workspace_id(), fid, user_id))
            if row is not None:
                row.role = encode_factory_person_role(role)
                if notify is not None:
                    row.notify = dump_json(dict(notify))
                return _person(row), False
            row = SqlFactoryPerson(
                factory_id=fid,
                user_id=user_id,
                role=encode_factory_person_role(role),
                is_creator=1 if is_creator else 0,
                invited_by=invited_by,
                invited_at=now_s() if invited_at is None else invited_at,
                notify=dump_json(dict(notify)) if notify is not None else None,
            )
            session.add(row)
            session.flush()
            return _person(row), True

        return self._write("upsert_factory_person", write)

    def get_person(self, factory_id: str, user_id: str) -> FactoryPerson | None:
        with self._session("get_factory_person") as session:
            row = session.get(
                SqlFactoryPerson, (current_workspace_id(), norm_id(factory_id), user_id)
            )
            return _person(row) if row is not None else None

    def list_people(self, factory_id: str) -> builtins.list[FactoryPerson]:
        stmt = (
            select(SqlFactoryPerson)
            .where(
                SqlFactoryPerson.workspace_id == current_workspace_id(),
                SqlFactoryPerson.factory_id == norm_id(factory_id),
            )
            .order_by(SqlFactoryPerson.invited_at, SqlFactoryPerson.user_id)
        )
        with self._session("list_factory_people") as session:
            return [_person(r) for r in session.execute(stmt).scalars().all()]

    def list_person_factories(self, user_id: str) -> builtins.list[FactoryPerson]:
        stmt = (
            select(SqlFactoryPerson)
            .where(
                SqlFactoryPerson.workspace_id == current_workspace_id(),
                SqlFactoryPerson.user_id == user_id,
            )
            .order_by(SqlFactoryPerson.invited_at, SqlFactoryPerson.factory_id)
        )
        with self._session("list_factories_of_person") as session:
            return [_person(r) for r in session.execute(stmt).scalars().all()]

    def update_person(self, factory_id: str, user_id: str, **fields: Any) -> FactoryPerson | None:
        check_fields(fields, PERSON_UPDATE_FIELDS, "update_person")
        validate_enums("person", fields)
        key = (current_workspace_id(), norm_id(factory_id), user_id)
        values = _columns("person", fields)

        def write(session: Session) -> FactoryPerson | None:
            row = session.get(SqlFactoryPerson, key)
            if row is None:
                return None
            for name, value in values.items():
                setattr(row, name, value)
            return _person(row)

        return run_write_transaction(self._session_immediate, "update_factory_person", write)

    def delete_person(self, factory_id: str, user_id: str) -> bool:
        stmt = delete(SqlFactoryPerson).where(
            SqlFactoryPerson.workspace_id == current_workspace_id(),
            SqlFactoryPerson.factory_id == norm_id(factory_id),
            SqlFactoryPerson.user_id == user_id,
        )

        def write(session: Session) -> bool:
            result = session.execute(stmt.execution_options(synchronize_session=False))
            return bool(result.rowcount)  # type: ignore[attr-defined]

        return run_write_transaction(self._session_immediate, "remove_factory_person", write)

    # ── Room ────────────────────────────────────────────────────

    @staticmethod
    def _next_seq(session: Session, factory_id: str) -> int:
        top = session.execute(
            select(func.max(SqlFactoryChatMessage.seq)).where(
                SqlFactoryChatMessage.workspace_id == current_workspace_id(),
                SqlFactoryChatMessage.factory_id == factory_id,
            )
        ).scalar()
        return int(top or 0) + 1

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
        kind_code = encode_factory_chat_author_kind(author_kind)
        fid = norm_id(factory_id)
        row_id = norm_id(message_id) if message_id else new_id()

        def write(session: Session) -> FactoryChatMessage:
            if message_id:
                existing = session.get(SqlFactoryChatMessage, (current_workspace_id(), row_id))
                if existing is not None:
                    return _chat(existing)
            row = SqlFactoryChatMessage(
                id=row_id,
                factory_id=fid,
                thread=thread,
                seq=self._next_seq(session, fid),
                author_kind=kind_code,
                author=author,
                author_role=author_role,
                body=body,
                mentions=dump_json(list(mentions or [])),
                refs=dump_json(list(refs or [])),
                reply_to=opt_id(reply_to),
                card=dump_json(dict(card)) if card is not None else None,
                routed=dump_json([dict(r) for r in routed or []]),
                auto_reply=1 if auto_reply else 0,
                created_at=now_s() if created_at is None else created_at,
            )
            session.add(row)
            session.flush()
            return _chat(row)

        return self._write("post_factory_chat", write, sequence=(_CHAT_LOCK_NS, fid))

    def next_chat_seq(self, factory_id: str) -> int:
        with self._session("next_factory_chat_seq") as session:
            return self._next_seq(session, norm_id(factory_id))

    def get_chat_message(self, message_id: str) -> FactoryChatMessage | None:
        with self._session("get_factory_chat_message") as session:
            row = session.get(SqlFactoryChatMessage, (current_workspace_id(), norm_id(message_id)))
            return _chat(row) if row is not None else None

    def list_chat(
        self,
        factory_id: str,
        thread: str | None = None,
        *,
        before_seq: int | None = None,
        after_seq: int | None = None,
        limit: int = 50,
    ) -> builtins.list[FactoryChatMessage]:
        model = SqlFactoryChatMessage
        stmt = select(model).where(
            model.workspace_id == current_workspace_id(),
            model.factory_id == norm_id(factory_id),
        )
        if thread is not None:
            stmt = stmt.where(model.thread == thread)
        if before_seq is not None:
            stmt = stmt.where(model.seq < before_seq)
        if after_seq is not None:
            stmt = stmt.where(model.seq > after_seq).order_by(model.seq)
        else:
            stmt = stmt.order_by(model.seq.desc())
        with self._session("list_factory_chat") as session:
            rows = [_chat(r) for r in session.execute(stmt.limit(limit)).scalars().all()]
        return rows if after_seq is not None else rows[::-1]

    def update_chat_message(self, message_id: str, **fields: Any) -> FactoryChatMessage | None:
        check_fields(fields, CHAT_UPDATE_FIELDS, "update_chat_message")
        key = (current_workspace_id(), norm_id(message_id))
        values = _columns("chat", fields)

        def write(session: Session) -> FactoryChatMessage | None:
            row = session.get(SqlFactoryChatMessage, key, with_for_update=True)
            if row is None:
                return None
            for name, value in values.items():
                setattr(row, name, value)
            return _chat(row)

        return run_write_transaction(self._session_immediate, "route_factory_chat", write)

    def chat_thread_stats(self, factory_id: str) -> builtins.list[ChatThreadStats]:
        model = SqlFactoryChatMessage
        ws, fid = current_workspace_id(), norm_id(factory_id)
        last = (
            select(model.thread, func.max(model.seq).label("last_seq"), func.count().label("n"))
            .where(model.workspace_id == ws, model.factory_id == fid)
            .group_by(model.thread)
            .subquery()
        )
        stmt = (
            select(last.c.thread, last.c.last_seq, last.c.n, model.created_at)
            .join(
                model,
                and_(
                    model.workspace_id == ws,
                    model.factory_id == fid,
                    model.seq == last.c.last_seq,
                ),
            )
            .order_by(last.c.last_seq.desc())
        )
        with self._session("list_factory_chat_threads") as session:
            rows = session.execute(stmt).all()
        return [
            ChatThreadStats(thread=t, last_seq=int(seq), last_at=int(at), messages=int(n))
            for t, seq, n, at in rows
        ]

    def upsert_chat_read(
        self, factory_id: str, user_id: str, thread: str, last_seq: int
    ) -> FactoryChatRead:
        key = (current_workspace_id(), norm_id(factory_id), user_id, thread)
        model = SqlFactoryChatRead

        def write(session: Session) -> FactoryChatRead:
            now = now_s()
            session.execute(
                update(model)
                .where(
                    model.workspace_id == key[0],
                    model.factory_id == key[1],
                    model.user_id == user_id,
                    model.thread == thread,
                    model.last_seq < last_seq,
                )
                .values(last_seq=last_seq, updated_at=now)
                .execution_options(synchronize_session=False)
            )
            row = session.get(model, key, populate_existing=True)
            if row is None:
                row = model(
                    factory_id=key[1],
                    user_id=user_id,
                    thread=thread,
                    last_seq=last_seq,
                    updated_at=now,
                )
                session.add(row)
                session.flush()
            return _chat_read(row)

        return self._write("mark_factory_chat_read", write)

    def list_chat_reads(self, factory_id: str, user_id: str) -> builtins.list[FactoryChatRead]:
        stmt = (
            select(SqlFactoryChatRead)
            .where(
                SqlFactoryChatRead.workspace_id == current_workspace_id(),
                SqlFactoryChatRead.factory_id == norm_id(factory_id),
                SqlFactoryChatRead.user_id == user_id,
            )
            .order_by(SqlFactoryChatRead.thread)
        )
        with self._session("list_factory_chat_reads") as session:
            return [_chat_read(r) for r in session.execute(stmt).scalars().all()]

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
        values = {"approval": "operator", "ops": [], **fields}
        values.update(kind=kind, title=title, author_kind=author_kind, author=author)
        if values.get("rationale") is None:
            values["rationale"] = ""
        validate_enums("proposal", values)
        columns = _columns("proposal", values)
        fid = norm_id(factory_id)
        model = SqlFactoryProposal

        def write(session: Session) -> tuple[FactoryProposal, bool]:
            ws = current_workspace_id()
            if dedupe_key is not None:
                existing = session.execute(
                    select(model).where(
                        model.workspace_id == ws,
                        model.factory_id == fid,
                        model.dedupe_key == dedupe_key,
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return _proposal(existing), True
            top = session.execute(
                select(func.max(model.number)).where(
                    model.workspace_id == ws, model.factory_id == fid
                )
            ).scalar()
            now = now_s()
            row = model(
                id=new_id(),
                factory_id=fid,
                number=(top or 0) + 1,
                dedupe_key=dedupe_key,
                created_at=now,
                updated_at=now,
                **{"status": encode_factory_proposal_status("pending"), **columns},
            )
            session.add(row)
            session.flush()
            return _proposal(row), False

        return self._write("file_factory_proposal", write, sequence=(_PROPOSAL_LOCK_NS, fid))

    def get_proposal(self, proposal_id: str) -> FactoryProposal | None:
        with self._session("get_factory_proposal") as session:
            row = session.get(SqlFactoryProposal, (current_workspace_id(), norm_id(proposal_id)))
            return _proposal(row) if row is not None else None

    def get_proposal_by_number(self, factory_id: str, number: int) -> FactoryProposal | None:
        with self._session("get_factory_proposal_by_number") as session:
            row = session.execute(
                select(SqlFactoryProposal).where(
                    SqlFactoryProposal.workspace_id == current_workspace_id(),
                    SqlFactoryProposal.factory_id == norm_id(factory_id),
                    SqlFactoryProposal.number == number,
                )
            ).scalar_one_or_none()
            return _proposal(row) if row is not None else None

    def list_proposals(
        self,
        factory_id: str,
        *,
        status: str | Sequence[str] | None = None,
        kind: str | Sequence[str] | None = None,
        limit: int = 100,
        newest_first: bool = True,
    ) -> builtins.list[FactoryProposal]:
        model = SqlFactoryProposal
        stmt = select(model).where(
            model.workspace_id == current_workspace_id(),
            model.factory_id == norm_id(factory_id),
        )
        statuses = as_list(status)
        if statuses is not None:
            stmt = stmt.where(
                model.status.in_([encode_factory_proposal_status(s) for s in statuses])
            )
        kinds = as_list(kind)
        if kinds is not None:
            stmt = stmt.where(model.kind.in_([encode_factory_proposal_kind(k) for k in kinds]))
        order = model.number.desc() if newest_first else model.number
        with self._session("list_factory_proposals") as session:
            rows = session.execute(stmt.order_by(order).limit(limit)).scalars().all()
            return [_proposal(r) for r in rows]

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
        key = (current_workspace_id(), norm_id(proposal_id))
        model = SqlFactoryProposal
        values = _columns("proposal", fields)

        def write(session: Session) -> FactoryProposal | None:
            conditions = [model.workspace_id == key[0], model.id == key[1]]
            if expected is not None:
                codes = [encode_factory_proposal_status(s) for s in expected]
                conditions.append(model.status.in_(codes))
            result = session.execute(
                update(model)
                .where(*conditions)
                .values(**values, updated_at=now_s())
                .execution_options(synchronize_session=False)
            )
            row = session.get(model, key, populate_existing=True)
            if row is None:
                return None
            if result.rowcount != 1:  # type: ignore[attr-defined]
                raise ProposalStateConflict(_proposal(row))
            return _proposal(row)

        return run_write_transaction(self._session_immediate, "update_factory_proposal", write)

    def next_proposal_number(self, factory_id: str) -> int:
        with self._session("next_factory_proposal_number") as session:
            top = session.execute(
                select(func.max(SqlFactoryProposal.number)).where(
                    SqlFactoryProposal.workspace_id == current_workspace_id(),
                    SqlFactoryProposal.factory_id == norm_id(factory_id),
                )
            ).scalar()
            return (top or 0) + 1

    # ── Work graph ──────────────────────────────────────────────

    @staticmethod
    def _task_row(
        session: Session, factory_id: str, project_slug: str, key: str
    ) -> SqlFactoryTask | None:
        return session.execute(
            select(SqlFactoryTask).where(
                SqlFactoryTask.workspace_id == current_workspace_id(),
                SqlFactoryTask.factory_id == factory_id,
                SqlFactoryTask.project_slug == project_slug,
                SqlFactoryTask.key == key,
            )
        ).scalar_one_or_none()

    def upsert_task(
        self, factory_id: str, project_slug: str, key: str, **fields: Any
    ) -> tuple[FactoryTask, bool]:
        check_fields(fields, TASK_FIELDS, "upsert_task")
        validate_enums("task", fields)
        fid = norm_id(factory_id)

        def write(session: Session) -> tuple[FactoryTask, bool]:
            now = now_s()
            row = self._task_row(session, fid, project_slug, key)
            if row is not None:
                for name, value in _columns("task", fields).items():
                    setattr(row, name, value)
                row.updated_at = now
                return _task(row), False
            top = session.execute(
                select(func.max(SqlFactoryTask.number)).where(
                    SqlFactoryTask.workspace_id == current_workspace_id(),
                    SqlFactoryTask.factory_id == fid,
                )
            ).scalar()
            values = {
                "title": key,
                "kind": "implement",
                "state": "planned",
                "card": {},
                "deps": [],
                "scope": [],
                "priority": 2,
                "attempts": 0,
                "created_by": "server",
                "created_via": "server",
                **fields,
            }
            row = SqlFactoryTask(
                id=new_id(),
                factory_id=fid,
                number=(top or 0) + 1,
                project_slug=project_slug,
                key=key,
                created_at=now,
                updated_at=now,
                **_columns("task", values),
            )
            session.add(row)
            session.flush()
            return _task(row), True

        return self._write("upsert_factory_task", write, sequence=(_TASK_LOCK_NS, fid))

    def get_task(self, task_id: str) -> FactoryTask | None:
        with self._session("get_factory_task") as session:
            row = session.get(SqlFactoryTask, (current_workspace_id(), norm_id(task_id)))
            return _task(row) if row is not None else None

    def get_task_by_key(self, factory_id: str, project_slug: str, key: str) -> FactoryTask | None:
        with self._session("get_factory_task_by_key") as session:
            row = self._task_row(session, norm_id(factory_id), project_slug, key)
            return _task(row) if row is not None else None

    def get_task_by_number(self, factory_id: str, number: int) -> FactoryTask | None:
        with self._session("get_factory_task_by_number") as session:
            row = session.execute(
                select(SqlFactoryTask).where(
                    SqlFactoryTask.workspace_id == current_workspace_id(),
                    SqlFactoryTask.factory_id == norm_id(factory_id),
                    SqlFactoryTask.number == number,
                )
            ).scalar_one_or_none()
            return _task(row) if row is not None else None

    def list_tasks(
        self,
        factory_id: str,
        *,
        project_slug: str | None = None,
        states: Sequence[str] | None = None,
    ) -> builtins.list[FactoryTask]:
        model = SqlFactoryTask
        stmt = select(model).where(
            model.workspace_id == current_workspace_id(),
            model.factory_id == norm_id(factory_id),
        )
        if project_slug is not None:
            stmt = stmt.where(model.project_slug == project_slug)
        if states is not None:
            stmt = stmt.where(model.state.in_([encode_factory_task_state(s) for s in states]))
        with self._session("list_factory_tasks") as session:
            rows = session.execute(stmt.order_by(model.number)).scalars().all()
            return [_task(r) for r in rows]

    def update_task(self, task_id: str, **fields: Any) -> FactoryTask | None:
        check_fields(fields, TASK_FIELDS, "update_task")
        validate_enums("task", fields)
        key = (current_workspace_id(), norm_id(task_id))
        values = _columns("task", fields)

        def write(session: Session) -> FactoryTask | None:
            row = session.get(SqlFactoryTask, key)
            if row is None:
                return None
            for name, value in values.items():
                setattr(row, name, value)
            row.updated_at = now_s()
            return _task(row)

        return run_write_transaction(self._session_immediate, "update_factory_task", write)

    def increment_task_attempts(self, task_id: str, by: int = 1) -> FactoryTask | None:
        key = (current_workspace_id(), norm_id(task_id))
        model = SqlFactoryTask

        def write(session: Session) -> FactoryTask | None:
            session.execute(
                update(model)
                .where(model.workspace_id == key[0], model.id == key[1])
                .values(attempts=model.attempts + by, updated_at=now_s())
                .execution_options(synchronize_session=False)
            )
            row = session.get(model, key, populate_existing=True)
            return _task(row) if row is not None else None

        return run_write_transaction(self._session_immediate, "count_factory_task_attempt", write)

    def next_task_number(self, factory_id: str) -> int:
        with self._session("next_factory_task_number") as session:
            top = session.execute(
                select(func.max(SqlFactoryTask.number)).where(
                    SqlFactoryTask.workspace_id == current_workspace_id(),
                    SqlFactoryTask.factory_id == norm_id(factory_id),
                )
            ).scalar()
            return (top or 0) + 1

    # ── Loops ───────────────────────────────────────────────────

    @staticmethod
    def _loop_row(
        session: Session, factory_id: str, kind: str, subject: str
    ) -> SqlFactoryLoop | None:
        return session.execute(
            select(SqlFactoryLoop).where(
                SqlFactoryLoop.workspace_id == current_workspace_id(),
                SqlFactoryLoop.factory_id == factory_id,
                SqlFactoryLoop.kind == encode_factory_loop_kind(kind),
                SqlFactoryLoop.subject == subject,
            )
        ).scalar_one_or_none()

    def upsert_loop(
        self, factory_id: str, kind: str, subject: str, **fields: Any
    ) -> tuple[FactoryLoop, bool]:
        check_fields(fields, LOOP_FIELDS, "upsert_loop")
        validate_enums("loop", {**fields, "kind": kind})
        fid = norm_id(factory_id)

        def write(session: Session) -> tuple[FactoryLoop, bool]:
            now = now_s()
            row = self._loop_row(session, fid, kind, subject)
            if row is not None:
                for name, value in _columns("loop", fields).items():
                    setattr(row, name, value)
                row.updated_at = now
                return _loop(row), False
            values = {
                "state": "running",
                "iteration": 0,
                "spec": {},
                "spent": {},
                "started_at": now,
                **fields,
                "kind": kind,
            }
            row = SqlFactoryLoop(
                id=new_id(),
                factory_id=fid,
                subject=subject,
                updated_at=now,
                **_columns("loop", values),
            )
            session.add(row)
            session.flush()
            return _loop(row), True

        return self._write("upsert_factory_loop", write)

    def get_loop(self, loop_id: str) -> FactoryLoop | None:
        with self._session("get_factory_loop") as session:
            row = session.get(SqlFactoryLoop, (current_workspace_id(), norm_id(loop_id)))
            return _loop(row) if row is not None else None

    def get_loop_by_subject(self, factory_id: str, kind: str, subject: str) -> FactoryLoop | None:
        with self._session("get_factory_loop_by_subject") as session:
            row = self._loop_row(session, norm_id(factory_id), kind, subject)
            return _loop(row) if row is not None else None

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
        model = SqlFactoryLoop
        stmt = select(model).where(
            model.workspace_id == current_workspace_id(),
            model.factory_id == norm_id(factory_id),
        )
        if states is not None:
            stmt = stmt.where(model.state.in_([encode_factory_loop_state(s) for s in states]))
        if kinds is not None:
            stmt = stmt.where(model.kind.in_([encode_factory_loop_kind(k) for k in kinds]))
        if project_slug is not None:
            stmt = stmt.where(model.project_slug == project_slug)
        if task_id is not None:
            stmt = stmt.where(model.task_id == norm_id(task_id))
        if pr_id is not None:
            stmt = stmt.where(model.pr_id == norm_id(pr_id))
        with self._session("list_factory_loops") as session:
            rows = session.execute(stmt.order_by(model.started_at, model.id)).scalars().all()
            return [_loop(r) for r in rows]

    def update_loop(self, loop_id: str, **fields: Any) -> FactoryLoop | None:
        check_fields(fields, LOOP_FIELDS, "update_loop")
        validate_enums("loop", fields)
        key = (current_workspace_id(), norm_id(loop_id))
        values = _columns("loop", fields)

        def write(session: Session) -> FactoryLoop | None:
            row = session.get(SqlFactoryLoop, key)
            if row is None:
                return None
            for name, value in values.items():
                setattr(row, name, value)
            row.updated_at = now_s()
            return _loop(row)

        return run_write_transaction(self._session_immediate, "update_factory_loop", write)

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
        fid = norm_id(factory_id)
        model = SqlFactoryIssue
        reopenable = {encode_factory_issue_status(s) for s in ISSUE_REOPENABLE}

        def write(session: Session) -> tuple[FactoryIssue, bool]:
            ws, now = current_workspace_id(), now_s()
            changes = {**fields, "rule": rule, "target": target, "summary": summary}
            if dedupe_key is not None:
                row = session.execute(
                    select(model)
                    .where(
                        model.workspace_id == ws,
                        model.factory_id == fid,
                        model.dedupe_key == dedupe_key,
                    )
                    .with_for_update()
                ).scalar_one_or_none()
                if row is not None:
                    for name, value in _columns("issue", changes).items():
                        setattr(row, name, value)
                    row.count = row.count + 1
                    row.last_seen = now
                    if reopen and row.status in reopenable and "status" not in fields:
                        row.status = encode_factory_issue_status("open")
                        row.resolved_at = None
                        row.resolved_by = None
                    return _issue(row), False
            class_code = encode_factory_issue_class(class_)
            top = session.execute(
                select(func.max(model.number)).where(
                    model.workspace_id == ws, model.factory_id == fid, model.class_ == class_code
                )
            ).scalar()
            values = {"severity": "notice", "source": "detector", "status": "open", **changes}
            row = model(
                id=new_id(),
                factory_id=fid,
                class_=class_code,
                number=(top or 0) + 1,
                dedupe_key=dedupe_key,
                count=1,
                first_seen=now,
                last_seen=now,
                **_columns("issue", values),
            )
            session.add(row)
            session.flush()
            return _issue(row), True

        return self._write("raise_factory_issue", write, sequence=(_ISSUE_LOCK_NS, fid))

    def get_issue(self, issue_id: str) -> FactoryIssue | None:
        with self._session("get_factory_issue") as session:
            row = session.get(SqlFactoryIssue, (current_workspace_id(), norm_id(issue_id)))
            return _issue(row) if row is not None else None

    def get_issue_by_number(
        self, factory_id: str, class_: str, number: int
    ) -> FactoryIssue | None:
        with self._session("get_factory_issue_by_number") as session:
            row = session.execute(
                select(SqlFactoryIssue).where(
                    SqlFactoryIssue.workspace_id == current_workspace_id(),
                    SqlFactoryIssue.factory_id == norm_id(factory_id),
                    SqlFactoryIssue.class_ == encode_factory_issue_class(class_),
                    SqlFactoryIssue.number == number,
                )
            ).scalar_one_or_none()
            return _issue(row) if row is not None else None

    def get_issue_by_dedupe(self, factory_id: str, dedupe_key: str) -> FactoryIssue | None:
        with self._session("get_factory_issue_by_dedupe") as session:
            row = session.execute(
                select(SqlFactoryIssue).where(
                    SqlFactoryIssue.workspace_id == current_workspace_id(),
                    SqlFactoryIssue.factory_id == norm_id(factory_id),
                    SqlFactoryIssue.dedupe_key == dedupe_key,
                )
            ).scalar_one_or_none()
            return _issue(row) if row is not None else None

    def list_issues(
        self,
        factory_id: str,
        *,
        class_: str | Sequence[str] | None = None,
        statuses: Sequence[str] | None = None,
        project_slug: str | None = None,
        limit: int = 200,
    ) -> builtins.list[FactoryIssue]:
        model = SqlFactoryIssue
        stmt = select(model).where(
            model.workspace_id == current_workspace_id(),
            model.factory_id == norm_id(factory_id),
        )
        classes = as_list(class_)
        if classes is not None:
            stmt = stmt.where(model.class_.in_([encode_factory_issue_class(c) for c in classes]))
        if statuses is not None:
            stmt = stmt.where(model.status.in_([encode_factory_issue_status(s) for s in statuses]))
        if project_slug is not None:
            stmt = stmt.where(model.project_slug == project_slug)
        stmt = stmt.order_by(model.first_seen.desc(), model.number.desc(), model.id.desc())
        stmt = stmt.limit(limit)
        with self._session("list_factory_issues") as session:
            return [_issue(r) for r in session.execute(stmt).scalars().all()]

    def update_issue(self, issue_id: str, **fields: Any) -> FactoryIssue | None:
        check_fields(fields, ISSUE_FIELDS, "update_issue")
        validate_enums("issue", fields)
        key = (current_workspace_id(), norm_id(issue_id))
        values = _columns("issue", fields)

        def write(session: Session) -> FactoryIssue | None:
            row = session.get(SqlFactoryIssue, key)
            if row is None:
                return None
            for name, value in values.items():
                setattr(row, name, value)
            return _issue(row)

        return run_write_transaction(self._session_immediate, "update_factory_issue", write)

    def next_issue_number(self, factory_id: str, class_: str) -> int:
        with self._session("next_factory_issue_number") as session:
            top = session.execute(
                select(func.max(SqlFactoryIssue.number)).where(
                    SqlFactoryIssue.workspace_id == current_workspace_id(),
                    SqlFactoryIssue.factory_id == norm_id(factory_id),
                    SqlFactoryIssue.class_ == encode_factory_issue_class(class_),
                )
            ).scalar()
            return (top or 0) + 1

    # ── Spans ───────────────────────────────────────────────────

    @staticmethod
    def _span_by_dedupe(
        session: Session, factory_id: str, dedupe_key: str
    ) -> SqlFactorySpan | None:
        return session.execute(
            select(SqlFactorySpan).where(
                SqlFactorySpan.workspace_id == current_workspace_id(),
                SqlFactorySpan.factory_id == factory_id,
                SqlFactorySpan.dedupe_key == dedupe_key,
            )
        ).scalar_one_or_none()

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
        fid = norm_id(factory_id)
        start = now_ms() if started_at is None else started_at
        row_id = norm_id(span_id) if span_id else new_event_id(start)
        columns = _columns("span", {"outcome": "open", **fields, "kind": kind})

        def write(session: Session) -> tuple[FactorySpan, bool]:
            if dedupe_key is not None:
                existing = self._span_by_dedupe(session, fid, dedupe_key)
                if existing is not None:
                    return _span(existing), False
            row = SqlFactorySpan(
                id=row_id,
                factory_id=fid,
                trace_key=trace_key,
                name=clip_value("span", "name", name),
                started_at=start,
                dedupe_key=dedupe_key,
                **columns,
            )
            session.add(row)
            session.flush()
            return _span(row), True

        return self._write("record_factory_span", write)

    def get_span(self, span_id: str) -> FactorySpan | None:
        with self._session("get_factory_span") as session:
            row = session.get(SqlFactorySpan, (current_workspace_id(), norm_id(span_id)))
            return _span(row) if row is not None else None

    def get_span_by_dedupe(self, factory_id: str, dedupe_key: str) -> FactorySpan | None:
        with self._session("get_factory_span_by_dedupe") as session:
            row = self._span_by_dedupe(session, norm_id(factory_id), dedupe_key)
            return _span(row) if row is not None else None

    def update_span(self, span_id: str, **fields: Any) -> FactorySpan | None:
        check_fields(fields, SPAN_FIELDS, "update_span")
        validate_enums("span", fields)
        key = (current_workspace_id(), norm_id(span_id))
        values = _columns("span", fields)

        def write(session: Session) -> FactorySpan | None:
            row = session.get(SqlFactorySpan, key)
            if row is None:
                return None
            for name, value in values.items():
                setattr(row, name, value)
            return _span(row)

        return run_write_transaction(self._session_immediate, "update_factory_span", write)

    @staticmethod
    def _span_filters(stmt: Any, filters: Mapping[str, str | None]) -> Any:
        for column, value in filters.items():
            if value is None:
                continue
            if column.endswith("_id"):
                value = norm_id(value)
            stmt = stmt.where(getattr(SqlFactorySpan, column) == value)
        return stmt

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
        model = SqlFactorySpan
        stmt = select(model).where(
            model.workspace_id == current_workspace_id(),
            model.factory_id == norm_id(factory_id),
            model.ended_at.is_(None),
        )
        if kind is not None:
            stmt = stmt.where(model.kind == encode_factory_span_kind(kind))
        stmt = self._span_filters(
            stmt,
            {
                "project_slug": project_slug,
                "pr_id": pr_id,
                "task_id": task_id,
                "loop_id": loop_id,
                "session_id": session_id,
                "trace_key": trace_key,
            },
        )
        stmt = stmt.order_by(model.started_at, model.id).limit(limit)
        with self._session("list_open_factory_spans") as session:
            return [_span(r) for r in session.execute(stmt).scalars().all()]

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
        model = SqlFactorySpan
        stmt = select(model).where(
            model.workspace_id == current_workspace_id(),
            model.factory_id == norm_id(factory_id),
        )
        if kinds is not None:
            stmt = stmt.where(model.kind.in_([encode_factory_span_kind(k) for k in kinds]))
        if since_ms is not None:
            stmt = stmt.where(model.started_at >= since_ms)
        if until_ms is not None:
            stmt = stmt.where(model.started_at < until_ms)
        stmt = self._span_filters(
            stmt,
            {
                "project_slug": project_slug,
                "pr_id": pr_id,
                "task_id": task_id,
                "loop_id": loop_id,
                "trace_key": trace_key,
            },
        )
        stmt = stmt.order_by(model.started_at, model.id).limit(limit)
        with self._session("list_factory_spans") as session:
            return [_span(r) for r in session.execute(stmt).scalars().all()]

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
        table = SqlFactoryCostDaily

        def write(session: Session) -> FactoryCostDaily:
            key = (current_workspace_id(), fid, project_slug, role, agent_name, model, day_utc)
            now = now_s()
            # An atomic increment, so concurrent turns never lose an addition.
            result = session.execute(
                update(table)
                .where(
                    table.workspace_id == key[0],
                    table.factory_id == fid,
                    table.project_slug == project_slug,
                    table.role == role,
                    table.agent_name == agent_name,
                    table.model == model,
                    table.day_utc == day_utc,
                )
                .values(
                    cost_usd=table.cost_usd + float(cost_usd),
                    tokens_in=table.tokens_in + int(tokens_in),
                    tokens_out=table.tokens_out + int(tokens_out),
                    turns=table.turns + int(turns),
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:  # type: ignore[attr-defined]
                session.add(
                    table(
                        factory_id=fid,
                        project_slug=project_slug,
                        role=role,
                        agent_name=agent_name,
                        model=model,
                        day_utc=day_utc,
                        cost_usd=float(cost_usd),
                        tokens_in=int(tokens_in),
                        tokens_out=int(tokens_out),
                        turns=int(turns),
                        updated_at=now,
                    )
                )
                session.flush()
            row = session.get(table, key, populate_existing=True)
            assert row is not None
            return _cost(row)

        return self._write("add_factory_cost", write)

    def list_cost_daily(
        self,
        factory_id: str,
        *,
        since_day: str | None = None,
        until_day: str | None = None,
        project_slug: str | None = None,
    ) -> builtins.list[FactoryCostDaily]:
        table = SqlFactoryCostDaily
        stmt = select(table).where(
            table.workspace_id == current_workspace_id(),
            table.factory_id == norm_id(factory_id),
        )
        if since_day is not None:
            stmt = stmt.where(table.day_utc >= since_day)
        if until_day is not None:
            stmt = stmt.where(table.day_utc <= until_day)
        if project_slug is not None:
            stmt = stmt.where(table.project_slug == project_slug)
        stmt = stmt.order_by(
            table.day_utc, table.project_slug, table.role, table.agent_name, table.model
        )
        with self._session("list_factory_cost") as session:
            return [_cost(r) for r in session.execute(stmt).scalars().all()]

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

        def write(session: Session) -> tuple[FactoryVersion, bool]:
            existing = session.execute(
                select(SqlFactoryVersion).where(
                    SqlFactoryVersion.workspace_id == current_workspace_id(),
                    SqlFactoryVersion.content_hash == content_hash,
                )
            ).scalar_one_or_none()
            if existing is not None:
                return _version(existing), False
            row = SqlFactoryVersion(
                id=new_id(),
                created_at=now_s(),
                image_tag=image_tag,
                server_sha=server_sha,
                bundles=dump_json(dict(bundles)),
                knobs=dump_json(dict(knobs)),
                content_hash=content_hash,
                note=note,
            )
            session.add(row)
            session.flush()
            return _version(row), True

        return self._write("record_factory_version", write)

    def get_version(self, version_id: str) -> FactoryVersion | None:
        with self._session("get_factory_version") as session:
            row = session.get(SqlFactoryVersion, (current_workspace_id(), norm_id(version_id)))
            return _version(row) if row is not None else None

    def list_versions(self, limit: int = 50) -> builtins.list[FactoryVersion]:
        stmt = (
            select(SqlFactoryVersion)
            .where(SqlFactoryVersion.workspace_id == current_workspace_id())
            .order_by(SqlFactoryVersion.created_at.desc(), SqlFactoryVersion.id.desc())
            .limit(limit)
        )
        with self._session("list_factory_versions") as session:
            return [_version(r) for r in session.execute(stmt).scalars().all()]

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
        via = encode_factory_definition_via(created_via)
        fid = norm_id(factory_id)
        body = dict(doc)
        model = SqlFactoryDefinition

        def write(session: Session) -> FactoryDefinition:
            ws = current_workspace_id()
            number = version
            if number is None:
                # A racer that takes this number first fails the insert on the
                # primary key, and the replay numbers past it.
                top = session.execute(
                    select(func.max(model.version)).where(
                        model.workspace_id == ws, model.factory_id == fid
                    )
                ).scalar()
                number = (top or 0) + 1
            else:
                existing = session.get(model, (ws, fid, number))
                if existing is not None:
                    raise DefinitionVersionExists(_definition(existing))
            row = model(
                factory_id=fid,
                version=number,
                parent_version=parent_version,
                doc=dump_json(body),
                hash=canonical_hash(body),
                created_by=created_by,
                created_via=via,
                proposal_id=opt_id(proposal_id),
                created_at=now_s(),
            )
            session.add(row)
            session.flush()
            return _definition(row)

        return self._write("put_factory_definition", write, sequence=(_DEFINITION_LOCK_NS, fid))

    def get_definition(self, factory_id: str, version: int) -> FactoryDefinition | None:
        with self._session("get_factory_definition") as session:
            row = session.get(
                SqlFactoryDefinition, (current_workspace_id(), norm_id(factory_id), version)
            )
            return _definition(row) if row is not None else None

    def latest_definition(self, factory_id: str) -> FactoryDefinition | None:
        rows = self.list_definitions(factory_id, limit=1)
        return rows[0] if rows else None

    def list_definitions(
        self, factory_id: str, limit: int = 50
    ) -> builtins.list[FactoryDefinition]:
        stmt = (
            select(SqlFactoryDefinition)
            .where(
                SqlFactoryDefinition.workspace_id == current_workspace_id(),
                SqlFactoryDefinition.factory_id == norm_id(factory_id),
            )
            .order_by(SqlFactoryDefinition.version.desc())
            .limit(limit)
        )
        with self._session("list_factory_definitions") as session:
            return [_definition(r) for r in session.execute(stmt).scalars().all()]

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
        values = {
            "id": new_id(),
            "factory_id": norm_id(factory_id),
            "project_slug": project_slug,
            "target_kind": encode_factory_feedback_target(target_kind),
            "target_id": target_id,
            "head_sha": head_sha,
            "author_kind": encode_factory_actor_kind(author_kind),
            "author": author,
            "kind": encode_factory_feedback_kind(kind),
            "rating": rating,
            "tags": dump_json(list(tags or [])),
            "body": body,
            "def_version": def_version,
        }

        def write(session: Session) -> FactoryFeedback:
            row = SqlFactoryFeedback(**values, created_at=now_s())
            session.add(row)
            session.flush()
            return _feedback(row)

        return run_write_transaction(self._session_immediate, "add_factory_feedback", write)

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
        model = SqlFactoryFeedback
        stmt = select(model).where(
            model.workspace_id == current_workspace_id(),
            model.factory_id == norm_id(factory_id),
        )
        if target_kind is not None:
            stmt = stmt.where(model.target_kind == encode_factory_feedback_target(target_kind))
        if target_id is not None:
            stmt = stmt.where(model.target_id == target_id)
        if kinds is not None:
            stmt = stmt.where(model.kind.in_([encode_factory_feedback_kind(k) for k in kinds]))
        if since is not None:
            stmt = stmt.where(model.created_at >= since)
        stmt = stmt.order_by(model.created_at.desc(), model.id.desc()).limit(limit)
        with self._session("list_factory_feedback") as session:
            return [_feedback(r) for r in session.execute(stmt).scalars().all()]
