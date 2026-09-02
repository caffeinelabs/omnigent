"""Keep a managed sandbox warm while its agent is still running.

The runner tunnel's ping loop already stamps ``runner_last_seen`` every
``PING_INTERVAL_S`` for as long as a runner holds a live tunnel, and a live
runner tunnel is the server's signal that an agent is still working on that
sandbox: the runner exits itself once idle (``runner.idle_timeout_s``), so the
signal disappears on its own when the session goes quiet.

This module turns that existing signal into a provider
:meth:`~omnigent.onboarding.sandboxes.base.SandboxHostLauncher.keep_alive` call,
so a sandbox whose platform reaps on inactivity — or one that expects the
operator to push an absolute deadline forward — stays up while work is happening
and is reclaimed once it is not. Nine providers already implement ``keep_alive``
but only the CLI bootstrap ever called it; this is the managed-path caller.

Providers that cannot extend a sandbox (``kubernetes`` today) raise
:class:`SandboxCapabilityError` and are skipped, leaving their behaviour exactly
as it is now.

Rate-limited per runner (:data:`_MIN_INTERVAL_S`): stamping ``runner_last_seen``
is a local write, but ``keep_alive`` is a provider API call — on Kubernetes-style
backends it is an apiserver write that also wakes a controller reconcile, so it
must not run at the 30s ping cadence.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from omnigent.onboarding.sandboxes.base import SandboxCapabilityError

if TYPE_CHECKING:
    from omnigent.server.managed_hosts import ManagedSandboxDeployment
    from omnigent.stores.conversation_store import ConversationStore
    from omnigent.stores.host_store import HostStore

_logger = logging.getLogger(__name__)

# How often one runner may trigger a provider keep_alive. Well under any
# platform inactivity window (the shortest in tree is Islo's 15min idle pause),
# and 20x cheaper than the 30s ping it rides on.
_MIN_INTERVAL_S = 600.0

# Cap on the per-runner throttle map before stale entries are pruned. Runners
# are transient, so without this a long-lived server accumulates one dead key
# per session.
# ponytail: prune-on-grow, not a background sweep — swap if profiling says so.
_THROTTLE_MAX_ENTRIES = 4096

_conversation_store: ConversationStore | None = None
_host_store: HostStore | None = None
_sandbox_config: ManagedSandboxDeployment | None = None
_executor: ThreadPoolExecutor | None = None

# runner_id -> monotonic seconds of its last keep_alive attempt.
_last_kept: dict[str, float] = {}


def configure(
    conversation_store: ConversationStore,
    host_store: HostStore | None,
    sandbox_config: ManagedSandboxDeployment | None,
) -> None:
    """Wire the stores and provider set the keepalive needs.

    Called once at app construction. A ``None`` *sandbox_config* (no
    ``sandbox:`` section) leaves :func:`touch` a no-op, so a server with no
    managed sandboxes pays nothing.

    :param conversation_store: Store used to resolve a runner to its session's host.
    :param host_store: Store used to read the host's recorded sandbox; ``None``
        (a server built without one) also disables the hook.
    :param sandbox_config: The deployment's provider set, or ``None`` when
        managed sandboxes are not configured.
    """
    global _conversation_store, _host_store, _sandbox_config, _executor
    _conversation_store = conversation_store
    _host_store = host_store
    _sandbox_config = sandbox_config
    if sandbox_config is not None and host_store is not None and _executor is None:
        _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="managed-keepalive")


def touch(runner_id: str) -> None:
    """Keep the sandbox behind *runner_id* warm, at most every :data:`_MIN_INTERVAL_S`.

    Non-blocking and fail-safe: the provider call runs on a worker thread so a
    slow backend cannot delay the tunnel ping loop that calls this, and every
    failure is logged and swallowed. Safe to call on every ping.

    :param runner_id: Runner with a live tunnel, e.g. ``"runner_token_abc"``.
    """
    if _sandbox_config is None or _host_store is None or _executor is None:
        return
    now = time.monotonic()
    last = _last_kept.get(runner_id)
    if last is not None and now - last < _MIN_INTERVAL_S:
        return
    _last_kept[runner_id] = now
    if len(_last_kept) > _THROTTLE_MAX_ENTRIES:
        _prune_throttle(now)
    _executor.submit(_keep_alive_for_runner, runner_id)


def _prune_throttle(now: float) -> None:
    """Drop throttle entries older than two intervals (their runners are gone)."""
    cutoff = now - 2 * _MIN_INTERVAL_S
    for runner_id in [rid for rid, seen in _last_kept.items() if seen < cutoff]:
        _last_kept.pop(runner_id, None)


def _keep_alive_for_runner(runner_id: str) -> None:
    """Resolve *runner_id* to its managed sandbox and extend it. Never raises."""
    try:
        conversation_store, host_store, deployment = (
            _conversation_store,
            _host_store,
            _sandbox_config,
        )
        if conversation_store is None or host_store is None or deployment is None:
            return
        host_ids = {
            conv.host_id
            for conv in conversation_store.list_conversations_by_runner_id(runner_id)
            if conv.host_id
        }
        for host_id in host_ids:
            host = host_store.get_host(host_id)
            # Only a server-provisioned sandbox has one to extend; a CLI host has
            # no sandbox_id and is left alone.
            if host is None or not host.sandbox_id:
                continue
            config = deployment.recorded(host.sandbox_provider)
            try:
                config.launcher_factory().keep_alive(host.sandbox_id)
            except SandboxCapabilityError:
                # Provider cannot extend a sandbox (e.g. kubernetes): today's
                # behaviour, nothing to log every 10 minutes.
                _logger.debug(
                    "keep_alive unsupported by provider %s; skipping sandbox %s",
                    host.sandbox_provider,
                    host.sandbox_id,
                )
    except Exception:  # noqa: BLE001 — keepalive must never disrupt the tunnel
        _logger.warning("managed sandbox keepalive failed for runner %s", runner_id, exc_info=True)
