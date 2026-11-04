"""add factory node names

Revision ID: fz9d0e1f2a3b
Revises: fz8c9d0e1f2a
Create Date: 2026-11-04 16:00:00.000000

Cockpit v3 shows every node under a short name of two to six words; a
person's ask, a proposal's machine label or a long title stays in the node's
details. ``factory_node_names`` keeps one name per node: the name, a digest of
the text it was made from (the factory names a node again only when that text
changes) and who named it (``NULL``: the factory did; a person's name is never
replaced by the factory's). The node is named by its ref in the level tree,
which never changes when the node moves.

Conventions match ``factory_node_reads``: the primary key leads with
``workspace_id``, times are epoch seconds and there are no foreign keys. The
table is created only when missing: CockroachDB bootstraps from the current
models and then replays every revision after its baseline.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "fz9d0e1f2a3b"
down_revision: str | None = "fz8c9d0e1f2a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "factory_node_names"


def upgrade() -> None:
    """Create ``factory_node_names`` unless it exists."""
    if _TABLE in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        _TABLE,
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("factory_id", Uuid16(), nullable=False),
        sa.Column("node_ref", sa.String(96), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("source_hash", sa.String(32), nullable=False),
        sa.Column("named_by", sa.String(128), nullable=True),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "factory_id", "node_ref"),
    )


def downgrade() -> None:
    """Drop ``factory_node_names``."""
    op.drop_table(_TABLE)
