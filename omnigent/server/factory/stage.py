"""Server-derived project stage: the cockpit's headline, computed from gates.

Pure functions. The agent's own claim (``agent_stage``) is never the headline
while the gates have a say; it is compared against the derived stage to flag
"claim ≠ gates". Once every PR is merged the gates are silent, so open tasks,
a PL claim made after the merge, or a merge older than the project itself keep
a project that still works from reading "Merged".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import (
    AGENT_STAGES,
    HEALTH_BLOCKED,
    HEALTH_DEAD,
    LABEL_HEALTH,
    LABEL_STATE,
    STAGE_BUILDING,
    STAGE_DONE,
    STAGE_MERGED,
    STAGE_PLANNING,
    STAGE_QUEUED,
    STAGE_READY,
    STAGE_REVIEW,
    STAGE_VERIFY,
    STAGES,
    STATE_DONE,
    STATE_PAUSED,
    TASK_CLOSED_STATES,
    TASK_STATES,
)

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactoryDecision, FactoryPr, FactoryProject, FactoryTask

OVERLAY_PAUSED: Final = "paused"
OVERLAY_BLOCKED: Final = "blocked"
OVERLAY_FAILED: Final = "failed"
OVERLAY_PR_CLOSED: Final = "pr_closed"
OVERLAY_CANCELLED: Final = "cancelled"
OVERLAY_MERGED: Final = "merged"
OVERLAY_WAITING: Final = "waiting"
OVERLAYS: Final = frozenset(
    {
        OVERLAY_PAUSED,
        OVERLAY_BLOCKED,
        OVERLAY_FAILED,
        OVERLAY_PR_CLOSED,
        OVERLAY_CANCELLED,
        OVERLAY_MERGED,
        OVERLAY_WAITING,
    }
)

# Gate values that let a PR move past a gate (skip = not applicable).
_PASSING: Final = frozenset({"pass", "skip"})
_RANK: Final = {stage: i for i, stage in enumerate(STAGES)}


@dataclass(frozen=True)
class StageResult:
    """
    The derived stage of one project.

    :param stage: One of :data:`omnigent.factory.STAGES`.
    :param overlay: ``paused`` / ``blocked`` / ``failed`` / ``pr_closed``, the
        lifecycle overlays ``cancelled`` / ``merged`` (closed projects) and
        ``waiting`` (held on its dependencies), or ``None``.
    :param overlay_reason: Human text for the overlay, e.g. ``"Blocked on D-3"`` or
        ``"merged → api-version"``.
    :param claim_mismatch: The PL claims a stage further along than the gates show
        (not while a human's change request holds an open PR's head: that drop
        is the human's, and the PL hears of it by mail).
    :param note: Why the stage is not what the PRs alone say, e.g.
        ``"!9774 was merged before this project started"``.
    """

    stage: str
    overlay: str | None = None
    overlay_reason: str | None = None
    claim_mismatch: bool = False
    note: str | None = None


def stage_rank(stage: str | None) -> int:
    """:returns: The pipeline position of *stage* (-1 when unknown)."""
    return _RANK.get(stage or "", -1)


def _gate(pr: FactoryPr, name: str) -> str:
    value = (pr.gates or {}).get(name)
    return value if isinstance(value, str) else "unknown"


def changes_requested_on_head(review: Mapping[str, Any] | None, head: str | None) -> bool:
    """
    A human asked for changes on *head* (or on a head nobody recorded).

    Such a head is never ready for a human: the hold lifts with a new head or an approval.

    :param review: The PR's ``human_review``.
    :param head: The PR's current head sha.
    """
    if not isinstance(review, Mapping) or review.get("verdict") != "changes_requested":
        return False
    sha = review.get("head_sha")
    if not isinstance(sha, str) or not sha or not head:
        return True
    return sha.lower() == head.lower()


def ready_for_human(pr: FactoryPr) -> bool:
    """:returns: Every gate of *pr* passes and no human change request holds its head."""
    return bool(pr.ready) and not changes_requested_on_head(pr.human_review, pr.head_sha)


def pr_stage(pr: FactoryPr) -> str:
    """
    The stage one open PR implies.

    Draft, checks not passing, or a human change request on the current head
    is building; review not passing is review; anything else short of ready
    (verify pending/failed/skipped, mergeable, protected paths) is verify.
    """
    if ready_for_human(pr):
        return STAGE_READY
    if (
        pr.is_draft
        or _gate(pr, "draft") not in _PASSING
        or _gate(pr, "checks") not in _PASSING
        or changes_requested_on_head(pr.human_review, pr.head_sha)
    ):
        return STAGE_BUILDING
    if _gate(pr, "review") not in _PASSING:
        return STAGE_REVIEW
    return STAGE_VERIFY


def _stage_from_prs(prs: Sequence[FactoryPr]) -> tuple[str, str | None]:
    if not prs:
        return STAGE_PLANNING, None
    open_prs = [p for p in prs if p.state == "open"]
    if any(ready_for_human(p) for p in open_prs):
        return STAGE_READY, None
    if open_prs:
        return min((pr_stage(p) for p in open_prs), key=stage_rank), None
    if any(p.state == "merged" for p in prs):
        return STAGE_MERGED, None
    return STAGE_PLANNING, OVERLAY_PR_CLOSED


OPEN_TASK_STATES: Final = tuple(s for s in TASK_STATES if s not in TASK_CLOSED_STATES)
"""Task states a project still works on (what :func:`derive_stage` needs of the graph)."""
# Task kinds whose running work checks something already built.
_CHECK_KINDS: Final = frozenset({"test", "ops"})
# ``created_by`` of rows reconcile backfills (``reconcile.CREATED_BY_RECONCILE``):
# their ``created_at`` is the backfill, not when the work began.
_BACKFILLED_BY: Final = "reconcile"


def _task_stage(task: FactoryTask) -> str:
    """The stage one open task implies once its project's PRs are merged."""
    if task.state == "review":
        return STAGE_REVIEW
    if task.state in ("running", "blocked"):
        return STAGE_VERIFY if task.kind in _CHECK_KINDS else STAGE_BUILDING
    return STAGE_PLANNING


def _merge_times(prs: Sequence[FactoryPr]) -> list[int | None]:
    return [p.merged_at or p.closed_at or None for p in prs if p.state == "merged"]


def _claim_after_merge(project: FactoryProject, prs: Sequence[FactoryPr]) -> str | None:
    """:returns: The PL's stage claim when it was made after the project's last merge."""
    claim = project.agent_stage if project.agent_stage in AGENT_STAGES else None
    last = max((t for t in _merge_times(prs) if t is not None), default=None)
    if claim is None or project.agent_stage_at is None or last is None:
        return None
    return claim if project.agent_stage_at > last else None


def work_after_merge(
    project: FactoryProject, prs: Sequence[FactoryPr], tasks: Sequence[FactoryTask] = ()
) -> tuple[str, str] | None:
    """
    The stage of the work a project still does once all its PRs are merged.

    In order: its open tasks (a working-stage claim the PL made after the merge
    names their stage); such a claim alone; a project whose every merge
    predates it (made to validate an already-merged PR) until its PL claims
    ``merged``.

    :param prs: The project's PRs; none open, at least one merged.
    :param tasks: The factory's tasks (other projects' and closed ones are ignored).
    :returns: ``(stage, note)``, or ``None`` when the merge finished the project's work.
    """
    claim = _claim_after_merge(project, prs)
    # "ready" means a PR ready for a human: with every PR merged, not a stage of work.
    working = claim if claim is not None and stage_rank(claim) < _RANK[STAGE_READY] else None
    open_tasks = [
        t for t in tasks if t.project_slug == project.slug and t.state not in TASK_CLOSED_STATES
    ]
    if open_tasks:
        active = [t for t in open_tasks if t.state not in ("planned", "ready")]
        stage = min((_task_stage(t) for t in active), key=stage_rank, default=STAGE_PLANNING)
        n = len(open_tasks)
        return working or stage, f"PR merged · {n} task{'s' if n != 1 else ''} still open"
    if working is not None:
        return working, f"PR merged · PL still at {working}"
    times = _merge_times(prs)
    started = project.created_at if project.created_by != _BACKFILLED_BY else 0
    adopted = bool(started) and all(t is not None and t < started for t in times)
    if adopted and times and claim != STAGE_MERGED:
        refs = [f"!{p.number}" for p in prs if p.state == "merged"]
        verb = "was" if len(refs) == 1 else "were"
        return STAGE_PLANNING, f"{', '.join(refs)} {verb} merged before this project started"
    return None


def _blocking_decision(
    project: FactoryProject,
    pl_id: str | None,
    open_decisions: Sequence[FactoryDecision],
) -> FactoryDecision | None:
    for decision in sorted(open_decisions, key=lambda d: d.number):
        if decision.status != "open" or not decision.blocking:
            continue
        if decision.project_slug == project.slug or (
            pl_id is not None and decision.asked_by_session_id == pl_id
        ):
            return decision
    return None


def derive_stage(
    project: FactoryProject,
    prs: Sequence[FactoryPr],
    pl_conv: Conversation | None,
    open_decisions: Sequence[FactoryDecision],
    *,
    brief_delivered: bool = True,
    sandbox_failed: bool = False,
    waiting_on: Sequence[str] = (),
    tasks: Sequence[FactoryTask] = (),
) -> StageResult:
    """
    Derive a project's stage and overlay from its PRs, PL and open decisions.

    A cancelled or merged project shows that overlay over everything else; a
    queued one without a PL whose dependencies are unmet shows ``waiting``.
    A project whose PRs are all merged but which still works shows that work's
    stage (see :func:`work_after_merge`).

    :param project: The project row (its ``slug``, ``status`` and ``agent_stage`` are read).
    :param prs: The project's PRs, any state.
    :param pl_conv: The project's PL session, or ``None`` before it is spawned.
    :param open_decisions: Open decisions of the factory (others are ignored).
    :param brief_delivered: ``False`` while the PL has not received its brief.
    :param sandbox_failed: The PL's sandbox failed to provision.
    :param waiting_on: The project's unmet dependencies.
    :param tasks: The project's work graph (any state; others' tasks are ignored).
    :returns: The :class:`StageResult`.
    """
    labels: dict[str, str] = dict(pl_conv.labels) if pl_conv is not None else {}
    note: str | None = None
    if pl_conv is None or not brief_delivered:
        stage, overlay = STAGE_QUEUED, None
    elif labels.get(LABEL_STATE) == STATE_DONE:
        stage, overlay = STAGE_DONE, None
    else:
        stage, overlay = _stage_from_prs(prs)
        if stage == STAGE_MERGED and (work := work_after_merge(project, prs, tasks)):
            stage, note = work

    reason = "PR closed without merge" if overlay == OVERLAY_PR_CLOSED else None
    if pl_conv is not None:
        health = labels.get(LABEL_HEALTH)
        blocker = _blocking_decision(project, pl_conv.id, open_decisions)
        if health == HEALTH_DEAD or sandbox_failed:
            overlay = OVERLAY_FAILED
            reason = "PL sandbox failed" if sandbox_failed else "PL dead"
        elif blocker is not None:
            overlay, reason = OVERLAY_BLOCKED, f"Blocked on {blocker.ref}"
        elif health == HEALTH_BLOCKED:
            overlay, reason = OVERLAY_BLOCKED, "PL reports blocked"
        elif labels.get(LABEL_STATE) == STATE_PAUSED:
            overlay, reason = OVERLAY_PAUSED, "Paused"

    status = getattr(project, "status", "active")
    if status == "cancelled":
        overlay, reason = OVERLAY_CANCELLED, "Cancelled"
    elif status == "merged":
        parent = getattr(project, "parent_slug", None)
        overlay, reason = OVERLAY_MERGED, f"merged → {parent}" if parent else "Merged"
    elif status == "done":
        stage = STAGE_DONE
    elif pl_conv is None and waiting_on:
        overlay, reason = OVERLAY_WAITING, f"waiting on {', '.join(waiting_on)}"

    claim = project.agent_stage
    held = any(
        p.state == "open" and changes_requested_on_head(p.human_review, p.head_sha) for p in prs
    )
    mismatch = claim is not None and stage_rank(claim) > stage_rank(stage) and not held
    if note is not None:
        # Past the merge only a claim made since counts ("merged" while tasks are open).
        mismatch = mismatch and _claim_after_merge(project, prs) is not None
    return StageResult(stage, overlay, reason, mismatch, note)


__all__ = [
    "OPEN_TASK_STATES",
    "OVERLAYS",
    "OVERLAY_BLOCKED",
    "OVERLAY_CANCELLED",
    "OVERLAY_FAILED",
    "OVERLAY_MERGED",
    "OVERLAY_PAUSED",
    "OVERLAY_PR_CLOSED",
    "OVERLAY_WAITING",
    "StageResult",
    "changes_requested_on_head",
    "derive_stage",
    "pr_stage",
    "ready_for_human",
    "stage_rank",
    "work_after_merge",
]
