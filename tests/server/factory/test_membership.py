"""Factory membership: lazy enrolment, inheritance via the root, aliases."""

from __future__ import annotations

import pytest
from sqlalchemy import update

from omnigent import factory as F
from omnigent.db.db_models import SqlConversation, current_workspace_id
from omnigent.server.factory.membership import (
    UnknownTargetError,
    ensure_member,
    factory_members,
    membership_of,
    resolve_factory_id,
    resolve_target,
)
from tests.server.factory.conftest import FactoryWorld

# ── lazy init ───────────────────────────────────────────


def test_lazy_init_enrols_a_top_level_lead_as_its_own_root(world: FactoryWorld) -> None:
    fresh = world.make_session("fresh", agent="lead", title="New lead")

    enrolled = ensure_member(fresh, world.deps)

    assert enrolled.labels[F.LABEL_ID] == fresh.id
    assert enrolled.labels[F.LABEL_ROLE] == "lead"
    assert enrolled.labels[F.LABEL_STATE] == "active"
    assert enrolled.labels[F.LABEL_HEARTBEAT_S] == "3600"
    assert world.get("fresh").labels == enrolled.labels


def test_lazy_init_joins_the_parent_factory_when_spawned(world: FactoryWorld) -> None:
    pl = world.make_session(
        "pl2",
        agent="pl",
        title="PL · docs",
        labels={F.LABEL_PARENT: world.id("lead_a"), F.LABEL_STATE: "paused"},
    )

    enrolled = ensure_member(pl, world.deps)

    assert enrolled.labels[F.LABEL_ID] == world.id("lead_a")
    assert enrolled.labels[F.LABEL_ROLE] == "project_lead"
    # Existing labels are kept, missing defaults are filled in.
    assert enrolled.labels[F.LABEL_STATE] == "paused"
    assert enrolled.labels[F.LABEL_HEARTBEAT_S] == "7200"


def test_lazy_init_leaves_non_factory_and_enrolled_sessions_alone(world: FactoryWorld) -> None:
    plain = world.sessions["plain"]
    assert ensure_member(plain, world.deps).labels == {}
    assert world.get("plain").labels == {}

    pl = world.sessions["pl"]
    assert ensure_member(pl, world.deps).labels == pl.labels


def test_lazy_init_never_stamps_sub_agents(world: FactoryWorld) -> None:
    ic = world.sessions["ic"]
    assert ensure_member(ic, world.deps).labels == {}


# ── inheritance via the root ────────────────────────────


def test_sub_agents_inherit_membership_from_their_root(world: FactoryWorld) -> None:
    ic = world.sessions["ic"]
    assert resolve_factory_id(ic, world.store) == world.id("lead_a")

    m = membership_of(ic, world.deps)
    assert m.factory_id == world.id("lead_a")
    assert m.role == "ic"
    assert m.project == "api-version"
    assert m.is_top_level is False
    assert m.root_session_id == world.id("pl")
    assert m.parent_session_id == world.id("pl")
    # A sub-agent's alerts go to its parent.
    assert m.supervisor_session_id == world.id("pl")
    assert m.state is None and m.heartbeat_s is None


def test_sub_agent_role_comes_from_its_spec_with_ic_default(world: FactoryWorld) -> None:
    assert membership_of(world.sessions["reviewer"], world.deps).role == "reviewer"
    assert membership_of(world.sessions["helper"], world.deps).role == "ic"


def test_top_level_membership_reads_labels(world: FactoryWorld) -> None:
    m = membership_of(world.sessions["pl"], world.deps)
    assert m.to_dict() == {
        "session_id": world.id("pl"),
        "factory_id": world.id("lead_a"),
        "role": "project_lead",
        "project": "api-version",
        "parent_session_id": world.id("lead_a"),
        "supervisor_session_id": world.id("lead_b"),
        "state": "active",
        "heartbeat_s": 600,
        "health": None,
        "summary": None,
        "is_top_level": True,
        "root_session_id": world.id("pl"),
        "title": "PL · api-version",
    }


def test_non_members_have_no_factory_or_role(world: FactoryWorld) -> None:
    m = membership_of(world.sessions["plain"], world.deps)
    assert m.factory_id is None
    assert m.role is None


def test_factory_members_lists_top_level_members_only(world: FactoryWorld) -> None:
    ids = {c.id for c in factory_members(world.id("lead_a"), world.store)}
    assert ids == {world.id("lead_a"), world.id("lead_b"), world.id("pl")}


# ── aliases ─────────────────────────────────────────────


def _resolve(world: FactoryWorld, sender_key: str, to: str) -> str:
    sender = membership_of(world.sessions[sender_key], world.deps)
    return resolve_target(to, sender, world.store).id


def test_alias_parent(world: FactoryWorld) -> None:
    assert _resolve(world, "pl", "parent") == world.id("lead_a")
    assert _resolve(world, "ic", "parent") == world.id("pl")
    with pytest.raises(UnknownTargetError):
        _resolve(world, "lead_a", "parent")


def test_alias_lead_is_the_factory_root(world: FactoryWorld) -> None:
    assert _resolve(world, "pl", "lead") == world.id("lead_a")
    assert _resolve(world, "ic", "lead") == world.id("lead_a")


def test_alias_supervisor(world: FactoryWorld) -> None:
    assert _resolve(world, "pl", "supervisor") == world.id("lead_b")
    assert _resolve(world, "lead_a", "supervisor") == world.id("lead_b")
    assert _resolve(world, "reviewer", "supervisor") == world.id("pl")


def test_alias_supervisor_falls_back_to_the_root(world: FactoryWorld) -> None:
    world.store.delete_label(world.id("pl"), F.LABEL_SUPERVISOR)
    world.sessions["pl"] = world.get("pl")
    assert _resolve(world, "pl", "supervisor") == world.id("lead_a")


def test_alias_peer_lead(world: FactoryWorld) -> None:
    assert _resolve(world, "lead_a", "peer_lead") == world.id("lead_b")
    assert _resolve(world, "lead_b", "peer_lead") == world.id("lead_a")
    # From a non-lead, the peer lead is the non-root lead (Sentinel).
    assert _resolve(world, "pl", "peer_lead") == world.id("lead_b")
    assert _resolve(world, "ic", "peer_lead") == world.id("lead_b")


def test_alias_project(world: FactoryWorld) -> None:
    assert _resolve(world, "lead_b", "project:api-version") == world.id("pl")
    with pytest.raises(UnknownTargetError):
        _resolve(world, "lead_b", "project:unknown")
    # A project in another factory is not addressable.
    with pytest.raises(UnknownTargetError):
        _resolve(world, "other_lead", "project:api-version")


def _age(world: FactoryWorld, key: str, seconds: int) -> None:
    """Backdate a session's creation by *seconds*."""
    created_at = world.get(key).created_at - seconds
    with world.store._conv_engine.begin() as connection:
        connection.execute(
            update(SqlConversation)
            .where(
                SqlConversation.workspace_id == current_workspace_id(),
                SqlConversation.id == world.id(key),
            )
            .values(created_at=created_at)
        )


def test_alias_project_prefers_the_active_replacement(world: FactoryWorld) -> None:
    lead_a, old = world.id("lead_a"), world.id("pl")
    # Recovery: the failing PL is paused and a new PL takes the project an hour later.
    _age(world, "pl", 3600)
    world.store.set_labels(old, {F.LABEL_STATE: "paused"})
    new = world.make_session(
        "pl_new",
        agent="pl",
        title="PL · api-version (2)",
        labels={
            F.LABEL_ID: lead_a,
            F.LABEL_ROLE: "project_lead",
            F.LABEL_PROJECT: "api-version",
            F.LABEL_PARENT: lead_a,
            F.LABEL_STATE: "active",
        },
    ).id

    assert _resolve(world, "lead_b", "project:api-version") == new

    # An active PL wins over a newer paused one...
    world.store.set_labels(old, {F.LABEL_STATE: "active"})
    world.store.set_labels(new, {F.LABEL_STATE: "paused"})
    assert _resolve(world, "lead_b", "project:api-version") == old

    # ...and among active PLs the newest wins.
    world.store.set_labels(new, {F.LABEL_STATE: "active"})
    assert _resolve(world, "lead_b", "project:api-version") == new


def test_alias_peer_lead_prefers_an_active_lead(world: FactoryWorld) -> None:
    lead_a = world.id("lead_a")
    world.store.set_labels(world.id("lead_b"), {F.LABEL_STATE: "paused"})
    lead_c = world.make_session(
        "lead_c",
        agent="lead",
        title="Lead C",
        labels={F.LABEL_ID: lead_a, F.LABEL_ROLE: "lead", F.LABEL_STATE: "active"},
    ).id

    assert _resolve(world, "lead_a", "peer_lead") == lead_c
    assert _resolve(world, "pl", "peer_lead") == lead_c


def test_plain_session_ids_and_unknown_addresses(world: FactoryWorld) -> None:
    assert _resolve(world, "lead_a", world.id("ic")) == world.id("ic")
    for address in ("conv_does_not_exist", "", "   ", "boss"):
        with pytest.raises(UnknownTargetError):
            _resolve(world, "lead_a", address)
