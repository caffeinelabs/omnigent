"""Seed the delivery profile of factories from before it, once, at boot.

A factory started before ``product.delivery`` existed gets the deployment's
delivery fields (:data:`omnigent.server.factory.product_profile.DELIVERY_SEED_ENV`)
as one seed version, so what the server and the cockpit read from the
profile matches what they did before (:func:`.product_profile.seed_existing`).
It runs in the background, one factory at a time under its definition lock,
and never again for a factory it marked.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps

_logger = logging.getLogger(__name__)


async def seed_all(app_state: Any) -> list[str]:
    """
    Seed every factory root that still needs it.

    :param app_state: ``app.state`` carrying the factory deps.
    :returns: The ids of the factories that got a seed version.
    """
    from omnigent.server.factory import product_profile
    from omnigent.server.factory.snapshot import list_factory_roots
    from omnigent.server.factory.tuning import definition_lock

    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps) or not product_profile.seed_values():
        return []
    roots = await asyncio.to_thread(list_factory_roots, deps.conversation_store)
    seeded: list[str] = []
    for root in roots:
        if root.labels.get(product_profile.LABEL_DELIVERY_SEED):
            continue
        try:
            async with definition_lock(app_state, root.id):
                version = await asyncio.to_thread(product_profile.seed_existing, deps, root)
        except Exception:  # noqa: BLE001 - one factory's failure never stops the others
            _logger.warning("factory %s: could not seed product.delivery", root.id, exc_info=True)
            continue
        if version is not None:
            seeded.append(root.id)
    return seeded


async def start(app_state: Any) -> Callable[[], Awaitable[None]]:
    """Start the one-shot seed; :returns: a callable that cancels it if still running."""
    task = asyncio.create_task(seed_all(app_state), name="factory-delivery-seed")

    async def _stop() -> None:
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    return _stop


__all__ = ["seed_all", "start"]
