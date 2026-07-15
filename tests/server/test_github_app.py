"""Tests for GitHub App config parsing and the async client.

HTTP is mocked at the transport boundary (``httpx.MockTransport``) so
the token exchange / refresh / user / SSH-key flows are exercised
without the network.
"""

from __future__ import annotations

import httpx
import pytest

from omnigent.server.github_app import (
    GitHubAppClient,
    GitHubAppConfig,
    GitHubAppError,
    build_authorize_url,
)


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "OMNIGENT_GITHUB_APP_ID",
        "OMNIGENT_GITHUB_APP_CLIENT_ID",
        "OMNIGENT_GITHUB_APP_CLIENT_SECRET",
        "OMNIGENT_GITHUB_APP_PRIVATE_KEY",
        "OMNIGENT_GITHUB_APP_PRIVATE_KEY_PATH",
        "OMNIGENT_GITHUB_APP_REDIRECT_URI",
        "OMNIGENT_GITHUB_APP_SLUG",
        "OMNIGENT_GITHUB_APP_TOKEN_ENC_KEY",
        "OMNIGENT_DOMAIN",
    ):
        monkeypatch.delenv(name, raising=False)


# ── Config ───────────────────────────────────────────────────────


def test_from_env_disabled_without_client(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    assert GitHubAppConfig.from_env() is None


def test_from_env_disabled_without_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("OMNIGENT_GITHUB_APP_CLIENT_ID", "Iv1abc")
    monkeypatch.setenv("OMNIGENT_GITHUB_APP_CLIENT_SECRET", "shh")
    # No redirect + no domain → stays disabled.
    assert GitHubAppConfig.from_env() is None


def test_from_env_derives_redirect_from_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("OMNIGENT_GITHUB_APP_CLIENT_ID", "Iv1abc")
    monkeypatch.setenv("OMNIGENT_GITHUB_APP_CLIENT_SECRET", "shh")
    monkeypatch.setenv("OMNIGENT_DOMAIN", "omni.example.com")
    monkeypatch.setenv("OMNIGENT_GITHUB_APP_SLUG", "omni-app")
    config = GitHubAppConfig.from_env()
    assert config is not None
    assert config.redirect_uri == "https://omni.example.com/v1/integrations/github/callback"
    assert config.install_url == "https://github.com/apps/omni-app/installations/new"
    # No dedicated enc key → derives from the client secret.
    assert config.token_enc_secret == "shh"


def test_from_env_explicit_redirect_and_enc_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("OMNIGENT_GITHUB_APP_CLIENT_ID", "Iv1abc")
    monkeypatch.setenv("OMNIGENT_GITHUB_APP_CLIENT_SECRET", "shh")
    monkeypatch.setenv("OMNIGENT_GITHUB_APP_REDIRECT_URI", "https://x/cb")
    monkeypatch.setenv("OMNIGENT_GITHUB_APP_TOKEN_ENC_KEY", "dedicated")
    config = GitHubAppConfig.from_env()
    assert config is not None
    assert config.redirect_uri == "https://x/cb"
    assert config.install_url is None  # no slug
    assert config.token_enc_secret == "dedicated"


def _config() -> GitHubAppConfig:
    return GitHubAppConfig(
        app_id=None,
        client_id="Iv1abc",
        client_secret="shh",
        private_key=None,
        redirect_uri="https://x/cb",
        slug=None,
        token_enc_secret="k",
    )


def test_build_authorize_url() -> None:
    url = build_authorize_url(_config(), state="STATE123")
    assert url.startswith("https://github.com/login/oauth/authorize?")
    assert "client_id=Iv1abc" in url
    assert "state=STATE123" in url
    # GitHub Apps take no scope param.
    assert "scope=" not in url


# ── Client ───────────────────────────────────────────────────────


def _client(handler) -> GitHubAppClient:
    return GitHubAppClient(_config(), transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_exchange_code_parses_tokens() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/login/oauth/access_token"
        return httpx.Response(
            200,
            json={
                "access_token": "ghu_new",
                "refresh_token": "ghr_new",
                "expires_in": 28800,
                "refresh_token_expires_in": 15897600,
                "scope": "",
            },
        )

    tokens = await _client(handler).exchange_code("code123")
    assert tokens.access_token == "ghu_new"
    assert tokens.refresh_token == "ghr_new"
    assert tokens.expires_at is not None
    assert tokens.refresh_token_expires_at is not None


@pytest.mark.asyncio
async def test_exchange_code_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "bad_verification_code"})

    with pytest.raises(GitHubAppError):
        await _client(handler).exchange_code("nope")


@pytest.mark.asyncio
async def test_non_expiring_tokens() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "ghu_x", "scope": "repo"})

    tokens = await _client(handler).exchange_code("c")
    assert tokens.refresh_token is None
    assert tokens.expires_at is None
    assert tokens.scopes == "repo"


@pytest.mark.asyncio
async def test_fetch_login() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer ghu_x"
        return httpx.Response(200, json={"login": "octocat", "id": 583231})

    login, uid = await _client(handler).fetch_login("ghu_x")
    assert (login, uid) == ("octocat", 583231)


@pytest.mark.asyncio
async def test_fetch_public_ssh_keys() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/users/octocat/keys"
        return httpx.Response(
            200,
            json=[{"id": 1, "key": "ssh-ed25519 AAAA a@b"}, {"id": 2, "key": "ssh-rsa BBBB c@d"}],
        )

    keys = await _client(handler).fetch_public_ssh_keys("octocat")
    assert keys == ("ssh-ed25519 AAAA a@b", "ssh-rsa BBBB c@d")


@pytest.mark.asyncio
async def test_fetch_public_ssh_keys_failure_is_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    assert await _client(handler).fetch_public_ssh_keys("ghost") == ()
