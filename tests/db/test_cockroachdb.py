"""Live CockroachDB compatibility checks selected by OMNIGENT_TEST_DB_URI."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url

from omnigent.db.cockroachdb import (
    _CRDB_BOOTSTRAP_MARKER_TABLE,
    _CRDB_BOOTSTRAP_MARKER_TOKEN,
    CRDB_BASELINE_REVISION,
    _crdb_server_version,
    _prepare_crdb_schema_transaction,
)
from omnigent.db.utils import (
    _create_engine,
    _get_current_db_revision,
    _get_head_db_revision,
    _initialize_or_verify_schema,
    get_or_create_engine,
    is_cockroachdb,
)

_FACTORY_ROUND3_TABLES = (
    "factory_people",
    "factory_chat_messages",
    "factory_chat_reads",
    "factory_proposals",
    "factory_tasks",
    "factory_loops",
    "factory_issues",
    "factory_spans",
    "factory_cost_daily",
    "factory_versions",
    "factory_definitions",
    "factory_feedback",
)
_FACTORY_ROUND3_COLUMNS = {
    "factory_projects": ("status", "parent_slug", "closed_at", "def_version", "version_id"),
    "factory_prs": ("opened_at", "first_ready_at", "task_id", "version_id"),
    "factory_decisions": ("assignee", "claimed_by", "claimed_at", "thread", "subject"),
}


def _crdb_engine(db_uri: str) -> Engine:
    engine = get_or_create_engine(db_uri)
    if not is_cockroachdb(engine.dialect.name):
        pytest.skip("requires OMNIGENT_TEST_DB_URI pointing to CockroachDB")
    return engine


@pytest.fixture
def scratch_crdb_uri(db_uri: str) -> Iterator[str]:
    """A dedicated empty CockroachDB database, so schema surgery never touches the worker's."""
    _crdb_engine(db_uri)
    url = make_url(db_uri)
    name = f"omnigent_test_crdb_upgrade_{os.environ.get('PYTEST_XDIST_WORKER', 'w0')}"
    root = create_engine(url, isolation_level="AUTOCOMMIT")
    with root.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" CASCADE'))
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield url.set(database=name).render_as_string(hide_password=False)
    finally:
        with root.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" CASCADE'))
        root.dispose()


def _run_ddl(uri: str, *statements: str) -> None:
    """Run each statement in its own transaction, as CRDB schema changes prefer."""
    engine = create_engine(uri, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            for statement in statements:
                connection.execute(text(statement))
    finally:
        engine.dispose()


def _factory_schema(engine: Engine) -> dict[str, tuple[Any, ...]]:
    inspector = inspect(engine)
    return {
        table: (
            sorted(
                (column["name"], str(column["type"]), column["nullable"])
                for column in inspector.get_columns(table)
            ),
            inspector.get_pk_constraint(table)["constrained_columns"],
            sorted(
                (str(index["name"]), tuple(index["column_names"]), bool(index["unique"]))
                for index in inspector.get_indexes(table)
            ),
            sorted(
                (str(check["name"]), check["sqltext"])
                for check in inspector.get_check_constraints(table)
            ),
        )
        for table in sorted(inspector.get_table_names())
        if table.startswith("factory_")
    }


def _regress_to_pre_factory(uri: str, engine: Engine) -> str:
    """Drop every factory table, as on a database bootstrapped before the cockpit."""
    tables = [name for name in inspect(engine).get_table_names() if name.startswith("factory_")]
    _run_ddl(uri, *(f"DROP TABLE {table}" for table in tables))
    return "daba139ca998"


def _regress_to_cockpit(uri: str, engine: Engine) -> str:
    """Strip the round-3 schema, as on a database bootstrapped at the cockpit revision."""
    _run_ddl(uri, *(f"DROP TABLE {table}" for table in _FACTORY_ROUND3_TABLES))
    _run_ddl(
        uri,
        "ALTER TABLE factory_projects DROP CONSTRAINT ck_factory_projects_status",
        "ALTER TABLE factory_decisions DROP CONSTRAINT ck_factory_decisions_source",
    )
    _run_ddl(
        uri,
        "ALTER TABLE factory_decisions ADD CONSTRAINT ck_factory_decisions_source "
        "CHECK (source IN (1, 2, 3, 4, 5))",
    )
    for table, columns in _FACTORY_ROUND3_COLUMNS.items():
        _run_ddl(uri, *(f"ALTER TABLE {table} DROP COLUMN {column}" for column in columns))
    return "fy1a2b3c4d5e"


def _regress_to_interrupted_widening(uri: str, engine: Engine) -> str:
    """Leave only the parked narrow source CHECK, as when fz1 stops right after its rename."""
    revision = _regress_to_cockpit(uri, engine)
    _run_ddl(
        uri,
        "ALTER TABLE factory_decisions RENAME CONSTRAINT "
        "ck_factory_decisions_source TO ck_factory_decisions_source_narrow",
    )
    return revision


def test_cockroachdb_bootstrap_is_at_head(db_uri: str) -> None:
    engine = _crdb_engine(db_uri)
    assert _get_current_db_revision(engine) == _get_head_db_revision(db_uri)


def test_cockroachdb_uses_read_committed(db_uri: str) -> None:
    engine = _crdb_engine(db_uri)
    with engine.connect() as connection:
        isolation = connection.execute(text("SHOW transaction_isolation")).scalar_one()
    assert str(isolation).lower() == "read committed"


def test_cockroachdb_search_indexes_exist(db_uri: str) -> None:
    engine = _crdb_engine(db_uri)
    expected = {"ix_conversations_title_trgm"}
    found: set[str] = set()
    with engine.connect() as connection:
        for table in ("conversation_items", "conversations"):
            found.update(
                str(row["index_name"])
                for row in connection.execute(text(f"SHOW INDEXES FROM {table}")).mappings()
            )
    assert expected <= found
    assert "ix_conversation_items_search_text_trgm" not in found


def test_cockroachdb_upgrades_from_supported_baseline(db_uri: str) -> None:
    engine = _crdb_engine(db_uri)
    head = _get_head_db_revision(db_uri)
    if head == CRDB_BASELINE_REVISION:
        pytest.skip("requires a migration after the CRDB baseline")

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE alembic_version SET version_num = :revision"),
            {"revision": CRDB_BASELINE_REVISION},
        )

    _initialize_or_verify_schema(engine, db_uri)

    assert _get_current_db_revision(engine) == head


@pytest.mark.parametrize(
    "regress",
    [_regress_to_pre_factory, _regress_to_cockpit, _regress_to_interrupted_widening],
    ids=["pre_factory", "cockpit", "interrupted_widening"],
)
def test_cockroachdb_upgrades_factory_schema_from_earlier_revisions(
    scratch_crdb_uri: str, regress: Callable[[str, Engine], str]
) -> None:
    engine = _create_engine(scratch_crdb_uri)
    try:
        _initialize_or_verify_schema(engine, scratch_crdb_uri)
        head = _get_head_db_revision(scratch_crdb_uri)
        models_schema = _factory_schema(engine)
        revision = regress(scratch_crdb_uri, engine)
        _run_ddl(scratch_crdb_uri, f"UPDATE alembic_version SET version_num = '{revision}'")

        _initialize_or_verify_schema(engine, scratch_crdb_uri)

        assert _get_current_db_revision(engine) == head
        assert _factory_schema(engine) == models_schema
    finally:
        engine.dispose()


def test_cockroachdb_resumes_empty_revision_and_repairs_indexes(db_uri: str) -> None:
    engine = _crdb_engine(db_uri)
    version = _crdb_server_version(engine)
    head = _get_head_db_revision(db_uri)
    index_name = "ix_agents_created_at"
    with engine.connect() as connection:
        _prepare_crdb_schema_transaction(connection, version)
        connection.execute(
            text(
                f"CREATE TABLE {_CRDB_BOOTSTRAP_MARKER_TABLE} "
                "(token STRING PRIMARY KEY, target_revision STRING NOT NULL)"
            )
        )
        connection.commit()
        _prepare_crdb_schema_transaction(connection, version)
        connection.execute(text(f"DROP INDEX IF EXISTS {index_name}"))
        connection.commit()
    with engine.begin() as connection:
        connection.execute(
            text(
                f"INSERT INTO {_CRDB_BOOTSTRAP_MARKER_TABLE} "
                "(token, target_revision) VALUES (:token, :target_revision)"
            ),
            {"token": _CRDB_BOOTSTRAP_MARKER_TOKEN, "target_revision": head},
        )
        connection.execute(text("DELETE FROM alembic_version"))

    _initialize_or_verify_schema(engine, db_uri)

    with engine.connect() as connection:
        found = {
            str(row["index_name"])
            for row in connection.execute(text("SHOW INDEXES FROM agents")).mappings()
        }
    assert _get_current_db_revision(engine) == head
    assert _CRDB_BOOTSTRAP_MARKER_TABLE not in inspect(engine).get_table_names()
    assert index_name in found
