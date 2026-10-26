"""Server-derived project stage: the cockpit's headline, computed from gates.

Pure functions. The agent's own claim (``agent_stage``) is never the headline;
it is compared against the derived stage to flag "claim ≠ gates".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import (
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
)

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactoryDecision, FactoryPr, FactoryProject

OVERLAY_PAUSED: Final = "paused"
OVERLAY_BLOCKED: Final = "blocked"
OVERLAY_FAILED: Final = "failed"
OVERLAY_PR_CLOSED: Final = "pr_closed"
OVERLAYS: Final = frozenset({OVERLAY_PAUSED, OVERLAY_BLOCKED, OVERLAY_FAILED, OVERLAY_PR_CLOSED})

# Gate values that let a PR move past a gate (skip = not applicable).
_PASSING: Final = frozenset({"pass", "skip"})
_RANK: Final = {stage: i for i, stage in enumerate(STAGES)}


@dataclass(frozen=True)
class StageResult:
    """
    The derived stage of one project.

    :param stage: One of :data:`omnigent.factory.STAGES`.
    :param overlay: ``paused`` / ``blocked`` / ``failed`` / ``pr_closed``, or ``None``.
    :param overlay_reason: Human text for the overlay, e.g. ``"Blocked on D-3"``.
    :param claim_mismatch: The PL claims a stage further along than the gates show.
    """

    stage: str
    overlay: str | None = None
    overlay_reason: str | None = None
    claim_mismatch: bool = False


def stage_rank(stage: str | None) -> int:
    """:returns: The pipeline position of *stage* (-1 when unknown)."""
    return _RANK.get(stage or "", -1)


def _gate(pr: FactoryPr, name: str) -> str:
    value = (pr.gates or {}).get(name)
    return value if isinstance(value, str) else "unknown"


def _changes_requested_on_head(pr: FactoryPr) -> bool:
    review: dict[str, Any] = pr.human_review or {}
    if review.get("verdict") != "changes_requested":
        return False
    sha = review.get("head_sha")
    return not sha or not pr.head_sha or sha == pr.head_sha


def pr_stage(pr: FactoryPr) -> str:
    """
    The stage one open PR implies.

    Draft, checks not passing, or a human change request on the current head
    is building; review not passing is review; anything else short of ready
    (verify pending/failed/skipped, mergeable, protected paths) is verify.
    """
    if pr.ready:
        return STAGE_READY
    if (
        pr.is_draft
        or _gate(pr, "draft") not in _PASSING
        or _gate(pr, "checks") not in _PASSING
        or _changes_requested_on_head(pr)
    ):
        return STAGE_BUILDING
    if _gate(pr, "review") not in _PASSING:
        return STAGE_REVIEW
    return STAGE_VERIFY


def _stage_from_prs(prs: Sequence[FactoryPr]) -> tuple[str, str | None]:
    if not prs:
        return STAGE_PLANNING, None
    open_prs = [p for p in prs if p.state == "open"]
    if any(p.ready for p in open_prs):
        return STAGE_READY, None
    if open_prs:
        return min((pr_stage(p) for p in open_prs), key=stage_rank), None
    if any(p.state == "merged" for p in prs):
        return STAGE_MERGED, None
    return STAGE_PLANNING, OVERLAY_PR_CLOSED


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
) -> StageResult:
    """
    Derive a project's stage and overlay from its PRs, PL and open decisions.

    :param project: The project row (its ``slug`` and ``agent_stage`` are read).
    :param prs: The project's PRs, any state.
    :param pl_conv: The project's PL session, or ``None`` before it is spawned.
    :param open_decisions: Open decisions of the factory (others are ignored).
    :param brief_delivered: ``False`` while the PL has not received its brief.
    :param sandbox_failed: The PL's sandbox failed to provision.
    :returns: The :class:`StageResult`.
    """
    labels: dict[str, str] = dict(pl_conv.labels) if pl_conv is not None else {}
    if pl_conv is None or not brief_delivered:
        stage, overlay = STAGE_QUEUED, None
    elif labels.get(LABEL_STATE) == STATE_DONE:
        stage, overlay = STAGE_DONE, None
    else:
        stage, overlay = _stage_from_prs(prs)

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

    claim = project.agent_stage
    mismatch = claim is not None and stage_rank(claim) > stage_rank(stage)
    return StageResult(stage, overlay, reason, mismatch)


__all__ = [
    "OVERLAYS",
    "OVERLAY_BLOCKED",
    "OVERLAY_FAILED",
    "OVERLAY_PAUSED",
    "OVERLAY_PR_CLOSED",
    "StageResult",
    "derive_stage",
    "pr_stage",
    "stage_rank",
]
