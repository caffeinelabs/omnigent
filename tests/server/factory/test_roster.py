"""``GET /v1/factory/roster``: members, their sub-agents, status and liveness."""

from __future__ import annotations

from typing import Any

import pytest

from omnigent import factory as F
from omnigent.server.routes._sessions.common import _session_status_cache
from tests.server.factory.conftest import FactoryApi, FactoryWorld


async def _roster(api: FactoryApi, sender: str, user: str | None = None) -> Any:
    headers = {"X-Test-User": user} if user else {}
    resp = await api.client.get(
        "/v1/factory/roster", params={"from_session_id": sender}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


class _Tunnels:
    def __init__(self, online: set[str]) -> None:
        self.online = online

    def get(self, runner_id: str) -> object | None:
        return object() if runner_id in self.online else None


async def test_roster_lists_members_and_their_sub_agents(
    world: FactoryWorld,
    api: FactoryApi,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pl = world.id("pl")
    world.store.set_labels(pl, {F.LABEL_SUMMARY: "2 ICs on /version", F.LABEL_HEALTH: "ok"})
    world.store.set_labels(world.id("reviewer"), {F.LABEL_HEALTH: "blocked"})
    monkeypatch.setitem(_session_status_cache, pl, "running")
    monkeypatch.setitem(_session_status_cache, world.id("ic"), "running")
    api.app.state.tunnel_registry = _Tunnels({"runner_pl"})

    roster = await _roster(api, world.id("ic"))

    assert roster["factory_id"] == world.id("lead_a")
    members = {m["session_id"]: m for m in roster["members"]}
    assert set(members) == {world.id("lead_a"), world.id("lead_b"), pl}

    pl_row = members[pl]
    assert {k: v for k, v in pl_row.items() if k not in ("children", "last_activity_at")} == {
        "session_id": pl,
        "title": "PL · api-version",
        "role": "project_lead",
        "project": "api-version",
        "parent_session_id": world.id("lead_a"),
        "supervisor_session_id": world.id("lead_b"),
        "status": "running",
        "runner_online": True,
        "heartbeat_s": 600,
        "state": "active",
        "health": "ok",
        "summary": "2 ICs on /version",
        "url": f"/c/{pl}",
    }
    assert isinstance(pl_row["last_activity_at"], int)

    children = {c["session_id"]: c for c in pl_row["children"]}
    assert set(children) == {world.id("ic"), world.id("reviewer"), world.id("helper")}
    ic_row = children[world.id("ic")]
    assert ic_row["agent"] == "ic"
    assert ic_row["role"] == "ic"
    assert ic_row["status"] == "running"
    assert ic_row["url"] == f"/c/{world.id('ic')}"
    assert children[world.id("reviewer")]["role"] == "reviewer"
    assert children[world.id("reviewer")]["health"] == "blocked"
    assert children[world.id("reviewer")]["status"] == "idle"
    assert children[world.id("helper")]["role"] == "ic"

    lead_row = members[world.id("lead_a")]
    assert lead_row["role"] == "lead"
    assert lead_row["runner_online"] is False
    assert lead_row["children"] == []


async def test_roster_excludes_archived_members(world: FactoryWorld, api: FactoryApi) -> None:
    world.store.update_conversation(world.id("lead_b"), archived=True)

    roster = await _roster(api, world.id("lead_a"))

    assert {m["session_id"] for m in roster["members"]} == {world.id("lead_a"), world.id("pl")}


async def test_roster_for_a_non_member_is_empty(world: FactoryWorld, api: FactoryApi) -> None:
    assert await _roster(api, world.id("plain")) == {"factory_id": None, "members": []}


async def test_roster_registers_a_fresh_lead_as_root(world: FactoryWorld, api: FactoryApi) -> None:
    fresh = world.make_session("fresh", agent="lead", title="New factory")

    roster = await _roster(api, fresh.id)

    assert roster["factory_id"] == fresh.id
    assert [m["session_id"] for m in roster["members"]] == [fresh.id]
    assert roster["members"][0]["heartbeat_s"] == F.DEFAULT_HEARTBEAT_S["lead"]


async def test_roster_hides_members_the_caller_cannot_read(
    world: FactoryWorld, authed_api: FactoryApi, db_uri: str
) -> None:
    from omnigent.server.auth import LEVEL_OWNER
    from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore

    # Bob's session claims alice's factory id; his roster must not reveal hers.
    intruder = world.make_session(
        "intruder",
        agent="lead",
        title="Intruder",
        labels={F.LABEL_ID: world.id("lead_a"), F.LABEL_ROLE: "lead"},
    )
    SqlAlchemyPermissionStore(db_uri).grant("bob", intruder.id, LEVEL_OWNER)

    bobs = await _roster(authed_api, intruder.id, user="bob")
    alices = await _roster(authed_api, world.id("lead_a"), user="alice")

    assert [m["session_id"] for m in bobs["members"]] == [intruder.id]
    assert intruder.id not in {m["session_id"] for m in alices["members"]}
