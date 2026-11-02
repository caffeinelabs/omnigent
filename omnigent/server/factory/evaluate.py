"""Trial evaluation: does a Tier-1 change on trial keep, revert or go to the owners?

At the end of a trial (``trial_until``, default 48 h after ``applied_at``) the
trial window ``[applied_at, trial_until)`` is compared with the same-length
baseline right before it, on the parent version:

- every guard metric (:data:`.telemetry.metrics.GUARDS`) must hold: merge
  acceptance ≥ baseline − 10 pp, $ per merged PR ≤ +10 %, incidents ≤
  baseline + 1, human prompts per day ≤ +20 %, loop exhaustion ≤ +10 pp;
- the proposal's ``expected_effect`` metric must move the promised way;
- n must reach ``min_n``: 3 merged PRs, or 24 h of heartbeats for a cadence change.

A regressed guard reverts automatically. Target met, guards held and n ≥ min_n
keeps it. Anything else is ``inconclusive``: an owners decision "Keep or revert
C-n?" (recommended: revert); it is never kept on its own. A trial nobody
decides reverts one original window after it ended (2× its window when it was
never extended).

Trials never overlap on one block (:func:`check_trial`): a second trial on a
block of :mod:`.definition_blocks` that already has one running is refused
with 409 ``trial_overlap``, since two trials on one block confound each
other's guards (today's settings and rules trial side by side, as before).
Strategy and product profile changes never run as a trial (422
``not_trialable``): they are judged on outcomes and apply as a version. Nor
do discovery and Scout's brief until the discovery guards exist.

An idle trial (no merged PR, finished task or sub-agent run in its window, see
:mod:`.idle`) proves nothing either way. Instead of ending inconclusive it is
extended by its original length, at most :data:`MAX_EXTENSIONS` times, with
one line in #improvements each time. Still idle after that, the owners decide
with Keep recommended when nothing got worse: nobody answering within
:data:`KEEP_DEFAULT_AFTER_S` keeps it. An idle trial with negative evidence (a
regressed guard, or an incident or finding that names it, see
:func:`attributed_incidents`) is never extended and keeps the path above; one
that names it before the Keep default applies reverts it instead.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, replace
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import ACTION_KEEP_PROPOSAL, ACTION_REVERT_PROPOSAL, THREAD_IMPROVEMENTS
from omnigent.server.factory import control, definition, events, idle, tuning
from omnigent.server.factory.actor import Actor, proposal_ref
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.telemetry import metrics
from omnigent.stores.factory_store import ProposalStateConflict, now_s

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactoryProposal

_logger = logging.getLogger(__name__)

MIN_MERGED: Final = 3
MIN_BEAT_HOURS: Final = 24
OUTCOME_KEPT: Final = "kept"
OUTCOME_REVERTED: Final = "reverted"
OUTCOME_INCONCLUSIVE: Final = "inconclusive"
OUTCOME_EXTENDED: Final = "extended"
"""Not stored: :func:`end_trial`'s answer for an idle trial it extended."""
MAX_EXTENSIONS: Final = 2
KEEP_DEFAULT_AFTER_S: Final = 24 * 3600
"""How long the owners have to answer an idle trial's decision before Keep applies."""
EXTENDED_KIND: Final = "proposal.trial_extended"
DEFAULT_KEEP: Final = "keep"
KEEP_DEFAULT_REASON: Final = "nobody answered in 24 h and nothing got worse"
HARM_FINDING_SOURCES: Final = frozenset({"human", "sentinel"})
"""Filers whose findings count as harm when they name a change (incidents always do)."""


@dataclass
class Guard:
    """One guard metric over the baseline and the trial window."""

    metric: str
    baseline: float | None
    trial: float | None
    n_baseline: int
    n_trial: int
    allowed: str
    status: str = "ok"


@dataclass
class Evaluation:
    """The verdict on one trial."""

    outcome: str
    reason: str
    n: int
    min_n: int
    target: dict[str, Any] = field(default_factory=dict)
    guards: list[Guard] = field(default_factory=list)
    trial_window: tuple[int, int] = (0, 0)
    baseline_window: tuple[int, int] = (0, 0)
    evaluated_at: int = 0
    activity: dict[str, int] = field(default_factory=dict)
    idle: bool = False
    """Nothing merged, finished or ran in the trial window (see :mod:`.idle`)."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _regressed(rule: str, limit: float, base: float | None, trial: float | None) -> bool | None:
    if base is None or trial is None:
        return None
    if rule == "pp_drop":
        return trial < base - limit
    if rule == "pp_rise":
        return trial > base + limit
    if rule == "abs_rise":
        return trial > base + limit
    if rule == "pct_rise":
        return base > 0 and trial > base * (1 + limit)
    return None


def _allowed(rule: str, limit: float) -> str:
    return {
        "pp_drop": f"≥ baseline − {limit * 100:g} pp",
        "pp_rise": f"≤ baseline + {limit * 100:g} pp",
        "abs_rise": f"≤ baseline + {limit:g}",
        "pct_rise": f"≤ +{limit * 100:g} %",
    }.get(rule, rule)


def _cadence_only(row: FactoryProposal) -> bool:
    paths = list((row.after or {}).keys())
    return bool(paths) and all(str(p).startswith("cadence.") for p in paths)


def evaluate_trial(deps: FactoryDeps, row: FactoryProposal, now: int) -> Evaluation:
    """Compare *row*'s trial window with its baseline (synchronous)."""
    applied = int(row.applied_at or row.created_at)
    end = int(row.trial_until or now)
    span = max(3600, end - applied)
    trial = metrics.Window(applied, min(end, now) if min(end, now) > applied else applied + 1)
    baseline = metrics.Window(applied - span, applied)
    names = list(metrics.GUARDS)
    effect = row.expected_effect or {}
    target_metric = effect.get("metric") if effect.get("metric") in metrics.CATALOG else None
    if target_metric and target_metric not in names:
        names.append(target_metric)
    names.append("throughput")
    now_values = metrics.compute(deps, row.factory_id, names, window=trial, use_cache=False)
    base_values = metrics.compute(deps, row.factory_id, names, window=baseline, use_cache=False)
    guards: list[Guard] = []
    for name, (rule, limit) in metrics.GUARDS.items():
        b, t = metrics.headline(base_values[name]), metrics.headline(now_values[name])
        verdict = _regressed(rule, limit, b, t)
        guards.append(
            Guard(
                metric=name,
                baseline=b,
                trial=t,
                n_baseline=int(base_values[name].get("n") or 0),
                n_trial=int(now_values[name].get("n") or 0),
                allowed=_allowed(rule, limit),
                status="no_data" if verdict is None else ("regressed" if verdict else "ok"),
            )
        )
    target: dict[str, Any] = {"metric": target_metric, "met": False}
    if target_metric:
        b = metrics.headline(base_values[target_metric])
        t = metrics.headline(now_values[target_metric])
        delta = float(effect.get("delta") or 0)
        met = False
        if b is not None and t is not None:
            met = t >= b + delta if effect.get("direction") == "up" else t <= b - delta
            met = met and t != b
        target.update(baseline=b, trial=t, direction=effect.get("direction"), met=met)
    if _cadence_only(row):
        beats = [
            e
            for e in deps.factory_store.find_events(
                row.factory_id, ["heartbeat.delivered", "heartbeat.skipped"], applied * 1000
            )
            if e.ts < trial.until * 1000
        ]
        n = int(trial.seconds // 3600) if beats else 0
        min_n = MIN_BEAT_HOURS
    else:
        n = int(now_values["throughput"].get("n") or 0)
        min_n = MIN_MERGED
    regressed = [g.metric for g in guards if g.status == "regressed"]
    if regressed:
        outcome, reason = OUTCOME_REVERTED, f"guard regressed: {', '.join(regressed)}"
    elif target.get("met") and n >= min_n:
        outcome, reason = OUTCOME_KEPT, "target met, guards held"
    else:
        why = []
        if n < min_n:
            why.append(f"n={n} < {min_n}")
        if not target.get("met"):
            why.append("target not met" if target_metric else "no target metric")
        outcome, reason = OUTCOME_INCONCLUSIVE, "; ".join(why) or "unclear"
    activity = idle.window_activity(
        deps,
        row.factory_id,
        trial.since,
        trial.until,
        merged_prs=int(now_values["throughput"].get("n") or 0),
    )
    return Evaluation(
        outcome=outcome,
        reason=reason,
        n=n,
        min_n=min_n,
        target=target,
        guards=guards,
        trial_window=(trial.since, trial.until),
        baseline_window=(baseline.since, baseline.until),
        evaluated_at=now,
        activity=activity.to_dict(),
        idle=activity.idle,
    )


# ── Which changes may run as a trial ───────────────────────────

TRIAL_OVERLAP: Final = "trial_overlap"
NOT_TRIALABLE: Final = "not_trialable"


def overlapping_trial(
    deps: FactoryDeps,
    factory_id: str,
    paths: Iterable[str],
    *,
    exclude_id: str | None = None,
) -> tuple[FactoryProposal, str] | None:
    """
    :returns: The running trial that holds a block a trial on *paths* would
        need, and that block; ``None`` when every block is free. Synchronous.
    """
    wanted = set(definition.trial_blocks(paths))
    if not wanted:
        return None
    rows = deps.factory_store.list_proposals(
        factory_id, status="trial", kind=["tuning", "rules"], limit=200
    )
    for row in rows:
        if exclude_id and control.same_id(row.id, exclude_id):
            continue
        held = wanted & set(definition.trial_blocks((row.after or {}).keys()))
        if held:
            return row, sorted(held)[0]
    return None


def trial_day(row: FactoryProposal, now: int) -> tuple[int, int]:
    """:returns: ``(day, of)`` of *row*'s trial, e.g. ``(4, 14)``."""
    applied = int(row.applied_at or row.created_at)
    until = int(row.trial_until or applied)
    day = max(1, min((now - applied) // 86400 + 1, max(1, (until - applied + 86399) // 86400)))
    return int(day), int(max(1, (until - applied + 86399) // 86400))


def overlap_error(row: FactoryProposal, block: str, now: int) -> CockpitError:
    """:returns: The 409 ``trial_overlap`` refusal naming *row*, the trial on *block*."""
    ref = proposal_ref(row.number)
    day, of = trial_day(row, now)
    return CockpitError(
        409,
        TRIAL_OVERLAP,
        f"{ref} is already trialing {block} (day {day} of {of}). Two trials on one block "
        f"confound each other's guards: apply it directly, or wait for {ref} to end.",
        trial=ref,
        block=block,
        day=day,
        of=of,
    )


def not_trialable_error(message: str, reason: str | None = None) -> CockpitError:
    """
    :param reason: The short reason (``judged on outcomes``, ``needs discovery guards``).
    :returns: The 422 ``not_trialable`` refusal.
    """
    return CockpitError(
        422, NOT_TRIALABLE, message, reason=reason or definition.blocks.NOT_TRIALABLE_REASON
    )


def check_trial(
    deps: FactoryDeps,
    factory_id: str,
    paths: Iterable[str],
    *,
    exclude_id: str | None = None,
    now: int | None = None,
) -> None:
    """
    Refuse a trial on *paths*. Synchronous.

    :raises CockpitError: 422 ``not_trialable`` (strategy, product profile,
        discovery, Scout's brief); 409 ``trial_overlap`` when a running trial
        holds one of its blocks.
    """
    paths = list(paths)
    ok, message = definition.trialable(paths)
    if not ok:
        why = "; ".join(dict.fromkeys(definition.not_trialable(paths).values()))
        raise not_trialable_error(message or definition.blocks.NOT_TRIALABLE_REASON, why)
    found = overlapping_trial(deps, factory_id, paths, exclude_id=exclude_id)
    if found is not None:
        raise overlap_error(found[0], found[1], now if now is not None else now_s())


def extensions(row: FactoryProposal) -> int:
    """:returns: How many times *row*'s trial was extended for being idle."""
    value = (row.result or {}).get("trial_extensions")
    return value if isinstance(value, int) and value > 0 else 0


def attributed_incidents(deps: FactoryDeps, row: FactoryProposal, now: int) -> list[str]:
    """
    Incidents and findings since *row* applied that name it: harm attributed to the change.

    An issue names a change when ``C-n`` appears in its target, summary or
    evidence; nothing else links them (an issue's ``proposal_id`` is the change
    that fixes it, not one that caused it). Findings count when a human or
    Sentinel filed them; incidents count from any source.

    :returns: Their refs, e.g. ``["INC-13", "F-4"]``. Synchronous.
    """
    ref = proposal_ref(row.number)
    named = re.compile(rf"(?<![\w-]){re.escape(ref)}(?!\w)")
    since = int(row.applied_at or row.created_at)
    out: list[str] = []
    rows = deps.factory_store.list_issues(
        row.factory_id, class_=["incident", "finding"], limit=500
    )
    for issue in rows:
        if not since <= issue.first_seen <= now:
            continue
        if issue.class_ == "finding" and issue.source not in HARM_FINDING_SOURCES:
            continue
        evidence = json.dumps(issue.evidence, default=str) if issue.evidence else ""
        if any(named.search(text) for text in (issue.target or "", issue.summary or "", evidence)):
            out.append(issue.ref or issue.id)
    return out


def _summary(ev: Evaluation) -> str:
    guards = " ".join(
        f"{g.metric}{'✓' if g.status == 'ok' else ('✗' if g.status == 'regressed' else '·')}"
        for g in ev.guards
    )
    return f"{ev.outcome}: {ev.reason} (n={ev.n}/{ev.min_n}) · guards {guards}"


def _idle_question(ref: str) -> str:
    return (
        f"Keep {ref}? The factory was idle for its whole trial, so there is nothing to judge "
        "it on. If nobody answers in 24 h this stays applied because nothing got worse."
    )


async def _ask_owners(
    app_state: Any,
    row: FactoryProposal,
    ev: Evaluation,
    *,
    keep_default: bool = False,
    harm: list[str] | None = None,
) -> str | None:
    """
    Open the owners' Keep-or-revert decision (Keep recommended for an idle, harmless trial).

    :param harm: Refs of the incidents and findings that name the change, for its context.
    """
    deps = factory_deps(app_state)
    ref = proposal_ref(row.number)
    target = f"{tuning.SUBJECT_PREFIX}{row.id}"
    if keep_default:
        question = _idle_question(ref)
        context = (
            f"Extended {extensions(row)}× by its original length and still no merged PRs or "
            f"finished tasks. No guard regressed and no incident names {ref}.\n\n{_summary(ev)}"
        )
    else:
        question = f"Keep or revert {ref}? Its trial was inconclusive: {ev.reason}"
        context = _summary(ev)
        if harm:
            context = f"{ref} is named in {', '.join(harm[:5])}.\n\n{context}"
    decision, _ = await asyncio.to_thread(
        lambda: deps.factory_store.create_decision(
            row.factory_id,
            dedupe_key=f"{tuning.SUBJECT_PREFIX}{row.id}:trial"[:64],
            question=control.clip(question, 1000),
            source="proposal",
            options=[
                {
                    "id": tuning.OPTION_KEEP,
                    "label": "Keep",
                    "action": ACTION_KEEP_PROPOSAL,
                    "target": target,
                    **({"recommended": True} if keep_default else {}),
                },
                {
                    "id": tuning.OPTION_REVERT,
                    "label": "Revert",
                    "action": ACTION_REVERT_PROPOSAL,
                    "target": target,
                    **({} if keep_default else {"recommended": True}),
                },
            ],
            blocking=False,
            allow_text=False,
            context=context[:4000],
            assignee="owners",
            thread=row.thread or THREAD_IMPROVEMENTS,
            subject=f"{tuning.SUBJECT_PREFIX}{ref}",
        )
    )
    await events.emit(
        app_state,
        row.factory_id,
        "decision.opened",
        severity="notice",
        subject=f"decision:{decision.ref}",
        summary=f"{decision.ref}: {decision.question}",
        data={"decision_id": decision.id, "source": "proposal", "proposal": ref},
    )
    return decision.id


async def _extend(
    app_state: Any, row: FactoryProposal, ev: Evaluation, now: int
) -> FactoryProposal | None:
    """
    Extend an idle trial by its original length and say so in its thread.

    :returns: The extended row, or ``None`` when it left ``trial`` meanwhile.
    """
    deps = factory_deps(app_state)
    ref = proposal_ref(row.number)
    result = dict(row.result or {})
    applied = int(row.applied_at or row.created_at)
    until = int(row.trial_until or now)
    window = int(result.get("trial_window_s") or max(3600, until - applied))
    count = extensions(row) + 1
    # A server that was down past a whole window still gives the trial a full one.
    new_until = until + window if until + window > now else now + window
    try:
        extended = await asyncio.to_thread(
            lambda: deps.factory_store.update_proposal(
                row.id,
                expect_status="trial",
                trial_until=new_until,
                result={**result, "trial_extensions": count, "trial_window_s": window},
            )
        )
    except ProposalStateConflict:
        return None
    again = "" if count == 1 else " again"
    why = "no merged PRs since it started — the factory was idle"
    await events.emit(
        app_state,
        row.factory_id,
        EXTENDED_KIND,
        severity="info",
        subject=f"{tuning.SUBJECT_PREFIX}{ref}",
        summary=f"{ref} trial extended{again}: {why}",
        data={
            "proposal_id": row.id,
            "by": "server",
            "by_kind": "server",
            "extensions": count,
            "max_extensions": MAX_EXTENSIONS,
            "trial_until": new_until,
            "window_s": window,
            "activity": ev.activity,
        },
    )
    return extended or row


async def end_trial(app_state: Any, row: FactoryProposal, now: int) -> Evaluation:
    """Evaluate one trial and act on the outcome (extend, keep, revert, or ask the owners)."""
    deps = factory_deps(app_state)
    root = await control.get_conv(deps, row.factory_id)
    ev = await asyncio.to_thread(evaluate_trial, deps, row, now)
    idle_inconclusive = ev.outcome == OUTCOME_INCONCLUSIVE and ev.idle
    # Negative evidence is never waited out: a trial an incident names goes to the owners now.
    harm = (
        await asyncio.to_thread(attributed_incidents, deps, row, now) if idle_inconclusive else []
    )
    if idle_inconclusive and not harm and extensions(row) < MAX_EXTENSIONS:
        if await _extend(app_state, row, ev, now) is not None:
            return replace(ev, outcome=OUTCOME_EXTENDED, reason="idle: extended")
    ref = proposal_ref(row.number)
    result = {**(row.result or {}), "evaluation": ev.to_dict()}
    row = await asyncio.to_thread(deps.factory_store.update_proposal, row.id, result=result) or row
    await events.emit(
        app_state,
        row.factory_id,
        "proposal.trial_ended",
        severity="notice",
        subject=f"{tuning.SUBJECT_PREFIX}{ref}",
        summary=f"{ref} trial ended · {_summary(ev)}"[:300],
        data={"proposal_id": row.id, "by": "server", "by_kind": "server", **ev.to_dict()},
    )
    if root is None:
        return ev
    server = Actor.server()
    if ev.outcome == OUTCOME_REVERTED:
        await tuning.revert(app_state, root, row, actor=server, reason=f"auto-revert: {ev.reason}")
    elif ev.outcome == OUTCOME_KEPT:
        await tuning.keep(app_state, root, row, actor=server)
    else:
        keep_default = ev.idle and not harm
        decision_id = await _ask_owners(app_state, row, ev, keep_default=keep_default, harm=harm)
        result = {**(row.result or {}), "keep_decision_id": decision_id}
        if keep_default:
            result.update(default=DEFAULT_KEEP, default_at=now + KEEP_DEFAULT_AFTER_S)
        await asyncio.to_thread(deps.factory_store.update_proposal, row.id, result=result)
    return ev


async def _settle_default(
    app_state: Any, root: Conversation, row: FactoryProposal, now: int
) -> str | None:
    """Apply an idle trial's default once its decision went unanswered. :returns: The action."""
    deps = factory_deps(app_state)
    if now < int((row.result or {}).get("default_at") or 0):
        return None
    harm = await asyncio.to_thread(attributed_incidents, deps, row, now)
    server = Actor.server()
    if harm:
        reason = f"unattended: {', '.join(harm[:3])} names it"
        await tuning.revert(app_state, root, row, actor=server, reason=reason)
        return "unattended"
    await tuning.keep(app_state, root, row, actor=server, reason=KEEP_DEFAULT_REASON)
    return "kept_unanswered"


async def tick(app_state: Any, factory_id: str, *, now: int | None = None) -> list[str]:
    """End due trials and settle unattended ones. :returns: ``"C-n:<outcome>"`` per action."""
    deps = factory_deps(app_state)
    now = now if now is not None else now_s()
    rows = await asyncio.to_thread(
        lambda: deps.factory_store.list_proposals(
            factory_id, status="trial", kind=["tuning", "rules"], limit=200
        )
    )
    done: list[str] = []
    for row in rows:
        ref = proposal_ref(row.number)
        evaluation = (row.result or {}).get("evaluation")
        try:
            if row.trial_until and now >= row.trial_until and not isinstance(evaluation, Mapping):
                ev = await end_trial(app_state, row, now)
                done.append(f"{ref}:{ev.outcome}")
                continue
            if not isinstance(evaluation, Mapping) or not row.applied_at or not row.trial_until:
                continue
            if (row.result or {}).get("default") == DEFAULT_KEEP:
                root = await control.get_conv(deps, factory_id)
                action = await _settle_default(app_state, root, row, now) if root else None
                if action:
                    done.append(f"{ref}:{action}")
                continue
            # One original window after the trial ended: an extension does not stretch it.
            stored = (row.result or {}).get("trial_window_s")
            window = int(stored or max(3600, row.trial_until - row.applied_at))
            if now >= row.trial_until + window:
                root = await control.get_conv(deps, factory_id)
                if root is None:
                    continue
                await tuning.revert(
                    app_state,
                    root,
                    row,
                    actor=Actor.server(),
                    reason="unattended: nobody decided within one trial window after it ended",
                )
                done.append(f"{ref}:unattended")
        except Exception:  # noqa: BLE001 - one trial must not stop the others
            _logger.warning("factory: evaluating %s failed", ref, exc_info=True)
    return done


__all__ = [
    "DEFAULT_KEEP",
    "EXTENDED_KIND",
    "HARM_FINDING_SOURCES",
    "KEEP_DEFAULT_AFTER_S",
    "KEEP_DEFAULT_REASON",
    "MAX_EXTENSIONS",
    "MIN_BEAT_HOURS",
    "MIN_MERGED",
    "NOT_TRIALABLE",
    "OUTCOME_EXTENDED",
    "OUTCOME_INCONCLUSIVE",
    "OUTCOME_KEPT",
    "OUTCOME_REVERTED",
    "TRIAL_OVERLAP",
    "Evaluation",
    "Guard",
    "attributed_incidents",
    "check_trial",
    "end_trial",
    "evaluate_trial",
    "extensions",
    "not_trialable_error",
    "overlap_error",
    "overlapping_trial",
    "tick",
    "trial_day",
]
