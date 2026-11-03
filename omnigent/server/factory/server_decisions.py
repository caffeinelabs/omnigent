"""Decisions the server opens for the human when supervision needs one.

Each sits next to an existing ``[NEEDS HUMAN …]`` / alert text (those still go
to the agents unchanged) and is deduplicated per episode through its
``dedupe_key``: while one is open, re-opening returns it.

- relaunch failed (S2b): a member whose heartbeat relaunch failed 3×;
  expired when it answers a beat again.
- launch failed: a member whose sandbox did not come up after the server's one
  retry (a new sandbox for a first launch, a wake of its own sandbox otherwise;
  same source, its own key); expired once it is up.
- blocked on an approval (S5): a session parked on a human prompt; expired
  when the episode clears.
- message storm: a sender that hit its post rate limit.

Once their project closed (cancelled, merged or done) these and the loop
decisions have nothing left to act on: the reconcile pass expires them
(:func:`ended_project_decisions`). A project whose PL only wrapped up (its
stage is done; a Stop does that too, and Resume brings the PL back) loses
just its "Mark it done?": its loop decisions stay answerable.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import (
    ACTION_DISMISS,
    ACTION_NEW_SANDBOX,
    ACTION_PAUSE,
    ACTION_RELAUNCH,
    ACTION_RESTART,
    LABEL_PROJECT,
    STAGE_DONE,
)
from omnigent.server.factory import decision_routing, events
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.envelope import display_title, failure_text
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
are :mod:`.loop_escalation`'s), expired once it closed; a PR closure's feedback
and proposals outlive it."""
WRAPPED_UP_SOURCES: Final = frozenset({"done_check"})
"""Expired already when the project's stage is done: "Mark it done?" was answered by its PL."""
_QUESTION_MAX: Final = 1000
_CONTEXT_MAX: Final = 4000


def relaunch_failed_key(session_id: str) -> str:
    """:returns: The dedupe key of a member's relaunch-failed decision."""
    return f"relaunch_failed:{session_id}"


def launch_failed_key(session_id: str) -> str:
    """:returns: The dedupe key of a member's sandbox-launch-failed decision."""
    return f"launch_failed:{session_id}"


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


SERVER_BY: Final = "server"
"""``answered_by`` of a decision the server closed itself (metrics leave it out)."""
CLOSED_PREFIX: Final = "Closed by the server: "


async def close_by_server(app_state: Any, decision_id: str, *, why: str) -> FactoryDecision | None:
    """
    Close an open decision whose question no longer applies: answered by the
    server with *why*, recorded with nobody to tell, and logged. Never raises.

    The answer shows in the decision's history ("Closed by the server: PR #9881
    merged · server") and ``decision.answered`` carries the reason.

    :param why: Why it no longer applies, e.g. ``"T-16 is done"``.
    :returns: The closed decision, or ``None`` when it was no longer open.
    """
    store = _store(app_state)
    if store is None:
        return None
    text = _clip(f"{CLOSED_PREFIX}{why}", _QUESTION_MAX)
    try:
        row = await asyncio.to_thread(
            store.answer_decision, decision_id, option_id=None, text=text, by=SERVER_BY
        )
    except (DecisionNotOpen, DecisionNotFound):
        return None
    except Exception:  # noqa: BLE001 - an open stale decision only costs a dismiss
        _logger.warning("factory: could not close decision %s", decision_id, exc_info=True)
        return None
    from omnigent.stores.factory_store import now_s

    row = await asyncio.to_thread(store.update_decision, row.id, delivered_at=now_s()) or row
    await events.emit(
        app_state,
        row.factory_id,
        "decision.answered",
        project=row.project_slug,
        from_id=None,
        to_id=row.asked_by_session_id,
        subject=f"decision:{row.ref}",
        summary=f"{row.ref} closed by the server: {why}",
        data={
            "decision_id": row.id,
            "source": row.source,
            "from": SERVER_BY,
            "by": SERVER_BY,
            "by_kind": "server",
            "label": "Closed by the server",
            "text": why,
            "reason": why,
            "nobody_to_tell": True,
            "auto": True,
        },
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


def ended_project_decisions(
    projects: Iterable[FactoryProject], decisions: Iterable[FactoryDecision]
) -> list[tuple[FactoryDecision, str]]:
    """
    The open supervisor decisions of *projects* that have nothing left to act on.

    A closed project (cancelled, merged or done) takes all of
    :data:`PROJECT_RUN_SOURCES` with it. A project whose PL wrapped it up
    (stage done, still active) takes only :data:`WRAPPED_UP_SOURCES`: a Stop
    sets that stage too and Resume brings the PL back, so a stopped loop's
    decision (Raise budget, Switch vendor, Abandon) must stay open for it.

    :param projects: The factory's projects.
    :param decisions: Its open decisions (others are skipped).
    :returns: ``(decision, why)`` pairs, e.g. ``(D-11, "docs is done")``.
    """
    by_slug = {p.slug: p for p in projects}
    out: list[tuple[FactoryDecision, str]] = []
    for row in decisions:
        if row.status != "open" or row.source not in PROJECT_RUN_SOURCES:
            continue
        project = by_slug.get(row.project_slug or "")
        if project is None:
            continue
        if project.status not in (None, "active"):
            out.append((row, f"{project.slug} is {project.status}"))
        elif project.stage == STAGE_DONE and row.source in WRAPPED_UP_SOURCES:
            out.append((row, f"{project.slug} is {STAGE_DONE}"))
    return out


def _member_target(conv: Conversation) -> str:
    return f"member:{conv.id}"


async def open_relaunch_failed_decision(
    app_state: Any,
    conv: Conversation,
    factory_id: str,
    reason: str,
    failures: int,
    *,
    detail: str | None = None,
) -> tuple[FactoryDecision, bool] | None:
    """
    S2b: ask what to do with a member whose relaunch failed *failures* times.

    :param reason: e.g. ``"terminal_failed_to_start"``.
    :param detail: The underlying error, e.g. the runner's
        ``"RuntimeError: tmux launch failed (rc=1): command too long"``, so
        nobody has to guess the cause from *reason* alone.
    """
    title = display_title(conv.title, conv.id)
    project = conv.labels.get(LABEL_PROJECT)
    pause_target = f"project:{project}" if project else _member_target(conv)
    # A backtick in the error would end the markdown code span early.
    quoted = (detail or "").replace("`", "'")
    cause = f"Last error: `{quoted}`\n\n" if detail else ""
    # No runner to reach means its sandbox is gone or never came up: only a new one helps.
    no_runner = reason == "runner_unavailable"
    return await open_decision(
        app_state,
        factory_id,
        source=SOURCE_RELAUNCH_FAILED,
        dedupe_key=relaunch_failed_key(conv.id),
        question=f'"{title}" relaunch failed {failures}× ({failure_text(reason, detail)})',
        context=(
            f"{cause}Its heartbeat could not wake or relaunch it. Restart stops and "
            "redelivers; relaunch first restores the launch mode its spec declares; both "
            "keep its sandbox. A new sandbox replaces it and keeps the project."
        ),
        options=[
            _option(
                "new_sandbox",
                "Relaunch on a new sandbox",
                action=ACTION_NEW_SANDBOX,
                target=_member_target(conv),
                recommended=no_runner,
            ),
            _option("restart", "Restart", action=ACTION_RESTART, target=_member_target(conv)),
            _option(
                "relaunch",
                "Relaunch with spec mode",
                action=ACTION_RELAUNCH,
                target=_member_target(conv),
                recommended=not no_runner,
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


def _times(tries: int) -> str:
    return "" if tries < 2 else " twice" if tries == 2 else f" {tries} times"


async def open_launch_failed_decision(
    app_state: Any,
    conv: Conversation,
    factory_id: str,
    tries: int,
    error: str | None,
    *,
    wake: bool = False,
    lost: bool = False,
    retried: bool = True,
    relaunched_by: str | None = None,
) -> tuple[FactoryDecision, bool] | None:
    """
    Ask owners what to do with a member whose sandbox did not come up.

    :param tries: Launches of this start, the failed one included.
    :param error: The last launch error, e.g. ``"… did not start within 900s …"``.
    :param wake: Its own sandbox (with its files) did not wake; else a new or
        first sandbox did not start.
    :param lost: A server restart cut the last launch off.
    :param retried: The server already retried this start once on its own.
    :param relaunched_by: Who relaunched it on the new sandbox that failed last,
        when a person did.
    """
    title = display_title(conv.title, conv.id)
    project = conv.labels.get(LABEL_PROJECT)
    target = _member_target(conv)
    if lost:
        question = f'"{title}" sandbox launch was cut off by a server restart'
        cause = f"A server restart cut off its launch (try {tries}).\n\n"
    else:
        verb = "wake" if wake else "start"
        question = f'"{title}" could not {verb} its sandbox{_times(tries)}'
        quoted = (error or "").replace("`", "'")
        cause = f"Last error: `{quoted}`\n\n" if error else ""
    story: list[str] = []
    if relaunched_by:
        story.append(f"It failed after {relaunched_by} relaunched it on a new sandbox.")
    if retried:
        again = "woke it once more" if wake else "tried a new sandbox once"
        story.append(f"The server already {again} and does not try again on its own.")
    if wake:
        story.append(
            "Its sandbox keeps its files: Restart wakes it again. A new sandbox deletes "
            "its files, dev env and previews; the project, charter, tasks and PRs stay."
        )
        options = [
            _option(
                "restart",
                "Restart",
                action=ACTION_RESTART,
                target=target,
                description="Wake the same sandbox again; its files stay.",
                recommended=True,
            ),
            _option(
                "new_sandbox",
                "Try a new sandbox",
                action=ACTION_NEW_SANDBOX,
                target=target,
                description="Deletes its files, dev env and previews.",
            ),
        ]
    else:
        story.append("A new sandbox keeps the project, its charter, tasks and PRs.")
        options = [
            _option(
                "new_sandbox",
                "Try a new sandbox",
                action=ACTION_NEW_SANDBOX,
                target=target,
                description="Start it again on a fresh sandbox.",
                recommended=True,
            )
        ]
    options += [
        _option(
            "pause",
            "Pause project" if project else "Pause",
            action=ACTION_PAUSE,
            target=f"project:{project}" if project else target,
            description="Leave it stopped until someone resumes it.",
        ),
        _option("dismiss", "Dismiss", action=ACTION_DISMISS),
    ]
    return await open_decision(
        app_state,
        factory_id,
        source=SOURCE_RELAUNCH_FAILED,
        dedupe_key=launch_failed_key(conv.id),
        question=question,
        context=cause + " ".join(story),
        options=options,
        blocking=True,
        project=project,
        asked_by=conv.id,
        assignee="owners",
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
    "CLOSED_PREFIX",
    "PROJECT_RUN_SOURCES",
    "SERVER_BY",
    "SOURCE_BLOCKED",
    "SOURCE_RELAUNCH_FAILED",
    "SOURCE_STORM",
    "WRAPPED_UP_SOURCES",
    "blocked_key",
    "close_by_server",
    "ended_project_decisions",
    "expire_by_key",
    "expire_decision",
    "launch_failed_key",
    "open_blocked_decision",
    "open_decision",
    "open_launch_failed_decision",
    "open_relaunch_failed_decision",
    "open_storm_decision",
    "project_ended",
    "relaunch_failed_key",
    "storm_key",
]
