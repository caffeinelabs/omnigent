"""add each factory project's home feature

Revision ID: fz7b8c9d0e1f
Revises: fz6a7b8c9d0e
Create Date: 2026-11-03 22:00:00.000000

The cockpit's levels give every project exactly one parent on the feature
level. Today the link lives only on the parent's side (an initiative's
``plan.projects``, a charter's ``why.proposal``), so the pointer moves onto
the child: one nullable column, ``factory_projects.feature``, holding the
``I-<n>`` of the initiative that planned the project, pays for it and
accepts it (its home). It is written when a projects proposal applies and
backfilled by the reconcile loop from the initiatives' plans
(:mod:`omnigent.server.factory.features`); NULL while the project's feature
is implicit (made from the proposal that created it).

Additive; the backfill runs in the server, so no data moves here. The column
is added only when missing: CockroachDB bootstraps from the current models
and then replays every revision after its baseline.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fz7b8c9d0e1f"
down_revision: str | None = "fz6a7b8c9d0e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "factory_projects"
_COLUMN = "feature"


def _existing() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def upgrade() -> None:
    """Add ``feature`` to ``factory_projects``."""
    if _COLUMN not in _existing():
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(16), nullable=True))


def downgrade() -> None:
    """Drop ``feature`` from ``factory_projects``."""
    if _COLUMN in _existing():
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.drop_column(_COLUMN)
