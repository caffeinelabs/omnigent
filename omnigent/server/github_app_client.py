"""Async HTTP client for the GitHub App user + app flows.

The network half of the GitHub App integration. It sends the OAuth
token requests and reads the user / public-key endpoints, but never
constructs credentials itself: the App secrets and the form fields that
carry them are owned by :mod:`omnigent.server.github_app`
(:class:`~omnigent.server.github_app.GitHubAppConfig`), which this
module simply POSTs. Keeping the secret-owning code and the network
sink in separate modules is deliberate. See
``designs/GITHUB_APP_SANDBOX_AUTH.md``.
"""

from __future__ import annotations

import logging

import httpx

from omnigent.server.github_app import (
    GitHubAppConfig,
    GitHubAppError,
    GitHubTokenSet,
    token_set_from_payload,
)

_logger = logging.getLogger(__name__)

_TOKEN_ENDPOINT = "https://github.com/login/oauth/access_token"
_USER_ENDPOINT = "https://api.github.com/user"
# Public per-user SSH keys — no auth required, returns only PUBLIC keys.
_USER_KEYS_ENDPOINT = "https://api.github.com/users/{login}/keys"
# Repos the token can access (App-scoped), most-recently-pushed first.
_USER_REPOS_ENDPOINT = "https://api.github.com/user/repos"
_REPOS_PER_PAGE = 100
# Cap the walk so a user with thousands of repos gets a bounded, fast
# response for the picker (the newest ~300 by push time).
_REPOS_MAX_PAGES = 3

_HTTP_TIMEOUT_S = 15.0


class GitHubAppClient:
    """Async HTTP client for the GitHub App user + app flows.

    Stateless beyond holding the config; every method opens its own
    short-lived :class:`httpx.AsyncClient` so the client is safe to build
    once and reuse across requests.
    """

    def __init__(
        self, config: GitHubAppConfig, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._config = config
        # Injectable transport for tests (httpx.MockTransport); None uses
        # the real network.
        self._transport = transport

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

    async def fetch_public_ssh_keys(self, login: str) -> tuple[str, ...]:
        """Fetch a user's PUBLIC SSH keys as ``authorized_keys`` lines.

        Uses the unauthenticated ``/users/{login}/keys`` endpoint, which
        only ever exposes public keys. A failure returns an empty tuple —
        SSH-key injection is best-effort and must not fail a launch.

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

    async def list_repos(self, access_token: str) -> list[dict[str, object]]:
        """List repos the authenticated user can access, App-scoped.

        Reads ``/user/repos`` most-recently-pushed first, following up to
        :data:`_REPOS_MAX_PAGES` pages. Returns a compact projection for the
        new-chat repo picker (not the full GitHub payload).

        :param access_token: A valid user access token.
        :returns: Repos as
            ``{full_name, clone_url, default_branch, private, pushed_at}``,
            newest first.
        :raises GitHubAppError: When the API call fails.
        """
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
        }
        repos: list[dict[str, object]] = []
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
        return repos

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
