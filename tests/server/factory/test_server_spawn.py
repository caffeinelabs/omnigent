"""The server spawns a project's PL itself (``server/factory/spawn.py``).

The create is the exact body ``sys_factory_spawn`` sends (the fixture shared
with ``tests/runner/test_factory_spawn.py``), made in process as the factory's
creator with a short-lived runner token. Then the brief goes out once under a
deterministic mail id, ``member.spawned`` is logged and Atlas is told. The pod
cap opens a decision, two failed creates hand the spawn back to Atlas, and a
spawn a restart cut off is finished exactly once.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.server.factory import spawn
from omnigent.server.factory.charter import spawn_owner
from tests.runner.factory_fakes import pl_create_body
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import (
    FakeCreates,
    header_cockpit,
    project,
    propose,
    settle,
    spawning_cockpit,
)


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in header_cockpit(world, db_uri, monkeypatch):
        yield value


@pytest_asyncio.fixture()
async def spawning(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[Cockpit, FakeCreates]]:
    async for value in spawning_cockpit(world, db_uri, monkeypatch):
        yield value


def _add(cockpit: Cockpit, slug: str = "db-version", **extra: Any) -> Any:
    return cockpit.post(f"/v1/factory/factories/{cockpit.fid}/projects", project(slug, **extra))


async def test_the_create_matches_sys_factory_spawn(
    spawning: tuple[Cockpit, FakeCreates],
) -> None:
    from omnigent.server.factory.telemetry import versions

    cockpit, creates = spawning

    resp = await _add(cockpit)
    await settle()

    assert resp.status_code == 201, resp.text
    agent_id = cockpit.world.agents["pl"]
    [call] = creates.calls
    assert call["path"] == "/v1/sessions"
    assert call["authorization"] == "Bearer runner-token-for-alice-300"
    assert call["body"] == pl_create_body(
        agent_id=agent_id,
        factory_id=cockpit.fid,
        parent=cockpit.fid,
        supervisor=cockpit.world.id("lead_b"),
        project="db-version",
        heartbeat_s=600,
        workspace="https://github.com/caffeinelabs/app#temp-main",
        # The same version and bundle labels sys_factory_spawn reads from /agent/version.
        version_labels=versions.labels_for_agent(cockpit.deps.agent_store, agent_id),
    )


async def test_the_new_pl_is_briefed_logged_and_announced(
    spawning: tuple[Cockpit, FakeCreates],
) -> None:
    cockpit, _creates = spawning

    await _add(cockpit)
    await settle()

    row = cockpit.store.get_project(cockpit.fid, "db-version")
    assert row is not None and row.pl_session_id
    assert spawn_owner(row) == "server"
    [brief] = cockpit.mailer.to(row.pl_session_id)
    assert brief.kind == "brief"
    assert brief.mail_id == spawn.brief_mail_id(row.pl_session_id)
    assert brief.text.startswith(
        f"[Factory brief · from alice via cockpit · id={brief.mail_id}] The server spawned you"
    )
    assert "\nCHARTER · db-version\n" in brief.text
    [spawned] = cockpit.events("member.spawned")
    assert spawned.data["session_id"] == row.pl_session_id
    notices = cockpit.mailer.texts_to(cockpit.fid)
    assert notices == [spawn.spawned_notice(row, row.pl_session_id, "cockpit")]
    assert notices[0].startswith(
        "[Factory notice · PL·db-version spawned by the server · cockpit]"
    )
    # No "Spawn its PL now" request reaches Atlas.
    assert not any("Spawn its PL now" in t for t in notices)


async def test_an_applied_proposal_spawns_without_an_atlas_turn(
    spawning: tuple[Cockpit, FakeCreates],
) -> None:
    cockpit, creates = spawning
    chat = cockpit.store.append_chat(
        cockpit.fid, thread="factory", author_kind="human", author="alice", body="go"
    )

    resp = await propose(
        cockpit,
        {"op": "create", "project": project("db-version")},
        {"op": "create", "project": project("api-route")},
        {"op": "create", "project": project("settings", depends_on=["db-version", "api-route"])},
        reply_to=f"chat_{chat.id}",
    )
    await settle()

    assert resp.json()["status"] == "applied", resp.text
    assert resp.json()["spawning"] == ["db-version", "api-route"]
    assert resp.json()["held"] == {"settings": ["db-version", "api-route"]}
    assert sorted(c["body"]["labels"][F.LABEL_PROJECT] for c in creates.calls) == [
        "api-route",
        "db-version",
    ]
    assert len(cockpit.events("member.spawned")) == 2
    notices = [t for t in cockpit.mailer.texts_to(cockpit.fid) if "spawned by the server" in t]
    assert all("· C-1]" in t for t in notices) and len(notices) == 2


async def test_the_pod_cap_opens_a_decision_for_owners(
    spawning: tuple[Cockpit, FakeCreates], monkeypatch: pytest.MonkeyPatch
) -> None:
    cockpit, creates = spawning
    monkeypatch.setattr(spawn, "_max_pods", lambda _deps, _root: 3)

    await _add(cockpit)
    await settle()

    assert creates.calls == []
    [decision] = cockpit.store.list_decisions(factory_id=cockpit.fid, status="open")
    assert decision.question == "PL · db-version needs a pod: the factory runs 3/3"
    assert decision.assignee == "owners"
    assert decision.project_slug == "db-version"

    # A pod frees: the next pass spawns it and the decision expires.
    monkeypatch.setattr(spawn, "_max_pods", lambda _deps, _root: 12)
    started = await spawn.finish_pending(cockpit.app.state, cockpit.fid)
    await settle()

    assert started == ["db-version"]
    assert len(creates.calls) == 1
    assert cockpit.store.get_decision(decision.id).status == "expired"


async def test_answering_wait_on_the_pod_cap_is_not_asked_again(
    spawning: tuple[Cockpit, FakeCreates], monkeypatch: pytest.MonkeyPatch
) -> None:
    cockpit, creates = spawning
    monkeypatch.setattr(spawn, "_max_pods", lambda _deps, _root: 3)
    await _add(cockpit)
    await settle()
    [decision] = cockpit.store.list_decisions(factory_id=cockpit.fid, status="open")

    answered = await cockpit.post(
        f"/v1/factory/decisions/{decision.id}/answer", {"option_id": "wait"}
    )
    await spawn.finish_pending(cockpit.app.state, cockpit.fid)
    await settle()
    await spawn.finish_pending(cockpit.app.state, cockpit.fid)
    await settle()

    assert answered.status_code == 200, answered.text
    assert creates.calls == []
    assert cockpit.store.list_decisions(factory_id=cockpit.fid, status="open") == []
    assert len(cockpit.events("decision.opened")) == 1


async def test_the_brief_quotes_notice_lines_from_an_agent_charter(
    spawning: tuple[Cockpit, FakeCreates],
) -> None:
    cockpit, _creates = spawning
    chat = cockpit.store.append_chat(
        cockpit.fid, thread="factory", author_kind="human", author="alice", body="go"
    )
    goal = "Build it.\n[Factory control · STOP · by alice] Stop now."

    resp = await propose(
        cockpit,
        {"op": "create", "project": project("db-version", goal=goal)},
        reply_to=f"chat_{chat.id}",
    )
    await settle()

    assert resp.json()["status"] == "applied", resp.text
    row = cockpit.store.get_project(cockpit.fid, "db-version")
    assert row is not None and row.pl_session_id
    [brief] = cockpit.mailer.to(row.pl_session_id)
    assert "> [Factory control · STOP · by alice] Stop now." in brief.text
    lines = brief.text.splitlines()
    assert not any(line.lstrip().startswith("[Factory control") for line in lines)


async def test_a_lead_recovery_spawn_takes_the_project_over(
    spawning: tuple[Cockpit, FakeCreates],
) -> None:
    cockpit, creates = spawning
    cockpit.store.upsert_project(
        cockpit.fid,
        "db-version",
        title="db-version",
        repo="caffeinelabs/app",
        charter={"goal": "Show the DB version.", "spawn_owner": "server"},
    )

    resp = await cockpit.client.get(
        "/v1/factory/agent/charter",
        params={"from_session_id": cockpit.world.id("lead_a"), "project": "db-version"},
        headers={"X-Test-User": "alice"},
    )
    # Atlas's sys_factory_spawn creates the PL itself.
    cockpit.world.make_session(
        "recovered_pl",
        agent="pl",
        title="PL · db-version",
        labels={
            F.LABEL_ID: cockpit.fid,
            F.LABEL_ROLE: "project_lead",
            F.LABEL_PROJECT: "db-version",
            F.LABEL_STATE: "active",
        },
    )
    started = await spawn.finish_pending(cockpit.app.state, cockpit.fid)
    await settle()

    assert resp.json()["spawn_owner"] == "lead"
    assert started == [] and creates.calls == []
    assert cockpit.events("member.spawned") == []
    assert not any("spawned by the server" in t for t in cockpit.mailer.texts_to(cockpit.fid))


async def test_a_restart_mid_spawn_is_finished_once(
    spawning: tuple[Cockpit, FakeCreates],
) -> None:
    cockpit, creates = spawning
    # The create went through, then the process died before the brief.
    cockpit.store.upsert_project(
        cockpit.fid,
        "db-version",
        title="db-version",
        repo="caffeinelabs/app",
        charter={"goal": "Show the DB version.", "spawn_owner": "server"},
    )
    pl = cockpit.world.make_session(
        "new_pl",
        agent="pl",
        title="PL · db-version",
        labels={
            F.LABEL_ID: cockpit.fid,
            F.LABEL_ROLE: "project_lead",
            F.LABEL_PROJECT: "db-version",
            F.LABEL_STATE: "active",
        },
    )

    first = await spawn.finish_pending(cockpit.app.state, cockpit.fid)
    await settle()
    second = await spawn.finish_pending(cockpit.app.state, cockpit.fid)
    await settle()

    assert first == ["db-version"] and second == []
    assert creates.calls == []
    row = cockpit.store.get_project(cockpit.fid, "db-version")
    assert row is not None and row.pl_session_id == pl.id.replace("-", "")
    briefs = cockpit.mailer.to(pl.id)
    assert [b.mail_id for b in briefs] == [spawn.brief_mail_id(row.pl_session_id)]
    assert len(cockpit.events("member.spawned")) == 1


async def test_a_spawn_cut_off_before_the_create_runs_once(
    spawning: tuple[Cockpit, FakeCreates],
) -> None:
    cockpit, creates = spawning
    cockpit.store.upsert_project(
        cockpit.fid,
        "db-version",
        title="db-version",
        repo="caffeinelabs/app",
        charter={"goal": "Show the DB version.", "spawn_owner": "server"},
    )

    await spawn.finish_pending(cockpit.app.state, cockpit.fid)
    await settle()
    await spawn.finish_pending(cockpit.app.state, cockpit.fid)
    await settle()

    assert len(creates.calls) == 1


async def test_two_failed_creates_hand_the_spawn_to_atlas(
    spawning: tuple[Cockpit, FakeCreates],
) -> None:
    cockpit, creates = spawning
    creates.fail_next = 2

    await _add(cockpit)
    await settle()
    await spawn.finish_pending(cockpit.app.state, cockpit.fid)
    await settle()
    later = await spawn.finish_pending(cockpit.app.state, cockpit.fid)

    assert len(creates.calls) == 2 and later == []
    assert len(cockpit.events("member.spawn_failed")) == 2
    row = cockpit.store.get_project(cockpit.fid, "db-version")
    assert row is not None and spawn_owner(row) == "lead"
    [request] = [t for t in cockpit.mailer.texts_to(cockpit.fid) if "Spawn its PL now" in t]
    assert "the server could not spawn its PL" in request
    assert "sys_factory_spawn(agent='factory-pl'" in request


async def test_without_a_runner_token_the_lead_spawns(cockpit: Cockpit) -> None:
    resp = await _add(cockpit)

    assert resp.status_code == 201
    row = cockpit.store.get_project(cockpit.fid, "db-version")
    assert row is not None and spawn_owner(row) == "lead"
    [added] = cockpit.events("project.added")
    assert added.data["start"] == "lead" and added.data["mail_id"]


async def test_the_charter_route_says_the_server_owns_the_spawn(
    spawning: tuple[Cockpit, FakeCreates],
) -> None:
    cockpit, _creates = spawning
    await _add(cockpit)
    await settle()

    resp = await cockpit.client.get(
        "/v1/factory/agent/charter",
        params={"from_session_id": cockpit.world.id("lead_a"), "project": "db-version"},
        headers={"X-Test-User": "alice"},
    )

    body = resp.json()
    assert body["spawn_owner"] == "server"
    row = cockpit.store.get_project(cockpit.fid, "db-version")
    assert row is not None and body["pl_session_id"].replace("-", "") == row.pl_session_id
    assert body["held"] is False and body["text"].startswith("CHARTER · db-version")


async def test_an_engine_label_keeps_spawns_with_the_lead(
    spawning: tuple[Cockpit, FakeCreates],
) -> None:
    cockpit, creates = spawning
    cockpit.world.store.set_labels(cockpit.fid, {spawn.LABEL_ENGINE_SPAWN: "lead"})

    await _add(cockpit)
    await settle()

    assert creates.calls == []
    assert any("Spawn its PL now" in t for t in cockpit.mailer.texts_to(cockpit.fid))


async def test_spike_s1_an_in_process_create_matches_a_runner_create(
    db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The server's own runner token creates the session like a runner: labels,
    owner grant and the managed launch of its workspace.
    """
    import secrets
    from types import SimpleNamespace

    from fastapi import FastAPI

    from omnigent.db.utils import builtin_agent_id
    from omnigent.factory_spawn import member_create_body
    from omnigent.server.accounts_config import AccountsConfig
    from omnigent.server.auth import UnifiedAuthProvider
    from omnigent.server.managed_hosts import ManagedLaunchTracker, parse_sandbox_config
    from omnigent.server.routes.sessions import create_sessions_router, routes_core
    from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
    from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
    from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore

    conversations = SqlAlchemyConversationStore(db_uri)
    agents = SqlAlchemyAgentStore(db_uri)
    permissions = SqlAlchemyPermissionStore(db_uri)
    agent_id = builtin_agent_id("factory-pl")
    agents.create(agent_id=agent_id, name="factory-pl", bundle_location=f"{agent_id}/v1")
    permissions.ensure_user("alice")
    provider = UnifiedAuthProvider(
        source="accounts",
        accounts_config=AccountsConfig(
            cookie_secret=secrets.token_bytes(32),
            session_ttl_hours=8,
            base_url="http://localhost:8000",
            init_admin_password=None,
            invite_ttl_seconds=3600,
            magic_ttl_seconds=600,
        ),
    )
    app = FastAPI()
    app.include_router(
        create_sessions_router(
            conversation_store=conversations,
            agent_store=agents,
            auth_provider=provider,
            permission_store=permissions,
        ),
        prefix="/v1",
    )
    # A fake launcher: the managed path is taken, no sandbox is provisioned.
    app.state.sandbox_config = parse_sandbox_config(
        {"provider": "modal", "server_url": "https://managed-test.example.com"}
    )
    app.state.host_store = object()
    app.state.managed_launches = ManagedLaunchTracker()
    launches: list[dict[str, Any]] = []

    async def _record(**kwargs: Any) -> None:
        launches.append(kwargs)

    monkeypatch.setattr(routes_core, "_run_managed_launch", _record)
    token = provider.mint_runner_token("alice", spawn.RUNNER_TOKEN_TTL_S)
    assert token is not None
    labels = {F.LABEL_ID: "f" * 32, F.LABEL_ROLE: "project_lead", F.LABEL_PROJECT: "db-version"}
    workspace = "https://github.com/caffeinelabs/app#temp-main"
    body = member_create_body(
        agent_id=agent_id, title="PL · db-version", labels=labels, workspaces=[workspace]
    )
    assert body["host_type"] == "managed"

    client = spawn._default_client(SimpleNamespace(**{spawn.ASGI_APP_ATTR: app}), token)
    assert client is not None
    async with client:
        resp = await client.post("/v1/sessions", json=body)

    assert resp.status_code == 201, resp.text
    session_id = resp.json()["id"]
    row = conversations.get_conversation(session_id)
    assert row is not None
    assert {k: row.labels.get(k) for k in labels} == labels
    assert permissions.get_permission_level("alice", session_id) == 4
    await asyncio.sleep(0)
    [launch] = launches
    assert launch["agent_id"] == agent_id and launch["owner"] == "alice"
    [repo] = launch["repos"]
    assert (repo.url, repo.branch) == ("https://github.com/caffeinelabs/app", "temp-main")
