"""``GET /v1/sessions/{id}/child_sessions?include_factory=true``.

Factory leads and project leads are top-level sessions joined to the session
that spawned them only by the ``omnigent.factory.parent`` label. The opt-in
flag lists them next to real sub-agents so the Agents panel renders the org
chart; without it the response must not change at all.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import update

from omnigent import factory as F
from omnigent.db.db_models import SqlConversation, current_workspace_id
from omnigent.entities import Conversation
from omnigent.errors import OmnigentError
from omnigent.server.auth import LEVEL_OWNER
from omnigent.server.routes.sessions.routes_items import register_items_routes
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore

pytestmark = pytest.mark.asyncio


class _HeaderAuth:
    """The caller is whoever ``X-Test-User`` names."""

    def get_user_id(self, request: Any) -> str | None:
        return request.headers.get("X-Test-User")


@dataclass
class _Org:
    store: SqlAlchemyConversationStore
    lead: Conversation
    children: list[Conversation]
    pl: Conversation
    peer: Conversation
    ic: Conversation


def _backdate(store: SqlAlchemyConversationStore, conv_id: str, created_at: int) -> None:
    with store._conv_engine.begin() as connection:
        connection.execute(
            update(SqlConversation)
            .where(
                SqlConversation.workspace_id == current_workspace_id(),
                SqlConversation.id == conv_id,
            )
            .values(created_at=created_at)
        )


def _member(
    store: SqlAlchemyConversationStore,
    title: str,
    parent_id: str,
    labels: dict[str, str] | None = None,
) -> Conversation:
    conv = store.create_conversation(kind="default", title=title)
    store.set_labels(conv.id, {F.LABEL_ID: parent_id, F.LABEL_PARENT: parent_id, **(labels or {})})
    return conv


@pytest.fixture()
def org(db_uri: str) -> _Org:
    """Lead "Atlas" with two sub-agents, a PL (with an IC) and a peer lead."""
    store = SqlAlchemyConversationStore(db_uri)
    lead = store.create_conversation(kind="default", title="Atlas")
    children = [
        store.create_conversation(
            kind="sub_agent", title=f"researcher:{name}", parent_conversation_id=lead.id
        )
        for name in ("auth", "billing")
    ]
    pl = _member(
        store,
        "PL · api-version",
        lead.id,
        {
            F.LABEL_ROLE: F.ROLE_PROJECT_LEAD,
            F.LABEL_PROJECT: "api-version",
            F.LABEL_SUMMARY: "2 ICs on /version",
            F.LABEL_HEALTH: F.HEALTH_BLOCKED,
        },
    )
    peer = _member(store, "Lead B · Sentinel", lead.id, {F.LABEL_ROLE: F.ROLE_LEAD})
    ic = store.create_conversation(
        kind="sub_agent", title="ic:task-1", parent_conversation_id=pl.id
    )
    # Distinct creation times make the merged order deterministic:
    # children[0] < pl < children[1] < peer.
    base = lead.created_at - 1000
    for offset, conv in enumerate([children[0], pl, children[1], peer]):
        _backdate(store, conv.id, base + offset * 10)
    return _Org(store=store, lead=lead, children=children, pl=pl, peer=peer, ic=ic)


async def _client(
    store: SqlAlchemyConversationStore,
    db_uri: str,
    *,
    permission_store: SqlAlchemyPermissionStore | None = None,
) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    router = APIRouter()
    register_items_routes(
        router,
        conversation_store=store,
        agent_store=SqlAlchemyAgentStore(db_uri),
        auth_provider=_HeaderAuth() if permission_store is not None else None,  # type: ignore[arg-type]
        permission_store=permission_store,
    )
    app.include_router(router, prefix="/v1")

    async def _omnigent_error(_request: Any, exc: Exception) -> JSONResponse:
        assert isinstance(exc, OmnigentError)
        return JSONResponse(status_code=exc.http_status, content={"error": exc.message})

    app.add_exception_handler(OmnigentError, _omnigent_error)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture()
async def client(org: _Org, db_uri: str) -> AsyncIterator[httpx.AsyncClient]:
    async for c in _client(org.store, db_uri):
        yield c


def _ids(body: dict[str, Any]) -> list[str]:
    return [row["id"] for row in body["data"]]


async def test_flag_off_response_is_unchanged(
    org: _Org, client: httpx.AsyncClient, db_uri: str
) -> None:
    url = f"/v1/sessions/{org.lead.id}/child_sessions"
    labelled = await client.get(url)
    assert labelled.status_code == 200
    assert _ids(labelled.json()) == [org.children[1].id, org.children[0].id]

    # Drop the factory labels: the default response must be byte-identical.
    for member in (org.pl, org.peer):
        org.store.delete_label(member.id, F.LABEL_PARENT)
    unlabelled = await client.get(url)
    explicit_off = await client.get(url, params={"include_factory": "false"})

    assert labelled.content == unlabelled.content
    assert explicit_off.content == unlabelled.content


async def test_flag_on_merges_labelled_top_level_members(
    org: _Org, client: httpx.AsyncClient
) -> None:
    resp = await client.get(
        f"/v1/sessions/{org.lead.id}/child_sessions", params={"include_factory": "true"}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Newest first, members interleaved with sub-agents by created_at.
    assert _ids(body) == [org.peer.id, org.children[1].id, org.pl.id, org.children[0].id]
    # Cursors still page the sub-agent children only.
    assert body["first_id"] == org.children[1].id
    assert body["last_id"] == org.children[0].id
    assert body["has_more"] is False

    rows = {row["id"]: row for row in body["data"]}
    pl_row = rows[org.pl.id]
    assert pl_row["object"] == "child_session"
    assert pl_row["kind"] == "default"
    assert pl_row["parent_session_id"] == org.lead.id
    assert pl_row["title"] == "PL · api-version"
    assert pl_row["labels"][F.LABEL_ROLE] == F.ROLE_PROJECT_LEAD
    assert pl_row["labels"][F.LABEL_PROJECT] == "api-version"
    assert pl_row["labels"][F.LABEL_SUMMARY] == "2 ICs on /version"
    assert pl_row["labels"][F.LABEL_HEALTH] == F.HEALTH_BLOCKED
    assert rows[org.children[0].id]["kind"] == "sub_agent"
    # The PL's own IC is not hoisted into the lead's list.
    assert org.ic.id not in rows


async def test_flag_on_ascending_order(org: _Org, client: httpx.AsyncClient) -> None:
    resp = await client.get(
        f"/v1/sessions/{org.lead.id}/child_sessions",
        params={"include_factory": "true", "order": "asc"},
    )

    assert _ids(resp.json()) == [org.children[0].id, org.pl.id, org.children[1].id, org.peer.id]


async def test_member_children_nest_under_the_member(org: _Org, client: httpx.AsyncClient) -> None:
    resp = await client.get(
        f"/v1/sessions/{org.pl.id}/child_sessions", params={"include_factory": "true"}
    )

    assert _ids(resp.json()) == [org.ic.id]


async def test_flag_on_skips_archived_sub_agent_and_self_labelled_rows(
    org: _Org, client: httpx.AsyncClient
) -> None:
    org.store.update_conversation(org.peer.id, archived=True)
    # A sub-agent carrying the label is already listed under its real parent.
    org.store.set_labels(org.ic.id, {F.LABEL_PARENT: org.lead.id})
    org.store.set_labels(org.lead.id, {F.LABEL_PARENT: org.lead.id})

    resp = await client.get(
        f"/v1/sessions/{org.lead.id}/child_sessions", params={"include_factory": "true"}
    )

    assert _ids(resp.json()) == [org.children[1].id, org.pl.id, org.children[0].id]


async def test_flag_on_members_only_on_the_first_page(
    org: _Org, client: httpx.AsyncClient
) -> None:
    url = f"/v1/sessions/{org.lead.id}/child_sessions"
    first = (await client.get(url, params={"include_factory": "true", "limit": 1})).json()
    assert first["has_more"] is True
    assert first["last_id"] == org.children[1].id
    assert set(_ids(first)) == {org.children[1].id, org.pl.id, org.peer.id}

    second = (
        await client.get(
            url, params={"include_factory": "true", "limit": 1, "after": first["last_id"]}
        )
    ).json()
    assert _ids(second) == [org.children[0].id]
    assert second["has_more"] is False


async def test_flag_on_honours_the_title_filter(org: _Org, client: httpx.AsyncClient) -> None:
    resp = await client.get(
        f"/v1/sessions/{org.lead.id}/child_sessions",
        params={"include_factory": "true", "tool": "researcher", "session_name": "auth"},
    )

    assert _ids(resp.json()) == [org.children[0].id]


async def test_flag_on_excludes_an_unrelated_owners_sessions(org: _Org, db_uri: str) -> None:
    permissions = SqlAlchemyPermissionStore(db_uri)
    for user in ("alice", "bob"):
        permissions.ensure_user(user)
    for conv in (org.lead, org.pl, org.peer):
        permissions.grant("alice", conv.id, LEVEL_OWNER)
    # Bob's session names alice's lead as its factory parent; labels are not an ACL.
    intruder = _member(org.store, "Bob's PL", org.lead.id, {F.LABEL_ROLE: F.ROLE_PROJECT_LEAD})
    permissions.grant("bob", intruder.id, LEVEL_OWNER)
    url = f"/v1/sessions/{org.lead.id}/child_sessions"

    async for client in _client(org.store, db_uri, permission_store=permissions):
        alice = await client.get(
            url, params={"include_factory": "true"}, headers={"X-Test-User": "alice"}
        )
        assert alice.status_code == 200, alice.text
        ids = _ids(alice.json())
        assert intruder.id not in ids
        assert {org.pl.id, org.peer.id} <= set(ids)

        # Bob cannot read the lead, so he cannot enumerate it at all.
        bob = await client.get(
            url, params={"include_factory": "true"}, headers={"X-Test-User": "bob"}
        )
        assert bob.status_code == 404
