"""Async HTTP client for the GitHub App user + app flows.

The network half of the GitHub App integration. It sends the OAuth
token requests and reads the user endpoint, but never constructs
credentials itself: the App secrets and the form fields that carry them
are owned by :mod:`omnigent.server.github_app`
(:class:`~omnigent.server.github_app.GitHubAppConfig`), which this
module simply POSTs. Keeping the secret-owning code and the network
sink in separate modules is deliberate. See
``docs/GITHUB_APP_SETUP.md``.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

from omnigent.factory_gates import BranchRules, parse_time, status_contexts
from omnigent.server.github_app import (
    GitHubAppConfig,
    GitHubAppError,
    GitHubTokenSet,
    token_set_from_payload,
)

_TOKEN_ENDPOINT = "https://github.com/login/oauth/access_token"
_USER_ENDPOINT = "https://api.github.com/user"
# Repos the token can access (App-scoped), most-recently-pushed first.
_USER_REPOS_ENDPOINT = "https://api.github.com/user/repos"
_REPOS_PER_PAGE = 100
# Cap the walk so a user with thousands of repos gets a bounded, fast
# response for the picker (the newest ~300 by push time).
_REPOS_MAX_PAGES = 3

# A user's PUBLIC SSH keys (unauthenticated endpoint — only ever public keys),
# injected into managed sandboxes for VS Code Remote-SSH.
_USER_KEYS_ENDPOINT = "https://api.github.com/users/{login}/keys"

_logger = logging.getLogger(__name__)

_REPO_BRANCHES_ENDPOINT = "https://api.github.com/repos/{full_name}/branches"
_BRANCHES_PER_PAGE = 100
# Cap the branch walk the same way — a busy repo can have hundreds of
# branches, but the picker only needs a bounded, fast list.
_BRANCHES_MAX_PAGES = 3

_HTTP_TIMEOUT_S = 15.0

_GRAPHQL_ENDPOINT = "https://api.github.com/graphql"
_REPO_PULLS_ENDPOINT = "https://api.github.com/repos/{full_name}/pulls"
_BRANCH_RULES_ENDPOINT = "https://api.github.com/repos/{full_name}/rules/branches/{branch}"
_BRANCH_PROTECTION_ENDPOINT = (
    "https://api.github.com/repos/{full_name}/branches/{branch}/protection"
)
_PULL_FILES_ENDPOINT = "https://api.github.com/repos/{full_name}/pulls/{number}/files"
_RULES_PER_PAGE = 100
_RULES_MAX_PAGES = 5
# GitHub lists at most 3000 files of a PR.
_PULL_FILES_PER_PAGE = 100
_PULL_FILES_MAX_PAGES = 30
BRANCH_RULES_TTL_S = 3600.0
"""How long :meth:`GitHubAppClient.branch_rules` reuses a repo/branch's rules."""


@dataclass(frozen=True)
class RateLimit:
    """
    GitHub's remaining API budget.

    :param remaining: Requests (REST) or points (GraphQL) left in the window.
    :param reset_at: Epoch seconds when the window resets, when known.
    """

    remaining: int | None = None
    reset_at: int | None = None


class GitHubApiError(GitHubAppError):
    """
    A GitHub API call failed.

    :param status_code: The HTTP status (429 for a GraphQL ``RATE_LIMITED``
        error), or ``None`` for other GraphQL errors.
    :param rate_limit: The rate-limit headers of the failed response.
    :param retry_after: Seconds GitHub asked to wait (``Retry-After``), if any.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        rate_limit: RateLimit | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.rate_limit = rate_limit
        self.retry_after = retry_after


@dataclass(frozen=True)
class GraphQLResult:
    """
    A GraphQL response.

    :param data: The ``data`` object.
    :param rate_limit: From ``data.rateLimit`` when queried, else the headers.
    :param errors: Per-field errors kept by ``allow_partial=True``.
    """

    data: dict[str, Any]
    rate_limit: RateLimit | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)


# Strings, comments, braces and names of a GraphQL document, in order.
_GRAPHQL_TOKEN = re.compile(
    r'"""(?:[^"\\]|\\.|"(?!""))*"""'  # block string
    r'|"(?:[^"\\\n]|\\.)*"'  # string
    r"|#[^\n]*"  # comment
    r"|[{}()]|[$@]?[_A-Za-z]\w*"
)
_WRITE_OPERATIONS = frozenset({"mutation", "subscription"})


def graphql_operations(document: str) -> list[str]:
    """
    List a GraphQL document's top-level keywords (``query``, ``mutation``, ``fragment``…).

    :param document: e.g. ``"query { viewer { login } }"``.
    :returns: Names outside any selection set or argument list, e.g. ``["query"]``.
    """
    depth = 0
    names: list[str] = []
    for match in _GRAPHQL_TOKEN.finditer(document):
        token = match.group(0)
        if token in ("{", "("):
            depth += 1
        elif token in ("}", ")"):
            depth = max(0, depth - 1)
        elif depth == 0 and not token.startswith(('"', "#", "$", "@")):
            names.append(token)
    return names


def _int_header(headers: httpx.Headers, name: str) -> int | None:
    try:
        return int(headers[name])
    except (KeyError, ValueError):
        return None


def _header_rate_limit(resp: httpx.Response) -> RateLimit | None:
    remaining = _int_header(resp.headers, "x-ratelimit-remaining")
    reset_at = _int_header(resp.headers, "x-ratelimit-reset")
    if remaining is None and reset_at is None:
        return None
    return RateLimit(remaining=remaining, reset_at=reset_at)


def _api_error(what: str, resp: httpx.Response) -> GitHubApiError:
    retry_after = _int_header(resp.headers, "retry-after")
    return GitHubApiError(
        f"GitHub {what} returned {resp.status_code}",
        status_code=resp.status_code,
        rate_limit=_header_rate_limit(resp),
        retry_after=float(retry_after) if retry_after is not None else None,
    )


def _graphql_rate_limit(data: Mapping[str, Any], resp: httpx.Response) -> RateLimit | None:
    node = data.get("rateLimit")
    if not isinstance(node, Mapping):
        return _header_rate_limit(resp)
    remaining = node.get("remaining")
    reset = node.get("resetAt")
    reset_at = None
    if isinstance(reset, str) and reset:
        when = parse_time(reset)
        reset_at = int(when.timestamp()) if when is not None else None
    return RateLimit(
        remaining=remaining if isinstance(remaining, int) else None, reset_at=reset_at
    )


class GitHubAppClient:
    """Async HTTP client for the GitHub App user + app flows.

    Holds only the config and a branch-rules cache; every method opens its
    own short-lived :class:`httpx.AsyncClient` so the client is safe to build
    once and reuse across requests.
    """

    def __init__(
        self,
        config: GitHubAppConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        # Injectable transport for tests (httpx.MockTransport); None uses
        # the real network.
        self._transport = transport
        self._clock = clock
        # (repo, base) -> (expires at, rules); see branch_rules.
        self._rules_cache: dict[tuple[str, str], tuple[float, BranchRules]] = {}

    def _http_client(self) -> httpx.AsyncClient:
        """Open an AsyncClient, honoring an injected test transport."""
        return httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S, transport=self._transport)

    async def exchange_code(self, code: str) -> GitHubTokenSet:
        """Exchange an authorization ``code`` for a user access token.

        :param code: The ``code`` GitHub returned to the callback.
        :returns: The resulting token set.
        :raises GitHubAppError: When GitHub rejects the exchange.
        """
        return await self._token_request(self._config.code_exchange_fields(code))

    async def refresh_token(self, refresh_token: str) -> GitHubTokenSet:
        """Exchange a refresh token for a fresh user access token.

        :param refresh_token: The stored ``ghr_…`` refresh token.
        :returns: The refreshed token set.
        :raises GitHubAppError: When GitHub rejects the refresh.
        """
        return await self._token_request(self._config.token_refresh_fields(refresh_token))

    async def fetch_login(self, access_token: str) -> tuple[str, int]:
        """Fetch the authenticated user's ``(login, id)``.

        :param access_token: A valid user access token.
        :returns: The GitHub login and numeric user id.
        :raises GitHubAppError: When the API call fails.
        """
        async with self._http_client() as client:
            resp = await client.get(
                _USER_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                },
            )
        if resp.status_code != 200:
            raise GitHubAppError(f"GitHub /user returned {resp.status_code}")
        data = resp.json()
        login = data.get("login")
        user_id = data.get("id")
        if not login or user_id is None:
            raise GitHubAppError("GitHub /user response missing login/id")
        return str(login), int(user_id)

    async def list_repos(self, access_token: str) -> tuple[list[dict[str, object]], bool]:
        """List repos the authenticated user can access, App-scoped.

        Reads ``/user/repos`` most-recently-pushed first, following up to
        :data:`_REPOS_MAX_PAGES` pages. Returns a compact projection for the
        new-chat repo picker (not the full GitHub payload).

        :param access_token: A valid user access token.
        :returns: ``(repos, truncated)`` — repos as
            ``{full_name, clone_url, default_branch, private, pushed_at}`` newest
            first, and ``truncated=True`` when the page cap was hit and more
            repos almost certainly exist (so the UI can say the list is partial
            rather than silently dropping them).
        :raises GitHubAppError: When the API call fails.
        """
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
        }
        repos: list[dict[str, object]] = []
        truncated = False
        async with self._http_client() as client:
            for page in range(1, _REPOS_MAX_PAGES + 1):
                resp = await client.get(
                    _USER_REPOS_ENDPOINT,
                    params={"per_page": _REPOS_PER_PAGE, "page": page, "sort": "pushed"},
                    headers=headers,
                )
                if resp.status_code != 200:
                    raise GitHubAppError(f"GitHub /user/repos returned {resp.status_code}")
                batch = resp.json()
                if not isinstance(batch, list) or not batch:
                    break
                for entry in batch:
                    if not isinstance(entry, dict) or not entry.get("full_name"):
                        continue
                    repos.append(
                        {
                            "full_name": entry["full_name"],
                            "clone_url": entry.get("clone_url"),
                            "default_branch": entry.get("default_branch"),
                            "private": bool(entry.get("private")),
                            "pushed_at": entry.get("pushed_at"),
                        }
                    )
                if len(batch) < _REPOS_PER_PAGE:
                    break
            else:
                # Ran the full page cap without an early break → the last page
                # was full, so there are almost certainly more repos than shown.
                truncated = True
        return repos, truncated

    async def list_branches(self, access_token: str, full_name: str) -> list[str]:
        """List branch names for ``full_name`` (``owner/repo``), App-scoped.

        Reads ``/repos/{full_name}/branches`` following up to
        :data:`_BRANCHES_MAX_PAGES` pages, for the per-repo branch picker.

        :param access_token: A valid user access token.
        :param full_name: The repository's ``owner/name``.
        :returns: Branch names in the order GitHub returns them.
        :raises GitHubAppError: When the API call fails.
        """
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
        }
        url = _REPO_BRANCHES_ENDPOINT.format(full_name=full_name)
        branches: list[str] = []
        async with self._http_client() as client:
            for page in range(1, _BRANCHES_MAX_PAGES + 1):
                resp = await client.get(
                    url,
                    params={"per_page": _BRANCHES_PER_PAGE, "page": page},
                    headers=headers,
                )
                if resp.status_code != 200:
                    raise GitHubAppError(
                        f"GitHub /repos/{full_name}/branches returned {resp.status_code}"
                    )
                batch = resp.json()
                if not isinstance(batch, list) or not batch:
                    break
                for entry in batch:
                    if isinstance(entry, dict) and entry.get("name"):
                        branches.append(str(entry["name"]))
                if len(batch) < _BRANCHES_PER_PAGE:
                    break
        return branches

    async def graphql(
        self,
        access_token: str,
        query: str,
        variables: Mapping[str, Any] | None = None,
        *,
        allow_partial: bool = False,
    ) -> GraphQLResult:
        """Run a read-only GraphQL query.

        :param access_token: A valid user access token.
        :param query: The query document, e.g. ``"query { viewer { login } }"``.
        :param variables: Query variables.
        :param allow_partial: Return field errors alongside partial data
            instead of raising (e.g. one aliased PR that no longer exists).
        :returns: The data and the rate limit (from ``rateLimit`` when the
            query selects it, else the response headers).
        :raises GitHubApiError: On a non-200 status, a ``RATE_LIMITED`` error
            (``status_code=429``), or any error when not *allow_partial*.
        :raises ValueError: When *query* holds a mutation or subscription.
        """
        if _WRITE_OPERATIONS & set(graphql_operations(query)):
            # This client only reads GitHub; a write must never be sent.
            raise ValueError("GitHubAppClient.graphql only runs read-only queries")
        async with self._http_client() as client:
            resp = await client.post(
                _GRAPHQL_ENDPOINT,
                json={"query": query, "variables": dict(variables or {})},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                },
            )
        if resp.status_code != 200:
            raise _api_error("GraphQL", resp)
        payload = resp.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        raw_errors = payload.get("errors") if isinstance(payload, dict) else None
        errors = [e for e in raw_errors or [] if isinstance(e, dict)]
        if any(e.get("type") == "RATE_LIMITED" for e in errors):
            raise GitHubApiError(
                "GitHub GraphQL rate limited",
                status_code=429,
                rate_limit=_header_rate_limit(resp),
            )
        if not isinstance(data, dict) or (errors and not allow_partial):
            first = errors[0].get("message") if errors else "no data"
            raise GitHubApiError(f"GitHub GraphQL error: {first}")
        return GraphQLResult(data=data, rate_limit=_graphql_rate_limit(data, resp), errors=errors)

    async def list_pulls(
        self,
        access_token: str,
        repo: str,
        *,
        state: str = "open",
        sort: str = "created",
        direction: str = "desc",
        page: int = 1,
        per_page: int = 100,
    ) -> list[dict[str, Any]]:
        """List one page of a repo's pull requests (``GET /repos/{repo}/pulls``).

        :param access_token: A valid user access token.
        :param repo: ``owner/name``, e.g. ``"caffeinelabs/app"``.
        :param state: ``open``, ``closed`` or ``all``.
        :param sort: ``created``, ``updated``, ``popularity`` or ``long-running``.
        :param direction: ``asc`` or ``desc``.
        :param page: 1-based page number.
        :param per_page: Page size (GitHub caps it at 100).
        :returns: The page's PR objects as GitHub returns them (empty past the end).
        :raises GitHubApiError: When the API call fails.
        """
        async with self._http_client() as client:
            resp = await client.get(
                _REPO_PULLS_ENDPOINT.format(full_name=repo),
                params={
                    "state": state,
                    "sort": sort,
                    "direction": direction,
                    "page": page,
                    "per_page": per_page,
                },
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                },
            )
        if resp.status_code != 200:
            raise _api_error(f"/repos/{repo}/pulls", resp)
        batch = resp.json()
        return [pr for pr in batch if isinstance(pr, dict)] if isinstance(batch, list) else []

    async def list_pull_files(self, access_token: str, repo: str, number: int) -> list[str]:
        """List every path a PR touches, rename sources included.

        Pages ``GET /repos/{repo}/pulls/{number}/files`` (GitHub stops at 3000 files).

        :param access_token: A valid user access token.
        :param repo: ``owner/name``.
        :param number: The PR number.
        :returns: Paths in GitHub's order; a renamed file's old path follows its new one.
        :raises GitHubApiError: When the API call fails.
        """
        paths: list[str] = []
        async with self._http_client() as client:
            for page in range(1, _PULL_FILES_MAX_PAGES + 1):
                resp = await client.get(
                    _PULL_FILES_ENDPOINT.format(full_name=repo, number=int(number)),
                    params={"per_page": _PULL_FILES_PER_PAGE, "page": page},
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/vnd.github+json",
                    },
                )
                if resp.status_code != 200:
                    raise _api_error(f"/repos/{repo}/pulls/{number}/files", resp)
                batch = resp.json()
                if not isinstance(batch, list):
                    break
                files = [f for f in batch if isinstance(f, dict)]
                for item in files:
                    for key in ("filename", "previous_filename"):
                        value = item.get(key)
                        if isinstance(value, str) and value:
                            paths.append(value)
                if len(files) < _PULL_FILES_PER_PAGE:
                    break
        return paths

    async def branch_rules(self, access_token: str, repo: str, base: str) -> BranchRules:
        """Read what *base* requires: rulesets, plus classic protection when readable.

        Results are cached for :data:`BRANCH_RULES_TTL_S` per ``(repo, base)``.

        :param access_token: A valid user access token.
        :param repo: ``owner/name``.
        :param base: Base branch, e.g. ``"main"``.
        :returns: Required status contexts, rule types and whether review
            threads must be resolved; ``classic_readable=False`` when classic
            protection exists but this token cannot read it.
        :raises GitHubApiError: When the rulesets cannot be read.
        """
        key = (repo.lower(), base)
        cached = self._rules_cache.get(key)
        now = self._clock()
        if cached is not None and cached[0] > now:
            return cached[1]
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
        }
        branch = quote(base, safe="/")
        contexts: set[str] = set()
        rule_types: set[str] = set()
        threads = False
        readable = True
        async with self._http_client() as client:
            for page in range(1, _RULES_MAX_PAGES + 1):
                resp = await client.get(
                    _BRANCH_RULES_ENDPOINT.format(full_name=repo, branch=branch),
                    params={"per_page": _RULES_PER_PAGE, "page": page},
                    headers=headers,
                )
                if resp.status_code != 200:
                    raise _api_error(f"/repos/{repo}/rules/branches/{base}", resp)
                batch = resp.json()
                rules = (
                    [r for r in batch if isinstance(r, dict)] if isinstance(batch, list) else []
                )
                for rule in rules:
                    kind = rule.get("type")
                    if not isinstance(kind, str):
                        continue
                    rule_types.add(kind)
                    params = (
                        rule.get("parameters") if isinstance(rule.get("parameters"), dict) else {}
                    )
                    if kind == "required_status_checks":
                        contexts |= status_contexts(params.get("required_status_checks"))
                    elif kind == "pull_request":
                        threads = (
                            threads or params.get("required_review_thread_resolution") is True
                        )
                if len(rules) < _RULES_PER_PAGE:
                    break
            classic = await client.get(
                _BRANCH_PROTECTION_ENDPOINT.format(full_name=repo, branch=branch),
                headers=headers,
            )
        if classic.status_code == 200:
            body = classic.json()
            protection = body if isinstance(body, dict) else {}
            required = protection.get("required_status_checks")
            required = required if isinstance(required, dict) else {}
            contexts |= status_contexts(required.get("contexts"))
            contexts |= status_contexts(required.get("checks"))
            resolution = protection.get("required_conversation_resolution")
            threads = threads or (
                isinstance(resolution, dict) and resolution.get("enabled") is True
            )
            lock = protection.get("lock_branch")
            if isinstance(lock, dict) and lock.get("enabled") is True:
                rule_types.add("classic_lock_branch")
            if protection.get("restrictions") is not None:
                rule_types.add("classic_push_restrictions")
        elif classic.status_code != 404:
            readable = False
        rules_out = BranchRules(
            contexts=frozenset(contexts),
            rule_types=frozenset(rule_types),
            thread_resolution=threads,
            classic_readable=readable,
        )
        self._rules_cache[key] = (now + BRANCH_RULES_TTL_S, rules_out)
        return rules_out

    async def fetch_public_ssh_keys(self, login: str) -> tuple[str, ...]:
        """Fetch a user's PUBLIC SSH keys as ``authorized_keys`` lines.

        Uses the unauthenticated ``/users/{login}/keys`` endpoint, which only
        ever exposes public keys. A failure returns an empty tuple — SSH-key
        injection is best-effort and must not fail a launch.

        :param login: The GitHub login to read keys for.
        :returns: Tuple of key lines (possibly empty).
        """
        url = _USER_KEYS_ENDPOINT.format(login=login)
        try:
            async with self._http_client() as client:
                resp = await client.get(url, headers={"Accept": "application/vnd.github+json"})
            if resp.status_code != 200:
                _logger.warning("GitHub public keys for %s returned %s", login, resp.status_code)
                return ()
            return tuple(
                str(entry["key"]).strip()
                for entry in resp.json()
                if isinstance(entry, dict) and entry.get("key")
            )
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            _logger.warning("Failed to fetch GitHub public keys for %s: %s", login, exc)
            return ()

    async def _token_request(self, fields: dict[str, str]) -> GitHubTokenSet:
        """POST the given form fields to the token endpoint and parse the reply."""
        async with self._http_client() as client:
            resp = await client.post(
                _TOKEN_ENDPOINT,
                data=fields,
                headers={"Accept": "application/json"},
            )
        if resp.status_code != 200:
            raise GitHubAppError(f"GitHub token endpoint returned {resp.status_code}")
        return token_set_from_payload(resp.json())
