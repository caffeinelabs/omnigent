"""The sources' sync: sync-mode sources read on their interval, through the gateway.

Every :data:`TICK_S` seconds, while the server runs discovery
(``OMNIGENT_FACTORY_DISCOVERY``), each open factory (not done, not closed)
with ``discovery.enabled`` syncs every source whose interval passed: not
paused, not on hold, a kind that syncs (``repo``, ``web``), ``interval_min``
above 0. A sync is the
gateway's own read (``source.sync`` events, the ledger, the daily call cap);
a pause stops one that runs at once, and repeated failures put the source on
hold with an incident.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Final

from omnigent.factory import LABEL_ID, LABEL_STATE, STATE_DONE
from omnigent.server.factory import definition
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.control import CockpitError, same_id
from omnigent.server.factory.definition_blocks import discovery_server_on
from omnigent.server.factory.membership import is_top_level
from omnigent.stores.factory_store import now_s
from omnigent.util.session_lifecycle import is_session_closed

_logger = logging.getLogger(__name__)

TICK_S: Final = 60.0


def _due(deps: FactoryDeps, now: int) -> list[tuple[str, str]]:
    """:returns: ``(factory id, source id)`` of each due source of an open factory."""
    from omnigent.server.factory.discovery import gateway, sources

    store = deps.conversation_store
    ids = store.list_conversation_ids_with_label(LABEL_ID)
    rows = store.get_conversations(ids)
    out: list[tuple[str, str]] = []
    for conv in (rows.get(i) for i in ids):
        if (
            conv is None
            or not is_top_level(conv)
            or not same_id(conv.labels.get(LABEL_ID), conv.id)
        ):
            continue
        # A finished or closed factory reads nothing more (as the PR poller skips it).
        if conv.labels.get(LABEL_STATE) == STATE_DONE or is_session_closed(
            conv.labels, conv.title
        ):
            continue
        if not definition.discovery_enabled(deps, conv.id):
            continue
        states = sources.states(deps, conv.id)
        out += [
            (conv.id, s.id)
            for s in sources.listed(deps, conv.id)
            if gateway.due(s, states.get(s.id), now)
        ]
    return out


async def run_once(app_state: Any, *, now: int | None = None) -> dict[str, int]:
    """
    Sync every due source once, one after another.

    :returns: ``{"synced": n, "failed": n}``.
    """
    from omnigent.server.factory.discovery import gateway

    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps) or not discovery_server_on():
        return {"synced": 0, "failed": 0}
    due = await asyncio.to_thread(_due, deps, now if now is not None else now_s())
    synced = failed = 0
    for factory_id, source_id in due:
        try:
            done = await gateway.sync_source(app_state, factory_id, source_id)
        except CockpitError as exc:
            failed += 1
            _logger.info(
                "factory discovery: sync of %s/%s refused or failed: %s",
                factory_id,
                source_id,
                exc.message,
            )
            continue
        except Exception:
            failed += 1
            _logger.exception("factory discovery: sync of %s/%s broke", factory_id, source_id)
            continue
        if done is not None:
            synced += 1
    return {"synced": synced, "failed": failed}


async def start(app_state: Any) -> Callable[[], Awaitable[None]]:
    """Run :func:`run_once` every :data:`TICK_S` while the server runs discovery."""
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)

    async def _noop() -> None:
        return None

    if not isinstance(deps, FactoryDeps) or not discovery_server_on():
        return _noop

    async def _loop() -> None:
        while True:
            try:
                done = await run_once(app_state)
                if done["synced"] or done["failed"]:
                    _logger.info(
                        "factory discovery: %d source(s) synced, %d failed",
                        done["synced"],
                        done["failed"],
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("factory discovery: source sync pass failed")
            await asyncio.sleep(TICK_S)

    task = asyncio.get_running_loop().create_task(_loop(), name="factory-discovery-sync")

    async def stop() -> None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    return stop


__all__ = ["TICK_S", "run_once", "start"]
