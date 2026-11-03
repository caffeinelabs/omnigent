"""The ``team`` source: what the factory's own people say becomes hunch signals.

A person's Room message is kept as a ``hunch`` (weight 0.1, never user
evidence) when it is one (:func:`omnigent.server.factory.chat_routing.team_signal`):
``hunch: …`` anywhere, and in ``#factory`` "I think … because …" or a
request such as "we need …". Scout later attaches it to an opportunity, and the
ledger compares the hit rate of requested work with discovered work.

Nothing is kept while discovery is off for the factory, or while its
``team`` source is paused.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Final

from omnigent.server.factory import chat_routing, definition, events
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.control import CockpitError, clip, human_name
from omnigent.server.factory.discovery import ledger

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryEvent

_logger = logging.getLogger(__name__)

POSTED_KIND: Final = "chat.posted"


def _store(app_state: Any, event: FactoryEvent) -> tuple[Any, str] | None:
    deps = factory_deps(app_state)
    data = event.data or {}
    if data.get("by_kind") != "human" or not data.get("message_id"):
        return None
    if not definition.discovery_enabled(deps, event.factory_id):
        return None
    message = deps.factory_store.get_chat_message(str(data["message_id"]))
    if message is None or message.author_kind != "human":
        return None
    found = chat_routing.team_signal(message.thread, message.body)
    if found is None:
        return None
    kind, text = found
    source = ledger.team_source(deps, event.factory_id)
    if source.state == "paused":
        return None
    try:
        row, existed = ledger.add_conviction(
            deps,
            event.factory_id,
            text=text,
            author=message.author,
            author_kind="human",
            pointer=f"chat:{message.id}",
        )
    except CockpitError as exc:
        _logger.info("factory %s: Room %s not kept as a hunch: %s", event.factory_id, kind, exc)
        return None
    return (None, kind) if existed else (row, kind)


async def on_event(app_state: Any, event: FactoryEvent) -> None:
    """Event listener: keep a person's Room hunch or request as a team signal."""
    if event.kind != POSTED_KIND:
        return
    stored = await asyncio.to_thread(_store, app_state, event)
    if stored is None or stored[0] is None:
        return
    row, kind = stored
    who = human_name(row.author)
    await events.emit(
        app_state,
        event.factory_id,
        "hunch.added",
        subject=f"signal:{row.ref}",
        summary=f"{who}'s Room {kind} kept as hunch {row.ref}: {clip(row.excerpt or '', 140)}",
        data={
            "signal": row.ref,
            "by": row.author,
            "by_kind": "human",
            "from": "room",
            "request": kind == "request",
            "message_id": (event.data or {}).get("message_id"),
        },
    )


def install() -> Callable[[], None]:
    """Listen for Room posts. :returns: A callable that stops listening."""
    return events.add_listener(on_event)


__all__ = ["install", "on_event"]
