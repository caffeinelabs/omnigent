"""The GitHub client's read-only methods for the factory poller.

``graphql``, ``list_pulls`` and ``branch_rules`` over ``httpx.MockTransport``;
the transport fails the test on anything but GET and GraphQL queries.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from omnigent.factory_gates import BranchRules
from omnigent.server.github_app_client import (
    BRANCH_RULES_TTL_S,
    GitHubApiError,
    GitHubAppClient,
    graphql_operations,
)
from tests.server.github_app_fixtures import make_config

_TOKEN = "ghu_client_test"


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _read_only(handler: Any) -> Any:
    def _guard(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert request.url.path == "/graphql", f"write attempted: POST {request.url}"
            assert json.loads(request.content)["query"].lstrip().startswith("query")
        else:
            assert request.method == "GET", f"write attempted: {request.method}"
        assert request.headers["authorization"] == f"Bearer {_TOKEN}"
        return handler(request)

    return _guard


def _client(handler: Any, clock: _Clock | None = None) -> GitHubAppClient:
    return GitHubAppClient(
        make_config(),
        transport=httpx.MockTransport(_read_only(handler)),
        clock=clock or _Clock(),
    )


# ── graphql ─────────────────────────────────────────────────────


async def test_graphql_returns_data_and_the_queried_rate_limit() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "data": {
                    "rateLimit": {"remaining": 4321, "resetAt": "2026-10-26T12:00:00Z"},
                    "viewer": {"login": "luka"},
                }
            },
            headers={"x-ratelimit-remaining": "1"},
        )

    result = await _client(handler).graphql(
        _TOKEN, "query($n: Int!) { viewer { login } }", {"n": 1}
    )
    assert result.data["viewer"] == {"login": "luka"}
    assert result.rate_limit is not None
    assert result.rate_limit.remaining == 4321
    assert result.rate_limit.reset_at == 1793016000
    assert seen == [{"query": "query($n: Int!) { viewer { login } }", "variables": {"n": 1}}]


async def test_graphql_falls_back_to_rate_limit_headers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": {"viewer": {"login": "x"}}},
            headers={"x-ratelimit-remaining": "77", "x-ratelimit-reset": "1793016000"},
        )

    result = await _client(handler).graphql(_TOKEN, "query { viewer { login } }")
    assert result.rate_limit is not None
    assert (result.rate_limit.remaining, result.rate_limit.reset_at) == (77, 1793016000)


async def test_graphql_errors_raise_unless_partial_is_allowed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {"repository": {"pr1": None, "pr2": {"number": 2}}},
                "errors": [{"type": "NOT_FOUND", "message": "Could not resolve PR 1"}],
            },
        )

    client = _client(handler)
    with pytest.raises(GitHubApiError, match="Could not resolve PR 1"):
        await client.graphql(_TOKEN, "query { x }")
    result = await client.graphql(_TOKEN, "query { x }", allow_partial=True)
    assert result.data["repository"]["pr2"] == {"number": 2}
    assert result.errors[0]["type"] == "NOT_FOUND"


async def test_graphql_rate_limited_error_is_a_429() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": None, "errors": [{"type": "RATE_LIMITED", "message": "slow"}]}
        )

    with pytest.raises(GitHubApiError) as info:
        await _client(handler).graphql(_TOKEN, "query { x }", allow_partial=True)
    assert info.value.status_code == 429


async def test_graphql_http_error_carries_status_and_retry_after() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"message": "secondary rate limit"},
            headers={"retry-after": "90", "x-ratelimit-remaining": "0"},
        )

    with pytest.raises(GitHubApiError) as info:
        await _client(handler).graphql(_TOKEN, "query { x }")
    assert info.value.status_code == 403
    assert info.value.retry_after == 90.0
    assert info.value.rate_limit is not None and info.value.rate_limit.remaining == 0


# ── list_pulls ──────────────────────────────────────────────────


async def test_list_pulls_pages_through_the_rest_listing() -> None:
    pulls = [{"number": n, "head": {"ref": f"factory/a/t{n}"}} for n in range(250, 0, -1)]
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/caffeinelabs/app/pulls"
        params = dict(request.url.params)
        seen.append(params)
        page, per_page = int(params["page"]), int(params["per_page"])
        return httpx.Response(200, json=pulls[(page - 1) * per_page : page * per_page])

    client = _client(handler)
    pages = [
        await client.list_pulls(_TOKEN, "caffeinelabs/app", state="all", page=page)
        for page in (1, 2, 3, 4)
    ]
    assert [len(p) for p in pages] == [100, 100, 50, 0]
    assert pages[1][0]["number"] == 150
    assert seen[0] == {
        "state": "all",
        "sort": "created",
        "direction": "desc",
        "page": "1",
        "per_page": "100",
    }
    defaults = await client.list_pulls(_TOKEN, "caffeinelabs/app")
    assert len(defaults) == 100
    assert seen[-1]["state"] == "open"


async def test_list_pulls_non_200_raises_with_the_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    with pytest.raises(GitHubApiError) as info:
        await _client(handler).list_pulls(_TOKEN, "caffeinelabs/app")
    assert info.value.status_code == 401


async def test_list_pull_files_pages_and_keeps_rename_sources() -> None:
    files: list[dict[str, Any]] = [{"filename": f"src/f{i}.ts"} for i in range(150)]
    files.append({"filename": "ci/ci.yml", "previous_filename": ".github/workflows/ci.yml"})
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/caffeinelabs/app/pulls/7/files"
        page, per_page = int(request.url.params["page"]), int(request.url.params["per_page"])
        seen.append(request.url.params["page"])
        return httpx.Response(200, json=files[(page - 1) * per_page : page * per_page])

    paths = await _client(handler).list_pull_files(_TOKEN, "caffeinelabs/app", 7)
    assert seen == ["1", "2"]
    assert len(paths) == 152
    assert paths[-2:] == ["ci/ci.yml", ".github/workflows/ci.yml"]


async def test_list_pull_files_non_200_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "nope"})

    with pytest.raises(GitHubApiError) as info:
        await _client(handler).list_pull_files(_TOKEN, "caffeinelabs/app", 7)
    assert info.value.status_code == 403


@pytest.mark.parametrize(
    "document",
    [
        "mutation { addComment(input: {subjectId: 1, body: 2}) { clientMutationId } }",
        "  # a comment\n  mutation M { x }",
        "query A { x }\nmutation B { y }",
        "subscription { x }",
    ],
)
async def test_graphql_refuses_to_send_a_write(document: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("a GraphQL write reached the transport")

    with pytest.raises(ValueError, match="read-only"):
        await _client(handler).graphql(_TOKEN, document)


@pytest.mark.parametrize(
    ("document", "operations"),
    [
        ('query($mutation: Int) { x(a: "mutation") { y } }', ["query"]),
        ('query { x(body: """\nmutation { y }\n""") { z } }', ["query"]),
        ("{ viewer { login } }", []),
        (
            "query A @mutation { x } fragment F on X { y }",
            ["query", "A", "fragment", "F", "on", "X"],
        ),
    ],
)
def test_graphql_operations_reads_only_the_top_level(document: str, operations: list[str]) -> None:
    assert graphql_operations(document) == operations


# ── branch_rules ────────────────────────────────────────────────


def _rules_handler(
    rules: list[dict[str, Any]], classic: tuple[int, dict[str, Any]], calls: list[str]
) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/repos/caffeinelabs/app/rules/branches/main":
            page = int(request.url.params["page"])
            per_page = int(request.url.params["per_page"])
            return httpx.Response(200, json=rules[(page - 1) * per_page : page * per_page])
        if request.url.path == "/repos/caffeinelabs/app/branches/main/protection":
            return httpx.Response(classic[0], json=classic[1])
        return httpx.Response(404, json={})

    return handler


async def test_branch_rules_reads_rulesets_and_classic_protection() -> None:
    rules = [{"type": "deletion"} for _ in range(100)] + [
        {
            "type": "required_status_checks",
            "parameters": {"required_status_checks": [{"context": "ci / test"}]},
        },
        {"type": "pull_request", "parameters": {"required_review_thread_resolution": True}},
    ]
    classic = {
        "required_status_checks": {"contexts": ["lint"], "checks": [{"context": "e2e"}]},
        "lock_branch": {"enabled": True},
    }
    calls: list[str] = []
    out = await _client(_rules_handler(rules, (200, classic), calls)).branch_rules(
        _TOKEN, "caffeinelabs/app", "main"
    )
    assert out == BranchRules(
        contexts=frozenset({"ci / test", "lint", "e2e"}),
        rule_types=frozenset(
            {"deletion", "required_status_checks", "pull_request", "classic_lock_branch"}
        ),
        thread_resolution=True,
        classic_readable=True,
    )
    # Two ruleset pages (the first was full), then the classic protection.
    assert calls == [
        "/repos/caffeinelabs/app/rules/branches/main",
        "/repos/caffeinelabs/app/rules/branches/main",
        "/repos/caffeinelabs/app/branches/main/protection",
    ]


@pytest.mark.parametrize(
    ("status", "readable"), [(404, True), (403, False)], ids=["unprotected", "unreadable"]
)
async def test_branch_rules_classic_protection_status(status: int, readable: bool) -> None:
    calls: list[str] = []
    out = await _client(_rules_handler([], (status, {"message": "x"}), calls)).branch_rules(
        _TOKEN, "caffeinelabs/app", "main"
    )
    assert out.contexts == frozenset()
    assert out.classic_readable is readable


async def test_branch_rules_are_cached_for_an_hour_per_repo_and_base() -> None:
    calls: list[str] = []
    clock = _Clock()
    client = _client(_rules_handler([], (404, {}), calls), clock)
    await client.branch_rules(_TOKEN, "caffeinelabs/app", "main")
    assert len(calls) == 2
    clock.now += BRANCH_RULES_TTL_S - 1
    await client.branch_rules(_TOKEN, "CaffeineLabs/App", "main")
    assert len(calls) == 2
    clock.now += 2
    await client.branch_rules(_TOKEN, "caffeinelabs/app", "main")
    assert len(calls) == 4


async def test_branch_rules_unreadable_rulesets_raise() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    with pytest.raises(GitHubApiError) as info:
        await _client(handler).branch_rules(_TOKEN, "caffeinelabs/app", "main")
    assert info.value.status_code == 404
