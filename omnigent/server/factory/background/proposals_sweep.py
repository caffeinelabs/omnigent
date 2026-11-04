"""Every minute, per factory: expire, close stale ones, grace-apply and spawn.

1. Projects proposals pending for 72 hours expire (``proposal.expired``).
2. Open projects proposals that no longer apply (a project closed, finished
   or changed since, a slug taken, changes the charter already holds) close
   themselves with their reason; a person can reopen one
   (:func:`.proposals.close_stale`). So do questions whose task, PR or
   project finished since they were asked
   (:func:`.server_decisions.close_finished_subjects`).
3. Under ``grace`` autonomy, proposals nobody objected to within 15 minutes
   are applied by the server.
4. Server-owned projects are (re)started: held ones whose dependencies are now
   met are released, and a spawn a restart cut off is finished, once.
5. Initiatives' acceptance runs: a due run whose base env never reported
   ready is sent anyway, and a requested run with no result expires.
6. Initiatives' waves: once every project of an initiative's current wave is
   finished and a later wave is planned, the lead is told once
   (:func:`.initiative_waves.sweep`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final

from omnigent.factory import LABEL_STATE, STATE_DONE
from omnigent.server.factory import (
    acceptance,
    initiative_waves,
    proposals,
    server_decisions,
    spawn,
)
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.snapshot import list_factory_roots

_logger = logging.getLogger(__name__)

INTERVAL_S: Final = 60.0


@dataclass
class SweepReport:
    """What one pass did (for logs and tests)."""

    expired: list[str] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)
    started: list[str] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)
    waves: list[str] = field(default_factory=list)


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
            report.closed += await proposals.close_stale(app_state, root.id)
            report.closed += await server_decisions.close_finished_subjects(app_state, root.id)
            report.applied += await proposals.apply_graceful(app_state, root.id)
            report.started += await spawn.finish_pending(app_state, root.id)
        except Exception:
            _logger.exception("factory: proposals sweep failed for %s", root.id)
        try:
            report.acceptance += await acceptance.sweep(app_state, root.id)
        except Exception:
            _logger.exception("factory: acceptance sweep failed for %s", root.id)
        try:
            report.waves += await initiative_waves.sweep(app_state, root.id)
        except Exception:
            _logger.exception("factory: initiative waves sweep failed for %s", root.id)
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
