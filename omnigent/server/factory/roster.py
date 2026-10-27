"""The factory roster: top-level members plus each member's sub-agents."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from omnigent.factory import (
    LABEL_HEALTH,
    LABEL_HEARTBEAT_S,
    LABEL_PARENT,
    LABEL_PROJECT,
    LABEL_ROLE,
    LABEL_STATE,
    LABEL_SUMMARY,
    LABEL_SUPERVISOR,
)
from omnigent.server.factory.context import FactoryDeps
from omnigent.server.factory.membership import load_session_spec, sub_agent_role
from omnigent.server.routes._sessions.helpers import _session_status_from_cache
from omnigent.server.routes._sessions.orchestration import (
    _child_session_summaries_from_conversations,
)
from omnigent.stores.conversation_store import runner_seen_is_fresh

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.server.schemas import ChildSessionSummary

# Sub-agents listed per member; a PL runs well under this many.
_MAX_CHILDREN_PER_MEMBER = 200


def session_url(session_id: str) -> str:
    """:returns: The web UI path of a session, e.g. ``"/c/conv_abc"``."""
    return f"/c/{session_id}"


def _status(conv: Conversation, summary: ChildSessionSummary) -> str:
    if summary.current_task_status == "failed":
        return "failed"
    if summary.pending_elicitations_count > 0:
        return "waiting"
    if summary.busy:
        return "running"
    # No live cache entry here: fall back to the row's persisted status.
    return _session_status_from_cache(conv.id, conv.live_status)


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _runner_online(
    conv: Conversation,
    connectivity: dict[str, Any],
    tunnel_registry: Any,
) -> bool:
    conn = connectivity.get(conv.id)
    runner_id = conn.runner_id if conn is not None else conv.runner_id
    if runner_id is None:
        return False
    if tunnel_registry is not None and tunnel_registry.get(runner_id) is not None:
        return True
    return conn is not None and runner_seen_is_fresh(conn.runner_last_seen)


async def _children(
    member: Conversation,
    deps: FactoryDeps,
) -> list[dict[str, Any]]:
    store = deps.conversation_store
    page = await asyncio.to_thread(
        store.list_conversations,
        limit=_MAX_CHILDREN_PER_MEMBER,
        kind="sub_agent",
        parent_conversation_id=member.id,
        order="asc",
    )
    children = list(page.data)
    if not children:
        return []
    summaries = await _child_session_summaries_from_conversations(children, member.id, store)
    roles = await asyncio.to_thread(
        lambda: {c.id: sub_agent_role(load_session_spec(c, deps)) for c in children}
    )
    return [
        {
            "session_id": child.id,
            "title": summary.title,
            "agent": child.sub_agent_name or summary.tool,
            "role": roles.get(child.id),
            "status": _status(child, summary),
            "last_activity_at": summary.updated_at,
            "summary": child.labels.get(LABEL_SUMMARY),
            "health": child.labels.get(LABEL_HEALTH),
            "url": session_url(child.id),
        }
        for child, summary in zip(children, summaries, strict=True)
    ]


async def build_roster(
    factory_id: str,
    members: list[Conversation],
    deps: FactoryDeps,
    app_state: Any,
    *,
    humans: bool = False,
) -> dict[str, Any]:
    """
    Build the ``GET /v1/factory/roster`` body.

    :param factory_id: Factory root session id.
    :param members: Top-level members to list (already access-filtered).
    :param deps: Factory stores.
    :param app_state: ``app.state`` (runner tunnel registry).
    :param humans: Add ``humans``: the people on the factory roster.
    :returns: ``{factory_id, members: [{…, children: [{…}]}], humans?: [{…}]}``.
    """
    body = await _members_body(factory_id, members, deps, app_state)
    if humans:
        from omnigent.server.factory.people import roster_humans

        body["humans"] = await roster_humans(app_state, factory_id)
    return body


async def _members_body(
    factory_id: str,
    members: list[Conversation],
    deps: FactoryDeps,
    app_state: Any,
) -> dict[str, Any]:
    store = deps.conversation_store
    if not members:
        return {"factory_id": factory_id, "members": []}
    summaries = await _child_session_summaries_from_conversations(members, factory_id, store)
    connectivity = await asyncio.to_thread(store.get_session_connectivity, [m.id for m in members])
    tunnel_registry = getattr(app_state, "tunnel_registry", None)
    children = await asyncio.gather(*(_children(m, deps) for m in members))
    rows: list[dict[str, Any]] = []
    for member, summary, kids in zip(members, summaries, children, strict=True):
        labels = member.labels
        rows.append(
            {
                "session_id": member.id,
                "title": summary.title,
                "role": labels.get(LABEL_ROLE),
                "project": labels.get(LABEL_PROJECT),
                "parent_session_id": labels.get(LABEL_PARENT),
                "supervisor_session_id": labels.get(LABEL_SUPERVISOR),
                "status": _status(member, summary),
                "runner_online": _runner_online(member, connectivity, tunnel_registry),
                "last_activity_at": summary.updated_at,
                "heartbeat_s": _int_or_none(labels.get(LABEL_HEARTBEAT_S)),
                "state": labels.get(LABEL_STATE),
                "health": labels.get(LABEL_HEALTH),
                "summary": labels.get(LABEL_SUMMARY),
                "url": session_url(member.id),
                "children": kids,
            }
        )
    return {"factory_id": factory_id, "members": rows}
