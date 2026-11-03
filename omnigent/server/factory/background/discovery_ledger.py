"""The discovery ledger's upkeep: retention, and a daily snapshot of every score.

Hourly, while the server runs discovery (``OMNIGENT_FACTORY_DISCOVERY``):

- signals past their source's retention (``keep_days``) lose their text; the
  rows, their links and every count stay (privacy floor, Tier 0);
- once a UTC day, each factory with ``discovery.enabled`` stores a snapshot
  of every open opportunity's score, so later backtests can re-score the past
  without later data leaking in.
"""

from __future__ import annotations

import asyncio
import calendar
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Final

from omnigent.factory import LABEL_ID
from omnigent.server.factory import definition
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.control import same_id
from omnigent.server.factory.definition_blocks import discovery_server_on
from omnigent.server.factory.membership import is_top_level
from omnigent.stores.factory_store import now_s

_logger = logging.getLogger(__name__)

INTERVAL_S: Final = 3600.0
DAILY: Final = "daily"


def _day_start(now: int) -> int:
    day = time.gmtime(now)
    return calendar.timegm((day.tm_year, day.tm_mon, day.tm_mday, 0, 0, 0))


def _roots(deps: FactoryDeps) -> list[str]:
    store = deps.conversation_store
    ids = store.list_conversation_ids_with_label(LABEL_ID)
    rows = store.get_conversations(ids)
    return [
        conv.id
        for conv in (rows.get(i) for i in ids)
        if conv is not None and is_top_level(conv) and same_id(conv.labels.get(LABEL_ID), conv.id)
    ]


def run_once(deps: FactoryDeps, *, now: int | None = None) -> dict[str, int]:
    """
    One pass (synchronous; run in a thread).

    :returns: ``{"expired": n, "snapshots": n}``.
    """
    from omnigent.server.factory.discovery import ledger, opportunities

    at = now if now is not None else now_s()
    expired = ledger.expire_due(deps, now=at)
    snapshots = 0
    start = _day_start(at)
    for fid in _roots(deps):
        if not definition.discovery_enabled(deps, fid):
            continue
        today = deps.factory_store.list_score_snapshots(fid, since=start, limit=500)
        if any(s.reason == DAILY for s in today):
            continue
        snapshots += opportunities.snapshot_all(deps, fid, reason=DAILY, at=at)
    return {"expired": expired, "snapshots": snapshots}


async def start(app_state: Any) -> Callable[[], Awaitable[None]]:
    """Run :func:`run_once` hourly while the server runs discovery."""
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)

    async def _noop() -> None:
        return None

    if not isinstance(deps, FactoryDeps) or not discovery_server_on():
        return _noop

    async def _loop() -> None:
        while True:
            try:
                done = await asyncio.to_thread(run_once, deps)
                if done["expired"] or done["snapshots"]:
                    _logger.info(
                        "factory discovery: %d signal text(s) expired, %d score snapshot(s)",
                        done["expired"],
                        done["snapshots"],
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("factory discovery: ledger upkeep failed")
            await asyncio.sleep(INTERVAL_S)

    task = asyncio.get_running_loop().create_task(_loop(), name="factory-discovery-ledger")

    async def stop() -> None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    return stop


__all__ = ["DAILY", "INTERVAL_S", "run_once", "start"]
