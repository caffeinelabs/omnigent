"""Tests for the factory proposal-bets migration (fz2a7c3e9b14).

Upgrades from the round-3 revision, checks the three new nullable columns on
``factory_proposals``, that an existing proposal survives with them empty,
that the ORM models match the migrated schema (CockroachDB bootstraps from the
models), that re-running the upgrade over present columns is a no-op, and
that it downgrades cleanly. Runs on SQLite.
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

from omnigent.db.db_models import OmnigentBase
from omnigent.db.utils import _build_alembic_config

_PREVIOUS = "fz1a2b3c4d5e"
_REVISION = "fz2a7c3e9b14"
_COLUMNS = {"opportunity", "bet", "case_against"}


def _migrate(uri: str, engine: sa.Engine, revision: str, *, down: bool = False) -> None:
    config = _build_alembic_config(uri)
    with engine.connect() as conn:
        config.attributes["connection"] = conn
        (command.downgrade if down else command.upgrade)(config, revision)
        conn.commit()


@pytest.fixture
def engine(tmp_path: Path) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{tmp_path / 'proposal_bets.db'}")


def _uri(engine: sa.Engine) -> str:
    return engine.url.render_as_string(hide_password=False)


def _columns(engine: sa.Engine) -> dict[str, dict]:
    return {c["name"]: c for c in sa.inspect(engine).get_columns("factory_proposals")}


def _seed_proposal(engine: sa.Engine) -> None:
    table = sa.Table("factory_proposals", sa.MetaData(), autoload_with=engine)
    with engine.begin() as conn:
        conn.execute(
            table.insert().values(
                workspace_id=1,
                id=os.urandom(16),
                factory_id=os.urandom(16),
                number=1,
                kind=1,
                approval=2,
                title="create db-version",
                ops=b"[]",
                rationale=b"",
                author_kind=2,
                author="lead",
                created_at=1,
                updated_at=1,
            )
        )


def test_round_trip_keeps_rows_and_matches_the_models(engine: sa.Engine) -> None:
    uri = _uri(engine)
    try:
        _migrate(uri, engine, _PREVIOUS)
        assert not _COLUMNS & set(_columns(engine))
        _seed_proposal(engine)

        _migrate(uri, engine, "head")
        columns = _columns(engine)
        assert set(columns) >= _COLUMNS
        assert all(columns[name]["nullable"] for name in _COLUMNS)
        with engine.connect() as conn:
            row = conn.execute(
                sa.text("SELECT number, opportunity, bet, case_against FROM factory_proposals")
            ).one()
        assert tuple(row) == (1, None, None, None)
        with engine.connect() as conn:
            context = MigrationContext.configure(conn, opts={"compare_type": True})
            diffs = compare_metadata(context, OmnigentBase.metadata)
        assert [d for d in diffs if "factory_proposals" in repr(d)] == []

        _migrate(uri, engine, _PREVIOUS, down=True)
        assert not _COLUMNS & set(_columns(engine))
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT count(*) FROM factory_proposals")).scalar() == 1
    finally:
        engine.dispose()


def test_upgrade_skips_columns_that_already_exist(engine: sa.Engine) -> None:
    # CockroachDB bootstraps from the models, then replays the revisions after its baseline.
    uri = _uri(engine)
    try:
        _migrate(uri, engine, _PREVIOUS)
        with engine.begin() as conn:
            conn.execute(
                sa.text("ALTER TABLE factory_proposals ADD COLUMN opportunity VARCHAR(16)")
            )
        _migrate(uri, engine, "head")
        assert set(_columns(engine)) >= _COLUMNS
    finally:
        engine.dispose()


def test_revision_is_the_single_head() -> None:
    script = ScriptDirectory.from_config(_build_alembic_config("sqlite://"))
    revision = script.get_revision(_REVISION)
    assert revision is not None and revision.down_revision == _PREVIOUS
    assert script.get_heads() == [_REVISION]
