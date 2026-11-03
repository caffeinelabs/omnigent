"""add factory readouts

Revision ID: fz5e6f7a8b9c
Revises: fz4c5d6e7f8a
Create Date: 2026-11-03 12:00:00.000000

Discovery closes its loop: ``factory_readouts`` holds one row per bet (its
``C-n`` number) per readout day: the plan it measured, what the server
measured, the verdict and recommendation fixed rules set, and a person's
keep or drop.

It also widens ``ck_factory_decisions_source`` to the decision source
``readout`` (10): a measured bet waits in "Needs you" for its keep or drop.

Conventions match ``fz3d4e5f6a7b``: the primary key leads with
``workspace_id``, enum-like columns are SMALLINT codes with named CHECK
constraints (``omnigent.db.enum_codecs``), JSON is a compressed blob, times
are epoch seconds, and there are no foreign keys. The table, its index and
the CHECK are created only when missing: CockroachDB bootstraps from the
current models and then replays every revision after its baseline.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "fz5e6f7a8b9c"
down_revision: str | None = "fz4c5d6e7f8a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "factory_readouts"
_INDEX = "ix_factory_readouts_due"
_SOURCE_CHECK = "ck_factory_decisions_source"
# CockroachDB parks the narrow CHECK under this name before dropping it.
_NARROW_SOURCE_CHECK = "ck_factory_decisions_source_pre_readout"
_WIDE = "source IN (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)"
_NARROW = "source IN (1, 2, 3, 4, 5, 6, 7, 8, 9)"


def _check_codes(inspector: sa.Inspector, table: str, name: str) -> set[int] | None:
    """Return the integer codes a named CHECK lists, or ``None`` when it is absent."""
    for check in inspector.get_check_constraints(table):
        if check["name"] == name:
            return {int(code) for code in re.findall(r"\b\d+\b", check["sqltext"])}
    return None


def _blob(name: str) -> sa.Column:
    return sa.Column(name, sa.LargeBinary(), nullable=True)


def _create_readouts(inspector: sa.Inspector) -> None:
    if _TABLE not in set(inspector.get_table_names()):
        op.create_table(
            _TABLE,
            sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("id", Uuid16(), nullable=False),
            sa.Column("factory_id", Uuid16(), nullable=False),
            sa.Column("proposal", sa.Integer(), nullable=False),
            sa.Column("day", sa.Integer(), nullable=False),
            sa.Column("opportunity", sa.Integer(), nullable=True),
            # FACTORY_READOUT_STATUS: scheduled=1, awaiting_answer=2, written=3,
            # decided=4, cancelled=5.
            sa.Column("status", sa.SmallInteger(), nullable=False),
            # FACTORY_READOUT_METHOD: controlled=1, before_after=2, adoption=3,
            # signal_trend=4, human_verdict=5.
            sa.Column("method", sa.SmallInteger(), nullable=False),
            sa.Column("plan_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("released_at", sa.Integer(), nullable=False),
            sa.Column("due_at", sa.Integer(), nullable=False),
            sa.Column("written_at", sa.Integer(), nullable=True),
            # FACTORY_READOUT_VERDICT: won=1, flat=2, lost=3, inconclusive=4.
            sa.Column("verdict", sa.SmallInteger(), nullable=True),
            # FACTORY_READOUT_RECOMMENDATION: keep=1, iterate=2, roll_back=3, extend=4.
            sa.Column("recommendation", sa.SmallInteger(), nullable=True),
            # FACTORY_READOUT_DECISION: keep=1, drop=2, iterate=3, extend=4.
            sa.Column("decision", sa.SmallInteger(), nullable=True),
            sa.Column("decided_by", sa.String(128), nullable=True),
            sa.Column("decided_at", sa.Integer(), nullable=True),
            sa.Column("signal", sa.Integer(), nullable=True),
            sa.Column("decision_id", Uuid16(), nullable=True),
            _blob("plan"),
            _blob("result"),
            _blob("answer"),
            _blob("review"),
            _blob("stamp"),
            sa.Column("created_at", sa.Integer(), nullable=False),
            sa.Column("updated_at", sa.Integer(), nullable=False),
            sa.CheckConstraint("status IN (1, 2, 3, 4, 5)", name="ck_factory_readouts_status"),
            sa.CheckConstraint("method IN (1, 2, 3, 4, 5)", name="ck_factory_readouts_method"),
            sa.CheckConstraint("verdict IN (1, 2, 3, 4)", name="ck_factory_readouts_verdict"),
            sa.CheckConstraint(
                "recommendation IN (1, 2, 3, 4)", name="ck_factory_readouts_recommendation"
            ),
            sa.CheckConstraint("decision IN (1, 2, 3, 4)", name="ck_factory_readouts_decision"),
            sa.UniqueConstraint(
                "workspace_id", "factory_id", "proposal", "day", name="uq_factory_readouts_day"
            ),
            sa.PrimaryKeyConstraint("workspace_id", "id"),
        )
        inspector = sa.inspect(op.get_bind())
    if _INDEX not in {index["name"] for index in inspector.get_indexes(_TABLE)}:
        op.create_index(
            _INDEX, _TABLE, ["workspace_id", "factory_id", "status", "due_at"], unique=False
        )


def _widen_decision_sources(inspector: sa.Inspector) -> None:
    """``ck_factory_decisions_source`` gains readout=10."""
    codes = _check_codes(inspector, "factory_decisions", _SOURCE_CHECK)
    stale = _check_codes(inspector, "factory_decisions", _NARROW_SOURCE_CHECK) is not None
    widen = codes is None or 10 not in codes
    if not widen and not stale:
        return
    narrow = _SOURCE_CHECK
    if widen and codes is not None and op.get_bind().dialect.name == "cockroachdb":
        # CockroachDB rejects re-adding a CHECK name dropped in the same transaction.
        narrow = _NARROW_SOURCE_CHECK
        op.execute(f"ALTER TABLE factory_decisions RENAME CONSTRAINT {_SOURCE_CHECK} TO {narrow}")
    with op.batch_alter_table("factory_decisions") as batch_op:
        if widen:
            if codes is not None:
                batch_op.drop_constraint(narrow, type_="check")
            batch_op.create_check_constraint(_SOURCE_CHECK, _WIDE)
        if stale:
            batch_op.drop_constraint(_NARROW_SOURCE_CHECK, type_="check")


def upgrade() -> None:
    """Create ``factory_readouts`` and widen the decision sources, skipping what exists."""
    inspector = sa.inspect(op.get_bind())
    _create_readouts(inspector)
    _widen_decision_sources(sa.inspect(op.get_bind()))


def downgrade() -> None:
    """Drop the readouts; decisions about a readout go first (the narrow CHECK rejects them)."""
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_table(_TABLE)
    op.execute("DELETE FROM factory_decisions WHERE source = 10")
    with op.batch_alter_table("factory_decisions") as batch_op:
        # Re-adding the same CHECK name fails on CockroachDB 23.2; CRDB downgrades are unsupported.
        batch_op.drop_constraint(_SOURCE_CHECK, type_="check")
        batch_op.create_check_constraint(_SOURCE_CHECK, _NARROW)
