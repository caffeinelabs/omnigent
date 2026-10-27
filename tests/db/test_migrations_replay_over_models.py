"""Guard that post-baseline migrations replay cleanly over a models-built schema.

CockroachDB bootstraps from the ORM models and stamps head, so every revision
after ``CRDB_BASELINE_REVISION`` must tolerate objects that already exist: a
CockroachDB database stamped at the baseline (or any later revision) replays
them over tables the models already created. The live check in
``test_cockroachdb.py`` needs a CockroachDB server; this one replays the same
segment on SQLite so the default lane catches a migration that is not
idempotent before it reaches the CockroachDB lane.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import command

from omnigent.db.cockroachdb import CRDB_BASELINE_REVISION
from omnigent.db.db_models import ConversationBase, OmnigentBase
from omnigent.db.utils import (
    _build_alembic_config,
    _get_current_db_revision,
    _get_head_db_revision,
)


def _schema(engine: sa.Engine) -> dict[str, tuple[Any, ...]]:
    """Reflect every table's columns, keys, indexes and CHECK constraints."""
    inspector = sa.inspect(engine)
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
                (str(unique["name"]), tuple(unique["column_names"]))
                for unique in inspector.get_unique_constraints(table)
            ),
            sorted(
                (str(check["name"]), check["sqltext"])
                for check in inspector.get_check_constraints(table)
            ),
        )
        for table in sorted(inspector.get_table_names())
        if table != "alembic_version"
    }


def test_post_baseline_migrations_are_no_ops_over_the_models_schema(tmp_path: Path) -> None:
    uri = f"sqlite:///{tmp_path / 'models.db'}"
    engine = sa.create_engine(uri)
    try:
        for base in (OmnigentBase, ConversationBase):
            base.metadata.create_all(engine)
        models_schema = _schema(engine)

        config = _build_alembic_config(uri)
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.stamp(config, CRDB_BASELINE_REVISION)
            command.upgrade(config, "head")
            connection.commit()

        assert _get_current_db_revision(engine) == _get_head_db_revision(uri)
        assert _schema(engine) == models_schema
    finally:
        engine.dispose()
