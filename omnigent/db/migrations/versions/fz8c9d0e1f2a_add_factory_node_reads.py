"""add factory node reads

Revision ID: fz8c9d0e1f2a
Revises: fz7b8c9d0e1f
Create Date: 2026-11-04 12:00:00.000000

Cockpit v3 says what changed "since your last look" at a node: the roll-up
lists the most significant changes in the node's subtree and each child names
its own. ``factory_node_reads`` keeps one row per person per node: when they
last looked at it (``seen_at``, epoch seconds, only moving forward). The node
is named by its ref in the level tree (``product``, ``I-1``, a project slug,
``T-16`` …), which never changes when the node moves.

Conventions match ``factory_chat_reads``: the primary key leads with
``workspace_id``, times are epoch seconds and there are no foreign keys. The
table is created only when missing: CockroachDB bootstraps from the current
models and then replays every revision after its baseline.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "fz8c9d0e1f2a"
down_revision: str | None = "fz7b8c9d0e1f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "factory_node_reads"


def upgrade() -> None:
    """Create ``factory_node_reads`` unless it exists."""
    if _TABLE in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        _TABLE,
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("factory_id", Uuid16(), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("node_ref", sa.String(96), nullable=False),
        sa.Column("seen_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "factory_id", "user_id", "node_ref"),
    )


def downgrade() -> None:
    """Drop ``factory_node_reads``."""
    op.drop_table(_TABLE)
