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
decides reverts at 2× its window.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import ACTION_KEEP_PROPOSAL, ACTION_REVERT_PROPOSAL, THREAD_IMPROVEMENTS
from omnigent.server.factory import control, events, tuning
from omnigent.server.factory.actor import Actor, proposal_ref
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.telemetry import metrics
from omnigent.stores.factory_store import now_s

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryProposal

_logger = logging.getLogger(__name__)

MIN_MERGED: Final = 3
MIN_BEAT_HOURS: Final = 24
OUTCOME_KEPT: Final = "kept"
OUTCOME_REVERTED: Final = "reverted"
OUTCOME_INCONCLUSIVE: Final = "inconclusive"


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
    )


def _summary(ev: Evaluation) -> str:
    guards = " ".join(
        f"{g.metric}{'✓' if g.status == 'ok' else ('✗' if g.status == 'regressed' else '·')}"
        for g in ev.guards
    )
    return f"{ev.outcome}: {ev.reason} (n={ev.n}/{ev.min_n}) · guards {guards}"


async def _ask_owners(app_state: Any, row: FactoryProposal, ev: Evaluation) -> str | None:
    deps = factory_deps(app_state)
    ref = proposal_ref(row.number)
    target = f"{tuning.SUBJECT_PREFIX}{row.id}"
    decision, _ = await asyncio.to_thread(
        lambda: deps.factory_store.create_decision(
            row.factory_id,
            dedupe_key=f"{tuning.SUBJECT_PREFIX}{row.id}:trial"[:64],
            question=control.clip(
                f"Keep or revert {ref}? Its trial was inconclusive: {ev.reason}", 1000
            ),
            source="proposal",
            options=[
                {
                    "id": tuning.OPTION_KEEP,
                    "label": "Keep",
                    "action": ACTION_KEEP_PROPOSAL,
                    "target": target,
                },
                {
                    "id": tuning.OPTION_REVERT,
                    "label": "Revert",
                    "action": ACTION_REVERT_PROPOSAL,
                    "target": target,
                    "recommended": True,
                },
            ],
            blocking=False,
            allow_text=False,
            context=_summary(ev)[:4000],
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


async def end_trial(app_state: Any, row: FactoryProposal, now: int) -> Evaluation:
    """Evaluate one trial and act on the outcome (keep, revert, or ask the owners)."""
    deps = factory_deps(app_state)
    root = await control.get_conv(deps, row.factory_id)
    ev = await asyncio.to_thread(evaluate_trial, deps, row, now)
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
        decision_id = await _ask_owners(app_state, row, ev)
        result = {**(row.result or {}), "keep_decision_id": decision_id}
        await asyncio.to_thread(deps.factory_store.update_proposal, row.id, result=result)
    return ev


async def tick(app_state: Any, factory_id: str, *, now: int | None = None) -> list[str]:
    """End due trials and revert unattended ones. :returns: ``"C-n:<outcome>"`` per action."""
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
            window = max(3600, row.trial_until - row.applied_at)
            if now >= row.applied_at + 2 * window:
                root = await control.get_conv(deps, factory_id)
                if root is None:
                    continue
                await tuning.revert(
                    app_state,
                    root,
                    row,
                    actor=Actor.server(),
                    reason="unattended: nobody kept the trial within 2× its window",
                )
                done.append(f"{ref}:unattended")
        except Exception:  # noqa: BLE001 - one trial must not stop the others
            _logger.warning("factory: evaluating %s failed", ref, exc_info=True)
    return done


__all__ = [
    "MIN_BEAT_HOURS",
    "MIN_MERGED",
    "OUTCOME_INCONCLUSIVE",
    "OUTCOME_KEPT",
    "OUTCOME_REVERTED",
    "Evaluation",
    "Guard",
    "end_trial",
    "evaluate_trial",
    "tick",
]
