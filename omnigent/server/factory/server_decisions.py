"""Decisions the server opens for the human when supervision needs one.

Each sits next to an existing ``[NEEDS HUMAN …]`` / alert text (those still go
to the agents unchanged) and is deduplicated per episode through its
``dedupe_key``: while one is open, re-opening returns it.

- relaunch failed (S2b): a member whose heartbeat relaunch failed 3×;
  expired when it answers a beat again.
- blocked on an approval (S5): a session parked on a human prompt; expired
  when the episode clears.
- message storm: a sender that hit its post rate limit.

Once their project ended (cancelled, merged, or its PL wrapped it up) these
and the loop decisions have nothing left to act on: the tick expires them.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import (
    ACTION_DISMISS,
    ACTION_PAUSE,
    ACTION_RELAUNCH,
    ACTION_RESTART,
    LABEL_PROJECT,
    STAGE_DONE,
)
from omnigent.server.factory import decision_routing, events
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.envelope import display_title
from omnigent.stores.factory_store import DecisionNotFound, DecisionNotOpen

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactoryDecision, FactoryProject
    from omnigent.server.factory.membership import Membership

_logger = logging.getLogger(__name__)

SOURCE_RELAUNCH_FAILED: Final = "relaunch_failed"
SOURCE_BLOCKED: Final = "blocked_approval"
SOURCE_STORM: Final = "storm"
PROJECT_RUN_SOURCES: Final = frozenset(
    {SOURCE_RELAUNCH_FAILED, SOURCE_BLOCKED, SOURCE_STORM, "loop", "done_check"}
)
"""Supervisor decisions about a project's running work (``loop`` and ``done_check``
are :mod:`.loop_escalation`'s); a PR closure's feedback and proposals outlive it."""
_QUESTION_MAX: Final = 1000
_CONTEXT_MAX: Final = 4000


def relaunch_failed_key(session_id: str) -> str:
    """:returns: The dedupe key of a member's relaunch-failed decision."""
    return f"relaunch_failed:{session_id}"


def blocked_key(session_id: str) -> str:
    """:returns: The dedupe key of a session's blocked-on-approval decision."""
    return f"blocked:{session_id}"


def storm_key(session_id: str) -> str:
    """:returns: The dedupe key of a sender's message-storm decision."""
    return f"storm:{session_id}"


def _store(app_state: Any) -> Any:
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    return deps.factory_store if isinstance(deps, FactoryDeps) else None


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _option(
    option_id: str,
    label: str,
    *,
    action: str | None = None,
    href: str | None = None,
    target: str | None = None,
    description: str | None = None,
    recommended: bool = False,
) -> dict[str, Any]:
    option: dict[str, Any] = {"id": option_id, "label": label, "recommended": recommended}
    for key, value in (
        ("action", action),
        ("href", href),
        ("target", target),
        ("description", description),
    ):
        if value is not None:
            option[key] = value
    return option


async def open_decision(
    app_state: Any,
    factory_id: str,
    *,
    source: str,
    dedupe_key: str,
    question: str,
    options: list[dict[str, Any]],
    blocking: bool,
    project: str | None,
    asked_by: str | None,
    context: str | None = None,
    assignee: str | None = None,
    proposal_kind: str | None = None,
) -> tuple[FactoryDecision, bool] | None:
    """
    Open a server decision (or find the open one with the same key). Never raises.

    A new decision emits ``decision.opened``.

    :param assignee: ``owners``, ``operators`` or a user id; by default the
        source's (:func:`~omnigent.server.factory.decision_routing.default_assignee`).
    :param proposal_kind: For ``proposal`` decisions, the proposal's kind.
    :returns: ``(decision, deduped)``, or ``None`` when it could not be stored.
    """
    store = _store(app_state)
    if store is None:
        return None
    if assignee is None:
        assignee = await asyncio.to_thread(
            _default_assignee, app_state, factory_id, source, project, options, proposal_kind
        )
    try:
        row, deduped = await asyncio.to_thread(
            lambda: store.create_decision(
                factory_id,
                dedupe_key=dedupe_key,
                question=_clip(question, _QUESTION_MAX),
                source=source,
                options=options,
                blocking=blocking,
                allow_text=False,
                project_slug=project,
                asked_by_session_id=asked_by,
                context=_clip(context, _CONTEXT_MAX) if context else None,
                assignee=assignee,
            )
        )
    except Exception:  # noqa: BLE001 - the NEEDS HUMAN text still reaches the agents
        _logger.warning("factory: could not open a %s decision", source, exc_info=True)
        return None
    if not deduped:
        await events.emit(
            app_state,
            factory_id,
            "decision.opened",
            severity="warning" if blocking else "notice",
            project=project,
            from_id=asked_by,
            to_id="human",
            subject=f"decision:{row.ref}",
            summary=f"{row.ref}: {row.question}",
            data={"decision_id": row.id, "source": source, "assignee": row.assignee},
        )
    return row, deduped


def _default_assignee(
    app_state: Any,
    factory_id: str,
    source: str,
    project: str | None,
    options: list[dict[str, Any]],
    proposal_kind: str | None,
) -> str:
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    human_owner = None
    if isinstance(deps, FactoryDeps):
        try:
            human_owner = decision_routing.human_owner_of(deps, factory_id, project)
        except Exception:  # noqa: BLE001 - the source's default still routes it
            _logger.warning("factory: could not read the human owner of %s", project)
    return decision_routing.default_assignee(
        source, human_owner=human_owner, proposal_kind=proposal_kind, options=options
    )


async def expire_decision(
    app_state: Any, decision_id: str, *, why: str = "the situation cleared"
) -> FactoryDecision | None:
    """
    Expire an open server decision whose episode ended. Never raises.

    :param why: The end of the event's summary, e.g. ``"docs is done"``.
    :returns: The expired decision, or ``None`` when it was no longer open.
    """
    store = _store(app_state)
    if store is None:
        return None
    try:
        row = await asyncio.to_thread(store.set_decision_status, decision_id, "expired")
    except (DecisionNotOpen, DecisionNotFound):
        return None
    except Exception:  # noqa: BLE001 - an open stale decision only costs a dismiss
        _logger.warning("factory: could not expire decision %s", decision_id, exc_info=True)
        return None
    await events.emit(
        app_state,
        row.factory_id,
        "decision.expired",
        project=row.project_slug,
        from_id=row.asked_by_session_id,
        subject=f"decision:{row.ref}",
        summary=f"{row.ref} expired: {why}",
        data={"decision_id": row.id, "source": row.source},
    )
    return row


async def expire_by_key(app_state: Any, factory_id: str, dedupe_key: str) -> None:
    """Expire the open decision of *factory_id* carrying *dedupe_key*, if any."""
    store = _store(app_state)
    if store is None:
        return
    try:
        rows = await asyncio.to_thread(
            store.list_decisions, status="open", factory_id=factory_id, limit=500
        )
    except Exception:  # noqa: BLE001 - see expire_decision
        _logger.warning("factory: could not list open decisions of %s", factory_id, exc_info=True)
        return
    for row in rows:
        if row.dedupe_key == dedupe_key:
            await expire_decision(app_state, row.id)


def project_ended(project: FactoryProject | None) -> str | None:
    """:returns: How *project* ended (``"done"``, ``"cancelled"``, ``"merged"``), or ``None``."""
    if project is None:
        return None
    if project.status not in (None, "active"):
        return str(project.status)
    return STAGE_DONE if project.stage == STAGE_DONE else None


async def expire_for_ended_projects(app_state: Any, factory_id: str) -> list[FactoryDecision]:
    """
    Expire the open supervisor decisions of projects that ended. Never raises.

    "Mark it done?" for a project its PL already wrapped up, or a relaunch of
    a PL that finished, has nobody left to act; it would wait in Needs you forever.

    :returns: The decisions expired.
    """
    store = _store(app_state)
    if store is None:
        return []
    try:
        rows = await asyncio.to_thread(
            store.list_decisions, status="open", factory_id=factory_id, limit=500
        )
        rows = [r for r in rows if r.source in PROJECT_RUN_SOURCES and r.project_slug]
        if not rows:
            return []
        projects = await asyncio.to_thread(store.list_projects, factory_id)
    except Exception:  # noqa: BLE001 - the next tick retries
        _logger.warning("factory: could not read the decisions of %s", factory_id, exc_info=True)
        return []
    ended = {p.slug: how for p in projects if (how := project_ended(p)) is not None}
    expired = []
    for row in rows:
        how = ended.get(row.project_slug or "")
        if how is None:
            continue
        done = await expire_decision(app_state, row.id, why=f"{row.project_slug} is {how}")
        if done is not None:
            expired.append(done)
    return expired


def _member_target(conv: Conversation) -> str:
    return f"member:{conv.id}"


async def open_relaunch_failed_decision(
    app_state: Any, conv: Conversation, factory_id: str, reason: str, failures: int
) -> tuple[FactoryDecision, bool] | None:
    """S2b: ask what to do with a member whose relaunch failed *failures* times."""
    title = display_title(conv.title, conv.id)
    project = conv.labels.get(LABEL_PROJECT)
    pause_target = f"project:{project}" if project else _member_target(conv)
    return await open_decision(
        app_state,
        factory_id,
        source=SOURCE_RELAUNCH_FAILED,
        dedupe_key=relaunch_failed_key(conv.id),
        question=f'"{title}" relaunch failed {failures}× ({reason})',
        context=(
            "Its heartbeat could not wake or relaunch it. Restart stops and redelivers; "
            "relaunch first restores the launch mode its spec declares."
        ),
        options=[
            _option("restart", "Restart", action=ACTION_RESTART, target=_member_target(conv)),
            _option(
                "relaunch",
                "Relaunch with spec mode",
                action=ACTION_RELAUNCH,
                target=_member_target(conv),
                recommended=True,
            ),
            _option(
                "pause",
                "Pause project" if project else "Pause",
                action=ACTION_PAUSE,
                target=pause_target,
            ),
            _option("dismiss", "Dismiss", action=ACTION_DISMISS),
        ],
        blocking=True,
        project=project,
        asked_by=conv.id,
    )


async def open_blocked_decision(
    app_state: Any,
    conv: Conversation,
    factory_id: str,
    blocked_on: str,
    since_iso: str,
    project: str | None,
) -> tuple[FactoryDecision, bool] | None:
    """S5: point the human at the approval a session is parked on."""
    title = display_title(conv.title, conv.id)
    return await open_decision(
        app_state,
        factory_id,
        source=SOURCE_BLOCKED,
        dedupe_key=blocked_key(conv.id),
        question=f'"{title}" is waiting on a human approval',
        context=f"Waiting since {since_iso}: {blocked_on}",
        options=[
            _option("open", "Open approval ↗", href="/inbox", recommended=True),
            _option("dismiss", "Dismiss", action=ACTION_DISMISS),
        ],
        blocking=True,
        project=project,
        asked_by=conv.id,
    )


async def open_storm_decision(
    app_state: Any, sender: Membership, limit: str
) -> tuple[FactoryDecision, bool] | None:
    """Storm: a sender hit its post rate limit; offer to pause it. Never raises."""
    if not sender.factory_id:
        return None
    title = display_title(sender.title, sender.session_id)
    return await open_decision(
        app_state,
        sender.factory_id,
        source=SOURCE_STORM,
        dedupe_key=storm_key(sender.session_id),
        question=f'"{title}" hit the message rate limit ({limit})',
        context="Its posts are refused until the hourly window frees up.",
        options=[
            _option(
                "pause",
                "Pause sender",
                action=ACTION_PAUSE,
                target=f"member:{sender.session_id}",
            ),
            _option("dismiss", "Dismiss", action=ACTION_DISMISS, recommended=True),
        ],
        blocking=False,
        project=sender.project,
        asked_by=sender.session_id,
    )


__all__ = [
    "PROJECT_RUN_SOURCES",
    "SOURCE_BLOCKED",
    "SOURCE_RELAUNCH_FAILED",
    "SOURCE_STORM",
    "blocked_key",
    "expire_by_key",
    "expire_decision",
    "expire_for_ended_projects",
    "open_blocked_decision",
    "open_decision",
    "open_relaunch_failed_decision",
    "open_storm_decision",
    "project_ended",
    "relaunch_failed_key",
    "storm_key",
]
