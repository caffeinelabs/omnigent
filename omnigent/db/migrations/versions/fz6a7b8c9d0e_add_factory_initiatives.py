"""add factory initiatives

Revision ID: fz6a7b8c9d0e
Revises: fz6f7a8b9c0d
Create Date: 2026-11-03 15:00:00.000000

An ask bigger than one project becomes one outcome: ``factory_initiatives``
holds one row per initiative (``I-<number>`` per factory) with the person's
ask verbatim, who asked, the plan (the user-visible acceptance steps, the
walking skeleton, the waves and the projects in it) and the newest
acceptance runs on the base env. Its state is computed from its projects;
``status`` only records a person's close or reopen.

Conventions match ``fz5e6f7a8b9c``: the primary key leads with
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

revision: str = "fz6a7b8c9d0e"
down_revision: str | None = "fz6f7a8b9c0d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "factory_initiatives"
_INDEX = "ix_factory_initiatives_status"


def _blob(name: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.LargeBinary(), nullable=nullable)


def upgrade() -> None:
    """Create ``factory_initiatives`` and its index, skipping what exists."""
    inspector = sa.inspect(op.get_bind())
    if _TABLE not in set(inspector.get_table_names()):
        op.create_table(
            _TABLE,
            sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("id", Uuid16(), nullable=False),
            sa.Column("factory_id", Uuid16(), nullable=False),
            sa.Column("number", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(120), nullable=False),
            _blob("ask", nullable=False),
            # FACTORY_INITIATIVE_STATUS: open=1, done=2, cancelled=3.
            sa.Column("status", sa.SmallInteger(), nullable=False),
            _blob("requested_by"),
            sa.Column("ask_ref", sa.String(64), nullable=True),
            sa.Column("thread", sa.String(128), nullable=True),
            sa.Column("outcome", sa.String(280), nullable=True),
            _blob("plan"),
            _blob("runs"),
            sa.Column("created_by", sa.String(128), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("closed_at", sa.Integer(), nullable=True),
            sa.Column("closed_by", sa.String(128), nullable=True),
            sa.Column("created_at", sa.Integer(), nullable=False),
            sa.Column("updated_at", sa.Integer(), nullable=False),
            sa.CheckConstraint("status IN (1, 2, 3)", name="ck_factory_initiatives_status"),
            sa.UniqueConstraint(
                "workspace_id", "factory_id", "number", name="uq_factory_initiatives_number"
            ),
            sa.PrimaryKeyConstraint("workspace_id", "id"),
        )
        inspector = sa.inspect(op.get_bind())
    if _INDEX not in {index["name"] for index in inspector.get_indexes(_TABLE)}:
        op.create_index(_INDEX, _TABLE, ["workspace_id", "factory_id", "status"], unique=False)


def downgrade() -> None:
    """Drop ``factory_initiatives`` (the projects keep their rows and charters)."""
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_table(_TABLE)
