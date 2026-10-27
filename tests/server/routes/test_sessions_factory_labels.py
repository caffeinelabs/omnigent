"""Runners never write ``omnigent.factory.*`` labels through the generic session PATCH.

Agents set their factory labels with ``sys_factory_set`` (``PATCH
/v1/factory/sessions/{id}``), which audits every change and refuses
server-only labels. A runner authenticates as the session owner, so the
generic ``PATCH /v1/sessions/{id}`` refuses factory labels from a runner
(its tunnel header or an ``org: runner`` bearer) before touching the store,
while a person keeps writing them and a runner keeps writing other labels.
"""

from __future__ import annotations

import secrets

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from omnigent import factory as F
from omnigent.errors import OmnigentError
from omnigent.runner.identity import RUNNER_TUNNEL_TOKEN_HEADER
from omnigent.server.accounts_config import AccountsConfig
from omnigent.server.auth import LEVEL_OWNER, UnifiedAuthProvider
from omnigent.server.oidc import mint_session_token
from omnigent.server.routes.sessions import create_sessions_router
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore

ALICE = "alice"
_SECRET = secrets.token_bytes(32)
_AGENT_ID = "1f0c8a7e4b5d4c3b9a8e7f6d5c4b3a29"


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


@pytest.fixture
def app_and_session(db_uri: str) -> tuple[FastAPI, SqlAlchemyConversationStore, str]:
    """An accounts-auth sessions app and a session Alice owns."""
    conversations = SqlAlchemyConversationStore(db_uri)
    agents = SqlAlchemyAgentStore(db_uri)
    permissions = SqlAlchemyPermissionStore(db_uri)
    agents.create(agent_id=_AGENT_ID, name="pl", bundle_location=f"{_AGENT_ID}/bundle")
    conv = conversations.create_conversation(title="PL", agent_id=_AGENT_ID)
    conversations.set_labels(conv.id, {F.LABEL_ID: conv.id, F.LABEL_ROLE: F.ROLE_LEAD})
    permissions.ensure_user(ALICE)
    permissions.grant(ALICE, conv.id, LEVEL_OWNER)

    app = FastAPI()

    @app.exception_handler(OmnigentError)
    async def _handle(request: Request, exc: OmnigentError) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    app.include_router(
        create_sessions_router(
            conversation_store=conversations,
            agent_store=agents,
            auth_provider=_provider(),
            permission_store=permissions,
        ),
        prefix="/v1",
    )
    return app, conversations, conv.id


def _person() -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_session_token(ALICE, _SECRET, 600, 'accounts')}"}


def _runner() -> dict[str, str]:
    token = _provider().mint_runner_token(ALICE, 600)
    assert token is not None
    return {"Authorization": f"Bearer {token}"}


_SERVER_ONLY = f"{F.LABEL_PREFIX}engine_mode"


@pytest.mark.parametrize(
    "headers",
    [_runner(), {**_person(), RUNNER_TUNNEL_TOKEN_HEADER: "rt_abc.def"}],
    ids=["runner-bearer", "runner-tunnel-header"],
)
@pytest.mark.parametrize("label", [_SERVER_ONLY, F.LABEL_ID, F.LABEL_STATE])
def test_a_runner_cannot_write_factory_labels_here(
    app_and_session: tuple[FastAPI, SqlAlchemyConversationStore, str],
    headers: dict[str, str],
    label: str,
) -> None:
    app, conversations, session_id = app_and_session

    resp = TestClient(app).patch(
        f"/v1/sessions/{session_id}",
        json={"title": "smuggled", "labels": {label: "x"}},
        headers=headers,
    )

    assert resp.status_code == 403, resp.text
    assert "sys_factory_set" in resp.json()["error"]["message"]
    conv = conversations.get_conversation(session_id)
    assert conv is not None
    assert conv.labels.get(label) != "x"
    assert conv.title == "PL"


def test_a_person_still_writes_factory_labels_and_a_runner_other_labels(
    app_and_session: tuple[FastAPI, SqlAlchemyConversationStore, str],
) -> None:
    app, conversations, session_id = app_and_session
    client = TestClient(app)

    person = client.patch(
        f"/v1/sessions/{session_id}", json={"labels": {_SERVER_ONLY: "auto"}}, headers=_person()
    )
    runner = client.patch(
        f"/v1/sessions/{session_id}", json={"labels": {"team": "ml"}}, headers=_runner()
    )

    assert person.status_code == 200, person.text
    assert runner.status_code == 200, runner.text
    conv = conversations.get_conversation(session_id)
    assert conv is not None
    assert conv.labels[_SERVER_ONLY] == "auto"
    assert conv.labels["team"] == "ml"
