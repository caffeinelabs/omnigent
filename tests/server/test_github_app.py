"""Tests for the GitHub App HTTP client.

Network half only. HTTP is mocked at the transport boundary
(``httpx.MockTransport``); the App config (which owns the secret-shaped
fields) is built by :func:`tests.server.github_app_fixtures.make_config`
so this file never names a client secret alongside the httpx sink.
"""

from __future__ import annotations

import httpx
import pytest

from omnigent.server.github_app import GitHubAppError
from omnigent.server.github_app_client import GitHubAppClient
from tests.server.github_app_fixtures import make_config


def _client(handler) -> GitHubAppClient:
    return GitHubAppClient(make_config(), transport=httpx.MockTransport(handler))


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


@pytest.mark.asyncio
async def test_exchange_code_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "bad_verification_code"})

    with pytest.raises(GitHubAppError):
        await _client(handler).exchange_code("nope")


@pytest.mark.asyncio
async def test_refresh_token_roundtrip() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "ghu_refreshed", "scope": "repo"})

    tokens = await _client(handler).refresh_token("ghr_old")
    assert tokens.access_token == "ghu_refreshed"


@pytest.mark.asyncio
async def test_token_endpoint_non_200_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(GitHubAppError):
        await _client(handler).exchange_code("c")


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


@pytest.mark.asyncio
async def test_list_repos_projects_fields_and_stops_on_short_page() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.url.path == "/user/repos"
        return httpx.Response(
            200,
            json=[
                {
                    "full_name": "caffeinelabs/app",
                    "clone_url": "https://github.com/caffeinelabs/app.git",
                    "default_branch": "main",
                    "private": True,
                    "pushed_at": "2026-07-28T00:00:00Z",
                    "stargazers_count": 3,
                },
                {"description": "no full_name — skipped"},
            ],
        )

    repos = await _client(handler).list_repos("ghu_x")
    # Short page (< per_page) → only one request, no over-fetch.
    assert len(calls) == 1
    # Only the projected keys survive; the entry missing full_name is dropped.
    assert repos == [
        {
            "full_name": "caffeinelabs/app",
            "clone_url": "https://github.com/caffeinelabs/app.git",
            "default_branch": "main",
            "private": True,
            "pushed_at": "2026-07-28T00:00:00Z",
        }
    ]


@pytest.mark.asyncio
async def test_list_repos_non_200_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    with pytest.raises(GitHubAppError):
        await _client(handler).list_repos("ghu_bad")


@pytest.mark.asyncio
async def test_list_branches_returns_names_and_stops_on_short_page() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.url.path == "/repos/caffeinelabs/app/branches"
        return httpx.Response(
            200,
            json=[
                {"name": "main", "protected": True},
                {"name": "dev"},
                {"no_name": "skipped"},
            ],
        )

    branches = await _client(handler).list_branches("ghu_x", "caffeinelabs/app")
    # Short page (< per_page) → single request, entries without a name dropped.
    assert calls == ["/repos/caffeinelabs/app/branches"]
    assert branches == ["main", "dev"]


@pytest.mark.asyncio
async def test_list_branches_non_200_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    with pytest.raises(GitHubAppError):
        await _client(handler).list_branches("ghu_bad", "caffeinelabs/nope")


@pytest.mark.asyncio
async def test_list_pulls_projects_fields_and_stops_on_short_page() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.url.path == "/repos/caffeinelabs/app/pulls"
        # All states so merged/closed PRs surface too.
        assert request.url.params.get("state") == "all"
        return httpx.Response(
            200,
            json=[
                {
                    "number": 7,
                    "title": "feat: thing",
                    "html_url": "https://github.com/caffeinelabs/app/pull/7",
                    "head": {"ref": "feat-thing"},
                    "user": {"login": "octocat"},
                    "draft": False,
                    "state": "closed",
                    "merged_at": "2026-07-29T02:00:00Z",
                    "created_at": "2026-07-29T00:00:00Z",
                    "extra": "ignored",
                },
                {"no_number": "skipped"},
            ],
        )

    pulls = await _client(handler).list_pulls("ghu_x", "caffeinelabs/app")
    assert calls == ["/repos/caffeinelabs/app/pulls"]
    # A merged PR: state closed + merged True, and it is still returned.
    assert pulls == [
        {
            "number": 7,
            "title": "feat: thing",
            "html_url": "https://github.com/caffeinelabs/app/pull/7",
            "head_ref": "feat-thing",
            "draft": False,
            "state": "closed",
            "merged": True,
            "author_login": "octocat",
            "created_at": "2026-07-29T00:00:00Z",
            "body": "",
        }
    ]


@pytest.mark.asyncio
async def test_list_pulls_non_200_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Forbidden"})

    with pytest.raises(GitHubAppError):
        await _client(handler).list_pulls("ghu_bad", "caffeinelabs/app")


@pytest.mark.asyncio
async def test_search_pulls_maps_items_and_derives_repo() -> None:
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        assert request.url.path == "/search/issues"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "number": 11,
                        "title": "cross-repo PR",
                        "html_url": "https://github.com/other/repo/pull/11",
                        "user": {"login": "octocat"},
                        "draft": False,
                        "state": "closed",
                        "created_at": "2026-07-29T05:00:00Z",
                        "body": "see /c/conv_9",
                        "repository_url": "https://api.github.com/repos/other/repo",
                        "pull_request": {"merged_at": "2026-07-30T00:00:00Z"},
                    },
                    # No number → skipped.
                    {"title": "not a pr row"},
                ]
            },
        )

    pulls = await _client(handler).search_pulls("ghu_x", "conv_9 in:body type:pr author:octocat")
    assert calls == [{"q": "conv_9 in:body type:pr author:octocat", "per_page": "100"}]
    assert pulls == [
        {
            "number": 11,
            "title": "cross-repo PR",
            "html_url": "https://github.com/other/repo/pull/11",
            "head_ref": None,
            "draft": False,
            "state": "closed",
            "merged": True,
            "author_login": "octocat",
            "created_at": "2026-07-29T05:00:00Z",
            "body": "see /c/conv_9",
            "repo": "other/repo",
        }
    ]


@pytest.mark.asyncio
async def test_search_pulls_non_200_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": "Unprocessable"})

    with pytest.raises(GitHubAppError):
        await _client(handler).search_pulls("ghu_bad", "q in:body type:pr author:x")


@pytest.mark.asyncio
async def test_list_pull_commit_messages_extracts_messages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/caffeinelabs/app/pulls/7/commits"
        return httpx.Response(
            200,
            json=[
                {"commit": {"message": "feat: x\n\nOmnigent-Session: conv_abc"}},
                {"commit": {"message": "fix: y"}},
                {"no_commit": True},
            ],
        )

    msgs = await _client(handler).list_pull_commit_messages("ghu_x", "caffeinelabs/app", 7)
    assert msgs == ["feat: x\n\nOmnigent-Session: conv_abc", "fix: y"]
    assert any("Omnigent-Session: conv_abc" in m for m in msgs)


@pytest.mark.asyncio
async def test_list_pull_commit_messages_non_200_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    with pytest.raises(GitHubAppError):
        await _client(handler).list_pull_commit_messages("ghu_bad", "caffeinelabs/app", 9)


@pytest.mark.asyncio
async def test_list_check_runs_projects_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/caffeinelabs/app/commits/feat-x/check-runs"
        return httpx.Response(
            200,
            json={
                "total_count": 2,
                "check_runs": [
                    {"name": "build", "status": "completed", "conclusion": "success", "x": 1},
                    {"name": "test", "status": "in_progress", "conclusion": None},
                    "not-a-dict",
                ],
            },
        )

    runs = await _client(handler).list_check_runs("ghu_x", "caffeinelabs/app", "feat-x")
    assert runs == [
        {"name": "build", "status": "completed", "conclusion": "success"},
        {"name": "test", "status": "in_progress", "conclusion": None},
    ]


@pytest.mark.asyncio
async def test_list_check_runs_non_200_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": "bad ref"})

    with pytest.raises(GitHubAppError):
        await _client(handler).list_check_runs("ghu_bad", "caffeinelabs/app", "nope")
