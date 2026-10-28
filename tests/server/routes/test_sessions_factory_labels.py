"""Runners never write or mint ``omnigent.factory.*`` labels outside a spawn.

Agents set their factory labels with ``sys_factory_set`` (``PATCH
/v1/factory/sessions/{id}``), which audits every change and refuses
server-only labels. A runner authenticates as the session owner, so:

- the generic ``PATCH /v1/sessions/{id}`` refuses factory labels from a runner
  (its tunnel header or an ``org: runner`` bearer) before touching the store;
- ``POST /v1/sessions`` from a runner accepts factory labels only as
  ``sys_factory_spawn`` sends them: the spawn labels, a spawn role, and a
  parent that is a lead of the named factory (``server/factory/create_guard.py``);
- a runner's fork of a factory member is an ordinary session.

A person keeps writing and creating them unchanged, and a runner keeps writing
other labels.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from omnigent import factory as F
from omnigent.errors import OmnigentError
from omnigent.factory_spawn import member_labels
from omnigent.runner.identity import RUNNER_TUNNEL_TOKEN_HEADER, token_bound_runner_id
from omnigent.server.accounts_config import AccountsConfig
from omnigent.server.auth import LEVEL_OWNER, UnifiedAuthProvider
from omnigent.server.factory.routes import (
    SERVER_ONLY_LABEL_FIELDS,
    FactoryRouteError,
    factory_error_handler,
)
from omnigent.server.oidc import mint_session_token
from omnigent.server.routes.sessions import create_sessions_router
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore

ALICE = "alice"
BOB = "bob"
_SECRET = secrets.token_bytes(32)
_AGENT_ID = "1f0c8a7e4b5d4c3b9a8e7f6d5c4b3a29"
_LEAD_TUNNEL_TOKEN = "rt_lead.secret"


def _provider() -> UnifiedAuthProvider:
    config = AccountsConfig(
        cookie_secret=_SECRET,
        session_ttl_hours=8,
        base_url="http://localhost:8000",
        init_admin_password=None,
        invite_ttl_seconds=3600,
        magic_ttl_seconds=600,
    )
    return UnifiedAuthProvider(source="accounts", accounts_config=config)


@dataclass
class World:
    """A sessions app and two factories: Alice's (lead + PL) and Alice's second one."""

    app: FastAPI
    conversations: SqlAlchemyConversationStore
    lead: str
    pl: str
    other_lead: str
    bobs_lead: str

    def client(self) -> TestClient:
        return TestClient(self.app)


def _member(
    conversations: SqlAlchemyConversationStore,
    permissions: SqlAlchemyPermissionStore,
    *,
    title: str,
    role: str,
    factory_id: str | None = None,
    owner: str = ALICE,
    runner_id: str | None = None,
) -> str:
    conv = conversations.create_conversation(title=title, agent_id=_AGENT_ID, runner_id=runner_id)
    conversations.set_labels(conv.id, {F.LABEL_ID: factory_id or conv.id, F.LABEL_ROLE: role})
    permissions.ensure_user(owner)
    permissions.grant(owner, conv.id, LEVEL_OWNER)
    return conv.id


@pytest.fixture
def world(db_uri: str, tmp_path: Path) -> World:
    conversations = SqlAlchemyConversationStore(db_uri)
    agents = SqlAlchemyAgentStore(db_uri)
    permissions = SqlAlchemyPermissionStore(db_uri)
    agents.create(agent_id=_AGENT_ID, name="pl", bundle_location=f"{_AGENT_ID}/bundle")
    lead = _member(
        conversations,
        permissions,
        title="Atlas",
        role=F.ROLE_LEAD,
        runner_id=token_bound_runner_id(_LEAD_TUNNEL_TOKEN),
    )
    pl = _member(conversations, permissions, title="PL", role=F.ROLE_PROJECT_LEAD, factory_id=lead)
    other_lead = _member(conversations, permissions, title="Atlas 2", role=F.ROLE_LEAD)
    bobs_lead = _member(
        conversations, permissions, title="Bob's Atlas", role=F.ROLE_LEAD, owner=BOB
    )

    app = FastAPI()

    @app.exception_handler(OmnigentError)
    async def _handle(request: Request, exc: OmnigentError) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    app.add_exception_handler(FactoryRouteError, factory_error_handler)
    app.include_router(
        create_sessions_router(
            conversation_store=conversations,
            agent_store=agents,
            artifact_store=LocalArtifactStore(str(tmp_path / "artifacts")),
            auth_provider=_provider(),
            permission_store=permissions,
        ),
        prefix="/v1",
    )
    return World(app, conversations, lead, pl, other_lead, bobs_lead)


def _person() -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_session_token(ALICE, _SECRET, 600, 'accounts')}"}


def _runner() -> dict[str, str]:
    token = _provider().mint_runner_token(ALICE, 600)
    assert token is not None
    return {"Authorization": f"Bearer {token}"}


def _person_with_tunnel_header(token: str = "rt_abc.def") -> dict[str, str]:
    return {**_person(), RUNNER_TUNNEL_TOKEN_HEADER: token}


def _spawn_labels(world: World, **overrides: str) -> dict[str, str]:
    """Exactly the labels ``_spawn_member`` sends for a PL (runner/factory_dispatch.py)."""
    labels = {
        F.LABEL_ID: world.lead,
        F.LABEL_ROLE: F.ROLE_PROJECT_LEAD,
        F.LABEL_PARENT: world.lead,
        F.LABEL_SUPERVISOR: world.lead,
        F.LABEL_STATE: F.STATE_ACTIVE,
        F.LABEL_HEARTBEAT_S: str(F.DEFAULT_HEARTBEAT_S[F.ROLE_PROJECT_LEAD]),
        F.LABEL_PROJECT: "app",
        F.LABEL_VERSION: "ver_1",
        F.LABEL_BUNDLE: f"{_AGENT_ID}/0123456789ab",
    }
    return {**labels, **overrides}


def _create(world: World, labels: dict[str, str], headers: dict[str, str]) -> httpx.Response:
    return world.client().post(
        "/v1/sessions",
        json={"agent_id": _AGENT_ID, "title": "PL · app", "labels": labels},
        headers=headers,
    )


def _code(resp: httpx.Response) -> str:
    return str(resp.json()["error"]["code"])


def _nothing_created(world: World) -> bool:
    """No session carries the spawn's project label (every refused body names ``app``)."""
    return not world.conversations.list_conversation_ids_with_label(F.LABEL_PROJECT, "app")


_RUNNER_HEADERS = pytest.mark.parametrize(
    "headers",
    [_runner, _person_with_tunnel_header],
    ids=["runner-bearer", "runner-tunnel-header"],
)
_SERVER_ONLY = f"{F.LABEL_PREFIX}engine_mode"


# ── PATCH /v1/sessions/{id} ──────────────────────────────────────


@_RUNNER_HEADERS
@pytest.mark.parametrize("label", [_SERVER_ONLY, F.LABEL_ID, F.LABEL_STATE])
def test_a_runner_cannot_write_factory_labels_here(
    world: World,
    headers: Callable[[], dict[str, str]],
    label: str,
) -> None:
    resp = world.client().patch(
        f"/v1/sessions/{world.lead}",
        json={"title": "smuggled", "labels": {label: "x"}},
        headers=headers(),
    )

    assert resp.status_code == 403, resp.text
    assert "sys_factory_set" in resp.json()["error"]["message"]
    conv = world.conversations.get_conversation(world.lead)
    assert conv is not None
    assert conv.labels.get(label) != "x"
    assert conv.title == "Atlas"


def test_a_person_still_writes_factory_labels_and_a_runner_other_labels(world: World) -> None:
    client = world.client()

    person = client.patch(
        f"/v1/sessions/{world.lead}", json={"labels": {_SERVER_ONLY: "auto"}}, headers=_person()
    )
    runner = client.patch(
        f"/v1/sessions/{world.lead}", json={"labels": {"team": "ml"}}, headers=_runner()
    )

    assert person.status_code == 200, person.text
    assert runner.status_code == 200, runner.text
    conv = world.conversations.get_conversation(world.lead)
    assert conv is not None
    assert conv.labels[_SERVER_ONLY] == "auto"
    assert conv.labels["team"] == "ml"


# ── POST /v1/sessions: what a spawn sends is accepted ────────────


def test_a_runner_creates_a_member_with_the_real_spawn_body(world: World) -> None:
    labels = _spawn_labels(world)
    # The very function both spawns build their labels with.
    assert labels == member_labels(
        factory_id=world.lead,
        role=F.ROLE_PROJECT_LEAD,
        parent=world.lead,
        supervisor=world.lead,
        project="app",
        extra={F.LABEL_VERSION: "ver_1", F.LABEL_BUNDLE: f"{_AGENT_ID}/0123456789ab"},
    )

    resp = _create(world, labels, _runner())

    assert resp.status_code == 201, resp.text
    conv = world.conversations.get_conversation(resp.json()["id"])
    assert conv is not None
    assert {key: conv.labels.get(key) for key in labels} == labels


def test_a_lead_spawns_a_second_lead_over_its_own_tunnel(world: World) -> None:
    labels = member_labels(
        factory_id=world.lead, role=F.ROLE_LEAD, parent=world.lead, supervisor=world.lead
    )

    resp = _create(world, labels, _person_with_tunnel_header(_LEAD_TUNNEL_TOKEN))

    assert resp.status_code == 201, resp.text
    conv = world.conversations.get_conversation(resp.json()["id"])
    assert conv is not None
    assert conv.labels[F.LABEL_ROLE] == F.ROLE_LEAD
    assert conv.labels[F.LABEL_ID] == world.lead


def test_a_runner_still_creates_sessions_without_factory_labels(world: World) -> None:
    resp = _create(world, {"team": "ml"}, _runner())

    assert resp.status_code == 201, resp.text


# ── POST /v1/sessions: what a spawn never sends is refused ───────


@_RUNNER_HEADERS
@pytest.mark.parametrize("field", sorted(SERVER_ONLY_LABEL_FIELDS))
def test_a_runner_cannot_create_with_a_server_only_label(
    world: World, headers: Callable[[], dict[str, str]], field: str
) -> None:
    resp = _create(world, _spawn_labels(world, **{f"{F.LABEL_PREFIX}{field}": "999"}), headers())

    assert resp.status_code == 422, resp.text
    assert _code(resp) == "server_only_label"
    assert field in resp.json()["error"]["message"]
    assert _nothing_created(world)


@pytest.mark.parametrize(
    "label",
    [
        F.LABEL_NAME,
        F.LABEL_CADENCE,
        F.LABEL_SUMMARY,
        F.LABEL_LAST_BEAT,
        f"{F.LABEL_BASE_HEAD_PREFIX}x",
    ],
)
def test_a_runner_cannot_create_with_other_factory_labels(world: World, label: str) -> None:
    resp = _create(world, _spawn_labels(world, **{label: "x"}), _runner())

    assert resp.status_code == 422, resp.text
    assert _code(resp) == "factory_label_not_allowed"
    assert _nothing_created(world)


@pytest.mark.parametrize("role", ["root", F.ROLE_IC, F.ROLE_PLANNER, F.ROLE_ENV_KEEPER, ""])
def test_a_runner_cannot_mint_a_role_no_spawn_creates(world: World, role: str) -> None:
    resp = _create(world, _spawn_labels(world, **{F.LABEL_ROLE: role}), _runner())

    assert resp.status_code == 403, resp.text
    assert _code(resp) == "spawn_role_not_allowed"
    assert _nothing_created(world)


def test_a_runner_cannot_create_a_member_without_a_role(world: World) -> None:
    labels = _spawn_labels(world)
    del labels[F.LABEL_ROLE]

    resp = _create(world, labels, _runner())

    assert resp.status_code == 403, resp.text
    assert _code(resp) == "spawn_role_not_allowed"


@pytest.mark.parametrize(
    ("case", "overrides"),
    [
        # A lead of another factory: the parent belongs to the factory named, but not the caller's.
        ("lead-of-another-factory", {"id": "other_lead", "role": F.ROLE_LEAD}),
        ("parent-in-another-factory", {"parent": "other_lead"}),
        ("parent-is-not-a-lead", {"parent": "pl"}),
        ("parent-missing", {"parent": ""}),
        ("parent-unknown", {"parent": "0" * 32}),
        ("factory-missing", {"id": ""}),
        ("supervisor-in-another-factory", {"supervisor": "other_lead"}),
    ],
)
def test_a_runner_creates_only_under_a_lead_of_the_named_factory(
    world: World, case: str, overrides: dict[str, str]
) -> None:
    del case
    ids = {"other_lead": world.other_lead, "pl": world.pl}
    labels = _spawn_labels(
        world,
        **{f"{F.LABEL_PREFIX}{key}": ids.get(value, value) for key, value in overrides.items()},
    )
    labels = {key: value for key, value in labels.items() if value}

    resp = _create(world, labels, _runner())

    assert resp.status_code == 403, resp.text
    assert _code(resp) == "session_out_of_factory"
    assert _nothing_created(world)


def test_a_runner_cannot_spawn_under_a_lead_it_does_not_own(world: World) -> None:
    labels = _spawn_labels(
        world,
        **{
            F.LABEL_ID: world.bobs_lead,
            F.LABEL_PARENT: world.bobs_lead,
            F.LABEL_SUPERVISOR: world.bobs_lead,
        },
    )

    resp = _create(world, labels, _runner())

    # Alice has no access to Bob's lead, so it does not even exist for her.
    assert resp.status_code == 404, resp.text
    assert _nothing_created(world)


def test_a_tunnel_runner_spawns_only_under_its_own_lead(world: World) -> None:
    resp = _create(world, _spawn_labels(world), _person_with_tunnel_header("rt_other.secret"))

    assert resp.status_code == 403, resp.text
    assert _code(resp) == "sender_not_bound"
    assert _nothing_created(world)


@pytest.mark.parametrize(
    ("label", "value", "code"),
    [
        (F.LABEL_STATE, "zombie", "invalid_state"),
        (F.LABEL_HEARTBEAT_S, "5", "invalid_heartbeat_s"),
        (F.LABEL_HEARTBEAT_S, "-1", "invalid_heartbeat_s"),
    ],
)
def test_a_runner_cannot_create_a_member_with_values_no_spawn_sends(
    world: World, label: str, value: str, code: str
) -> None:
    resp = _create(world, _spawn_labels(world, **{label: value}), _runner())

    assert resp.status_code == 400, resp.text
    assert _code(resp) == code


def _bundled_create(
    world: World, labels: dict[str, str], headers: dict[str, str]
) -> httpx.Response:
    from tests.server.helpers import build_agent_bundle

    return world.client().post(
        "/v1/sessions",
        data={"metadata": json.dumps({"title": "PL · app", "labels": labels})},
        files={"bundle": ("agent.tar.gz", build_agent_bundle(name="pl"), "application/gzip")},
        headers=headers,
    )


def test_a_runner_cannot_create_a_member_from_its_own_bundle(world: World) -> None:
    server_only = _bundled_create(world, _spawn_labels(world, **{_SERVER_ONLY: "x"}), _runner())
    bundled = _bundled_create(world, _spawn_labels(world), _runner())

    assert server_only.status_code == 422, server_only.text
    assert _code(server_only) == "server_only_label"
    assert bundled.status_code == 403, bundled.text
    assert _code(bundled) == "factory_custom_bundle_denied"
    assert _nothing_created(world)


def test_a_runner_cannot_bind_a_member_to_the_agent_its_own_upload_made(world: World) -> None:
    # Step 1: an ordinary upload (no factory labels) makes a session-scoped agent.
    upload = _bundled_create(world, {"team": "ml"}, _runner())
    assert upload.status_code == 201, upload.text
    own_agent = upload.json()["agent_id"]
    assert own_agent != _AGENT_ID

    # Step 2: the exact spawn body, bound to that agent by id.
    resp = world.client().post(
        "/v1/sessions",
        json={"agent_id": own_agent, "title": "PL · app", "labels": _spawn_labels(world)},
        headers=_runner(),
    )

    assert resp.status_code == 403, resp.text
    assert _code(resp) == "factory_custom_bundle_denied"
    assert _nothing_created(world)


def test_a_runner_names_the_agent_of_a_member_it_creates(world: World) -> None:
    # A project create may leave agent_id to the project's default; a spawn never does.
    resp = world.client().post(
        "/v1/sessions",
        json={"project_id": "proj_x", "title": "PL · app", "labels": _spawn_labels(world)},
        headers=_runner(),
    )

    assert resp.status_code == 422, resp.text
    assert _code(resp) == "agent_required"
    assert _nothing_created(world)


@pytest.mark.parametrize(
    "key",
    [
        "Omnigent.factory.engine_mode",
        " omnigent.factory.engine_mode",
        "OMNIGENT.FACTORY.ROLE",
    ],
)
def test_a_runner_cannot_smuggle_a_factory_label_in_another_case(world: World, key: str) -> None:
    resp = _create(world, _spawn_labels(world, **{key: "autonomous"}), _runner())

    assert resp.status_code == 422, resp.text
    assert _code(resp) == "factory_label_not_allowed"
    assert _nothing_created(world)


def test_an_uploaded_spec_cannot_seed_factory_labels(world: World) -> None:
    from tests.server.helpers import build_agent_bundle

    bundle = build_agent_bundle(
        name="pl",
        guardrails={
            "labels": {
                F.LABEL_ID: {"initial": world.lead},
                F.LABEL_ROLE: {"initial": "root"},
                _SERVER_ONLY: {"initial": "autonomous"},
            }
        },
    )

    resp = world.client().post(
        "/v1/sessions",
        data={"metadata": json.dumps({"title": "probe", "labels": {"team": "ml"}})},
        files={"bundle": ("agent.tar.gz", bundle, "application/gzip")},
        headers=_runner(),
    )

    assert resp.status_code == 400, resp.text
    assert "omnigent.factory.* labels are set only by the server" in resp.text
    assert not world.conversations.list_conversation_ids_with_label("team", "ml")


# ── People are unaffected ────────────────────────────────────────


def test_a_person_still_creates_sessions_with_any_factory_labels(world: World) -> None:
    labels = _spawn_labels(
        world,
        **{
            F.LABEL_ROLE: "root",
            F.LABEL_PARENT: world.other_lead,
            F.LABEL_DEF_VERSION: "999",
            _SERVER_ONLY: "autonomous",
        },
    )

    resp = _create(world, labels, _person())

    assert resp.status_code == 201, resp.text
    conv = world.conversations.get_conversation(resp.json()["id"])
    assert conv is not None
    assert {key: conv.labels.get(key) for key in labels} == labels


# ── Fork ─────────────────────────────────────────────────────────


def test_a_runners_fork_of_a_member_is_not_a_member(world: World) -> None:
    world.conversations.set_labels(world.lead, {F.LABEL_DEF_VERSION: "3", "team": "ml"})
    client = world.client()

    by_runner = client.post(f"/v1/sessions/{world.lead}/fork", json={}, headers=_runner())
    by_person = client.post(f"/v1/sessions/{world.lead}/fork", json={}, headers=_person())

    assert by_runner.status_code == 201, by_runner.text
    assert by_person.status_code == 201, by_person.text
    runner_fork = world.conversations.get_conversation(by_runner.json()["id"])
    person_fork = world.conversations.get_conversation(by_person.json()["id"])
    assert runner_fork is not None and person_fork is not None
    assert not [key for key in runner_fork.labels if key.startswith(F.LABEL_PREFIX)]
    assert runner_fork.labels["team"] == "ml"
    assert person_fork.labels[F.LABEL_ID] == world.lead
    assert person_fork.labels[F.LABEL_DEF_VERSION] == "3"
