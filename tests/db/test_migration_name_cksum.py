"""Tests for the ``name_cksum`` migration on agents and policies."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command

from omnigent.db.db_models import name_checksum
from omnigent.db.utils import _build_alembic_config

# The revision immediately before name_cksum was introduced, and the
# name_cksum migration itself.
_PRE_REVISION = "n1a2b3c4d5e6"
_CKSUM_REVISION = "o1a2b3c4d5e6"


def _new_engine(uri: str) -> sa.Engine:
    """
    Create a raw migration-test engine without auto-upgrading to head.

    :param uri: SQLAlchemy database URI.
    :returns: SQLAlchemy engine with SQLite foreign keys enabled.
    """
    engine = sa.create_engine(uri)
    with engine.connect() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys=ON"))
    return engine


def _upgrade(engine: sa.Engine, uri: str, revision: str) -> None:
    """Run Alembic upgrade to *revision* on a raw engine."""
    config = _build_alembic_config(uri)
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, revision)


def _downgrade(engine: sa.Engine, uri: str, revision: str) -> None:
    """Run Alembic downgrade to *revision* on a raw engine."""
    config = _build_alembic_config(uri)
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.downgrade(config, revision)


def _insert_agent(
    conn: sa.Connection,
    *,
    agent_id: str,
    name: str,
    session_id: str | None = None,
    name_cksum: str | None = None,
) -> None:
    """Insert an ``agents`` row, optionally supplying ``name_cksum``."""
    if name_cksum is None:
        conn.execute(
            sa.text(
                "INSERT INTO agents (id, created_at, name, bundle_location, version, session_id) "
                "VALUES (:id, :ts, :name, :loc, 1, :session_id)"
            ),
            {
                "id": agent_id,
                "ts": 1700000000,
                "name": name,
                "loc": f"{agent_id}/bundle",
                "session_id": session_id,
            },
        )
    else:
        conn.execute(
            sa.text(
                "INSERT INTO agents "
                "(id, created_at, name, name_cksum, bundle_location, version, session_id) "
                "VALUES (:id, :ts, :name, :cksum, :loc, 1, :session_id)"
            ),
            {
                "id": agent_id,
                "ts": 1700000000,
                "name": name,
                "cksum": name_cksum,
                "loc": f"{agent_id}/bundle",
                "session_id": session_id,
            },
        )


def _insert_policy(
    conn: sa.Connection,
    *,
    policy_id: str,
    name: str,
    session_id: str | None = None,
    name_cksum: str | None = None,
) -> None:
    """Insert a ``policies`` row, optionally supplying ``name_cksum``."""
    if name_cksum is None:
        conn.execute(
            sa.text(
                "INSERT INTO policies (id, name, session_id, created_at, type, handler) "
                "VALUES (:id, :name, :session_id, :ts, 'python', 'pkg.handler')"
            ),
            {"id": policy_id, "name": name, "session_id": session_id, "ts": 1700000000},
        )
    else:
        conn.execute(
            sa.text(
                "INSERT INTO policies "
                "(id, name, name_cksum, session_id, created_at, type, handler) "
                "VALUES (:id, :name, :cksum, :session_id, :ts, 'python', 'pkg.handler')"
            ),
            {
                "id": policy_id,
                "name": name,
                "cksum": name_cksum,
                "session_id": session_id,
                "ts": 1700000000,
            },
        )


def _insert_conversation(conn: sa.Connection, conv_id: str) -> None:
    """Insert a minimal top-level conversation for FK targets."""
    conn.execute(
        sa.text(
            "INSERT INTO conversations "
            "(id, created_at, updated_at, root_conversation_id, kind) "
            "VALUES (:id, :ts, :ts, :id, 'default')"
        ),
        {"id": conv_id, "ts": 1700000000},
    )


def test_name_cksum_backfill_populates_agents_and_policies(tmp_path: Path) -> None:
    """Upgrade backfills name_cksum = sha256(name) for every existing row."""
    uri = f"sqlite:///{tmp_path / 'backfill.db'}"
    engine = _new_engine(uri)
    try:
        _upgrade(engine, uri, _PRE_REVISION)
        with engine.begin() as conn:
            _insert_agent(conn, agent_id="ag_a", name="code-assistant")
            _insert_agent(conn, agent_id="ag_b", name="researcher")
            # A default policy (session_id IS NULL) plus a session-scoped one.
            _insert_conversation(conn, "conv_p")
            _insert_policy(conn, policy_id="pol_default", name="baseline")
            _insert_policy(conn, policy_id="pol_scoped", name="baseline", session_id="conv_p")

        _upgrade(engine, uri, _CKSUM_REVISION)

        with engine.connect() as conn:
            agents = {
                str(r["name"]): str(r["name_cksum"])
                for r in conn.execute(sa.text("SELECT name, name_cksum FROM agents")).mappings()
            }
            policies = {
                str(r["id"]): (str(r["name"]), str(r["name_cksum"]))
                for r in conn.execute(
                    sa.text("SELECT id, name, name_cksum FROM policies")
                ).mappings()
            }
        assert agents == {
            "code-assistant": name_checksum("code-assistant"),
            "researcher": name_checksum("researcher"),
        }
        # The migration's inlined digest must match the app-side helper.
        assert agents["researcher"] == hashlib.sha256(b"researcher").hexdigest()
        for _pid, (name, cksum) in policies.items():
            assert cksum == name_checksum(name)
    finally:
        engine.dispose()


def test_name_cksum_indexes_are_swapped(tmp_path: Path) -> None:
    """The unique indexes move from the name columns to the cksum columns."""
    uri = f"sqlite:///{tmp_path / 'indexes.db'}"
    engine = _new_engine(uri)
    try:
        _upgrade(engine, uri, _CKSUM_REVISION)
        inspector = sa.inspect(engine)

        agent_indexes = {idx["name"]: idx for idx in inspector.get_indexes("agents")}
        assert "ix_agents_template_name" not in agent_indexes
        assert "ix_agents_template_name_cksum" in agent_indexes
        assert bool(agent_indexes["ix_agents_template_name_cksum"]["unique"]) is True
        assert agent_indexes["ix_agents_template_name_cksum"]["column_names"] == ["name_cksum"]

        policy_uniques = {uc["name"]: uc for uc in inspector.get_unique_constraints("policies")}
        assert "uq_policies_session_id_name" not in policy_uniques
        assert "uq_policies_session_id_name_cksum" in policy_uniques
        assert policy_uniques["uq_policies_session_id_name_cksum"]["column_names"] == [
            "session_id",
            "name_cksum",
        ]
    finally:
        engine.dispose()


def test_template_name_cksum_rejects_duplicate(tmp_path: Path) -> None:
    """Two template agents (session_id IS NULL) can't share a name."""
    uri = f"sqlite:///{tmp_path / 'dup-agent.db'}"
    engine = _new_engine(uri)
    try:
        _upgrade(engine, uri, _CKSUM_REVISION)
        cksum = name_checksum("dup-name")
        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as conn:
                _insert_agent(conn, agent_id="ag_1", name="dup-name", name_cksum=cksum)
                _insert_agent(conn, agent_id="ag_2", name="dup-name", name_cksum=cksum)
    finally:
        engine.dispose()


def test_distinct_sessions_may_reuse_agent_name(tmp_path: Path) -> None:
    """Session-scoped agents are not covered by the template-name index."""
    uri = f"sqlite:///{tmp_path / 'reuse-agent.db'}"
    engine = _new_engine(uri)
    try:
        _upgrade(engine, uri, _CKSUM_REVISION)
        cksum = name_checksum("shared")
        with engine.begin() as conn:
            _insert_conversation(conn, "conv_one")
            _insert_conversation(conn, "conv_two")
            _insert_agent(
                conn, agent_id="ag_s1", name="shared", session_id="conv_one", name_cksum=cksum
            )
            _insert_agent(
                conn, agent_id="ag_s2", name="shared", session_id="conv_two", name_cksum=cksum
            )
        with engine.connect() as conn:
            count = conn.execute(
                sa.text("SELECT COUNT(*) FROM agents WHERE name = 'shared'")
            ).scalar()
        assert count == 2
    finally:
        engine.dispose()


def test_policy_name_cksum_uniqueness_per_session(tmp_path: Path) -> None:
    """Two policies in one session can't share a name (via the cksum key)."""
    uri = f"sqlite:///{tmp_path / 'dup-policy.db'}"
    engine = _new_engine(uri)
    try:
        _upgrade(engine, uri, _CKSUM_REVISION)
        cksum = name_checksum("guardrail")
        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as conn:
                _insert_conversation(conn, "conv_dup")
                _insert_policy(
                    conn,
                    policy_id="pol_1",
                    name="guardrail",
                    session_id="conv_dup",
                    name_cksum=cksum,
                )
                _insert_policy(
                    conn,
                    policy_id="pol_2",
                    name="guardrail",
                    session_id="conv_dup",
                    name_cksum=cksum,
                )
    finally:
        engine.dispose()


def test_name_cksum_downgrade_round_trips(tmp_path: Path) -> None:
    """Downgrade drops the cksum columns and restores the name indexes."""
    uri = f"sqlite:///{tmp_path / 'downgrade.db'}"
    engine = _new_engine(uri)
    try:
        _upgrade(engine, uri, _PRE_REVISION)
        with engine.begin() as conn:
            _insert_agent(conn, agent_id="ag_d", name="code-assistant")
            _insert_policy(conn, policy_id="pol_d", name="baseline")
        _upgrade(engine, uri, _CKSUM_REVISION)

        _downgrade(engine, uri, _PRE_REVISION)

        inspector = sa.inspect(engine)
        agent_cols = {c["name"] for c in inspector.get_columns("agents")}
        policy_cols = {c["name"] for c in inspector.get_columns("policies")}
        assert "name_cksum" not in agent_cols
        assert "name_cksum" not in policy_cols

        agent_indexes = {idx["name"] for idx in inspector.get_indexes("agents")}
        assert "ix_agents_template_name" in agent_indexes
        assert "ix_agents_template_name_cksum" not in agent_indexes

        policy_uniques = {uc["name"] for uc in inspector.get_unique_constraints("policies")}
        assert "uq_policies_session_id_name" in policy_uniques
        assert "uq_policies_session_id_name_cksum" not in policy_uniques
    finally:
        engine.dispose()
