"""Factory membership: who belongs to which factory, in what role.

Top-level members carry the ``omnigent.factory.*`` labels themselves.
Sub-agents carry none; they inherit membership from their root conversation
(their project lead) and take their role from their own spec's
``executor.config.factory_role``. Everything here is synchronous store work;
async callers run it under :func:`asyncio.to_thread`.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from omnigent.db.db_models import InvalidUuidError, uuid_to_bytes
from omnigent.factory import (
    DEFAULT_HEARTBEAT_S,
    LABEL_HEALTH,
    LABEL_HEARTBEAT_S,
    LABEL_ID,
    LABEL_PARENT,
    LABEL_PROJECT,
    LABEL_ROLE,
    LABEL_STATE,
    LABEL_SUMMARY,
    LABEL_SUPERVISOR,
    PROJECT_ALIAS_PREFIX,
    ROLE_IC,
    ROLE_LEAD,
    ROLE_PROJECT_LEAD,
    ROLES,
    STATE_ACTIVE,
    TOP_LEVEL_ROLES,
    factory_role_from_spec,
)
from omnigent.runner.identity import token_bound_runner_id
from omnigent.server.factory.context import FactoryDeps

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.spec.types import AgentSpec
    from omnigent.stores import ConversationStore

_logger = logging.getLogger(__name__)


class UnknownTargetError(LookupError):
    """A ``to`` address resolved to no session."""


@dataclass(frozen=True)
class Membership:
    """
    One session's place in a factory (the ``/membership`` response body).

    :param session_id: The session described, e.g. ``"conv_pl1"``.
    :param factory_id: Factory root session id, or ``None`` for non-members.
    :param role: ``lead`` / ``project_lead`` (top-level) or the sub-agent's
        spec role (``ic`` by default). ``None`` for non-members.
    :param project: Project slug (a sub-agent reports its root's).
    :param parent_session_id: Spawning member (top-level) or the sub-agent's
        parent conversation.
    :param supervisor_session_id: Who receives alerts: the supervisor label
        (top-level) or the parent conversation (sub-agent).
    :param state: ``active`` / ``paused`` / ``done`` (top-level only).
    :param heartbeat_s: LLM heartbeat interval (top-level only).
    :param health: Beacon health, e.g. ``"ok"``.
    :param summary: Beacon summary line.
    :param is_top_level: ``True`` for sessions without a parent conversation.
    :param root_session_id: The spawn-tree root (self for top-level).
    :param title: Session title.
    """

    session_id: str
    factory_id: str | None
    role: str | None
    project: str | None
    parent_session_id: str | None
    supervisor_session_id: str | None
    state: str | None
    heartbeat_s: int | None
    health: str | None
    summary: str | None
    is_top_level: bool
    root_session_id: str
    title: str | None

    def to_dict(self) -> dict[str, Any]:
        """:returns: The JSON body of ``GET /v1/factory/membership``."""
        return asdict(self)


def is_session_id(value: str) -> bool:
    """:returns: Whether *value* is shaped like a session id (a hex uuid)."""
    try:
        uuid_to_bytes(value)
    except InvalidUuidError:
        return False
    return True


def is_top_level(conv: Conversation) -> bool:
    """:returns: ``True`` when *conv* has no parent conversation."""
    return conv.parent_conversation_id is None


def root_conversation(conv: Conversation, store: ConversationStore) -> Conversation:
    """
    Return the spawn-tree root of *conv* (itself when top-level).

    :param conv: Any session row.
    :param store: Store for the root lookup.
    :returns: The root row, or *conv* when the root no longer exists.
    """
    if is_top_level(conv) or conv.root_conversation_id == conv.id:
        return conv
    root = store.get_conversation(conv.root_conversation_id)
    return root if root is not None else conv


def resolve_factory_id(conv: Conversation, store: ConversationStore) -> str | None:
    """
    Return the factory *conv* belongs to: its own label, else its root's.

    :param conv: Any session row.
    :param store: Store for the root lookup.
    :returns: The factory root session id, or ``None`` for non-members.
    """
    own = conv.labels.get(LABEL_ID)
    if own:
        return own
    if is_top_level(conv):
        return None
    root = root_conversation(conv, store)
    if root is conv:
        return None
    return root.labels.get(LABEL_ID) or None


def load_session_spec(conv: Conversation, deps: FactoryDeps) -> AgentSpec | None:
    """
    Load the spec *conv* runs: its agent's, or the named sub-agent within it.

    :param conv: Any session row.
    :param deps: Factory stores (agent store + cache).
    :returns: The resolved spec, or ``None`` when unbound or unloadable.
    """
    if conv.agent_id is None or deps.agent_cache is None:
        return None
    agent = deps.agent_store.get(conv.agent_id)
    if agent is None:
        return None
    try:
        spec = deps.agent_cache.load(
            agent.id,
            agent.bundle_location,
            expand_env=agent.session_id is None,
        ).spec
    except Exception:  # noqa: BLE001 - a broken bundle only costs the role lookup
        _logger.warning(
            "factory: could not load the spec of agent %s for session %s",
            agent.id,
            conv.id,
            exc_info=True,
        )
        return None
    if not conv.sub_agent_name:
        return spec
    from omnigent.runtime.workflow import _find_spec_by_name

    return _find_spec_by_name(spec, conv.sub_agent_name)


def sub_agent_role(spec: AgentSpec | None) -> str:
    """
    Return a sub-agent's factory role: its spec's, but never a top-level one.

    ``lead`` and ``project_lead`` belong to top-level members, whose labels a
    spawn sets. A child that runs a lead's or a PL's spec (a factory member can
    create one with ``sys_session_create(agent_id=…)``) is an ``ic``, so it
    never acts with its parent's or a lead's powers.

    :param spec: The sub-agent's resolved spec, or ``None``.
    :returns: e.g. ``"reviewer"``; ``"ic"`` when the spec declares no role or a
        top-level one.
    """
    role = factory_role_from_spec(spec)
    return ROLE_IC if role is None or role in TOP_LEVEL_ROLES else role


def resolve_role(conv: Conversation, deps: FactoryDeps) -> str | None:
    """
    Return *conv*'s factory role.

    :param conv: A factory member.
    :param deps: Factory stores.
    :returns: The role label (top-level) or the sub-agent's spec role.
    """
    if is_top_level(conv):
        role = conv.labels.get(LABEL_ROLE)
        return role if role in ROLES else None
    return sub_agent_role(load_session_spec(conv, deps))


def runner_token_binds(
    token: str | None,
    session: Conversation,
    store: ConversationStore,
    allow_list: frozenset[str] | None,
) -> bool:
    """
    Whether a runner tunnel token may act for *session*.

    No token, or an allow-listed one (it proves a runner with a stable id, as on
    the tunnel route), passes. Otherwise the token's runner must run *session*
    (or, for a sub-agent, its root).

    :param token: The runner tunnel token header value, or ``None``.
    :param session: The session the caller claims to act for.
    :param store: Store for a sub-agent's root lookup.
    :param allow_list: The server's allow-listed tunnel tokens, if any.
    :returns: ``False`` when the token belongs to another runner.
    """
    if token is None or (allow_list is not None and token.strip() in allow_list):
        return True
    bound = token_bound_runner_id(token) if token.strip() else None
    allowed = {session.runner_id}
    if not is_top_level(session):
        allowed.add(root_conversation(session, store).runner_id)
    allowed.discard(None)
    return bound is not None and bound in allowed


def ensure_member(conv: Conversation, deps: FactoryDeps) -> Conversation:
    """
    Lazily enrol a top-level session whose spec declares a factory role.

    The first factory call from such a session (e.g. Lead A, created in the
    web UI) stamps the factory id (itself, or its spawning parent's factory),
    role, ``state=active`` and the role's default heartbeat. Labels that are
    already present are kept.

    :param conv: The calling session.
    :param deps: Factory stores.
    :returns: The (possibly re-read) session row.
    """
    if not is_top_level(conv) or conv.labels.get(LABEL_ID):
        return conv
    store = deps.conversation_store
    spec_role = factory_role_from_spec(load_session_spec(conv, deps))
    if spec_role is None or spec_role not in TOP_LEVEL_ROLES:
        return conv
    factory_id: str | None = conv.id
    parent_id = conv.labels.get(LABEL_PARENT)
    if parent_id:
        parent = store.get_conversation(parent_id)
        factory_id = resolve_factory_id(parent, store) if parent is not None else None
    if not factory_id:
        return conv
    role = conv.labels.get(LABEL_ROLE) or spec_role
    updates: dict[str, str] = {LABEL_ID: factory_id}
    if not conv.labels.get(LABEL_ROLE):
        updates[LABEL_ROLE] = role
    if not conv.labels.get(LABEL_STATE):
        updates[LABEL_STATE] = STATE_ACTIVE
    if not conv.labels.get(LABEL_HEARTBEAT_S):
        updates[LABEL_HEARTBEAT_S] = str(DEFAULT_HEARTBEAT_S.get(role, 0))
    store.set_labels(conv.id, updates)
    _logger.info("factory: enrolled %s as %s of factory %s", conv.id, role, factory_id)
    refreshed = store.get_conversation(conv.id)
    return refreshed if refreshed is not None else conv


def _int_label(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def membership_of(conv: Conversation, deps: FactoryDeps) -> Membership:
    """
    Describe *conv*'s factory membership.

    :param conv: Any session row.
    :param deps: Factory stores.
    :returns: Its :class:`Membership` (``factory_id=None`` for non-members).
    """
    store = deps.conversation_store
    top = is_top_level(conv)
    root = conv if top else root_conversation(conv, store)
    factory_id = conv.labels.get(LABEL_ID) or (None if top else root.labels.get(LABEL_ID))
    factory_id = factory_id or None
    role = resolve_role(conv, deps) if factory_id else None
    labels = conv.labels
    return Membership(
        session_id=conv.id,
        factory_id=factory_id,
        role=role,
        project=labels.get(LABEL_PROJECT) or (None if top else root.labels.get(LABEL_PROJECT)),
        parent_session_id=labels.get(LABEL_PARENT) if top else conv.parent_conversation_id,
        supervisor_session_id=labels.get(LABEL_SUPERVISOR) if top else conv.parent_conversation_id,
        state=labels.get(LABEL_STATE) if top else None,
        heartbeat_s=_int_label(labels.get(LABEL_HEARTBEAT_S)) if top else None,
        health=labels.get(LABEL_HEALTH),
        summary=labels.get(LABEL_SUMMARY),
        is_top_level=top,
        root_session_id=root.id,
        title=conv.title,
    )


def factory_members(factory_id: str, store: ConversationStore) -> list[Conversation]:
    """
    Return the top-level members of a factory, oldest first.

    :param factory_id: Factory root session id.
    :param store: Store for the label index and batched row read.
    :returns: Non-archived top-level sessions labelled with *factory_id*.
    """
    ids = store.list_conversation_ids_with_label(LABEL_ID, factory_id)
    rows = store.get_conversations(ids)
    return [rows[i] for i in ids if i in rows and is_top_level(rows[i])]


def _replacement_rank(conv: Conversation) -> tuple[bool, int]:
    """Sort key preferring an active member, then the newest (a replacement)."""
    return conv.labels.get(LABEL_STATE) != STATE_ACTIVE, -conv.created_at


def _peer_lead(sender: Membership, store: ConversationStore) -> str | None:
    if sender.factory_id is None:
        return None
    leads = [
        m
        for m in factory_members(sender.factory_id, store)
        if m.labels.get(LABEL_ROLE) == ROLE_LEAD and m.id != sender.root_session_id
    ]
    if sender.role != ROLE_LEAD:
        # From a non-lead, the peer lead is the one that is not the root.
        leads = [m for m in leads if m.id != sender.factory_id]
    leads.sort(key=_replacement_rank)
    return leads[0].id if leads else None


def _project_lead(sender: Membership, slug: str, store: ConversationStore) -> str | None:
    if not slug:
        return None
    ids = store.list_conversation_ids_with_label(LABEL_PROJECT, slug)
    rows = store.get_conversations(ids)
    matches = [
        rows[i]
        for i in ids
        if i in rows
        and is_top_level(rows[i])
        and rows[i].labels.get(LABEL_ID) == sender.factory_id
    ]
    # Recovery replaces a failing PL with a new one and pauses the old one.
    matches.sort(
        key=lambda c: (c.labels.get(LABEL_ROLE) != ROLE_PROJECT_LEAD, *_replacement_rank(c))
    )
    return matches[0].id if matches else None


def resolve_target(to: str, sender: Membership, store: ConversationStore) -> Conversation:
    """
    Resolve a ``to`` address (session id or alias) to a session row.

    Aliases: ``parent``, ``lead`` (the factory root), ``peer_lead``,
    ``supervisor`` and ``project:<slug>``. Where an alias matches several
    members, an ``active`` one wins over a paused/done one, then the newest.
    Factory scoping is the caller's job; this only answers "which session
    does the address name".

    :param to: The address, e.g. ``"conv_abc"`` or ``"project:api-version"``.
    :param sender: The sender's membership (must be a member).
    :param store: Store for label and row lookups.
    :returns: The target session row.
    :raises UnknownTargetError: When the address names no session.
    """
    address = to.strip()
    target_id: str | None
    if address == "parent":
        target_id = sender.parent_session_id
    elif address == "lead":
        target_id = sender.factory_id
    elif address == "supervisor":
        target_id = sender.supervisor_session_id
        if target_id is None and sender.factory_id != sender.session_id:
            target_id = sender.factory_id
    elif address == "peer_lead":
        target_id = _peer_lead(sender, store)
    elif address.startswith(PROJECT_ALIAS_PREFIX):
        target_id = _project_lead(sender, address[len(PROJECT_ALIAS_PREFIX) :].strip(), store)
    else:
        target_id = address or None
    if not target_id or not is_session_id(target_id):
        raise UnknownTargetError(f"{to!r} does not name a session")
    target = store.get_conversation(target_id)
    if target is None:
        raise UnknownTargetError(f"{to!r} does not name a session")
    return target
