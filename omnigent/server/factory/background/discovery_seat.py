"""The discovery seat's pass: Scout runs exactly while discovery runs for its factory.

Every :data:`TICK_S` seconds, while the server runs discovery
(``OMNIGENT_FACTORY_DISCOVERY``), every factory's Scout is matched to its
``discovery.enabled`` (:func:`~omnigent.server.factory.discovery.seat.reconcile`):
seated (or woken) when it is on, stopped when it is off. With the server env
off there is no loop; one pass at start stops any Scout a previous run left
seated, so turning the env off and restarting stops every seat.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Final

from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.definition_blocks import discovery_server_on

_logger = logging.getLogger(__name__)

TICK_S: Final = 60.0


async def run_once(app_state: Any) -> dict[str, str]:
    """One pass over every factory. :returns: Factory id → what it did."""
    from omnigent.server.factory.discovery import seat

    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps):
        return {}
    return await seat.reconcile_all(app_state)


async def start(app_state: Any) -> Callable[[], Awaitable[None]]:
    """Run :func:`run_once` every :data:`TICK_S` while the server runs discovery."""
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)

    async def _noop() -> None:
        return None

    if not isinstance(deps, FactoryDeps):
        return _noop
    if not discovery_server_on():
        # Off server-wide: stop seats a previous run left, in the background.
        task = asyncio.get_running_loop().create_task(
            _stop_leftovers(app_state), name="factory-discovery-seat-off"
        )

        async def _stop_once() -> None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        return _stop_once

    async def _loop() -> None:
        while True:
            try:
                done = await run_once(app_state)
                changed = {k: v for k, v in done.items() if v not in ("on", "off")}
                if changed:
                    _logger.info("factory discovery: seat pass %s", changed)
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("factory discovery: seat pass failed")
            await asyncio.sleep(TICK_S)

    task = asyncio.get_running_loop().create_task(_loop(), name="factory-discovery-seat")

    async def stop() -> None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    return stop


async def _stop_leftovers(app_state: Any) -> None:
    try:
        await run_once(app_state)
    except asyncio.CancelledError:
        raise
    except Exception:
        _logger.exception("factory discovery: could not stop leftover seats")


__all__ = ["TICK_S", "run_once", "start"]
