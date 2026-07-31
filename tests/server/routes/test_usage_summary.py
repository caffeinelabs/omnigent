"""End-to-end tests for ``GET /v1/usage/summary``.

Drives the real router through a FastAPI ``TestClient`` against file-backed
SQLite stores — the regression guard for the schema drift that made the
handler 500 (``session_usage`` / ``harness_override`` / ``kind`` are no longer
columns on ``conversations``). Covers the happy path, each grouping dimension,
and the admin / single-user scope gating.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from omnigent.errors import OmnigentError
from omnigent.server.auth import LEVEL_OWNER, UnifiedAuthProvider
from omnigent.server.routes.usage import create_usage_router
from omnigent.stores.conversation_store.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)
from omnigent.stores.permission_store.sqlalchemy_store import (
    SqlAlchemyPermissionStore,
)

ALICE = "alice@example.com"
BOB = "bob@example.com"
ADMIN = "admin@example.com"
_AGENT_ID = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def conv_store(db_uri: str) -> SqlAlchemyConversationStore:
    return SqlAlchemyConversationStore(db_uri)


@pytest.fixture
def perm_store(db_uri: str) -> SqlAlchemyPermissionStore:
    return SqlAlchemyPermissionStore(db_uri)


def _install_error_handler(app: FastAPI) -> None:
    """Mirror ``create_app()``'s OmnigentError → HTTP status translation."""

    @app.exception_handler(OmnigentError)
    async def _handle(request: Request, exc: OmnigentError) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )


def _app(
    conv_store: SqlAlchemyConversationStore,
    *,
    perm_store: SqlAlchemyPermissionStore | None,
    header_auth: bool,
) -> FastAPI:
    """Mount the usage router. ``header_auth`` picks multi-user (header) vs
    single-user (no auth, no permission store)."""
    app = FastAPI()
    _install_error_handler(app)
    app.include_router(
        create_usage_router(
            conv_store,
            auth_provider=UnifiedAuthProvider(source="header") if header_auth else None,
            permission_store=perm_store,
        ),
        prefix="/v1",
    )
    return app


def _seed(
    conv_store: SqlAlchemyConversationStore,
    perm_store: SqlAlchemyPermissionStore | None,
    *,
    usage: dict[str, object],
    owner: str | None = None,
    harness_override: str | None = None,
) -> str:
    """Create a conversation (created_at ≈ now, so it lands in every window)
    with the given usage, optionally owner-granted."""
    conv = conv_store.create_conversation(agent_id=_AGENT_ID)
    if harness_override is not None:
        conv_store.update_conversation(conv.id, harness_override=harness_override)
    conv_store.set_session_usage(conv.id, usage)
    if owner is not None and perm_store is not None:
        perm_store.grant(owner, conv.id, level=LEVEL_OWNER)
    return conv.id


def test_single_user_happy_path_no_500(
    conv_store: SqlAlchemyConversationStore,
) -> None:
    # The regression: this used to 500 because list_usage_records referenced
    # dropped columns. Single-user mode → caller None → all rows.
    _seed(
        conv_store,
        None,
        usage={"input_tokens": 100, "output_tokens": 20, "total_cost_usd": 1.5},
    )
    _seed(
        conv_store,
        None,
        usage={"input_tokens": 50, "output_tokens": 10, "total_cost_usd": 0.5},
    )
    client = TestClient(_app(conv_store, perm_store=None, header_auth=False))

    resp = client.get("/v1/usage/summary?period=30days&group_by=model")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_input_tokens"] == 150
    assert body["total_output_tokens"] == 30
    assert body["total_cost_usd"] == 2.0
    assert body["period"] == "30days"
    assert body["group_by"] == "model"


def test_header_user_sees_only_own_spend(
    conv_store: SqlAlchemyConversationStore,
    perm_store: SqlAlchemyPermissionStore,
) -> None:
    _seed(conv_store, perm_store, usage={"total_cost_usd": 1.0}, owner=ALICE)
    _seed(conv_store, perm_store, usage={"total_cost_usd": 9.0}, owner=BOB)
    client = TestClient(_app(conv_store, perm_store=perm_store, header_auth=True))

    resp = client.get("/v1/usage/summary?period=30days", headers={"X-Forwarded-Email": ALICE})

    assert resp.status_code == 200, resp.text
    assert resp.json()["total_cost_usd"] == 1.0  # not Bob's 9.0


def test_group_by_provider_derived_from_model(
    conv_store: SqlAlchemyConversationStore,
) -> None:
    _seed(
        conv_store,
        None,
        usage={
            "total_cost_usd": 4.0,
            "by_model": {
                "z-ai/glm-5.2": {"total_cost_usd": 1.0, "input_tokens": 10},
                "claude-opus-4-8": {"total_cost_usd": 3.0, "input_tokens": 20},
            },
        },
    )
    client = TestClient(_app(conv_store, perm_store=None, header_auth=False))

    resp = client.get("/v1/usage/summary?period=30days&group_by=provider")

    assert resp.status_code == 200, resp.text
    groups = {g["group_key"]: g["total_cost_usd"] for g in resp.json()["groups"]}
    assert groups == {"z-ai": 1.0, "anthropic": 3.0}


def test_group_by_harness_uses_override(
    conv_store: SqlAlchemyConversationStore,
) -> None:
    _seed(conv_store, None, usage={"total_cost_usd": 2.0}, harness_override="pi")
    client = TestClient(_app(conv_store, perm_store=None, header_auth=False))

    resp = client.get("/v1/usage/summary?period=30days&group_by=harness")

    assert resp.status_code == 200, resp.text
    groups = {g["group_key"]: g["total_cost_usd"] for g in resp.json()["groups"]}
    assert groups == {"pi": 2.0}


def test_buckets_sum_to_total(
    conv_store: SqlAlchemyConversationStore,
) -> None:
    _seed(conv_store, None, usage={"total_cost_usd": 1.25})
    _seed(conv_store, None, usage={"total_cost_usd": 2.75})
    client = TestClient(_app(conv_store, perm_store=None, header_auth=False))

    body = client.get("/v1/usage/summary?period=30days").json()

    assert body["buckets"], "expected a non-empty time series"
    assert sum(b["total_cost_usd"] for b in body["buckets"]) == pytest.approx(4.0)


def test_non_admin_requesting_other_user_is_forbidden(
    conv_store: SqlAlchemyConversationStore,
    perm_store: SqlAlchemyPermissionStore,
) -> None:
    _seed(conv_store, perm_store, usage={"total_cost_usd": 1.0}, owner=BOB)
    client = TestClient(_app(conv_store, perm_store=perm_store, header_auth=True))

    resp = client.get(f"/v1/usage/summary?user={BOB}", headers={"X-Forwarded-Email": ALICE})

    assert resp.status_code == 403, resp.text


def test_single_user_mode_rejects_user_param(
    conv_store: SqlAlchemyConversationStore,
) -> None:
    client = TestClient(_app(conv_store, perm_store=None, header_auth=False))

    resp = client.get(f"/v1/usage/summary?user={ALICE}")

    assert resp.status_code == 400, resp.text


def test_admin_can_see_all_users(
    conv_store: SqlAlchemyConversationStore,
    perm_store: SqlAlchemyPermissionStore,
) -> None:
    perm_store.ensure_user(ADMIN, is_admin=True)
    _seed(conv_store, perm_store, usage={"total_cost_usd": 1.0}, owner=ALICE)
    _seed(conv_store, perm_store, usage={"total_cost_usd": 9.0}, owner=BOB)
    client = TestClient(_app(conv_store, perm_store=perm_store, header_auth=True))

    resp = client.get(
        "/v1/usage/summary?period=30days&user=all",
        headers={"X-Forwarded-Email": ADMIN},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_cost_usd"] == 10.0  # Alice + Bob
    assert body["user"] is None  # all-users view reports no single scope
