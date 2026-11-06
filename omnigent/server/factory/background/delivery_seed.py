"""Seed the delivery and environment profile of factories from before them, once, at boot.

A factory started before ``product.delivery`` (or ``environment``) existed
gets the deployment's fields for its repo
(:data:`omnigent.server.factory.product_profile.DELIVERY_SEED_ENV`,
:data:`~omnigent.server.factory.product_profile.ENVIRONMENT_SEED_ENV`) as one
seed version each, so what the server, the cockpit and the agents read from
the profile matches what they did before
(:func:`.product_profile.seed_existing`). The autonomy level and the
auto-merge targets (:data:`~omnigent.server.factory.product_profile.AUTO_MERGE_SEED_ENV`)
are seeded the same way while no version ever set them. A factory on a repo the seed does
not name is left alone. It runs in the background, one factory at a time
under its definition lock, and never again for a factory it marked. Each
live PL whose charter gains ``Environment`` lines gets them as a CHARTER
UPDATE, since its playbook now points there; so does each PL whose merge
policy line now says the server merges its PRs.
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
    seeds = [s for s in product_profile.SEEDS if product_profile.deployment_seeds(s)]
    if not isinstance(deps, FactoryDeps) or not seeds:
        return []
    roots = await asyncio.to_thread(list_factory_roots, deps.conversation_store)
    seeded: list[str] = []
    for root in roots:
        for seed in seeds:
            if root.labels.get(seed.label):
                continue
            try:
                async with definition_lock(app_state, root.id):
                    version = await asyncio.to_thread(
                        product_profile.seed_existing, deps, root, seed
                    )
            except Exception:  # noqa: BLE001 - one factory's failure never stops the others
                _logger.warning("factory %s: could not seed %s", root.id, seed.path, exc_info=True)
                continue
            if version is None:
                continue
            if root.id not in seeded:
                seeded.append(root.id)
            if seed in (product_profile.ENVIRONMENT, product_profile.AUTO_MERGE):
                # Charter lines: the environment, and who merges PRs into a target base.
                await _announce(app_state, deps, root, version)
    return seeded


async def _announce(app_state: Any, deps: FactoryDeps, root: Any, version: int) -> None:
    """Send each live PL the CHARTER UPDATE the seed version *version* makes (never raises)."""
    from omnigent.server.factory.actor import Actor
    from omnigent.server.factory.projects import announce_delivery

    try:
        row = await asyncio.to_thread(deps.factory_store.get_definition, root.id, version)
        parent = (
            await asyncio.to_thread(deps.factory_store.get_definition, root.id, row.parent_version)
            if row is not None and row.parent_version
            else None
        )
        if row is None or parent is None:
            return
        await announce_delivery(
            app_state, root, parent.doc, row.doc, Actor.server(), via=f"seed v{version}"
        )
    except Exception:  # noqa: BLE001 - the seed stays; the PLs read it on their next brief
        _logger.warning(
            "factory %s: could not announce the seeded profile", root.id, exc_info=True
        )


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
