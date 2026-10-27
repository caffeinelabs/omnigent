"""add the software-factory cockpit tables

Revision ID: fy1a2b3c4d5e
Revises: daba139ca998
Create Date: 2026-10-26 00:00:00.000000

Adds the factory cockpit's read model: ``factory_projects``, ``factory_prs``,
``factory_decisions``, ``factory_events`` and ``factory_demos``.

All five tables are brand-new and additive. Each leads its primary key with the
tenant-partition ``workspace_id``; session ids are 16-byte ``Uuid16`` columns
like ``conversations.id``; there are no foreign keys (schema Rule R032).
Enum-like columns are SMALLINT codes guarded by CHECK constraints (see
``omnigent.db.enum_codecs``), JSON and free text are compressed blobs
(``CompressedText`` -> LargeBinary), and times are epoch-second integers except
``factory_events.ts``, which is epoch milliseconds.

Each table and index is created only when missing: CockroachDB bootstraps from
the current models and then replays every revision after its baseline.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.schema import SchemaItem

from omnigent.db.db_models import Uuid16

revision: str = "fy1a2b3c4d5e"
down_revision: str | None = "daba139ca998"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _workspace_id() -> sa.Column:
    return sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0")


def _create_table(existing: set[str], name: str, *elements: SchemaItem) -> None:
    if name not in existing:
        op.create_table(name, *elements)


def _create_index(inspector: sa.Inspector, name: str, table: str, columns: list[str]) -> None:
    if name not in {index["name"] for index in inspector.get_indexes(table)}:
        op.create_index(name, table, columns, unique=False)


def upgrade() -> None:
    """Create the five factory cockpit tables, skipping any that already exist."""
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    _create_table(
        tables,
        "factory_projects",
        _workspace_id(),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("factory_id", Uuid16(), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("repo", sa.String(256), nullable=True),
        sa.Column("base_branch", sa.String(255), nullable=True),
        sa.Column("charter", sa.LargeBinary(), nullable=True),
        sa.Column("priority", sa.SmallInteger(), nullable=False, server_default="2"),
        # FACTORY_PROJECT_STAGE: queued=1 … done=8.
        sa.Column("stage", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("stage_changed_at", sa.Integer(), nullable=False),
        sa.Column("agent_stage", sa.String(32), nullable=True),
        sa.Column("agent_stage_at", sa.Integer(), nullable=True),
        sa.Column("pl_session_id", Uuid16(), nullable=True),
        sa.Column("env", sa.LargeBinary(), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("priority IN (0, 1, 2, 3)", name="ck_factory_projects_priority"),
        sa.CheckConstraint("stage IN (1, 2, 3, 4, 5, 6, 7, 8)", name="ck_factory_projects_stage"),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.UniqueConstraint("workspace_id", "factory_id", "slug", name="uq_factory_projects_slug"),
    )

    _create_table(
        tables,
        "factory_prs",
        _workspace_id(),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("factory_id", Uuid16(), nullable=False),
        sa.Column("project_slug", sa.String(64), nullable=True),
        sa.Column("repo", sa.String(256), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("head_ref", sa.String(255), nullable=False),
        sa.Column("base_ref", sa.String(255), nullable=False),
        sa.Column("head_sha", sa.String(40), nullable=False),
        # FACTORY_PR_STATE: open=1, merged=2, closed=3.
        sa.Column("state", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("is_draft", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("task", sa.String(64), nullable=True),
        sa.Column("additions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deletions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("changed_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gates", sa.LargeBinary(), nullable=False),
        sa.Column("ready", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("ready_at", sa.Integer(), nullable=True),
        sa.Column("sentinel", sa.LargeBinary(), nullable=True),
        sa.Column("human_review", sa.LargeBinary(), nullable=True),
        sa.Column("preview", sa.LargeBinary(), nullable=True),
        sa.Column("merged_sha", sa.String(40), nullable=True),
        sa.Column("merged_by", sa.String(128), nullable=True),
        sa.Column("merged_at", sa.Integer(), nullable=True),
        sa.Column("closed_at", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("last_polled_at", sa.Integer(), nullable=True),
        sa.Column("poll_error", sa.String(512), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("state IN (1, 2, 3)", name="ck_factory_prs_state"),
        sa.CheckConstraint("is_draft IN (0, 1)", name="ck_factory_prs_is_draft"),
        sa.CheckConstraint("ready IN (0, 1)", name="ck_factory_prs_ready"),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.UniqueConstraint(
            "workspace_id", "factory_id", "repo", "number", name="uq_factory_prs_number"
        ),
    )
    _create_index(
        inspector,
        "ix_factory_prs_poll",
        "factory_prs",
        ["workspace_id", "state", "last_polled_at", "id"],
    )

    _create_table(
        tables,
        "factory_decisions",
        _workspace_id(),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("factory_id", Uuid16(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("project_slug", sa.String(64), nullable=True),
        sa.Column("asked_by_session_id", Uuid16(), nullable=True),
        # FACTORY_DECISION_SOURCE: agent=1, relaunch_failed=2,
        # blocked_approval=3, pr_closed=4, storm=5.
        sa.Column("source", sa.SmallInteger(), nullable=False),
        sa.Column("blocking", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("question", sa.String(1000), nullable=False),
        sa.Column("context", sa.LargeBinary(), nullable=True),
        sa.Column("options", sa.LargeBinary(), nullable=False),
        sa.Column("allow_text", sa.SmallInteger(), nullable=False, server_default="1"),
        # FACTORY_DECISION_STATUS: open=1, answered=2, withdrawn=3, expired=4.
        sa.Column("status", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("dedupe_key", sa.String(64), nullable=False),
        sa.Column("answer_option_id", sa.String(16), nullable=True),
        sa.Column("answer_text", sa.LargeBinary(), nullable=True),
        sa.Column("answered_by", sa.String(128), nullable=True),
        sa.Column("answered_at", sa.Integer(), nullable=True),
        sa.Column("delivered_mail_id", sa.String(32), nullable=True),
        sa.Column("delivered_at", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("source IN (1, 2, 3, 4, 5)", name="ck_factory_decisions_source"),
        sa.CheckConstraint("status IN (1, 2, 3, 4)", name="ck_factory_decisions_status"),
        sa.CheckConstraint("blocking IN (0, 1)", name="ck_factory_decisions_blocking"),
        sa.CheckConstraint("allow_text IN (0, 1)", name="ck_factory_decisions_allow_text"),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.UniqueConstraint(
            "workspace_id", "factory_id", "number", name="uq_factory_decisions_number"
        ),
    )
    _create_index(
        inspector,
        "ix_factory_decisions_status",
        "factory_decisions",
        ["workspace_id", "factory_id", "status", "created_at", "id"],
    )
    _create_index(
        inspector,
        "ix_factory_decisions_dedupe",
        "factory_decisions",
        ["workspace_id", "factory_id", "dedupe_key", "id"],
    )

    _create_table(
        tables,
        "factory_events",
        _workspace_id(),
        # uuid7 (time-ordered) stored as 16 raw bytes.
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("factory_id", Uuid16(), nullable=False),
        sa.Column("ts", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        # FACTORY_EVENT_SEVERITY: info=0, notice=1, warning=2, critical=3.
        sa.Column("severity", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("project_slug", sa.String(64), nullable=True),
        sa.Column("from_id", Uuid16(), nullable=True),
        sa.Column("to_id", Uuid16(), nullable=True),
        sa.Column("subject", sa.String(128), nullable=True),
        sa.Column("summary", sa.String(300), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=True),
        sa.CheckConstraint("severity IN (0, 1, 2, 3)", name="ck_factory_events_severity"),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    _create_index(
        inspector,
        "ix_factory_events_factory_ts",
        "factory_events",
        ["workspace_id", "factory_id", "ts", "id"],
    )
    _create_index(
        inspector,
        "ix_factory_events_kind_ts",
        "factory_events",
        ["workspace_id", "kind", "ts", "id"],
    )

    _create_table(
        tables,
        "factory_demos",
        _workspace_id(),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("factory_id", Uuid16(), nullable=False),
        sa.Column("project_slug", sa.String(64), nullable=False),
        sa.Column("pr_id", Uuid16(), nullable=True),
        sa.Column("repo", sa.String(256), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("head_sha", sa.String(40), nullable=False),
        sa.Column("session_id", Uuid16(), nullable=False),
        # FACTORY_DEMO_STATUS: PASS=1, PARTIAL=2, FAIL=3, ERROR=4.
        sa.Column("status", sa.SmallInteger(), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("summary", sa.String(1000), nullable=False),
        sa.Column("preview_url", sa.String(512), nullable=True),
        sa.Column("files", sa.LargeBinary(), nullable=False),
        sa.Column("manifest", sa.LargeBinary(), nullable=True),
        sa.Column("created_by_session", Uuid16(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("status IN (1, 2, 3, 4)", name="ck_factory_demos_status"),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    _create_index(
        inspector,
        "ix_factory_demos_pr",
        "factory_demos",
        ["workspace_id", "factory_id", "pr_number", "created_at", "id"],
    )


def downgrade() -> None:
    """Drop the five factory cockpit tables, newest first."""
    op.drop_index("ix_factory_demos_pr", table_name="factory_demos")
    op.drop_table("factory_demos")
    op.drop_index("ix_factory_events_kind_ts", table_name="factory_events")
    op.drop_index("ix_factory_events_factory_ts", table_name="factory_events")
    op.drop_table("factory_events")
    op.drop_index("ix_factory_decisions_dedupe", table_name="factory_decisions")
    op.drop_index("ix_factory_decisions_status", table_name="factory_decisions")
    op.drop_table("factory_decisions")
    op.drop_index("ix_factory_prs_poll", table_name="factory_prs")
    op.drop_table("factory_prs")
    op.drop_table("factory_projects")
