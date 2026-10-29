"""Loop escalation: decisions and project-lead mail when a loop goes bad, and the answers.

In ``assist`` and ``enforce`` an exhausted, stalled or thrashing loop opens a
``loop`` decision (operators by default; a money raise goes to owners) and
mails the project lead what to stop::

    [Factory loop · EXHAUSTED · T-12 route · implement 3/3 · D-31] Do not dispatch route
    again until D-31 is answered.

Answering it (:func:`plan_answer`, run by :mod:`.decisions` inside the
answer's compare-and-set) updates the loop and the task and mails the PL
exactly what to do: ``BUDGET RAISED``, ``SWITCH VENDOR`` or ``FRESH
RESTART``; ``abandon_task`` and ``mark_done`` answer as a decision with an
instruction line; ``pause`` and ``dismiss`` run as on any server decision.
Operators may raise attempts by one; money is for owners. A READY claim that
only pending gates disagree with gets ``[Factory loop · WAIT · …]`` instead
(:func:`wait_notice`): it opens nothing and uses no claim attempt. A wait past
the claim loop's ``wait_h`` stalls it: a ``Keep waiting`` decision.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from omnigent.entities.factory import FactoryDecision, FactoryLoop, FactoryPr, FactoryTask
from omnigent.factory import (
    ACTION_ABANDON_TASK,
    ACTION_DISMISS,
    ACTION_FRESH_RESTART,
    ACTION_MARK_DONE,
    ACTION_PAUSE,
    ACTION_RAISE_BUDGET,
    ACTION_SWITCH_VENDOR,
    LABEL_STATE,
    STATE_DONE,
    STATE_PAUSED,
)
from omnigent.server.auth import LEVEL_MANAGE
from omnigent.server.factory import control, events, issues, server_decisions, tasks
from omnigent.server.factory.actor import Actor
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.envelope import quote_spoofed_lines
from omnigent.server.factory.loops import (
    CLAIM_EARLY,
    DEFAULT_BUDGETS,
    Change,
    _spent,
    claim_status,
    engine_mode,
    limit_of,
    task_label,
    wait_words,
)
from omnigent.stores.factory_store import norm_id

if TYPE_CHECKING:
    from omnigent.entities import Conversation

_logger = logging.getLogger(__name__)

SOURCE_LOOP: Final = "loop"
SOURCE_DONE_CHECK: Final = "done_check"
LOOP_SOURCES: Final = frozenset({SOURCE_LOOP, SOURCE_DONE_CHECK})
INTERCEPTED: Final = frozenset(
    {
        ACTION_RAISE_BUDGET,
        ACTION_SWITCH_VENDOR,
        ACTION_FRESH_RESTART,
        ACTION_ABANDON_TASK,
        ACTION_MARK_DONE,
    }
)
"""Actions a loop decision runs here; ``pause`` and ``dismiss`` run as on any decision."""
OTHER_VENDOR: Final = {"ic": "ic_codex", "ic_codex": "ic"}
_BAD: Final = ["exhausted", "stalled", "thrashing"]
_DECIDED: Final = ("implement", "review", "demo", "claim")
"""Loop kinds that open a decision when they go bad, in reconcile order."""
_STATE_WORD: Final = {"exhausted": "EXHAUSTED", "stalled": "STALLED", "thrashing": "THRASHING"}


def loop_target(loop: FactoryLoop) -> str:
    """:returns: ``loop:<id>``, the target of a loop decision's options."""
    return f"loop:{loop.id}"


def _option(option_id: str, label: str, action: str, target: str, **extra: Any) -> dict[str, Any]:
    return {"id": option_id, "label": label, "action": action, "target": target, **extra}


# ── Reading the world ──────────────────────────────────────────


@dataclass
class LoopFacts:
    """A loop with its task, PR and project lead (synchronous read)."""

    loop: FactoryLoop
    task: FactoryTask | None = None
    pr: FactoryPr | None = None
    pl: Conversation | None = None
    base: str = "main"


def read_facts(deps: FactoryDeps, loop: FactoryLoop) -> LoopFacts:
    """Load what the texts and actions of *loop* need (synchronous)."""
    from omnigent.server.factory.charter import project_base_branch

    store = deps.factory_store
    task = store.get_task(loop.task_id) if loop.task_id else None
    pr = store.get_pr(loop.pr_id) if loop.pr_id else None
    if task is None and pr is not None and pr.task_id:
        task = store.get_task(pr.task_id)
    project = store.get_project(loop.factory_id, loop.project_slug) if loop.project_slug else None
    pl = control.find_project_pl(deps, loop.factory_id, loop.project_slug)
    base = project_base_branch(project) if project is not None else "main"
    return LoopFacts(loop=loop, task=task, pr=pr, pl=pl, base=base)


def _what(facts: LoopFacts) -> str:
    """The loop's name in texts, e.g. ``T-12 route`` or ``PR #9631 @5e1c2d4``."""
    loop = facts.loop
    if loop.kind == "implement" and facts.task is not None:
        return task_label(facts.task)
    if facts.pr is not None:
        head = loop.subject.rpartition("@")[2] if "@" in loop.subject else ""
        return f"PR #{facts.pr.number}" + (f" @{head}" if head else "")
    return str(_spent(loop).get("label") or loop.subject)


def _count(loop: FactoryLoop) -> str:
    limit = limit_of(loop)
    return f"{loop.kind} {loop.iteration}/{limit}" if limit else f"{loop.kind} {loop.iteration}"


def worker_of(deps: FactoryDeps, task: FactoryTask | None) -> Conversation | None:
    """:returns: The task's newest implement worker (``ic`` / ``ic_codex``) that is still open."""
    if task is None:
        return None
    store = deps.conversation_store
    for session_id in reversed(task.session_ids or []):
        conv = store.get_conversation(session_id)
        if (
            conv is not None
            and conv.sub_agent_name in OTHER_VENDOR
            and not control.is_closed(conv)
        ):
            return conv
    return None


# ── Escalation ─────────────────────────────────────────────────


def _findings_diff(loop: FactoryLoop) -> str | None:
    rounds = _spent(loop).get("rounds") or []
    if not rounds:
        return None
    lines = []
    for index, item in enumerate(rounds, start=1):
        prints = ", ".join(item.get("findings") or []) or "no fingerprints"
        blocking = item.get("blocking")
        count = f"{blocking} blocking" if isinstance(blocking, int) else "blocking ?"
        lines.append(
            f"round {index} @{str(item.get('sha'))[:7]}: {item.get('verdict')} · {count} · "
            f"{prints}"
        )
    return "Findings by round:\n" + "\n".join(lines)


def decision_for(
    facts: LoopFacts, state: str
) -> tuple[str, str, list[dict[str, Any]], str | None]:
    """
    The question, context, options and assignee of a loop's escalation.

    :returns: ``(question, context, options, assignee or None for the default)``.
    """
    loop = facts.loop
    target = loop_target(loop)
    what = _what(facts)
    reason = loop.last_reason or state
    question = f"{what}: {_count(loop)} {state} - what next?"
    context = [f"Why: {reason}", f"Last verdict: {loop.last_verdict or '-'}"]
    pause = (
        [_option("pause", "Pause project", ACTION_PAUSE, f"project:{loop.project_slug}")]
        if loop.project_slug
        else []
    )
    task = facts.task
    if loop.kind in ("implement", "review") and task is not None:
        harness = tasks.harness_of(task)
        other = OTHER_VENDOR.get(harness, "ic_codex")
        switched = bool(_spent(loop).get("switched"))
        money = bool(_spent(loop).get("usd")) and state == "exhausted" and "$" in reason
        options: list[dict[str, Any]] = []
        if money:
            usd = float((loop.spec or {}).get("usd") or 0.0) or 15.0
            options.append(
                _option(
                    "raise_usd",
                    f"Raise to ${usd * 2:.0f}",
                    ACTION_RAISE_BUDGET,
                    target,
                    usd=round(usd, 2),
                    owner_only=True,
                    description="Money: owners only",
                )
            )
        elif loop.kind == "review":
            options.append(
                _option("raise", "One more review round", ACTION_RAISE_BUDGET, target, rounds=1)
            )
        elif state == "stalled":
            options.append(_option("wait", "Keep waiting", ACTION_RAISE_BUDGET, target, wait=True))
        else:
            options.append(_option("raise", "+1 attempt", ACTION_RAISE_BUDGET, target, attempts=1))
        options += [
            _option(
                "switch",
                f"Switch vendor → {other}",
                ACTION_SWITCH_VENDOR,
                target,
                vendor=other,
                recommended=not switched,
            ),
            _option("fresh", "Fresh restart", ACTION_FRESH_RESTART, target, recommended=switched),
            _option("abandon", "Abandon task", ACTION_ABANDON_TASK, target),
        ]
        if money:
            options = [options[0], options[-1], *pause]
        diff = _findings_diff(loop) if loop.kind == "review" else None
        if diff:
            context.append(diff)
        return question, "\n".join(context), options, None
    if loop.kind == "demo":
        return (
            question,
            "\n".join(context),
            [
                _option(
                    "no_demo",
                    "Review without a demo",
                    ACTION_MARK_DONE,
                    target,
                    recommended=True,
                    description="Label the PR factory:no-env",
                ),
                _option(
                    "retry", "Fix the env, then retry", ACTION_RAISE_BUDGET, target, per_head=1
                ),
                *pause,
            ],
            None,
        )
    if loop.kind == "claim" and state == "stalled":
        return (
            question,
            "\n".join(
                [
                    *context,
                    "A gate the PL's early READY claim waits on has not finished (a hung CI "
                    "run?). The PR turns ready by itself once its gates pass.",
                ]
            ),
            [
                _option(
                    "wait",
                    "Keep waiting",
                    ACTION_RAISE_BUDGET,
                    target,
                    wait=True,
                    recommended=True,
                ),
                *pause,
                _option("dismiss", "Dismiss", ACTION_DISMISS, target),
            ],
            None,
        )
    if loop.kind == "claim":
        return (
            question,
            "\n".join(
                [*context, "The PL's READY claims disagree with the server's gates or Sentinel."]
            ),
            [
                _option(
                    "fresh",
                    "Fresh restart the PL from its plan",
                    ACTION_FRESH_RESTART,
                    target,
                    recommended=True,
                ),
                _option("dismiss", "Dismiss", ACTION_DISMISS, target),
            ],
            "owners",
        )
    return (
        question,
        "\n".join(context),
        [
            _option("raise", "Raise the budget", ACTION_RAISE_BUDGET, target, rounds=1),
            *pause,
            _option("dismiss", "Dismiss", ACTION_DISMISS, target),
        ],
        None,
    )


def escalation_text(facts: LoopFacts, state: str, ref: str | None) -> str:
    """
    The ``[Factory loop · EXHAUSTED|STALLED|THRASHING · …]`` mail to the project lead.

    :param ref: The decision, e.g. ``D-31`` (``None`` when none was opened).
    """
    loop = facts.loop
    word = _STATE_WORD.get(state, state.upper())
    tail = f" · {ref}" if ref else ""
    head = f"[Factory loop · {word} · {_what(facts)} · {_count(loop)}{tail}]"
    why = f"Why: {loop.last_reason or state}."
    until = f" until {ref} is answered" if ref else ""
    if loop.kind == "verify":
        pr = facts.pr
        number = pr.number if pr is not None else "<n>"
        sha = pr.head_sha if pr is not None and pr.head_sha else "<sha>"
        if "env" in (loop.last_reason or ""):
            body = (
                "Restart env_keeper: close its session (sys_session_close), dispatch a fresh "
                f'env_keeper titled "env" with UP base={facts.base}, then PREVIEW pr={number} '
                f"head={sha} base={facts.base} again."
            )
        else:
            body = (
                "The head keeps failing its env checks: send the failures to the author for a fix."
            )
        return f"{head} {body}\n{why}"
    if facts.task is not None and loop.kind in ("implement", "review"):
        body = f"Do not dispatch {facts.task.key} again{until}."
    elif loop.kind == "claim" and state == "stalled" and facts.pr is not None:
        body = (
            f"PR #{facts.pr.number} still waits on its gates: re-run a check that hung, do not "
            "claim READY again; the server sends READY FOR HUMAN MERGE when they pass."
        )
    elif facts.pr is not None:
        body = f"Hold PR #{facts.pr.number}{until}."
    else:
        body = f"Hold it{until}."
    return f"{head} {body}\n{why} Keep the rest of the project moving."


async def escalate(
    app_state: Any, deps: FactoryDeps, loop: FactoryLoop, change: Change, mode: str
) -> None:
    """A loop went bad: open its decision and mail the PL (assist and enforce). Never raises."""
    try:
        await _escalate(app_state, deps, loop, change.what, mode)
    except Exception:  # noqa: BLE001 - the loop state and its incident stand without it
        _logger.warning("factory loops: could not escalate %s", loop.id, exc_info=True)


async def _escalate(
    app_state: Any, deps: FactoryDeps, loop: FactoryLoop, state: str, mode: str
) -> None:
    store = deps.factory_store
    current = await asyncio.to_thread(store.get_loop, loop.id)
    if current is None or current.state != state:
        return
    facts = await asyncio.to_thread(read_facts, deps, current)
    fid = current.factory_id
    if current.kind == "verify":
        if facts.pl is not None:
            control.send_text(
                app_state, facts.pl.id, escalation_text(facts, state, None), kind="info"
            )
        return
    if current.kind in ("implement", "review") and facts.task is not None:
        # One decision per task: whichever of its implement and review loops went bad
        # second rides on the other's open decision.
        shared = await asyncio.to_thread(_task_decision, deps, current, facts.task)
        if shared is not None:
            await asyncio.to_thread(store.update_loop, current.id, decision_id=shared.id)
            return
    if current.kind in ("plan", "ci", "intake", "improve"):
        return
    question, context, options, assignee = decision_for(facts, state)
    # A claim's hung wait asks something else than its refuted claims: its own decision.
    wait = current.kind == "claim" and state == "stalled"
    opened = await server_decisions.open_decision(
        app_state,
        fid,
        source=SOURCE_LOOP,
        dedupe_key=f"loop:{current.id}" + (":wait" if wait else ""),
        question=question,
        context=context,
        options=options,
        blocking=True,
        project=current.project_slug,
        asked_by=None,
        assignee=assignee,
    )
    if opened is None:
        return
    decision, deduped = opened
    await asyncio.to_thread(store.update_loop, current.id, decision_id=decision.id)
    previous = current.decision_id
    if current.kind == "claim" and previous and norm_id(previous) != norm_id(decision.id):
        # A wait that stalled, then ended refuted: its "Keep waiting?" no longer applies.
        await server_decisions.expire_decision(app_state, previous)
    if deduped:
        return
    await events.emit(
        app_state,
        fid,
        "loop.escalated",
        severity="warning",
        project=current.project_slug,
        subject=f"decision:{decision.ref}",
        summary=(
            f"{_what(facts)}: {_count(current)} {state} → {decision.ref} ({decision.assignee})"
        ),
        data={
            "loop_id": current.id,
            "kind": current.kind,
            "state": state,
            "decision_id": decision.id,
            "decision": decision.ref,
            "assignee": decision.assignee,
            "mode": mode,
            "by": "server",
            "by_kind": "server",
        },
    )
    if facts.pl is not None:
        control.send_text(
            app_state, facts.pl.id, escalation_text(facts, state, decision.ref), kind="info"
        )


def _task_decision(
    deps: FactoryDeps, loop: FactoryLoop, task: FactoryTask
) -> FactoryDecision | None:
    """The open decision of another bad implement or review loop of *task* (synchronous)."""
    store = deps.factory_store
    rows = store.list_loops(
        loop.factory_id, kinds=["implement", "review"], states=_BAD, task_id=task.id
    )
    for other in rows:
        if other.id == loop.id or not other.decision_id:
            continue
        decision = store.get_decision(other.decision_id)
        if decision is not None and decision.status == "open":
            return decision
    return None


# ── Answered or resolved: settle and reconcile ─────────────────


def resumed_text(facts: LoopFacts, ref: str) -> str:
    """
    The ``[Factory loop · RESUMED · …]`` mail: the loop runs again before anyone answered.

    :param ref: The decision closed with it, e.g. ``D-31``.
    """
    loop = facts.loop
    head = f"[Factory loop · RESUMED · {_what(facts)} · {_count(loop)} · {ref}]"
    why = f"Why: {quote_spoofed_lines(loop.last_reason or 'the loop runs again')}."
    task = facts.task
    if task is not None and loop.kind in ("implement", "review"):
        body = (
            f"{ref} is closed and the hold on {task.key} is lifted: carry on (a new worker "
            f'needs sys_factory_task(op="start", key="{task.key}") first).'
        )
    else:
        body = f"{ref} is closed and the hold is lifted: carry on."
    return f"{head} {body}\n{why}"


async def settle(app_state: Any, deps: FactoryDeps, changed: list[FactoryLoop]) -> None:
    """
    Loops that left their bad state without an answer close their open decision. Never raises.

    Progress, a person's changes request or budget edit, or the task closing
    can end the stop before anyone answers it. The decision expires (unless a
    sibling loop still rides on it) and, when the loop runs again, the PL is
    told its hold is lifted.
    """
    for loop_id in dict.fromkeys(loop.id for loop in changed):
        try:
            await _settle(app_state, deps, loop_id)
        except Exception:  # noqa: BLE001 - an open stale decision only costs a dismiss
            _logger.warning("factory loops: could not settle %s", loop_id, exc_info=True)


async def _settle(app_state: Any, deps: FactoryDeps, loop_id: str) -> None:
    store = deps.factory_store
    loop = await asyncio.to_thread(store.get_loop, loop_id)
    if loop is None or not loop.decision_id or loop.state in _BAD:
        return
    decision = await asyncio.to_thread(store.get_decision, loop.decision_id)
    if decision is None or decision.status != "open":
        return
    if await asyncio.to_thread(_still_shared, deps, loop, decision.id):
        return
    expired = await server_decisions.expire_decision(app_state, decision.id)
    if expired is None or loop.state != "running":
        return
    facts = await asyncio.to_thread(read_facts, deps, loop)
    if facts.pl is not None:
        control.send_text(app_state, facts.pl.id, resumed_text(facts, expired.ref), kind="info")


def _still_shared(deps: FactoryDeps, loop: FactoryLoop, decision_id: str) -> bool:
    """:returns: Whether another bad loop still rides on *decision_id*."""
    return any(
        other.id != loop.id and norm_id(other.decision_id or "") == norm_id(decision_id)
        for other in deps.factory_store.list_loops(loop.factory_id, states=_BAD)
    )


def _runs(deps: FactoryDeps, factory_id: str, slug: str | None) -> bool:
    """:returns: Whether the factory and the project of *slug* run (neither paused nor done)."""
    root = deps.conversation_store.get_conversation(factory_id)
    if root is not None and root.labels.get(LABEL_STATE) == STATE_PAUSED:
        return False
    project = deps.factory_store.get_project(factory_id, slug) if slug else None
    if project is None or project.status not in (None, "active"):
        return False
    pl = control.find_project_pl(deps, factory_id, slug)
    return pl is not None and pl.labels.get(LABEL_STATE) not in (STATE_PAUSED, STATE_DONE)


def _answered_action(decision: FactoryDecision) -> str | None:
    """:returns: The action of the option an answered decision chose."""
    for option in decision.options or []:
        if option.get("id") == decision.answer_option_id:
            return str(option.get("action") or "") or None
    return None


async def reconcile(app_state: Any, deps: FactoryDeps, factory_id: str) -> None:
    """
    The tick's pass over bad loops whose decision was answered without a loop action.

    ``Pause project`` leaves the loop stopped: once the project and the factory
    run again, a new decision opens (and the PL is told to hold again); a
    project that ended meanwhile is not asked. A claim's fresh restart (the PL
    restarted from its plan) starts the claim loop over. Dismiss, withdraw and
    expiry leave the loop as it is. Never raises.
    """
    try:
        mode = await asyncio.to_thread(engine_mode, deps, factory_id)
        if mode == "observe":
            return
        store = deps.factory_store
        rows = await asyncio.to_thread(
            store.list_loops, factory_id, kinds=list(_DECIDED), states=_BAD
        )
        pending = [(row, row.decision_id) for row in rows if row.decision_id]
        # Implement first: a review loop of the same task then rides on its new decision.
        for loop, decision_id in sorted(pending, key=lambda pair: _DECIDED.index(pair[0].kind)):
            decision = await asyncio.to_thread(store.get_decision, decision_id)
            if decision is None or decision.status != "answered":
                continue
            action = _answered_action(decision)
            if action == ACTION_PAUSE:
                if await asyncio.to_thread(_runs, deps, factory_id, loop.project_slug):
                    await _escalate(app_state, deps, loop, loop.state, mode)
            elif action == ACTION_FRESH_RESTART and loop.kind == "claim":
                spent = {**_spent(loop), "streak": 0}
                await asyncio.to_thread(
                    store.update_loop, loop.id, state="running", ended_at=None, spent=spent
                )
    except Exception:  # noqa: BLE001 - the next tick retries
        _logger.warning("factory loops: reconcile failed for %s", factory_id, exc_info=True)


def wait_text(facts: LoopFacts, pending: Sequence[str], sha7: str) -> str:
    """The ``[Factory loop · WAIT · PR #<n> · CI pending on <sha7>]`` mail: an early claim."""
    head = f"[Factory loop · WAIT · {_what(facts)} · {wait_words(pending)} pending on {sha7}]"
    body = (
        "Your READY claim came early: no gate failed, so it uses no claim attempt. Do not "
        "claim again; the server marks the PR ready and sends READY FOR HUMAN MERGE when "
        "the gates pass."
    )
    gates = facts.pr.gates if facts.pr is not None and isinstance(facts.pr.gates, dict) else {}
    # One flattened line after "Pending:", so a reason never starts a line of its own.
    reasons = [" ".join(str(r).split())[:200] for r in gates.get("reasons") or [] if r][:3]
    if any(reasons):
        body += "\nPending: " + "; ".join(r for r in reasons if r)
    return f"{head} {body}"


async def wait_notice(
    app_state: Any, deps: FactoryDeps, loop: FactoryLoop, change: Change
) -> None:
    """
    Tell the PL its READY claim came early, unless it landed meanwhile. Never raises.

    Landed: the loop no longer waits on that head, or the PR's gates now hold
    or refute the claim (the PL gets the READY FOR HUMAN MERGE or the
    checks-failed notice instead).
    """
    try:
        current = await asyncio.to_thread(deps.factory_store.get_loop, loop.id)
        sha7 = str(change.data.get("head") or "")
        if current is None or _spent(current).get("waiting") != sha7:
            return
        facts = await asyncio.to_thread(read_facts, deps, current)
        if facts.pl is None:
            return
        if facts.pr is not None and claim_status(facts.pr, sha7)[0] != CLAIM_EARLY:
            return
        pending = [str(g) for g in change.data.get("pending") or []]
        control.send_text(app_state, facts.pl.id, wait_text(facts, pending, sha7), kind="info")
    except Exception:  # noqa: BLE001 - the loop.waiting event already records it
        _logger.warning("factory loops: no WAIT notice for %s", loop.id, exc_info=True)


async def violation(app_state: Any, loop: FactoryLoop, change: Change) -> None:
    """A dispatch past the budget: a finding Sentinel's retro reads. Never raises."""
    try:
        await issues.raise_issue(
            app_state,
            loop.factory_id,
            rule="loop.budget_violation",
            target=f"loop:{loop.id}",
            summary=change.summary,
            class_=issues.FINDING,
            severity="notice",
            project=loop.project_slug,
            evidence={k: change.data.get(k) for k in ("attempt", "limit", "via")},
            source="server",
        )
    except Exception:  # noqa: BLE001 - the event already records it
        _logger.warning("factory loops: no finding for %s", loop.id, exc_info=True)


# ── Done check (L7) ────────────────────────────────────────────


async def done_checks(app_state: Any, deps: FactoryDeps, factory_id: str, now: float) -> None:
    """
    Ask "Mark done?" for an active project whose tasks all closed a while ago (assist+).

    Never raises.
    """
    try:
        mode = await asyncio.to_thread(engine_mode, deps, factory_id)
        if mode == "observe":
            return
        from omnigent.server.factory.loops import DONE_CHECK_GRACE_S

        rows = await asyncio.to_thread(
            deps.factory_store.list_loops, factory_id, kinds=["plan"], states=["converged"]
        )
        for loop in rows:
            done_at = _spent(loop).get("all_done_at")
            if not isinstance(done_at, int) or now - done_at < DONE_CHECK_GRACE_S:
                continue
            if _spent(loop).get("done_asked") or not loop.project_slug:
                continue  # asked once per episode; a reopened task starts a new one
            project = await asyncio.to_thread(
                deps.factory_store.get_project, factory_id, loop.project_slug
            )
            if project is None or project.status not in (None, "active"):
                continue
            await asyncio.to_thread(
                deps.factory_store.update_loop,
                loop.id,
                spent={**_spent(loop), "done_asked": True},
            )
            await server_decisions.open_decision(
                app_state,
                factory_id,
                source=SOURCE_DONE_CHECK,
                dedupe_key=f"done_check:{loop.project_slug}",
                question=f"Every task of {loop.project_slug} is closed. Mark it done?",
                context=f"{loop.last_reason or ''}".strip() or None,
                options=[
                    _option(
                        "done",
                        "Mark done",
                        ACTION_MARK_DONE,
                        loop_target(loop),
                        recommended=True,
                    ),
                    _option("not_yet", "Not yet", ACTION_DISMISS, loop_target(loop)),
                ],
                blocking=False,
                project=loop.project_slug,
                asked_by=None,
            )
    except Exception:  # noqa: BLE001 - the next tick retries
        _logger.warning("factory loops: done check failed for %s", factory_id, exc_info=True)


# ── Answers ────────────────────────────────────────────────────


@dataclass
class LoopAnswer:
    """
    A validated answer to a loop decision, run after the compare-and-set.

    :param pl: The project lead mailed the outcome.
    :param raised: ``(before, after)`` of what was raised, e.g. ``("3", "4")``.
    """

    action: str
    target: str
    decision: FactoryDecision
    option: dict[str, Any]
    facts: LoopFacts
    raised: tuple[str, str] | None = None
    vendor: str | None = None
    worker: Conversation | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def pl(self) -> Conversation | None:
        """:returns: The PL the outcome goes to."""
        return self.facts.pl

    def text(self, by: str) -> str:
        """:returns: The exact mail the PL gets for this answer."""
        return answer_text(self, by)


@dataclass
class BudgetAnswer:
    """An owner raised a project's $ cap from a budget decision."""

    action: str
    target: str
    decision: FactoryDecision
    slug: str
    before: float
    after: float
    pl: Conversation | None

    def text(self, by: str) -> str:
        """:returns: The ``[Factory loop · BUDGET RAISED · <slug> · project $a→$b …]`` mail."""
        return (
            f"[Factory loop · BUDGET RAISED · {self.slug} · project ${self.before:.0f}→"
            f"${self.after:.0f} by {by} · {self.decision.ref}] Carry on; the project's spend is "
            "now measured against the new cap."
        )


def _loop_of(deps: FactoryDeps, decision: FactoryDecision, option: dict[str, Any]) -> FactoryLoop:
    target = str(option.get("target") or "")
    loop = None
    if target.startswith("loop:"):
        try:
            loop = deps.factory_store.get_loop(target[5:])
        except ValueError:
            loop = None
    if loop is None or norm_id(loop.factory_id) != norm_id(decision.factory_id):
        raise CockpitError(404, "unknown_loop", f"{decision.ref} names no loop of this factory")
    return loop


async def plan_answer(
    app_state: Any,
    decision: FactoryDecision,
    option: dict[str, Any] | None,
    user_id: str | None,
) -> LoopAnswer | BudgetAnswer | control.ControlPlan | None:
    """
    Validate an answer to a ``loop`` or ``done_check`` decision before it is stored.

    :returns: The loop answer to run, a control plan (a PL's fresh restart),
        or ``None`` when the generic decision path handles the option.
    :raises CockpitError: 404 unknown loop, 409 no project lead, 403 money by a non-owner.
    """
    if option is None or option.get("action") not in INTERCEPTED or option.get("href"):
        return None
    deps = factory_deps(app_state)
    if str(option.get("target") or "").startswith("budget:project:"):
        return await _plan_budget(deps, decision, option, user_id)
    loop = await asyncio.to_thread(_loop_of, deps, decision, option)
    facts = await asyncio.to_thread(read_facts, deps, loop)
    action = str(option["action"])
    if option.get("owner_only") or option.get("usd"):
        await control.require(deps, user_id, decision.factory_id, LEVEL_MANAGE)
    if action == ACTION_FRESH_RESTART and loop.kind == "claim":
        if facts.pl is None:
            raise CockpitError(409, "no_project_lead", f"{decision.ref} has no project lead")
        return await control.plan_control(
            app_state,
            decision.factory_id,
            user_id=user_id,
            action=control.ACTION_RESTART,
            target=f"{control.TARGET_MEMBER_PREFIX}{facts.pl.id}",
            note=(
                f"{decision.ref}: restart from plan.md; trust the server's gates before a "
                "READY claim"
            ),
        )
    if facts.pl is None:
        raise CockpitError(409, "no_project_lead", f"{decision.ref} has no project lead to tell")
    answer = LoopAnswer(
        action=action,
        target=loop_target(loop),
        decision=decision,
        option=dict(option),
        facts=facts,
    )
    spec = dict(loop.spec or {})
    if action == ACTION_RAISE_BUDGET:
        key, step = _raise_key(loop, option)
        # A claim loop from before ``wait_h`` existed waited the default (the step) so far.
        before = spec.get(key) or (step if key == "wait_h" else 0)
        after = before + step if key != "usd" else round(float(before) * 2 or step, 2)
        answer.raised = (_fmt(key, before), _fmt(key, after))
        answer.extra = {"key": key, "value": after}
    elif action in (ACTION_SWITCH_VENDOR, ACTION_FRESH_RESTART):
        if facts.task is None:
            raise CockpitError(409, "no_task", f"{decision.ref} has no task to re-dispatch")
        cap = int(spec.get("attempts") or 0) if loop.kind == "implement" else None
        attempts = cap if cap is not None else facts.task.attempts
        answer.raised = (str(attempts), str(attempts + 1))
        answer.worker = await asyncio.to_thread(worker_of, deps, facts.task)
        if action == ACTION_SWITCH_VENDOR:
            harness = tasks.harness_of(facts.task)
            answer.vendor = str(option.get("vendor") or OTHER_VENDOR.get(harness, "ic_codex"))
    return answer


async def _plan_budget(
    deps: FactoryDeps, decision: FactoryDecision, option: dict[str, Any], user_id: str | None
) -> BudgetAnswer:
    """Money is for owners: a project's $ raise needs MANAGE on the root."""
    from omnigent.server.factory.budgets import project_limit

    await control.require(deps, user_id, decision.factory_id, LEVEL_MANAGE)
    slug = str(option["target"]).removeprefix("budget:project:")
    before = await asyncio.to_thread(project_limit, deps, decision.factory_id, slug)
    value = option.get("usd")
    after = float(value) if isinstance(value, int | float) and value > 0 else before * 2
    pl = await asyncio.to_thread(control.find_project_pl, deps, decision.factory_id, slug)
    return BudgetAnswer(
        action=ACTION_RAISE_BUDGET,
        target=str(option["target"]),
        decision=decision,
        slug=slug,
        before=before,
        after=after,
        pl=pl,
    )


def _raise_key(loop: FactoryLoop, option: dict[str, Any]) -> tuple[str, float]:
    for key in ("usd", "attempts", "rounds", "per_head"):
        value = option.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
            return key, value
    if option.get("wait"):
        key = "wait_h" if loop.kind == "claim" else "wall_h"
        default = DEFAULT_BUDGETS["claim" if loop.kind == "claim" else "implement"][key]
        return key, float((loop.spec or {}).get(key) or default)
    return ("rounds" if loop.kind == "review" else "attempts"), 1


def _fmt(key: str, value: Any) -> str:
    if key == "usd":
        return f"${float(value):.0f}"
    if key in ("wall_h", "wait_h"):
        return f"{float(value):g} h"
    return str(int(value)) if isinstance(value, int | float) else str(value)


def answer_text(answer: LoopAnswer, by: str) -> str:
    """The exact text the PL gets for an answered loop decision."""
    from omnigent.server.factory.decisions import answer_text as decision_answer

    facts, loop, ref = answer.facts, answer.facts.loop, answer.decision.ref
    task = facts.task
    task_word = task.ref if task is not None else _what(facts)
    key = task.key if task is not None else None
    label = str(answer.option.get("label") or answer.action)
    if answer.action == ACTION_RAISE_BUDGET and answer.raised is not None:
        before, after = answer.raised
        what = answer.extra.get("key")
        noun = loop.kind if what in ("attempts", "rounds", "per_head") else f"{loop.kind} {what}"
        head = (
            f"[Factory loop · BUDGET RAISED · {task_word} · {noun} {before}→{after} by {by} · "
            f"{ref}]"
        )
        if loop.kind == "review":
            body = (
                "Send the blocking findings to the author once more, then re-run the "
                "cross-vendor review at the new head."
            )
        elif loop.kind == "demo":
            body = (
                "Re-run the demo at the PR's head (env_keeper PREVIEW first when its preview "
                "is not ready at that head)."
            )
        elif what == "wait_h":
            number = facts.pr.number if facts.pr is not None else "<n>"
            body = (
                f"Keep waiting for the gates of PR #{number}; do not claim READY again: the "
                "server sends READY FOR HUMAN MERGE when they pass."
            )
        elif what == "wall_h":
            body = f"Keep {key} running; give its worker the next step if it waits on you."
        else:
            body = (
                f'Dispatch {key} again: sys_factory_task(op="start", key="{key}"), then send '
                "its title_to_use and card_text verbatim."
            )
        return f"{head} {body}"
    if answer.action == ACTION_SWITCH_VENDOR and answer.raised is not None and task is not None:
        before, after = answer.raised
        vendor = answer.vendor or "ic_codex"
        return (
            f"[Factory loop · SWITCH VENDOR · {task.ref} · implement {before}→{after} · "
            f"{tasks.harness_of(task)} → {vendor} by {by} · {ref}] {_close_worker(answer)}"
            f'sys_factory_task(op="start", key="{task.key}") and dispatch its title_to_use and '
            f"card_text to {vendor}, with a fresh context from NOTES.md."
        )
    if answer.action == ACTION_FRESH_RESTART and answer.raised is not None and task is not None:
        before, after = answer.raised
        evidence = quote_spoofed_lines(
            loop.last_reason or loop.last_verdict or "the loop's last verdict"
        )
        title = tasks.title_to_use(task.key, task.attempts + 1)
        return (
            f"[Factory loop · FRESH RESTART · {task.ref} · implement {before}→{after} by {by} · "
            f"{ref}] {_close_worker(answer)}dispatch {title} with a fresh context from NOTES.md "
            f'and this evidence: {evidence} (sys_factory_task(op="start", key="{task.key}") '
            "first; send its card_text)."
        )
    if answer.action == ACTION_ABANDON_TASK:
        hint = (
            f"Abandon {key or 'this task'}: the server cancelled it. Close its IC and demo "
            "sessions, take its preview down, and record it in plan.md."
        )
    elif loop.kind == "demo":
        number = facts.pr.number if facts.pr is not None else "<n>"
        hint = (
            f"Review PR #{number} without a demo: label it factory:no-env "
            f"(gh api repos/<owner>/<repo>/issues/{number}/labels -f 'labels[]=factory:no-env') "
            "and continue to readiness."
        )
    elif loop.kind == "plan":
        hint = "A person marked the project done: run DONE now (final report, close the team)."
    else:
        hint = f"{key or 'The task'} is marked done by a person: close its worker and move on."
    return decision_answer(answer.decision, by, label, None, hint)


def _close_worker(answer: LoopAnswer) -> str:
    worker = answer.worker
    if worker is None:
        return "No worker of it is open; "
    return f"Close worker {worker.id} (sys_session_close); then "


async def execute_answer(
    app_state: Any, decision: FactoryDecision, answer: LoopAnswer | BudgetAnswer
) -> None:
    """
    Apply an answered loop decision to the loop and its task (``loop.budget_raised`` etc).

    :raises Exception: Store failures (the decision stays answered; the caller reports 502).
    """
    if isinstance(answer, BudgetAnswer):
        from omnigent.server.factory import budgets

        await budgets.raise_project(app_state, decision.factory_id, answer.slug, answer.after)
        await events.emit(
            app_state,
            decision.factory_id,
            "budget.raised",
            severity="notice",
            project=answer.slug,
            subject=f"budget:project:{answer.slug}",
            summary=f"{answer.slug}: budget ${answer.before:.0f} → ${answer.after:.0f} "
            f"({decision.ref}, {decision.answered_by or 'an owner'})",
            data={
                "scope": f"project:{answer.slug}",
                "before": answer.before,
                "limit": answer.after,
                "decision_id": decision.id,
                "by": decision.answered_by,
            },
        )
        return
    deps = factory_deps(app_state)
    store = deps.factory_store
    loop = await asyncio.to_thread(store.get_loop, answer.facts.loop.id) or answer.facts.loop
    task = answer.facts.task
    server = Actor.server()
    moved: list[tasks.TaskEvent] = []
    fields: dict[str, Any] = {}
    kind_event: str | None = None
    if answer.action == ACTION_RAISE_BUDGET:
        key, value = answer.extra["key"], answer.extra["value"]
        spec = {**(loop.spec or {}), key: value}
        if key == "per_head" and loop.kind == "demo":
            spec["per_pr_heads"] = int((loop.spec or {}).get("per_pr_heads") or 6) + 1
        values = {**_spent(loop), "progress_at": int(decision.answered_at or loop.updated_at)}
        if key == "usd":
            values["usd_near"] = False  # budget.near fires again at 80 % of the new cap
        fields = {"spec": spec, "state": "running", "ended_at": None, "spent": values}
        if task is not None and loop.kind == "implement" and key in ("attempts", "usd"):
            budget = {**(task.budget or {}), key: value}
            moved = await _task_write(deps, task, budget=budget)
        kind_event = "loop.budget_raised"
    elif answer.action in (ACTION_SWITCH_VENDOR, ACTION_FRESH_RESTART) and task is not None:
        implement = await asyncio.to_thread(
            store.get_loop_by_subject, loop.factory_id, "implement", f"task:{norm_id(task.id)}"
        )
        target = implement or loop
        cap = int((target.spec or {}).get("attempts") or 0) or task.attempts
        spec = {**(target.spec or {}), "attempts": cap + 1}
        values = {**_spent(target), "progress_at": int(decision.answered_at or target.updated_at)}
        if answer.action == ACTION_SWITCH_VENDOR:
            values["switched"] = True
        await asyncio.to_thread(
            store.update_loop, target.id, spec=spec, state="running", ended_at=None, spent=values
        )
        if target.id != loop.id:
            fields = {"state": "running", "ended_at": None}
        budget = {**(task.budget or {}), "attempts": cap + 1}
        card = dict(task.card or {})
        if answer.vendor is not None:
            card["harness"] = answer.vendor
        moved = await _task_write(deps, task, budget=budget, card=card)
        kind_event = "loop.budget_raised"
    elif answer.action == ACTION_ABANDON_TASK:
        fields = {"state": "cancelled", "ended_at": int(decision.answered_at or loop.updated_at)}
        if task is not None and task.state not in ("done", "cancelled"):
            moved = await _task_move(
                deps, task, "cancelled", f"abandoned ({decision.ref})", server
            )
        kind_event = "loop.cancelled"
    elif answer.action == ACTION_MARK_DONE:
        fields = {"state": "converged", "ended_at": int(decision.answered_at or loop.updated_at)}
        if (
            task is not None
            and loop.kind == "implement"
            and task.state not in ("done", "cancelled")
        ):
            moved = await _task_move(deps, task, "done", f"marked done ({decision.ref})", server)
        kind_event = "loop.converged"
    if fields:
        loop = await asyncio.to_thread(store.update_loop, loop.id, **fields) or loop
    await asyncio.to_thread(_settle_siblings, deps, decision, loop, answer.action)
    await tasks.emit_all(app_state, loop.factory_id, moved)
    if kind_event is not None:
        await events.emit(
            app_state,
            loop.factory_id,
            kind_event,
            severity="notice",
            project=loop.project_slug,
            subject=f"decision:{decision.ref}",
            summary=f"{_what(answer.facts)}: {answer.option.get('label') or answer.action} "
            f"({decision.ref}, {decision.answered_by or 'a person'})",
            data={
                "loop_id": loop.id,
                "kind": loop.kind,
                "state": loop.state,
                "action": answer.action,
                "decision_id": decision.id,
                "raised": list(answer.raised) if answer.raised else None,
                "vendor": answer.vendor,
                "by": decision.answered_by,
            },
        )


def _settle_siblings(
    deps: FactoryDeps, decision: FactoryDecision, loop: FactoryLoop, action: str
) -> None:
    """Loops that rode on the same decision (a review with its implement loop) follow it."""
    store = deps.factory_store
    for other in store.list_loops(loop.factory_id, states=_BAD):
        if other.id == loop.id or not other.decision_id:
            continue
        if norm_id(other.decision_id) != norm_id(decision.id):
            continue
        if action == ACTION_ABANDON_TASK:
            store.update_loop(other.id, state="cancelled", ended_at=decision.answered_at)
        elif action == ACTION_MARK_DONE:
            store.update_loop(other.id, state="converged", ended_at=decision.answered_at)
        else:
            spec = dict(other.spec or {})
            if other.kind == "review":
                spec["rounds"] = int(spec.get("rounds") or 0) + 1
            store.update_loop(other.id, state="running", ended_at=None, spec=spec)


async def _task_write(
    deps: FactoryDeps, task: FactoryTask, **fields: Any
) -> list[tasks.TaskEvent]:
    async with tasks.lock(task.factory_id):
        await asyncio.to_thread(deps.factory_store.update_task, task.id, **fields)
    return []


async def _task_move(
    deps: FactoryDeps, task: FactoryTask, to: str, reason: str, actor: Actor
) -> list[tasks.TaskEvent]:
    def run() -> list[tasks.TaskEvent]:
        current = deps.factory_store.get_task(task.id) or task
        _, event = tasks.move(deps, current, to, actor, reason=reason)
        return [event] if event is not None else []

    async with tasks.lock(task.factory_id):
        return await asyncio.to_thread(run)


ANSWER_TYPES: Final = (LoopAnswer, BudgetAnswer)
"""What :func:`plan_answer` returns besides a control plan."""

__all__ = [
    "ANSWER_TYPES",
    "INTERCEPTED",
    "LOOP_SOURCES",
    "SOURCE_DONE_CHECK",
    "SOURCE_LOOP",
    "BudgetAnswer",
    "LoopAnswer",
    "LoopFacts",
    "answer_text",
    "decision_for",
    "done_checks",
    "escalate",
    "escalation_text",
    "execute_answer",
    "loop_target",
    "plan_answer",
    "read_facts",
    "reconcile",
    "resumed_text",
    "settle",
    "violation",
    "wait_notice",
    "wait_text",
    "worker_of",
]
