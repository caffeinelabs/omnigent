"""add name_cksum to agents and policies

Revision ID: o1a2b3c4d5e6
Revises: n1a2b3c4d5e6
Create Date: 2026-07-07 00:00:00.000000

Replaces the text-based unique indexes on ``agents.name`` and
``policies.name`` with checksum-based ones so no unique index carries
raw variable-length text.

- Adds ``agents.name_cksum`` and ``policies.name_cksum`` (SHA-256 hex
  of the name).
- Backfills both from the existing names in Python — SQLite and
  Cloudflare D1 have no ``sha256()``/``md5()`` SQL function, so the
  digest is computed in the migration and written via parameterized
  ``UPDATE``s. The digest here is inlined (not imported from the app)
  so the backfill stays frozen if the app-side helper ever changes;
  both must produce an identical value.
- Swaps ``ix_agents_template_name`` (partial unique on ``name`` where
  ``session_id IS NULL``) for ``ix_agents_template_name_cksum`` on
  ``name_cksum`` with the same partial predicate, and
  ``uq_policies_session_id_name`` for
  ``uq_policies_session_id_name_cksum`` on ``(session_id, name_cksum)``.

The checksum columns are backfilled while nullable, then flipped to
NOT NULL. No new uniqueness violation is possible: the pre-existing
name indexes already forbade the duplicates that would collide on the
checksum.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "o1a2b3c4d5e6"
down_revision: str | None = "n1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _backfill_name_cksum(table: str) -> None:
    """
    Populate ``{table}.name_cksum`` from ``{table}.name`` in Python.

    :param table: ``"agents"`` or ``"policies"``.
    """
    bind = op.get_bind()
    rows = bind.execute(sa.text(f"SELECT id, name FROM {table}")).mappings().all()
    for row in rows:
        bind.execute(
            sa.text(f"UPDATE {table} SET name_cksum = :cksum WHERE id = :id"),
            {
                "cksum": hashlib.sha256(row["name"].encode()).hexdigest(),
                "id": row["id"],
            },
        )
    remaining = bind.execute(
        sa.text(f"SELECT COUNT(*) FROM {table} WHERE name_cksum IS NULL")
    ).scalar()
    if remaining and remaining > 0:
        raise RuntimeError(f"{table}.name_cksum backfill incomplete: {remaining} rows still NULL")


def upgrade() -> None:
    # Add the checksum columns nullable so existing rows can be
    # backfilled before the NOT NULL constraint is enforced.
    with op.batch_alter_table("agents") as batch_op:
        batch_op.add_column(sa.Column("name_cksum", sa.String(length=64), nullable=True))
    with op.batch_alter_table("policies") as batch_op:
        batch_op.add_column(sa.Column("name_cksum", sa.String(length=64), nullable=True))

    _backfill_name_cksum("agents")
    _backfill_name_cksum("policies")

    # agents: enforce NOT NULL, then swap the partial unique index off
    # ``name`` and onto ``name_cksum``. Index ops run on the bare op
    # (SQLite executes CREATE/DROP INDEX natively); only the NOT NULL
    # flip needs batch mode.
    op.drop_index("ix_agents_template_name", table_name="agents")
    with op.batch_alter_table("agents") as batch_op:
        batch_op.alter_column(
            "name_cksum",
            existing_type=sa.String(length=64),
            nullable=False,
        )
    op.create_index(
        "ix_agents_template_name_cksum",
        "agents",
        ["name_cksum"],
        unique=True,
        sqlite_where=sa.text("session_id IS NULL"),
        postgresql_where=sa.text("session_id IS NULL"),
    )

    # policies: enforce NOT NULL and swap the composite unique
    # constraint in a single batch rebuild.
    with op.batch_alter_table("policies") as batch_op:
        batch_op.alter_column(
            "name_cksum",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        batch_op.drop_constraint("uq_policies_session_id_name", type_="unique")
        batch_op.create_unique_constraint(
            "uq_policies_session_id_name_cksum",
            ["session_id", "name_cksum"],
        )


def downgrade() -> None:
    # policies: restore the name-based unique constraint and drop the
    # checksum column in one batch rebuild.
    with op.batch_alter_table("policies") as batch_op:
        batch_op.drop_constraint("uq_policies_session_id_name_cksum", type_="unique")
        batch_op.drop_column("name_cksum")
        batch_op.create_unique_constraint(
            "uq_policies_session_id_name",
            ["session_id", "name"],
        )

    # agents: drop the checksum index and column, then recreate the
    # original partial unique index on ``name``.
    op.drop_index("ix_agents_template_name_cksum", table_name="agents")
    with op.batch_alter_table("agents") as batch_op:
        batch_op.drop_column("name_cksum")
    op.create_index(
        "ix_agents_template_name",
        "agents",
        ["name"],
        unique=True,
        sqlite_where=sa.text("session_id IS NULL"),
        postgresql_where=sa.text("session_id IS NULL"),
    )
