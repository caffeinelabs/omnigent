"""Tests for the factory initiatives migration (fz6a7b8c9d0e).

Upgrades from the task-hold revision, checks ``factory_initiatives`` (its
status CHECK and one number per factory), that the ORM model matches the
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

_PREVIOUS = "fz6f7a8b9c0d"
_REVISION = "fz6a7b8c9d0e"
_TABLE = "factory_initiatives"
_FACTORY = b"f" * 16


def _migrate(uri: str, engine: sa.Engine, revision: str, *, down: bool = False) -> None:
    config = _build_alembic_config(uri)
    with engine.connect() as conn:
        config.attributes["connection"] = conn
        (command.downgrade if down else command.upgrade)(config, revision)
        conn.commit()


@pytest.fixture
def engine(tmp_path: Path) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{tmp_path / 'initiatives.db'}")


def _uri(engine: sa.Engine) -> str:
    return engine.url.render_as_string(hide_password=False)


def _tables(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names())


def _initiative(engine: sa.Engine, **over: object) -> None:
    table = sa.Table(_TABLE, sa.MetaData(), autoload_with=engine)
    row = {
        "workspace_id": 1,
        "id": os.urandom(16),
        "factory_id": _FACTORY,
        "number": 1,
        "title": "Sample assistant",
        "ask": b"Build an assistant (sample)",
        "status": 1,
        "created_at": 1,
        "updated_at": 1,
        **over,
    }
    with engine.begin() as conn:
        conn.execute(table.insert().values(**row))


def test_round_trip_creates_initiatives_and_matches_the_models(engine: sa.Engine) -> None:
    uri = _uri(engine)
    try:
        _migrate(uri, engine, _PREVIOUS)
        assert _TABLE not in _tables(engine)

        _migrate(uri, engine, "head")
        assert _TABLE in _tables(engine)
        _initiative(engine)
        with engine.connect() as conn:
            version = conn.execute(sa.text(f"SELECT version FROM {_TABLE}")).scalar()
            context = MigrationContext.configure(conn, opts={"compare_type": True})
            diffs = compare_metadata(context, OmnigentBase.metadata)
        assert version == 1
        assert [d for d in diffs if _TABLE in repr(d)] == []

        _migrate(uri, engine, _PREVIOUS, down=True)
        assert _TABLE not in _tables(engine)
        _migrate(uri, engine, "head")
        assert _TABLE in _tables(engine)
    finally:
        engine.dispose()


def test_status_check_and_one_number_per_factory(engine: sa.Engine) -> None:
    uri = _uri(engine)
    try:
        _migrate(uri, engine, "head")
        _initiative(engine)
        with pytest.raises(IntegrityError):
            _initiative(engine)  # I-1 again
        _initiative(engine, number=2, status=3)
        _initiative(engine, factory_id=b"g" * 16)  # another factory's I-1
        with pytest.raises(IntegrityError):
            _initiative(engine, number=3, status=4)
    finally:
        engine.dispose()


def test_upgrade_skips_a_table_that_already_exists(engine: sa.Engine) -> None:
    # CockroachDB bootstraps from the models, then replays the revisions after its baseline.
    uri = _uri(engine)
    try:
        _migrate(uri, engine, _PREVIOUS)
        OmnigentBase.metadata.tables[_TABLE].create(engine)
        _migrate(uri, engine, "head")
        indexes = {i["name"] for i in sa.inspect(engine).get_indexes(_TABLE)}
        assert "ix_factory_initiatives_status" in indexes
    finally:
        engine.dispose()


def test_revision_chains_off_the_task_hold_revision() -> None:
    # test_migration_connections.test_single_alembic_head guards the single head.
    script = ScriptDirectory.from_config(_build_alembic_config("sqlite://"))
    revision = script.get_revision(_REVISION)
    assert revision is not None and revision.down_revision == _PREVIOUS
