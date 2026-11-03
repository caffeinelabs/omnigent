"""add the factory discovery ledger

Revision ID: fz3d4e5f6a7b
Revises: fz2a7c3e9b14
Create Date: 2026-11-03 00:00:00.000000

The discovery ledger: ``factory_signals`` (``S-n``, append-only evidence the
server writes), ``factory_signal_links`` (a signal attached to an
opportunity, kept when detached), ``factory_opportunities`` (``O-n``),
``factory_score_snapshots`` (a score's inputs and outputs at a decision
point) and ``factory_source_state`` (a source's cursor, health and usage).

It also widens ``ck_factory_feedback_target_kind`` to the calibration
targets ``signal`` (9) and ``opportunity`` (10).

Conventions match ``fz1a2b3c4d5e``: every primary key leads with
``workspace_id``, enum-like columns are SMALLINT codes with named CHECK
constraints (``omnigent.db.enum_codecs``), JSON and free text are compressed
blobs, times are epoch seconds, and there are no foreign keys. Every table,
index and CHECK is created only when missing: CockroachDB bootstraps from the
current models and then replays every revision after its baseline.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.schema import SchemaItem

from omnigent.db.db_models import Uuid16

revision: str = "fz3d4e5f6a7b"
down_revision: str | None = "fz2a7c3e9b14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FEEDBACK_CHECK = "ck_factory_feedback_target_kind"
# CockroachDB parks the narrow CHECK under this name before dropping it.
_NARROW_FEEDBACK_CHECK = "ck_factory_feedback_target_kind_narrow"
_WIDE = "target_kind IN (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)"
_NARROW = "target_kind IN (1, 2, 3, 4, 5, 6, 7, 8)"


def _workspace_id() -> sa.Column:
    return sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0")


def _blob(name: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.LargeBinary(), nullable=nullable)


def _create_table(existing: set[str], name: str, *elements: SchemaItem) -> None:
    if name not in existing:
        op.create_table(name, *elements)


def _create_index(inspector: sa.Inspector, name: str, table: str, columns: list[str]) -> None:
    if name not in {index["name"] for index in inspector.get_indexes(table)}:
        op.create_index(name, table, columns, unique=False)


def _check_codes(inspector: sa.Inspector, table: str, name: str) -> set[int] | None:
    """Return the integer codes a named CHECK lists, or ``None`` when it is absent."""
    for check in inspector.get_check_constraints(table):
        if check["name"] == name:
            return {int(code) for code in re.findall(r"\b\d+\b", check["sqltext"])}
    return None


def _create_signals(inspector: sa.Inspector, tables: set[str]) -> None:
    _create_table(
        tables,
        "factory_signals",
        _workspace_id(),
        sa.Column("factory_id", Uuid16(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False, autoincrement=False),
        sa.Column("source", sa.String(32), nullable=False),
        # FACTORY_SOURCE_KIND: repo=1 … mcp=11, factory=12.
        sa.Column("source_kind", sa.SmallInteger(), nullable=False),
        # FACTORY_SIGNAL_KIND: request=1 … simulation=11; NULL until labelled.
        sa.Column("kind", sa.SmallInteger(), nullable=True),
        # FACTORY_SAY_DO: say=1, do=2.
        sa.Column("say_do", sa.SmallInteger(), nullable=False, server_default="1"),
        _blob("excerpt"),
        sa.Column("excerpt_sha", sa.String(64), nullable=True),
        sa.Column("pointer", sa.String(500), nullable=True),
        sa.Column("pointer_key", sa.String(64), nullable=True),
        sa.Column("query_event", Uuid16(), nullable=True),
        sa.Column("observed_at", sa.Integer(), nullable=True),
        sa.Column("ingested_at", sa.Integer(), nullable=False),
        sa.Column("account", sa.String(64), nullable=True),
        _blob("segments"),
        _blob("redactions"),
        # FACTORY_PRIVACY: public=1, internal=2, personal=3, sensitive=4.
        sa.Column("privacy", sa.SmallInteger(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=True),
        sa.Column("injection", sa.SmallInteger(), nullable=False, server_default="0"),
        _blob("flags"),
        sa.Column("author", sa.String(128), nullable=True),
        # FACTORY_ACTOR_KIND: human=1, agent=2, server=3.
        sa.Column("author_kind", sa.SmallInteger(), nullable=True),
        _blob("meta"),
        sa.Column("paraphrase", sa.String(200), nullable=True),
        # FACTORY_INTENSITY: blocker=1, workaround=2, annoyance=3, nice=4.
        sa.Column("intensity", sa.SmallInteger(), nullable=True),
        # FACTORY_FORCE: push=1, pull=2, anxiety=3, habit=4.
        sa.Column("force", sa.SmallInteger(), nullable=True),
        sa.Column("labelled_by", sa.String(128), nullable=True),
        sa.Column("labelled_at", sa.Integer(), nullable=True),
        sa.Column("withdrawn_at", sa.Integer(), nullable=True),
        sa.Column("withdrawn_by", sa.String(128), nullable=True),
        sa.Column("expired_at", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "source_kind IN (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)",
            name="ck_factory_signals_source_kind",
        ),
        sa.CheckConstraint(
            "kind IN (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)", name="ck_factory_signals_kind"
        ),
        sa.CheckConstraint("say_do IN (1, 2)", name="ck_factory_signals_say_do"),
        sa.CheckConstraint("privacy IN (1, 2, 3, 4)", name="ck_factory_signals_privacy"),
        sa.CheckConstraint("intensity IN (1, 2, 3, 4)", name="ck_factory_signals_intensity"),
        sa.CheckConstraint("force IN (1, 2, 3, 4)", name="ck_factory_signals_force"),
        sa.CheckConstraint("author_kind IN (1, 2, 3)", name="ck_factory_signals_author_kind"),
        sa.UniqueConstraint(
            "workspace_id",
            "factory_id",
            "source",
            "pointer_key",
            name="uq_factory_signals_pointer",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "factory_id", "number"),
    )
    _create_index(
        inspector,
        "ix_factory_signals_ingested",
        "factory_signals",
        ["workspace_id", "factory_id", "ingested_at", "number"],
    )
    _create_index(
        inspector,
        "ix_factory_signals_expires",
        "factory_signals",
        ["workspace_id", "expires_at", "factory_id", "number"],
    )

    _create_table(
        tables,
        "factory_signal_links",
        _workspace_id(),
        sa.Column("factory_id", Uuid16(), nullable=False),
        sa.Column("signal", sa.Integer(), nullable=False, autoincrement=False),
        sa.Column("opportunity", sa.Integer(), nullable=False, autoincrement=False),
        sa.Column("counter", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.SmallInteger(), nullable=True),
        sa.Column("attached_by", sa.String(128), nullable=False),
        # FACTORY_ACTOR_KIND: human=1, agent=2, server=3.
        sa.Column("attached_kind", sa.SmallInteger(), nullable=False),
        sa.Column("attached_at", sa.Integer(), nullable=False),
        sa.Column("via", sa.String(32), nullable=True),
        sa.Column("detached_at", sa.Integer(), nullable=True),
        sa.Column("detached_by", sa.String(128), nullable=True),
        sa.Column("detach_reason", sa.String(200), nullable=True),
        sa.CheckConstraint("counter IN (0, 1)", name="ck_factory_signal_links_counter"),
        sa.CheckConstraint(
            "attached_kind IN (1, 2, 3)", name="ck_factory_signal_links_attached_kind"
        ),
        sa.PrimaryKeyConstraint("workspace_id", "factory_id", "signal", "opportunity"),
    )
    _create_index(
        inspector,
        "ix_factory_signal_links_opportunity",
        "factory_signal_links",
        ["workspace_id", "factory_id", "opportunity", "signal"],
    )


def _create_opportunities(inspector: sa.Inspector, tables: set[str]) -> None:
    _create_table(
        tables,
        "factory_opportunities",
        _workspace_id(),
        sa.Column("factory_id", Uuid16(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False, autoincrement=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("statement", sa.String(300), nullable=False),
        sa.Column("problem", sa.String(200), nullable=True),
        sa.Column("goal", sa.String(16), nullable=True),
        sa.Column("non_goal", sa.String(200), nullable=True),
        _blob("segments"),
        sa.Column("area", sa.String(32), nullable=True),
        _blob("alternatives"),
        _blob("bet_note"),
        _blob("votes"),
        sa.Column("pinned_by", sa.String(128), nullable=True),
        sa.Column("pinned_at", sa.Integer(), nullable=True),
        _blob("park"),
        _blob("kill"),
        sa.Column("merged_into", sa.Integer(), nullable=True),
        sa.Column("split_from", sa.Integer(), nullable=True),
        _blob("research"),
        _blob("plan_request"),
        _blob("declines"),
        _blob("origin"),
        _blob("result"),
        sa.Column("created_by", sa.String(128), nullable=False),
        # FACTORY_ACTOR_KIND: human=1, agent=2, server=3.
        sa.Column("created_kind", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "created_kind IN (1, 2, 3)", name="ck_factory_opportunities_created_kind"
        ),
        sa.PrimaryKeyConstraint("workspace_id", "factory_id", "number"),
    )

    _create_table(
        tables,
        "factory_score_snapshots",
        _workspace_id(),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("factory_id", Uuid16(), nullable=False),
        sa.Column("opportunity", sa.Integer(), nullable=False),
        sa.Column("at", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("def_version", sa.Integer(), nullable=True),
        sa.Column("rank", sa.Float(), nullable=False),
        sa.Column("band", sa.String(16), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),
        _blob("inputs"),
        _blob("parts"),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    _create_index(
        inspector,
        "ix_factory_score_snapshots_opportunity",
        "factory_score_snapshots",
        ["workspace_id", "factory_id", "opportunity", "at", "id"],
    )

    _create_table(
        tables,
        "factory_source_state",
        _workspace_id(),
        sa.Column("factory_id", Uuid16(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        _blob("cursor"),
        sa.Column("last_ok_at", sa.Integer(), nullable=True),
        sa.Column("last_error_at", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.String(300), nullable=True),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("usage_day", sa.String(10), nullable=True),
        sa.Column("calls_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("signals_total", sa.Integer(), nullable=False, server_default="0"),
        _blob("data"),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "factory_id", "source"),
    )


def _widen_feedback_targets(inspector: sa.Inspector) -> None:
    """``ck_factory_feedback_target_kind`` gains signal=9 and opportunity=10."""
    codes = _check_codes(inspector, "factory_feedback", _FEEDBACK_CHECK)
    stale = _check_codes(inspector, "factory_feedback", _NARROW_FEEDBACK_CHECK) is not None
    widen = codes is None or not {9, 10} <= codes
    if not widen and not stale:
        return
    narrow = _FEEDBACK_CHECK
    if widen and codes is not None and op.get_bind().dialect.name == "cockroachdb":
        # CockroachDB rejects re-adding a CHECK name dropped in the same transaction.
        narrow = _NARROW_FEEDBACK_CHECK
        op.execute(f"ALTER TABLE factory_feedback RENAME CONSTRAINT {_FEEDBACK_CHECK} TO {narrow}")
    with op.batch_alter_table("factory_feedback") as batch_op:
        if widen:
            if codes is not None:
                batch_op.drop_constraint(narrow, type_="check")
            batch_op.create_check_constraint(_FEEDBACK_CHECK, _WIDE)
        if stale:
            batch_op.drop_constraint(_NARROW_FEEDBACK_CHECK, type_="check")


def upgrade() -> None:
    """Create the five discovery tables and widen the feedback targets, skipping what exists."""
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    _create_signals(inspector, tables)
    _create_opportunities(inspector, tables)
    _widen_feedback_targets(inspector)


_NEW_TABLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("factory_source_state", ()),
    ("factory_score_snapshots", ("ix_factory_score_snapshots_opportunity",)),
    ("factory_opportunities", ()),
    ("factory_signal_links", ("ix_factory_signal_links_opportunity",)),
    ("factory_signals", ("ix_factory_signals_expires", "ix_factory_signals_ingested")),
)


def downgrade() -> None:
    """Drop the discovery tables; feedback on signals and opportunities goes first."""
    for table, indexes in _NEW_TABLES:
        for index in indexes:
            op.drop_index(index, table_name=table)
        op.drop_table(table)
    # The narrowed CHECK would reject feedback on a signal or an opportunity.
    op.execute("DELETE FROM factory_feedback WHERE target_kind > 8")
    with op.batch_alter_table("factory_feedback") as batch_op:
        # Re-adding the same CHECK name fails on CockroachDB 23.2; CRDB downgrades are unsupported.
        batch_op.drop_constraint(_FEEDBACK_CHECK, type_="check")
        batch_op.create_check_constraint(_FEEDBACK_CHECK, _NARROW)
