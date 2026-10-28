"""The loop engine: every factory loop is explicit, bounded, observed and escalated.

Each loop (``factory_loops``, one row per ``(kind, subject)``) has a spec in
:data:`CATALOGUE` (L1 implement … L9 improve) and a snapshot of its resolved
budgets on the row. The server derives the loop's state from facts it
observes through the event log (:func:`on_event`) and the supervisor tick
(:func:`tick`), never from agent narration:

========== ======================== ==========================================
loop       subject                  one iteration
========== ======================== ==========================================
implement  ``task:<id>``            a worker attempt, or a rework a verifier
                                    asked for (review CHANGES, verify or demo
                                    code FAIL, a human's changes request)
review     ``pr:<id>``              one ``factory:review`` marker
verify     ``pr:<id>@<sha7>``       one verify marker or ``PREVIEW FAILED``
demo       ``pr:<id>@<sha7>``       one recorded demo at the head
ci         ``pr:<id>@<sha7>``       one completed check-suite verdict
claim      ``pr:<id>``              one READY claim or Sentinel verdict
plan       ``project:<slug>``       a task closed (stall: none in 24 h)
========== ======================== ==========================================

States: ``running`` → ``converged`` (met), ``exhausted`` (budget),
``stalled`` (no progress, wall, review dry) or ``thrashing`` (a fixed review
finding came back); ``superseded`` (a new head) and ``cancelled``. A bad
state escalates by the factory's engine mode (§4 of the round-3 spec):
``observe`` only records it (detectors turn ``loop.*`` into incidents);
``assist`` and ``enforce`` open a ``loop`` decision and mail the project lead
``[Factory loop · EXHAUSTED|STALLED|THRASHING · …]``. Answering the decision
(:func:`plan_answer`) raises the budget, switches the vendor, restarts the
worker fresh, marks it done or abandons it. A budget stop is never "done",
and the engine never cancels a session on its own.

Budgets come from :data:`DEFAULT_BUDGETS` through :func:`budget` (the seam the
factory definition plugs into), then the charter's ``budgets`` and the task's
own budget.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from omnigent.entities.factory import FactoryDecision, FactoryLoop, FactoryPr, FactoryTask
from omnigent.factory import (
    ACTION_ABANDON_TASK,
    ACTION_FRESH_RESTART,
    ACTION_MARK_DONE,
    ACTION_PAUSE,
    ACTION_RAISE_BUDGET,
    ACTION_SWITCH_VENDOR,
    LABEL_ID,
    LABEL_ROLE,
    LABEL_STATE,
    LOOP_ENDED_STATES,
    LOOP_KINDS,
    LOOP_STATES,
    ROLE_PROJECT_LEAD,
    STATE_PAUSED,
)
from omnigent.server.factory import events, tasks
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.telemetry.spans import span_context, write_span
from omnigent.stores.factory_store import norm_id

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactoryEvent, FactoryProject

_logger = logging.getLogger(__name__)

# ── Catalogue (spec §12.2) ─────────────────────────────────────


@dataclass(frozen=True)
class LoopSpec:
    """
    One loop of the catalogue.

    :param ref: ``L1`` … ``L9``.
    :param stop: ``met`` / ``impossible`` / ``budget`` / ``no_progress`` / ``thrash`` rules.
    :param budget_keys: The keys of :data:`DEFAULT_BUDGETS` for this kind.
    :param remedies: The decision actions an escalation offers.
    """

    ref: str
    kind: str
    subject_kind: str
    trigger: str
    goal: str
    worker_role: str
    verifier: str
    iteration_source: str
    stop: Mapping[str, str]
    budget_keys: tuple[str, ...]
    escalation: str
    remedies: tuple[str, ...] = ()


DEFAULT_BUDGETS: Final[dict[str, dict[str, float]]] = {
    "implement": {"attempts": 3, "usd": 15.0, "wall_h": 8, "no_progress_s": 3600},
    "review": {"rounds": 3},
    "verify": {"per_head": 2, "env_retries": 1},
    "demo": {"per_head": 2, "per_pr_heads": 6},
    "ci": {"flake_reruns": 1},
    "claim": {"mismatches": 2},
    "plan": {"stall_h": 24},
    "intake": {"revisions": 3},
    "improve": {"usd": 3.0},
    "budgets": {"project_usd": 150.0, "factory_usd_per_day": 400.0},
}
"""Code defaults per loop kind (``budgets`` holds the project and factory $ caps)."""
_FLOAT_KEYS: Final = frozenset({"usd", "project_usd", "factory_usd_per_day"})

_LOOP_REMEDIES: Final = (
    ACTION_RAISE_BUDGET,
    ACTION_SWITCH_VENDOR,
    ACTION_FRESH_RESTART,
    ACTION_ABANDON_TASK,
)

CATALOGUE: Final[dict[str, LoopSpec]] = {
    s.kind: s
    for s in (
        LoopSpec(
            "L1",
            "implement",
            "task",
            "sys_factory_task(op=start)",
            "a PR open with the required checks green at its head",
            "ic",
            "the PL re-runs acceptance, then CI at the head",
            "a bound worker <key>[-r<k>]; a verifier's rework; a vendor switch counts",
            {
                "met": "PR open and required checks green at the head",
                "impossible": "IC RESULT BLOCKED: <reason>",
                "budget": "attempts, usd, wall_h",
                "no_progress": "no new commit, check or marker for no_progress_s",
            },
            ("attempts", "usd", "wall_h", "no_progress_s"),
            "loop decision: +1 attempt, switch vendor, fresh restart, abandon",
            _LOOP_REMEDIES,
        ),
        LoopSpec(
            "L2",
            "review",
            "pr",
            "a new head with CI green",
            "APPROVE at the head",
            "author",
            "cross-vendor reviewer",
            "one factory:review marker",
            {
                "met": "APPROVE at the head",
                "budget": "rounds",
                "no_progress": "dry: a round adds no new blocking fingerprint and fixes none",
                "thrash": "a fixed fingerprint reappears",
            },
            ("rounds",),
            "a human decision with the findings diff",
            _LOOP_REMEDIES,
        ),
        LoopSpec(
            "L3",
            "verify",
            "head",
            "review APPROVE",
            "verify PASS at the head",
            "env_keeper",
            "PREVIEW + env checks",
            "one factory:verify marker or one PREVIEW FAILED",
            {
                "met": "PASS at the head",
                "budget": "per_head; env failures charge env_retries, code failures implement",
            },
            ("per_head", "env_retries"),
            "env: env_keeper restart notice, then an incident; code: back to implement",
        ),
        LoopSpec(
            "L4",
            "demo",
            "head",
            "verify PASS",
            "a demo PASS or PARTIAL at the head",
            "demo",
            "the recorded demo",
            "one factory_demos row at the head",
            {
                "met": "PASS or PARTIAL",
                "budget": "per_head (ERROR retries), per_pr_heads; FAIL charges implement",
            },
            ("per_head", "per_pr_heads"),
            "decision: review without a demo, or fix the env",
            (ACTION_MARK_DONE, ACTION_RAISE_BUDGET, ACTION_PAUSE),
        ),
        LoopSpec(
            "L5",
            "ci",
            "head",
            "a push",
            "required checks pass",
            "GitHub Actions",
            "GitHub Actions",
            "a completed check suite at a sha",
            {"met": "required checks pass", "budget": "flake: fail→pass on the same sha"},
            ("flake_reruns",),
            "finding ci.flaky",
        ),
        LoopSpec(
            "L6",
            "claim",
            "pr",
            "the PL claims ready_for_human_merge",
            "Sentinel verified the head",
            "project_lead",
            "Sentinel and the server's gates",
            "a sentinel verdict or claim_mismatch at the head",
            {"met": "verified at the head", "budget": "mismatches on consecutive claims"},
            ("mismatches",),
            "finding, plus a fresh restart of the PL from its plan",
            (ACTION_FRESH_RESTART,),
        ),
        LoopSpec(
            "L7",
            "plan",
            "project",
            "PL heartbeat or NEXT",
            "every Definition of Done item passes",
            "project_lead",
            "the DoD checklist and the gates",
            "a task closed",
            {"met": "every task closed with one done", "no_progress": "no task closed in stall_h"},
            ("stall_h",),
            "done_check decision (Mark done?) or a stall incident",
            (ACTION_MARK_DONE,),
        ),
        LoopSpec(
            "L8",
            "intake",
            "chat",
            "a human chat message asking for work",
            "the proposal applied or rejected",
            "lead / planner",
            "the proposal validator, then a human",
            "one proposal version",
            {"met": "proposal applied or rejected", "budget": "revisions"},
            ("revisions",),
            "ask the requester in the thread",
        ),
        LoopSpec(
            "L9",
            "improve",
            "factory",
            "Sentinel's daily retro",
            "at most 2 proposals filed, each with evidence",
            "lead (Sentinel)",
            "trial evaluation",
            "one retro run",
            {"met": "<= 2 proposals with evidence", "budget": "usd per run"},
            ("usd",),
            "skip until the next run",
        ),
    )
}
"""The loop catalogue: L1–L9 (the supervisor rules stay the deterministic ops loop)."""

BAD_STATES: Final = frozenset({"exhausted", "stalled", "thrashing"})
ACTIVE_STATES: Final = frozenset({"running", *BAD_STATES, "escalated"})
NEAR_FRACTION: Final = 0.8
DONE_CHECK_GRACE_S: Final = 1800
MAX_MARKERS: Final = 40
MAX_ROUNDS: Final = 20
REASON_MAX: Final = 300
ENGINE_MODE_LABEL: Final = tasks.LABEL_ENGINE_MODE
"""Mirrored from the root onto every PL, where the factory guard reads it."""

# ── Budgets and modes ──────────────────────────────────────────

BudgetResolver = Callable[[str, str, str], Any]
_resolver: BudgetResolver | None = None


def set_budget_resolver(resolver: BudgetResolver | None) -> Callable[[], None]:
    """
    Plug in a per-factory budget source (the factory definition's ``loops.*``).

    :param resolver: ``(factory_id, kind, key) -> value``; ``None`` or a
        non-positive value falls back to the code default.
    :returns: A callable that restores the previous resolver.
    """
    global _resolver
    previous, _resolver = _resolver, resolver

    def _restore() -> None:
        global _resolver
        _resolver = previous

    return _restore


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if value > 0 else None


def _cast(key: str, value: float) -> float | int:
    return round(value, 2) if key in _FLOAT_KEYS else int(value)


def budget(factory_id: str, kind: str, key: str) -> float | int:
    """
    One budget value: the resolver's (factory definition), else the code default.

    :param kind: A loop kind, or ``budgets`` for the project and factory $ caps.
    :raises KeyError: For an unknown kind or key.
    """
    default = DEFAULT_BUDGETS[kind][key]
    if _resolver is not None:
        try:
            value = _number(_resolver(factory_id, kind, key))
        except Exception:  # noqa: BLE001 - a broken resolver falls back to defaults
            _logger.warning("factory loops: budget resolver failed for %s.%s", kind, key)
            value = None
        if value is not None:
            return _cast(key, value)
    return _cast(key, default)


def resolve_spec(
    factory_id: str,
    kind: str,
    *,
    project: FactoryProject | None = None,
    task: FactoryTask | None = None,
) -> dict[str, float | int]:
    """
    The budgets a new loop starts with: the definition, the charter, then the task.

    The charter's ``budgets.<kind>.<key>`` (and ``budgets.task_usd`` for
    implement) override per project; a task's own ``budget`` wins for implement.
    """
    spec: dict[str, float | int] = {k: budget(factory_id, kind, k) for k in DEFAULT_BUDGETS[kind]}
    charter: Mapping[str, Any] = (project.charter or {}) if project is not None else {}
    found = charter.get("budgets")
    budgets: Mapping[str, Any] = found if isinstance(found, Mapping) else {}
    found = budgets.get(kind)
    scoped: Mapping[str, Any] = found if isinstance(found, Mapping) else {}
    for key in spec:
        if (value := _number(scoped.get(key))) is not None:
            spec[key] = _cast(key, value)
    if kind == "implement":
        if (value := _number(budgets.get("task_usd"))) is not None:
            spec["usd"] = _cast("usd", value)
        own = task.budget if task is not None and isinstance(task.budget, Mapping) else {}
        if (value := _number(own.get("attempts"))) is not None:
            spec["attempts"] = int(value)
        if isinstance(own.get("usd"), int | float) and own["usd"] >= 0:
            spec["usd"] = _cast("usd", float(own["usd"]))
    return spec


def engine_mode(deps: FactoryDeps, factory_id: str) -> str:
    """:returns: ``observe`` / ``assist`` / ``enforce`` (the root label, ``assist`` by default)."""
    return tasks.engine_mode(deps, factory_id)


def limit_of(loop: FactoryLoop) -> int | None:
    """:returns: The iteration budget of *loop* (attempts, rounds, per head, …)."""
    spec = loop.spec or {}
    for key in ("attempts", "rounds", "per_head", "revisions", "mismatches"):
        value = spec.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return int(value)
    return None


# ── Subjects and labels ────────────────────────────────────────


def task_subject(task_id: str) -> str:
    """:returns: ``task:<id>``."""
    return f"task:{norm_id(task_id)}"


def pr_subject(pr_id: str) -> str:
    """:returns: ``pr:<id>``."""
    return f"pr:{norm_id(pr_id)}"


def head_subject(pr_id: str, sha: str) -> str:
    """:returns: ``pr:<id>@<sha7>``."""
    return f"pr:{norm_id(pr_id)}@{sha[:7].lower()}"


def project_subject(slug: str) -> str:
    """:returns: ``project:<slug>``."""
    return f"project:{slug}"[:64]


def classify(text: str | None, *, default: str, exit_code: int | None = None) -> str:
    """
    Whether a verify or demo failure is the code's or the env's.

    :param text: The failure's reason, e.g. ``"env: tilt timed out"`` or
        ``"code: migration failed"``; an explicit prefix wins.
    :param default: The class when nothing says otherwise.
    :param exit_code: The demo harness exit code; ``3`` (stale preview) is env.
    :returns: ``code`` or ``env``.
    """
    lowered = (text or "").strip().lower()
    if lowered.startswith(("code:", "code ")):
        return "code"
    if lowered.startswith(("env:", "env ")):
        return "env"
    if exit_code == 3 or "stale" in lowered:
        return "env"
    return default


def _clip(text: str | None, limit: int = REASON_MAX) -> str | None:
    if text is None:
        return None
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


# ── Changes (computed under the lock, applied after) ───────────


@dataclass
class Change:
    """
    One thing that happened to a loop, emitted as ``loop.<what>`` after the write.

    :param what: ``started`` / ``iteration`` / ``converged`` / ``stalled`` /
        ``thrashing`` / ``exhausted`` / ``superseded`` / ``cancelled`` /
        ``budget_violation`` / ``budget_raised``, or ``budget.near``.
    :param iter_span: The ``loop_iter`` span to write: ``{n, started_ms, ended_ms,
        outcome, verdict, reason, class, head_sha}``.
    """

    loop: FactoryLoop
    what: str
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    severity: str = "info"
    iter_span: dict[str, Any] | None = None


@dataclass
class Ctx:
    """A synchronous pass over one factory's loops."""

    deps: FactoryDeps
    factory_id: str
    now: float
    changes: list[Change] = field(default_factory=list)

    @property
    def store(self) -> Any:
        """:returns: The factory store."""
        return self.deps.factory_store

    @property
    def ms(self) -> int:
        """:returns: Now in epoch milliseconds."""
        return int(self.now * 1000)


def task_label(task: FactoryTask) -> str:
    """:returns: e.g. ``T-12 route``."""
    return f"{task.ref} {task.key}"


def _pr_label(pr: FactoryPr | None, sha: str | None = None) -> str:
    if pr is None:
        return "PR"
    head = f" @{sha[:7]}" if sha else ""
    return f"PR #{pr.number}{head}"


def _spent(loop: FactoryLoop) -> dict[str, Any]:
    return dict(loop.spent or {})


def _ensure(
    ctx: Ctx,
    kind: str,
    subject: str,
    *,
    project: str | None,
    task_id: str | None = None,
    pr_id: str | None = None,
    spec: Mapping[str, Any] | None = None,
    label: str,
    iteration: int = 0,
    spent: Mapping[str, Any] | None = None,
) -> FactoryLoop:
    """The loop ``(kind, subject)``, created (``loop.started``) when missing."""
    row = ctx.store.get_loop_by_subject(ctx.factory_id, kind, subject)
    if row is not None:
        return row
    row, created = ctx.store.upsert_loop(
        ctx.factory_id,
        kind,
        subject,
        project_slug=project,
        task_id=task_id,
        pr_id=pr_id,
        state="running",
        iteration=iteration,
        spec=dict(spec) if spec is not None else resolve_spec(ctx.factory_id, kind),
        spent={
            "attempts": iteration,
            "label": label,
            "progress_at": int(ctx.now),
            **(spent or {}),
        },
        def_version=span_context(ctx.deps, ctx.factory_id).def_version,
        started_at=int(ctx.now),
    )
    if created:
        ctx.changes.append(Change(row, "started", f"{label}: {kind} loop started"))
    return row


def _update(ctx: Ctx, loop: FactoryLoop, **fields: Any) -> FactoryLoop:
    return ctx.store.update_loop(loop.id, **fields) or loop


def _label(loop: FactoryLoop) -> str:
    return str(_spent(loop).get("label") or loop.subject)


def _iterate(
    ctx: Ctx,
    loop: FactoryLoop,
    *,
    verdict: str | None,
    reason: str | None,
    n: int | None = None,
    cls: str | None = None,
    head: str | None = None,
    ok: bool | None = None,
    spent: Mapping[str, Any] | None = None,
    state: str | None = None,
) -> FactoryLoop:
    """
    Record one iteration (``loop.iteration`` and its ``loop_iter`` span).

    :param n: The iteration number (default: the next one).
    :param ok: The iteration's outcome, when it has one now.
    """
    number = n if n is not None else loop.iteration + 1
    values = _spent(loop)
    started = int(values.get("iter_started_ms") or ctx.ms)
    values.update(spent or {})
    values["attempts"] = max(int(values.get("attempts") or 0), number)
    values["iter_started_ms"] = ctx.ms
    fields: dict[str, Any] = {
        "iteration": number,
        "last_verdict": verdict,
        "last_reason": _clip(reason),
        "spent": values,
    }
    if state is not None and state != loop.state:
        fields["state"] = state
        if state == "running":
            fields["ended_at"] = None
    row = _update(ctx, loop, **fields)
    what = f"{loop.kind} {number}"
    limit = limit_of(row)
    if limit is not None:
        what += f"/{limit}"
    text = f"{_label(row)}: {what}" + (f" · {verdict}" if verdict else "")
    ctx.changes.append(
        Change(
            row,
            "iteration",
            text + (f" · {reason}" if reason else ""),
            {"iteration": number, "verdict": verdict, "reason": _clip(reason), "class": cls},
            iter_span={
                "n": number,
                "started_ms": started if ok is not None else ctx.ms,
                "ended_ms": ctx.ms if ok is not None else None,
                "outcome": None if ok is None else ("ok" if ok else "fail"),
                "verdict": verdict if ok is not None else None,
                "reason": _clip(reason) if ok is not None else None,
                "class": cls,
                "head_sha": head,
                "previous": loop.iteration if number > loop.iteration else None,
            },
        )
    )
    return row


def _verdict(
    ctx: Ctx,
    loop: FactoryLoop,
    *,
    verdict: str,
    reason: str | None,
    ok: bool,
    cls: str | None = None,
    spent: Mapping[str, Any] | None = None,
) -> FactoryLoop:
    """Close the current iteration (its ``loop_iter`` span) without starting another."""
    values = _spent(loop)
    values.update(spent or {})
    row = _update(ctx, loop, last_verdict=verdict, last_reason=_clip(reason), spent=values)
    ctx.changes.append(
        Change(
            row,
            "verdict",
            f"{_label(row)}: {verdict}" + (f" · {reason}" if reason else ""),
            {"iteration": row.iteration, "verdict": verdict, "reason": _clip(reason)},
            iter_span={
                "n": max(1, row.iteration),
                "started_ms": int(values.get("iter_started_ms") or ctx.ms),
                "ended_ms": ctx.ms,
                "outcome": "ok" if ok else "fail",
                "verdict": verdict,
                "reason": _clip(reason),
                "class": cls,
            },
        )
    )
    return row


def _transition(
    ctx: Ctx,
    loop: FactoryLoop,
    state: str,
    reason: str,
    *,
    severity: str | None = None,
    **data: Any,
) -> FactoryLoop:
    """Move *loop* to *state* (``loop.<state>``); a no-op when it is already there."""
    if loop.state == state:
        return loop
    fields: dict[str, Any] = {"state": state, "last_reason": _clip(reason)}
    if state in LOOP_ENDED_STATES:
        fields["ended_at"] = int(ctx.now)
    elif loop.ended_at is not None:
        fields["ended_at"] = None
    row = _update(ctx, loop, **fields)
    if state in LOOP_STATES and state != "running":
        bad = state in BAD_STATES
        ctx.changes.append(
            Change(
                row,
                state,
                f"{_label(row)}: {_progress_text(row)} {state} · {reason}",
                {"reason": _clip(reason), **data},
                severity=severity or ("warning" if bad else "notice"),
            )
        )
    return row


def _progress_text(loop: FactoryLoop) -> str:
    limit = limit_of(loop)
    count = f" {loop.iteration}/{limit}" if limit is not None else f" {loop.iteration}"
    return f"{loop.kind}{count}"


def _note_progress(ctx: Ctx, loop: FactoryLoop, **spent: Any) -> FactoryLoop:
    """Progress happened: reset the no-progress clock; a stalled loop runs again."""
    values = _spent(loop)
    values.update(spent)
    values["progress_at"] = int(ctx.now)
    row = _update(ctx, loop, spent=values)
    if row.state == "stalled":
        row = _transition(ctx, row, "running", "progress resumed")
    return row


# ── Implement (L1) ─────────────────────────────────────────────


def _project(ctx: Ctx, slug: str | None) -> FactoryProject | None:
    return ctx.store.get_project(ctx.factory_id, slug) if slug else None


def implement_loop(ctx: Ctx, task: FactoryTask, *, create: bool = True) -> FactoryLoop | None:
    """
    The implement loop of *task* (created on first use unless *create* is false).

    Its iteration counts tries: a worker run, or a rework a verifier asked for.
    ``spent.try_open`` says the current try is still going; ``try_worker`` is
    the worker attempt bound to it (``None``: the next new worker joins it).
    """
    subject = task_subject(task.id)
    row = ctx.store.get_loop_by_subject(ctx.factory_id, "implement", subject)
    if row is not None or not create:
        return row
    spec = resolve_spec(
        ctx.factory_id, "implement", project=_project(ctx, task.project_slug), task=task
    )
    workers = max(0, task.attempts)
    return _ensure(
        ctx,
        "implement",
        subject,
        project=task.project_slug,
        task_id=task.id,
        pr_id=task.pr_id,
        spec=spec,
        label=task_label(task),
        iteration=workers,
        spent={"workers": workers, "try_open": workers > 0, "try_worker": workers or None},
    )


def _cap(loop: FactoryLoop) -> int:
    return int((loop.spec or {}).get("attempts") or DEFAULT_BUDGETS["implement"]["attempts"])


def observe_attempt(ctx: Ctx, task: FactoryTask, attempt: int, via: str) -> FactoryLoop | None:
    """
    A worker attempt of *task* started (``start``, or the binder saw its title).

    A new worker joins an open try that has none of its own yet (a rework);
    otherwise it starts the next try. A try past the budget, or any new worker
    while the loop is exhausted, is a ``loop.budget_violation`` (enforce refuses
    ``start``; a dispatch by title still gets through).
    """
    if task.kind == "adhoc" or attempt < 1:
        return None
    row = implement_loop(ctx, task)
    assert row is not None
    values = _spent(row)
    if attempt <= int(values.get("workers") or 0):
        return _note_progress(ctx, row)
    cap = _cap(row)
    joins = bool(values.get("try_open")) and values.get("try_worker") is None
    joins = joins and row.state != "converged"
    number = row.iteration if joins else row.iteration + 1
    violation = row.state == "exhausted" or number > cap
    if violation:
        ctx.changes.append(
            Change(
                row,
                "budget_violation",
                f"{task_label(task)}: attempt {attempt} dispatched past implement "
                f"{row.iteration}/{cap} ({via})",
                {"attempt": attempt, "limit": cap, "via": via, "decision_id": row.decision_id},
                severity="warning",
            )
        )
    progress = {
        "progress_at": int(ctx.now),
        "workers": attempt,
        "try_open": True,
        "try_worker": attempt,
    }
    if joins:
        row = _note_progress(ctx, row, **progress)
    else:
        row = _iterate(
            ctx,
            row,
            n=number,
            verdict="DISPATCHED",
            reason=f"attempt {attempt} ({via})",
            spent=progress,
            state=None if row.state in BAD_STATES else "running",
        )
    if violation and row.state == "running":
        row = _transition(
            ctx, row, "exhausted", f"try {number} is past the budget of {cap}", limit=cap
        )
    return row


def observe_failure(
    ctx: Ctx, task: FactoryTask, *, verdict: str, reason: str | None, cls: str = "code"
) -> FactoryLoop | None:
    """
    A worker of *task* reported FAIL or BLOCKED: the try failed.

    An env failure does not use the try up: the next worker continues it.
    """
    if task.kind == "adhoc":
        return None
    row = implement_loop(ctx, task)
    assert row is not None
    if row.state in LOOP_ENDED_STATES:
        return row
    values = _spent(row)
    if cls == "env":
        values.update(env_retries=int(values.get("env_retries") or 0) + 1, try_worker=None)
    else:
        values["try_open"] = False
    row = _verdict(ctx, row, verdict=verdict, reason=reason, ok=False, cls=cls, spent=values)
    if cls == "code" and row.iteration >= _cap(row) and row.state == "running":
        why = f"{verdict}: {reason}" if reason and not reason.startswith(verdict) else reason
        row = _transition(ctx, row, "exhausted", why or verdict, limit=_cap(row))
    return row


def observe_rework(
    ctx: Ctx,
    task: FactoryTask,
    *,
    verdict: str,
    reason: str,
    source: str,
    human: bool = False,
    head: str | None = None,
) -> FactoryLoop | None:
    """
    A verifier sent *task* back (review CHANGES, verify or demo code FAIL, a human).

    The current try failed and the next one starts now (the author reworks;
    a new worker joins it); at the budget the loop is exhausted instead. A
    human's changes request grants its own try (the budget grows by one).
    """
    if task.kind == "adhoc":
        return None
    row = implement_loop(ctx, task)
    assert row is not None
    if row.iteration == 0:
        row = _update(ctx, row, iteration=max(1, task.attempts))
    if row.state == "cancelled":
        return row
    cap = _cap(row)
    if human:
        spec = {**(row.spec or {}), "attempts": max(cap, row.iteration) + 1}
        row = _update(ctx, row, spec=spec)
        cap = _cap(row)
    if row.iteration >= cap:
        values = {**_spent(row), "try_open": False}
        row = _verdict(
            ctx, row, verdict=verdict, reason=reason, ok=False, cls="code", spent=values
        )
        if row.state in ("running", "converged"):
            row = _transition(
                ctx, row, "exhausted", f"{source} {verdict}: {reason}", limit=cap, source=source
            )
        return row
    row = _verdict(ctx, row, verdict=verdict, reason=f"{source}: {reason}", ok=False, cls="code")
    return _iterate(
        ctx,
        row,
        verdict=verdict,
        reason=f"{source}: {reason}",
        cls="code",
        head=head,
        spent={"progress_at": int(ctx.now), "try_open": True, "try_worker": None},
        state="running" if row.state in ("converged", "running") else None,
    )


def observe_task_closed(ctx: Ctx, task: FactoryTask, to: str, reason: str | None) -> None:
    """A task closed: its implement loop converges (done) or ends (cancelled)."""
    row = implement_loop(ctx, task, create=False)
    if row is None or row.state in LOOP_ENDED_STATES:
        return
    if to == "done":
        _transition(ctx, row, "converged", reason or "task done")
    elif to == "cancelled":
        _transition(ctx, row, "cancelled", reason or "task cancelled")
    elif to == "failed":
        observe_failure(ctx, task, verdict="FAIL", reason=reason or "task failed")


def check_spend(ctx: Ctx, task: FactoryTask) -> None:
    """``budget.near`` at 80 % of the task's $ budget; exhausted at 100 %."""
    row = implement_loop(ctx, task, create=False)
    if row is None or row.state in LOOP_ENDED_STATES or task.cost_usd is None:
        return
    usd = round(float(task.cost_usd), 2)
    cap = float((row.spec or {}).get("usd") or 0.0)
    values = _spent(row)
    if values.get("usd") != usd:
        values["usd"] = usd
        row = _update(ctx, row, spent=values)
    if cap <= 0:
        return
    if usd >= NEAR_FRACTION * cap and not values.get("usd_near"):
        values["usd_near"] = True
        row = _update(ctx, row, spent=values)
        ctx.changes.append(
            Change(
                row,
                "budget.near",
                f"{_label(row)}: ${usd:.2f} of ${cap:.2f} spent",
                {"scope": "task", "usd": usd, "limit": cap, "task_id": task.id},
                severity="notice",
            )
        )
    if usd >= cap and row.state == "running":
        _transition(
            ctx, row, "exhausted", f"${usd:.2f} of ${cap:.2f} spent", limit=cap, money=True
        )


# ── Review (L2) ────────────────────────────────────────────────


def _evidence(pr: FactoryPr, gate: str) -> dict[str, Any]:
    found = ((pr.gates or {}).get("evidence") or {}).get(gate)
    return dict(found) if isinstance(found, Mapping) else {}


def _seen_marker(loop: FactoryLoop, key: str) -> bool:
    return key in (_spent(loop).get("markers") or [])


def _with_marker(values: dict[str, Any], key: str) -> dict[str, Any]:
    markers = [*(values.get("markers") or []), key][-MAX_MARKERS:]
    return {**values, "markers": markers}


def _task_of(ctx: Ctx, pr: FactoryPr) -> FactoryTask | None:
    if not pr.task_id:
        return None
    try:
        return ctx.store.get_task(pr.task_id)
    except ValueError:
        return None


def thrashed(rounds: Sequence[Mapping[str, Any]]) -> str | None:
    """
    A finding fixed in an earlier round that came back in the newest one.

    :param rounds: Review rounds oldest first, each with ``findings`` fingerprints.
    :returns: The returned fingerprint, or ``None``.
    """
    if len(rounds) < 3:
        return None
    current = list(rounds[-1].get("findings") or [])
    for fp in current:
        seen = [fp in (r.get("findings") or []) for r in rounds[:-1]]
        if True in seen and not seen[-1] and seen.index(True) < len(seen) - 1:
            return str(fp)
    return None


def dry(rounds: Sequence[Mapping[str, Any]]) -> bool:
    """
    The newest round adds no new blocking finding and fixes none.

    Without fingerprints, a blocking count that does not fall counts as dry.
    """
    if len(rounds) < 2:
        return False
    last, before = rounds[-1], rounds[-2]
    prints = set(last.get("findings") or [])
    blocking, previous = last.get("blocking"), before.get("blocking")
    count = blocking if isinstance(blocking, int) else (len(prints) if prints else None)
    earlier = previous if isinstance(previous, int) else len(before.get("findings") or []) or None
    if not count:
        return False
    seen = {fp for r in rounds[:-1] for fp in r.get("findings") or []}
    no_new = bool(prints) and prints <= seen
    not_falling = earlier is not None and count >= earlier
    if prints:
        return no_new and not_falling
    return not_falling


def observe_review(ctx: Ctx, pr: FactoryPr, head: str) -> FactoryLoop | None:
    """A review marker at *head* decided the review gate (APPROVE or not)."""
    found = _evidence(pr, "review")
    verdict = str(found.get("verdict") or "").upper()
    sha = str(found.get("sha") or "").lower()
    if not verdict or not sha or not head.lower().startswith(sha[:7]):
        return None
    row = _ensure(
        ctx,
        "review",
        pr_subject(pr.id),
        project=pr.project_slug,
        task_id=pr.task_id,
        pr_id=pr.id,
        spec=resolve_spec(ctx.factory_id, "review", project=_project(ctx, pr.project_slug)),
        label=_pr_label(pr),
    )
    key = f"{sha[:12]}@{found.get('at')}"
    if _seen_marker(row, key):
        return row
    blocking = found.get("blocking") if isinstance(found.get("blocking"), int) else None
    prints = [str(f) for f in found.get("findings") or [] if isinstance(f, str)]
    values = _with_marker(_spent(row), key)
    rounds = [
        *(values.get("rounds") or []),
        {"sha": sha[:12], "verdict": verdict, "blocking": blocking, "findings": prints},
    ][-MAX_ROUNDS:]
    values["rounds"] = rounds
    approve = verdict == "APPROVE"
    what = "APPROVE" if approve else f"{blocking if blocking is not None else '?'} blocking"
    row = _iterate(
        ctx,
        row,
        verdict=verdict,
        reason=f"{what} at {sha[:7]}",
        head=sha,
        ok=approve,
        spent={**values, "progress_at": int(ctx.now)},
        state="running" if row.state in ("converged", "stalled") else None,
    )
    if approve:
        return _transition(ctx, row, "converged", f"APPROVE at {sha[:7]}")
    task = _task_of(ctx, pr)
    if task is not None:
        observe_rework(
            ctx,
            task,
            verdict="CHANGES",
            reason=f"review round {row.iteration}: {what}",
            source="review",
            head=sha,
        )
    if row.state != "running":
        return row
    returned = thrashed(rounds)
    if returned is not None:
        return _transition(
            ctx,
            row,
            "thrashing",
            f"finding {returned} came back in round {row.iteration}",
            fingerprint=returned,
        )
    if dry(rounds):
        return _transition(
            ctx,
            row,
            "stalled",
            f"review dry: round {row.iteration} adds no new blocking finding and fixes none",
            dry=True,
        )
    limit = limit_of(row) or 0
    if row.iteration >= limit:
        return _transition(
            ctx, row, "exhausted", f"review {row.iteration}/{limit}: still {verdict}", limit=limit
        )
    return row


# ── Per-head loops: verify (L3), demo (L4), ci (L5) ────────────


def _head_loop(ctx: Ctx, kind: str, pr: FactoryPr, sha: str) -> FactoryLoop:
    """The *kind* loop of *pr* at *sha*; loops at older heads are superseded."""
    subject = head_subject(pr.id, sha)
    for other in ctx.store.list_loops(ctx.factory_id, kinds=[kind], pr_id=pr.id):
        if other.subject != subject and other.state not in LOOP_ENDED_STATES:
            _transition(ctx, other, "superseded", f"new head {sha[:7]}")
    return _ensure(
        ctx,
        kind,
        subject,
        project=pr.project_slug,
        task_id=pr.task_id,
        pr_id=pr.id,
        spec=resolve_spec(ctx.factory_id, kind, project=_project(ctx, pr.project_slug)),
        label=_pr_label(pr, sha),
    )


def _per_head(values: Mapping[str, Any], sha: str) -> dict[str, int]:
    found = values.get("per_head")
    counts = dict(found) if isinstance(found, Mapping) else {}
    counts[sha[:7]] = int(counts.get(sha[:7]) or 0) + 1
    return counts


def observe_verify(
    ctx: Ctx,
    pr: FactoryPr,
    sha: str,
    *,
    passed: bool,
    reason: str | None,
    verdict: str,
    default_class: str,
    key: str,
) -> FactoryLoop:
    """A verify marker or a failed preview at *sha*: env failures retry, code fails implement."""
    row = _head_loop(ctx, "verify", pr, sha)
    if _seen_marker(row, key) or row.state in LOOP_ENDED_STATES:
        return row
    values = _with_marker(_spent(row), key)
    values["per_head"] = _per_head(values, sha)
    cls = None if passed else classify(reason, default=default_class)
    if cls == "env":
        values["env_retries"] = int(values.get("env_retries") or 0) + 1
    row = _iterate(
        ctx, row, verdict=verdict, reason=reason, cls=cls, head=sha, ok=passed, spent=values
    )
    if passed:
        return _transition(ctx, row, "converged", f"verify PASS at {sha[:7]}")
    if cls == "code":
        task = _task_of(ctx, pr)
        if task is not None:
            observe_rework(
                ctx,
                task,
                verdict="VERIFY FAIL",
                reason=reason or "verify failed",
                source="verify",
                head=sha,
            )
    spec = row.spec or {}
    retries, runs = int(values.get("env_retries") or 0), values["per_head"][sha[:7]]
    if cls == "env" and retries > int(spec.get("env_retries") or 0):
        why = f"env failures {retries}/{spec.get('env_retries')} at {sha[:7]}"
    elif runs >= int(spec.get("per_head") or 0):
        why = f"verify failed {runs}/{spec.get('per_head')} at {sha[:7]}"
    else:
        return row
    return _transition(
        ctx, row, "exhausted", f"{why}: {reason or verdict}", env=cls == "env", head=sha
    )


def observe_demo(
    ctx: Ctx, pr: FactoryPr, sha: str, status: str, reason: str | None, key: str, exit_code: Any
) -> FactoryLoop:
    """A demo recorded at *sha*: PASS/PARTIAL converge, ERROR retries, FAIL charges implement."""
    row = _head_loop(ctx, "demo", pr, sha)
    if _seen_marker(row, key):
        return row
    values = _with_marker(_spent(row), key)
    values["per_head"] = _per_head(values, sha)
    passed = status in ("PASS", "PARTIAL")
    code = exit_code if isinstance(exit_code, int) else None
    cls = (
        None
        if passed
        else ("code" if status == "FAIL" else classify(reason, default="env", exit_code=code))
    )
    if cls == "env":
        values["env_retries"] = int(values.get("env_retries") or 0) + 1
    state = "running" if row.state in ("converged", "superseded") else None
    row = _iterate(
        ctx,
        row,
        verdict=status,
        reason=reason,
        cls=cls,
        head=sha,
        ok=passed,
        spent=values,
        state=state,
    )
    heads = {
        loop.subject for loop in ctx.store.list_loops(ctx.factory_id, kinds=["demo"], pr_id=pr.id)
    }
    spec = row.spec or {}
    if passed:
        return _transition(ctx, row, "converged", f"demo {status} at {sha[:7]}")
    if cls == "code":
        task = _task_of(ctx, pr)
        if task is not None:
            observe_rework(
                ctx,
                task,
                verdict="DEMO FAIL",
                reason=reason or "demo failed",
                source="demo",
                head=sha,
            )
    per_pr = int(spec.get("per_pr_heads") or 0)
    if per_pr and len(heads) > per_pr:
        return _transition(
            ctx, row, "exhausted", f"demos on {len(heads)} heads (budget {per_pr})", limit=per_pr
        )
    per_head = int(spec.get("per_head") or 0)
    if cls == "env" and per_head and values["per_head"][sha[:7]] >= per_head:
        return _transition(
            ctx,
            row,
            "exhausted",
            f"demo {status} {values['per_head'][sha[:7]]}/{per_head} at {sha[:7]}",
            limit=per_head,
        )
    return row


def observe_checks(ctx: Ctx, pr: FactoryPr, sha: str, to: str) -> FactoryLoop | None:
    """The checks gate of *pr* at *sha* passed or failed (ci loop); a green head converges L1."""
    row = _ci(ctx, pr, sha, to) if to in ("pass", "fail") else None
    task = _task_of(ctx, pr)
    if task is None or task.kind == "adhoc":
        return row
    loop = implement_loop(ctx, task, create=to in ("pass", "fail"))
    if loop is None:
        return row
    # Progress: a new commit, or checks going green.
    if _spent(loop).get("head") != sha[:12] or to == "pass":
        loop = _note_progress(ctx, loop, head=sha[:12])
    if to == "pass" and pr.state == "open" and not pr.is_draft:
        if loop.state in ("running", "stalled"):
            _transition(ctx, loop, "converged", f"PR open, checks green at {sha[:7]}")
    return row


def _ci(ctx: Ctx, pr: FactoryPr, sha: str, to: str) -> FactoryLoop:
    """The ci loop at *sha*: a failure is an iteration; fail → pass on one sha is a flake."""
    row = _head_loop(ctx, "ci", pr, sha)
    values = _spent(row)
    failing = (_evidence(pr, "checks").get("failing_names") or [])[:5]
    if to == "fail":
        values["fails"] = int(values.get("fails") or 0) + 1
        row = _iterate(
            ctx,
            row,
            verdict="FAIL",
            reason=", ".join(str(n) for n in failing) or "checks failed",
            head=sha,
            ok=False,
            spent=values,
            state="running" if row.state == "converged" else None,
        )
    else:
        flake = int(values.get("fails") or 0) > 0
        if flake:
            values["flakes"] = int(values.get("flakes") or 0) + 1
        row = _iterate(
            ctx,
            row,
            verdict="FLAKE" if flake else "PASS",
            reason=f"failed then passed at {sha[:7]}" if flake else None,
            head=sha,
            ok=True,
            spent=values,
        )
        row = _transition(ctx, row, "converged", f"checks green at {sha[:7]}", flake=flake)
    return row


# ── Claim (L6) ─────────────────────────────────────────────────


def observe_claim(ctx: Ctx, pr: FactoryPr, data: Mapping[str, Any]) -> FactoryLoop | None:
    """A READY claim (mismatch or not) or a Sentinel verdict on *pr*."""
    claim, sentinel = data.get("claim"), data.get("sentinel_verdict")
    if claim != "ready_for_human_merge" and sentinel is None:
        return None
    row = _ensure(
        ctx,
        "claim",
        pr_subject(pr.id),
        project=pr.project_slug,
        task_id=pr.task_id,
        pr_id=pr.id,
        spec=resolve_spec(ctx.factory_id, "claim", project=_project(ctx, pr.project_slug)),
        label=_pr_label(pr),
    )
    head = str(data.get("head_sha") or pr.head_sha or "")[:12]
    values = _spent(row)
    if sentinel == "verified" and (not head or pr.head_sha.startswith(head[:7])):
        values["streak"] = 0
        row = _iterate(
            ctx,
            row,
            verdict="VERIFIED",
            reason=f"Sentinel verified {head[:7]}",
            ok=True,
            spent=values,
            state="running" if row.state != "running" else None,
        )
        return _transition(ctx, row, "converged", f"verified at {head[:7]}")
    mismatch = bool(data.get("claim_mismatch")) or sentinel == "not_verified"
    values["streak"] = int(values.get("streak") or 0) + 1 if mismatch else 0
    if claim == "ready_for_human_merge" and head:
        values["claimed_head"] = head
    verdict = (
        "NOT_VERIFIED" if sentinel == "not_verified" else ("MISMATCH" if mismatch else "CLAIMED")
    )
    row = _iterate(
        ctx,
        row,
        verdict=verdict,
        reason=_clip(data.get("note")) or f"READY claim at {head[:7]}",
        ok=not mismatch if mismatch else None,
        spent=values,
        state="running" if row.state == "converged" else None,
    )
    limit = int((row.spec or {}).get("mismatches") or 0)
    if limit and values["streak"] >= limit and row.state == "running":
        return _transition(
            ctx,
            row,
            "exhausted",
            f"{values['streak']} READY claims in a row the server's gates or Sentinel refuted",
            limit=limit,
        )
    return row


# ── Plan (L7) and the clock rules ──────────────────────────────


def check_plan(
    ctx: Ctx, slug: str, rows: Sequence[FactoryTask], *, paused: bool
) -> FactoryLoop | None:
    """The plan loop of a project: converged when every task closed, stalled without closes."""
    live = [t for t in rows if t.state != "cancelled"]
    if not live:
        return None
    row = _ensure(
        ctx,
        "plan",
        project_subject(slug),
        project=slug,
        spec=resolve_spec(ctx.factory_id, "plan", project=_project(ctx, slug)),
        label=slug,
    )
    closed = [t for t in live if t.state in ("done", "failed")]
    if len(closed) > row.iteration:
        row = _iterate(
            ctx,
            row,
            n=len(closed),
            verdict="CLOSED",
            reason=f"{len(closed)}/{len(live)} tasks closed",
            ok=True,
            spent={"progress_at": int(ctx.now)},
            state="running" if row.state == "stalled" else None,
        )
    if all(t.state in ("done", "failed") for t in live) and any(t.state == "done" for t in live):
        values = _spent(row)
        if not values.get("all_done_at"):
            values["all_done_at"] = int(ctx.now)
            row = _update(ctx, row, spent=values)
        return _transition(ctx, row, "converged", "every task closed")
    if row.state == "converged":
        values = {**_spent(row), "all_done_at": None, "done_asked": False}
        row = _update(ctx, row, spent=values)
        row = _transition(ctx, row, "running", "a task reopened")
    stall_s = float((row.spec or {}).get("stall_h") or 0) * 3600
    last = max([int(t.closed_at or 0) for t in closed] + [row.started_at])
    if stall_s and not paused and row.state == "running" and ctx.now - last >= stall_s:
        hours = int((ctx.now - last) // 3600)
        return _transition(ctx, row, "stalled", f"no task closed in {hours} h")
    return row


def check_clock(ctx: Ctx, loop: FactoryLoop, *, paused: bool) -> FactoryLoop:
    """The wall and no-progress rules of a running implement loop (fake-clock testable)."""
    if loop.kind != "implement" or loop.state != "running" or paused:
        return loop
    spec, values = loop.spec or {}, _spent(loop)
    wall = int(ctx.now - loop.started_at)
    if values.get("wall_s") != wall:
        values["wall_s"] = wall
        loop = _update(ctx, loop, spent=values)
    wall_h = float(spec.get("wall_h") or 0)
    if wall_h and wall >= wall_h * 3600:
        return _transition(
            ctx, loop, "stalled", f"running {wall // 3600} h (wall budget {wall_h:g} h)", wall=True
        )
    idle_budget = float(spec.get("no_progress_s") or 0)
    since = int(values.get("progress_at") or loop.started_at)
    idle = int(ctx.now - since)
    if idle_budget and idle >= idle_budget:
        return _transition(
            ctx, loop, "stalled", f"no progress for {idle // 60} min", no_progress=True
        )
    return loop


# ── Event mapping ──────────────────────────────────────────────

OBSERVED_KINDS: Final = frozenset(
    {
        "task.state",
        "task.bound",
        "task.failed",
        "gate.changed",
        "pr.human_review",
        "pr.claim",
        "demo.published",
        "preview.reported",
        "preview.sha_mismatch",
        "env.reported",
    }
)
_ENV_FAILED: Final = frozenset({"failed", "degraded", "down"})


def _get_task(ctx: Ctx, task_id: Any) -> FactoryTask | None:
    if not isinstance(task_id, str) or not task_id:
        return None
    try:
        return ctx.store.get_task(task_id)
    except ValueError:
        return None


def _get_pr(ctx: Ctx, pr_id: Any) -> FactoryPr | None:
    if not isinstance(pr_id, str) or not pr_id:
        return None
    try:
        pr = ctx.store.get_pr(pr_id)
    except ValueError:
        return None
    return pr if pr is not None and norm_id(pr.factory_id) == norm_id(ctx.factory_id) else None


def _pr_of_event(ctx: Ctx, event: FactoryEvent, data: Mapping[str, Any]) -> FactoryPr | None:
    """The PR an event names: ``data.pr_id``, else its ``pr:<repo>#<n>`` subject."""
    pr = _get_pr(ctx, data.get("pr_id"))
    if pr is not None:
        return pr
    return pr_from_subject(ctx.store, ctx.factory_id, event.subject)


def pr_from_subject(store: Any, factory_id: str, subject: str | None) -> FactoryPr | None:
    """:returns: The PR of a ``pr:<owner/repo>#<n>`` event subject, or ``None``."""
    repo, sep, number = (subject or "").removeprefix("pr:").rpartition("#")
    if not (subject or "").startswith("pr:") or not sep or not number.isdigit():
        return None
    return store.get_pr_by_number(factory_id, repo, int(number))


def _task_of_session(ctx: Ctx, session_id: str) -> FactoryTask | None:
    sid = norm_id(session_id)
    for task in ctx.store.list_tasks(ctx.factory_id):
        if sid in {norm_id(s) for s in task.session_ids or []}:
            return task
    return None


def handle_event(ctx: Ctx, event: FactoryEvent) -> None:
    """Map one stored event onto loop iterations and transitions (synchronous)."""
    kind = event.kind
    data = event.data if isinstance(event.data, dict) else {}
    if kind == "task.state":
        task = _get_task(ctx, data.get("task_id"))
        if task is None:
            return
        attempt = data.get("attempt")
        if isinstance(attempt, int) and data.get("to") == "running":
            observe_attempt(ctx, task, attempt, "start")
        result = data.get("result")
        note = data.get("note") if isinstance(data.get("note"), str) else None
        if result == "BLOCKED":
            observe_failure(
                ctx,
                task,
                verdict="BLOCKED",
                reason=f"BLOCKED: {note or 'no reason'}",
                cls=classify(note, default="code"),
            )
        elif result == "FAIL":
            observe_failure(
                ctx, task, verdict="FAIL", reason=note, cls=classify(note, default="code")
            )
        elif result == "PASS":
            loop = implement_loop(ctx, task, create=False)
            if loop is not None:
                _note_progress(ctx, loop)
        if data.get("to") in ("done", "cancelled", "failed") and data.get("from") != data.get(
            "to"
        ):
            observe_task_closed(ctx, task, str(data["to"]), data.get("reason"))
        check_spend(ctx, task)
    elif kind == "task.bound":
        task = _get_task(ctx, data.get("task_id"))
        attempt = data.get("attempt")
        if task is not None and data.get("loop") == "implement" and isinstance(attempt, int):
            observe_attempt(ctx, task, attempt, "dispatch")
    elif kind == "task.failed" and event.to_id:
        worker = ctx.deps.conversation_store.get_conversation(event.to_id)
        implementer = worker is not None and worker.sub_agent_name in tasks.HARNESSES
        task = _task_of_session(ctx, event.to_id) if implementer else None
        if task is not None and task.state not in ("done", "cancelled"):
            observe_failure(ctx, task, verdict="FAIL", reason="the worker's run failed")
            check_spend(ctx, task)
    elif kind == "gate.changed":
        pr = _get_pr(ctx, data.get("pr_id"))
        head = str(data.get("head_sha") or "")
        gate, to = data.get("gate"), data.get("to")
        if pr is None or not head:
            return
        if gate == "checks":
            observe_checks(ctx, pr, head, str(to))
        elif gate == "review" and to in ("pass", "fail"):
            observe_review(ctx, pr, head)
        elif gate == "verify" and to in ("pass", "fail"):
            found = _evidence(pr, "verify")
            observe_verify(
                ctx,
                pr,
                head,
                passed=to == "pass",
                reason=found.get("reason") or (None if to == "pass" else "verify FAIL"),
                verdict="PASS" if to == "pass" else "FAIL",
                default_class="code",
                key=f"verify:{found.get('sha')}@{found.get('at')}",
            )
    elif kind == "preview.reported" and data.get("state") == "failed":
        pr = _get_pr(ctx, data.get("pr_id"))
        if pr is not None and pr.state == "open" and pr.head_sha:
            note = (pr.preview or {}).get("note")
            observe_verify(
                ctx,
                pr,
                str(data.get("sha") or pr.head_sha),
                passed=False,
                reason=_clip(note) or "PREVIEW FAILED",
                verdict="PREVIEW FAILED",
                default_class="env",
                key=f"preview:{event.id}",
            )
    elif kind == "preview.sha_mismatch":
        pr = _get_pr(ctx, data.get("pr_id"))
        if pr is not None and pr.state == "open" and pr.head_sha:
            observe_verify(
                ctx,
                pr,
                pr.head_sha,
                passed=False,
                reason=f"env: stale preview serves {str(data.get('probed') or '')[:7]}",
                verdict="STALE",
                default_class="env",
                key=f"stale:{event.id}",
            )
    elif kind == "demo.published":
        demo = ctx.store.get_demo(str(data.get("demo_id") or "")) if data.get("demo_id") else None
        pr = _get_pr(ctx, data.get("pr_id") or (demo.pr_id if demo is not None else None))
        if demo is not None and pr is not None:
            manifest = demo.manifest if isinstance(demo.manifest, Mapping) else {}
            observe_demo(
                ctx,
                pr,
                demo.head_sha,
                str(demo.status).upper(),
                _clip(demo.summary, 200),
                f"demo:{demo.id}",
                manifest.get("exit_code"),
            )
    elif kind == "pr.human_review":
        pr = _pr_of_event(ctx, event, data)
        task = _task_of(ctx, pr) if pr is not None else None
        if task is not None and data.get("verdict") == "changes_requested":
            observe_rework(
                ctx,
                task,
                verdict="HUMAN CHANGES",
                reason=f"changes requested by {data.get('by') or 'a human'}",
                source="human review",
                human=True,
            )
    elif kind == "pr.claim":
        pr = _pr_of_event(ctx, event, data)
        if pr is not None:
            observe_claim(ctx, pr, data)
    elif kind == "env.reported" and event.project_slug:
        _note_env(ctx, event.project_slug, str(data.get("state") or ""))


def _note_env(ctx: Ctx, slug: str, state: str) -> None:
    """Record the dev env's state on the project's plan loop (context for failures)."""
    row = ctx.store.get_loop_by_subject(ctx.factory_id, "plan", project_subject(slug))
    if row is None:
        return
    values = {**_spent(row), "env_state": state, "env_at": int(ctx.now)}
    _update(ctx, row, spent=values)


# ── Locks and the async driver ─────────────────────────────────

_locks: dict[tuple[int, str], asyncio.Lock] = {}
_clock: Callable[[], float] = time.time
"""Wall clock of event-driven passes (tests swap it for a fake one)."""


def lock(factory_id: str) -> asyncio.Lock:
    """One loop-engine pass at a time per factory (in this process)."""
    key = (id(asyncio.get_running_loop()), norm_id(factory_id))
    found = _locks.get(key)
    if found is None:
        if len(_locks) > 4096:
            _locks.clear()
        found = _locks[key] = asyncio.Lock()
    return found


def _deps(app_state: Any) -> FactoryDeps | None:
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    return deps if isinstance(deps, FactoryDeps) else None


async def on_event(app_state: Any, event: FactoryEvent) -> None:
    """The ``events.add_listener`` hook: observed facts become loop iterations. Never raises."""
    if event.kind not in OBSERVED_KINDS:
        return
    deps = _deps(app_state)
    if deps is None:
        return
    try:
        async with lock(event.factory_id):
            ctx = Ctx(deps, norm_id(event.factory_id), _clock())
            await asyncio.to_thread(handle_event, ctx, event)
        await apply(app_state, ctx)
    except Exception:  # noqa: BLE001 - the event log swallows it too; the next fact retries
        _logger.warning("factory loops: could not observe %s", event.kind, exc_info=True)


async def observe_started(
    app_state: Any, factory_id: str, slug: str, key: str, attempt: int
) -> None:
    """``sys_factory_task(op="start")`` of a task that was already running. Never raises."""
    deps = _deps(app_state)
    if deps is None:
        return
    try:
        async with lock(factory_id):
            ctx = Ctx(deps, norm_id(factory_id), _clock())

            def run() -> None:
                task = ctx.store.get_task_by_key(ctx.factory_id, slug, key)
                if task is not None:
                    observe_attempt(ctx, task, attempt, "start")

            await asyncio.to_thread(run)
        await apply(app_state, ctx)
    except Exception:  # noqa: BLE001 - the binder still sees the worker's title
        _logger.warning("factory loops: could not observe the start of %s", key, exc_info=True)


async def tick(
    app_state: Any,
    members: Sequence[Conversation],
    _children: Sequence[Conversation],
    now: float | None = None,
) -> None:
    """
    The supervisor's per-tick hook. Never raises.

    Mirrors each factory's engine mode onto its PLs (the guard reads it),
    applies the wall, no-progress, $ and plan rules, checks the project and
    factory $ budgets, sends READY waves and flushes coalesced NEXT hints.
    """
    from omnigent.server.factory import budgets, loop_escalation, pipeline

    deps = _deps(app_state)
    if deps is None:
        return
    at = _clock() if now is None else now
    factory_ids = sorted({norm_id(m.labels[LABEL_ID]) for m in members if m.labels.get(LABEL_ID)})
    for factory_id in factory_ids:
        try:
            mine = [m for m in members if norm_id(m.labels.get(LABEL_ID) or "") == factory_id]
            await asyncio.to_thread(_mirror_mode, deps, factory_id, mine)
            async with lock(factory_id):
                ctx = Ctx(deps, factory_id, at)
                await asyncio.to_thread(tick_sync, ctx)
            await apply(app_state, ctx)
            await loop_escalation.done_checks(app_state, deps, factory_id, at)
            await budgets.check(app_state, factory_id, now=at)
            await pipeline.on_tick(app_state, factory_id, now=at)
        except Exception:  # noqa: BLE001 - one factory must not stop the others
            _logger.warning("factory loops: tick failed for %s", factory_id, exc_info=True)


def _mirror_mode(deps: FactoryDeps, factory_id: str, members: Sequence[Conversation]) -> None:
    """Copy the root's engine mode onto its project leads (where the guard sees it)."""
    mode = engine_mode(deps, factory_id)
    for member in members:
        if member.labels.get(LABEL_ROLE) != ROLE_PROJECT_LEAD:
            continue
        if member.labels.get(ENGINE_MODE_LABEL) != mode:
            deps.conversation_store.set_labels(member.id, {ENGINE_MODE_LABEL: mode})


def _paused_projects(deps: FactoryDeps, factory_id: str) -> tuple[bool, set[str]]:
    from omnigent.server.factory.control import find_project_pl

    store = deps.conversation_store
    root = store.get_conversation(factory_id)
    root_paused = root is not None and root.labels.get(LABEL_STATE) == STATE_PAUSED
    paused: set[str] = set()
    for project in deps.factory_store.list_projects(factory_id):
        pl = find_project_pl(deps, factory_id, project.slug)
        if pl is not None and pl.labels.get(LABEL_STATE) == STATE_PAUSED:
            paused.add(project.slug)
    return root_paused, paused


def tick_sync(ctx: Ctx) -> None:
    """The clock rules over one factory (synchronous): wall, no progress, $, plan."""
    root_paused, paused = _paused_projects(ctx.deps, ctx.factory_id)
    rows = ctx.store.list_tasks(ctx.factory_id)
    projects = {p.slug: p for p in ctx.store.list_projects(ctx.factory_id)}
    by_task = {norm_id(t.id): t for t in rows}
    for loop in ctx.store.list_loops(ctx.factory_id, kinds=["implement"], states=["running"]):
        task = by_task.get(norm_id(loop.task_id or ""))
        if task is None or task.state in ("done", "cancelled"):
            continue
        check_spend(ctx, task)
        current = ctx.store.get_loop(loop.id) or loop
        check_clock(ctx, current, paused=root_paused or task.project_slug in paused)
    for slug in sorted({t.project_slug for t in rows}):
        project = projects.get(slug)
        if project is not None and project.status not in (None, "active"):
            continue
        check_plan(
            ctx,
            slug,
            [t for t in rows if t.project_slug == slug],
            paused=root_paused or slug in paused,
        )


# ── Applying changes: events, spans, escalation ────────────────


def _event_subject(deps: FactoryDeps, loop: FactoryLoop) -> str:
    if loop.pr_id:
        try:
            pr = deps.factory_store.get_pr(loop.pr_id)
        except ValueError:
            pr = None
        if pr is not None:
            return f"pr:{pr.repo}#{pr.number}"
    return f"loop:{loop.id}"


def _loop_data(loop: FactoryLoop) -> dict[str, Any]:
    return {
        "loop_id": loop.id,
        "kind": loop.kind,
        "subject": loop.subject,
        "state": loop.state,
        "iteration": loop.iteration,
        "limit": limit_of(loop),
        "task_id": loop.task_id,
        "pr_id": loop.pr_id,
        "decision_id": loop.decision_id,
        "def_version": loop.def_version,
        "by": "server",
        "by_kind": "server",
    }


_SPAN_OUTCOME: Final = {
    "converged": "ok",
    "cancelled": "cancelled",
    "superseded": "superseded",
    "exhausted": "exhausted",
}


def _write_spans(deps: FactoryDeps, factory_id: str, change: Change) -> None:
    loop = change.loop
    common = {
        "project_slug": loop.project_slug,
        "pr_id": loop.pr_id,
        "task_id": loop.task_id,
        "loop_id": loop.id,
    }
    ctx = span_context(deps, factory_id)
    outcome = _SPAN_OUTCOME.get(change.what)
    reopen = change.what in ("budget_raised",) or (
        change.what == "iteration" and loop.state == "running"
    )
    parent = write_span(
        deps.factory_store,
        factory_id,
        kind="loop",
        name=loop.kind,
        dedupe_key=f"loop:{loop.id}",
        started_at=loop.started_at * 1000,
        ended_at=(loop.ended_at or loop.updated_at) * 1000 if outcome else None,
        outcome=outcome or ("open" if reopen else None),
        force=bool(outcome),
        reopen=reopen,
        attrs={"loop": loop.kind, "subject": loop.subject, "state": loop.state},
        ctx=ctx,
        **common,
    )
    span = change.iter_span
    if span is None:
        return
    previous = span.get("previous")
    if isinstance(previous, int) and previous >= 1:
        # A new iteration ends the one before it, unless a verdict already closed it.
        for found in deps.factory_store.list_open_spans(factory_id, "loop_iter", loop_id=loop.id):
            if found.dedupe_key == f"loop_iter:{loop.id}:{previous}":
                deps.factory_store.update_span(
                    found.id,
                    ended_at=max(found.started_at, int(span["started_ms"])),
                    outcome="superseded",
                )
    attrs = {k: span.get(k) for k in ("verdict", "reason", "class") if span.get(k) is not None}
    write_span(
        deps.factory_store,
        factory_id,
        kind="loop_iter",
        name=f"{loop.kind} {span['n']}",
        dedupe_key=f"loop_iter:{loop.id}:{span['n']}",
        started_at=int(span["started_ms"]),
        ended_at=span.get("ended_ms"),
        outcome=span.get("outcome"),
        attrs={"loop": loop.kind, "iteration": span["n"], **attrs},
        ctx=ctx,
        parent_id=parent.id,
        head_sha=(span.get("head_sha") or None) and str(span["head_sha"])[:40],
        attempt=span["n"] if loop.kind == "implement" else None,
        **common,
    )


async def apply(app_state: Any, ctx: Ctx) -> None:
    """Emit the collected changes, write their spans and escalate bad states. Never raises."""
    if not ctx.changes:
        return
    deps = ctx.deps
    fid = ctx.factory_id
    mode = await asyncio.to_thread(engine_mode, deps, fid)
    for change in ctx.changes:
        loop = change.loop
        try:
            await asyncio.to_thread(_write_spans, deps, fid, change)
        except Exception:  # noqa: BLE001 - heal rebuilds spans from the tables
            _logger.warning("factory loops: no span for %s", loop.id, exc_info=True)
        if change.what == "verdict":
            continue
        kind = change.what if change.what.startswith("budget.") else f"loop.{change.what}"
        subject = await asyncio.to_thread(_event_subject, deps, loop)
        await events.emit(
            app_state,
            fid,
            kind,
            severity=change.severity,
            project=loop.project_slug,
            subject=subject,
            summary=change.summary,
            data={**_loop_data(loop), **change.data, "mode": mode},
            ts=ctx.ms,
        )
        if change.what in BAD_STATES and mode != "observe":
            from omnigent.server.factory import loop_escalation

            await loop_escalation.escalate(app_state, deps, loop, change, mode)
        elif change.what == "budget_violation" and mode != "observe":
            from omnigent.server.factory import loop_escalation

            await loop_escalation.violation(app_state, loop, change)
    from omnigent.server.factory import pipeline

    await pipeline.on_loops(app_state, fid, [c.loop for c in ctx.changes])


# ── Views ──────────────────────────────────────────────────────

_SPENT_VIEW: Final = ("attempts", "usd", "wall_s", "per_head", "env_retries")


def loop_chip(loop: FactoryLoop) -> dict[str, Any]:
    """:returns: The :class:`~omnigent.server.factory.schemas.LoopChipView` JSON."""
    return {
        "id": loop.id,
        "kind": loop.kind,
        "state": loop.state,
        "iteration": loop.iteration,
        "limit": limit_of(loop),
    }


def loop_view(
    loop: FactoryLoop,
    *,
    tasks_by_id: Mapping[str, FactoryTask] | None = None,
    prs_by_id: Mapping[str, FactoryPr] | None = None,
    decisions_by_id: Mapping[str, FactoryDecision] | None = None,
) -> dict[str, Any]:
    """:returns: The :class:`~omnigent.server.factory.schemas.LoopView` JSON."""
    from omnigent.server.factory import schemas

    values = _spent(loop)
    spent = {k: values.get(k) for k in _SPENT_VIEW if values.get(k) is not None}
    if not isinstance(spent.get("per_head"), dict):
        spent.pop("per_head", None)
    task = (tasks_by_id or {}).get(norm_id(loop.task_id)) if loop.task_id else None
    pr = (prs_by_id or {}).get(norm_id(loop.pr_id)) if loop.pr_id else None
    decision = (decisions_by_id or {}).get(norm_id(loop.decision_id)) if loop.decision_id else None
    body = {
        "id": loop.id,
        "kind": loop.kind,
        "subject": loop.subject,
        "project_slug": loop.project_slug,
        "task_id": loop.task_id,
        "task_ref": task.ref if task is not None else None,
        "pr_id": loop.pr_id,
        "pr_number": pr.number if pr is not None else None,
        "state": loop.state,
        "iteration": loop.iteration,
        "limit": limit_of(loop),
        "spec": dict(loop.spec or {}),
        "spent": spent,
        "last_verdict": loop.last_verdict,
        "last_reason": loop.last_reason,
        "decision_id": loop.decision_id,
        "decision_ref": decision.ref if decision is not None else None,
        "def_version": loop.def_version,
        "started_at": loop.started_at,
        "updated_at": loop.updated_at,
        "ended_at": loop.ended_at,
    }
    return schemas.LoopView.model_validate(body).model_dump(mode="json")


def list_view(
    deps: FactoryDeps,
    factory_id: str,
    *,
    states: Sequence[str] | None = None,
    project: str | None = None,
    kinds: Sequence[str] | None = None,
    pr_id: str | None = None,
) -> list[dict[str, Any]]:
    """The factory's loops as :class:`LoopView` JSON, newest activity first (synchronous)."""
    store = deps.factory_store
    rows = store.list_loops(
        factory_id, states=states, kinds=kinds, project_slug=project, pr_id=pr_id
    )
    task_rows = {norm_id(t.id): t for t in store.list_tasks(factory_id, project_slug=project)}
    pr_rows = {norm_id(p.id): p for p in store.list_prs(factory_id)}
    decisions: dict[str, FactoryDecision] = {}
    for row in rows:
        if row.decision_id and norm_id(row.decision_id) not in decisions:
            found = store.get_decision(row.decision_id)
            if found is not None:
                decisions[norm_id(found.id)] = found
    rows.sort(key=lambda r: (r.updated_at, r.started_at), reverse=True)
    return [
        loop_view(r, tasks_by_id=task_rows, prs_by_id=pr_rows, decisions_by_id=decisions)
        for r in rows
    ]


def snapshot_fields(
    deps: FactoryDeps, factory_id: str, prs: Iterable[FactoryPr]
) -> tuple[dict[str, dict[str, int]], dict[str, list[dict[str, Any]]]]:
    """
    What the snapshot shows of the loops (synchronous; never raises).

    :returns: ``(project slug → LoopsSummaryView JSON, PR id → LoopChipView list)``;
        a PR's chips are its task's implement loop plus its own live (or last) loops.
    """
    try:
        rows = deps.factory_store.list_loops(factory_id)
    except Exception:  # noqa: BLE001 - the snapshot renders without them
        _logger.warning("factory loops: no snapshot fields for %s", factory_id, exc_info=True)
        return {}, {}
    summaries: dict[str, dict[str, int]] = {}
    for row in rows:
        if not row.project_slug:
            continue
        counts = summaries.setdefault(row.project_slug, _empty_summary())
        state = "escalated" if row.state == "escalated" else row.state
        if state in counts:
            counts[state] += 1
    chips: dict[str, list[dict[str, Any]]] = {}
    by_task = {norm_id(r.task_id): r for r in rows if r.kind == "implement" and r.task_id}
    for pr in prs:
        mine: list[FactoryLoop] = []
        implement = by_task.get(norm_id(pr.task_id)) if pr.task_id else None
        if implement is not None:
            mine.append(implement)
        own = [
            r
            for r in rows
            if r.pr_id and norm_id(r.pr_id) == norm_id(pr.id) and r.kind != "implement"
        ]
        for kind in ("review", "verify", "demo", "ci", "claim"):
            of_kind = [r for r in own if r.kind == kind]
            live = [r for r in of_kind if r.state != "superseded"] or of_kind
            if live:
                mine.append(max(live, key=lambda r: (r.started_at, r.updated_at)))
        chips[norm_id(pr.id)] = [loop_chip(r) for r in mine]
    return summaries, chips


def _empty_summary() -> dict[str, int]:
    states = ("running", "converged", "stalled", "thrashing", "exhausted", "escalated")
    return dict.fromkeys(states, 0)


# ── sys_factory_task(op="start") in enforce mode ───────────────


def start_refusal(
    deps: FactoryDeps, factory_id: str, task: FactoryTask, mode: str
) -> CockpitError | None:
    """
    The 409 ``budget_exhausted`` an enforce-mode ``start`` gets past the budget (synchronous).

    Past the budget means the task's implement loop is exhausted, or (no loop
    yet) its attempts reached the budget. Other modes never refuse; the loop
    records the dispatch instead.
    """
    if mode != "enforce" or task.kind == "adhoc":
        return None
    store = deps.factory_store
    loop = store.get_loop_by_subject(factory_id, "implement", task_subject(task.id))
    if loop is not None:
        exhausted = loop.state == "exhausted"
        cap = _cap(loop)
    else:
        spec = resolve_spec(
            factory_id,
            "implement",
            project=store.get_project(factory_id, task.project_slug),
            task=task,
        )
        cap = int(spec["attempts"])
        exhausted = task.attempts >= cap
    if not exhausted:
        return None
    decision = (
        store.get_decision(loop.decision_id) if loop is not None and loop.decision_id else None
    )
    ref = decision.ref if decision is not None else None
    tail = f" until {ref} is answered" if ref else "; a person raises it"
    return CockpitError(
        409,
        "budget_exhausted",
        f"{task_label(task)}: implement {loop.iteration if loop else task.attempts}/{cap} is "
        f"exhausted; do not dispatch it again{tail}",
        decision=ref,
        decision_id=decision.id if decision is not None else None,
    )


def start_warning(deps: FactoryDeps, factory_id: str, task: FactoryTask) -> str | None:
    """The warning an assist-mode ``start`` past the budget carries (it is still allowed)."""
    loop = deps.factory_store.get_loop_by_subject(factory_id, "implement", task_subject(task.id))
    if loop is None or loop.state != "exhausted":
        return None
    decision = deps.factory_store.get_decision(loop.decision_id) if loop.decision_id else None
    ref = f" {decision.ref} is open" if decision is not None else ""
    return (
        f"implement {loop.iteration}/{_cap(loop)} is exhausted;{ref} - this dispatch is "
        "recorded as a budget violation"
    )


def install() -> Callable[[], None]:
    """Register :func:`on_event` on the event log. :returns: The uninstaller."""
    return events.add_listener(on_event)


def reset() -> None:
    """Forget locks (tests)."""
    _locks.clear()


__all__ = [
    "ACTIVE_STATES",
    "BAD_STATES",
    "CATALOGUE",
    "DEFAULT_BUDGETS",
    "LOOP_KINDS",
    "Change",
    "Ctx",
    "LoopSpec",
    "apply",
    "budget",
    "check_clock",
    "check_plan",
    "check_spend",
    "classify",
    "dry",
    "engine_mode",
    "handle_event",
    "head_subject",
    "implement_loop",
    "install",
    "limit_of",
    "list_view",
    "loop_chip",
    "loop_view",
    "observe_attempt",
    "observe_checks",
    "observe_claim",
    "observe_demo",
    "observe_failure",
    "observe_review",
    "observe_rework",
    "observe_verify",
    "on_event",
    "pr_subject",
    "project_subject",
    "reset",
    "resolve_spec",
    "set_budget_resolver",
    "snapshot_fields",
    "start_refusal",
    "start_warning",
    "task_label",
    "task_subject",
    "thrashed",
    "tick",
    "tick_sync",
]
