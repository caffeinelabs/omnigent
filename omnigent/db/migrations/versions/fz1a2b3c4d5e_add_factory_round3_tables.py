"""add the software-factory round-3 tables

Revision ID: fz1a2b3c4d5e
Revises: fy1a2b3c4d5e
Create Date: 2026-10-27 00:00:00.000000

All round-3 factory persistence in one forward-only step: the people roster,
the Room (chat messages and read marks), proposals (``C-n``), the work graph
(``T-n`` tasks), loops, issues (``INC-n`` incidents, ``F-n`` findings,
retros), OTel-shaped spans, the daily cost ledger, platform versions, versioned
factory definitions and human feedback.

It also adds lifecycle and attribution columns to ``factory_projects``,
``factory_prs`` and ``factory_decisions``, and widens
``ck_factory_decisions_source`` to the sources ``proposal`` (6), ``loop`` (7),
``access_request`` (8) and ``done_check`` (9).

Conventions match ``fy1a2b3c4d5e``: every primary key leads with
``workspace_id``, ids are ``Uuid16``, enum-like columns are SMALLINT codes with
named CHECK constraints (``omnigent.db.enum_codecs``), JSON and free text are
compressed blobs, times are epoch seconds (spans use epoch milliseconds), and
there are no foreign keys. Changes to existing tables go through
``batch_alter_table`` so SQLite works.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "fz1a2b3c4d5e"
down_revision: str | None = "fy1a2b3c4d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SPAN_KINDS = ", ".join(str(code) for code in range(1, 22))


def _workspace_id() -> sa.Column:
    return sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0")


def _blob(name: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.LargeBinary(), nullable=nullable)


def _upgrade_existing_tables() -> None:
    with op.batch_alter_table("factory_projects") as batch_op:
        # FACTORY_PROJECT_STATUS: active=1, cancelled=2, merged=3, done=4.
        batch_op.add_column(
            sa.Column("status", sa.SmallInteger(), nullable=False, server_default="1")
        )
        batch_op.add_column(sa.Column("parent_slug", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("closed_at", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("def_version", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("version_id", Uuid16(), nullable=True))
        batch_op.create_check_constraint("ck_factory_projects_status", "status IN (1, 2, 3, 4)")

    with op.batch_alter_table("factory_prs") as batch_op:
        batch_op.add_column(sa.Column("opened_at", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("first_ready_at", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("task_id", Uuid16(), nullable=True))
        batch_op.add_column(sa.Column("version_id", Uuid16(), nullable=True))

    with op.batch_alter_table("factory_decisions") as batch_op:
        batch_op.add_column(sa.Column("assignee", sa.String(128), nullable=True))
        batch_op.add_column(sa.Column("claimed_by", sa.String(128), nullable=True))
        batch_op.add_column(sa.Column("claimed_at", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("thread", sa.String(80), nullable=True))
        batch_op.add_column(sa.Column("subject", sa.String(64), nullable=True))
        batch_op.drop_constraint("ck_factory_decisions_source", type_="check")
        # FACTORY_DECISION_SOURCE gains proposal=6, loop=7, access_request=8, done_check=9.
        batch_op.create_check_constraint(
            "ck_factory_decisions_source", "source IN (1, 2, 3, 4, 5, 6, 7, 8, 9)"
        )


def _create_people_and_room() -> None:
    op.create_table(
        "factory_people",
        _workspace_id(),
        sa.Column("factory_id", Uuid16(), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        # FACTORY_PERSON_ROLE: viewer=1, operator=2, owner=3.
        sa.Column("role", sa.SmallInteger(), nullable=False),
        sa.Column("is_creator", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("invited_by", sa.String(128), nullable=False),
        sa.Column("invited_at", sa.Integer(), nullable=False),
        sa.Column("last_seen_at", sa.Integer(), nullable=True),
        _blob("notify"),
        sa.CheckConstraint("role IN (1, 2, 3)", name="ck_factory_people_role"),
        sa.CheckConstraint("is_creator IN (0, 1)", name="ck_factory_people_is_creator"),
        sa.PrimaryKeyConstraint("workspace_id", "factory_id", "user_id"),
    )
    op.create_index(
        "ix_factory_people_user",
        "factory_people",
        ["workspace_id", "user_id", "factory_id"],
        unique=False,
    )

    op.create_table(
        "factory_chat_messages",
        _workspace_id(),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("factory_id", Uuid16(), nullable=False),
        sa.Column("thread", sa.String(80), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        # FACTORY_CHAT_AUTHOR_KIND: human=1, agent=2, system=3.
        sa.Column("author_kind", sa.SmallInteger(), nullable=False),
        sa.Column("author", sa.String(128), nullable=False),
        sa.Column("author_role", sa.String(16), nullable=True),
        _blob("body", nullable=False),
        _blob("mentions"),
        _blob("refs"),
        sa.Column("reply_to", Uuid16(), nullable=True),
        _blob("card"),
        _blob("routed"),
        sa.Column("auto_reply", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "author_kind IN (1, 2, 3)", name="ck_factory_chat_messages_author_kind"
        ),
        sa.CheckConstraint("auto_reply IN (0, 1)", name="ck_factory_chat_messages_auto_reply"),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.UniqueConstraint(
            "workspace_id", "factory_id", "seq", name="uq_factory_chat_messages_seq"
        ),
    )
    op.create_index(
        "ix_factory_chat_messages_thread",
        "factory_chat_messages",
        ["workspace_id", "factory_id", "thread", "seq", "id"],
        unique=False,
    )

    op.create_table(
        "factory_chat_reads",
        _workspace_id(),
        sa.Column("factory_id", Uuid16(), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("thread", sa.String(80), nullable=False),
        sa.Column("last_seq", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "factory_id", "user_id", "thread"),
    )


def _create_proposals() -> None:
    op.create_table(
        "factory_proposals",
        _workspace_id(),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("factory_id", Uuid16(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        # FACTORY_PROPOSAL_KIND: projects=1, tuning=2, rules=3, code=4.
        sa.Column("kind", sa.SmallInteger(), nullable=False),
        # FACTORY_PROPOSAL_STATUS: pending=1 … awaiting_cosign=11.
        sa.Column("status", sa.SmallInteger(), nullable=False, server_default="1"),
        # FACTORY_PROPOSAL_APPROVAL: auto=1, operator=2, owner=3.
        sa.Column("approval", sa.SmallInteger(), nullable=False),
        # FACTORY_PROPOSAL_CLASS: A=1, B=2, human=3.
        sa.Column("class", sa.SmallInteger(), nullable=True),
        sa.Column("title", sa.String(256), nullable=False),
        _blob("ops", nullable=False),
        _blob("before"),
        _blob("after"),
        _blob("rationale", nullable=False),
        _blob("analysis"),
        _blob("evidence"),
        _blob("expected_effect"),
        _blob("warnings"),
        # FACTORY_ACTOR_KIND: human=1, agent=2, server=3.
        sa.Column("author_kind", sa.SmallInteger(), nullable=False),
        sa.Column("author", sa.String(128), nullable=False),
        sa.Column("author_role", sa.String(16), nullable=True),
        sa.Column("on_behalf_of", sa.String(128), nullable=True),
        sa.Column("cosigned_by", sa.String(128), nullable=True),
        sa.Column("thread", sa.String(80), nullable=True),
        sa.Column("reply_to_chat", Uuid16(), nullable=True),
        sa.Column("decision_id", Uuid16(), nullable=True),
        _blob("based_on"),
        sa.Column("trial_until", sa.Integer(), nullable=True),
        sa.Column("undo_until", sa.Integer(), nullable=True),
        _blob("result"),
        sa.Column("def_version_from", sa.Integer(), nullable=True),
        sa.Column("def_version_to", sa.Integer(), nullable=True),
        sa.Column("decided_by", sa.String(128), nullable=True),
        sa.Column("decided_at", sa.Integer(), nullable=True),
        sa.Column("applied_at", sa.Integer(), nullable=True),
        sa.Column("reverted_at", sa.Integer(), nullable=True),
        sa.Column("reverted_by", sa.String(128), nullable=True),
        sa.Column("dedupe_key", sa.String(96), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("kind IN (1, 2, 3, 4)", name="ck_factory_proposals_kind"),
        sa.CheckConstraint(
            "status IN (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)", name="ck_factory_proposals_status"
        ),
        sa.CheckConstraint("approval IN (1, 2, 3)", name="ck_factory_proposals_approval"),
        sa.CheckConstraint("class IN (1, 2, 3)", name="ck_factory_proposals_class"),
        sa.CheckConstraint("author_kind IN (1, 2, 3)", name="ck_factory_proposals_author_kind"),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.UniqueConstraint(
            "workspace_id", "factory_id", "number", name="uq_factory_proposals_number"
        ),
        sa.UniqueConstraint(
            "workspace_id", "factory_id", "dedupe_key", name="uq_factory_proposals_dedupe"
        ),
    )
    op.create_index(
        "ix_factory_proposals_status",
        "factory_proposals",
        ["workspace_id", "factory_id", "status", "id"],
        unique=False,
    )


def _create_work_graph() -> None:
    op.create_table(
        "factory_tasks",
        _workspace_id(),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("factory_id", Uuid16(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("project_slug", sa.String(64), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        # FACTORY_TASK_KIND: implement=1, test=2, docs=3, spike=4, ops=5, adhoc=6.
        sa.Column("kind", sa.SmallInteger(), nullable=False),
        # FACTORY_TASK_STATE: planned=1 … cancelled=8.
        sa.Column("state", sa.SmallInteger(), nullable=False, server_default="1"),
        _blob("card", nullable=False),
        _blob("deps", nullable=False),
        _blob("scope", nullable=False),
        sa.Column("priority", sa.SmallInteger(), nullable=False, server_default="2"),
        sa.Column("estimate_min", sa.Integer(), nullable=True),
        sa.Column("vendor", sa.String(16), nullable=True),
        _blob("session_ids"),
        sa.Column("pr_id", Uuid16(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        _blob("budget"),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=False),
        # FACTORY_ACTOR_KIND: human=1, agent=2, server=3.
        sa.Column("created_via", sa.SmallInteger(), nullable=False),
        sa.Column("def_version", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.Integer(), nullable=True),
        sa.Column("closed_at", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("kind IN (1, 2, 3, 4, 5, 6)", name="ck_factory_tasks_kind"),
        sa.CheckConstraint("state IN (1, 2, 3, 4, 5, 6, 7, 8)", name="ck_factory_tasks_state"),
        sa.CheckConstraint("priority IN (0, 1, 2, 3)", name="ck_factory_tasks_priority"),
        sa.CheckConstraint("created_via IN (1, 2, 3)", name="ck_factory_tasks_created_via"),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.UniqueConstraint(
            "workspace_id", "factory_id", "project_slug", "key", name="uq_factory_tasks_key"
        ),
        sa.UniqueConstraint(
            "workspace_id", "factory_id", "number", name="uq_factory_tasks_number"
        ),
    )
    op.create_index(
        "ix_factory_tasks_state",
        "factory_tasks",
        ["workspace_id", "factory_id", "state", "id"],
        unique=False,
    )

    op.create_table(
        "factory_loops",
        _workspace_id(),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("factory_id", Uuid16(), nullable=False),
        # FACTORY_LOOP_KIND: implement=1 … improve=9.
        sa.Column("kind", sa.SmallInteger(), nullable=False),
        sa.Column("subject", sa.String(64), nullable=False),
        sa.Column("project_slug", sa.String(64), nullable=True),
        sa.Column("task_id", Uuid16(), nullable=True),
        sa.Column("pr_id", Uuid16(), nullable=True),
        # FACTORY_LOOP_STATE: running=1 … superseded=8.
        sa.Column("state", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("iteration", sa.Integer(), nullable=False, server_default="0"),
        _blob("spec", nullable=False),
        _blob("spent", nullable=False),
        sa.Column("last_verdict", sa.String(32), nullable=True),
        sa.Column("last_reason", sa.String(300), nullable=True),
        sa.Column("decision_id", Uuid16(), nullable=True),
        sa.Column("def_version", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.Column("ended_at", sa.Integer(), nullable=True),
        sa.CheckConstraint("kind IN (1, 2, 3, 4, 5, 6, 7, 8, 9)", name="ck_factory_loops_kind"),
        sa.CheckConstraint("state IN (1, 2, 3, 4, 5, 6, 7, 8)", name="ck_factory_loops_state"),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.UniqueConstraint(
            "workspace_id", "factory_id", "kind", "subject", name="uq_factory_loops_subject"
        ),
    )
    op.create_index(
        "ix_factory_loops_state",
        "factory_loops",
        ["workspace_id", "factory_id", "state", "id"],
        unique=False,
    )


def _create_issues() -> None:
    op.create_table(
        "factory_issues",
        _workspace_id(),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("factory_id", Uuid16(), nullable=False),
        # FACTORY_ISSUE_CLASS: incident=1, finding=2, retro=3.
        sa.Column("class", sa.SmallInteger(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("rule", sa.String(48), nullable=False),
        sa.Column("category", sa.String(48), nullable=True),
        # FACTORY_EVENT_SEVERITY: info=0, notice=1, warning=2, critical=3.
        sa.Column("severity", sa.SmallInteger(), nullable=False, server_default="1"),
        # FACTORY_ISSUE_SOURCE: detector=1, sentinel=2, human=3, agent=4, server=5.
        sa.Column("source", sa.SmallInteger(), nullable=False),
        sa.Column("project_slug", sa.String(64), nullable=True),
        sa.Column("target", sa.String(128), nullable=False),
        sa.Column("summary", sa.String(300), nullable=False),
        _blob("body"),
        _blob("evidence"),
        # FACTORY_ISSUE_STATUS: open=1, acked=2, snoozed=3, resolved=4, wontfix=5.
        sa.Column("status", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("assignee", sa.String(128), nullable=True),
        sa.Column("snoozed_until", sa.Integer(), nullable=True),
        sa.Column("acked_by", sa.String(128), nullable=True),
        sa.Column("resolved_by", sa.String(128), nullable=True),
        sa.Column("dedupe_key", sa.String(96), nullable=True),
        sa.Column("count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_seen", sa.Integer(), nullable=False),
        sa.Column("last_seen", sa.Integer(), nullable=False),
        sa.Column("resolved_at", sa.Integer(), nullable=True),
        sa.Column("proposal_id", Uuid16(), nullable=True),
        sa.Column("version_id", Uuid16(), nullable=True),
        sa.Column("def_version", sa.Integer(), nullable=True),
        sa.CheckConstraint("class IN (1, 2, 3)", name="ck_factory_issues_class"),
        sa.CheckConstraint("severity IN (0, 1, 2, 3)", name="ck_factory_issues_severity"),
        sa.CheckConstraint("source IN (1, 2, 3, 4, 5)", name="ck_factory_issues_source"),
        sa.CheckConstraint("status IN (1, 2, 3, 4, 5)", name="ck_factory_issues_status"),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.UniqueConstraint(
            "workspace_id", "factory_id", "class", "number", name="uq_factory_issues_number"
        ),
        sa.UniqueConstraint(
            "workspace_id", "factory_id", "dedupe_key", name="uq_factory_issues_dedupe"
        ),
    )
    op.create_index(
        "ix_factory_issues_status",
        "factory_issues",
        ["workspace_id", "factory_id", "status", "severity", "id"],
        unique=False,
    )


def _create_telemetry() -> None:
    op.create_table(
        "factory_spans",
        _workspace_id(),
        # uuid7 (time-ordered) stored as 16 raw bytes.
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("factory_id", Uuid16(), nullable=False),
        sa.Column("trace_key", sa.String(80), nullable=False),
        sa.Column("parent_id", Uuid16(), nullable=True),
        # FACTORY_SPAN_KIND: project=1 … tool=21.
        sa.Column("kind", sa.SmallInteger(), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("project_slug", sa.String(64), nullable=True),
        sa.Column("pr_id", Uuid16(), nullable=True),
        sa.Column("task_id", Uuid16(), nullable=True),
        sa.Column("loop_id", Uuid16(), nullable=True),
        sa.Column("head_sha", sa.String(40), nullable=True),
        sa.Column("session_id", Uuid16(), nullable=True),
        sa.Column("role", sa.String(16), nullable=True),
        sa.Column("agent_name", sa.String(64), nullable=True),
        sa.Column("actor", sa.String(128), nullable=True),
        # Epoch milliseconds.
        sa.Column("started_at", sa.BigInteger(), nullable=False),
        sa.Column("ended_at", sa.BigInteger(), nullable=True),
        # FACTORY_SPAN_OUTCOME: open=1 … exhausted=8.
        sa.Column("outcome", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("attempt", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("tokens_in", sa.BigInteger(), nullable=True),
        sa.Column("tokens_out", sa.BigInteger(), nullable=True),
        sa.Column("version_id", Uuid16(), nullable=True),
        sa.Column("def_version", sa.Integer(), nullable=True),
        sa.Column("dedupe_key", sa.String(96), nullable=True),
        _blob("attrs"),
        sa.CheckConstraint(f"kind IN ({_SPAN_KINDS})", name="ck_factory_spans_kind"),
        sa.CheckConstraint("outcome IN (1, 2, 3, 4, 5, 6, 7, 8)", name="ck_factory_spans_outcome"),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.UniqueConstraint(
            "workspace_id", "factory_id", "dedupe_key", name="uq_factory_spans_dedupe"
        ),
    )
    for name, columns in (
        ("ix_factory_spans_kind", ["workspace_id", "factory_id", "kind", "started_at", "id"]),
        ("ix_factory_spans_pr", ["workspace_id", "pr_id", "started_at", "id"]),
        ("ix_factory_spans_task", ["workspace_id", "task_id", "started_at", "id"]),
        ("ix_factory_spans_ended", ["workspace_id", "factory_id", "ended_at", "id"]),
    ):
        op.create_index(name, "factory_spans", columns, unique=False)

    op.create_table(
        "factory_cost_daily",
        _workspace_id(),
        sa.Column("factory_id", Uuid16(), nullable=False),
        # "" is the leads' bucket.
        sa.Column("project_slug", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("agent_name", sa.String(64), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("day_utc", sa.String(10), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("tokens_in", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("turns", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint(
            "workspace_id", "factory_id", "project_slug", "role", "agent_name", "model", "day_utc"
        ),
    )

    op.create_table(
        "factory_versions",
        _workspace_id(),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("image_tag", sa.String(64), nullable=True),
        sa.Column("server_sha", sa.String(40), nullable=True),
        _blob("bundles", nullable=False),
        _blob("knobs", nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("note", sa.String(256), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.UniqueConstraint("workspace_id", "content_hash", name="uq_factory_versions_hash"),
    )


def _create_definitions_and_feedback() -> None:
    op.create_table(
        "factory_definitions",
        _workspace_id(),
        sa.Column("factory_id", Uuid16(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, autoincrement=False),
        sa.Column("parent_version", sa.Integer(), nullable=True),
        _blob("doc", nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        # FACTORY_DEFINITION_VIA: seed=1, human=2, proposal=3, revert=4.
        sa.Column("created_via", sa.SmallInteger(), nullable=False),
        sa.Column("proposal_id", Uuid16(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "created_via IN (1, 2, 3, 4)", name="ck_factory_definitions_created_via"
        ),
        sa.PrimaryKeyConstraint("workspace_id", "factory_id", "version"),
    )

    op.create_table(
        "factory_feedback",
        _workspace_id(),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("factory_id", Uuid16(), nullable=False),
        sa.Column("project_slug", sa.String(64), nullable=True),
        # FACTORY_FEEDBACK_TARGET: pr=1 … proposal=8.
        sa.Column("target_kind", sa.SmallInteger(), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=False),
        sa.Column("head_sha", sa.String(40), nullable=True),
        # FACTORY_ACTOR_KIND: human=1, agent=2, server=3.
        sa.Column("author_kind", sa.SmallInteger(), nullable=False),
        sa.Column("author", sa.String(128), nullable=False),
        # FACTORY_FEEDBACK_KIND: rating=1 … root_cause=6.
        sa.Column("kind", sa.SmallInteger(), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=True),
        _blob("tags"),
        _blob("body"),
        sa.Column("def_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "target_kind IN (1, 2, 3, 4, 5, 6, 7, 8)", name="ck_factory_feedback_target_kind"
        ),
        sa.CheckConstraint("author_kind IN (1, 2, 3)", name="ck_factory_feedback_author_kind"),
        sa.CheckConstraint("kind IN (1, 2, 3, 4, 5, 6)", name="ck_factory_feedback_kind"),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_factory_feedback_target",
        "factory_feedback",
        ["workspace_id", "factory_id", "target_kind", "created_at", "id"],
        unique=False,
    )


def upgrade() -> None:
    """Add the round-3 columns and the twelve round-3 factory tables."""
    _upgrade_existing_tables()
    _create_people_and_room()
    _create_proposals()
    _create_work_graph()
    _create_issues()
    _create_telemetry()
    _create_definitions_and_feedback()


_NEW_TABLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("factory_feedback", ("ix_factory_feedback_target",)),
    ("factory_definitions", ()),
    ("factory_versions", ()),
    ("factory_cost_daily", ()),
    (
        "factory_spans",
        (
            "ix_factory_spans_ended",
            "ix_factory_spans_task",
            "ix_factory_spans_pr",
            "ix_factory_spans_kind",
        ),
    ),
    ("factory_issues", ("ix_factory_issues_status",)),
    ("factory_loops", ("ix_factory_loops_state",)),
    ("factory_tasks", ("ix_factory_tasks_state",)),
    ("factory_proposals", ("ix_factory_proposals_status",)),
    ("factory_chat_reads", ()),
    ("factory_chat_messages", ("ix_factory_chat_messages_thread",)),
    ("factory_people", ("ix_factory_people_user",)),
)


def downgrade() -> None:
    """Drop the round-3 tables and columns; decisions with a round-3 source go first."""
    for table, indexes in _NEW_TABLES:
        for index in indexes:
            op.drop_index(index, table_name=table)
        op.drop_table(table)

    # The narrowed CHECK would reject rows opened from a round-3 source.
    op.execute("DELETE FROM factory_decisions WHERE source > 5")
    with op.batch_alter_table("factory_decisions") as batch_op:
        batch_op.drop_constraint("ck_factory_decisions_source", type_="check")
        batch_op.create_check_constraint(
            "ck_factory_decisions_source", "source IN (1, 2, 3, 4, 5)"
        )
        for column in ("subject", "thread", "claimed_at", "claimed_by", "assignee"):
            batch_op.drop_column(column)

    with op.batch_alter_table("factory_prs") as batch_op:
        for column in ("version_id", "task_id", "first_ready_at", "opened_at"):
            batch_op.drop_column(column)

    with op.batch_alter_table("factory_projects") as batch_op:
        batch_op.drop_constraint("ck_factory_projects_status", type_="check")
        for column in ("version_id", "def_version", "closed_at", "parent_slug", "status"):
            batch_op.drop_column(column)
