"""Live cockpit updates: a debounced ``factory_changed`` frame per factory.

The frame rides the existing ``WS /v1/sessions/updates`` stream (the
``user_session_stream`` discovery channel) to the factory's owner and every
user granted access to its root; clients then refetch the snapshot. Bursts are
coalesced: at most one frame per factory per :data:`DEBOUNCE_S`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from omnigent.runtime import user_session_stream
from omnigent.stores.factory_store import norm_id

_logger = logging.getLogger(__name__)

DEBOUNCE_S = 0.5
FRAME_TYPE = "factory_changed"


@dataclass
class _Pending:
    scopes: set[str] = field(default_factory=set)
    task: asyncio.Task[None] | None = None


# Keyed by (event loop, factory id) so tests on fresh loops never share a timer.
_pending: dict[tuple[int, str], _Pending] = {}


def recipients(app_state: Any, factory_id: str) -> list[str | None]:
    """
    Users who should see *factory_id* change: its owner and every grantee.

    A public share is not a user, so it gets no frame.

    :returns: Distinct user ids, or ``[None]`` in single-user mode.
    """
    from omnigent.server.auth import LEVEL_OWNER, RESERVED_USER_PUBLIC
    from omnigent.server.factory.context import FACTORY_STATE_ATTR

    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    permission_store = getattr(deps, "permission_store", None)
    if permission_store is None:
        return [None]
    grants, _ = permission_store.list_for_session(factory_id, limit=1000)
    # Owner first (as get_session_owner_id picks it), then the other grantees.
    owners = [g for g in grants if g.level >= LEVEL_OWNER][:1]
    users: list[str | None] = []
    for grant in [*owners, *grants]:
        if grant.user_id != RESERVED_USER_PUBLIC and grant.user_id not in users:
            users.append(grant.user_id)
    return users


def _bare_id(factory_id: str) -> str:
    """*factory_id* as the bare hex the snapshot uses; an invalid id passes through."""
    try:
        return norm_id(factory_id)
    except ValueError:
        return factory_id


def publish(factory_id: str, scopes: Iterable[str], users: Iterable[str | None]) -> None:
    """Push one ``factory_changed`` frame to each user's session-updates streams."""
    from omnigent.server.routes._sessions.helpers import _discovery_key

    frame = {
        "type": FRAME_TYPE,
        "factory_id": _bare_id(factory_id),
        "scopes": sorted(set(scopes)),
    }
    for user in users:
        user_session_stream.publish(_discovery_key(user), dict(frame))


async def _flush_later(app_state: Any, key: tuple[int, str]) -> None:
    await asyncio.sleep(DEBOUNCE_S)
    pending = _pending.pop(key, None)
    if pending is None:
        return
    factory_id = key[1]
    try:
        users = await asyncio.to_thread(recipients, app_state, factory_id)
        publish(factory_id, pending.scopes, users)
    except Exception:  # noqa: BLE001 - the cockpit falls back to polling
        _logger.warning(
            "factory: factory_changed fan-out failed for %s", factory_id, exc_info=True
        )


def announce_factory_changed(
    app_state: Any, factory_id: str, scopes: Iterable[str] = ("snapshot",)
) -> None:
    """
    Schedule a ``factory_changed`` frame for *factory_id* (debounced 500 ms).

    Calls inside the window merge their scopes into the one pending frame.
    Outside an event loop the frame is published at once.

    :param app_state: ``app.state`` carrying the factory deps.
    :param factory_id: Root lead session id.
    :param scopes: What changed: ``snapshot`` / ``events`` / ``decisions``.
    """
    factory_id = _bare_id(factory_id)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        publish(factory_id, scopes, recipients(app_state, factory_id))
        return
    key = (id(loop), factory_id)
    pending = _pending.get(key)
    if pending is not None and pending.task is not None and not pending.task.done():
        pending.scopes.update(scopes)
        return
    pending = _Pending(scopes=set(scopes))
    _pending[key] = pending
    pending.task = loop.create_task(_flush_later(app_state, key))


__all__ = ["DEBOUNCE_S", "FRAME_TYPE", "announce_factory_changed", "publish", "recipients"]
