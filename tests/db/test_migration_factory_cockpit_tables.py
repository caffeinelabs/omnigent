"""Tests for the factory cockpit tables migration (fy1a2b3c4d5e).

Upgrades from the previous head, checks the five tables, their keys, indexes
and CHECK constraints, checks that the ORM models match the migrated schema
(CockroachDB bootstraps from the models, every other backend from the
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

_PREVIOUS_HEAD = "daba139ca998"
_REVISION = "fy1a2b3c4d5e"
_TABLES = {
    "factory_projects",
    "factory_prs",
    "factory_decisions",
    "factory_events",
    "factory_demos",
}
_INDEXES = {
    "factory_prs": {"ix_factory_prs_poll": ["workspace_id", "state", "last_polled_at", "id"]},
    "factory_decisions": {
        "ix_factory_decisions_status": [
            "workspace_id",
            "factory_id",
            "status",
            "created_at",
            "id",
        ],
        "ix_factory_decisions_dedupe": ["workspace_id", "factory_id", "dedupe_key", "id"],
    },
    "factory_events": {
        "ix_factory_events_factory_ts": ["workspace_id", "factory_id", "ts", "id"],
        "ix_factory_events_kind_ts": ["workspace_id", "kind", "ts", "id"],
    },
    "factory_demos": {
        "ix_factory_demos_pr": ["workspace_id", "factory_id", "pr_number", "created_at", "id"],
    },
}
_UNIQUES = {
    "factory_projects": ["workspace_id", "factory_id", "slug"],
    "factory_prs": ["workspace_id", "factory_id", "repo", "number"],
    "factory_decisions": ["workspace_id", "factory_id", "number"],
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
    return f"sqlite:///{tmp_path / 'factory.db'}"


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
    name = f"omnigent_test_factory_{os.environ.get('PYTEST_XDIST_WORKER', 'w0')}"
    with root.connect() as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    try:
        yield re.sub(r"/[^/]*(\?.*)?$", f"/{name}", base_uri)
    finally:
        with root.connect() as conn:
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        root.dispose()


def _assert_schema(engine: sa.Engine) -> None:
    insp = sa.inspect(engine)
    assert set(insp.get_table_names()) >= _TABLES
    for table in _TABLES:
        assert insp.get_pk_constraint(table)["constrained_columns"] == ["workspace_id", "id"]
        assert insp.get_foreign_keys(table) == []
    for table, expected in _INDEXES.items():
        found = {i["name"]: list(i["column_names"]) for i in insp.get_indexes(table)}
        for name, columns in expected.items():
            assert found[name] == columns, (table, name)
    for table, columns in _UNIQUES.items():
        uniques = [list(u["column_names"]) for u in insp.get_unique_constraints(table)]
        assert columns in uniques, (table, uniques)


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
        _migrate(uri, engine, _PREVIOUS_HEAD)
        assert not _TABLES & set(sa.inspect(engine).get_table_names())

        _migrate(uri, engine, "head")
        _assert_schema(engine)
        _assert_models_match(engine)

        _migrate(uri, engine, _PREVIOUS_HEAD, down=True)
        assert not _TABLES & set(sa.inspect(engine).get_table_names())

        _migrate(uri, engine, "head")
        _assert_schema(engine)
    finally:
        engine.dispose()


def test_upgrade_and_downgrade_sqlite(sqlite_uri: str) -> None:
    _round_trip(sqlite_uri)


def test_upgrade_and_downgrade_postgres(pg_uri: str) -> None:
    _round_trip(pg_uri)


def test_revision_chains_off_the_previous_head() -> None:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_build_alembic_config("sqlite://"))
    revision = script.get_revision(_REVISION)
    assert revision is not None and revision.down_revision == _PREVIOUS_HEAD
    assert script.get_heads() == [_REVISION]


@pytest.mark.parametrize(
    ("table", "column", "bad"),
    [
        ("factory_projects", "stage", 9),
        ("factory_projects", "priority", 4),
        ("factory_prs", "state", 4),
        ("factory_decisions", "status", 5),
        ("factory_decisions", "source", 6),
        ("factory_events", "severity", 4),
        ("factory_demos", "status", 5),
    ],
)
def test_enum_checks_reject_unknown_codes(
    sqlite_uri: str, table: str, column: str, bad: int
) -> None:
    engine = sa.create_engine(sqlite_uri)
    _migrate(sqlite_uri, engine, "head")
    meta = sa.MetaData()
    tbl = sa.Table(table, meta, autoload_with=engine)
    row: dict[str, object] = {}
    for col in tbl.columns:
        if col.nullable or col.server_default is not None:
            continue
        row[col.name] = (
            os.urandom(16)
            if isinstance(col.type, sa.LargeBinary)
            else 1
            if isinstance(col.type, (sa.Integer, sa.SmallInteger, sa.BigInteger))
            else "x"
        )
    for name in ("id", "factory_id", "session_id", "created_by_session"):
        if name in tbl.columns:
            row[name] = os.urandom(16)
    if table == "factory_events":
        row["severity"] = 0
    with engine.begin() as conn:
        conn.execute(tbl.insert().values(**row))
    row["id"] = os.urandom(16)
    if "number" in row:
        row["number"] = 2
    if "slug" in row:
        row["slug"] = "y"
    row[column] = bad
    with pytest.raises(IntegrityError) as excinfo, engine.begin() as conn:
        conn.execute(tbl.insert().values(**row))
    assert "CHECK constraint failed" in str(excinfo.value)
    engine.dispose()
