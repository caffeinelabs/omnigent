"""The learning side of the supervisor tick, one hook for every factory.

Per factory, each tick: expire stale tuning and rules proposals, end due
trials (and revert unattended ones), send Sentinel's retro beat (rule S12),
run eval mode, and once a day record yesterday's human-prompt scan. Each step
is idempotent across a server restart (events or proposal state record it).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import LABEL_ID

if TYPE_CHECKING:
    from omnigent.entities import Conversation

_logger = logging.getLogger(__name__)

SCAN_AFTER_H: Final = 1
"""The prompt scan of a UTC day runs after this hour of the next day."""

_scanned: set[tuple[str, str]] = set()


async def on_tick(
    app_state: Any, members: Sequence[Conversation], now: float | None = None
) -> None:
    """The supervisor hook. Never raises."""
    from omnigent.server.factory import eval_mode, evaluate, retro, tuning
    from omnigent.server.factory.telemetry import metrics

    now = now if now is not None else time.time()
    factories = sorted({m.labels[LABEL_ID] for m in members if m.labels.get(LABEL_ID)})
    stamp = datetime.fromtimestamp(now, tz=UTC)
    yesterday = (stamp - timedelta(days=1)).strftime("%Y-%m-%d")
    for factory_id in factories:
        steps = (
            ("expiry", lambda f=factory_id: tuning.expire_due(app_state, f, now=int(now))),
            ("trials", lambda f=factory_id: evaluate.tick(app_state, f, now=int(now))),
            ("retro", lambda f=factory_id: retro.tick(app_state, f, now)),
            ("eval", lambda f=factory_id: eval_mode.tick(app_state, f, now)),
        )
        for name, step in steps:
            try:
                await step()
            except Exception:  # noqa: BLE001 - one step must not stop the others
                _logger.warning(
                    "factory learning: %s failed for %s", name, factory_id, exc_info=True
                )
        if stamp.hour >= SCAN_AFTER_H and (factory_id, yesterday) not in _scanned:
            try:
                await metrics.record_prompt_scan(app_state, factory_id, yesterday)
                _scanned.add((factory_id, yesterday))
            except Exception:  # noqa: BLE001 - retried next tick
                _logger.warning(
                    "factory learning: prompt scan failed for %s", factory_id, exc_info=True
                )


def reset() -> None:
    """Forget the in-process scan memo (tests)."""
    _scanned.clear()


__all__ = ["on_tick", "reset"]
