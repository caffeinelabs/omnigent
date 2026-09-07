"""Tests for the browser-facing pre-sandbox model-options endpoint."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import omnigent.server.routes.sandbox_model_options as mod
from omnigent.errors import OmnigentError
from omnigent.server.routes.sandbox_model_options import create_sandbox_model_options_router


class _AuthProvider:
    """Resolves a fixed user, or None to force a 401."""

    def __init__(self, user_id: str | None) -> None:
        self._user_id = user_id

    def get_user_id(self, _request: Request) -> str | None:
        return self._user_id


def _app(
    *,
    auth_provider: object | None,
    store: object | None,
    client: object | None,
) -> TestClient:
    app = FastAPI()
    app.state.databricks_store = store
    app.state.databricks_client = client

    # The real app maps OmnigentError → its http_status (401 for UNAUTHORIZED);
    # register the same so require_user's 401 is exercised, not a bare 500.
    async def _omnigent_error(_request: Request, exc: OmnigentError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content={"error": exc.message})

    app.add_exception_handler(OmnigentError, _omnigent_error)  # type: ignore[arg-type]
    app.include_router(create_sandbox_model_options_router(auth_provider), prefix="/v1")  # type: ignore[arg-type]
    return TestClient(app, raise_server_exceptions=False)


_ROWS = [
    {
        "id": "databricks-gateway/databricks-glm-5-3-flash",
        "model": "databricks-glm-5-3-flash",
        "providerID": "databricks-gateway",
        "displayName": "GLM 5 3 Flash",
        "isDefault": True,
    }
]


def test_returns_models_for_connected_user(monkeypatch) -> None:
    async def _token(user_id, *, store, client):
        assert user_id == "alice@example.com"
        return ("dapi-x", "https://ws.example.com")

    seen: dict[str, object] = {}

    def _resolve(harness, host, token, *, default_model=None):
        seen.update(harness=harness, host=host, token=token, default_model=default_model)
        return _ROWS

    monkeypatch.setattr(mod, "resolve_databricks_token", _token)
    monkeypatch.setattr(mod, "resolve_sandbox_model_options", _resolve)
    monkeypatch.setenv("OMNIGENT_DATABRICKS_GATEWAY_MODEL", "databricks-glm-5-3-flash")

    tc = _app(auth_provider=_AuthProvider("alice@example.com"), store=object(), client=object())
    resp = tc.get("/v1/sandbox/model-options", params={"harness": "opencode-native"})
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-store"
    assert resp.json() == {"connected": True, "models": _ROWS}
    # The harness + the deployment default flow through to the resolver.
    assert seen == {
        "harness": "opencode-native",
        "host": "https://ws.example.com",
        "token": "dapi-x",
        "default_model": "databricks-glm-5-3-flash",
    }


def test_connected_false_when_user_has_no_databricks(monkeypatch) -> None:
    async def _token(user_id, *, store, client):
        return None

    monkeypatch.setattr(mod, "resolve_databricks_token", _token)
    tc = _app(auth_provider=_AuthProvider("bob@example.com"), store=object(), client=object())
    resp = tc.get("/v1/sandbox/model-options", params={"harness": "opencode-native"})
    assert resp.status_code == 200
    assert resp.json() == {"connected": False, "models": []}


def test_connected_false_when_provider_not_configured() -> None:
    # No databricks store/client on app.state (provider not configured here).
    tc = _app(auth_provider=_AuthProvider("bob@example.com"), store=None, client=None)
    resp = tc.get("/v1/sandbox/model-options", params={"harness": "opencode-native"})
    assert resp.status_code == 200
    assert resp.json() == {"connected": False, "models": []}


def test_broker_fault_degrades_to_connected_false(monkeypatch) -> None:
    async def _boom(user_id, *, store, client):
        raise RuntimeError("db down")

    monkeypatch.setattr(mod, "resolve_databricks_token", _boom)
    tc = _app(auth_provider=_AuthProvider("bob@example.com"), store=object(), client=object())
    resp = tc.get("/v1/sandbox/model-options", params={"harness": "opencode-native"})
    assert resp.status_code == 200
    assert resp.json() == {"connected": False, "models": []}


def test_unauthenticated_multi_user_is_401() -> None:
    # An auth provider that resolves no user must fail closed (never vend).
    tc = _app(auth_provider=_AuthProvider(None), store=object(), client=object())
    resp = tc.get("/v1/sandbox/model-options", params={"harness": "opencode-native"})
    assert resp.status_code == 401
