"""Tests for the factory readouts migration (fz5e6f7a8b9c).

Upgrades from the lead-update revision, checks ``factory_readouts`` and the
widened decision sources (``readout`` = 10), that the ORM models match the
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

_PREVIOUS = "fz4c5d6e7f8a"
_REVISION = "fz5e6f7a8b9c"
_TABLE = "factory_readouts"


def _migrate(uri: str, engine: sa.Engine, revision: str, *, down: bool = False) -> None:
    config = _build_alembic_config(uri)
    with engine.connect() as conn:
        config.attributes["connection"] = conn
        (command.downgrade if down else command.upgrade)(config, revision)
        conn.commit()


@pytest.fixture
def engine(tmp_path: Path) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{tmp_path / 'readouts.db'}")


def _uri(engine: sa.Engine) -> str:
    return engine.url.render_as_string(hide_password=False)


def _tables(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names())


def _decision(engine: sa.Engine, source: int, number: int) -> None:
    table = sa.Table("factory_decisions", sa.MetaData(), autoload_with=engine)
    with engine.begin() as conn:
        conn.execute(
            table.insert().values(
                workspace_id=1,
                id=os.urandom(16),
                factory_id=os.urandom(16),
                number=number,
                dedupe_key=f"readout:{number}",
                question="Keep or drop C-1? (sample)",
                options=b"[]",
                source=source,
                status=1,
                blocking=0,
                allow_text=0,
                created_at=1,
                updated_at=1,
            )
        )


def _readout(engine: sa.Engine, **over: object) -> None:
    table = sa.Table(_TABLE, sa.MetaData(), autoload_with=engine)
    row = {
        "workspace_id": 1,
        "id": os.urandom(16),
        "factory_id": b"f" * 16,
        "proposal": 31,
        "day": 7,
        "status": 1,
        "method": 4,
        "released_at": 1,
        "due_at": 2,
        "created_at": 1,
        "updated_at": 1,
        **over,
    }
    with engine.begin() as conn:
        conn.execute(table.insert().values(**row))


def test_round_trip_creates_readouts_and_matches_the_models(engine: sa.Engine) -> None:
    uri = _uri(engine)
    try:
        _migrate(uri, engine, _PREVIOUS)
        assert _TABLE not in _tables(engine)
        with pytest.raises(IntegrityError):
            _decision(engine, 10, 1)

        _migrate(uri, engine, "head")
        assert _TABLE in _tables(engine)
        _decision(engine, 10, 2)
        with pytest.raises(IntegrityError):
            _decision(engine, 11, 3)
        with engine.connect() as conn:
            context = MigrationContext.configure(conn, opts={"compare_type": True})
            diffs = compare_metadata(context, OmnigentBase.metadata)
        touched = (_TABLE, "factory_decisions")
        assert [d for d in diffs if any(t in repr(d) for t in touched)] == []

        _migrate(uri, engine, _PREVIOUS, down=True)
        assert _TABLE not in _tables(engine)
        with engine.connect() as conn:
            # A decision about a readout goes with the widened CHECK.
            count = conn.execute(sa.text("SELECT count(*) FROM factory_decisions")).scalar()
            assert count == 0
    finally:
        engine.dispose()


def test_readout_checks_and_one_row_per_bet_and_day(engine: sa.Engine) -> None:
    uri = _uri(engine)
    try:
        _migrate(uri, engine, "head")
        _readout(engine)
        with pytest.raises(IntegrityError):
            _readout(engine)  # the same bet and day again
        _readout(engine, day=28, verdict=1, recommendation=1, decision=1)
        for column, code in (
            ("status", 6),
            ("method", 6),
            ("verdict", 5),
            ("recommendation", 5),
            ("decision", 5),
        ):
            with pytest.raises(IntegrityError):
                _readout(engine, day=90, **{column: code})
    finally:
        engine.dispose()


def test_upgrade_skips_a_readouts_table_that_already_exists(engine: sa.Engine) -> None:
    # CockroachDB bootstraps from the models, then replays the revisions after its baseline.
    uri = _uri(engine)
    try:
        _migrate(uri, engine, _PREVIOUS)
        OmnigentBase.metadata.tables[_TABLE].create(engine)
        _migrate(uri, engine, "head")
        indexes = {i["name"] for i in sa.inspect(engine).get_indexes(_TABLE)}
        assert "ix_factory_readouts_due" in indexes
        _decision(engine, 10, 4)
    finally:
        engine.dispose()


def test_revision_chains_off_the_lead_update_revision() -> None:
    # test_migration_connections.test_single_alembic_head guards the single head.
    script = ScriptDirectory.from_config(_build_alembic_config("sqlite://"))
    revision = script.get_revision(_REVISION)
    assert revision is not None and revision.down_revision == _PREVIOUS
