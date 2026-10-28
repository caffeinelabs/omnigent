"""A factory member's child sessions never forge a top-level role or run an upload.

A PL's runner can create a child of the PL with ``POST /v1/sessions`` (what
``sys_session_create`` sends) and name any agent it can read. Such a child
inherits the PL's factory through its root, so:

- it is an ``ic`` even when it runs the factory-lead or factory-pl spec: a
  sub-agent never resolves to ``lead`` or ``project_lead``, so it cannot pause
  a lead or record a Sentinel verdict where the PL itself is refused;
- it never runs a bundle someone uploaded (Tier 0): binding a session-scoped
  agent made by an earlier, unparented upload is refused, unless it is the
  agent its parent (or root) already runs, which a ``sys_session_send``
  sub-agent binds.

Runs the real sessions router and the real factory routes on one app, with
accounts auth, a runner bearer and runner tunnel headers bound to the lead's
and the PL's runners.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from omnigent import factory as F
from omnigent.db.utils import builtin_agent_id
from omnigent.errors import OmnigentError
from omnigent.runner.identity import RUNNER_TUNNEL_TOKEN_HEADER, token_bound_runner_id
from omnigent.runtime.agent_cache import AgentCache
from omnigent.server.accounts_config import AccountsConfig
from omnigent.server.auth import LEVEL_OWNER, UnifiedAuthProvider
from omnigent.server.factory import register_factory_routes
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.membership import membership_of
from omnigent.server.oidc import mint_session_token
from omnigent.server.routes.sessions import create_sessions_router
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
from tests.server.helpers import build_agent_bundle

ALICE = "alice"
_SECRET = secrets.token_bytes(32)
_LEAD_TOKEN = "rt_lead.secret"
_PL_TOKEN = "rt_pl.secret"
_PR_URL = "https://github.com/caffeinelabs/app/pull/9630"
_HEAD_SHA = "7767f0d1" + "b" * 32


def _provider() -> UnifiedAuthProvider:
    return UnifiedAuthProvider(
        source="accounts",
        accounts_config=AccountsConfig(
            cookie_secret=_SECRET,
            session_ttl_hours=8,
            base_url="http://localhost:8000",
            init_admin_password=None,
            invite_ttl_seconds=3600,
            magic_ttl_seconds=600,
        ),
    )


def _executor(role: str | None) -> dict[str, Any]:
    config: dict[str, Any] = {"harness": "claude-native"}
    if role is not None:
        config["factory_role"] = role
    return {"type": "omnigent", "config": config}


@dataclass
class World:
    """Atlas (root lead), Lead B (peer lead) and a PL, each owned by Alice."""

    app: FastAPI
    conversations: SqlAlchemyConversationStore
    agents: SqlAlchemyAgentStore
    ids: dict[str, str] = field(default_factory=dict)
    agent_ids: dict[str, str] = field(default_factory=dict)

    def client(self) -> TestClient:
        return TestClient(self.app)


@pytest.fixture
def world(db_uri: str, tmp_path: Path) -> World:
    conversations = SqlAlchemyConversationStore(db_uri)
    agents = SqlAlchemyAgentStore(db_uri)
    permissions = SqlAlchemyPermissionStore(db_uri)
    artifacts = LocalArtifactStore(str(tmp_path / "artifacts"))
    cache = AgentCache(artifacts, tmp_path / "agent-cache")

    def register(name: str, bundle: bytes) -> str:
        agent_id = builtin_agent_id(name)
        location = f"{agent_id}/v1"
        artifacts.put(location, bundle)
        agents.create(agent_id, name, location)
        return agent_id

    agent_ids = {
        "lead": register(
            "factory-lead",
            build_agent_bundle("factory-lead", executor=_executor("lead"), include_llm=False),
        ),
        "pl": register(
            "factory-pl",
            build_agent_bundle(
                "factory-pl",
                executor=_executor("project_lead"),
                include_llm=False,
                sub_agents=[{"name": "ic", "description": "impl", "executor": _executor("ic")}],
            ),
        ),
        "plain": register(
            "plain", build_agent_bundle("plain", executor=_executor(None), include_llm=False)
        ),
    }
    w = World(FastAPI(), conversations, agents, agent_ids=agent_ids)
    permissions.ensure_user(ALICE)

    def member(key: str, agent: str, labels: dict[str, str], runner_id: str | None = None) -> str:
        conv = conversations.create_conversation(
            title=key, agent_id=agent_ids[agent], runner_id=runner_id
        )
        permissions.grant(ALICE, conv.id, LEVEL_OWNER)
        w.ids[key] = conv.id
        if labels:
            conversations.set_labels(conv.id, labels)
        return conv.id

    atlas = member("atlas", "lead", {}, runner_id=token_bound_runner_id(_LEAD_TOKEN))
    conversations.set_labels(
        atlas, {F.LABEL_ID: atlas, F.LABEL_ROLE: F.ROLE_LEAD, F.LABEL_STATE: F.STATE_ACTIVE}
    )
    top_level = {F.LABEL_ID: atlas, F.LABEL_PARENT: atlas, F.LABEL_SUPERVISOR: atlas}
    member(
        "lead_b", "lead", {**top_level, F.LABEL_ROLE: F.ROLE_LEAD, F.LABEL_STATE: F.STATE_ACTIVE}
    )
    member(
        "pl",
        "pl",
        {
            **top_level,
            F.LABEL_ROLE: F.ROLE_PROJECT_LEAD,
            F.LABEL_PROJECT: "api",
            F.LABEL_STATE: F.STATE_ACTIVE,
            F.LABEL_HEARTBEAT_S: "600",
        },
        runner_id=token_bound_runner_id(_PL_TOKEN),
    )
    member("outsider", "plain", {})

    @w.app.exception_handler(OmnigentError)
    async def _handle(request: Request, exc: OmnigentError) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    provider = _provider()
    w.app.include_router(
        create_sessions_router(
            conversation_store=conversations,
            agent_store=agents,
            artifact_store=artifacts,
            auth_provider=provider,
            permission_store=permissions,
            agent_cache=cache,
        ),
        prefix="/v1",
    )
    register_factory_routes(
        w.app,
        conversation_store=conversations,
        agent_store=agents,
        agent_cache=cache,
        permission_store=permissions,
        auth_provider=provider,
        artifact_store=artifacts,
    )
    return w


def _person() -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_session_token(ALICE, _SECRET, 600, 'accounts')}"}


def _runner() -> dict[str, str]:
    token = _provider().mint_runner_token(ALICE, 600)
    assert token is not None
    return {"Authorization": f"Bearer {token}"}


def _tunnel(token: str) -> dict[str, str]:
    """A runner over its tunnel: the runner bearer plus its tunnel header."""
    return {**_runner(), RUNNER_TUNNEL_TOKEN_HEADER: token}


def _code(resp: httpx.Response) -> str:
    return str(resp.json()["error"]["code"])


def _child(world: World, agent_id: str, headers: dict[str, str], **extra: str) -> httpx.Response:
    """What ``sys_session_create(agent_id=…)`` sends from the PL."""
    return world.client().post(
        "/v1/sessions",
        json={"agent_id": agent_id, "title": "helper", "parent_session_id": world.ids["pl"]}
        | extra,
        headers=headers,
    )


def _upload(world: World, headers: dict[str, str]) -> tuple[str, str]:
    """An unparented upload without factory labels: its session and session-scoped agent."""
    resp = world.client().post(
        "/v1/sessions",
        data={"metadata": json.dumps({"title": "upload", "labels": {"team": "ml"}})},
        files={
            "bundle": (
                "agent.tar.gz",
                build_agent_bundle(name="mine", include_llm=False),
                "application/gzip",
            )
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["session_id"]), str(resp.json()["agent_id"])


def _children_of_pl(world: World) -> list[str]:
    page = world.conversations.list_conversations(
        limit=50, kind="sub_agent", parent_conversation_id=world.ids["pl"]
    )
    return [c.id for c in page.data]


# ── a child running a top-level spec is an ic ─────────────────────


@pytest.mark.parametrize("agent", ["lead", "pl"])
def test_a_child_running_a_top_level_spec_is_an_ic(world: World, agent: str) -> None:
    resp = _child(world, world.agent_ids[agent], _tunnel(_PL_TOKEN))
    assert resp.status_code == 201, resp.text
    child_id = resp.json()["id"]

    membership = world.client().get(
        f"/v1/factory/membership/{child_id}",
        params={"from_session_id": child_id},
        headers=_tunnel(_PL_TOKEN),
    )

    assert membership.status_code == 200, membership.text
    body = membership.json()
    assert body["factory_id"] == world.ids["atlas"]
    assert body["is_top_level"] is False
    assert body["role"] == F.ROLE_IC


def test_a_lead_spec_child_cannot_pause_a_lead(world: World) -> None:
    child_id = _child(world, world.agent_ids["lead"], _tunnel(_PL_TOKEN)).json()["id"]

    def pause(target: str, sender: str) -> httpx.Response:
        return world.client().patch(
            f"/v1/factory/sessions/{world.ids[target]}",
            json={"from_session_id": sender, "state": F.STATE_PAUSED, "heartbeat_s": 0},
            headers=_tunnel(_PL_TOKEN),
        )

    as_pl = pause("lead_b", world.ids["pl"])
    peer = pause("lead_b", child_id)
    root = pause("atlas", child_id)

    assert as_pl.status_code == 403, as_pl.text
    for resp in (peer, root):
        assert resp.status_code == 403, resp.text
        assert _code(resp) == _code(as_pl)
    for key in ("lead_b", "atlas"):
        row = world.conversations.get_conversation(world.ids[key])
        assert row is not None and row.labels[F.LABEL_STATE] == F.STATE_ACTIVE


def test_a_lead_spec_child_cannot_record_a_sentinel_verdict(world: World) -> None:
    store = factory_deps(world.app.state).factory_store
    store.upsert_project(
        world.ids["atlas"],
        "api",
        title="api",
        repo="caffeinelabs/app",
        base_branch="main",
        pl_session_id=world.ids["pl"],
    )
    child_id = _child(world, world.agent_ids["lead"], _tunnel(_PL_TOKEN)).json()["id"]

    def verdict(sender: str) -> httpx.Response:
        return world.client().post(
            "/v1/factory/agent/prs",
            json={
                "from_session_id": sender,
                "url": _PR_URL,
                "sentinel_verdict": "verified",
                "head_sha": _HEAD_SHA,
            },
            headers=_tunnel(_PL_TOKEN),
        )

    as_pl = verdict(world.ids["pl"])
    as_child = verdict(child_id)

    assert as_pl.status_code == 403, as_pl.text
    assert as_child.status_code == 403, as_child.text
    assert _code(as_child) == _code(as_pl) == "sentinel_lead_only"
    assert not [row for row in store.list_prs(world.ids["atlas"]) if row.sentinel]


# ── a child never runs an uploaded bundle ─────────────────────────


@pytest.mark.parametrize(
    "headers",
    [lambda: _tunnel(_PL_TOKEN), _runner, _person],
    ids=["pl-tunnel", "runner-bearer", "person"],
)
def test_a_child_of_a_member_cannot_bind_an_uploaded_agent(world: World, headers: Any) -> None:
    # Step 1: an unparented upload outside the factory is allowed.
    _, uploaded = _upload(world, _runner())
    # Step 2: a child of the PL bound to that agent by id is not.
    resp = _child(world, uploaded, headers())

    assert resp.status_code == 403, resp.text
    assert _code(resp) == "factory_custom_bundle_denied"
    assert _children_of_pl(world) == []


def test_a_child_of_a_member_still_runs_a_built_in_or_its_parents_agent(world: World) -> None:
    sub_agent = _child(world, world.agent_ids["pl"], _tunnel(_PL_TOKEN), sub_agent_name="ic")
    built_in = _child(world, world.agent_ids["plain"], _tunnel(_PL_TOKEN), title="plain")

    assert sub_agent.status_code == 201, sub_agent.text
    assert built_in.status_code == 201, built_in.text
    deps = factory_deps(world.app.state)
    for resp in (sub_agent, built_in):
        child = world.conversations.get_conversation(resp.json()["id"])
        assert child is not None
        assert membership_of(child, deps).factory_id == world.ids["atlas"]


def test_a_member_running_its_own_upload_still_dispatches_sub_agents(world: World) -> None:
    # A person may run a member on an upload of their own; its sys_session_send
    # children bind that same agent, and their children the root's.
    member_id, uploaded = _upload(world, _person())
    world.conversations.set_labels(
        member_id,
        {
            F.LABEL_ID: world.ids["atlas"],
            F.LABEL_ROLE: F.ROLE_PROJECT_LEAD,
            F.LABEL_PARENT: world.ids["atlas"],
        },
    )

    def child(parent: str) -> httpx.Response:
        return world.client().post(
            "/v1/sessions",
            json={"agent_id": uploaded, "title": "helper", "parent_session_id": parent},
            headers=_runner(),
        )

    first = child(member_id)
    assert first.status_code == 201, first.text
    nested = child(first.json()["id"])

    assert nested.status_code == 201, nested.text


def test_a_child_outside_a_factory_still_binds_an_uploaded_agent(world: World) -> None:
    _, uploaded = _upload(world, _runner())

    resp = world.client().post(
        "/v1/sessions",
        json={"agent_id": uploaded, "title": "helper", "parent_session_id": world.ids["outsider"]},
        headers=_runner(),
    )

    assert resp.status_code == 201, resp.text
