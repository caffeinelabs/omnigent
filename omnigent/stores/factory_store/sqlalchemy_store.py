"""SQLAlchemy-backed factory store."""

from __future__ import annotations

import builtins
import random
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypeVar

from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from omnigent.db.db_models import (
    SqlFactoryDecision,
    SqlFactoryDemo,
    SqlFactoryEvent,
    SqlFactoryPr,
    SqlFactoryProject,
    current_workspace_id,
)
from omnigent.db.enum_codecs import (
    decode_factory_decision_source,
    decode_factory_decision_status,
    decode_factory_demo_status,
    decode_factory_event_severity,
    decode_factory_pr_state,
    decode_factory_project_stage,
    encode_factory_decision_source,
    encode_factory_decision_status,
    encode_factory_demo_status,
    encode_factory_event_severity,
    encode_factory_pr_state,
    encode_factory_project_stage,
)
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    run_write_transaction,
)
from omnigent.entities.factory import (
    EventDraft,
    FactoryDecision,
    FactoryDemo,
    FactoryEvent,
    FactoryPr,
    FactoryProject,
)
from omnigent.stores.factory_store import (
    DECISION_FIELDS,
    DECISION_UPDATE_FIELDS,
    DEFAULT_PR_POLL_INTERVAL_S,
    PR_FIELDS,
    PROJECT_FIELDS,
    TERMINAL_PR_WINDOW_S,
    DecisionNotFound,
    DecisionNotOpen,
    FactoryStore,
    as_list,
    check_fields,
    default_pr_url,
    dump_json,
    load_json,
    new_event_id,
    new_id,
    norm_id,
    now_ms,
    now_s,
    opt_id,
    validate_pr_fields,
    validate_project_fields,
)

_T = TypeVar("_T")

# A collision on a UNIQUE key (decision number, project slug, PR number) between
# concurrent writers is retried this many times, after a short jittered pause.
_INTEGRITY_RETRIES = 3
_RETRY_PAUSE_S = 0.02

# Namespace mixed into the per-factory PostgreSQL advisory lock key.
_DECISION_LOCK_NS = 0x66616374


def _decision_lock_key(workspace_id: int, factory_id: str) -> int:
    """A signed 64-bit advisory-lock key for one workspace's factory decision numbering."""
    value = int.from_bytes(bytes.fromhex(factory_id)[:8], "big") ^ _DECISION_LOCK_NS
    value = (value ^ (workspace_id * 0x9E3779B97F4A7C15)) & ((1 << 64) - 1)
    return value - (1 << 64) if value >= (1 << 63) else value


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
    for key, value in fields.items():
        if key in _PROJECT_JSON:
            out[key] = dump_json(value)
        elif key == "stage":
            out[key] = encode_factory_project_stage(value)
        else:
            out[key] = value
    return out


def _pr_columns(fields: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in fields.items():
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

    def _write(self, operation: str, callback: Callable[[Session], _T]) -> _T:
        """Run a write transaction, retrying UNIQUE collisions between racing writers."""
        for attempt in range(_INTEGRITY_RETRIES + 1):
            try:
                return run_write_transaction(self._session_immediate, operation, callback)
            except IntegrityError:
                if attempt == _INTEGRITY_RETRIES:
                    raise
                time.sleep(_RETRY_PAUSE_S * (attempt + 1) * random.random())
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
        if "pl_session_id" in values:
            values["pl_session_id"] = opt_id(values["pl_session_id"])
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
                if "pl_session_id" in values:
                    values["pl_session_id"] = opt_id(values["pl_session_id"])
                values.setdefault("title", slug)
                values.setdefault("stage", "queued")
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
            if self._engine.dialect.name == "postgresql":
                # Serialize numbering per factory; SQLite's BEGIN IMMEDIATE already does.
                session.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"),
                    {"key": _decision_lock_key(ws, fid)},
                )
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

        return self._write("open_decision", write)

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
