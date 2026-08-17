"""Tests for the GitHub App integration routes.

Builds a minimal FastAPI app with the integration router, a header-based
auth provider, and a fake GitHub client so the connect → callback →
status → disconnect flow is exercised end-to-end without the network.
"""

from __future__ import annotations

import jwt
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
        return [
            # Caller's open PR, opened after session start → kept.
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
            },
            # Someone else's PR → filtered out.
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
            },
            # Caller's OWN recent PR in this repo but from ANOTHER session (its
            # commits lack this session's trailer) → must be filtered out. This
            # is the leak the trailer check fixes.
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
            },
        ]

    async def search_pulls(self, access_token: str, query: str) -> list[dict[str, object]]:
        self.search_calls: list[str] = getattr(self, "search_calls", [])
        self.search_calls.append(query)
        # Default: the cross-repo body-link search finds nothing, so tests that
        # only exercise the trailer path are unaffected. A test can set
        # ``client.search_results`` to exercise the body-link union.
        return list(getattr(self, "search_results", []))

    async def list_pull_commit_messages(
        self, access_token: str, full_name: str, number: int
    ) -> list[str]:
        self.commit_calls: list[tuple[str, int]] = getattr(self, "commit_calls", [])
        self.commit_calls.append((full_name, number))
        # Only PRs 1 and 4 were opened in the "conv_1" session, so only their
        # commits carry the session trailer.
        if number in (1, 4):
            return ["do the thing\n\nOmnigent-Session: conv_1\n"]
        return ["unrelated work with no session trailer"]

    async def list_check_runs(
        self, access_token: str, full_name: str, ref: str
    ) -> list[dict[str, object]]:
        self.check_calls: list[tuple[str, str]] = getattr(self, "check_calls", [])
        self.check_calls.append((full_name, ref))
        return [
            {"name": "build", "status": "completed", "conclusion": "success"},
            {"name": "test", "status": "completed", "conclusion": "success"},
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
    from omnigent.server.ci_watch import CiWatchRegistry

    app.state.ci_watch_registry = CiWatchRegistry()
    return TestClient(app, follow_redirects=False), store, client


def test_session_pulls_scopes_to_author_and_session_start(db_uri: str) -> None:
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
    # Only #4 (merged) and #1 (open) survive — their commits carry this
    # session's trailer. #2 (pre-session) and #3 (other author) are pre-filtered;
    # crucially #5 (caller's own recent PR in the same repo, but from another
    # session → no trailer) is EXCLUDED by the trailer check. Newest first.
    assert [p["number"] for p in body["pulls"]] == [4, 1]
    assert body["pulls"][0]["merged"] is True
    assert body["pulls"][0]["repo"] == "caffeinelabs/app"
    assert client.pull_calls == ["caffeinelabs/app"]
    # The commit-messages endpoint was consulted for the candidate PRs.
    assert ("caffeinelabs/app", 5) in getattr(client, "commit_calls", [])


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


# ── CI watch arming ─────────────────────────────────────────────────


def test_arm_ci_watch_registers_session_prs(db_uri: str) -> None:
    convs = {
        "conv_1": _FakeConv(
            labels={_REPO_LABEL_KEY: "https://github.com/caffeinelabs/app#main"},
            created_at=_SESSION_START,
        )
    }
    tc, store, _client = _app_with_convs(db_uri, convs)
    store.upsert(
        "alice@example.com",
        github_login="octocat",
        github_user_id=42,
        tokens=GitHubTokenSet("ghu_a", "ghr_a", None, None, "repo"),
    )
    resp = tc.post("/v1/integrations/github/sessions/conv_1/ci-watch", headers=_USER)
    assert resp.status_code == 200
    body = resp.json()
    assert body["armed"] is True
    # PRs #4 and #1 are this session's (trailer-confirmed) → both watched.
    assert body["watched"] == 2
    assert set(body["prs"]) == {"caffeinelabs/app#4", "caffeinelabs/app#1"}
    # The registry now holds the watch.
    reg = tc.app.state.ci_watch_registry
    assert len(reg) == 1
    assert reg.snapshot()[0].session_id == "conv_1"


def test_arm_ci_watch_no_prs_is_not_armed(db_uri: str) -> None:
    convs = {"conv_2": _FakeConv(labels={}, created_at=_SESSION_START)}
    tc, store, _client = _app_with_convs(db_uri, convs)
    store.upsert(
        "alice@example.com",
        github_login="octocat",
        github_user_id=42,
        tokens=GitHubTokenSet("ghu_a", "ghr_a", None, None, "repo"),
    )
    resp = tc.post("/v1/integrations/github/sessions/conv_2/ci-watch", headers=_USER)
    assert resp.status_code == 200
    assert resp.json()["armed"] is False
    assert len(tc.app.state.ci_watch_registry) == 0


def test_arm_ci_watch_unconnected(db_uri: str) -> None:
    convs = {
        "conv_3": _FakeConv(
            labels={_REPO_LABEL_KEY: "https://github.com/caffeinelabs/app"},
            created_at=_SESSION_START,
        )
    }
    tc, _store, _client = _app_with_convs(db_uri, convs)
    resp = tc.post("/v1/integrations/github/sessions/conv_3/ci-watch", headers=_USER)
    assert resp.status_code == 200
    assert resp.json() == {"armed": False, "reason": "github not connected"}


def test_arm_ci_watch_missing_session_404(db_uri: str) -> None:
    tc, store, _client = _app_with_convs(db_uri, {})
    store.upsert(
        "alice@example.com",
        github_login="octocat",
        github_user_id=42,
        tokens=GitHubTokenSet("ghu_a", "ghr_a", None, None, "repo"),
    )
    resp = tc.post("/v1/integrations/github/sessions/nope/ci-watch", headers=_USER)
    assert resp.status_code == 404


def test_ci_watch_dryrun_returns_runs_and_conclusion(db_uri: str) -> None:
    convs = {
        "conv_1": _FakeConv(
            labels={_REPO_LABEL_KEY: "https://github.com/caffeinelabs/app#main"},
            created_at=_SESSION_START,
        )
    }
    tc, store, _client = _app_with_convs(db_uri, convs)
    store.upsert(
        "alice@example.com",
        github_login="octocat",
        github_user_id=42,
        tokens=GitHubTokenSet("ghu_a", "ghr_a", None, None, "repo"),
    )
    resp = tc.get("/v1/integrations/github/sessions/conv_1/ci-watch", headers=_USER)
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is True
    # Both trailer-confirmed session PRs are probed, each aggregating to success.
    assert {p["repo"] for p in body["prs"]} == {"caffeinelabs/app"}
    assert all(p["conclusion"] == "success" for p in body["prs"])
    assert all("error" not in p for p in body["prs"])


# ── Body-link (cross-repo search) union path ─────────────────────────


def _link_body(session_id: str, *, base: str = "https://omni.example.com") -> str:
    """A PR body carrying this session's Open-in-Omnigent link (anchor form)."""
    url = f"{base}/c/{session_id}"
    return f'work\n\n<a href="{url}"><img alt="Open in Omnigent" src="https://cdn/x.svg"></a>'


def test_session_pulls_finds_body_linked_pr_in_uncloned_repo(db_uri: str) -> None:
    # A session with NO cloned repo label: the trailer path never runs, but the
    # cross-repo body-link search still associates a PR whose body links back.
    convs = {"conv_9": _FakeConv(labels={}, created_at=_SESSION_START)}
    tc, store, client = _app_with_convs(db_uri, convs)
    store.upsert(
        "alice@example.com",
        github_login="octocat",
        github_user_id=42,
        tokens=GitHubTokenSet("ghu_a", "ghr_a", None, None, "repo"),
    )
    client.search_results = [
        # Kept: octocat, after session start, body links this session.
        {
            "number": 11,
            "title": "cross-repo PR",
            "html_url": "https://github.com/other/repo/pull/11",
            "head_ref": None,
            "draft": False,
            "state": "open",
            "merged": False,
            "author_login": "octocat",
            "created_at": "2026-07-29T05:00:00Z",
            "body": _link_body("conv_9"),
            "repo": "other/repo",
        },
        # Dropped: body does NOT carry this session's link (search is fuzzy).
        {
            "number": 12,
            "title": "unrelated hit",
            "html_url": "https://github.com/other/repo/pull/12",
            "head_ref": None,
            "draft": False,
            "state": "open",
            "merged": False,
            "author_login": "octocat",
            "created_at": "2026-07-29T06:00:00Z",
            "body": "mentions conv_9 in prose but no link",
            "repo": "other/repo",
        },
        # Dropped: opened before the session started.
        {
            "number": 13,
            "title": "pre-session",
            "html_url": "https://github.com/other/repo/pull/13",
            "head_ref": None,
            "draft": False,
            "state": "open",
            "merged": False,
            "author_login": "octocat",
            "created_at": "2026-07-01T00:00:00Z",
            "body": _link_body("conv_9"),
            "repo": "other/repo",
        },
    ]
    resp = tc.get("/v1/integrations/github/sessions/conv_9/pull-requests", headers=_USER)
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is True
    assert [p["number"] for p in body["pulls"]] == [11]
    assert body["pulls"][0]["repo"] == "other/repo"
    # The raw body is never surfaced to the client.
    assert "body" not in body["pulls"][0]
    # The search was scoped to the session id, PR type, and the caller's login.
    assert client.search_calls == ["conv_9 in:body type:pr author:octocat"]


def test_session_pulls_union_dedups_trailer_and_body_link(db_uri: str) -> None:
    # PR #1 is found by BOTH the cloned-repo trailer path and the body-link
    # search; it must appear once, with the richer trailer record (head_ref).
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
    client.search_results = [
        {
            "number": 1,
            "title": "feat",
            "html_url": "https://github.com/caffeinelabs/app/pull/1",
            "head_ref": None,  # search result carries no head ref
            "draft": False,
            "state": "open",
            "merged": False,
            "author_login": "octocat",
            "created_at": "2026-07-29T01:00:00Z",
            "body": _link_body("conv_1"),
            "repo": "caffeinelabs/app",
        }
    ]
    resp = tc.get("/v1/integrations/github/sessions/conv_1/pull-requests", headers=_USER)
    assert resp.status_code == 200
    pulls = resp.json()["pulls"]
    # #4 and #1 from the trailer path; #1 not duplicated by the search hit.
    assert [p["number"] for p in pulls] == [4, 1]
    pr1 = next(p for p in pulls if p["number"] == 1)
    # Trailer record won on dedup, so its head_ref survives (search had None).
    assert pr1["head_ref"] == "feat"
