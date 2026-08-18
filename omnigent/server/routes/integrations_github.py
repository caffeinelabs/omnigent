"""GitHub App integration routes: connect / callback / status / disconnect.

Mounted under ``/v1`` so paths are ``/v1/integrations/github/...``.
Only mounted when a :class:`GitHubAppConfig` is configured. Lets a
signed-in user connect their GitHub account so their managed sandboxes
authenticate ``gh`` / git as them and receive their public SSH keys.
See ``designs/GITHUB_APP_SANDBOX_AUTH.md``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import quote, urlencode

import jwt
from fastapi import APIRouter, HTTPException, Request
from starlette.responses import RedirectResponse

from omnigent.server.auth import RESERVED_USER_LOCAL, AuthProvider
from omnigent.server.github_app import (
    GitHubAppConfig,
    GitHubAppError,
    build_authorize_url,
)
from omnigent.server.github_app_client import GitHubAppClient
from omnigent.server.github_identity import resolve_access_token
from omnigent.server.github_store import GithubConnectionStore
from omnigent.server.routes._auth_helpers import require_user

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.stores.conversation_store import ConversationStore

_logger = logging.getLogger(__name__)

# The OAuth state JWT is short-lived: it only has to survive the user's
# round trip to GitHub's consent screen.
_STATE_TTL_S = 600
_STATE_ALG = "HS256"

# Fallback landing after connect/disconnect when no (safe) return_to is
# supplied. The SPA renders the integrations panel in Settings.
_DEFAULT_RETURN_TO = "/settings"

# GitHub owner / repo name charset, enforced before either reaches the
# branches URL so a caller can never smuggle a path or query.
_GITHUB_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# Extract ``owner/repo`` from a github.com clone URL (https or scp-style),
# stripping any trailing ``.git``. Returns None for non-github or malformed URLs.
_GITHUB_HTTPS_RE = re.compile(r"^https://github\.com/([^/\s]+)/([^/\s#]+?)(?:\.git)?/?$")
_GITHUB_SSH_RE = re.compile(r"^git@github\.com:([^/\s]+)/([^/\s#]+?)(?:\.git)?/?$")


def _repo_full_name(url: str) -> str | None:
    """Return the ``owner/repo`` of a github.com clone URL, or ``None``.

    :param url: A clone URL (fragment already stripped), https or scp-style.
    :returns: ``"owner/repo"`` when it is a github.com URL, else ``None``.
    """
    m = _GITHUB_HTTPS_RE.match(url) or _GITHUB_SSH_RE.match(url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def _iso_to_epoch(value: object) -> int | None:
    """Parse an ISO-8601 timestamp (e.g. GitHub ``created_at``) to epoch seconds.

    :param value: The timestamp string, or anything non-string.
    :returns: Epoch seconds, or ``None`` when unparseable.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _body_links_session(body: str, session_id: str) -> bool:
    """Whether a PR *body* carries this session's Open-in-Omnigent link.

    Matches the stable ``/c/<id>`` suffix of the link target inside a delimited
    href/markdown link, not the full URL. The stamp uses its own session URL
    while this server derives the URL from its own base; a trailing-slash /
    mount-path / query-string divergence between the two would make a full-URL
    match silently drop every PR. The id is bounded by a link delimiter (or a
    query) so a longer id sharing this one as a prefix can't collide.
    """
    suffix = re.escape(f"/c/{quote(session_id, safe='')}")
    pattern = rf'(?:href="|\]\()[^"\)\s]*{suffix}(?:\?[^"\)\s]*)?(?:"|\))'
    return re.search(pattern, body) is not None


def _sanitize_return_to(raw: str | None) -> str:
    """Clamp a caller-supplied return path to a safe same-origin path.

    Only relative paths beginning with a single ``/`` are accepted, so a
    redirect can never be pointed at an external origin
    (``//evil.com``, ``https://evil.com``).

    :param raw: The caller-supplied ``return_to``, or ``None``.
    :returns: A safe relative path.
    """
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return _DEFAULT_RETURN_TO
    return raw


def _redirect_with_status(return_to: str, status: str) -> RedirectResponse:
    """Redirect back to *return_to* with a ``?github=<status>`` marker."""
    sep = "&" if "?" in return_to else "?"
    return RedirectResponse(
        url=f"{return_to}{sep}{urlencode({'github': status})}", status_code=302
    )


def create_integrations_github_router(
    config: GitHubAppConfig,
    store: GithubConnectionStore,
    *,
    auth_provider: AuthProvider | None = None,
    client: GitHubAppClient | None = None,
    conversation_store: ConversationStore | None = None,
) -> APIRouter:
    """Build the GitHub App integration router.

    :param config: Validated GitHub App config (feature is enabled).
    :param store: Connection persistence.
    :param auth_provider: Auth provider for identity resolution, or
        ``None`` when auth is disabled (single-user/local).
    :param client: GitHub App client. Defaults to one built from
        *config*; injectable for tests.
    :param conversation_store: Session store, used to resolve a session's
        cloned repos for the "PRs opened this session" endpoint. When
        ``None`` that endpoint returns an empty list.
    :returns: A FastAPI router with the integration endpoints.
    """
    router = APIRouter()
    api = client if client is not None else GitHubAppClient(config)

    def _current_user(request: Request) -> str:
        """Return the caller's id, mapping the disabled case to ``local``."""
        user_id = require_user(request, auth_provider)
        return user_id if user_id is not None else RESERVED_USER_LOCAL

    def _sign_state(user_id: str, return_to: str) -> str:
        payload = {
            "sub": user_id,
            "return_to": return_to,
            "nonce": secrets.token_urlsafe(16),
            "exp": int(time.time()) + _STATE_TTL_S,
        }
        return jwt.encode(payload, config.token_enc_secret, algorithm=_STATE_ALG)

    def _verify_state(state: str) -> dict:
        return jwt.decode(state, config.token_enc_secret, algorithms=[_STATE_ALG])

    @router.get("/integrations/github/status")
    async def status(request: Request) -> dict[str, object]:
        """Return the caller's GitHub connection status.

        Never surfaces tokens — only the connected login, scopes, and
        the App's install URL.
        """
        user_id = _current_user(request)
        connection = await asyncio.to_thread(store.get, user_id)
        return {
            "enabled": True,
            "connected": connection is not None,
            "login": connection.github_login if connection is not None else None,
            "scopes": connection.scopes if connection is not None else None,
            "connected_at": connection.created_at if connection is not None else None,
            "install_url": config.install_url,
        }

    @router.get("/integrations/github/repos")
    async def repos(request: Request) -> dict[str, object]:
        """List repos the connected user can access, for the new-chat picker.

        ``connected: false`` (with an empty list) when the caller hasn't
        linked GitHub, so the UI can fall back to a free-text repo URL.
        """
        user_id = _current_user(request)
        token = await resolve_access_token(user_id, store=store, client=api)
        if token is None:
            return {"connected": False, "repos": []}
        try:
            repo_list = await api.list_repos(token)
        except GitHubAppError as exc:
            _logger.warning("GitHub repo list failed for %s: %s", user_id, exc)
            raise HTTPException(
                status_code=502, detail="Failed to list GitHub repositories"
            ) from exc
        return {"connected": True, "repos": repo_list}

    @router.get("/integrations/github/repos/{owner}/{repo}/branches")
    async def repo_branches(request: Request, owner: str, repo: str) -> dict[str, object]:
        """List branch names for ``owner/repo``, for the per-repo branch picker.

        ``connected: false`` (empty list) when the caller hasn't linked
        GitHub. Owner/repo are charset-validated before they reach the
        GitHub URL so a caller cannot smuggle a path.
        """
        user_id = _current_user(request)
        if not _GITHUB_NAME_RE.match(owner) or not _GITHUB_NAME_RE.match(repo):
            raise HTTPException(status_code=400, detail="Invalid repository name")
        token = await resolve_access_token(user_id, store=store, client=api)
        if token is None:
            return {"connected": False, "branches": []}
        try:
            branches = await api.list_branches(token, f"{owner}/{repo}")
        except GitHubAppError as exc:
            _logger.warning("GitHub branch list failed for %s/%s: %s", owner, repo, exc)
            raise HTTPException(status_code=502, detail="Failed to list GitHub branches") from exc
        return {"connected": True, "branches": branches}

    async def _resolve_session_pulls(
        user_id: str, session_id: str, conv: Conversation
    ) -> dict[str, object]:
        """Resolve the PRs opened during *session_id* (shared by list + ci-watch).

        UNIONs two detection paths so a PR is associated with this session by
        EITHER signal:

        * **Commit trailer** — for the session's cloned repos, a PR whose commits
          carry the ``Omnigent-Session: <id>`` trailer the sandbox stamps.
        * **Body link** — across ALL repos (a single GitHub search), a PR whose
          body carries this session's Open-in-Omnigent link (``…/c/<id>``),
          stamped by the sandbox ``gh`` wrapper on PR creation. This finds PRs in
          repos the session never cloned.

        Returns ``{connected, pulls}``; ``connected: false`` when GitHub isn't
        linked. Deduped by ``(repo, number)``; the cloned-repo (trailer) record
        wins on collision since it carries a richer ``head_ref``.
        """
        from omnigent.server.managed_hosts import (
            MANAGED_REPO_LABEL_KEY,
            parse_repo_workspaces,
        )

        # Resolve identity first — it gates BOTH paths and must run even when the
        # session cloned no repos (the body-link search can still find PRs).
        connection = await asyncio.to_thread(store.get, user_id)
        token = await resolve_access_token(user_id, store=store, client=api)
        if token is None:
            return {"connected": False, "pulls": []}
        login = connection.github_login if connection is not None else None

        since = conv.created_at
        # Keyed by (repo_full_name, number) so the two paths dedup against each
        # other; the trailer path inserts first so its richer record wins.
        found: dict[tuple[str, object], dict[str, object]] = {}

        raw_repos = conv.labels.get(MANAGED_REPO_LABEL_KEY)
        if raw_repos:
            try:
                repos = parse_repo_workspaces(raw_repos)
            except ValueError:
                repos = []
            full_names: list[str] = []
            for repo in repos:
                full_name = _repo_full_name(repo.url)
                if full_name and full_name not in full_names:
                    full_names.append(full_name)

            marker = f"Omnigent-Session: {session_id}"
            for full_name in full_names:
                try:
                    repo_pulls = await api.list_pulls(token, full_name)
                except GitHubAppError as exc:
                    _logger.warning("session PRs: list_pulls failed for %s: %s", full_name, exc)
                    continue
                for pr in repo_pulls:
                    if login is not None and pr.get("author_login") != login:
                        continue
                    created = _iso_to_epoch(pr.get("created_at"))
                    if created is not None and created < since:
                        continue
                    number = pr.get("number")
                    if not isinstance(number, int):
                        continue
                    try:
                        messages = await api.list_pull_commit_messages(token, full_name, number)
                    except GitHubAppError as exc:
                        _logger.warning(
                            "session PRs: commits fetch failed for %s#%s: %s",
                            full_name,
                            number,
                            exc,
                        )
                        continue
                    if not any(marker in message for message in messages):
                        continue
                    key = (full_name, number)
                    if key not in found:
                        found[key] = {
                            **{k: v for k, v in pr.items() if k != "body"},
                            "repo": full_name,
                        }

        # Cross-repo body-link search, scoped to the connected user's PRs. Needs a
        # resolved login: the author filter is the only thing scoping results to
        # the caller, so a missing login must not widen the query to every author.
        if login is not None:
            query = f"{session_id} in:body type:pr author:{login}"
            try:
                search_hits = await api.search_pulls(token, query)
            except GitHubAppError as exc:
                _logger.warning("session PRs: search failed for %s: %s", session_id, exc)
                search_hits = []
            for pr in search_hits:
                if not _body_links_session(str(pr.get("body") or ""), session_id):
                    continue
                created = _iso_to_epoch(pr.get("created_at"))
                if created is not None and created < since:
                    continue
                number = pr.get("number")
                if not isinstance(number, int):
                    continue
                key = (str(pr.get("repo") or ""), number)
                if key not in found:
                    found[key] = {k: v for k, v in pr.items() if k != "body"}

        pulls = sorted(found.values(), key=lambda p: str(p.get("created_at") or ""), reverse=True)
        return {"connected": True, "pulls": pulls}

    @router.get("/integrations/github/sessions/{session_id}/pull-requests")
    async def session_pull_requests(request: Request, session_id: str) -> dict[str, object]:
        """List PRs opened DURING *session_id*, across its cloned repos.

        Any state (open/draft/merged/closed) is included. ``connected: false``
        when the caller hasn't linked GitHub; an empty list for a non-managed
        session or one with no cloned repos.
        """
        user_id = _current_user(request)
        if conversation_store is None:
            return {"connected": True, "pulls": []}
        conv = await asyncio.to_thread(conversation_store.get_conversation, session_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="session not found")
        return await _resolve_session_pulls(user_id, session_id, conv)

    @router.post("/integrations/github/sessions/{session_id}/ci-watch")
    async def arm_ci_watch(request: Request, session_id: str) -> dict[str, object]:
        """Arm a CI watch: wake this session when its PRs' checks conclude.

        Resolves the session's PRs now and registers a watch; a background
        poller then injects a ``[CI] …`` message into the session when a PR's
        check runs reach a terminal state. Idempotent (re-arming replaces).
        """
        user_id = _current_user(request)
        registry = getattr(request.app.state, "ci_watch_registry", None)
        if registry is None or conversation_store is None:
            raise HTTPException(status_code=503, detail="CI watch is not enabled on this server")
        conv = await asyncio.to_thread(conversation_store.get_conversation, session_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="session not found")

        resolved = await _resolve_session_pulls(user_id, session_id, conv)
        if not resolved.get("connected"):
            return {"armed": False, "reason": "github not connected"}

        from omnigent.server.ci_watch import build_watch_from_prs

        pulls = resolved.get("pulls") or []
        watch = build_watch_from_prs(session_id, user_id, pulls)  # type: ignore[arg-type]
        if not watch.prs:
            registry.disarm(session_id)
            return {"armed": False, "watched": 0, "reason": "no session PRs to watch"}
        registry.arm(watch)
        _logger.info("ci-watch armed for %s (%d PRs)", session_id, len(watch.prs))
        return {
            "armed": True,
            "watched": len(watch.prs),
            "prs": [f"{pr.repo}#{pr.number}" for pr in watch.prs],
        }

    @router.get("/integrations/github/sessions/{session_id}/ci-watch")
    async def ci_watch_dryrun(request: Request, session_id: str) -> dict[str, object]:
        """Dry-run the CI-watch poll for a session (diagnostic, no wake).

        Resolves the session's PRs and, using the caller's token, fetches each
        PR's check runs exactly as the background poller does — returning the
        raw runs, the computed aggregate, and any error. Surfaces silent
        poll-side failures (e.g. a token lacking ``checks:read``) that the
        fire-and-forget poller only logs. Never injects a message.
        """
        from omnigent.server.ci_watch import aggregate_check_conclusion

        user_id = _current_user(request)
        if conversation_store is None:
            return {"connected": True, "prs": []}
        conv = await asyncio.to_thread(conversation_store.get_conversation, session_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="session not found")

        resolved = await _resolve_session_pulls(user_id, session_id, conv)
        if not resolved.get("connected"):
            return {"connected": False, "prs": []}
        token = await resolve_access_token(user_id, store=store, client=api)
        if token is None:
            return {"connected": False, "prs": [], "reason": "no token"}

        results: list[dict[str, object]] = []
        pulls = resolved.get("pulls") or []
        if not isinstance(pulls, list):
            pulls = []
        for pr in pulls:
            if not isinstance(pr, dict):
                continue
            full_name = pr.get("repo")
            head_ref = pr.get("head_ref")
            entry: dict[str, object] = {"repo": full_name, "head_ref": head_ref}
            if not isinstance(full_name, str) or not isinstance(head_ref, str):
                entry["error"] = "malformed pr"
                results.append(entry)
                continue
            try:
                runs = await api.list_check_runs(token, full_name, head_ref)
                entry["runs"] = runs
                entry["conclusion"] = aggregate_check_conclusion(runs)
            except GitHubAppError as exc:
                entry["error"] = f"{type(exc).__name__}: {exc}"
            results.append(entry)
        return {"connected": True, "prs": results}

    @router.get("/integrations/github/connect")
    async def connect(request: Request, return_to: str | None = None) -> RedirectResponse:
        """Redirect the user into the GitHub authorization flow."""
        user_id = _current_user(request)
        state = _sign_state(user_id, _sanitize_return_to(return_to))
        return RedirectResponse(url=build_authorize_url(config, state=state), status_code=302)

    @router.get("/integrations/github/callback")
    async def callback(
        request: Request, code: str | None = None, state: str | None = None
    ) -> RedirectResponse:
        """Handle the GitHub redirect: exchange the code and store tokens.

        Validates the signed state and binds it to the authenticated
        caller so the callback cannot be replayed or cross-bound to
        another user. Redirects back to the state's ``return_to`` with a
        ``?github=connected|error`` marker.
        """
        user_id = _current_user(request)
        if not code or not state:
            return _redirect_with_status(_DEFAULT_RETURN_TO, "error")
        try:
            claims = _verify_state(state)
        except jwt.PyJWTError:
            _logger.warning("GitHub callback with invalid state")
            return _redirect_with_status(_DEFAULT_RETURN_TO, "error")
        return_to = _sanitize_return_to(claims.get("return_to"))
        if claims.get("sub") != user_id:
            _logger.warning("GitHub callback state/user mismatch")
            return _redirect_with_status(return_to, "error")
        try:
            tokens = await api.exchange_code(code)
            login, github_user_id = await api.fetch_login(tokens.access_token)
        except GitHubAppError as exc:
            _logger.warning("GitHub connect failed for %s: %s", user_id, exc)
            return _redirect_with_status(return_to, "error")
        await asyncio.to_thread(
            store.upsert,
            user_id,
            github_login=login,
            github_user_id=github_user_id,
            tokens=tokens,
        )
        _logger.info("GitHub account %s connected for %s", login, user_id)
        return _redirect_with_status(return_to, "connected")

    @router.post("/integrations/github/disconnect")
    async def disconnect(request: Request) -> dict[str, bool]:
        """Remove the caller's GitHub connection."""
        user_id = _current_user(request)
        removed = await asyncio.to_thread(store.delete, user_id)
        return {"disconnected": removed}

    return router
