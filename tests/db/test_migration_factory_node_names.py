"""Tests for the node short-name migration (fz9d0e1f2a3b).

Upgrades from the node last-look revision, checks ``factory_node_names`` and
its primary key (one name per node per factory), that the ORM models match the
migrated schema (CockroachDB bootstraps from the models), that re-running the
upgrade over a present table is a no-op, and that it downgrades cleanly. Runs
on SQLite; the PostgreSQL run is the alembic upgrade/downgrade/upgrade against
a live database.
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

_PREVIOUS = "fz8c9d0e1f2a"
_REVISION = "fz9d0e1f2a3b"
_TABLE = "factory_node_names"


def _migrate(uri: str, engine: sa.Engine, revision: str, *, down: bool = False) -> None:
    config = _build_alembic_config(uri)
    with engine.connect() as conn:
        config.attributes["connection"] = conn
        (command.downgrade if down else command.upgrade)(config, revision)
        conn.commit()


@pytest.fixture
def engine(tmp_path: Path) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{tmp_path / 'node_names.db'}")


def _uri(engine: sa.Engine) -> str:
    return engine.url.render_as_string(hide_password=False)


def _tables(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names())


def _name(engine: sa.Engine, factory: bytes, ref: str, name: str, by: str | None = None) -> None:
    table = sa.Table(_TABLE, sa.MetaData(), autoload_with=engine)
    with engine.begin() as conn:
        conn.execute(
            table.insert().values(
                workspace_id=1,
                factory_id=factory,
                node_ref=ref,
                name=name,
                source_hash="0" * 16,
                named_by=by,
                updated_at=100,
            )
        )


def test_round_trip_creates_the_table_and_matches_the_models(engine: sa.Engine) -> None:
    uri = _uri(engine)
    try:
        _migrate(uri, engine, _PREVIOUS)
        assert _TABLE not in _tables(engine)

        _migrate(uri, engine, "head")
        assert _TABLE in _tables(engine)
        pk = sa.inspect(engine).get_pk_constraint(_TABLE)["constrained_columns"]
        assert pk == ["workspace_id", "factory_id", "node_ref"]
        factory = os.urandom(16)
        _name(engine, factory, "feature:from-C-11", "Agent assistant")
        _name(engine, factory, "T-16", "Debit each turn", "ana@example.com")
        _name(engine, os.urandom(16), "T-16", "Another factory")
        with pytest.raises(IntegrityError):
            _name(engine, factory, "T-16", "Twice")
        with engine.connect() as conn:
            count = conn.execute(sa.text(f"SELECT count(*) FROM {_TABLE}")).scalar()
            context = MigrationContext.configure(conn, opts={"compare_type": True})
            diffs = compare_metadata(context, OmnigentBase.metadata)
        assert count == 3
        assert [d for d in diffs if _TABLE in repr(d)] == []

        _migrate(uri, engine, _PREVIOUS, down=True)
        assert _TABLE not in _tables(engine)
        _migrate(uri, engine, "head")
        assert _TABLE in _tables(engine)
    finally:
        engine.dispose()


def test_upgrade_skips_a_table_that_already_exists(engine: sa.Engine) -> None:
    # CockroachDB bootstraps from the models, then replays the revisions after its baseline.
    uri = _uri(engine)
    try:
        _migrate(uri, engine, _PREVIOUS)
        OmnigentBase.metadata.tables[_TABLE].create(engine)
        _migrate(uri, engine, "head")
        assert _TABLE in _tables(engine)
    finally:
        engine.dispose()


def test_revision_chains_off_the_node_reads() -> None:
    # test_migration_connections.test_single_alembic_head guards the single head.
    script = ScriptDirectory.from_config(_build_alembic_config("sqlite://"))
    revision = script.get_revision(_REVISION)
    assert revision is not None and revision.down_revision == _PREVIOUS
