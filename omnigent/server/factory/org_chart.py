"""Factory members in the Agents panel's session tree.

Leads and project leads run as top-level sessions in their own sandboxes, so
they are not ``parent_conversation_id`` children of the session that spawned
them; only the ``omnigent.factory.parent`` label links them. The child-sessions
route lists them next to real sub-agents (opt-in via ``include_factory``) so
the web Agents panel renders the whole org chart: lead → PLs → ICs.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from omnigent.db.utils import shared_read_scope
from omnigent.factory import LABEL_ID, LABEL_PARENT, LABEL_ROLE
from omnigent.server.auth import LEVEL_READ
from omnigent.server.permissions import check_session_access
from omnigent.server.routes._sessions.common import _session_status_cache
from omnigent.server.routes._sessions.helpers import _child_session_summary_from_conversation
from omnigent.server.routes._sessions.orchestration import (
    _child_session_summaries_from_conversations,
)

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.server.schemas import ChildSessionSummary
    from omnigent.stores import ConversationStore
    from omnigent.stores.permission_store import PermissionStore

_logger = logging.getLogger(__name__)

# A lead spawns at most a pod cap's worth of members; this only bounds the read.
MAX_FACTORY_CHILDREN = 200
# Labels are client-settable, so the read pages past rows the caller cannot
# read (up to this many) before giving up on filling the list.
MAX_FACTORY_SCAN = 2000


def is_factory_session(conv: Conversation) -> bool:
    """
    Whether *conv* is enrolled in a factory.

    Only an enrolled session spawns members (spawning enrols the lead first),
    so the label scan is skipped for every other session.

    :param conv: The would-be parent, e.g. a lead with ``omnigent.factory.id``.
    :returns: ``True`` when it carries a factory id or role label.
    """
    return bool(conv.labels.get(LABEL_ID) or conv.labels.get(LABEL_ROLE))


def _may_read(
    user_id: str | None,
    conv_id: str,
    conversation_store: ConversationStore,
    permission_store: PermissionStore | None,
) -> bool:
    # Same outcome as require_access(LEVEL_READ), the check the route ran on the parent.
    if permission_store is None:
        return True
    if user_id is None:
        return False
    return check_session_access(user_id, conv_id, LEVEL_READ, permission_store, conversation_store)


def _readable_members(
    parent_id: str,
    user_id: str | None,
    title: str | None,
    conversation_store: ConversationStore,
    permission_store: PermissionStore | None,
) -> list[Conversation]:
    """
    Load the readable top-level sessions labelled as spawned by *parent_id*.

    Synchronous and read-only: the caller runs the whole read, permission
    checks included, in one worker thread on one pooled connection.

    :returns: Up to :data:`MAX_FACTORY_CHILDREN` members, oldest first.
    """
    members: list[Conversation] = []
    with shared_read_scope():
        ids = conversation_store.list_conversation_ids_with_label(
            LABEL_PARENT, parent_id, MAX_FACTORY_SCAN
        )
        ids = [i for i in ids if i != parent_id]
        for start in range(0, len(ids), MAX_FACTORY_CHILDREN):
            chunk = ids[start : start + MAX_FACTORY_CHILDREN]
            rows = conversation_store.get_conversations(chunk)
            for conv_id in chunk:
                conv = rows.get(conv_id)
                # Sub-agents already appear as regular children; only top-level members join.
                if conv is None or conv.parent_conversation_id is not None:
                    continue
                if title is not None and conv.title != title:
                    continue
                if not _may_read(user_id, conv_id, conversation_store, permission_store):
                    continue
                members.append(conv)
                if len(members) >= MAX_FACTORY_CHILDREN:
                    return members
    return members


def _member_summary(
    conv: Conversation, summary: ChildSessionSummary, parent_id: str
) -> ChildSessionSummary:
    # The relay cache is empty after a restart; fall back to the persisted status.
    if _session_status_cache.get(conv.id) is None and conv.live_status is not None:
        summary = _child_session_summary_from_conversation(
            conv, parent_id, summary.last_message_preview, cached_status=conv.live_status
        )
    return summary.model_copy(update={"kind": conv.kind})


async def factory_child_summaries(
    parent: Conversation,
    *,
    user_id: str | None,
    conversation_store: ConversationStore,
    permission_store: PermissionStore | None,
    title: str | None = None,
) -> list[ChildSessionSummary]:
    """
    Summarise the top-level sessions labelled as spawned by *parent*.

    Labels are not an ACL: each member is checked for READ through the same
    permission path as the parent, so another user's session that names
    *parent* as its factory parent is never listed. Best-effort: a failure is
    logged and lists no members, so the sub-agent page still renders.

    :param parent: The spawning session, e.g. lead ``"conv_lead_a"``. Only a
        factory session (see :func:`is_factory_session`) has members.
    :param user_id: The caller, or ``None`` when auth is off.
    :param conversation_store: Label index and session rows.
    :param permission_store: Grants, or ``None`` when auth is off.
    :param title: When set, only members whose title equals it (mirrors the
        route's ``{tool}:{session_name}`` filter).
    :returns: One summary per readable, non-archived member, oldest first,
        each carrying its labels and its own ``kind``.
    """
    if not is_factory_session(parent):
        return []
    try:
        members = await asyncio.to_thread(
            _readable_members,
            parent.id,
            user_id,
            title,
            conversation_store,
            permission_store,
        )
        summaries = await _child_session_summaries_from_conversations(
            members, parent.id, conversation_store
        )
    except Exception:  # noqa: BLE001 - the members are an add-on to the sub-agent page
        _logger.warning("factory: could not list the members of %s", parent.id, exc_info=True)
        return []
    return [
        _member_summary(conv, s, parent.id) for conv, s in zip(members, summaries, strict=True)
    ]


def merge_child_summaries(
    children: list[ChildSessionSummary],
    members: list[ChildSessionSummary],
    order: str,
) -> list[ChildSessionSummary]:
    """
    Merge factory members into a page of sub-agent children by ``created_at``.

    The sort is stable, so the children keep the store's order among
    themselves and a tie places a child before a member.

    :param children: Sub-agent summaries in store order.
    :param members: Factory member summaries.
    :param order: ``"desc"`` (newest first) or ``"asc"``.
    :returns: One merged list; *children* unchanged when *members* is empty.
    """
    if not members:
        return children
    seen = {c.id for c in children}
    extra = [m for m in members if m.id not in seen]
    return sorted([*children, *extra], key=lambda s: s.created_at, reverse=order == "desc")
