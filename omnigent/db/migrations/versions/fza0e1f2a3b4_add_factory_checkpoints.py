"""add factory checkpoints

Revision ID: fza0e1f2a3b4
Revises: fz9d0e1f2a3b
Create Date: 2026-11-04 20:00:00.000000

Every decision point is a checkpoint a person can see and change direction
from. ``factory_checkpoints`` keeps one row (``K-<number>`` per factory) per
decision the factory makes itself (an agent's own, or the server's on an
agent's ask or a proposal) and per change of direction a person makes: who
decided, the question, the options considered, the choice, a one-line why and
a confidence, the node it is scoped to, its effects (refs with their undo or
revert routes), the facts and definition version at the time, the policy's
verdict and its status (``decided``, ``asked``, ``answered``, ``changed``).
A person's answers (``D-n``) and applied proposals (``C-n``) are read as
checkpoints through an adapter: no history is copied.

Conventions match ``fz6a7b8c9d0e``: the primary key leads with
``workspace_id``, enum-like columns are SMALLINT codes with named CHECK
constraints (``omnigent.db.enum_codecs``), JSON is a compressed blob, times
are epoch seconds, and there are no foreign keys. The table and its index
are created only when missing: CockroachDB bootstraps from the current
models and then replays every revision after its baseline.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "fza0e1f2a3b4"
down_revision: str | None = "fz9d0e1f2a3b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "factory_checkpoints"
_INDEX = "ix_factory_checkpoints_created"


def _blob(name: str) -> sa.Column:
    return sa.Column(name, sa.LargeBinary(), nullable=True)


def upgrade() -> None:
    """Create ``factory_checkpoints`` and its index, skipping what exists."""
    inspector = sa.inspect(op.get_bind())
    if _TABLE not in set(inspector.get_table_names()):
        op.create_table(
            _TABLE,
            sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("id", Uuid16(), nullable=False),
            sa.Column("factory_id", Uuid16(), nullable=False),
            sa.Column("number", sa.Integer(), nullable=False),
            # FACTORY_CHECKPOINT_STATUS: decided=1, asked=2, answered=3, changed=4.
            sa.Column("status", sa.SmallInteger(), nullable=False),
            # FACTORY_CHECKPOINT_BY: agent=1, server=2, person=3.
            sa.Column("decided_by_kind", sa.SmallInteger(), nullable=False),
            sa.Column("decided_by", sa.String(128), nullable=True),
            sa.Column("decided_by_name", sa.String(120), nullable=True),
            sa.Column("question", sa.String(1000), nullable=False),
            sa.Column("node_ref", sa.String(96), nullable=True),
            sa.Column("project_slug", sa.String(64), nullable=True),
            _blob("options"),
            sa.Column("choice_id", sa.String(16), nullable=True),
            sa.Column("choice_label", sa.String(200), nullable=True),
            _blob("direction"),
            sa.Column("why", sa.String(300), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("decision_class", sa.String(32), nullable=True),
            _blob("policy"),
            _blob("effects"),
            _blob("facts"),
            sa.Column("decision_id", Uuid16(), nullable=True),
            sa.Column("proposal_id", Uuid16(), nullable=True),
            sa.Column("overrides", sa.String(32), nullable=True),
            sa.Column("changed_by", sa.String(32), nullable=True),
            sa.Column("asked_by_session_id", Uuid16(), nullable=True),
            sa.Column("dedupe_key", sa.String(64), nullable=True),
            sa.Column("created_at", sa.Integer(), nullable=False),
            sa.Column("updated_at", sa.Integer(), nullable=False),
            sa.CheckConstraint("status IN (1, 2, 3, 4)", name="ck_factory_checkpoints_status"),
            sa.CheckConstraint("decided_by_kind IN (1, 2, 3)", name="ck_factory_checkpoints_by"),
            sa.UniqueConstraint(
                "workspace_id", "factory_id", "number", name="uq_factory_checkpoints_number"
            ),
            sa.PrimaryKeyConstraint("workspace_id", "id"),
        )
        inspector = sa.inspect(op.get_bind())
    if _INDEX not in {index["name"] for index in inspector.get_indexes(_TABLE)}:
        op.create_index(_INDEX, _TABLE, ["workspace_id", "factory_id", "created_at"], unique=False)


def downgrade() -> None:
    """Drop ``factory_checkpoints`` (decisions and proposals keep their rows)."""
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_table(_TABLE)
