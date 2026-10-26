"""Daily retention of the factory event log.

Events older than 14 days are deleted, and heartbeat events (the noisiest
kind) after 2 days. Demo files are kept; their size shows in the health strip.
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


async def prune_once(app_state: Any, *, now: int | None = None) -> int:
    """
    Delete expired events.

    :param app_state: ``app.state`` carrying the factory deps.
    :param now: Epoch milliseconds (tests); defaults to now.
    :returns: How many events were deleted.
    """
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps):
        return 0
    store = deps.factory_store
    at = now if now is not None else now_ms()
    old = await asyncio.to_thread(store.prune_events, at - EVENT_RETENTION_S * 1000)
    beats = await asyncio.to_thread(
        store.prune_events, at - HEARTBEAT_RETENTION_S * 1000, ["heartbeat.*"]
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


__all__ = ["EVENT_RETENTION_S", "HEARTBEAT_RETENTION_S", "prune_once", "start"]
