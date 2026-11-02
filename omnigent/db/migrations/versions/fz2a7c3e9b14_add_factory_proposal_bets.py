"""add a bet to factory projects proposals

Revision ID: fz2a7c3e9b14
Revises: fz1a2b3c4d5e
Create Date: 2026-11-02 00:00:00.000000

A projects proposal (``C-n``) can carry a bet: the opportunity it answers
(``O-n``), the bet itself (kind, hypothesis, metric, expected range, flag,
measurement plan, kill rule; frozen into ``charter.why`` of every project the
proposal creates) and the peer lead's case against it. Three nullable columns
on ``factory_proposals``; JSON is a compressed blob, as in ``fz1a2b3c4d5e``.

Additive; no backfill. Columns are added only when missing: CockroachDB
bootstraps from the current models and then replays every revision after its
baseline.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fz2a7c3e9b14"
down_revision: str | None = "fz1a2b3c4d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "factory_proposals"
_COLUMNS = ("opportunity", "bet", "case_against")


def _existing() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def upgrade() -> None:
    """Add ``opportunity``, ``bet`` and ``case_against`` to ``factory_proposals``."""
    existing = _existing()
    columns = {
        "opportunity": sa.Column("opportunity", sa.String(16), nullable=True),
        "bet": sa.Column("bet", sa.LargeBinary(), nullable=True),
        "case_against": sa.Column("case_against", sa.LargeBinary(), nullable=True),
    }
    for name in _COLUMNS:
        if name not in existing:
            op.add_column(_TABLE, columns[name])


def downgrade() -> None:
    """Drop the bet columns from ``factory_proposals``."""
    existing = _existing()
    with op.batch_alter_table(_TABLE) as batch_op:
        for name in reversed(_COLUMNS):
            if name in existing:
                batch_op.drop_column(name)
