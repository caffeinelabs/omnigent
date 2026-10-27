"""Daily retention of the factory event log.

Lifecycle facts (stages, PRs, gates, decisions, controls, demos, tasks, …)
are kept indefinitely: telemetry and the nightly span heal are built from
them. Only chatter is pruned: mail, notices and preview probes after 14
days, heartbeats after 2. Demo files are kept; their size shows in the
health strip.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Final

from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.stores.factory_store import now_ms

_logger = logging.getLogger(__name__)

INTERVAL_S: Final = 86400.0
EVENT_RETENTION_S: Final = 14 * 86400
HEARTBEAT_RETENTION_S: Final = 2 * 86400

PRUNED_KINDS: Final[tuple[str, ...]] = ("message.*", "notice.*", "preview.health")
"""The only kinds dropped after :data:`EVENT_RETENTION_S` (besides heartbeats)."""
HEARTBEAT_KINDS: Final[tuple[str, ...]] = ("heartbeat.*",)
"""Dropped after :data:`HEARTBEAT_RETENTION_S`."""
KEEP_KINDS: Final[tuple[str, ...]] = (
    "stage.*",
    "pr.*",
    "gate.*",
    "decision.*",
    "control.*",
    "alert.*",
    "needs_human*",
    "demo.*",
    "preview.reported",
    "preview.parked",
    "preview.sha_mismatch",
    "env.reported",
    "env.health",
    "factory.*",
    "project.*",
    "dispatch",
    "task.*",
    "proposal.*",
    "people.*",
    "loop.*",
    "issue.*",
    "label.changed",
    "base.moved",
    "member.*",
    "budget.*",
    "chat.posted",
)
"""Lifecycle kinds that are never pruned (any kind outside the pruned lists is kept too)."""


async def prune_once(app_state: Any, *, now: int | None = None) -> int:
    """
    Delete expired chatter events; lifecycle events are kept.

    :param app_state: ``app.state`` carrying the factory deps.
    :param now: Epoch milliseconds (tests); defaults to now.
    :returns: How many events were deleted.
    """
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps):
        return 0
    store = deps.factory_store
    at = now if now is not None else now_ms()
    old = await asyncio.to_thread(
        store.prune_events, at - EVENT_RETENTION_S * 1000, list(PRUNED_KINDS)
    )
    beats = await asyncio.to_thread(
        store.prune_events, at - HEARTBEAT_RETENTION_S * 1000, list(HEARTBEAT_KINDS)
    )
    if old or beats:
        _logger.info("factory: pruned %d old event(s) and %d heartbeat event(s)", old, beats)
    return old + beats


async def start(app_state: Any) -> Callable[[], Awaitable[None]]:
    """Prune now and then daily."""

    async def _loop() -> None:
        while True:
            try:
                await prune_once(app_state)
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("factory: event pruning failed")
            await asyncio.sleep(INTERVAL_S)

    task = asyncio.get_running_loop().create_task(_loop(), name="factory-prune")

    async def stop() -> None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    return stop


__all__ = [
    "EVENT_RETENTION_S",
    "HEARTBEAT_KINDS",
    "HEARTBEAT_RETENTION_S",
    "KEEP_KINDS",
    "PRUNED_KINDS",
    "prune_once",
    "start",
]
