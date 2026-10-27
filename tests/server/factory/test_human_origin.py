"""Human origin (Tier 0): agents never pull a cockpit lever.

Agents act with their creator's identity, so the human write routes refuse a
runner bearer (``org: runner``), the runner tunnel token header and an
untrusted ``Origin`` with 403 ``human_only``, while a person's browser cookie
and CLI bearer keep working. The tests run the real accounts auth provider,
so the tokens are the ones the server mints.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import jwt
import pytest
import pytest_asyncio
from fastapi.routing import APIRoute
from starlette.requests import Request

from omnigent.entities.factory import FactoryDecision, FactoryPr
from omnigent.runner.identity import RUNNER_TUNNEL_TOKEN_HEADER
from omnigent.server.accounts_config import AccountsConfig
from omnigent.server.auth import LEVEL_OWNER, UnifiedAuthProvider
from omnigent.server.factory import nudge
from omnigent.server.factory.api import cockpit_write
from omnigent.server.factory.origin import require_human_origin
from omnigent.server.oidc import mint_session_token
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit, error_code
from tests.server.factory.conftest import FactoryWorld

_SECRET = secrets.token_bytes(32)
_TRUSTED = "https://omnigent.example"
HEAD = "b2c3d4e" + "f" * 33


def _config() -> AccountsConfig:
    return AccountsConfig(
        cookie_secret=_SECRET,
        session_ttl_hours=8,
        base_url="http://localhost:8000",
        init_admin_password=None,
        invite_ttl_seconds=3600,
        magic_ttl_seconds=600,
    )


@pytest.fixture()
def provider() -> UnifiedAuthProvider:
    return UnifiedAuthProvider(source="accounts", accounts_config=_config())


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
    provider: UnifiedAuthProvider,
) -> AsyncIterator[Cockpit]:
    nudge.reset()
    monkeypatch.setenv("OMNIGENT_WS_ALLOWED_ORIGINS", _TRUSTED)
    async for value in build_cockpit(world, db_uri, monkeypatch, auth_provider=provider):
        yield value
    nudge.reset()


# ── Credentials ──────────────────────────────────────────────────────────────


def _session(user: str = "alice") -> str:
    return mint_session_token(user, _SECRET, 3600, "accounts")


def browser(user: str = "alice", origin: str = _TRUSTED) -> dict[str, str]:
    """A person's browser: the session cookie plus the page's Origin."""
    return {"Cookie": f"{_config().session_cookie_name}={_session(user)}", "Origin": origin}


def cli(user: str = "alice") -> dict[str, str]:
    """A person's own CLI: ``omnigent login`` stores a session bearer."""
    return {"Authorization": f"Bearer {_session(user)}"}


def runner_bearer(provider: UnifiedAuthProvider, user: str = "alice") -> dict[str, str]:
    """A sandbox runner's callback bearer, minted for the session owner."""
    token = provider.mint_runner_token(user, 1800)
    assert token is not None
    return {"Authorization": f"Bearer {token}"}


# ── Fixtures for each human route ────────────────────────────────────────────


def _pr(cockpit: Cockpit) -> FactoryPr:
    pr, _ = cockpit.store.upsert_pr(
        cockpit.fid, "caffeinelabs/app", 9631, project_slug="api-version", head_sha=HEAD
    )
    return pr


def _decision(cockpit: Cockpit, key: str = "k1") -> FactoryDecision:
    decision, _ = cockpit.store.create_decision(
        cockpit.fid,
        dedupe_key=key,
        question="Where should the ENV badge go?",
        source="agent",
        options=[{"id": "1", "label": "Header, right"}],
        blocking=False,
        allow_text=True,
        project_slug="api-version",
        asked_by_session_id=cockpit.world.id("pl"),
    )
    return decision


def _new_root(cockpit: Cockpit) -> str:
    conv = cockpit.world.make_session("atlas", agent="lead", title="Atlas")
    cockpit.permissions.grant("alice", conv.id, LEVEL_OWNER)
    return conv.id


def _project(cockpit: Cockpit) -> str:
    cockpit.store.upsert_project(
        cockpit.fid,
        "api-version",
        title="API version",
        repo="caffeinelabs/app",
        charter={"goal": "Add GET /version."},
        pl_session_id=cockpit.world.id("pl"),
    )
    return "api-version"


_NEW_PROJECT = {"slug": "db-version", "repo": "caffeinelabs/app", "goal": "Show the DB version."}

_Call = tuple[str, str, Any]
# Each route with a body a person's request succeeds with.
_ROUTES: dict[str, Callable[[Cockpit], _Call]] = {
    "start": lambda c: (
        "POST",
        "/v1/factory/factories",
        {
            "root_session_id": _new_root(c),
            "name": "Atlas factory",
            "cadence": "demo",
            "projects": [{**_NEW_PROJECT, "env_needed": False}],
        },
    ),
    "add_project": lambda c: ("POST", f"/v1/factory/factories/{c.fid}/projects", _NEW_PROJECT),
    "charter": lambda c: (
        "PATCH",
        f"/v1/factory/factories/{c.fid}/projects/{_project(c)}",
        {"goal": "Add GET /version with the git sha."},
    ),
    "control": lambda c: (
        "POST",
        f"/v1/factory/factories/{c.fid}/control",
        {"action": "pause", "target": "project:api-version"},
    ),
    "answer": lambda c: (
        "POST",
        f"/v1/factory/decisions/{_decision(c).id}/answer",
        {"option_id": "1"},
    ),
    "withdraw": lambda c: ("POST", f"/v1/factory/decisions/{_decision(c).id}/withdraw", {}),
    "feedback": lambda c: (
        "POST",
        f"/v1/factory/prs/{_pr(c).id}/feedback",
        {"verdict": "approved"},
    ),
    "refresh": lambda c: ("POST", f"/v1/factory/prs/{_pr(c).id}/refresh", None),
}


async def _call(cockpit: Cockpit, route: str, headers: dict[str, str]) -> httpx.Response:
    method, path, body = _ROUTES[route](cockpit)
    return await cockpit.client.request(method, path, json=body, headers=headers)


# ── Refusals ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("route", sorted(_ROUTES))
async def test_a_runner_token_gets_human_only_on_every_human_route(
    cockpit: Cockpit, provider: UnifiedAuthProvider, route: str
) -> None:
    """The runner's bearer authenticates as the owner, but never pulls a human lever."""
    resp = await _call(cockpit, route, runner_bearer(provider))

    assert (resp.status_code, error_code(resp)) == (403, "human_only"), resp.text
    assert "runner token" in resp.json()["error"]["message"]
    assert cockpit.mailer.items == []


@pytest.mark.parametrize("route", sorted(_ROUTES))
async def test_the_runner_tunnel_header_gets_human_only(cockpit: Cockpit, route: str) -> None:
    """Even with a person's bearer, a request a runner relays is not a person's."""
    resp = await _call(cockpit, route, {**cli(), RUNNER_TUNNEL_TOKEN_HEADER: "rt_abc.def"})

    assert (resp.status_code, error_code(resp)) == (403, "human_only"), resp.text


async def test_a_runner_token_in_the_cookie_is_still_a_runner(
    cockpit: Cockpit, provider: UnifiedAuthProvider
) -> None:
    token = runner_bearer(provider)["Authorization"].removeprefix("Bearer ")
    headers = {"Cookie": f"{_config().session_cookie_name}={token}", "Origin": _TRUSTED}

    resp = await _call(cockpit, "control", headers)

    assert (resp.status_code, error_code(resp)) == (403, "human_only")


async def test_a_cross_site_cookie_request_gets_human_only(cockpit: Cockpit) -> None:
    resp = await _call(cockpit, "control", browser(origin="https://evil.example"))

    assert (resp.status_code, error_code(resp)) == (403, "human_only")
    assert "untrusted Origin" in resp.json()["error"]["message"]


# ── People still pull the levers ─────────────────────────────────────────────


@pytest.mark.parametrize("route", sorted(_ROUTES))
@pytest.mark.parametrize("who", ["browser", "cli"])
async def test_a_person_with_a_cookie_or_cli_bearer_passes(
    cockpit: Cockpit, route: str, who: str
) -> None:
    headers = browser() if who == "browser" else cli()

    resp = await _call(cockpit, route, headers)

    assert resp.status_code in (200, 201, 202), resp.text


async def test_agent_routes_keep_accepting_the_runner_token(
    cockpit: Cockpit, provider: UnifiedAuthProvider
) -> None:
    """Only human routes refuse runners; the factory's agent routes are theirs."""
    resp = await cockpit.client.get(
        "/v1/factory/roster",
        params={"from_session_id": cockpit.world.id("pl")},
        headers=runner_bearer(provider),
    )

    assert resp.status_code == 200, resp.text


def test_every_cockpit_write_route_requires_human_origin() -> None:
    """A write route added without the dependency fails here, not in production."""
    writes = [
        route
        for route in cockpit_write.router.routes
        if isinstance(route, APIRoute) and route.methods & {"POST", "PUT", "PATCH", "DELETE"}
    ]
    assert len(writes) == 8
    for route in writes:
        calls = [dep.call for dep in route.dependant.dependencies]
        assert require_human_origin in calls, route.path


# ── Tokens ───────────────────────────────────────────────────────────────────


def _request(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw})


def test_runner_tokens_carry_the_runner_origin_and_still_resolve_to_the_owner(
    provider: UnifiedAuthProvider,
) -> None:
    """``_check_cookie`` ignores the ``org`` claim: the runner keeps acting as its owner."""
    headers = runner_bearer(provider)
    claims = jwt.decode(
        headers["Authorization"].removeprefix("Bearer "), _SECRET, algorithms=["HS256"]
    )

    assert claims["org"] == "runner"
    assert claims["sub"] == "alice"
    assert provider.get_user_id(_request(headers)) == "alice"
    assert provider.session_token_claims(_request(headers)) == ("bearer", claims)
    assert provider.session_token_claims(_request(cli()))[1].get("org") is None  # type: ignore[index]
    assert provider.session_token_claims(_request({"Authorization": "Bearer x.y.z"})) is None


def test_extra_claims_never_override_the_standard_ones() -> None:
    token = mint_session_token(
        "alice", _SECRET, 60, "accounts", extra_claims={"sub": "mallory", "org": "runner"}
    )
    claims = jwt.decode(token, _SECRET, algorithms=["HS256"])

    assert claims["sub"] == "alice"
    assert claims["provider"] == "accounts"
    assert claims["org"] == "runner"
