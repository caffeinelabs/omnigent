"""A read-only fake of the GitHub API slice the factory poller uses.

Not a test module. :class:`FakeGitHub` serves ``GET /repos/{repo}/pulls``,
the branch rules and protection reads, and the poller's GraphQL refresh
query from in-memory :class:`FakePr` objects. Its transport fails the test
on any request that could write to GitHub: a method other than GET, a POST
anywhere but ``/graphql``, or a GraphQL document that is not a query.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from omnigent.server.github_app_client import GitHubAppClient
from tests.server.github_app_fixtures import make_config

_ALIAS_RE = re.compile(r"(pr\d+): pullRequest\(number: (\d+)\)")


def iso(epoch: float) -> str:
    """:returns: GitHub's ISO-8601 form of *epoch*."""
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha(seed: str) -> str:
    """:returns: A deterministic 40-hex sha for *seed*, e.g. ``sha("a")``."""
    return (seed.encode().hex() * 40)[:40]


@dataclass
class FakeCheck:
    """One check on the head: a CheckRun (or a StatusContext with ``status=None``)."""

    name: str
    status: str | None = "COMPLETED"
    conclusion: str | None = "SUCCESS"
    required: bool = True
    state: str | None = None
    at: str = "2026-10-26T10:00:00Z"

    def node(self) -> dict[str, Any]:
        """:returns: The ``statusCheckRollup.contexts`` node."""
        if self.status is None:
            return {
                "__typename": "StatusContext",
                "context": self.name,
                "state": self.state or "SUCCESS",
                "targetUrl": f"https://ci.example/{self.name}",
                "createdAt": self.at,
                "isRequired": self.required,
            }
        return {
            "__typename": "CheckRun",
            "name": self.name,
            "status": self.status,
            "conclusion": self.conclusion if self.status == "COMPLETED" else None,
            "detailsUrl": f"https://ci.example/{self.name}",
            "startedAt": self.at,
            "completedAt": self.at if self.status == "COMPLETED" else None,
            "isRequired": self.required,
        }


@dataclass
class FakePr:
    """A pull request as GitHub would report it."""

    repo: str
    number: int
    head_ref: str
    head_sha: str
    created_at: float
    title: str = "factory change"
    base_ref: str = "main"
    state: str = "OPEN"
    is_draft: bool = False
    mergeable: str = "MERGEABLE"
    labels: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=lambda: ["services/caffeine-api/src/app.ts"])
    commits: list[tuple[str, str]] = field(default_factory=list)
    comments: list[dict[str, Any]] = field(default_factory=list)
    reviews: list[dict[str, Any]] = field(default_factory=list)
    checks: list[FakeCheck] = field(default_factory=list)
    merged_at: float | None = None
    merged_by: str | None = None
    merge_sha: str | None = None
    closed_at: float | None = None
    closed_by: str | None = None

    @property
    def url(self) -> str:
        """:returns: The PR web URL."""
        return f"https://github.com/{self.repo}/pull/{self.number}"

    def push(self, new_sha: str, at: float, checks: list[FakeCheck] | None = None) -> None:
        """Move the head to *new_sha*, committed at *at*; checks restart."""
        self.head_sha = new_sha
        self.commits.append((new_sha, iso(at)))
        self.checks = checks if checks is not None else []

    def comment(self, body: str, at: float, login: str = "factory-bot") -> None:
        """Add an issue comment."""
        n = len(self.comments) + 1
        self.comments.append(
            {
                "body": body,
                "createdAt": iso(at),
                "url": f"{self.url}#issuecomment-{n}",
                "author": {"login": login},
            }
        )

    def review(self, body: str, at: float, state: str = "COMMENTED", login: str = "luka") -> None:
        """Add a PR review."""
        n = len(self.reviews) + 1
        self.reviews.append(
            {
                "body": body,
                "submittedAt": iso(at),
                "state": state,
                "url": f"{self.url}#pullrequestreview-{n}",
                "author": {"login": login},
                "commit": {"oid": self.head_sha},
            }
        )

    def rest(self) -> dict[str, Any]:
        """:returns: The ``GET /repos/{repo}/pulls`` item."""
        return {
            "number": self.number,
            "html_url": self.url,
            "title": self.title,
            "state": "open" if self.state == "OPEN" else "closed",
            "draft": self.is_draft,
            "created_at": iso(self.created_at),
            "merged_at": iso(self.merged_at) if self.merged_at else None,
            "head": {"ref": self.head_ref, "sha": self.head_sha},
            "base": {"ref": self.base_ref},
        }

    def graphql(self) -> dict[str, Any]:
        """:returns: The aliased ``pullRequest`` node of the refresh query."""
        commits = self.commits or [(self.head_sha, iso(self.created_at))]
        return {
            "number": self.number,
            "url": self.url,
            "title": self.title,
            "state": self.state,
            "isDraft": self.is_draft,
            "createdAt": iso(self.created_at),
            "headRefOid": self.head_sha,
            "headRefName": self.head_ref,
            "baseRefName": self.base_ref,
            "mergeable": self.mergeable,
            "mergedAt": iso(self.merged_at) if self.merged_at else None,
            "closedAt": iso(self.closed_at) if self.closed_at else None,
            "mergeCommit": {"oid": self.merge_sha} if self.merge_sha else None,
            "mergedBy": {"login": self.merged_by} if self.merged_by else None,
            "additions": 12,
            "deletions": 3,
            "changedFiles": len(self.files),
            "labels": {"nodes": [{"name": name} for name in self.labels]},
            "files": {"nodes": [{"path": path} for path in self.files]},
            "commits": {
                "nodes": [{"commit": {"oid": oid, "committedDate": at}} for oid, at in commits]
            },
            "head": {
                "nodes": [
                    {
                        "commit": {
                            "oid": self.head_sha,
                            "statusCheckRollup": (
                                {"contexts": {"nodes": [c.node() for c in self.checks]}}
                                if self.checks
                                else None
                            ),
                        }
                    }
                ]
            },
            "comments": {"nodes": list(self.comments)},
            "reviews": {"nodes": list(self.reviews)},
            "closedEvents": {
                "nodes": (
                    [{"actor": {"login": self.closed_by}, "createdAt": iso(self.closed_at)}]
                    if self.closed_by and self.closed_at
                    else []
                )
            },
        }


@dataclass
class FakeGitHub:
    """In-memory GitHub; :attr:`requests` records every call."""

    prs: list[FakePr] = field(default_factory=list)
    rules: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)
    classic: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    remaining: int = 4800
    fail_status: int | None = None
    retry_after: int | None = None
    requests: list[httpx.Request] = field(default_factory=list)

    def add(self, pr: FakePr) -> FakePr:
        """Publish *pr* and return it."""
        self.prs.append(pr)
        return pr

    def client(self) -> GitHubAppClient:
        """:returns: A client whose transport is this fake."""
        return GitHubAppClient(make_config(), transport=httpx.MockTransport(self.handler))

    @property
    def graphql_calls(self) -> list[httpx.Request]:
        """:returns: The GraphQL requests made so far."""
        return [r for r in self.requests if r.url.path == "/graphql"]

    def handler(self, request: httpx.Request) -> httpx.Response:
        """Serve one request; fail the test on anything that could write."""
        self.requests.append(request)
        if request.method == "POST":
            if request.url.path != "/graphql":
                pytest.fail(f"GitHub write attempted: POST {request.url}")
            query = json.loads(request.content)["query"]
            if not query.lstrip().startswith("query") or "mutation" in query:
                pytest.fail("GraphQL mutation attempted")
        elif request.method != "GET":
            pytest.fail(f"GitHub write attempted: {request.method} {request.url}")
        if self.fail_status is not None:
            headers = {"retry-after": str(self.retry_after)} if self.retry_after else {}
            return httpx.Response(self.fail_status, json={"message": "nope"}, headers=headers)
        path = request.url.path
        if path == "/graphql":
            return self._graphql(json.loads(request.content))
        match = re.fullmatch(r"/repos/([^/]+/[^/]+)/pulls", path)
        if match:
            return self._pulls(match.group(1), request.url.params)
        match = re.fullmatch(r"/repos/([^/]+/[^/]+)/rules/branches/(.+)", path)
        if match:
            rules = self.rules.get((match.group(1), match.group(2)), [])
            page = int(request.url.params.get("page", "1"))
            per_page = int(request.url.params.get("per_page", "100"))
            return httpx.Response(200, json=rules[(page - 1) * per_page : page * per_page])
        match = re.fullmatch(r"/repos/([^/]+/[^/]+)/branches/(.+)/protection", path)
        if match:
            classic = self.classic.get((match.group(1), match.group(2)))
            if classic is None:
                return httpx.Response(404, json={"message": "Branch not protected"})
            return httpx.Response(200, json=classic)
        return httpx.Response(404, json={"message": "Not Found"})

    def _pulls(self, repo: str, params: httpx.QueryParams) -> httpx.Response:
        state = params.get("state", "open")
        page = int(params.get("page", "1"))
        per_page = int(params.get("per_page", "100"))
        pulls = [
            p
            for p in self.prs
            if p.repo == repo and (state == "all" or (state == "open") == (p.state == "OPEN"))
        ]
        pulls.sort(key=lambda p: p.created_at, reverse=True)
        chunk = pulls[(page - 1) * per_page : page * per_page]
        return httpx.Response(200, json=[p.rest() for p in chunk])

    def _graphql(self, body: dict[str, Any]) -> httpx.Response:
        variables = body.get("variables") or {}
        repo = f"{variables.get('owner')}/{variables.get('name')}"
        repository: dict[str, Any] = {}
        errors: list[dict[str, Any]] = []
        for alias, number in _ALIAS_RE.findall(body["query"]):
            pr = next((p for p in self.prs if p.repo == repo and p.number == int(number)), None)
            repository[alias] = pr.graphql() if pr is not None else None
            if pr is None:
                errors.append({"type": "NOT_FOUND", "path": ["repository", alias]})
        payload: dict[str, Any] = {
            "data": {
                "rateLimit": {"remaining": self.remaining, "resetAt": "2026-10-26T12:00:00Z"},
                "repository": repository,
            }
        }
        if errors:
            payload["errors"] = errors
        return httpx.Response(200, json=payload)


@dataclass
class FakeConnection:
    """What :func:`resolve_access_token` reads from the connection store."""

    access_token: str | None = "ghu_factory_test"
    token_expires_at: int | None = None
    refresh_token: str | None = None
    github_login: str = "luka"


@dataclass
class FakeGithubStore:
    """A connection store holding one user's connection (or none)."""

    connections: dict[str, FakeConnection] = field(default_factory=dict)

    def get(self, user_id: str, *, with_tokens: bool = False) -> FakeConnection | None:
        return self.connections.get(user_id)
