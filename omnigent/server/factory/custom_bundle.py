"""Factory members never run a bundle of their own making (Tier 0).

``sys_session_create(config_path=…)`` uploads a local bundle as a new child.
That child runs the uploaded spec, not its parent's, so a factory agent could
write a spec without ``factory_role_guard`` and escape its fence. The
session-from-bundle route calls :func:`deny_factory_custom_bundle` for every
parented upload and refuses one whose parent belongs to a factory, and the
JSON create calls :func:`deny_factory_child_agent` so a child cannot bind such
an upload's agent by id afterwards.

A runner (an agent's callback) also cannot change the agent a member runs in
place: :func:`deny_runner_member_rebind` keeps a switch-agent on a factory
agent of the member's role, and :func:`deny_runner_member_bundle` refuses a
new bundle for it.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from omnigent.factory import LABEL_ID, LABEL_ROLE, factory_role_from_spec

if TYPE_CHECKING:
    from fastapi import Request

    from omnigent.entities import Agent, Conversation
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


async def deny_factory_child_agent(
    parent_session_id: str, agent: Agent, conversation_store: ConversationStore
) -> None:
    """
    Refuse a child of a factory member bound to an agent an upload made.

    The JSON create binds an existing agent by id, so an upload made earlier
    (top-level, outside the factory) would otherwise reach a factory child in a
    second step. A session-scoped agent passes only when it is what the parent
    or its root already runs: the agent a ``sys_session_send`` sub-agent binds.

    :param parent_session_id: The create's ``parent_session_id``.
    :param agent: The agent the child would run.
    :param conversation_store: Store for the parent and root lookup.
    :raises FactoryRouteError: 403 ``factory_custom_bundle_denied``.
    """
    if agent.session_id is None:
        return
    from omnigent.server.factory.membership import resolve_factory_id, root_conversation
    from omnigent.server.factory.routes import FactoryRouteError

    def _member_agents() -> set[str | None] | None:
        parent = conversation_store.get_conversation(parent_session_id)
        if parent is None or not resolve_factory_id(parent, conversation_store):
            return None
        return {parent.agent_id, root_conversation(parent, conversation_store).agent_id}

    member_agents = await asyncio.to_thread(_member_agents)
    if member_agents is not None and agent.id not in member_agents:
        raise FactoryRouteError(
            403,
            FACTORY_CUSTOM_BUNDLE_DENIED,
            "factory members cannot run a custom bundle: a child of a factory member runs "
            "a built-in agent or its parent's own; dispatch a team member with "
            "sys_session_send(agent=…) or ask your lead",
        )


def deny_runner_member_rebind(
    request: Request, conv: Conversation, target_spec: Any, auth_provider: object
) -> None:
    """
    Refuse a runner switching a factory member to an agent of another factory role.

    :param request: The switch-agent request.
    :param conv: The session being switched.
    :param target_spec: The target agent's parsed spec.
    :param auth_provider: The server's auth provider, for ``runner_origin``.
    :raises FactoryRouteError: 403 ``factory_agent_required``.
    """
    from omnigent.server.auth import runner_origin
    from omnigent.server.factory.routes import FactoryRouteError

    if not conv.labels.get(LABEL_ID) or runner_origin(request, auth_provider) is None:
        return
    role = conv.labels.get(LABEL_ROLE)
    if role is None or factory_role_from_spec(target_spec) != role:
        raise FactoryRouteError(
            403,
            "factory_agent_required",
            f"a factory member runs a factory agent of its role ({role!r}); an agent "
            "cannot switch it to another agent",
        )


async def deny_runner_member_bundle(
    request: Request,
    conv: Conversation,
    auth_provider: object,
    conversation_store: ConversationStore,
) -> None:
    """
    Refuse a runner replacing the bundle a factory member (or its sub-agent) runs.

    :param request: The bundle-update request.
    :param conv: The session whose agent bundle would change.
    :param auth_provider: The server's auth provider, for ``runner_origin``.
    :param conversation_store: Store for the root lookup.
    :raises FactoryRouteError: 403 ``factory_custom_bundle_denied``.
    """
    from omnigent.server.auth import runner_origin
    from omnigent.server.factory.membership import resolve_factory_id
    from omnigent.server.factory.routes import FactoryRouteError

    if runner_origin(request, auth_provider) is None:
        return
    if await asyncio.to_thread(resolve_factory_id, conv, conversation_store):
        raise FactoryRouteError(
            403,
            FACTORY_CUSTOM_BUNDLE_DENIED,
            "factory members cannot run a custom bundle; an agent cannot replace a "
            "member's bundle",
        )
