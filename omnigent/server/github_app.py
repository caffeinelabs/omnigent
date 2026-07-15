"""GitHub App configuration and API client.

Implements the *GitHub App* (not classic OAuth App) integration that
lets a user connect their GitHub account from the web UI and have their
managed sandboxes authenticate ``gh`` / git as them. See
``designs/GITHUB_APP_SANDBOX_AUTH.md``.

Two credential shapes are involved:

* **App JWT** — a short-lived RS256 token signed with the App's private
  key (``iss = app_id``). Authenticates *as the app*; only needed for
  app-level API calls (not required for the per-user connect flow).
* **User access token** (``ghu_…``) — obtained through the user
  authorization web flow using the App's client id / secret. Acts *as
  the connecting user*; this is what we inject into the sandbox.

The authorize / token endpoints are the same GitHub OAuth endpoints an
OAuth App uses, but the credentials belong to the App.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

import httpx
import jwt

_logger = logging.getLogger(__name__)

_AUTHORIZE_ENDPOINT = "https://github.com/login/oauth/authorize"
_TOKEN_ENDPOINT = "https://github.com/login/oauth/access_token"
_USER_ENDPOINT = "https://api.github.com/user"
# Public per-user SSH keys — no auth required, returns only PUBLIC keys.
_USER_KEYS_ENDPOINT = "https://api.github.com/users/{login}/keys"

_HTTP_TIMEOUT_S = 15.0

# App JWTs are accepted for up to 10 minutes by GitHub; use a small skew
# on the issued-at to tolerate minor clock drift between us and GitHub.
_APP_JWT_TTL_S = 540
_APP_JWT_CLOCK_SKEW_S = 30


@dataclass(frozen=True)
class GitHubTokenSet:
    """Result of a code exchange or refresh.

    :param access_token: The user access token (``ghu_…``).
    :param refresh_token: The refresh token (``ghr_…``), or ``None`` when
        the App issues non-expiring user tokens.
    :param expires_at: Unix epoch seconds the access token expires at, or
        ``None`` for non-expiring tokens.
    :param refresh_token_expires_at: Unix epoch seconds the refresh token
        expires at, or ``None``.
    :param scopes: Space-separated granted scopes reported by GitHub
        (usually empty for Apps — permissions are set on the App).
    """

    access_token: str
    refresh_token: str | None
    expires_at: int | None
    refresh_token_expires_at: int | None
    scopes: str


@dataclass(frozen=True)
class SandboxGithubIdentity:
    """Per-user GitHub credentials to inject into a managed sandbox.

    :param token: A currently-valid user access token.
    :param login: The user's GitHub login, e.g. ``"octocat"``.
    :param ssh_authorized_keys: The user's PUBLIC SSH keys, each a full
        ``authorized_keys`` line (``"ssh-ed25519 AAAA… "``).
    """

    token: str
    login: str
    ssh_authorized_keys: tuple[str, ...]


@dataclass(frozen=True)
class GitHubAppConfig:
    """Validated GitHub App configuration.

    Built once at startup via :meth:`from_env`. When required env is
    absent, :meth:`from_env` returns ``None`` and the whole feature stays
    dormant (the connect UI is hidden, sandboxes keep the shared
    ``GIT_TOKEN`` behaviour).

    :param app_id: Numeric App ID, or ``None`` (only needed for the app
        JWT / app-level calls).
    :param client_id: App client id used for the user authorization flow.
    :param client_secret: App client secret.
    :param private_key: RSA private key PEM for the app JWT, or ``None``.
    :param redirect_uri: OAuth callback URL registered on the App.
    :param slug: App slug used to build the ``install_url``, or ``None``.
    :param token_enc_secret: Key material for encrypting stored tokens at
        rest (see :class:`omnigent.server.secretbox.SecretBox`).
    """

    app_id: str | None
    client_id: str
    client_secret: str
    private_key: str | None
    redirect_uri: str
    slug: str | None
    token_enc_secret: str

    @property
    def install_url(self) -> str | None:
        """The App's public installation URL, or ``None`` when no slug."""
        if not self.slug:
            return None
        return f"https://github.com/apps/{self.slug}/installations/new"

    @staticmethod
    def from_env() -> GitHubAppConfig | None:
        """Build config from ``OMNIGENT_GITHUB_APP_*`` env, or ``None``.

        The feature requires a client id, a client secret, and a
        resolvable redirect URI (explicit, or derived from
        ``OMNIGENT_DOMAIN``). Missing any of these disables it.

        :returns: A validated config, or ``None`` when GitHub App
            integration is not configured.
        :raises RuntimeError: When a private key path is set but
            unreadable — a misconfiguration the operator should fix
            rather than silently run without app-level calls.
        """
        client_id = os.environ.get("OMNIGENT_GITHUB_APP_CLIENT_ID", "").strip()
        client_secret = os.environ.get("OMNIGENT_GITHUB_APP_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            return None

        redirect_uri = os.environ.get("OMNIGENT_GITHUB_APP_REDIRECT_URI", "").strip()
        if not redirect_uri:
            domain = os.environ.get("OMNIGENT_DOMAIN", "").strip()
            if not domain:
                _logger.warning(
                    "GitHub App client id/secret are set but neither "
                    "OMNIGENT_GITHUB_APP_REDIRECT_URI nor OMNIGENT_DOMAIN is — "
                    "GitHub App integration stays disabled."
                )
                return None
            redirect_uri = f"https://{domain}/v1/integrations/github/callback"

        private_key = os.environ.get("OMNIGENT_GITHUB_APP_PRIVATE_KEY", "").strip() or None
        if private_key is None:
            key_path = os.environ.get("OMNIGENT_GITHUB_APP_PRIVATE_KEY_PATH", "").strip()
            if key_path:
                try:
                    private_key = _read_text(key_path)
                except OSError as exc:
                    raise RuntimeError(
                        f"OMNIGENT_GITHUB_APP_PRIVATE_KEY_PATH={key_path!r} is unreadable: {exc}"
                    ) from exc

        # Token-at-rest encryption: a dedicated secret if provided, else
        # derive from the client secret so a minimal config still encrypts.
        token_enc_secret = (
            os.environ.get("OMNIGENT_GITHUB_APP_TOKEN_ENC_KEY", "").strip() or client_secret
        )

        return GitHubAppConfig(
            app_id=os.environ.get("OMNIGENT_GITHUB_APP_ID", "").strip() or None,
            client_id=client_id,
            client_secret=client_secret,
            private_key=private_key,
            redirect_uri=redirect_uri,
            slug=os.environ.get("OMNIGENT_GITHUB_APP_SLUG", "").strip() or None,
            token_enc_secret=token_enc_secret,
        )


def _read_text(path: str) -> str:
    """Read a text file (small helper isolated for test monkeypatching)."""
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def build_authorize_url(config: GitHubAppConfig, *, state: str) -> str:
    """Build the GitHub user-authorization URL to redirect the user to.

    GitHub Apps take no ``scope`` parameter — permissions are configured
    on the App itself — so this only carries the client id, redirect, and
    the signed ``state``.

    :param config: The GitHub App config.
    :param state: An opaque, signed state string (see the routes module).
    :returns: The full ``https://github.com/login/oauth/authorize?…`` URL.
    """
    from urllib.parse import urlencode

    params = {
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "state": state,
    }
    return f"{_AUTHORIZE_ENDPOINT}?{urlencode(params)}"


class GitHubAppClient:
    """Async HTTP client for the GitHub App user + app flows.

    Stateless beyond holding the config; every method opens its own
    short-lived :class:`httpx.AsyncClient` so the client is safe to build
    once and reuse across requests.
    """

    def __init__(self, config: GitHubAppConfig) -> None:
        self._config = config

    def app_jwt(self) -> str:
        """Mint a short-lived RS256 app JWT signed with the private key.

        :returns: A signed JWT for app-level GitHub API calls.
        :raises RuntimeError: When no app id / private key is configured.
        """
        if not self._config.app_id or not self._config.private_key:
            raise RuntimeError("app JWT requires OMNIGENT_GITHUB_APP_ID and a private key")
        now = int(time.time())
        payload = {
            "iat": now - _APP_JWT_CLOCK_SKEW_S,
            "exp": now + _APP_JWT_TTL_S,
            "iss": self._config.app_id,
        }
        return jwt.encode(payload, self._config.private_key, algorithm="RS256")

    async def exchange_code(self, code: str) -> GitHubTokenSet:
        """Exchange an authorization ``code`` for a user access token.

        :param code: The ``code`` GitHub returned to the callback.
        :returns: The resulting token set.
        :raises GitHubAppError: When GitHub rejects the exchange.
        """
        return await self._token_request(
            {
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
                "code": code,
                "redirect_uri": self._config.redirect_uri,
            }
        )

    async def refresh_token(self, refresh_token: str) -> GitHubTokenSet:
        """Exchange a refresh token for a fresh user access token.

        :param refresh_token: The stored ``ghr_…`` refresh token.
        :returns: The refreshed token set.
        :raises GitHubAppError: When GitHub rejects the refresh.
        """
        return await self._token_request(
            {
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
        )

    async def fetch_login(self, access_token: str) -> tuple[str, int]:
        """Fetch the authenticated user's ``(login, id)``.

        :param access_token: A valid user access token.
        :returns: The GitHub login and numeric user id.
        :raises GitHubAppError: When the API call fails.
        """
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
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
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
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

    async def _token_request(self, data: dict[str, str]) -> GitHubTokenSet:
        """POST to the token endpoint and parse the JSON token response."""
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            resp = await client.post(
                _TOKEN_ENDPOINT,
                data=data,
                headers={"Accept": "application/json"},
            )
        if resp.status_code != 200:
            raise GitHubAppError(f"GitHub token endpoint returned {resp.status_code}")
        payload = resp.json()
        if "error" in payload:
            detail = payload.get("error_description", payload["error"])
            raise GitHubAppError(f"GitHub token exchange failed: {detail}")
        access_token = payload.get("access_token")
        if not access_token:
            raise GitHubAppError("GitHub token response missing access_token")
        now = int(time.time())
        expires_in = payload.get("expires_in")
        refresh_expires_in = payload.get("refresh_token_expires_in")
        return GitHubTokenSet(
            access_token=str(access_token),
            refresh_token=payload.get("refresh_token") or None,
            expires_at=now + int(expires_in) if expires_in else None,
            refresh_token_expires_at=(
                now + int(refresh_expires_in) if refresh_expires_in else None
            ),
            scopes=str(payload.get("scope", "")),
        )


class GitHubAppError(Exception):
    """Raised when a GitHub App API interaction fails."""
