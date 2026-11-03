"""add the project lead's structured update to factory projects

Revision ID: fz4c5d6e7f8a
Revises: fz3d4e5f6a7b
Create Date: 2026-11-03 06:00:00.000000

A project lead posts a structured update on its project
(``sys_factory_set(update={health, done, next, blocker})``): one nullable
compressed JSON column, ``factory_projects.lead_update``, holding the latest
one with when and who. The cockpit shows it next to the health the server
computes from facts (checks, budget, stuck members), which overrides the
lead's "on track" when the facts disagree.

Additive; no backfill. The column is added only when missing: CockroachDB
bootstraps from the current models and then replays every revision after its
baseline.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fz4c5d6e7f8a"
down_revision: str | None = "fz3d4e5f6a7b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "factory_projects"
_COLUMN = "lead_update"


def _existing() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def upgrade() -> None:
    """Add ``lead_update`` to ``factory_projects``."""
    if _COLUMN not in _existing():
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    """Drop ``lead_update`` from ``factory_projects``."""
    if _COLUMN in _existing():
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.drop_column(_COLUMN)
