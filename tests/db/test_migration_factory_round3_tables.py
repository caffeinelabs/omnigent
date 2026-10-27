"""Tests for the factory round-3 migration (fz1a2b3c4d5e).

Upgrades from the cockpit revision, checks the twelve new tables, their keys,
indexes and CHECK constraints, the new columns on the cockpit tables and the
widened decision-source CHECK, checks that the ORM models match the migrated
schema (CockroachDB bootstraps from the models, every other backend from the
migration), and downgrades cleanly. Runs on SQLite, and on PostgreSQL when
``OMNIGENT_TEST_DB_URI`` points at one.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.exc import IntegrityError

from omnigent.db.db_models import OmnigentBase
from omnigent.db.utils import _build_alembic_config

_PREVIOUS = "fy1a2b3c4d5e"
_REVISION = "fz1a2b3c4d5e"

_PKS = {
    "factory_people": ["workspace_id", "factory_id", "user_id"],
    "factory_chat_messages": ["workspace_id", "id"],
    "factory_chat_reads": ["workspace_id", "factory_id", "user_id", "thread"],
    "factory_proposals": ["workspace_id", "id"],
    "factory_tasks": ["workspace_id", "id"],
    "factory_loops": ["workspace_id", "id"],
    "factory_issues": ["workspace_id", "id"],
    "factory_spans": ["workspace_id", "id"],
    "factory_cost_daily": [
        "workspace_id",
        "factory_id",
        "project_slug",
        "role",
        "agent_name",
        "model",
        "day_utc",
    ],
    "factory_versions": ["workspace_id", "id"],
    "factory_definitions": ["workspace_id", "factory_id", "version"],
    "factory_feedback": ["workspace_id", "id"],
}
_TABLES = set(_PKS)
_INDEXES = {
    "factory_people": {"ix_factory_people_user": ["workspace_id", "user_id", "factory_id"]},
    "factory_chat_messages": {
        "ix_factory_chat_messages_thread": ["workspace_id", "factory_id", "thread", "seq", "id"]
    },
    "factory_proposals": {
        "ix_factory_proposals_status": ["workspace_id", "factory_id", "status", "id"]
    },
    "factory_tasks": {"ix_factory_tasks_state": ["workspace_id", "factory_id", "state", "id"]},
    "factory_loops": {"ix_factory_loops_state": ["workspace_id", "factory_id", "state", "id"]},
    "factory_issues": {
        "ix_factory_issues_status": ["workspace_id", "factory_id", "status", "severity", "id"]
    },
    "factory_spans": {
        "ix_factory_spans_kind": ["workspace_id", "factory_id", "kind", "started_at", "id"],
        "ix_factory_spans_pr": ["workspace_id", "pr_id", "started_at", "id"],
        "ix_factory_spans_task": ["workspace_id", "task_id", "started_at", "id"],
        "ix_factory_spans_ended": ["workspace_id", "factory_id", "ended_at", "id"],
    },
    "factory_feedback": {
        "ix_factory_feedback_target": [
            "workspace_id",
            "factory_id",
            "target_kind",
            "created_at",
            "id",
        ]
    },
}
_UNIQUES = {
    "factory_chat_messages": [["workspace_id", "factory_id", "seq"]],
    "factory_proposals": [
        ["workspace_id", "factory_id", "number"],
        ["workspace_id", "factory_id", "dedupe_key"],
    ],
    "factory_tasks": [
        ["workspace_id", "factory_id", "project_slug", "key"],
        ["workspace_id", "factory_id", "number"],
    ],
    "factory_loops": [["workspace_id", "factory_id", "kind", "subject"]],
    "factory_issues": [
        ["workspace_id", "factory_id", "class", "number"],
        ["workspace_id", "factory_id", "dedupe_key"],
    ],
    "factory_spans": [["workspace_id", "factory_id", "dedupe_key"]],
    "factory_versions": [["workspace_id", "content_hash"]],
}
_NEW_COLUMNS = {
    "factory_projects": {"status", "parent_slug", "closed_at", "def_version", "version_id"},
    "factory_prs": {"opened_at", "first_ready_at", "task_id", "version_id"},
    "factory_decisions": {"assignee", "claimed_by", "claimed_at", "thread", "subject"},
}


def _migrate(uri: str, engine: sa.Engine, revision: str, *, down: bool = False) -> None:
    config = _build_alembic_config(uri)
    # A plain connection lets PostgreSQL migrations open autocommit blocks.
    with engine.connect() as conn:
        config.attributes["connection"] = conn
        (command.downgrade if down else command.upgrade)(config, revision)
        conn.commit()


@pytest.fixture
def sqlite_uri(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'factory_round3.db'}"


@pytest.fixture
def pg_uri() -> Iterator[str]:
    """A fresh PostgreSQL scratch database (skips without a Postgres base URI)."""
    base_uri = os.environ.get("OMNIGENT_TEST_DB_URI", "")
    if not base_uri:
        pytest.skip("requires a PostgreSQL OMNIGENT_TEST_DB_URI")
    root = sa.create_engine(base_uri, isolation_level="AUTOCOMMIT")
    if root.dialect.name != "postgresql":
        root.dispose()
        pytest.skip("PostgreSQL-only check")
    name = f"omnigent_test_factory_r3_{os.environ.get('PYTEST_XDIST_WORKER', 'w0')}"
    with root.connect() as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    try:
        yield re.sub(r"/[^/]*(\?.*)?$", f"/{name}", base_uri)
    finally:
        with root.connect() as conn:
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        root.dispose()


def _columns(engine: sa.Engine, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(engine).get_columns(table)}


def _assert_schema(engine: sa.Engine) -> None:
    insp = sa.inspect(engine)
    assert set(insp.get_table_names()) >= _TABLES
    for table, pk in _PKS.items():
        assert insp.get_pk_constraint(table)["constrained_columns"] == pk, table
        assert insp.get_foreign_keys(table) == []
    for table, expected in _INDEXES.items():
        found = {i["name"]: list(i["column_names"]) for i in insp.get_indexes(table)}
        for name, columns in expected.items():
            assert found[name] == columns, (table, name)
    for table, expected_uniques in _UNIQUES.items():
        uniques = [list(u["column_names"]) for u in insp.get_unique_constraints(table)]
        for columns in expected_uniques:
            assert columns in uniques, (table, uniques)
    for table, columns in _NEW_COLUMNS.items():
        assert columns <= _columns(engine, table), table


def _assert_previous_schema(engine: sa.Engine) -> None:
    names = set(sa.inspect(engine).get_table_names())
    assert not _TABLES & names
    for table, columns in _NEW_COLUMNS.items():
        assert not columns & _columns(engine, table), table


def _assert_models_match(engine: sa.Engine) -> None:
    """The ORM models (CockroachDB's create_all source) match the migrated tables."""
    with engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={"compare_type": True})
        diffs = compare_metadata(context, OmnigentBase.metadata)
    factory_diffs = [d for d in diffs if "factory_" in repr(d)]
    assert factory_diffs == []


def _round_trip(uri: str) -> None:
    engine = sa.create_engine(uri)
    try:
        _migrate(uri, engine, _PREVIOUS)
        _assert_previous_schema(engine)

        _migrate(uri, engine, "head")
        _assert_schema(engine)
        _assert_models_match(engine)

        _migrate(uri, engine, _PREVIOUS, down=True)
        _assert_previous_schema(engine)

        _migrate(uri, engine, "head")
        _assert_schema(engine)
    finally:
        engine.dispose()


def test_upgrade_and_downgrade_sqlite(sqlite_uri: str) -> None:
    _round_trip(sqlite_uri)


def test_upgrade_and_downgrade_postgres(pg_uri: str) -> None:
    _round_trip(pg_uri)


def test_revision_is_the_single_head() -> None:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_build_alembic_config("sqlite://"))
    revision = script.get_revision(_REVISION)
    assert revision is not None and revision.down_revision == _PREVIOUS
    assert script.get_heads() == [_REVISION]


def _seed_previous(engine: sa.Engine) -> None:
    """One cockpit-era project, PR and decision, as the deployed database holds them."""
    meta = sa.MetaData()
    projects = sa.Table("factory_projects", meta, autoload_with=engine)
    prs = sa.Table("factory_prs", meta, autoload_with=engine)
    decisions = sa.Table("factory_decisions", meta, autoload_with=engine)
    fid = os.urandom(16)
    with engine.begin() as conn:
        conn.execute(
            projects.insert().values(
                id=os.urandom(16),
                factory_id=fid,
                slug="api-version",
                title="API version",
                stage_changed_at=1,
                created_by="luka@caffeine.ai",
                created_at=1,
                updated_at=1,
            )
        )
        conn.execute(
            prs.insert().values(
                id=os.urandom(16),
                factory_id=fid,
                repo="caffeinelabs/app",
                number=9630,
                url="https://github.com/caffeinelabs/app/pull/9630",
                title="Add GET /version",
                head_ref="factory/api-version/version-endpoint",
                base_ref="main",
                head_sha="0" * 40,
                gates=b"{}",
                source="tool",
                created_at=1,
                updated_at=1,
            )
        )
        conn.execute(
            decisions.insert().values(
                id=os.urandom(16),
                factory_id=fid,
                number=1,
                source=1,
                question="Which table?",
                options=b"[]",
                dedupe_key="k",
                created_at=1,
                updated_at=1,
            )
        )


def test_existing_rows_survive_and_default_to_active(sqlite_uri: str) -> None:
    engine = sa.create_engine(sqlite_uri)
    try:
        _migrate(sqlite_uri, engine, _PREVIOUS)
        _seed_previous(engine)
        _migrate(sqlite_uri, engine, "head")
        with engine.connect() as conn:
            project = conn.execute(sa.text("SELECT status, parent_slug FROM factory_projects"))
            assert project.one() == (1, None)
            pr = conn.execute(sa.text("SELECT number, opened_at, task_id FROM factory_prs"))
            assert pr.one() == (9630, None, None)
            decision = conn.execute(sa.text("SELECT number, assignee FROM factory_decisions"))
            assert decision.one() == (1, None)
    finally:
        engine.dispose()


def _insert_minimal(engine: sa.Engine, table: str, **overrides: object) -> None:
    """Insert a row with every required column filled with a plausible value."""
    meta = sa.MetaData()
    tbl = sa.Table(table, meta, autoload_with=engine)
    row: dict[str, object] = {}
    for col in tbl.columns:
        if col.nullable or col.server_default is not None:
            continue
        if isinstance(col.type, sa.LargeBinary):
            row[col.name] = os.urandom(16)
        elif isinstance(col.type, (sa.Integer, sa.SmallInteger, sa.BigInteger, sa.Float)):
            row[col.name] = 1
        else:
            row[col.name] = "x"
    row.update(overrides)
    with engine.begin() as conn:
        conn.execute(tbl.insert().values(**row))


@pytest.mark.parametrize(
    ("table", "column", "good", "bad"),
    [
        ("factory_projects", "status", 4, 5),
        ("factory_decisions", "source", 9, 10),
        ("factory_people", "role", 3, 4),
        ("factory_chat_messages", "author_kind", 3, 4),
        ("factory_proposals", "status", 11, 12),
        ("factory_proposals", "class", 3, 4),
        ("factory_tasks", "state", 8, 9),
        ("factory_tasks", "created_via", 3, 4),
        ("factory_loops", "kind", 9, 10),
        ("factory_issues", "class", 3, 4),
        ("factory_issues", "severity", 3, 4),
        ("factory_spans", "kind", 21, 22),
        ("factory_spans", "outcome", 8, 9),
        ("factory_definitions", "created_via", 4, 5),
        ("factory_feedback", "target_kind", 8, 9),
        ("factory_feedback", "kind", 6, 7),
    ],
)
def test_enum_checks_accept_the_last_code_and_reject_the_next(
    sqlite_uri: str, table: str, column: str, good: int, bad: int
) -> None:
    engine = sa.create_engine(sqlite_uri)
    try:
        _migrate(sqlite_uri, engine, "head")
        uuid_columns = {"id", "factory_id", "session_id", "created_by_session"}
        meta = sa.MetaData()
        tbl = sa.Table(table, meta, autoload_with=engine)
        base = {name: os.urandom(16) for name in uuid_columns if name in tbl.columns}
        if "severity" in tbl.columns and column != "severity":
            base["severity"] = 0
        if table == "factory_definitions":
            base["version"] = 1
        _insert_minimal(engine, table, **base, **{column: good})
        again = {name: os.urandom(16) for name in uuid_columns if name in tbl.columns}
        if "severity" in tbl.columns and column != "severity":
            again["severity"] = 0
        for unique in ("number", "seq", "version"):
            if unique in tbl.columns:
                again[unique] = 2
        for text_key in ("user_id", "slug", "thread"):
            if text_key in tbl.columns and not tbl.columns[text_key].nullable:
                again[text_key] = "y"
        with pytest.raises(IntegrityError) as excinfo:
            _insert_minimal(engine, table, **again, **{column: bad})
        assert "CHECK constraint failed" in str(excinfo.value)
    finally:
        engine.dispose()


def test_downgrade_drops_round3_decision_sources(sqlite_uri: str) -> None:
    engine = sa.create_engine(sqlite_uri)
    try:
        _migrate(sqlite_uri, engine, "head")
        _insert_minimal(
            engine,
            "factory_decisions",
            id=os.urandom(16),
            factory_id=os.urandom(16),
            number=1,
            source=7,
        )
        _migrate(sqlite_uri, engine, _PREVIOUS, down=True)
        with engine.connect() as conn:
            count = conn.execute(sa.text("SELECT COUNT(*) FROM factory_decisions")).scalar()
        assert count == 0
    finally:
        engine.dispose()
