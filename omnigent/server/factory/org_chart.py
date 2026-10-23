"""Factory members in the Agents panel's session tree.

Leads and project leads run as top-level sessions in their own sandboxes, so
they are not ``parent_conversation_id`` children of the session that spawned
them; only the ``omnigent.factory.parent`` label links them. The child-sessions
route lists them next to real sub-agents (opt-in via ``include_factory``) so
the web Agents panel renders the whole org chart: lead → PLs → ICs.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from omnigent.errors import OmnigentError
from omnigent.factory import LABEL_PARENT
from omnigent.server.auth import LEVEL_READ
from omnigent.server.routes._auth_helpers import require_access
from omnigent.server.routes._sessions.orchestration import (
    _child_session_summaries_from_conversations,
)

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.server.schemas import ChildSessionSummary
    from omnigent.stores import ConversationStore
    from omnigent.stores.permission_store import PermissionStore

# A lead spawns at most a pod cap's worth of members; this only bounds the read.
MAX_FACTORY_CHILDREN = 200


async def _readable(
    user_id: str | None,
    conv: Conversation,
    conversation_store: ConversationStore,
    permission_store: PermissionStore | None,
) -> bool:
    try:
        await require_access(user_id, conv.id, LEVEL_READ, permission_store, conversation_store)
    except OmnigentError:
        return False
    return True


async def factory_child_summaries(
    session_id: str,
    *,
    user_id: str | None,
    conversation_store: ConversationStore,
    permission_store: PermissionStore | None,
    title: str | None = None,
) -> list[ChildSessionSummary]:
    """
    Summarise the top-level sessions labelled as spawned by *session_id*.

    Labels are not an ACL: each member is checked for READ through the same
    permission path as the parent, so another user's session that names
    *session_id* as its factory parent is never listed.

    :param session_id: The spawning session, e.g. ``"conv_lead_a"``.
    :param user_id: The caller, or ``None`` when auth is off.
    :param conversation_store: Label index and session rows.
    :param permission_store: Grants, or ``None`` when auth is off.
    :param title: When set, only members whose title equals it (mirrors the
        route's ``{tool}:{session_name}`` filter).
    :returns: One summary per readable, non-archived member, oldest first,
        each carrying its labels and its own ``kind``.
    """
    ids = await asyncio.to_thread(
        conversation_store.list_conversation_ids_with_label,
        LABEL_PARENT,
        session_id,
        MAX_FACTORY_CHILDREN,
    )
    ids = [i for i in ids if i != session_id]
    if not ids:
        return []
    rows = await asyncio.to_thread(conversation_store.get_conversations, ids)
    # Sub-agents already appear as regular children; only top-level members join here.
    candidates = [
        rows[i]
        for i in ids
        if i in rows
        and rows[i].parent_conversation_id is None
        and (title is None or rows[i].title == title)
    ]
    allowed = await asyncio.gather(
        *(_readable(user_id, c, conversation_store, permission_store) for c in candidates)
    )
    members = [c for c, ok in zip(candidates, allowed, strict=True) if ok]
    summaries = await _child_session_summaries_from_conversations(
        members, session_id, conversation_store
    )
    return [
        s.model_copy(update={"kind": conv.kind})
        for conv, s in zip(members, summaries, strict=True)
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
