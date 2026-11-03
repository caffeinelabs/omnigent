"""Tests for the project lead's update migration (fz4c5d6e7f8a).

Upgrades from the discovery-ledger revision, checks the new nullable
``lead_update`` column on ``factory_projects``, that an existing project
survives with it empty, that the ORM models match the migrated schema
(CockroachDB bootstraps from the models), that re-running the upgrade over a
present column is a no-op, and that it downgrades cleanly. Runs on SQLite.
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

_PREVIOUS = "fz3d4e5f6a7b"
_REVISION = "fz4c5d6e7f8a"
_COLUMN = "lead_update"


def _migrate(uri: str, engine: sa.Engine, revision: str, *, down: bool = False) -> None:
    config = _build_alembic_config(uri)
    with engine.connect() as conn:
        config.attributes["connection"] = conn
        (command.downgrade if down else command.upgrade)(config, revision)
        conn.commit()


@pytest.fixture
def engine(tmp_path: Path) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{tmp_path / 'lead_update.db'}")


def _uri(engine: sa.Engine) -> str:
    return engine.url.render_as_string(hide_password=False)


def _columns(engine: sa.Engine) -> dict[str, dict]:
    return {c["name"]: c for c in sa.inspect(engine).get_columns("factory_projects")}


def _seed_project(engine: sa.Engine) -> None:
    table = sa.Table("factory_projects", sa.MetaData(), autoload_with=engine)
    with engine.begin() as conn:
        conn.execute(
            table.insert().values(
                workspace_id=1,
                id=os.urandom(16),
                factory_id=os.urandom(16),
                slug="csv-export",
                title="(sample) CSV export",
                priority=2,
                stage=1,
                stage_changed_at=1,
                created_by="alice",
                created_at=1,
                updated_at=1,
                status=1,
            )
        )


def test_round_trip_keeps_rows_and_matches_the_models(engine: sa.Engine) -> None:
    uri = _uri(engine)
    try:
        _migrate(uri, engine, _PREVIOUS)
        assert _COLUMN not in _columns(engine)
        _seed_project(engine)

        _migrate(uri, engine, "head")
        columns = _columns(engine)
        assert _COLUMN in columns and columns[_COLUMN]["nullable"]
        with engine.connect() as conn:
            row = conn.execute(sa.text("SELECT slug, lead_update FROM factory_projects")).one()
        assert tuple(row) == ("csv-export", None)
        with engine.connect() as conn:
            context = MigrationContext.configure(conn, opts={"compare_type": True})
            diffs = compare_metadata(context, OmnigentBase.metadata)
        assert [d for d in diffs if "factory_projects" in repr(d)] == []

        _migrate(uri, engine, _PREVIOUS, down=True)
        assert _COLUMN not in _columns(engine)
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT count(*) FROM factory_projects")).scalar() == 1
    finally:
        engine.dispose()


def test_upgrade_skips_a_column_that_already_exists(engine: sa.Engine) -> None:
    # CockroachDB bootstraps from the models, then replays the revisions after its baseline.
    uri = _uri(engine)
    try:
        _migrate(uri, engine, _PREVIOUS)
        with engine.begin() as conn:
            conn.execute(sa.text("ALTER TABLE factory_projects ADD COLUMN lead_update BLOB"))
        _migrate(uri, engine, "head")
        assert _COLUMN in _columns(engine)
    finally:
        engine.dispose()


def test_revision_chains_off_the_discovery_ledger() -> None:
    # test_migration_connections.test_single_alembic_head guards the single head.
    script = ScriptDirectory.from_config(_build_alembic_config("sqlite://"))
    revision = script.get_revision(_REVISION)
    assert revision is not None and revision.down_revision == _PREVIOUS
