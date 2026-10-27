"""Factory members never run a bundle of their own making (Tier 0).

``sys_session_create(config_path=…)`` uploads a local bundle as a new child.
That child runs the uploaded spec, not its parent's, so a factory agent could
write a spec without ``factory_role_guard`` and escape its fence. The
session-from-bundle route calls :func:`deny_factory_custom_bundle` for every
parented upload and refuses one whose parent belongs to a factory.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from omnigent.stores import ConversationStore

FACTORY_CUSTOM_BUNDLE_DENIED = "factory_custom_bundle_denied"


async def deny_factory_custom_bundle(
    parent_session_id: str, conversation_store: ConversationStore
) -> None:
    """
    Refuse a bundle upload whose parent (or its root) carries ``omnigent.factory.id``.

    :param parent_session_id: The upload's ``parent_session_id``.
    :param conversation_store: Store for the parent and root lookup.
    :raises FactoryRouteError: 403 ``factory_custom_bundle_denied``.
    """
    from omnigent.server.factory.membership import resolve_factory_id
    from omnigent.server.factory.routes import FactoryRouteError

    parent = await asyncio.to_thread(conversation_store.get_conversation, parent_session_id)
    if parent is None:
        return
    factory_id = await asyncio.to_thread(resolve_factory_id, parent, conversation_store)
    if factory_id:
        raise FactoryRouteError(
            403,
            FACTORY_CUSTOM_BUNDLE_DENIED,
            "factory members cannot run a custom bundle (sys_session_create with "
            "config_path); dispatch a team member with sys_session_send(agent=…) or ask "
            "your lead",
        )
