"""Readouts on schedule: notice each bet's release, then measure it on its readout days.

Every :data:`TICK_S` seconds, while the server runs discovery
(``OMNIGENT_FACTORY_DISCOVERY``), each open factory (not done, not closed)
with ``discovery.enabled``:

- schedules the readouts of every applied bet whose release the profile's
  ``product.delivery.release`` sees (``merged``: all its projects are out;
  ``github_release`` / ``github_deployment:<env>``: then a release or a
  successful deployment of their repos, looked up with the owner's GitHub
  connection; ``manual``: only a person marks it);
- writes each readout whose day came: the measurement, the fixed-rule verdict,
  the write-back through the ledger, a line in the Room's discovery thread
  (``#factory`` until the Room has one), a mail to Scout when it runs, and a
  "Needs you" decision for the last readout of a bet or a lost one.

Readout days count from the release; an eval-mode factory's readout day is a
minute (:data:`.discovery.readouts.EVAL_DAY_S`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Final

from omnigent.factory import LABEL_ID, LABEL_STATE, STATE_DONE
from omnigent.server.factory import definition
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.control import same_id
from omnigent.server.factory.definition_blocks import discovery_server_on
from omnigent.server.factory.membership import is_top_level
from omnigent.stores.factory_store import now_s
from omnigent.util.session_lifecycle import is_session_closed

_logger = logging.getLogger(__name__)

TICK_S: Final = 120.0


def _factories(deps: FactoryDeps) -> list[str]:
    """:returns: The open factory roots with discovery on."""
    store = deps.conversation_store
    ids = store.list_conversation_ids_with_label(LABEL_ID)
    rows = store.get_conversations(ids)
    out: list[str] = []
    for conv in (rows.get(i) for i in ids):
        if (
            conv is None
            or not is_top_level(conv)
            or not same_id(conv.labels.get(LABEL_ID), conv.id)
        ):
            continue
        if conv.labels.get(LABEL_STATE) == STATE_DONE or is_session_closed(
            conv.labels, conv.title
        ):
            continue
        if definition.discovery_enabled(deps, conv.id):
            out.append(conv.id)
    return out


async def run_once(app_state: Any, *, now: int | None = None) -> dict[str, int]:
    """
    One pass over every open factory with discovery on.

    :returns: ``{"scheduled": n, "written": n}``.
    """
    from omnigent.server.factory.discovery import readouts

    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps) or not discovery_server_on():
        return {"scheduled": 0, "written": 0}
    at = now if now is not None else now_s()
    total = {"scheduled": 0, "written": 0}
    for factory_id in await asyncio.to_thread(_factories, deps):
        try:
            done = await readouts.tick(app_state, factory_id, now=at)
        except Exception:
            _logger.exception("factory discovery: readouts of %s failed", factory_id)
            continue
        total["scheduled"] += done["scheduled"]
        total["written"] += done["written"]
    return total


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
                if done["scheduled"] or done["written"]:
                    _logger.info(
                        "factory discovery: %d readout(s) scheduled, %d written",
                        done["scheduled"],
                        done["written"],
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("factory discovery: readout pass failed")
            await asyncio.sleep(TICK_S)

    task = asyncio.get_running_loop().create_task(_loop(), name="factory-discovery-readouts")

    async def stop() -> None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    return stop


__all__ = ["TICK_S", "run_once", "start"]
