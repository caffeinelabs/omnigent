"""add the hold a lead puts on a factory task

Revision ID: fz6f7a8b9c0d
Revises: fz5e6f7a8b9c
Create Date: 2026-11-03 18:00:00.000000

A project lead holds a task on purpose and says on what
(``sys_factory_task(op="hold", key, on="C-16")``): one nullable compressed
JSON column, ``factory_tasks.hold``, holding ``{on, kind, ref, note, until,
by, by_kind, since}``. While it holds, the loop engine's stall clock waits
and the task shows "held: waiting on …" instead of a stall decision.

Additive; no backfill. The column is added only when missing: CockroachDB
bootstraps from the current models and then replays every revision after its
baseline.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fz6f7a8b9c0d"
down_revision: str | None = "fz5e6f7a8b9c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "factory_tasks"
_COLUMN = "hold"


def _existing() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def upgrade() -> None:
    """Add ``hold`` to ``factory_tasks``."""
    if _COLUMN not in _existing():
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    """Drop ``hold`` from ``factory_tasks``."""
    if _COLUMN in _existing():
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.drop_column(_COLUMN)
