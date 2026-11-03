"""Shared setup for the source gateway tests: sample sources, fake GitHub, fake search, the web.

Every product word, repo, host and text here is sample data. The factory is
the discovery cockpit's (Atlas ``lead_a`` is the root, Sentinel ``lead_b``);
the sample sources let both the discovery seat and the leads read them, and
the backends the gateway reaches are fakes on ``app.state``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from omnigent import factory as F
from omnigent.server.auth import LEVEL_OWNER
from omnigent.server.factory import definition
from omnigent.server.factory.discovery.kinds import BACKENDS_ATTR
from omnigent.server.factory.discovery.kinds.base import Backends, SourceFailed
from omnigent.server.factory.discovery.web_io import Hit, WebFetcher
from tests.server.factory.cockpit_helpers import Cockpit

# Outside the projects of the factory fixtures (their repo is sample-org/sample-app).
SAMPLE_REPO = "sample-org/sample-upstream"
OTHER_REPO = "sample-org/sample-docs"
REPO_SOURCE: dict[str, Any] = {
    "id": "repo-issues",
    "label": "Repo issues (sample)",
    "kind": "repo",
    "params": {"repos": [SAMPLE_REPO], "since_days": 90},
    "roles": ["scout", "lead"],
    "budget": {"calls_per_day": 50},
    "interval_min": 60,
    "state": "active",
}
WEB_SOURCE: dict[str, Any] = {
    "id": "web",
    "label": "Web and market (sample)",
    "kind": "web",
    "params": {"urls": ["https://changelog.example.com/releases"], "changelogs": False},
    "roles": ["scout", "lead"],
    "budget": {"calls_per_day": 30},
    "interval_min": 1440,
    "state": "active",
}
TEAM_SOURCE: dict[str, Any] = {
    "id": "team",
    "label": "The team (sample)",
    "kind": "team",
    "roles": ["scout", "lead"],
    "interval_min": 0,
    "state": "active",
}
PUBLIC_IP = "93.184.216.34"
"""(sample) A public address every fake host resolves to."""
NOW = "2026-11-02T10:00:00Z"


def set_sources(cockpit: Cockpit, *items: Mapping[str, Any]) -> None:
    """Replace the factory's sources with *items* (as an owner's seed would)."""
    definition.set_values(
        cockpit.deps,
        cockpit.fid,
        {"discovery.sources": [dict(i) for i in items]},
        created_by="alice",
    )
    definition.invalidate(cockpit.fid)


def issue(
    number: int,
    title: str,
    body: str = "",
    *,
    login: str = "sample-user",
    association: str = "NONE",
    updated: str = NOW,
    labels: tuple[str, ...] = (),
    bot: bool = False,
    repo: str = SAMPLE_REPO,
) -> dict[str, Any]:
    """A GitHub issue node as the GraphQL API returns it (sample)."""
    return {
        "number": number,
        "title": title,
        "bodyText": body,
        "url": f"https://github.com/{repo}/issues/{number}",
        "createdAt": updated,
        "updatedAt": updated,
        "authorAssociation": association,
        "author": {"login": login, "__typename": "Bot" if bot else "User"},
        "labels": {"nodes": [{"name": n} for n in labels]},
        "repository": {"nameWithOwner": repo},
        "__typename": "Issue",
    }


def discussion(
    number: int, title: str, body: str = "", *, association: str = "NONE", updated: str = NOW
) -> dict[str, Any]:
    """A GitHub discussion node (sample)."""
    return {
        "number": number,
        "title": title,
        "bodyText": body,
        "url": f"https://github.com/{SAMPLE_REPO}/discussions/{number}",
        "createdAt": updated,
        "updatedAt": updated,
        "authorAssociation": association,
        "author": {"login": f"reader-{number}", "__typename": "User"},
        "repository": {"nameWithOwner": SAMPLE_REPO},
        "__typename": "Discussion",
    }


def release(tag: str, notes: str, *, created: str = NOW) -> dict[str, Any]:
    """A GitHub release node (sample)."""
    return {
        "name": f"Release {tag} (sample)",
        "tagName": tag,
        "description": notes,
        "url": f"https://github.com/{SAMPLE_REPO}/releases/tag/{tag}",
        "createdAt": created,
        "publishedAt": created,
    }


@dataclass
class FakeGitHub:
    """Answers the repo kind's read-only GraphQL documents from sample nodes."""

    repos: dict[str, dict[str, list[dict[str, Any]]]] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    fail: str | None = None

    async def graphql(
        self, factory_id: str, query: str, variables: Mapping[str, Any]
    ) -> dict[str, Any]:
        self.calls.append((query, dict(variables)))
        if self.fail is not None:
            raise SourceFailed(self.fail)
        if "search(" in query:
            nodes = [
                n
                for repo in self.repos.values()
                for n in repo.get("issues", []) + repo.get("discussions", [])
            ]
            return {"search": {"nodes": nodes}}
        repo = f"{variables['owner']}/{variables['name']}".lower()
        found = self.repos.get(repo)
        if found is None:
            return {"repository": None}
        out: dict[str, Any] = {
            "nameWithOwner": repo,
            "hasIssuesEnabled": True,
            "hasDiscussionsEnabled": True,
            "issues": {"nodes": found.get("issues", []), "totalCount": 3},
        }
        if variables.get("discussions", True):
            out["discussions"] = {"nodes": found.get("discussions", [])}
        if variables.get("releases", True):
            out["releases"] = {"nodes": found.get("releases", [])}
        return {"repository": out}


@dataclass
class FakeSearch:
    """A keyless search backend that answers sample hits."""

    hits: list[Hit] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    name: str = "sample-search"

    async def search(self, query: str, limit: int) -> list[Hit]:
        self.queries.append(query)
        return self.hits[:limit]


Pages = dict[str, Callable[[httpx.Request], httpx.Response]]


def web_fetcher(
    pages: Pages, *, addresses: Mapping[str, list[str]] | None = None, **kwargs: Any
) -> WebFetcher:
    """
    A real :class:`WebFetcher` over a mock network: *pages* maps a ``Host``
    header to a handler; every host resolves to :data:`PUBLIC_IP` unless
    *addresses* says otherwise.
    """

    async def resolve(host: str, _port: int) -> list[str]:
        return list((addresses or {}).get(host, [PUBLIC_IP]))

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.headers.get("host", "")
        page = pages.get(host)
        if page is None:
            return httpx.Response(404, text="no such sample page")
        return page(request)

    return WebFetcher(resolver=resolve, transport=httpx.MockTransport(handler), **kwargs)


def html(title: str, *paragraphs: str) -> Callable[[httpx.Request], httpx.Response]:
    """:returns: A handler that serves a sample HTML page."""
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    page = (
        f"<html><head><title>{title}</title><script>var secret = 1;</script></head>"
        f"<body>{body}</body></html>"
    )
    return lambda _request: httpx.Response(
        200, text=page, headers={"content-type": "text/html; charset=utf-8"}
    )


def install_backends(
    cockpit: Cockpit,
    *,
    github: FakeGitHub | None = None,
    search: FakeSearch | None = None,
    fetcher: WebFetcher | None = None,
) -> Backends:
    """Put fake backends on the app (the gateway reads them from ``app.state``)."""
    found = Backends(github=github or FakeGitHub(), search=search, fetcher=fetcher)
    setattr(cockpit.app.state, BACKENDS_ATTR, found)
    return found


async def query(
    cockpit: Cockpit,
    source: str,
    tool: str,
    args: Mapping[str, Any] | None = None,
    *,
    key: str = "lead_a",
    session_id: str | None = None,
    **extra: Any,
) -> httpx.Response:
    """``sys_discovery_query`` from member *key* (Atlas by default)."""
    return await cockpit.client.post(
        "/v1/factory/agent/discovery/query",
        json={
            "from_session_id": session_id or cockpit.world.id(key),
            "source": source,
            "tool": tool,
            "args": dict(args or {}),
            **extra,
        },
        headers={"X-Test-User": "alice"},
    )


def add_scout(cockpit: Cockpit, monkeypatch: Any) -> str:
    """
    A discovery seat in the factory (the role the Scout bundle brings): a
    top-level member labelled ``scout``. :returns: Its session id.
    """
    from omnigent.server.factory import membership

    monkeypatch.setattr(membership, "ROLES", frozenset({*F.ROLES, "scout"}))
    conv = cockpit.world.make_session(
        "scout",
        agent="lead",
        title="Scout (sample)",
        labels={
            F.LABEL_ID: cockpit.fid,
            F.LABEL_ROLE: "scout",
            F.LABEL_PARENT: cockpit.fid,
            F.LABEL_STATE: "active",
        },
    )
    cockpit.permissions.grant("alice", conv.id, LEVEL_OWNER)
    return conv.id


def source_events(cockpit: Cockpit, kind: str = "source.query") -> list[Any]:
    """:returns: The factory's events of *kind*, oldest first."""
    return cockpit.events(kind)


__all__ = [
    "NOW",
    "OTHER_REPO",
    "PUBLIC_IP",
    "REPO_SOURCE",
    "SAMPLE_REPO",
    "TEAM_SOURCE",
    "WEB_SOURCE",
    "FakeGitHub",
    "FakeSearch",
    "add_scout",
    "discussion",
    "html",
    "install_backends",
    "issue",
    "query",
    "release",
    "set_sources",
    "source_events",
    "web_fetcher",
]
