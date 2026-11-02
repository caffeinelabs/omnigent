"""Slack DM notifications: the engine's loop (every 30 s).

Inert until an owner saves a Slack bot token for a factory and someone links
their Slack account; ``OMNIGENT_FACTORY_SLACK_DM=0`` switches it off. No
factory code path calls into it: it only reads the store and talks to Slack
from its own task, so a Slack outage delays DMs and nothing else.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any, Final

from omnigent.server.factory.slackdm.engine import Engine

_logger = logging.getLogger(__name__)

ENABLED_ENV: Final = "OMNIGENT_FACTORY_SLACK_DM"
TICK_ENV: Final = "OMNIGENT_FACTORY_SLACK_TICK_S"
TICK_S: Final = 30.0
ENGINE_ATTR: Final = "factory_slack_dm"
"""``app.state`` attribute holding the running :class:`Engine`."""


def enabled() -> bool:
    """:returns: Whether the kill switch leaves the service on (the default)."""
    return os.environ.get(ENABLED_ENV, "1").strip().lower() not in ("0", "false", "off", "no")


def tick_s() -> float:
    """:returns: Seconds between ticks (``OMNIGENT_FACTORY_SLACK_TICK_S``, at least 5)."""
    try:
        return max(5.0, float(os.environ.get(TICK_ENV, "") or TICK_S))
    except ValueError:
        return TICK_S


async def start(app_state: Any) -> Callable[[], Awaitable[None]]:
    """Run the engine every :func:`tick_s` seconds until stopped."""
    if not enabled():
        _logger.info("factory slack: DMs are off (%s=0)", ENABLED_ENV)

        async def _noop() -> None:
            return None

        return _noop
    engine = Engine(app_state)
    setattr(app_state, ENGINE_ATTR, engine)
    interval = tick_s()

    async def _loop() -> None:
        while True:
            try:
                await engine.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("factory slack: tick failed")
            await asyncio.sleep(interval)

    task = asyncio.get_running_loop().create_task(_loop(), name="factory-slack-dm")

    async def stop() -> None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if getattr(app_state, ENGINE_ATTR, None) is engine:
            setattr(app_state, ENGINE_ATTR, None)

    return stop


__all__ = ["ENABLED_ENV", "ENGINE_ATTR", "TICK_ENV", "enabled", "start", "tick_s"]
