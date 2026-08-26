"""Keep running managed sandboxes' GitHub credentials fresh.

The GitHub user-to-server token a managed sandbox uses for ``git``/``gh`` is
minted once, at launch, and baked into the pod's credential files
(``~/.git-credentials`` + ``~/.config/gh/hosts.yml``). GitHub user tokens expire
after a few hours, so a long-lived sandbox eventually can't push or pull.

This background loop re-mints the token server-side (reusing
:func:`omnigent.server.github_identity.resolve_access_token`, which refreshes a
near-expiry token and persists it) and pushes it to each live managed-sandbox
host over the EXISTING host tunnel via a ``host.refresh_github`` frame; the host
rewrites its credential files in place. No relaunch, and the token never touches
a log.

Scope guard: only SERVER-MANAGED sandbox hosts are refreshed. A user's personal
machine also runs ``omnigent host`` — overwriting *its* ``~/.git-credentials``
would clobber the user's own git setup — so a host is skipped unless
``host_store`` reports a ``sandbox_provider``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from omnigent.db.db_models import workspace_scope
from omnigent.host.frames import HostRefreshGithubFrame, encode_host_frame
from omnigent.server.auth import RESERVED_USER_LOCAL
from omnigent.server.github_app_client import GitHubAppClient
from omnigent.server.github_identity import resolve_access_token
from omnigent.server.github_store import GithubConnectionStore
from omnigent.server.host_registry import HostConnection, HostRegistry
from omnigent.stores.host_store import HostStore

_logger = logging.getLogger(__name__)

# Sweep interval. Managed sandboxes are few and each push is a tiny frame + two
# small file writes, so a brisk interval is cheap and keeps the pushed token
# comfortably ahead of expiry.
REFRESH_INTERVAL_S = 15 * 60

# Refresh a token that expires within this window, so every token we push
# outlives the gap until the next sweep even if one tick is missed. Must exceed
# REFRESH_INTERVAL_S.
_REFRESH_MARGIN_S = 2 * REFRESH_INTERVAL_S + 5 * 60

# A refresh push is a small file write on the host; a short timeout surfaces a
# wedged host quickly without blocking the sweep.
_PUSH_TIMEOUT_S = 30.0


async def _push_refresh(
    host_registry: HostRegistry,
    conn: HostConnection,
    *,
    token: str,
    login: str,
) -> None:
    """Send one ``host.refresh_github`` frame and await the host's result.

    :raises RuntimeError: If the host reports a failure.
    :raises asyncio.TimeoutError: If the host doesn't reply within the timeout.
    :raises ConnectionError: If the connection was replaced mid-send.
    """
    request_id = f"ghrefresh_{uuid.uuid4().hex}"
    frame = HostRefreshGithubFrame(request_id=request_id, token=token, github_login=login)
    loop = asyncio.get_running_loop()
    future: asyncio.Future[dict[str, Any]] = loop.create_future()
    conn.pending_github_refreshes[request_id] = future
    try:
        host_registry.send_text(conn, encode_host_frame(frame))
        result = await asyncio.wait_for(future, timeout=_PUSH_TIMEOUT_S)
        if result.get("status") != "ok":
            raise RuntimeError(result.get("error") or "host reported failure")
    finally:
        conn.pending_github_refreshes.pop(request_id, None)


async def refresh_once(
    *,
    host_registry: HostRegistry,
    host_store: HostStore,
    github_store: GithubConnectionStore,
    github_client: GitHubAppClient,
) -> int:
    """Push a fresh GitHub token to every eligible live managed sandbox.

    Eligible = a live host whose owner is a real user (not the reserved local
    identity), that ``host_store`` marks as server-managed (has a
    ``sandbox_provider``), and whose owner has a usable GitHub connection.

    Best-effort and isolated: a per-host failure (no connection, refresh
    rejected, host unreachable) is logged and the sweep continues.

    :returns: The number of sandboxes successfully refreshed.
    """
    refreshed = 0
    for conn in host_registry.all_connections():
        owner = conn.owner
        if not owner or owner == RESERVED_USER_LOCAL:
            continue
        try:
            with workspace_scope(conn.workspace_id):
                host = host_store.get_host(conn.host_id)
                if host is None or host.sandbox_provider is None:
                    # Not a server-managed sandbox — never touch a personal
                    # machine's credentials.
                    continue
                token = await resolve_access_token(
                    owner,
                    store=github_store,
                    client=github_client,
                    refresh_margin_s=_REFRESH_MARGIN_S,
                )
                connection = github_store.get(owner)
            if token is None or connection is None or not connection.github_login:
                # Owner hasn't connected GitHub (or the token can't be
                # refreshed) — the sandbox runs on the shared credential; leave
                # it be.
                continue
            await _push_refresh(
                host_registry, conn, token=token, login=connection.github_login
            )
            refreshed += 1
        except Exception:  # noqa: BLE001 - one host's failure must not stop the sweep
            _logger.warning(
                "github credential refresh failed for host %s", conn.host_id, exc_info=True
            )
    return refreshed


async def run_github_refresh_loop(
    *,
    host_registry: HostRegistry,
    host_store: HostStore,
    github_store: GithubConnectionStore,
    github_client: GitHubAppClient,
    interval_s: float = REFRESH_INTERVAL_S,
) -> None:
    """Run :func:`refresh_once` every *interval_s* seconds until cancelled.

    Sleeps first so it doesn't race sandbox launches at server start (freshly
    launched sandboxes already hold a valid token). Cancelled during server
    shutdown via the lifespan's task-cancellation block.
    """
    _logger.info("github credential refresh loop started (every %.0fs)", interval_s)
    try:
        while True:
            await asyncio.sleep(interval_s)
            try:
                count = await refresh_once(
                    host_registry=host_registry,
                    host_store=host_store,
                    github_store=github_store,
                    github_client=github_client,
                )
                if count:
                    _logger.info(
                        "refreshed github credentials on %d managed sandbox(es)", count
                    )
            except Exception:  # noqa: BLE001 - a sweep failure must not kill the loop
                _logger.warning("github credential refresh sweep failed", exc_info=True)
    except asyncio.CancelledError:
        _logger.info("github credential refresh loop stopped")
        raise
