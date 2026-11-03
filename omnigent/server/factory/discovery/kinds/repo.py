"""The ``repo`` kind: issues, discussions and releases of the factory's repos, read on the server.

The server reads GitHub with the factory owner's GitHub connection, as the PR
poller does (``background/pr_poller.py``); no token ever reaches an agent.
Every request is one of the read-only GraphQL documents below (the client
refuses a mutation), and the only parts an agent chooses are a repo among the
source's own, a page size and search words (qualifiers such as ``repo:`` are
stripped, so a search never leaves the source's repos).

``params``: ``repos`` (``owner/name``; empty means the repos of the factory's
projects), ``since_days`` (90), ``include`` (``issues``, ``discussions``,
``releases``), ``per_sync`` (50). An item whose author GitHub reports as
``OWNER``, ``MEMBER`` or ``COLLABORATOR`` is the team's, and the ledger
stores it as a hunch; so are releases of the factory's own repos. Bots are
skipped.
"""

from __future__ import annotations

import asyncio
import calendar
import json
import re
import time
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal, Protocol

import httpx
from pydantic import Field, field_validator

from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.discovery.kinds.base import (
    Args,
    Check,
    Context,
    Fetched,
    Kind,
    Params,
    Source,
    SourceFailed,
    ToolSpec,
    clip_text,
    unique,
)
from omnigent.server.factory.discovery.ledger import Item

if TYPE_CHECKING:
    from omnigent.server.factory.context import FactoryDeps

REPO_RE: Final = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
REPOS_MAX: Final = 10
TEAM_ASSOCIATIONS: Final = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
"""GitHub's ``author_association`` values of the repo's own people."""
Part = Literal["issues", "discussions", "releases"]
INCLUDE: Final[tuple[Part, ...]] = ("issues", "discussions", "releases")
SEARCH_Q_MAX: Final = 200
_BUG_RE: Final = re.compile(r"\b(bug|defect|regression|crash)\b", re.IGNORECASE)
_REQUEST_RE: Final = re.compile(
    r"\b(enhancement|feature|request|idea|proposal|suggestion)\b", re.IGNORECASE
)
_QUALIFIER_RE: Final = re.compile(r"^-?[A-Za-z_][\w.-]*:")
_OPERATORS: Final = frozenset({"and", "or", "not"})
"""Search operators (any case) dropped from search words, with parentheses."""

_ISSUE_FIELDS: Final = (
    "number title bodyText url createdAt updatedAt authorAssociation "
    "author { login __typename } labels(first: 10) { nodes { name } }"
)
_DISCUSSION_FIELDS: Final = (
    "number title bodyText url createdAt updatedAt authorAssociation author { login __typename }"
)
_RELEASE_FIELDS: Final = "name tagName description url createdAt publishedAt"

SYNC_QUERY: Final = (
    "query($owner: String!, $name: String!, $n: Int!, $since: DateTime, "
    "$issues: Boolean!, $discussions: Boolean!, $releases: Boolean!) {"
    " repository(owner: $owner, name: $name) { nameWithOwner"
    " issues(first: $n, orderBy: {field: UPDATED_AT, direction: ASC}, filterBy: {since: $since})"
    f" @include(if: $issues) {{ nodes {{ {_ISSUE_FIELDS} }} }}"
    " discussions(first: $n, orderBy: {field: UPDATED_AT, direction: DESC})"
    f" @include(if: $discussions) {{ nodes {{ {_DISCUSSION_FIELDS} }} }}"
    " releases(first: 10, orderBy: {field: CREATED_AT, direction: DESC})"
    f" @include(if: $releases) {{ nodes {{ {_RELEASE_FIELDS} }} }}"
    " } }"
)
"""One request per repo per sync: issues since the cursor (oldest first), discussions, releases."""
ISSUES_QUERY: Final = (
    "query($owner: String!, $name: String!, $n: Int!, $since: DateTime,"
    " $states: [IssueState!]) {"
    " repository(owner: $owner, name: $name) { nameWithOwner"
    " issues(first: $n, orderBy: {field: UPDATED_AT, direction: DESC},"
    f" filterBy: {{since: $since, states: $states}}) {{ nodes {{ {_ISSUE_FIELDS} }} }}"
    " } }"
)
DISCUSSIONS_QUERY: Final = (
    "query($owner: String!, $name: String!, $n: Int!) {"
    " repository(owner: $owner, name: $name) { nameWithOwner"
    " discussions(first: $n, orderBy: {field: UPDATED_AT, direction: DESC})"
    f" {{ nodes {{ {_DISCUSSION_FIELDS} }} }}"
    " } }"
)
RELEASES_QUERY: Final = (
    "query($owner: String!, $name: String!, $n: Int!) {"
    " repository(owner: $owner, name: $name) { nameWithOwner"
    " releases(first: $n, orderBy: {field: CREATED_AT, direction: DESC})"
    f" {{ nodes {{ {_RELEASE_FIELDS} }} }}"
    " } }"
)
SEARCH_QUERY: Final = (
    "query($q: String!, $n: Int!, $type: SearchType!) {"
    " search(query: $q, type: $type, first: $n) { nodes { __typename"
    f" ... on Issue {{ {_ISSUE_FIELDS} repository {{ nameWithOwner }} }}"
    f" ... on Discussion {{ {_DISCUSSION_FIELDS} repository {{ nameWithOwner }} }}"
    " } } }"
)
TEST_QUERY: Final = (
    "query($owner: String!, $name: String!) {"
    " repository(owner: $owner, name: $name) { nameWithOwner hasIssuesEnabled"
    " hasDiscussionsEnabled issues(states: OPEN) { totalCount } } }"
)


# ── Reading GitHub on the server ───────────────────────────────


class GitHubReader(Protocol):
    """Runs one read-only GraphQL document as the factory's owner."""

    async def graphql(
        self, factory_id: str, query: str, variables: Mapping[str, Any]
    ) -> dict[str, Any]:
        """:raises SourceFailed: When GitHub cannot be read for the factory."""
        ...


class AppGitHubReader:
    """The server's GitHub App client with the factory owner's connection (like the PR poller)."""

    def __init__(self, app_state: Any) -> None:
        self._app_state = app_state

    async def graphql(
        self, factory_id: str, query: str, variables: Mapping[str, Any]
    ) -> dict[str, Any]:
        from omnigent.server.factory.context import factory_deps
        from omnigent.server.factory.prs import factory_owner
        from omnigent.server.github_app_client import GitHubApiError
        from omnigent.server.github_identity import resolve_access_token

        client = getattr(self._app_state, "github_client", None)
        store = getattr(self._app_state, "github_store", None)
        if client is None or store is None:
            raise SourceFailed("this server has no GitHub App configured")
        deps = factory_deps(self._app_state)
        owner = await asyncio.to_thread(factory_owner, deps, factory_id)
        token = await resolve_access_token(owner, store=store, client=client) if owner else None
        if token is None:
            raise SourceFailed("GitHub is not connected for the factory's owner")
        try:
            result = await client.graphql(token, query, dict(variables), allow_partial=True)
        except (GitHubApiError, httpx.HTTPError, ValueError) as exc:
            raise SourceFailed(f"GitHub: {exc}"[:300]) from None
        return dict(result.data)


def own_repos(deps: FactoryDeps, factory_id: str) -> list[str]:
    """
    :returns: The repos of the factory's projects (and the root's launch
        workspaces), lowercase ``owner/name``.
    """
    from omnigent.server.factory.agent_scope import repo_from_workspace
    from omnigent.server.managed_hosts import read_managed_repo_workspaces

    repos = [p.repo.lower() for p in deps.factory_store.list_projects(factory_id) if p.repo]
    root = deps.conversation_store.get_conversation(factory_id)
    if root is not None:
        for workspace in read_managed_repo_workspaces(root.labels):
            repo = repo_from_workspace(workspace)
            if repo is not None:
                repos.append(repo)
    return unique(repos)


# ── Params and arguments ───────────────────────────────────────


class RepoParams(Params):
    repos: list[str] = Field(default_factory=list, max_length=REPOS_MAX)
    since_days: int = Field(90, ge=1, le=365)
    include: list[Part] = Field(default_factory=lambda: list(INCLUDE))
    per_sync: int = Field(50, ge=1, le=100)

    @field_validator("repos")
    @classmethod
    def _repos(cls, repos: list[str]) -> list[str]:
        for repo in repos:
            if not REPO_RE.match(repo):
                raise ValueError(f"{repo!r} is not owner/name")
        return unique([r.lower() for r in repos])


class IssuesArgs(Args):
    repo: str | None = Field(None, max_length=201)
    since_days: int | None = Field(None, ge=1, le=365)
    state: Literal["open", "closed", "all"] = "all"
    limit: int = Field(20, ge=1, le=50)


class DiscussionsArgs(Args):
    repo: str | None = Field(None, max_length=201)
    limit: int = Field(10, ge=1, le=30)


class ReleasesArgs(Args):
    repo: str | None = Field(None, max_length=201)
    limit: int = Field(5, ge=1, le=20)


class SearchArgs(Args):
    q: str = Field(min_length=1, max_length=SEARCH_Q_MAX)
    in_: Literal["issues", "discussions"] = Field("issues", alias="in")
    repo: str | None = Field(None, max_length=201)
    limit: int = Field(10, ge=1, le=20)


def search_words(q: str) -> str:
    """
    :returns: *q* as plain words: no qualifiers (``repo:``, ``org:``, ``is:`` …),
        no parentheses, no ``AND``/``OR``/``NOT``, no control characters.
    """
    flat = "".join(ch for ch in q if ch.isprintable() and ch not in "()")
    words = [w for w in flat.split() if not _QUALIFIER_RE.match(w) and w.lower() not in _OPERATORS]
    return " ".join(words)[:SEARCH_Q_MAX]


# ── Items ──────────────────────────────────────────────────────


def _epoch(value: Any) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return calendar.timegm(time.strptime(value[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def _iso(epoch: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _label_kind(node: Mapping[str, Any]) -> str | None:
    labels = (node.get("labels") or {}).get("nodes") or []
    names = " ".join(str(n.get("name") or "") for n in labels if isinstance(n, Mapping))
    if _BUG_RE.search(names):
        return "bug"
    if _REQUEST_RE.search(names):
        return "request"
    return None


def _nodes(found: Any) -> list[Mapping[str, Any]]:
    if not isinstance(found, Mapping):
        return []
    return [n for n in found.get("nodes") or [] if isinstance(n, Mapping)]


def post_item(node: Mapping[str, Any], repo: str, kind_of: str) -> Item | None:
    """:returns: An issue or discussion as a ledger item; ``None`` for a bot's."""
    found = node.get("author")
    author: Mapping[str, Any] = found if isinstance(found, Mapping) else {}
    login = str(author.get("login") or "")
    if author.get("__typename") == "Bot" or login.endswith("[bot]"):
        return None
    url = str(node.get("url") or "")
    if not url:
        return None
    return Item(
        text=clip_text(str(node.get("title") or ""), str(node.get("bodyText") or "")),
        pointer=url,
        observed_at=_epoch(node.get("createdAt")),
        account=f"github:{login.lower()}" if login else None,
        kind=_label_kind(node) if kind_of == "issue" else None,
        team_author=str(node.get("authorAssociation") or "") in TEAM_ASSOCIATIONS,
        meta={"repo": repo, "type": kind_of, "number": node.get("number")},
    )


def release_item(node: Mapping[str, Any], repo: str, *, own: bool) -> Item | None:
    """:returns: A release as a ``market`` item (the team's own when the repo is the factory's)."""
    url = str(node.get("url") or "")
    if not url:
        return None
    return Item(
        text=clip_text(
            str(node.get("name") or node.get("tagName") or ""), str(node.get("description") or "")
        ),
        pointer=url,
        observed_at=_epoch(node.get("publishedAt") or node.get("createdAt")),
        kind="market",
        team_author=own,
        meta={"repo": repo, "type": "release", "tag": node.get("tagName")},
    )


# ── The kind ───────────────────────────────────────────────────


class RepoKind(Kind):
    name: ClassVar[str] = "repo"
    params_model: ClassVar[type[Params]] = RepoParams
    tools: ClassVar[Mapping[str, ToolSpec]] = {
        "issues": ToolSpec(
            "issues", IssuesArgs, "recent issues of the source's repos (newest change first)"
        ),
        "discussions": ToolSpec("discussions", DiscussionsArgs, "recent discussions"),
        "releases": ToolSpec("releases", ReleasesArgs, "recent releases and their notes"),
        "search": ToolSpec(
            "search", SearchArgs, "search words in issues or discussions of the source's repos"
        ),
    }

    def allowed(self, source: Source, backends: Any) -> list[str]:  # noqa: ARG002
        params = self.params(source)
        assert isinstance(params, RepoParams)
        names: list[str] = [n for n in INCLUDE if n in params.include]
        if "issues" in names or "discussions" in names:
            names.append("search")
        return [n for n in names if params.tools is None or n in params.tools]

    def repos(self, deps: FactoryDeps, factory_id: str, source: Source) -> list[str]:
        """:returns: The source's repos: its ``params.repos``, else the factory's own."""
        params = self.params(source)
        assert isinstance(params, RepoParams)
        return params.repos or own_repos(deps, factory_id)[:REPOS_MAX]

    def pick(
        self, deps: FactoryDeps, factory_id: str, source: Source, wanted: str | None
    ) -> list[str]:
        """
        :returns: The repos a read covers: *wanted* (one of the source's), else all of them.
        :raises CockpitError: 409 ``source_invalid`` (no repos), 403 ``repo_not_allowed``.
        """
        repos = self.repos(deps, factory_id, source)
        if not repos:
            raise CockpitError(
                409,
                "source_invalid",
                f"source {source.id} has no repos: list them in params.repos, or give the "
                "factory a project with a repo",
            )
        if wanted is None:
            return repos
        if wanted.lower() not in repos:
            raise CockpitError(
                403,
                "repo_not_allowed",
                f"{wanted} is not a repo of source {source.id} ({', '.join(repos)})",
                field="args.repo",
            )
        return [wanted.lower()]

    def check(
        self,
        deps: FactoryDeps,
        factory_id: str,
        source: Source,
        tool: str,  # noqa: ARG002
        args: Args,
    ) -> None:
        self.pick(deps, factory_id, source, getattr(args, "repo", None))

    def _pick(self, ctx: Context, wanted: str | None) -> list[str]:
        return self.pick(ctx.deps, ctx.factory_id, ctx.source, wanted)

    async def _read(self, ctx: Context, query: str, variables: Mapping[str, Any]) -> Any:
        if ctx.backends.github is None:
            raise SourceFailed("this server cannot read GitHub")
        return await ctx.backends.github.graphql(ctx.factory_id, query, variables)

    async def _each(
        self, ctx: Context, repos: Sequence[str], query: str, extra: Mapping[str, Any]
    ) -> list[tuple[str, Mapping[str, Any]]]:
        """Run *query* per repo (each after the first is counted); a missing repo fails."""
        out: list[tuple[str, Mapping[str, Any]]] = []
        for i, repo in enumerate(repos):
            if i:
                await ctx.charge()
            owner, name = repo.split("/", 1)
            data = await self._read(ctx, query, {"owner": owner, "name": name, **extra})
            found = data.get("repository") if isinstance(data, Mapping) else None
            if not isinstance(found, Mapping):
                raise SourceFailed(f"{repo} cannot be read with the factory owner's GitHub access")
            out.append((repo, found))
        return out

    async def query(self, ctx: Context, tool: str, args: Args) -> Fetched:  # noqa: ARG002
        own = set(own_repos(ctx.deps, ctx.factory_id))
        out = Fetched()
        if isinstance(args, SearchArgs):
            repos = self._pick(ctx, args.repo)
            words = search_words(args.q)
            if not words:
                raise CockpitError(
                    400, "invalid_args", "search: q has no words once qualifiers are removed"
                )
            scope = " ".join(f"repo:{r}" for r in repos)
            q = f"{scope} is:issue {words}" if args.in_ == "issues" else f"{scope} {words}"
            out.q = words
            data = await self._read(
                ctx,
                SEARCH_QUERY,
                {
                    "q": q,
                    "n": args.limit,
                    "type": "ISSUE" if args.in_ == "issues" else "DISCUSSION",
                },
            )
            # Only nodes of the source's repos count, so not even a count leaves them.
            for node in _nodes((data or {}).get("search")):
                repo = str((node.get("repository") or {}).get("nameWithOwner") or "").lower()
                if repo not in repos:
                    continue
                out.returned += 1
                kind_of = "issue" if node.get("__typename") == "Issue" else "discussion"
                item = post_item(node, repo, kind_of)
                if item is not None:
                    out.items.append(item)
            return out
        repos = self._pick(ctx, getattr(args, "repo", None))
        if isinstance(args, IssuesArgs):
            params = self.params(ctx.source)
            assert isinstance(params, RepoParams)
            days = args.since_days or params.since_days
            states = {"open": ["OPEN"], "closed": ["CLOSED"], "all": None}[args.state]
            extra = {"n": args.limit, "since": _iso(ctx.now - days * 86400), "states": states}
            for repo, found in await self._each(ctx, repos, ISSUES_QUERY, extra):
                nodes = _nodes(found.get("issues"))
                out.returned += len(nodes)
                out.items += [i for n in nodes if (i := post_item(n, repo, "issue")) is not None]
        elif isinstance(args, DiscussionsArgs):
            for repo, found in await self._each(ctx, repos, DISCUSSIONS_QUERY, {"n": args.limit}):
                nodes = _nodes(found.get("discussions"))
                out.returned += len(nodes)
                out.items += [
                    i for n in nodes if (i := post_item(n, repo, "discussion")) is not None
                ]
        elif isinstance(args, ReleasesArgs):
            for repo, found in await self._each(ctx, repos, RELEASES_QUERY, {"n": args.limit}):
                nodes = _nodes(found.get("releases"))
                out.returned += len(nodes)
                out.items += [
                    i for n in nodes if (i := release_item(n, repo, own=repo in own)) is not None
                ]
        return out

    async def sync(self, ctx: Context) -> Fetched:
        params = self.params(ctx.source)
        assert isinstance(params, RepoParams)
        repos = self._pick(ctx, None)
        own = set(own_repos(ctx.deps, ctx.factory_id))
        cursor = _load_cursor(ctx.state.cursor if ctx.state is not None else None)
        floor = ctx.now - params.since_days * 86400
        out = Fetched()
        for i, repo in enumerate(repos):
            if i:
                await ctx.charge()
            marks = dict(cursor.get(repo) or {})
            owner, name = repo.split("/", 1)
            try:
                data = await self._read(
                    ctx,
                    SYNC_QUERY,
                    {
                        "owner": owner,
                        "name": name,
                        "n": params.per_sync,
                        "since": _iso(max(floor, int(marks.get("issues") or 0))),
                        "issues": "issues" in params.include,
                        "discussions": "discussions" in params.include,
                        "releases": "releases" in params.include,
                    },
                )
            except SourceFailed as exc:
                out.errors.append(f"{repo}: {exc}")
                continue
            found = data.get("repository") if isinstance(data, Mapping) else None
            if not isinstance(found, Mapping):
                out.errors.append(f"{repo} cannot be read with the factory owner's GitHub access")
                continue
            for part, kind_of in (("issues", "issue"), ("discussions", "discussion")):
                nodes = _nodes(found.get(part))
                out.returned += len(nodes)
                since = max(floor, int(marks.get(part) or 0))
                newest = int(marks.get(part) or 0)
                for node in nodes:
                    changed = _epoch(node.get("updatedAt")) or 0
                    newest = max(newest, changed)
                    if part == "discussions" and changed <= since:
                        continue
                    item = post_item(node, repo, kind_of)
                    if item is not None:
                        out.items.append(item)
                if newest:
                    marks[part] = newest
            nodes = _nodes(found.get("releases"))
            out.returned += len(nodes)
            since = max(floor, int(marks.get("releases") or 0))
            newest = int(marks.get("releases") or 0)
            for node in nodes:
                made = _epoch(node.get("createdAt")) or 0
                newest = max(newest, made)
                if made <= since:
                    continue
                item = release_item(node, repo, own=repo in own)
                if item is not None:
                    out.items.append(item)
            if newest:
                marks["releases"] = newest
            cursor[repo] = marks
        if repos and len(out.errors) == len(repos):
            raise SourceFailed("; ".join(out.errors)[:300])
        out.cursor = json.dumps(cursor, separators=(",", ":"), sort_keys=True)
        return out

    async def test(self, ctx: Context) -> list[Check]:
        try:
            repos = self._pick(ctx, None)
        except CockpitError as exc:
            return [Check("repos", False, exc.message)]
        checks = [Check("repos", True, ", ".join(repos))]
        for i, repo in enumerate(repos):
            if i:
                await ctx.charge()
            owner, name = repo.split("/", 1)
            try:
                data = await self._read(ctx, TEST_QUERY, {"owner": owner, "name": name})
            except SourceFailed as exc:
                checks.append(Check(repo, False, str(exc)))
                continue
            found = data.get("repository") if isinstance(data, Mapping) else None
            if not isinstance(found, Mapping):
                checks.append(Check(repo, False, "not readable with the owner's GitHub access"))
                continue
            open_issues = (found.get("issues") or {}).get("totalCount")
            parts = [f"{open_issues} open issues" if open_issues is not None else "readable"]
            if not found.get("hasIssuesEnabled"):
                parts.append("issues are off")
            if not found.get("hasDiscussionsEnabled"):
                parts.append("discussions are off")
            checks.append(Check(repo, True, ", ".join(parts)))
        return checks


def _load_cursor(raw: str | None) -> dict[str, dict[str, int]]:
    try:
        found = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return {
        str(k): {str(p): int(t) for p, t in v.items() if isinstance(t, int)}
        for k, v in found.items()
        if isinstance(v, dict)
    }


__all__ = [
    "INCLUDE",
    "SEARCH_QUERY",
    "SYNC_QUERY",
    "TEAM_ASSOCIATIONS",
    "AppGitHubReader",
    "GitHubReader",
    "RepoKind",
    "RepoParams",
    "own_repos",
    "post_item",
    "release_item",
    "search_words",
]
