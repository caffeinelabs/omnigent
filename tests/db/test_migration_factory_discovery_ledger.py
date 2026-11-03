"""Tests for the factory discovery-ledger migration (fz3d4e5f6a7b).

Upgrades from the proposal-bets revision, checks the five new tables and the
widened feedback targets, that the ORM models match the migrated schema
(CockroachDB bootstraps from the models), that re-running the upgrade over
present tables is a no-op, and that it downgrades cleanly. Runs on SQLite.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.exc import IntegrityError

from omnigent.db.db_models import OmnigentBase
from omnigent.db.utils import _build_alembic_config

_PREVIOUS = "fz2a7c3e9b14"
_REVISION = "fz3d4e5f6a7b"
_TABLES = {
    "factory_signals",
    "factory_signal_links",
    "factory_opportunities",
    "factory_score_snapshots",
    "factory_source_state",
}


def _migrate(uri: str, engine: sa.Engine, revision: str, *, down: bool = False) -> None:
    config = _build_alembic_config(uri)
    with engine.connect() as conn:
        config.attributes["connection"] = conn
        (command.downgrade if down else command.upgrade)(config, revision)
        conn.commit()


@pytest.fixture
def engine(tmp_path: Path) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{tmp_path / 'discovery_ledger.db'}")


def _uri(engine: sa.Engine) -> str:
    return engine.url.render_as_string(hide_password=False)


def _tables(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names())


def _feedback(engine: sa.Engine, target_kind: int) -> None:
    table = sa.Table("factory_feedback", sa.MetaData(), autoload_with=engine)
    with engine.begin() as conn:
        conn.execute(
            table.insert().values(
                workspace_id=1,
                id=os.urandom(16),
                factory_id=os.urandom(16),
                target_kind=target_kind,
                target_id="O-1",
                author_kind=1,
                author="alice",
                kind=5,
                created_at=1,
            )
        )


def test_round_trip_creates_the_ledger_and_matches_the_models(engine: sa.Engine) -> None:
    uri = _uri(engine)
    try:
        _migrate(uri, engine, _PREVIOUS)
        assert not _TABLES & _tables(engine)
        with pytest.raises(IntegrityError):
            _feedback(engine, 10)

        _migrate(uri, engine, "head")
        assert _tables(engine) >= _TABLES
        _feedback(engine, 9)
        _feedback(engine, 10)
        with pytest.raises(IntegrityError):
            _feedback(engine, 11)
        with engine.connect() as conn:
            context = MigrationContext.configure(conn, opts={"compare_type": True})
            diffs = compare_metadata(context, OmnigentBase.metadata)
        touched = [*_TABLES, "factory_feedback"]
        assert [d for d in diffs if any(t in repr(d) for t in touched)] == []

        _migrate(uri, engine, _PREVIOUS, down=True)
        assert not _TABLES & _tables(engine)
        with engine.connect() as conn:
            # Feedback on a signal or an opportunity goes with the widened CHECK.
            assert conn.execute(sa.text("SELECT count(*) FROM factory_feedback")).scalar() == 0
    finally:
        engine.dispose()


def test_signal_checks_and_the_pointer_key_are_enforced(engine: sa.Engine) -> None:
    uri = _uri(engine)
    try:
        _migrate(uri, engine, "head")
        table = sa.Table("factory_signals", sa.MetaData(), autoload_with=engine)
        fid = os.urandom(16)
        row = {
            "workspace_id": 1,
            "factory_id": fid,
            "source": "web",
            "source_kind": 3,
            "privacy": 1,
            "ingested_at": 1,
            "pointer_key": "k" * 64,
        }
        with engine.begin() as conn:
            conn.execute(table.insert().values(number=1, **row))
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(table.insert().values(number=2, **row))
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    table.insert().values(**{**row, "number": 3, "pointer_key": None, "kind": 12})
                )
    finally:
        engine.dispose()


def test_upgrade_skips_tables_that_already_exist(engine: sa.Engine) -> None:
    # CockroachDB bootstraps from the models, then replays the revisions after its baseline.
    uri = _uri(engine)
    try:
        _migrate(uri, engine, _PREVIOUS)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE factory_source_state (workspace_id BIGINT NOT NULL, "
                    "factory_id BLOB NOT NULL, source VARCHAR(32) NOT NULL, "
                    "updated_at INTEGER NOT NULL, PRIMARY KEY (workspace_id, factory_id, source))"
                )
            )
        _migrate(uri, engine, "head")
        assert _tables(engine) >= _TABLES
    finally:
        engine.dispose()


def test_revision_chains_off_the_proposal_bets_revision() -> None:
    # test_migration_connections.test_single_alembic_head guards the single head.
    script = ScriptDirectory.from_config(_build_alembic_config("sqlite://"))
    revision = script.get_revision(_REVISION)
    assert revision is not None and revision.down_revision == _PREVIOUS
