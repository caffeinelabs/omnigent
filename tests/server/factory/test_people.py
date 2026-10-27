"""The factory roster: grants copied onto every member, drift repair, the People API,
the viewer block, the managed-grant guard and admission."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.runner.identity import RUNNER_TUNNEL_TOKEN_HEADER
from omnigent.server import oidc_access
from omnigent.server import presence as session_presence
from omnigent.server.admin_list import AdminList
from omnigent.server.auth import LEVEL_EDIT, LEVEL_MANAGE, LEVEL_OWNER, LEVEL_READ, SharingMode
from omnigent.server.factory import live, people, snapshot
from omnigent.server.factory import presence as factory_presence
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit, error_code
from tests.server.factory.conftest import FactoryWorld

MIA = "mia@caffeine.ai"
OPS = "ops@caffeine.ai"
DAVE = "dave@caffeine.ai"


@pytest.fixture(autouse=True)
def _fresh_state() -> Iterator[None]:
    snapshot.reset_cache()
    factory_presence.registry.reset()
    session_presence.reset_for_tests()
    oidc_access.register_admission_policy(None)
    yield
    snapshot.reset_cache()
    factory_presence.registry.reset()
    session_presence.reset_for_tests()
    oidc_access.register_admission_policy(None)


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in build_cockpit(world, db_uri, monkeypatch):
        yield value


@pytest.fixture()
def published(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, list[str], list[Any]]]:
    """Frames pushed straight to users (the removed user's own frame)."""
    frames: list[tuple[str, list[str], list[Any]]] = []
    monkeypatch.setattr(
        live,
        "publish",
        lambda fid, scopes, users: frames.append((fid, sorted(scopes), list(users))),
    )
    return frames


def _members(cockpit: Cockpit) -> list[str]:
    return [cockpit.world.id(k) for k in ("lead_a", "lead_b", "pl")]


def _level(cockpit: Cockpit, user: str, key: str) -> int | None:
    grant = cockpit.permissions.get(user, cockpit.world.id(key))
    return grant.level if grant is not None else None


async def _put(
    cockpit: Cockpit, user: str, role: str, *, as_user: str = "alice"
) -> httpx.Response:
    return await cockpit.client.put(
        f"/v1/factory/factories/{cockpit.fid}/people/{user}",
        json={"role": role},
        headers={"X-Test-User": as_user},
    )


async def _delete(cockpit: Cockpit, user: str, *, as_user: str = "alice") -> httpx.Response:
    return await cockpit.client.delete(
        f"/v1/factory/factories/{cockpit.fid}/people/{user}", headers={"X-Test-User": as_user}
    )


async def _get(cockpit: Cockpit, path: str, *, user: str = "alice") -> httpx.Response:
    return await cockpit.client.get(path, headers={"X-Test-User": user})


# ── Copy, revoke, drift ────────────────────────────────────────


async def test_an_invite_copies_the_role_onto_every_member(cockpit: Cockpit) -> None:
    cockpit.permissions.grant(DAVE, cockpit.world.id("pl"), LEVEL_READ)

    resp = await _put(cockpit, MIA, "operator")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] is True
    assert body["admission"] == "ok"
    assert body["person"]["role"] == "operator"
    for key in ("lead_a", "lead_b", "pl"):
        assert _level(cockpit, MIA, key) == LEVEL_EDIT
    # Owner rows and manual grants are untouched; sub-agents inherit, no grant of their own.
    for key in ("lead_a", "lead_b", "pl"):
        assert _level(cockpit, "alice", key) == LEVEL_OWNER
    assert _level(cockpit, DAVE, "pl") == LEVEL_READ
    assert _level(cockpit, MIA, "ic") is None
    assert _level(cockpit, MIA, "other_lead") is None
    # The creator joins the roster as an owner flagged is_creator.
    roster = {p.user_id: p for p in cockpit.store.list_people(cockpit.fid)}
    assert roster["alice"].is_creator and roster["alice"].role == "owner"
    [event] = cockpit.events("people.invited")
    assert event.data["user"] == MIA and event.data["role"] == "operator"
    assert event.data["by"] == "alice (creator)"
    assert event.data["by_kind"] == "human"
    assert cockpit.mailer.texts_to(cockpit.fid) == [f"[Factory notice · {MIA} joined as operator]"]
    assert (cockpit.fid, ["snapshot", "people"]) in cockpit.announces


async def test_a_role_change_rewrites_every_member_grant(cockpit: Cockpit) -> None:
    await _put(cockpit, MIA, "operator")

    resp = await _put(cockpit, MIA, "owner")

    assert resp.status_code == 200, resp.text
    assert resp.json()["previous_role"] == "operator"
    for key in ("lead_a", "lead_b", "pl"):
        assert _level(cockpit, MIA, key) == LEVEL_MANAGE
    [event] = cockpit.events("people.role_changed")
    assert (event.data["from_role"], event.data["role"]) == ("operator", "owner")
    assert cockpit.mailer.texts_to(cockpit.fid)[-1] == f"[Factory notice · {MIA} is now owner]"


async def test_removing_revokes_only_the_roster_grants(
    cockpit: Cockpit, published: list[Any]
) -> None:
    await _put(cockpit, MIA, "operator")
    cockpit.permissions.grant(DAVE, cockpit.world.id("pl"), LEVEL_EDIT)

    resp = await _delete(cockpit, MIA)

    assert resp.status_code == 200, resp.text
    assert resp.json()["revoked"] == 3
    for key in ("lead_a", "lead_b", "pl"):
        assert _level(cockpit, MIA, key) is None
        assert _level(cockpit, "alice", key) == LEVEL_OWNER
    assert _level(cockpit, DAVE, "pl") == LEVEL_EDIT
    assert _level(cockpit, "carol", "pl") == LEVEL_EDIT
    assert cockpit.store.get_person(cockpit.fid, MIA) is None
    [event] = cockpit.events("people.removed")
    assert event.data["user"] == MIA
    assert cockpit.mailer.texts_to(cockpit.fid)[-1] == (
        f"[Factory notice · {MIA} was removed by alice (creator)]"
    )
    # The removed user is told directly: their refetch answers 404.
    assert (cockpit.fid, ["people", "snapshot"], [MIA]) in published
    gone = await _get(cockpit, f"/v1/factory/factories/{cockpit.fid}", user=MIA)
    assert gone.status_code == 404


async def test_leaving_is_self_service_and_logged_as_left(
    cockpit: Cockpit, published: list[Any]
) -> None:
    await _put(cockpit, OPS, "viewer")

    resp = await _delete(cockpit, OPS, as_user=OPS)

    assert resp.status_code == 200, resp.text
    assert resp.json()["left"] is True
    assert [e.data["user"] for e in cockpit.events("people.left")] == [OPS]
    assert _level(cockpit, OPS, "pl") is None


async def test_removing_closes_the_users_open_streams(
    cockpit: Cockpit, published: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.runtime import session_stream

    await _put(cockpit, MIA, "operator")
    pl = cockpit.world.id("pl")
    ic = cockpit.world.id("ic")
    session_presence.connect(pl, ic, MIA, False)
    closed: list[str] = []
    monkeypatch.setattr(session_stream, "close", closed.append)

    await _delete(cockpit, MIA)

    assert closed == [ic]


async def test_drift_is_repaired_without_touching_owner_or_manual_grants(
    cockpit: Cockpit,
) -> None:
    await _put(cockpit, MIA, "operator")
    cockpit.permissions.revoke(MIA, cockpit.world.id("lead_b"))
    cockpit.permissions.grant(MIA, cockpit.world.id("pl"), LEVEL_READ)
    cockpit.permissions.grant(DAVE, cockpit.world.id("pl"), LEVEL_MANAGE)
    root = cockpit.world.get("lead_a")

    repaired = await people.repair_drift(cockpit.app.state, root)

    assert repaired == 2
    assert _level(cockpit, MIA, "lead_b") == LEVEL_EDIT
    assert _level(cockpit, MIA, "pl") == LEVEL_EDIT
    assert _level(cockpit, DAVE, "pl") == LEVEL_MANAGE
    assert _level(cockpit, "alice", "pl") == LEVEL_OWNER
    [event] = cockpit.events("people.drift_repaired")
    assert event.data["count"] == 2
    assert await people.repair_drift(cockpit.app.state, root) == 0
    assert len(cockpit.events("people.drift_repaired")) == 1


async def test_reconcile_repairs_a_member_that_missed_the_copy(cockpit: Cockpit) -> None:
    from omnigent.server.factory.background import reconcile

    await _put(cockpit, MIA, "operator")
    late = cockpit.world.make_session(
        "late_pl",
        agent="pl",
        title="PL · db-version",
        labels={
            F.LABEL_ID: cockpit.fid,
            F.LABEL_ROLE: "project_lead",
            F.LABEL_PROJECT: "db-version",
            F.LABEL_STATE: "active",
        },
    )
    cockpit.permissions.grant("alice", late.id, LEVEL_OWNER)

    report = await reconcile.reconcile_once(cockpit.app.state)

    assert (cockpit.fid, 1) in report.grants_repaired
    assert _level(cockpit, MIA, "late_pl") == LEVEL_EDIT
    snap = await _get(cockpit, f"/v1/factory/factories/{cockpit.fid}", user=MIA)
    assert snap.json()["hidden_projects"] == 0


async def test_a_new_member_gets_the_roster_at_create(cockpit: Cockpit) -> None:
    await _put(cockpit, MIA, "viewer")
    member = cockpit.world.make_session(
        "late_pl",
        agent="pl",
        title="PL · docs",
        labels={F.LABEL_ID: cockpit.fid, F.LABEL_ROLE: "project_lead"},
    )
    cockpit.permissions.grant("alice", member.id, LEVEL_OWNER)

    await people.mirror_member_on_create(cockpit.app.state, member)

    assert _level(cockpit, MIA, "late_pl") == LEVEL_READ
    assert _level(cockpit, "alice", "late_pl") == LEVEL_OWNER


async def test_removing_revokes_grants_on_archived_members(cockpit: Cockpit) -> None:
    await _put(cockpit, MIA, "owner")
    cockpit.world.store.update_conversation(cockpit.world.id("pl"), archived=True)

    await _put(cockpit, MIA, "viewer")
    assert _level(cockpit, MIA, "pl") == LEVEL_READ
    resp = await _delete(cockpit, MIA)

    assert resp.status_code == 200, resp.text
    assert resp.json()["revoked"] == 3
    for key in ("lead_a", "lead_b", "pl"):
        assert _level(cockpit, MIA, key) is None
    assert _level(cockpit, "carol", "pl") == LEVEL_EDIT
    assert _level(cockpit, "alice", "pl") == LEVEL_OWNER


async def test_a_session_someone_else_labels_is_not_a_member(
    cockpit: Cockpit, db_uri: str, tmp_path: Path
) -> None:
    from omnigent.server.factory.context import factory_deps

    await _put(cockpit, MIA, "operator")
    state = cockpit.app.state
    foreign = cockpit.world.make_session(
        "foreign",
        agent="pl",
        title="Carol's PL",
        labels={F.LABEL_ID: cockpit.fid, F.LABEL_ROLE: "project_lead"},
    )
    cockpit.permissions.grant("carol", foreign.id, LEVEL_OWNER)
    cockpit.permissions.grant(DAVE, foreign.id, LEVEL_READ)

    managed = people.managed_sessions(factory_deps(state), cockpit.fid)
    await people.mirror_member_on_create(state, foreign)
    repaired = await people.repair_drift(state, cockpit.world.get("lead_a"))

    assert foreign.id not in managed and cockpit.world.id("pl") in managed
    assert repaired == 0
    assert _level(cockpit, MIA, "foreign") is None
    # Carol's own shares are hers to manage; the creator's members stay guarded.
    cockpit.permissions.grant(MIA, foreign.id, LEVEL_READ)
    assert await people.managed_grant_conflict(state, foreign.id, MIA) is None
    assert await people.managed_grant_conflict(state, cockpit.world.id("pl"), MIA) is not None
    await _delete(cockpit, MIA)
    assert _level(cockpit, MIA, "foreign") == LEVEL_READ
    # The create hook skips a labelled session a non-creator creates.
    await _put(cockpit, OPS, "operator")
    app = _real_app(db_uri, tmp_path, cockpit.permissions)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/v1/sessions",
            json={"agent_id": cockpit.world.agents["pl"], "labels": {F.LABEL_ID: cockpit.fid}},
            headers={"X-Forwarded-Email": "carol"},
        )
    assert created.status_code == 201, created.text
    assert cockpit.permissions.get(OPS, created.json()["id"]) is None


async def test_removal_drops_the_roster_row_before_revoking(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _put(cockpit, MIA, "operator")
    seen: list[Any] = []
    revoke = people._revoke

    def spy(deps: Any, user_id: str, session_ids: Any) -> list[str]:
        seen.append(cockpit.store.get_person(cockpit.fid, user_id))
        return revoke(deps, user_id, session_ids)

    monkeypatch.setattr(people, "_revoke", spy)

    await _delete(cockpit, MIA)

    assert seen == [None]


def test_roster_writes_for_one_factory_wait_for_each_other(cockpit: Cockpit) -> None:
    import threading
    import uuid

    from omnigent.server.factory.context import factory_deps

    deps = factory_deps(cockpit.app.state)
    lock = people._roster_lock(cockpit.fid)
    assert people._roster_lock(str(uuid.UUID(cockpit.fid))) is lock
    assert people._roster_lock(cockpit.world.id("pl")) is not lock
    done = threading.Event()

    def repair() -> None:
        people._repair_sync(deps, cockpit.fid, None)
        done.set()

    with lock:
        worker = threading.Thread(target=repair)
        worker.start()
        assert not done.wait(0.3)
    worker.join(10)
    assert done.is_set()


async def test_new_members_and_repairs_follow_the_sharing_mode(cockpit: Cockpit) -> None:
    await _put(cockpit, MIA, "operator")
    state = cockpit.app.state
    root = cockpit.world.get("lead_a")
    state.sharing_mode = lambda: SharingMode.READ_ONLY
    late = cockpit.world.make_session(
        "late_pl",
        agent="pl",
        title="PL · docs",
        labels={F.LABEL_ID: cockpit.fid, F.LABEL_ROLE: "project_lead"},
    )
    cockpit.permissions.grant("alice", late.id, LEVEL_OWNER)
    cockpit.permissions.revoke(MIA, cockpit.world.id("lead_b"))

    await people.mirror_member_on_create(state, late)
    repaired = await people.repair_drift(state, root)

    assert _level(cockpit, MIA, "late_pl") == LEVEL_READ
    assert repaired == 1
    assert _level(cockpit, MIA, "lead_b") == LEVEL_READ
    # Existing grants are not new shares: read-only keeps them.
    assert _level(cockpit, MIA, "pl") == LEVEL_EDIT
    state.sharing_mode = lambda: SharingMode.OFF
    cockpit.permissions.revoke(MIA, cockpit.world.id("pl"))
    assert await people.repair_drift(state, root) == 0
    assert _level(cockpit, MIA, "pl") is None


async def test_drift_announces_each_user_only_their_new_sessions(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.server.routes._sessions import helpers

    await _put(cockpit, MIA, "operator")
    await _put(cockpit, OPS, "viewer")
    cockpit.permissions.revoke(MIA, cockpit.world.id("lead_b"))
    cockpit.permissions.grant(OPS, cockpit.world.id("pl"), LEVEL_EDIT)
    added: list[tuple[str, str]] = []
    monkeypatch.setattr(
        helpers, "_announce_session_added", lambda user, sid: added.append((user, sid))
    )

    assert await people.repair_drift(cockpit.app.state, cockpit.world.get("lead_a")) == 2

    assert added == [(MIA, cockpit.world.id("lead_b"))]
    assert _level(cockpit, OPS, "pl") == LEVEL_READ


# ── PUT / DELETE refusals, sharing mode, admission ─────────────


@pytest.mark.parametrize(
    ("as_user", "status"),
    [("carol", 403), ("bob", 404)],
)
async def test_only_owners_invite(cockpit: Cockpit, as_user: str, status: int) -> None:
    resp = await _put(cockpit, MIA, "viewer", as_user=as_user)

    assert resp.status_code == status
    assert cockpit.store.get_person(cockpit.fid, MIA) is None


@pytest.mark.parametrize(
    ("target", "status", "code"),
    [
        ("__public__", 400, "reserved_user"),
        ("local", 400, "reserved_user"),
        ("alice", 409, "creator"),
    ],
)
async def test_reserved_users_and_the_creator_are_refused(
    cockpit: Cockpit, target: str, status: int, code: str
) -> None:
    put = await _put(cockpit, target, "viewer")
    delete = await _delete(cockpit, target)

    assert (put.status_code, error_code(put)) == (status, code)
    assert (delete.status_code, error_code(delete)) == (status, code)


async def test_an_unknown_role_and_self_changes_are_refused(cockpit: Cockpit) -> None:
    await _put(cockpit, MIA, "owner")

    bad = await _put(cockpit, OPS, "admin")
    self_change = await _put(cockpit, MIA, "viewer", as_user=MIA)
    missing = await _delete(cockpit, OPS)

    assert (bad.status_code, error_code(bad)) == (400, "invalid_role")
    assert (self_change.status_code, error_code(self_change)) == (403, "cannot_change_self")
    assert (missing.status_code, error_code(missing)) == (404, "not_on_roster")


async def test_an_owner_on_the_roster_may_invite(cockpit: Cockpit) -> None:
    await _put(cockpit, MIA, "owner")

    resp = await _put(cockpit, OPS, "viewer", as_user=MIA)

    assert resp.status_code == 200, resp.text
    assert cockpit.events("people.invited")[-1].data["by"] == f"{MIA} (owner)"


@pytest.mark.parametrize(
    ("mode", "role", "status"),
    [
        (SharingMode.OFF, "viewer", 403),
        (SharingMode.READ_ONLY, "operator", 403),
        (SharingMode.READ_ONLY, "viewer", 200),
    ],
)
async def test_the_sharing_mode_gates_invites(
    cockpit: Cockpit, mode: SharingMode, role: str, status: int
) -> None:
    cockpit.app.state.sharing_mode = lambda: mode

    resp = await _put(cockpit, MIA, role)

    assert resp.status_code == status, resp.text
    if status == 403:
        assert error_code(resp) == "sharing_refused"
        assert cockpit.store.get_person(cockpit.fid, MIA) is None


async def test_the_sharing_mode_checks_every_member(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dataclasses

    store = cockpit.world.store
    rows = store.get_conversations
    pl = cockpit.world.id("pl")

    def with_home(ids: list[str]) -> dict[str, Any]:
        out = rows(ids)
        if pl in out:
            out[pl] = dataclasses.replace(out[pl], workspace="/home/alice")
        return out

    monkeypatch.setattr(store, "get_conversations", with_home)
    cockpit.app.state.sharing_mode = lambda: SharingMode.RESTRICTED_READ_ONLY

    resp = await _put(cockpit, MIA, "viewer")

    assert (resp.status_code, error_code(resp)) == (403, "sharing_refused")
    assert "home" in resp.json()["error"]["message"]


async def test_admission_reports_blocked_for_a_domain_the_server_refuses(
    cockpit: Cockpit, tmp_path: Path
) -> None:
    oidc_access.register_admission_policy(
        oidc_access.OidcAdmissionPolicy(
            env_allowed_domains=frozenset({"caffeine.ai"}),
            domains_file_path=tmp_path / "allowed_domains",
            admin_list=AdminList(tmp_path / "admins"),
        )
    )

    blocked = await _put(cockpit, "mia@gmail.com", "operator")
    admitted = await _put(cockpit, MIA, "operator")

    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["admission"] == "blocked"
    assert admitted.json()["admission"] == "ok"


# ── GET people, search ─────────────────────────────────────────


async def test_the_roster_names_who_agents_run_as(cockpit: Cockpit) -> None:
    await _put(cockpit, MIA, "operator")
    factory_presence.registry.heartbeat(cockpit.fid, MIA, "overview", False)

    resp = await _get(cockpit, f"/v1/factory/factories/{cockpit.fid}/people", user=MIA)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["runs_as"] == {"user_id": "alice", "name": "alice"}
    rows = {p["user_id"]: p for p in body["data"]}
    assert rows["alice"]["is_creator"] is True
    assert rows[MIA]["role"] == "operator"
    assert rows[MIA]["present"] is True and rows[MIA]["location"] == "overview"
    assert body["viewer"]["role"] == "operator"


async def test_search_needs_three_characters_and_manage(cockpit: Cockpit) -> None:
    for n in range(12):
        cockpit.permissions.ensure_user(f"user{n:02d}@caffeine.ai")
    url = f"/v1/factory/factories/{cockpit.fid}/people/search"

    short = await _get(cockpit, f"{url}?q=us")
    found = await _get(cockpit, f"{url}?q=user")
    exact = await _get(cockpit, f"{url}?q=new.person@caffeine.ai")
    operator = await _get(cockpit, f"{url}?q=user", user="carol")

    assert (short.status_code, error_code(short)) == (400, "query_too_short")
    assert found.status_code == 200
    assert len(found.json()["data"]) == 10
    assert all(r["user_id"].startswith("user") for r in found.json()["data"])
    assert exact.json()["data"] == [
        {
            "user_id": "new.person@caffeine.ai",
            "name": "new.person@caffeine.ai",
            "known": False,
            "role": None,
            "admission": "ok",
        }
    ]
    assert operator.status_code == 403
    agent = await cockpit.client.get(
        f"{url}?q=user", headers={"X-Test-User": "alice", RUNNER_TUNNEL_TOKEN_HEADER: "rt_a.b"}
    )
    assert (agent.status_code, error_code(agent)) == (403, "human_only")


# ── The viewer block ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("user", "role", "level", "allowed"),
    [
        ("alice", "creator", 4, set(people.VIEWER_CAN_LEVELS)),
        (
            "owner_p",
            "owner",
            3,
            set(people.VIEWER_CAN_LEVELS) - {"terminal", "start"},
        ),
        (
            "operator_p",
            "operator",
            2,
            {"post", "answer", "review", "control", "intervene", "apply_projects"},
        ),
        ("viewer_p", "viewer", 1, set()),
    ],
)
async def test_viewer_can_follows_the_role(
    cockpit: Cockpit, user: str, role: str, level: int, allowed: set[str]
) -> None:
    if user != "alice":
        await _put(cockpit, user, role)

    resp = await _get(cockpit, f"/v1/factory/factories/{cockpit.fid}", user=user)

    assert resp.status_code == 200, resp.text
    viewer = resp.json()["viewer"]
    assert viewer["role"] == role
    assert viewer["level"] == level
    assert viewer["is_creator"] is (user == "alice")
    assert {k for k, v in viewer["can"].items() if v} == allowed
    assert resp.json()["hidden_projects"] == 0
    assert resp.json()["people_count"] == (1 if user == "alice" else 2)


# ── The managed-grant guard (the session permissions route) ────


def _real_app(db_uri: str, tmp_path: Path, permissions: Any) -> Any:
    from omnigent.runtime.agent_cache import AgentCache
    from omnigent.server.app import create_app
    from omnigent.server.auth import UnifiedAuthProvider
    from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
    from omnigent.stores.artifact_store.local import LocalArtifactStore
    from omnigent.stores.conversation_store.sqlalchemy_store import (
        SqlAlchemyConversationStore,
    )
    from omnigent.stores.factory_store.sqlalchemy_store import SqlAlchemyFactoryStore
    from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore

    artifacts = LocalArtifactStore(str(tmp_path / "real-artifacts"))
    return create_app(
        agent_store=SqlAlchemyAgentStore(db_uri),
        file_store=SqlAlchemyFileStore(db_uri),
        conversation_store=SqlAlchemyConversationStore(db_uri),
        artifact_store=artifacts,
        agent_cache=AgentCache(artifact_store=artifacts, cache_dir=tmp_path / "real-cache"),
        permission_store=permissions,
        auth_provider=UnifiedAuthProvider(source="header"),
        factory_store=SqlAlchemyFactoryStore(db_uri),
    )


async def test_revoking_a_factory_managed_grant_is_409(
    cockpit: Cockpit, db_uri: str, tmp_path: Path
) -> None:
    await _put(cockpit, MIA, "operator")
    cockpit.permissions.grant(DAVE, cockpit.world.id("pl"), LEVEL_READ)
    app = _real_app(db_uri, tmp_path, cockpit.permissions)
    pl = cockpit.world.id("pl")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        revoke = await client.delete(
            f"/v1/sessions/{pl}/permissions/{MIA}", headers={"X-Forwarded-Email": "alice"}
        )
        leave = await client.delete(
            f"/v1/sessions/{pl}/permissions/{MIA}", headers={"X-Forwarded-Email": MIA}
        )
        manual = await client.delete(
            f"/v1/sessions/{pl}/permissions/{DAVE}", headers={"X-Forwarded-Email": "alice"}
        )

    assert revoke.status_code == 409, revoke.text
    assert revoke.json()["error"]["code"] == "managed_by_factory"
    assert "People page" in revoke.json()["error"]["message"]
    assert leave.status_code == 409
    assert manual.status_code == 204
    assert _level(cockpit, MIA, "pl") == LEVEL_EDIT
    assert _level(cockpit, DAVE, "pl") is None


async def test_the_create_hook_copies_the_roster_onto_a_new_member(
    cockpit: Cockpit, db_uri: str, tmp_path: Path
) -> None:
    await _put(cockpit, MIA, "operator")
    app = _real_app(db_uri, tmp_path, cockpit.permissions)
    labels = {
        F.LABEL_ID: cockpit.fid,
        F.LABEL_ROLE: "project_lead",
        F.LABEL_PROJECT: "db-version",
        F.LABEL_PARENT: cockpit.fid,
    }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/v1/sessions",
            json={"agent_id": cockpit.world.agents["pl"], "labels": labels},
            headers={"X-Forwarded-Email": "alice"},
        )
        plain = await client.post(
            "/v1/sessions",
            json={"agent_id": cockpit.world.agents["plain"]},
            headers={"X-Forwarded-Email": "alice"},
        )

    assert created.status_code == 201, created.text
    member = created.json()["id"]
    grant = cockpit.permissions.get(MIA, member)
    assert grant is not None and grant.level == LEVEL_EDIT
    owner = cockpit.permissions.get("alice", member)
    assert owner is not None and owner.level == LEVEL_OWNER
    assert cockpit.permissions.get(MIA, plain.json()["id"]) is None


# ── Access requests ────────────────────────────────────────────


async def test_a_viewer_requests_operator_and_an_owner_grants_it(cockpit: Cockpit) -> None:
    await _put(cockpit, OPS, "viewer")
    await _put(cockpit, MIA, "operator")
    url = f"/v1/factory/factories/{cockpit.fid}/access-requests"

    resp = await cockpit.post(url, {"note": "I run the deploys"}, user=OPS)
    again = await cockpit.post(url, {}, user=OPS)
    already = await cockpit.post(url, {"role": "operator"}, user=MIA)

    assert resp.status_code == 201, resp.text
    decision = resp.json()["decision"]
    assert decision["source"] == "access_request"
    assert decision["assignee"] == "owners"
    assert decision["context"] == "I run the deploys"
    grant = next(o for o in decision["options"] if o["action"] == "grant_role")
    assert (grant["target"], grant["role"]) == (f"person:{OPS}", "operator")
    assert again.json()["deduped"] is True
    assert (already.status_code, error_code(already)) == (409, "already_has_role")
    [requested] = cockpit.events("access.requested")
    assert requested.data["user"] == OPS
    answer = f"/v1/factory/decisions/{decision['id']}/answer"

    by_operator = await cockpit.post(answer, {"option_id": "grant"}, user=MIA)
    by_creator = await cockpit.post(answer, {"option_id": "grant"}, user="alice")

    assert (by_operator.status_code, error_code(by_operator)) == (403, "not_assigned")
    assert by_creator.status_code == 200, by_creator.text
    assert cockpit.store.get_person(cockpit.fid, OPS).role == "operator"  # type: ignore[union-attr]
    for key in ("lead_a", "lead_b", "pl"):
        assert _level(cockpit, OPS, key) == LEVEL_EDIT
    [changed] = cockpit.events("people.role_changed")
    assert (changed.data["from_role"], changed.data["role"]) == ("viewer", "operator")


async def test_granting_an_access_request_follows_the_sharing_mode(cockpit: Cockpit) -> None:
    await _put(cockpit, OPS, "viewer")
    resp = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/access-requests", {"role": "operator"}, user=OPS
    )
    cockpit.app.state.sharing_mode = lambda: SharingMode.READ_ONLY

    granted = await cockpit.post(
        f"/v1/factory/decisions/{resp.json()['decision']['id']}/answer",
        {"option_id": "grant"},
        user="alice",
    )

    assert (granted.status_code, error_code(granted)) == (403, "sharing_refused")
    assert cockpit.store.get_person(cockpit.fid, OPS).role == "viewer"  # type: ignore[union-attr]


async def test_declining_an_access_request_changes_nothing(cockpit: Cockpit) -> None:
    await _put(cockpit, OPS, "viewer")
    resp = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/access-requests", {"role": "owner"}, user=OPS
    )
    decision = resp.json()["decision"]

    declined = await cockpit.post(
        f"/v1/factory/decisions/{decision['id']}/answer", {"option_id": "decline"}, user="alice"
    )

    assert declined.status_code == 200, declined.text
    assert cockpit.store.get_person(cockpit.fid, OPS).role == "viewer"  # type: ignore[union-attr]
    assert _level(cockpit, OPS, "pl") == LEVEL_READ


async def test_the_agent_roster_lists_the_humans(cockpit: Cockpit) -> None:
    await _put(cockpit, MIA, "operator")
    await _put(cockpit, OPS, "viewer")
    factory_presence.registry.heartbeat(cockpit.fid, MIA, "overview", False)

    resp = await _get(cockpit, f"/v1/factory/roster?from_session_id={cockpit.world.id('pl')}")

    assert resp.status_code == 200, resp.text
    humans = resp.json()["humans"]
    assert [(h["name"], h["role"], h["present"]) for h in humans] == [
        ("alice", "creator", False),
        (MIA, "operator", True),
        (OPS, "viewer", False),
    ]
    assert humans[1]["last_seen"] is not None
    assert {m["session_id"] for m in resp.json()["members"]} >= set(_members(cockpit))
