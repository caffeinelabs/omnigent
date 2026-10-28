"""Runners create factory members only the way a spawn does.

A runner bearer authenticates as the session owner, so ``POST /v1/sessions``
alone would let an agent mint a session carrying server-only factory labels,
a role no spawn may create, or a place in another factory. A runner-origin
create that names any ``omnigent.factory.*`` label must instead look like the
body ``sys_factory_spawn`` sends (:func:`omnigent.factory_spawn.member_labels`):

- only the spawn labels, never a server-only one (422 ``server_only_label``)
  or any other factory label (422 ``factory_label_not_allowed``);
- a role in :data:`~omnigent.factory.SPAWN_ROLES` (403 ``spawn_role_not_allowed``);
- a top-level session: never a ``parent_session_id`` (422 ``factory_member_has_parent``);
- a parent that is a lead of the named factory, which the caller may edit and,
  over a tunnel, whose runner the caller is (403 ``session_out_of_factory`` /
  ``sender_not_bound``), and a supervisor in the same factory;
- never a bundle of its own, uploaded with the create or bound by the id of a
  session-scoped agent an earlier upload made (403 ``factory_custom_bundle_denied``);
  a spawn always names a built-in or template agent by ``agent_id`` (422
  ``agent_required`` otherwise);
- what ``sys_factory_spawn`` checks before it creates (409): no second live PL
  of the project (``duplicate_project``), no pod over the parent's
  ``factory_max_pods`` (``pod_cap``) and no PL of a server-spawned project held
  on its dependencies (``server_owns_spawn``).

People's creates are not checked here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import TYPE_CHECKING, Final

from omnigent.factory import (
    LABEL_BUNDLE,
    LABEL_HEARTBEAT_S,
    LABEL_ID,
    LABEL_PARENT,
    LABEL_PREFIX,
    LABEL_PROJECT,
    LABEL_ROLE,
    LABEL_STATE,
    LABEL_SUPERVISOR,
    LABEL_VERSION,
    ROLE_LEAD,
    ROLE_PROJECT_LEAD,
    SPAWN_ROLES,
    STATES,
    is_factory_label,
    valid_heartbeat_s,
)
from omnigent.runner.identity import RUNNER_TUNNEL_TOKEN_HEADER
from omnigent.server.auth import LEVEL_EDIT, runner_origin
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.custom_bundle import FACTORY_CUSTOM_BUNDLE_DENIED
from omnigent.server.factory.membership import is_session_id, is_top_level, runner_token_binds
from omnigent.server.factory.routes import SERVER_ONLY_LABEL_FIELDS, FactoryRouteError
from omnigent.server.routes._auth_helpers import require_access

if TYPE_CHECKING:
    from fastapi import Request

    from omnigent.entities import Conversation
    from omnigent.stores import ConversationStore
    from omnigent.stores.agent_store import AgentStore
    from omnigent.stores.permission_store import PermissionStore

SPAWN_LABELS: Final = frozenset(
    {
        LABEL_ID,
        LABEL_ROLE,
        LABEL_PARENT,
        LABEL_SUPERVISOR,
        LABEL_STATE,
        LABEL_HEARTBEAT_S,
        LABEL_PROJECT,
        LABEL_VERSION,
        LABEL_BUNDLE,
    }
)
"""The factory labels a spawn creates a member with; a runner may send no others."""


def _factory_keys(labels: Mapping[str, str] | None) -> list[str]:
    """:returns: The ``omnigent.factory.*`` keys of *labels* (any case or padding), sorted."""
    return sorted(key for key in labels or {} if is_factory_label(key))


async def check_runner_create(
    request: Request,
    labels: Mapping[str, str] | None,
    *,
    user_id: str | None,
    auth_provider: object,
    conversation_store: ConversationStore,
    permission_store: PermissionStore | None,
    runner_tunnel_tokens: frozenset[str] | None,
    agent_store: AgentStore,
    agent_id: str | None,
    parent_session_id: str | None = None,
    bundled: bool = False,
) -> None:
    """
    Refuse a runner-origin session create whose factory labels a spawn would not send.

    :param request: The create request.
    :param labels: The requested labels, e.g. ``{"omnigent.factory.role": "project_lead"}``.
    :param user_id: The authenticated caller (the session owner a runner acts as).
    :param auth_provider: The server's auth provider, for :func:`runner_origin`.
    :param conversation_store: Store for the parent and supervisor lookups.
    :param permission_store: Store for the caller's access to the parent.
    :param runner_tunnel_tokens: The server's allow-listed tunnel tokens, if any.
    :param agent_store: Store for the *agent_id* lookup.
    :param agent_id: The agent a JSON create binds, e.g. the ``factory-pl`` row's id;
        ``None`` for a multipart create (or a project-filled one).
    :param parent_session_id: The create's ``parent_session_id``, if any.
    :param bundled: Whether the create uploads its own bundle (multipart).
    :raises FactoryRouteError: When the create is refused.
    """
    keys = _factory_keys(labels)
    if not keys or labels is None or runner_origin(request, auth_provider) is None:
        return
    server_only = sorted(
        short
        for short in (key.removeprefix(LABEL_PREFIX) for key in keys)
        if short in SERVER_ONLY_LABEL_FIELDS
    )
    if server_only:
        raise FactoryRouteError(
            422,
            "server_only_label",
            f"{', '.join(server_only)} can only be set by the server or a factory owner, "
            "never by an agent; propose the change instead",
        )
    other = [key for key in keys if key not in SPAWN_LABELS]
    if other:
        raise FactoryRouteError(
            422,
            "factory_label_not_allowed",
            f"labels {', '.join(other)} are not set when a factory member is created; "
            "the member sets its own with sys_factory_set once it runs",
        )
    if parent_session_id is not None:
        raise FactoryRouteError(
            422,
            "factory_member_has_parent",
            "a factory member is a top-level session: sys_factory_spawn never sets "
            "parent_session_id, and sub-agents take their role from their spec",
        )
    await _check_agent(bundled, agent_id, agent_store)
    _check_values(labels)
    parent = await _spawning_lead(labels, conversation_store)
    await require_access(user_id, parent.id, LEVEL_EDIT, permission_store, conversation_store)
    token = request.headers.get(RUNNER_TUNNEL_TOKEN_HEADER)
    if token is not None and not await asyncio.to_thread(
        runner_token_binds, token, parent, conversation_store, runner_tunnel_tokens
    ):
        raise FactoryRouteError(
            403, "sender_not_bound", "the runner token does not belong to the parent lead"
        )
    supervisor = labels.get(LABEL_SUPERVISOR)
    if supervisor is not None:
        await _same_factory_member(supervisor, labels[LABEL_ID], conversation_store)
    deps = getattr(request.app.state, FACTORY_STATE_ATTR, None)
    await asyncio.to_thread(
        _check_room,
        labels,
        parent,
        conversation_store,
        deps if isinstance(deps, FactoryDeps) else None,
    )


async def _check_agent(bundled: bool, agent_id: str | None, store: AgentStore) -> None:
    """Refuse an uploaded bundle, or a bound agent that an upload made (session-scoped)."""
    if not bundled and agent_id is None:
        raise FactoryRouteError(
            422,
            "agent_required",
            "a factory member is created with agent_id naming a built-in agent; spawn it "
            "with sys_factory_spawn",
        )
    agent = None if agent_id is None else await asyncio.to_thread(store.get, agent_id)
    if bundled or (agent is not None and agent.session_id is not None):
        raise FactoryRouteError(
            403,
            FACTORY_CUSTOM_BUNDLE_DENIED,
            "factory members cannot run a custom bundle; spawn a built-in agent with "
            "sys_factory_spawn",
        )


def _check_room(
    labels: Mapping[str, str],
    lead: Conversation,
    store: ConversationStore,
    deps: FactoryDeps | None,
) -> None:
    """
    Refuse what ``sys_factory_spawn`` refuses before its create (runner/factory_dispatch.py).

    Counts pods the way the server's own PL spawn does (``spawn.live_members``), so
    neither spawn is refused here when its own check passed. Without factory deps
    (or a loadable lead spec) only the duplicate check runs.

    :param labels: The create's labels, already checked against a spawn's.
    :param lead: The parent lead, whose spec sets ``factory_max_pods``.
    :param store: Store for the factory's members.
    :param deps: The factory's stores, or ``None`` when the factory routes are off.
    """
    from omnigent.server.factory import spawn

    factory_id = labels[LABEL_ID]
    project = labels.get(LABEL_PROJECT) or ""
    members = spawn.live_members(factory_id, store)
    twin = next(
        (
            m
            for m in members
            if project
            and m.labels.get(LABEL_PROJECT) == project
            and m.labels.get(LABEL_ROLE) == ROLE_PROJECT_LEAD
        ),
        None,
    )
    if twin is not None:
        raise FactoryRouteError(
            409,
            "duplicate_project",
            f"project {project!r} already has a live PL ({twin.id}): message it with "
            "sys_session_post, or mark it done first",
        )
    if deps is None:
        return
    cap = spawn.pod_cap(deps, lead)
    if cap is not None and len(members) >= cap:
        raise FactoryRouteError(
            409,
            "pod_cap",
            f"the factory runs {len(members)} live members (factory_max_pods {cap}); "
            "mark finished members state=done or reuse an existing project lead",
        )
    if project and _server_holds(deps, factory_id, project):
        raise FactoryRouteError(
            409,
            "server_owns_spawn",
            f"the server spawns project {project!r}'s PL itself once its dependencies "
            "are met; wait for its [Factory notice · PL·<slug> spawned …]",
        )


def _server_holds(deps: FactoryDeps, factory_id: str, slug: str) -> bool:
    """Whether *slug* is a server-spawned project held on its dependencies."""
    from omnigent.server.factory.charter import SPAWN_SERVER, spawn_owner
    from omnigent.server.factory.gating import unmet_dependencies

    store = deps.factory_store
    rows = {p.slug: p for p in store.list_projects(factory_id)}
    row = rows.get(slug)
    if row is None or spawn_owner(row) != SPAWN_SERVER:
        return False
    return bool(unmet_dependencies(row, rows, store.list_prs(factory_id)))


def _check_values(labels: Mapping[str, str]) -> None:
    """Refuse a role, state or heartbeat no spawn would send."""
    role = labels.get(LABEL_ROLE)
    if role not in SPAWN_ROLES:
        raise FactoryRouteError(
            403,
            "spawn_role_not_allowed",
            f"a factory member is created as one of {list(SPAWN_ROLES)}, not {role!r}; "
            "sub-agents take their role from their spec",
        )
    state = labels.get(LABEL_STATE)
    if state is not None and state not in STATES:
        raise FactoryRouteError(400, "invalid_state", f"state must be one of {sorted(STATES)}")
    heartbeat = labels.get(LABEL_HEARTBEAT_S)
    if heartbeat is not None and not (heartbeat.isdigit() and valid_heartbeat_s(int(heartbeat))):
        raise FactoryRouteError(
            400, "invalid_heartbeat_s", "heartbeat_s must be 0 or between 120 and 86400"
        )


async def _spawning_lead(labels: Mapping[str, str], store: ConversationStore) -> Conversation:
    """:returns: The parent, which must be a top-level lead of the factory the labels name."""
    factory_id = labels.get(LABEL_ID) or ""
    parent_id = labels.get(LABEL_PARENT) or ""
    parent = (
        await asyncio.to_thread(store.get_conversation, parent_id)
        if factory_id and is_session_id(parent_id)
        else None
    )
    if (
        parent is None
        or not is_top_level(parent)
        or parent.labels.get(LABEL_ID) != factory_id
        or parent.labels.get(LABEL_ROLE) != ROLE_LEAD
    ):
        raise FactoryRouteError(
            403,
            "session_out_of_factory",
            "a factory member is created with omnigent.factory.parent naming a lead of the "
            "factory in omnigent.factory.id; spawn it with sys_factory_spawn",
        )
    return parent


async def _same_factory_member(session_id: str, factory_id: str, store: ConversationStore) -> None:
    """Refuse a supervisor that is not a top-level member of *factory_id*."""
    row = (
        await asyncio.to_thread(store.get_conversation, session_id)
        if is_session_id(session_id)
        else None
    )
    if row is None or not is_top_level(row) or row.labels.get(LABEL_ID) != factory_id:
        raise FactoryRouteError(
            403,
            "session_out_of_factory",
            "omnigent.factory.supervisor must be a top-level member of the same factory",
        )


__all__ = ["SPAWN_LABELS", "check_runner_create"]
