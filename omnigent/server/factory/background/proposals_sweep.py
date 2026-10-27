"""Every minute, per factory: expire, grace-apply and spawn.

1. Projects proposals pending for 72 hours expire (``proposal.expired``).
2. Under ``grace`` autonomy, proposals nobody objected to within 15 minutes
   are applied by the server.
3. Server-owned projects are (re)started: held ones whose dependencies are now
   met are released, and a spawn a restart cut off is finished, once.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final

from omnigent.factory import LABEL_STATE, STATE_DONE
from omnigent.server.factory import proposals, spawn
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.snapshot import list_factory_roots

_logger = logging.getLogger(__name__)

INTERVAL_S: Final = 60.0


@dataclass
class SweepReport:
    """What one pass did (for logs and tests)."""

    expired: list[str] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)
    started: list[str] = field(default_factory=list)


async def sweep_once(app_state: Any) -> SweepReport:
    """Run one pass over every factory; a failing factory is logged and skipped."""
    report = SweepReport()
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps):
        return report
    roots = await asyncio.to_thread(list_factory_roots, deps.conversation_store)
    for root in roots:
        if root.labels.get(LABEL_STATE) == STATE_DONE:
            continue
        try:
            report.expired += await proposals.expire_due(app_state, root.id)
            report.applied += await proposals.apply_graceful(app_state, root.id)
            report.started += await spawn.finish_pending(app_state, root.id)
        except Exception:
            _logger.exception("factory: proposals sweep failed for %s", root.id)
    return report


async def start(app_state: Any) -> Callable[[], Awaitable[None]]:
    """Sweep now and then every :data:`INTERVAL_S`."""

    async def _loop() -> None:
        while True:
            try:
                await sweep_once(app_state)
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("factory: proposals sweep failed")
            await asyncio.sleep(INTERVAL_S)

    task = asyncio.get_running_loop().create_task(_loop(), name="factory-proposals-sweep")

    async def stop() -> None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await spawn.drain()

    return stop


__all__ = ["INTERVAL_S", "SweepReport", "start", "sweep_once"]
