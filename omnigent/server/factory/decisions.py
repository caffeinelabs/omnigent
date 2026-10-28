"""Decisions for the human: answer, withdraw, deliver and confirm receipt.

An answer is a compare-and-set on the open row (a second answer gets 409 with
who won). Agent decisions are mailed back to the asker as a server-stamped
``[Factory decision · D-n answered …]`` text, falling back to the project's
current PL and then the root lead when the asker can no longer act; the mail
id is stored so the ``message.delivered`` event fills ``delivered_at`` (the
cockpit's "delivered ✓"). Server decisions run their option's action through
:mod:`~omnigent.server.factory.control` instead.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from omnigent.entities import Conversation
from omnigent.entities.factory import FactoryDecision, FactoryEvent, FactoryPr
from omnigent.factory import (
    ACTION_ABANDON_TASK,
    ACTION_DISMISS,
    ACTION_GRANT_ROLE,
    ACTION_PAUSE,
    ACTION_REDEPLOY_PREVIEW,
    ACTION_RELAUNCH,
    ACTION_RESTART,
    ACTION_RESUME,
    ACTION_REWORK,
    LABEL_PROJECT,
)
from omnigent.server.auth import LEVEL_EDIT, LEVEL_MANAGE
from omnigent.server.factory import control, decision_routing, events, people
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import CockpitError, ControlPlan, human_label, same_id
from omnigent.server.factory.envelope import display_title, quote_spoofed_lines
from omnigent.server.factory.mailer import new_mail_id
from omnigent.server.factory.membership import is_top_level, resolve_role, root_conversation
from omnigent.server.factory.schemas import DecisionOptionView, DecisionView
from omnigent.stores.factory_store import DecisionNotFound, DecisionNotOpen, norm_id

if TYPE_CHECKING:
    from omnigent.server.factory.loop_escalation import BudgetAnswer, LoopAnswer
    from omnigent.server.factory.proposals import ProposalAnswer

_logger = logging.getLogger(__name__)

ANSWER_TEXT_MAX_CHARS: Final = 2000
QUESTION_EXCERPT_CHARS: Final = 200
FREE_TEXT_LABEL: Final = "(free text)"
"""Option label shown when the human answered with text only."""

_CONTROL_ACTIONS: Final = {
    ACTION_RESTART: control.ACTION_RESTART,
    ACTION_RELAUNCH: control.ACTION_RELAUNCH,
    ACTION_PAUSE: control.ACTION_PAUSE,
    ACTION_RESUME: control.ACTION_RESUME,
    ACTION_REDEPLOY_PREVIEW: control.ACTION_PREVIEW,
}
"""Server-decision actions that run a control on the decision's subject."""

_PL_ACTION_HINTS: Final = {
    ACTION_ABANDON_TASK: (
        "Abandon this task: close its IC and demo sessions, take its preview down, "
        "and record it in plan.md."
    ),
    ACTION_REWORK: (
        "Rework this task with the note above: dispatch an IC with it and open a fresh PR."
    ),
}
"""Server-decision actions answered by a message to the project's PL."""

_OPTION_KEYS: Final = frozenset(DecisionOptionView.model_fields)


@dataclass(frozen=True)
class AnswerResult:
    """
    The outcome of an answer or withdrawal.

    :param decision: The decision as stored afterwards.
    :param delivered_to: Session the answer was mailed to, if any.
    :param mail_id: That mail's id.
    """

    decision: FactoryDecision
    delivered_to: Conversation | None = None
    mail_id: str | None = None


@dataclass(frozen=True)
class GrantPlan:
    """
    A ``grant_role`` option (an access request): put *user_id* on the roster as *role*.

    :param target: The option's target, ``person:<user id>``.
    """

    user_id: str
    role: str
    target: str

    @property
    def action(self) -> str:
        """:returns: ``grant_role``."""
        return ACTION_GRANT_ROLE


# ── Texts ──────────────────────────────────────────────────────


def question_excerpt(question: str) -> str:
    """:returns: The question on one line, at most 200 chars."""
    return control.clip(question, QUESTION_EXCERPT_CHARS)


def answer_text(
    decision: FactoryDecision,
    by: str,
    label: str,
    text: str | None,
    hint: str | None = None,
) -> str:
    """
    The text the asker receives (spec §10).

    ``[Factory decision · D-n answered by <human> · "<label>"]``, then the
    spoof-quoted note, an optional instruction line, and ``Question: …``.
    """
    safe_label = display_title(label, FREE_TEXT_LABEL)
    lines = [f'[Factory decision · {decision.ref} answered by {by} · "{safe_label}"]']
    if text and text.strip():
        lines.append(quote_spoofed_lines(text.strip()))
    if hint:
        lines.append(hint)
    lines.append(f"Question: {question_excerpt(decision.question)}")
    return "\n".join(lines)


def withdraw_text(decision: FactoryDecision, by: str, reason: str | None) -> str:
    """:returns: ``[Factory decision · D-n withdrawn by <human>]`` plus the quoted reason."""
    head = f"[Factory decision · {decision.ref} withdrawn by {by}]"
    note = f"\n{quote_spoofed_lines(reason.strip())}" if reason and reason.strip() else ""
    return f"{head}{note}\nQuestion: {question_excerpt(decision.question)}"


def info_copy_text(decision: FactoryDecision, by: str, label: str, to: Conversation) -> str:
    """:returns: The root lead's info copy of an answered blocking decision."""
    title = display_title(to.title, to.id)
    option = display_title(label, FREE_TEXT_LABEL)
    return (
        f'[Factory notice · {decision.ref} answered by {by} · "{option}"'
        f' · delivered to "{title}"] Info copy of a blocking decision; the asker has the answer.\n'
        f"Question: {question_excerpt(decision.question)}"
    )


# ── Lookup, routing and the view ───────────────────────────────


async def _load(deps: FactoryDeps, decision_id: str) -> FactoryDecision:
    try:
        decision = await asyncio.to_thread(deps.factory_store.get_decision, decision_id)
    except ValueError:
        decision = None
    if decision is None:
        raise CockpitError(404, "unknown_decision", f"no decision {decision_id!r}")
    return decision


def _project_of(
    decision: FactoryDecision, asker: Conversation | None, deps: FactoryDeps
) -> str | None:
    if decision.project_slug:
        return decision.project_slug
    if asker is None:
        return None
    root = root_conversation(asker, deps.conversation_store)
    return root.labels.get(LABEL_PROJECT) or None


def route_answer(
    deps: FactoryDeps, decision: FactoryDecision, asker: Conversation | None
) -> Conversation | None:
    """
    Who receives an agent decision's answer: the asker, else the project's
    current PL, else the root lead.

    :returns: The recipient, or ``None`` when even the root is gone.
    """
    if asker is not None and not control.is_gone(asker):
        return asker
    pl = control.find_project_pl(deps, decision.factory_id, _project_of(decision, asker, deps))
    if pl is not None:
        return pl
    root = deps.conversation_store.get_conversation(decision.factory_id)
    return None if control.is_gone(root) else root


def _option_view(option: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in option.items() if k in _OPTION_KEYS and v is not None}
    out["id"] = str(option.get("id", ""))
    out["label"] = str(option.get("label") or out["id"])
    if "recommended" in out:
        out["recommended"] = bool(out["recommended"])
    for key in ("description", "action", "href", "target"):
        if key in out:
            out[key] = str(out[key])
    return out


def decision_view(
    deps: FactoryDeps,
    decision: FactoryDecision,
    *,
    delivered_to_title: str | None = None,
) -> dict[str, Any]:
    """
    Render a decision as the cockpit's :class:`DecisionView` (sync store reads).

    :param deps: Factory stores.
    :param decision: The stored row.
    :param delivered_to_title: Title of the session the answer went to, when known.
    :returns: A JSON-ready dict.
    """
    asker = (
        deps.conversation_store.get_conversation(decision.asked_by_session_id)
        if decision.asked_by_session_id
        else None
    )
    role = resolve_role(asker, deps) if asker is not None else None
    view = DecisionView(
        id=decision.id,
        ref=decision.ref,
        number=decision.number,
        factory_id=decision.factory_id,
        project_slug=decision.project_slug,
        asked_by_session_id=decision.asked_by_session_id,
        asked_by_title=display_title(asker.title, asker.id) if asker is not None else None,
        asked_by_role=role,  # type: ignore[arg-type]
        source=decision.source,  # type: ignore[arg-type]
        blocking=decision.blocking,
        question=decision.question,
        context=decision.context,
        options=[DecisionOptionView(**_option_view(o)) for o in decision.options],
        allow_text=decision.allow_text,
        status=decision.status,  # type: ignore[arg-type]
        answer_option_id=decision.answer_option_id,
        answer_text=decision.answer_text,
        answered_by=decision.answered_by,
        answered_at=decision.answered_at,
        delivered_mail_id=decision.delivered_mail_id,
        delivered_at=decision.delivered_at,
        delivered_to_title=delivered_to_title,
        created_at=decision.created_at,
        updated_at=decision.updated_at,
        assignee=decision.assignee,
        claimed_by=decision.claimed_by,
        claimed_at=decision.claimed_at,
        thread=decision.thread,
        subject=decision.subject,
    )
    return view.model_dump()


async def _authorize(
    deps: FactoryDeps, user_id: str | None, decision: FactoryDecision
) -> Conversation | None:
    """
    EDIT on the asker (agent decisions) or the factory root (server decisions),
    and the decision is assigned to the caller or a role they hold.

    :returns: The asker row, when there is one.
    :raises CockpitError: 403 ``not_assigned``.
    """
    await control.load_factory_root(deps, user_id, decision.factory_id)
    asker = await control.get_conv(deps, decision.asked_by_session_id)
    if decision.source == "agent" and asker is not None:
        await control.require(deps, user_id, asker.id, LEVEL_EDIT)
    else:
        await control.require(deps, user_id, decision.factory_id, LEVEL_EDIT)
    await require_assigned(deps, user_id, decision)
    return asker


async def require_assigned(
    deps: FactoryDeps, user_id: str | None, decision: FactoryDecision
) -> None:
    """
    Refuse an operator acting on a decision assigned to owners or to someone else.

    :raises CockpitError: 403 ``not_assigned`` naming the assignee.
    """
    level = await asyncio.to_thread(people.viewer_level, deps, decision.factory_id, user_id)
    if decision_routing.may_answer(decision, user_id, level):
        return
    assignee = decision_routing.assignee_of(decision)
    raise CockpitError(
        403,
        "not_assigned",
        f"{decision.ref} is assigned to {assignee}; an owner can reassign it",
        assignee=assignee,
    )


async def not_open_error(decision: FactoryDecision, deps: FactoryDeps) -> CockpitError:
    """:returns: The 409 a lost compare-and-set answers with."""
    view = await asyncio.to_thread(decision_view, deps, decision)
    return CockpitError(
        409,
        "decision_not_open",
        f"{decision.ref} is already {decision.status}",
        status=decision.status,
        answered_by=decision.answered_by,
        answered_at=decision.answered_at,
        decision=view,
    )


# ── Server-decision actions ────────────────────────────────────


def _subject(decision: FactoryDecision, option: dict[str, Any]) -> str | None:
    target = option.get("target")
    if isinstance(target, str) and target.strip():
        return target.strip()
    if decision.asked_by_session_id:
        return f"{control.TARGET_MEMBER_PREFIX}{decision.asked_by_session_id}"
    if decision.project_slug:
        return f"{control.TARGET_PROJECT_PREFIX}{decision.project_slug}"
    return None


async def _subject_pr(
    deps: FactoryDeps, decision: FactoryDecision, subject: str | None
) -> FactoryPr:
    """The PR ``redeploy_preview`` acts on: ``pr:<id>``, else the project's newest open PR."""
    store = deps.factory_store
    if subject and subject.startswith("pr:"):
        try:
            pr = await asyncio.to_thread(store.get_pr, subject[3:].strip())
        except ValueError:
            pr = None
        if pr is not None and same_id(pr.factory_id, decision.factory_id):
            return pr
        raise CockpitError(404, "unknown_pr", "the decision names no PR of this factory")
    slug = decision.project_slug
    if subject and subject.startswith(control.TARGET_PROJECT_PREFIX):
        slug = subject[len(control.TARGET_PROJECT_PREFIX) :].strip()
    prs = await asyncio.to_thread(store.list_prs, decision.factory_id, ["open"])
    mine = [p for p in prs if slug and p.project_slug == slug]
    if not mine:
        raise CockpitError(409, "no_open_pr", "the decision's project has no open PR to preview")
    return mine[-1]


async def _top_level_subject(deps: FactoryDeps, subject: str) -> str:
    """
    Map ``member:<sub-agent>`` to its top-level ancestor (the IC's PL).

    Controls act on leads and PLs; pausing a PL stops its ICs too.
    """
    if not subject.startswith(control.TARGET_MEMBER_PREFIX):
        return subject
    conv = await control.get_conv(deps, subject[len(control.TARGET_MEMBER_PREFIX) :].strip())
    if conv is None or is_top_level(conv):
        return subject
    top = await asyncio.to_thread(root_conversation, conv, deps.conversation_store)
    return f"{control.TARGET_MEMBER_PREFIX}{top.id}"


async def _plan_action(
    app_state: Any,
    deps: FactoryDeps,
    decision: FactoryDecision,
    option: dict[str, Any] | None,
    user_id: str | None,
    *,
    text: str | None = None,
    changes: Any = None,
) -> ControlPlan | GrantPlan | Conversation | ProposalAnswer | LoopAnswer | BudgetAnswer | None:
    """
    Validate a server decision's action before the answer is stored.

    A proposal's decision (``apply_proposal``, Edit & apply, Reject) is planned
    by :mod:`.proposals`, a loop's (raise, switch vendor, fresh restart, abandon,
    mark done) by :mod:`.loop_escalation`; they run after the compare-and-set below.

    :returns: A control plan, a roster grant (access requests), the PL to
        message (abandon/rework), a proposal answer, or ``None`` for no side
        effect (dismiss, link-only and unknown actions).
    """
    from omnigent.server.factory import proposals

    if proposals.is_mirror(decision):
        return await proposals.plan_answer(
            app_state, decision, option, text, user_id, changes=changes
        )
    if decision.source in ("loop", "done_check"):
        from omnigent.server.factory import loop_escalation

        planned = await loop_escalation.plan_answer(app_state, decision, option, user_id)
        if planned is not None:
            return planned
    if option is None:
        return None
    action = option.get("action")
    if not action or action == ACTION_DISMISS or option.get("href"):
        return None
    if action == ACTION_GRANT_ROLE:
        return await _plan_grant(app_state, deps, decision, option, user_id)
    subject = _subject(decision, option)
    if action in _CONTROL_ACTIONS:
        options: dict[str, Any] = {}
        if action == ACTION_REDEPLOY_PREVIEW:
            pr = await _subject_pr(deps, decision, subject)
            if not pr.project_slug:
                raise CockpitError(409, "no_project_lead", f"PR #{pr.number} has no project")
            subject = f"{control.TARGET_PROJECT_PREFIX}{pr.project_slug}"
            options = {"pr_id": pr.id, "mode": control.PREVIEW_UP}
        if subject is None:
            raise CockpitError(409, "no_subject", f"{decision.ref} names nothing to {action}")
        if subject.startswith("pr:"):
            raise CockpitError(400, "invalid_target", f"{action} acts on a project or member")
        if action in (ACTION_PAUSE, ACTION_RESUME):
            subject = await _top_level_subject(deps, subject)
        return await control.plan_control(
            app_state,
            decision.factory_id,
            user_id=user_id,
            action=_CONTROL_ACTIONS[action],
            target=subject,
            options=options,
        )
    if action in _PL_ACTION_HINTS:
        slug = decision.project_slug
        if subject and subject.startswith(control.TARGET_PROJECT_PREFIX):
            slug = subject[len(control.TARGET_PROJECT_PREFIX) :].strip()
        pl = await asyncio.to_thread(control.find_project_pl, deps, decision.factory_id, slug)
        if pl is None:
            raise CockpitError(
                409, "no_project_lead", f"{decision.ref} has no project lead to tell"
            )
        await control.require(deps, user_id, pl.id, LEVEL_EDIT)
        return pl
    _logger.warning(
        "factory: %s option %s has unknown action %r", decision.ref, option.get("id"), action
    )
    return None


async def _plan_grant(
    app_state: Any,
    deps: FactoryDeps,
    decision: FactoryDecision,
    option: dict[str, Any],
    user_id: str | None,
) -> GrantPlan:
    """Validate a ``grant_role`` option: ``person:<user>``, a role, MANAGE, the sharing mode."""
    target = str(option.get("target") or "")
    role = str(option.get("role") or people.ROLE_OPERATOR)
    person = target.removeprefix("person:").strip()
    if not target.startswith("person:") or not person or role not in people.ROLE_LEVELS:
        raise CockpitError(409, "invalid_option", f"{decision.ref} names no person and role")
    await control.require(deps, user_id, decision.factory_id, LEVEL_MANAGE)
    await people.require_roster_sharing(app_state, decision.factory_id, role)
    return GrantPlan(user_id=person, role=role, target=target)


async def _action_failed(
    app_state: Any,
    deps: FactoryDeps,
    decision: FactoryDecision,
    by: str,
    plan: ControlPlan | GrantPlan | LoopAnswer | BudgetAnswer,
    exc: Exception,
) -> tuple[FactoryDecision, CockpitError]:
    """
    Record a server decision whose control raised after the answer was stored.

    The receipt id is cleared (nothing is delivered), a ``factory.warning``
    tells the cockpit, and the caller gets a 502 naming the action.

    :returns: The decision as stored now and the error to raise.
    """
    _logger.warning(
        "factory: %s answered but its %s failed", decision.ref, plan.action, exc_info=exc
    )
    try:
        decision = (
            await asyncio.to_thread(
                deps.factory_store.update_decision, decision.id, delivered_mail_id=None
            )
            or decision
        )
    except Exception:  # noqa: BLE001 - best effort; the warning below still goes out
        _logger.warning("factory: could not clear the receipt of %s", decision.ref, exc_info=True)
    # The exception text stays in the server log; it may carry internals.
    message = f"{decision.ref} is answered, but its {plan.action} failed ({type(exc).__name__})"
    await events.emit(
        app_state,
        decision.factory_id,
        "factory.warning",
        severity="warning",
        project=decision.project_slug,
        subject=f"decision:{decision.ref}",
        summary=message,
        data={
            "from": by,
            "by": by,
            "decision_id": decision.id,
            "action": plan.action,
            "target": plan.target if isinstance(plan.target, str) else plan.target.ref,
            "error": type(exc).__name__,
        },
    )
    view = await asyncio.to_thread(decision_view, deps, decision)
    error = CockpitError(
        502,
        "action_failed",
        f"{message}; run it from the controls instead",
        action=plan.action,
        decision=view,
    )
    return decision, error


async def _run_proposal_answer(
    app_state: Any,
    deps: FactoryDeps,
    answered: FactoryDecision,
    by: str,
    answer: ProposalAnswer,
) -> tuple[FactoryDecision, Conversation | None, str | None, CockpitError | None]:
    """
    Apply or reject the proposal of an answered decision; the proposer gets the outcome.

    :returns: ``(decision, proposer, outcome mail id, failure)``.
    """
    from omnigent.server.factory import proposals

    mail_id = new_mail_id()
    answered = (
        await asyncio.to_thread(
            deps.factory_store.update_decision, answered.id, delivered_mail_id=mail_id
        )
        or answered
    )
    try:
        proposer, sent = await proposals.execute_answer(app_state, answer, mail_id=mail_id)
    except Exception as exc:  # noqa: BLE001 - the answer is stored; report, don't 500
        _logger.warning("factory: %s answered but its proposal failed", answered.ref, exc_info=exc)
        answered = (
            await asyncio.to_thread(
                deps.factory_store.update_decision, answered.id, delivered_mail_id=None
            )
            or answered
        )
        code = exc.code if isinstance(exc, CockpitError) else type(exc).__name__
        message = f"{answered.ref} is answered, but applying its proposal failed ({code})"
        await events.emit(
            app_state,
            answered.factory_id,
            "factory.warning",
            severity="warning",
            project=answered.project_slug,
            subject=f"decision:{answered.ref}",
            summary=message,
            data={"from": by, "by": by, "decision_id": answered.id, "error": code},
        )
        view = await asyncio.to_thread(decision_view, deps, answered)
        status = exc.status_code if isinstance(exc, CockpitError) else 502
        failure = CockpitError(
            status,
            code if isinstance(exc, CockpitError) else "action_failed",
            message,
            decision=view,
        )
        if isinstance(exc, CockpitError):
            failure.extra = {**exc.extra, "decision": view}
        return answered, None, None, failure
    if not sent:
        answered = (
            await asyncio.to_thread(
                deps.factory_store.update_decision, answered.id, delivered_mail_id=None
            )
            or answered
        )
    target = await control.get_conv(deps, proposer) if proposer else None
    return answered, target, sent, None


# ── Answer and withdraw ────────────────────────────────────────


async def answer_decision(
    app_state: Any,
    decision_id: str,
    *,
    user_id: str | None,
    option_id: str | None = None,
    text: str | None = None,
    changes: Any = None,
    reason: str | None = None,
    answered_by: str | None = None,
) -> AnswerResult:
    """
    Answer an open decision (``POST /v1/factory/decisions/{id}/answer``).

    :param changes: Edit & apply of a proposal decision: the edited changes.
    :param reason: The closure reason of a ``pr_closed`` decision (optional).
    :param answered_by: Who the answer is recorded as (``eval-auto`` in eval mode).

    :raises CockpitError: 400 on a bad option/text, 404 unknown decision,
        409 when it is no longer open (with ``answered_by``/``answered_at``).
    :raises OmnigentError: 401/403/404 from the access checks.
    """
    from omnigent.server.factory import feedback, loop_escalation
    from omnigent.server.factory.proposals import ProposalAnswer

    deps = factory_deps(app_state)
    decision = await _load(deps, decision_id)
    asker = await _authorize(deps, user_id, decision)
    if decision.status != "open":
        raise await not_open_error(decision, deps)
    text = text.strip() if text and text.strip() else None
    if text is not None and len(text) > ANSWER_TEXT_MAX_CHARS:
        raise CockpitError(
            400, "text_too_long", f"text exceeds {ANSWER_TEXT_MAX_CHARS} chars", field="text"
        )
    option: dict[str, Any] | None = None
    if option_id:
        option = next((o for o in decision.options if str(o.get("id")) == option_id), None)
        if option is None:
            raise CockpitError(
                400,
                "unknown_option",
                f"{decision.ref} has no option {option_id!r}",
                field="option_id",
            )
    elif text is None:
        raise CockpitError(
            400, "answer_required", "pick an option or write an answer", field="option_id"
        )
    elif not decision.allow_text:
        raise CockpitError(
            400, "text_not_allowed", f"{decision.ref} needs one of its options", field="text"
        )

    closure = feedback.closure_reason(decision, option, reason)
    by = answered_by or await human_label(deps, decision.factory_id, user_id)
    label = str(option.get("label") or option_id) if option is not None else FREE_TEXT_LABEL
    planned: (
        ControlPlan | GrantPlan | Conversation | ProposalAnswer | LoopAnswer | BudgetAnswer | None
    ) = None
    failure: CockpitError | None = None
    if decision.source != "agent":
        planned = await _plan_action(
            app_state, deps, decision, option, user_id, text=text, changes=changes
        )

    try:
        answered = await asyncio.to_thread(
            deps.factory_store.answer_decision, decision.id, option_id=option_id, text=text, by=by
        )
    except DecisionNotOpen as exc:
        raise await not_open_error(exc.decision, deps) from None
    except DecisionNotFound:
        raise CockpitError(404, "unknown_decision", f"no decision {decision_id!r}") from None
    if closure is not None:
        await feedback.record_closure(app_state, answered, closure, user_id)

    target: Conversation | None = None
    outgoing: str | None = None
    kind = "info"
    action = option.get("action") if option is not None else None
    if decision.source == "agent":
        target = await asyncio.to_thread(route_answer, deps, answered, asker)
        if target is not None:
            outgoing = answer_text(answered, by, label, text)
    elif isinstance(planned, ControlPlan):
        target = planned.recipients[0] if planned.recipients else None
    elif isinstance(planned, Conversation):
        target = planned
        outgoing = answer_text(answered, by, label, text, _PL_ACTION_HINTS.get(str(action)))
        kind = "request"
    elif isinstance(planned, loop_escalation.ANSWER_TYPES):
        target, outgoing = planned.pl, planned.text(by)
    # The receipt id is stored before the mail is queued, so its delivery is never missed.
    mailed = outgoing is not None or isinstance(planned, ControlPlan)
    mail_id = new_mail_id() if target is not None and mailed else None
    if mail_id is not None:
        answered = (
            await asyncio.to_thread(
                deps.factory_store.update_decision, answered.id, delivered_mail_id=mail_id
            )
            or answered
        )
    if isinstance(planned, ProposalAnswer):
        answered, target, mail_id, failure = await _run_proposal_answer(
            app_state, deps, answered, by, planned
        )
    elif isinstance(planned, ControlPlan):
        if target is not None and mail_id is not None:
            planned.mail_ids[target.id] = mail_id
        try:
            await control.execute_control(app_state, planned)
        except Exception as exc:  # noqa: BLE001 - the answer is stored; report, don't 500
            answered, failure = await _action_failed(app_state, deps, answered, by, planned, exc)
            mail_id = None
    elif isinstance(planned, GrantPlan):
        try:
            await people.set_person(
                app_state, answered.factory_id, planned.user_id, planned.role, by_user=user_id
            )
        except Exception as exc:  # noqa: BLE001 - the answer is stored; report, don't 500
            answered, failure = await _action_failed(app_state, deps, answered, by, planned, exc)
    elif isinstance(planned, loop_escalation.ANSWER_TYPES):
        try:
            await loop_escalation.execute_answer(app_state, answered, planned)
        except Exception as exc:  # noqa: BLE001 - the answer is stored; report, don't 500
            answered, failure = await _action_failed(app_state, deps, answered, by, planned, exc)
            mail_id = None
        if failure is None and target is not None and outgoing is not None:
            control.send_text(app_state, target.id, outgoing, kind=kind, mail_id=mail_id)
    elif target is not None and outgoing is not None:
        control.send_text(app_state, target.id, outgoing, kind=kind, mail_id=mail_id)
        if (
            decision.source == "agent"
            and answered.blocking
            and not same_id(target.id, answered.factory_id)
        ):
            control.send_text(
                app_state,
                answered.factory_id,
                info_copy_text(answered, by, label, target),
                kind="info",
            )

    to_title = display_title(target.title, target.id) if target is not None else None
    await events.emit(
        app_state,
        answered.factory_id,
        "decision.answered",
        severity="notice",
        project=answered.project_slug,
        to_id=target.id if target is not None else None,
        subject=f"decision:{answered.ref}",
        summary=f'{by} answered {answered.ref} · "{display_title(label, FREE_TEXT_LABEL)}"',
        data={
            "from": by,
            "by": by,
            "option_id": option_id,
            "label": label,
            "action": action,
            "text": control.clip(text, 300) if text else None,
            "mail_id": mail_id,
            "delivered_to_title": to_title,
            "action_failed": failure is not None,
        },
    )
    if failure is not None:
        raise failure
    return AnswerResult(decision=answered, delivered_to=target, mail_id=mail_id)


async def withdraw_decision(
    app_state: Any,
    decision_id: str,
    *,
    user_id: str | None,
    reason: str | None = None,
) -> AnswerResult:
    """
    Withdraw an open decision (``POST /v1/factory/decisions/{id}/withdraw``).

    An agent's asker is told, if it can still act.

    :raises CockpitError: 404 unknown decision, 409 when it is not open.
    """
    deps = factory_deps(app_state)
    decision = await _load(deps, decision_id)
    asker = await _authorize(deps, user_id, decision)
    if reason is not None and len(reason) > ANSWER_TEXT_MAX_CHARS:
        raise CockpitError(
            400, "text_too_long", f"reason exceeds {ANSWER_TEXT_MAX_CHARS} chars", field="reason"
        )
    try:
        withdrawn = await asyncio.to_thread(
            deps.factory_store.set_decision_status, decision.id, "withdrawn"
        )
    except DecisionNotOpen as exc:
        raise await not_open_error(exc.decision, deps) from None
    except DecisionNotFound:
        raise CockpitError(404, "unknown_decision", f"no decision {decision_id!r}") from None
    by = await human_label(deps, decision.factory_id, user_id)
    mail_id: str | None = None
    target = asker if decision.source == "agent" and not control.is_gone(asker) else None
    if target is not None:
        mail_id = control.send_text(
            app_state, target.id, withdraw_text(withdrawn, by, reason), kind="info"
        )
    await events.emit(
        app_state,
        withdrawn.factory_id,
        "decision.withdrawn",
        project=withdrawn.project_slug,
        to_id=target.id if target is not None else None,
        subject=f"decision:{withdrawn.ref}",
        summary=f"{by} withdrew {withdrawn.ref}",
        data={
            "from": by,
            "by": by,
            "reason": control.clip(reason, 300) if reason else None,
            "mail_id": mail_id,
        },
    )
    return AnswerResult(decision=withdrawn, delivered_to=target, mail_id=mail_id)


# ── Delivery receipts ──────────────────────────────────────────


def _event_mail_id(event: FactoryEvent) -> str | None:
    mail_id = (event.data or {}).get("mail_id")
    if isinstance(mail_id, str) and mail_id:
        return mail_id
    subject = event.subject or ""
    return subject[len("mail:") :] if subject.startswith("mail:") else None


def _fill_receipt(
    deps: FactoryDeps, factory_id: str, mail_id: str, at: int
) -> FactoryDecision | None:
    store = deps.factory_store
    for decision in store.list_decisions(factory_id=factory_id, status="answered", limit=200):
        if decision.delivered_mail_id == mail_id and decision.delivered_at is None:
            return store.update_decision(decision.id, delivered_at=at)
    return None


async def _receipt_on_delivery(app_state: Any, event: FactoryEvent) -> None:
    """Fill ``delivered_at`` when the mailer delivers an answer's mail."""
    if event.kind != "message.delivered":
        return
    # Answers are server mail; a message an agent sent is never a receipt.
    if event.from_id or (event.data or {}).get("sender_id"):
        return
    mail_id = _event_mail_id(event)
    if mail_id is None:
        return
    deps = factory_deps(app_state)
    filled = await asyncio.to_thread(
        _fill_receipt, deps, norm_id(event.factory_id), mail_id, event.ts // 1000
    )
    if filled is not None:
        _logger.info("factory: %s answer delivered (%s)", filled.ref, mail_id)


events.add_listener(_receipt_on_delivery)


__all__ = [
    "ANSWER_TEXT_MAX_CHARS",
    "AnswerResult",
    "GrantPlan",
    "answer_decision",
    "answer_text",
    "decision_view",
    "require_assigned",
    "route_answer",
    "withdraw_decision",
    "withdraw_text",
]
