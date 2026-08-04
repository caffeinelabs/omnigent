"""Tests for the GitHub App integration routes.

Builds a minimal FastAPI app with the integration router, a header-based
auth provider, and a fake GitHub client so the connect → callback →
status → disconnect flow is exercised end-to-end without the network.
"""

from __future__ import annotations

import jwt
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

from omnigent.errors import OmnigentError
from omnigent.server.github_app import GitHubAppConfig, GitHubTokenSet
from omnigent.server.github_store import GithubConnectionStore
from omnigent.server.routes.integrations_github import create_integrations_github_router
from omnigent.server.secretbox import SecretBox


class _HeaderAuth:
    """Auth provider reading the user id from ``X-Test-User``."""

    def get_user_id(self, request: object) -> str | None:
        return getattr(request, "headers", {}).get("x-test-user")


class _FakeClient:
    """Stand-in for :class:`GitHubAppClient`."""

    def __init__(self) -> None:
        self.exchanged: list[str] = []

    async def exchange_code(self, code: str) -> GitHubTokenSet:
        self.exchanged.append(code)
        return GitHubTokenSet(
            access_token="ghu_new",
            refresh_token="ghr_new",
            expires_at=None,
            refresh_token_expires_at=None,
            scopes="repo",
        )

    async def fetch_login(self, access_token: str) -> tuple[str, int]:
        return "octocat", 42

    async def list_repos(self, access_token: str) -> list[dict[str, object]]:
        return [
            {
                "full_name": "caffeinelabs/app",
                "clone_url": "https://github.com/caffeinelabs/app.git",
                "default_branch": "main",
                "private": True,
                "pushed_at": "2026-07-28T00:00:00Z",
            }
        ]

    async def list_branches(self, access_token: str, full_name: str) -> list[str]:
        self.branch_calls: list[str] = getattr(self, "branch_calls", [])
        self.branch_calls.append(full_name)
        return ["main", "dev"]

    async def list_pulls(self, access_token: str, full_name: str) -> list[dict[str, object]]:
        self.pull_calls: list[str] = getattr(self, "pull_calls", [])
        self.pull_calls.append(full_name)
        # PRs 1 and 4 carry this session's Open-in-Omnigent link (stamped by the
        # MCP proxy) in their body → they belong to conv_1.
        this_session_link = f"[Open in Omnigent]({_SESSION_LINK})"
        return [
            # Caller's open PR, opened after session start, carries the link → kept.
            {
                "number": 1,
                "title": "feat",
                "html_url": f"https://github.com/{full_name}/pull/1",
                "head_ref": "feat",
                "draft": False,
                "state": "open",
                "merged": False,
                "author_login": "octocat",
                "created_at": "2026-07-29T01:00:00Z",
                "body": f"does the thing\n\n{this_session_link}",
            },
            # Caller's MERGED PR from this session → still kept (any state).
            {
                "number": 4,
                "title": "merged one",
                "html_url": f"https://github.com/{full_name}/pull/4",
                "head_ref": "merged-one",
                "draft": False,
                "state": "closed",
                "merged": True,
                "author_login": "octocat",
                "created_at": "2026-07-29T01:30:00Z",
                "body": this_session_link,
            },
            # Caller's PR, but opened BEFORE the session started → filtered out.
            {
                "number": 2,
                "title": "old",
                "html_url": f"https://github.com/{full_name}/pull/2",
                "head_ref": "old",
                "draft": False,
                "state": "closed",
                "merged": False,
                "author_login": "octocat",
                "created_at": "2026-07-28T00:00:00Z",
                "body": this_session_link,
            },
            # Someone else's PR → filtered out (author).
            {
                "number": 3,
                "title": "theirs",
                "html_url": f"https://github.com/{full_name}/pull/3",
                "head_ref": "theirs",
                "draft": False,
                "state": "open",
                "merged": False,
                "author_login": "someone-else",
                "created_at": "2026-07-29T02:00:00Z",
                "body": this_session_link,
            },
            # Caller's OWN recent PR in this repo but from ANOTHER session: its
            # body carries a DIFFERENT session's link → must be filtered out.
            # This is the leak the link check fixes.
            {
                "number": 5,
                "title": "unrelated other-session PR",
                "html_url": f"https://github.com/{full_name}/pull/5",
                "head_ref": "other",
                "draft": False,
                "state": "open",
                "merged": False,
                "author_login": "octocat",
                "created_at": "2026-07-29T01:45:00Z",
                "body": "[Open in Omnigent](https://omni.example/c/other_session)",
            },
        ]


class _FakeConv:
    """Minimal conversation stub exposing the fields the PR route reads."""

    def __init__(self, labels: dict[str, str], created_at: int) -> None:
        self.labels = labels
        self.created_at = created_at


class _FakeConvStore:
    """Conversation store stub returning a single canned session."""

    def __init__(self, convs: dict[str, _FakeConv]) -> None:
        self._convs = convs

    def get_conversation(self, session_id: str) -> _FakeConv | None:
        return self._convs.get(session_id)


def _config() -> GitHubAppConfig:
    return GitHubAppConfig(
        app_id=None,
        client_id="Iv1abc",
        client_secret="shh",
        private_key=None,
        redirect_uri="https://x/v1/integrations/github/callback",
        slug="omni-app",
        token_enc_secret="enc-secret",
    )


def _app(db_uri: str) -> tuple[TestClient, GithubConnectionStore, GitHubAppConfig, _FakeClient]:
    config = _config()
    store = GithubConnectionStore(db_uri, SecretBox(config.token_enc_secret))
    client = _FakeClient()
    app = FastAPI()

    @app.exception_handler(OmnigentError)
    async def _handle(request: Request, exc: OmnigentError) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    app.include_router(
        create_integrations_github_router(
            config, store, auth_provider=_HeaderAuth(), client=client
        ),
        prefix="/v1",
    )
    # TestClient must not chase the external GitHub redirect.
    return TestClient(app, follow_redirects=False), store, config, client


_USER = {"X-Test-User": "alice@example.com"}


def test_open_in_omnigent_button_svg_is_public(db_uri: str) -> None:
    tc, _store, _config, _client = _app(db_uri)
    # No auth header — GitHub's image proxy fetches this without cookies.
    resp = tc.get("/v1/integrations/github/open-in-omnigent.svg")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/svg+xml")
    assert "Open in Omnigent" in resp.text
    assert resp.text.startswith("<svg")
    # Dark variant differs from the (default) light one.
    dark = tc.get("/v1/integrations/github/open-in-omnigent.svg", params={"theme": "dark"})
    assert dark.status_code == 200
    assert dark.text != resp.text


def test_status_unconnected(db_uri: str) -> None:
    tc, _store, _config, _client = _app(db_uri)
    resp = tc.get("/v1/integrations/github/status", headers=_USER)
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["connected"] is False
    assert body["login"] is None
    assert body["install_url"] == "https://github.com/apps/omni-app/installations/new"


def test_status_requires_auth(db_uri: str) -> None:
    tc, _store, _config, _client = _app(db_uri)
    # No X-Test-User header → require_user raises 401.
    resp = tc.get("/v1/integrations/github/status")
    assert resp.status_code == 401


def test_connect_redirects_to_github_with_signed_state(db_uri: str) -> None:
    tc, _store, config, _client = _app(db_uri)
    resp = tc.get(
        "/v1/integrations/github/connect", params={"return_to": "/settings"}, headers=_USER
    )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("https://github.com/login/oauth/authorize?")
    # Pull the state back out and verify it is signed + bound to the user.
    state = location.split("state=", 1)[1].split("&", 1)[0]
    claims = jwt.decode(state, config.token_enc_secret, algorithms=["HS256"])
    assert claims["sub"] == "alice@example.com"
    assert claims["return_to"] == "/settings"


def test_callback_stores_connection_and_redirects(db_uri: str) -> None:
    tc, store, config, client = _app(db_uri)
    state = jwt.encode(
        {"sub": "alice@example.com", "return_to": "/settings", "nonce": "n", "exp": 9999999999},
        config.token_enc_secret,
        algorithm="HS256",
    )
    resp = tc.get(
        "/v1/integrations/github/callback",
        params={"code": "abc", "state": state},
        headers=_USER,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/settings?github=connected"
    assert client.exchanged == ["abc"]
    conn = store.get("alice@example.com", with_tokens=True)
    assert conn is not None
    assert conn.github_login == "octocat"
    assert conn.access_token == "ghu_new"


def test_callback_rejects_state_user_mismatch(db_uri: str) -> None:
    tc, store, config, _client = _app(db_uri)
    # State was signed for someone else — must not bind to alice.
    state = jwt.encode(
        {"sub": "mallory@example.com", "return_to": "/settings", "nonce": "n", "exp": 9999999999},
        config.token_enc_secret,
        algorithm="HS256",
    )
    resp = tc.get(
        "/v1/integrations/github/callback",
        params={"code": "abc", "state": state},
        headers=_USER,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/settings?github=error"
    assert store.get("alice@example.com") is None


def test_callback_rejects_bad_state(db_uri: str) -> None:
    tc, _store, _config, _client = _app(db_uri)
    resp = tc.get(
        "/v1/integrations/github/callback",
        params={"code": "abc", "state": "garbage"},
        headers=_USER,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/settings?github=error"


def test_disconnect(db_uri: str) -> None:
    tc, store, _config, _client = _app(db_uri)
    store.upsert(
        "alice@example.com",
        github_login="octocat",
        github_user_id=42,
        tokens=GitHubTokenSet("ghu_a", "ghr_a", None, None, "repo"),
    )
    resp = tc.post("/v1/integrations/github/disconnect", headers=_USER)
    assert resp.status_code == 200
    assert resp.json() == {"disconnected": True}
    assert store.get("alice@example.com") is None


def test_repos_unconnected_returns_false(db_uri: str) -> None:
    tc, _store, _config, _client = _app(db_uri)
    resp = tc.get("/v1/integrations/github/repos", headers=_USER)
    assert resp.status_code == 200
    assert resp.json() == {"connected": False, "repos": []}


def test_repos_lists_when_connected(db_uri: str) -> None:
    tc, store, _config, _client = _app(db_uri)
    store.upsert(
        "alice@example.com",
        github_login="octocat",
        github_user_id=42,
        tokens=GitHubTokenSet("ghu_a", "ghr_a", None, None, "repo"),
    )
    resp = tc.get("/v1/integrations/github/repos", headers=_USER)
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is True
    assert [r["full_name"] for r in body["repos"]] == ["caffeinelabs/app"]
    assert body["repos"][0]["default_branch"] == "main"


def test_repo_branches_unconnected_returns_false(db_uri: str) -> None:
    tc, _store, _config, _client = _app(db_uri)
    resp = tc.get("/v1/integrations/github/repos/caffeinelabs/app/branches", headers=_USER)
    assert resp.status_code == 200
    assert resp.json() == {"connected": False, "branches": []}


def test_repo_branches_lists_when_connected(db_uri: str) -> None:
    tc, store, _config, client = _app(db_uri)
    store.upsert(
        "alice@example.com",
        github_login="octocat",
        github_user_id=42,
        tokens=GitHubTokenSet("ghu_a", "ghr_a", None, None, "repo"),
    )
    resp = tc.get("/v1/integrations/github/repos/caffeinelabs/app/branches", headers=_USER)
    assert resp.status_code == 200
    assert resp.json() == {"connected": True, "branches": ["main", "dev"]}
    assert client.branch_calls == ["caffeinelabs/app"]


def test_repo_branches_rejects_bad_name(db_uri: str) -> None:
    tc, store, _config, _client = _app(db_uri)
    store.upsert(
        "alice@example.com",
        github_login="octocat",
        github_user_id=42,
        tokens=GitHubTokenSet("ghu_a", "ghr_a", None, None, "repo"),
    )
    # A path-traversal owner must be rejected before any GitHub call.
    resp = tc.get("/v1/integrations/github/repos/..%2Fx/app/branches", headers=_USER)
    assert resp.status_code in (400, 404)


# ── session pull-requests ───────────────────────────────────────────

_REPO_LABEL_KEY = "omnigent.sandbox.repo"
# 2026-07-29T00:00:00Z as epoch seconds — the fake session's start time.
_SESSION_START = 1785283200
# This instance's public base URL and the resulting Open-in-Omnigent link the
# MCP proxy stamps into PR bodies for session "conv_1".
_PUBLIC_BASE = "https://omni.example"
_SESSION_LINK = f"{_PUBLIC_BASE}/c/conv_1"


def _app_with_convs(
    db_uri: str, convs: dict[str, _FakeConv]
) -> tuple[TestClient, GithubConnectionStore, _FakeClient]:
    config = _config()
    store = GithubConnectionStore(db_uri, SecretBox(config.token_enc_secret))
    client = _FakeClient()
    app = FastAPI()
    app.include_router(
        create_integrations_github_router(
            config,
            store,
            auth_provider=_HeaderAuth(),
            client=client,
            conversation_store=_FakeConvStore(convs),
        ),
        prefix="/v1",
    )
    return TestClient(app, follow_redirects=False), store, client


def test_session_pulls_scopes_to_author_and_session_start(
    db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMNIGENT_ACCOUNTS_BASE_URL", _PUBLIC_BASE)
    convs = {
        "conv_1": _FakeConv(
            labels={_REPO_LABEL_KEY: "https://github.com/caffeinelabs/app#main"},
            created_at=_SESSION_START,
        )
    }
    tc, store, client = _app_with_convs(db_uri, convs)
    store.upsert(
        "alice@example.com",
        github_login="octocat",
        github_user_id=42,
        tokens=GitHubTokenSet("ghu_a", "ghr_a", None, None, "repo"),
    )
    resp = tc.get("/v1/integrations/github/sessions/conv_1/pull-requests", headers=_USER)
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is True
    # Only #4 (merged) and #1 (open) survive — their bodies carry this session's
    # Open-in-Omnigent link. #2 (pre-session) and #3 (other author) are
    # pre-filtered; crucially #5 (caller's own recent PR in the same repo, but
    # carrying ANOTHER session's link) is EXCLUDED by the link check. Newest first.
    assert [p["number"] for p in body["pulls"]] == [4, 1]
    assert body["pulls"][0]["merged"] is True
    assert body["pulls"][0]["repo"] == "caffeinelabs/app"
    # The raw body is not leaked to the panel — the link was only a match key.
    assert "body" not in body["pulls"][0]
    assert client.pull_calls == ["caffeinelabs/app"]


def test_session_pulls_empty_when_no_repo_label(db_uri: str) -> None:
    convs = {"conv_2": _FakeConv(labels={}, created_at=_SESSION_START)}
    tc, store, _client = _app_with_convs(db_uri, convs)
    store.upsert(
        "alice@example.com",
        github_login="octocat",
        github_user_id=42,
        tokens=GitHubTokenSet("ghu_a", "ghr_a", None, None, "repo"),
    )
    resp = tc.get("/v1/integrations/github/sessions/conv_2/pull-requests", headers=_USER)
    assert resp.status_code == 200
    assert resp.json() == {"connected": True, "pulls": []}


def test_session_pulls_unconnected_returns_false(db_uri: str) -> None:
    convs = {
        "conv_3": _FakeConv(
            labels={_REPO_LABEL_KEY: "https://github.com/caffeinelabs/app"},
            created_at=_SESSION_START,
        )
    }
    tc, _store, _client = _app_with_convs(db_uri, convs)
    resp = tc.get("/v1/integrations/github/sessions/conv_3/pull-requests", headers=_USER)
    assert resp.status_code == 200
    assert resp.json() == {"connected": False, "pulls": []}


def test_session_pulls_missing_session_404(db_uri: str) -> None:
    tc, store, _client = _app_with_convs(db_uri, {})
    store.upsert(
        "alice@example.com",
        github_login="octocat",
        github_user_id=42,
        tokens=GitHubTokenSet("ghu_a", "ghr_a", None, None, "repo"),
    )
    resp = tc.get("/v1/integrations/github/sessions/nope/pull-requests", headers=_USER)
    assert resp.status_code == 404
