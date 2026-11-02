"""Table tests for the server-derived project stage (``stage.derive_stage``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from omnigent.entities.factory import FactoryDecision, FactoryPr, FactoryProject, FactoryTask
from omnigent.factory import LABEL_HEALTH, LABEL_STATE, STAGES
from omnigent.server.factory.stage import (
    OVERLAYS,
    StageResult,
    changes_requested_on_head,
    derive_stage,
    pr_stage,
    ready_for_human,
    work_after_merge,
)

FID = "f" * 32
PL_ID = "a" * 32
OTHER_ID = "b" * 32
HEAD = "1" * 40
OLD = "2" * 40

_ALL_PASS = dict.fromkeys(
    ("draft", "checks", "review", "verify", "mergeable", "protected_paths"), "pass"
)


@dataclass
class _Conv:
    """The two fields ``derive_stage`` reads from a PL session row."""

    id: str = PL_ID
    labels: dict[str, str] = field(default_factory=lambda: {LABEL_STATE: "active"})


def _pl(**labels: str) -> _Conv:
    return _Conv(labels={LABEL_STATE: "active", **labels})


def _project(
    agent_stage: str | None = None, status: str = "active", parent_slug: str | None = None
) -> FactoryProject:
    return FactoryProject(
        id="c" * 32,
        factory_id=FID,
        slug="api",
        title="API",
        agent_stage=agent_stage,
        status=status,
        parent_slug=parent_slug,
    )


_n = iter(range(1, 10_000))


def _pr(
    state: str = "open", *, ready: bool = False, is_draft: bool = False, **gates: Any
) -> FactoryPr:
    review = gates.pop("human_review", None)
    return FactoryPr(
        id=f"{next(_n):032x}",
        factory_id=FID,
        repo="caffeinelabs/app",
        number=next(_n),
        url="https://github.com/caffeinelabs/app/pull/1",
        head_sha=HEAD,
        state=state,
        is_draft=is_draft,
        ready=ready,
        gates={**_ALL_PASS, **gates},
        human_review=review,
    )


def _decision(
    number: int = 4,
    *,
    blocking: bool = True,
    project: str | None = "api",
    asked_by: str | None = None,
    status: str = "open",
) -> FactoryDecision:
    return FactoryDecision(
        id=f"{number:032x}",
        factory_id=FID,
        number=number,
        question="?",
        status=status,
        blocking=blocking,
        project_slug=project,
        asked_by_session_id=asked_by,
    )


_CHANGES_ON_HEAD = {"verdict": "changes_requested", "head_sha": HEAD, "by": "luka", "at": 1}
_CHANGES_ON_OLD = {"verdict": "changes_requested", "head_sha": OLD, "by": "luka", "at": 1}

# (id, prs, pl, decisions, kwargs, agent_stage, expected)
CASES: list[
    tuple[str, list[FactoryPr], _Conv | None, list[FactoryDecision], dict, str | None, StageResult]
] = [
    ("no PL yet", [], None, [], {}, None, StageResult("queued")),
    (
        "brief not delivered",
        [],
        _pl(),
        [],
        {"brief_delivered": False},
        None,
        StageResult("queued"),
    ),
    ("PL done", [_pr("merged")], _pl(**{LABEL_STATE: "done"}), [], {}, None, StageResult("done")),
    ("no PRs", [], _pl(), [], {}, None, StageResult("planning")),
    ("open ready PR", [_pr(ready=True)], _pl(), [], {}, None, StageResult("ready")),
    ("draft PR", [_pr(is_draft=True)], _pl(), [], {}, None, StageResult("building")),
    ("draft gate pending", [_pr(draft="pending")], _pl(), [], {}, None, StageResult("building")),
    ("checks pending", [_pr(checks="pending")], _pl(), [], {}, None, StageResult("building")),
    ("checks failing", [_pr(checks="fail")], _pl(), [], {}, None, StageResult("building")),
    (
        "gates unknown",
        [_pr(checks="unknown", review="unknown")],
        _pl(),
        [],
        {},
        None,
        StageResult("building"),
    ),
    ("review pending", [_pr(review="pending")], _pl(), [], {}, None, StageResult("review")),
    ("review changes", [_pr(review="fail")], _pl(), [], {}, None, StageResult("review")),
    ("verify pending", [_pr(verify="pending")], _pl(), [], {}, None, StageResult("verify")),
    ("verify failed", [_pr(verify="fail")], _pl(), [], {}, None, StageResult("verify")),
    (
        "verify skipped, not ready",
        [_pr(verify="skip", mergeable="pending")],
        _pl(),
        [],
        {},
        None,
        StageResult("verify"),
    ),
    (
        "human changes on head",
        [_pr(human_review=_CHANGES_ON_HEAD)],
        _pl(),
        [],
        {},
        None,
        StageResult("building"),
    ),
    (
        "human changes on old head",
        [_pr(review="pending", human_review=_CHANGES_ON_OLD)],
        _pl(),
        [],
        {},
        None,
        StageResult("review"),
    ),
    (
        "human changes on a ready head (stored ready not yet cleared)",
        [_pr(ready=True, human_review=_CHANGES_ON_HEAD)],
        _pl(),
        [],
        {},
        "ready",
        # The PL's READY claim predates the human's request: not its mismatch.
        StageResult("building"),
    ),
    (
        "PL still claims ready right after a human change request",
        [_pr(human_review=_CHANGES_ON_HEAD)],
        _pl(),
        [],
        {},
        "ready",
        StageResult("building"),
    ),
    (
        "claim ahead of gates on a new head after human changes",
        [_pr(checks="pending", human_review=_CHANGES_ON_OLD)],
        _pl(),
        [],
        {},
        "ready",
        StageResult("building", claim_mismatch=True),
    ),
    (
        "ready on a new head after human changes",
        [_pr(ready=True, human_review=_CHANGES_ON_OLD)],
        _pl(),
        [],
        {},
        None,
        StageResult("ready"),
    ),
    (
        "ready wins over building",
        [_pr(is_draft=True), _pr(ready=True)],
        _pl(),
        [],
        {},
        None,
        StageResult("ready"),
    ),
    (
        "furthest behind decides",
        [_pr(verify="pending"), _pr(checks="pending")],
        _pl(),
        [],
        {},
        None,
        StageResult("building"),
    ),
    (
        "open beats merged",
        [_pr("merged"), _pr(verify="pending")],
        _pl(),
        [],
        {},
        None,
        StageResult("verify"),
    ),
    ("only merged", [_pr("merged")], _pl(), [], {}, None, StageResult("merged")),
    (
        "merged and closed",
        [_pr("closed"), _pr("merged")],
        _pl(),
        [],
        {},
        None,
        StageResult("merged"),
    ),
    (
        "only closed",
        [_pr("closed")],
        _pl(),
        [],
        {},
        None,
        StageResult("planning", "pr_closed", "PR closed without merge"),
    ),
    (
        "paused",
        [_pr(review="pending")],
        _pl(**{LABEL_STATE: "paused"}),
        [],
        {},
        None,
        StageResult("review", "paused", "Paused"),
    ),
    (
        "blocking decision for project",
        [],
        _pl(),
        [_decision(4)],
        {},
        None,
        StageResult("planning", "blocked", "Blocked on D-4"),
    ),
    (
        "blocking decision asked by PL",
        [],
        _pl(),
        [_decision(5, project=None, asked_by=PL_ID)],
        {},
        None,
        StageResult("planning", "blocked", "Blocked on D-5"),
    ),
    (
        "lowest blocking decision named",
        [],
        _pl(),
        [_decision(9), _decision(6)],
        {},
        None,
        StageResult("planning", "blocked", "Blocked on D-6"),
    ),
    (
        "non-blocking decision",
        [],
        _pl(),
        [_decision(blocking=False)],
        {},
        None,
        StageResult("planning"),
    ),
    (
        "other project's decision",
        [],
        _pl(),
        [_decision(project="ux", asked_by=OTHER_ID)],
        {},
        None,
        StageResult("planning"),
    ),
    (
        "answered decision ignored",
        [],
        _pl(),
        [_decision(status="answered")],
        {},
        None,
        StageResult("planning"),
    ),
    (
        "health blocked",
        [],
        _pl(**{LABEL_HEALTH: "blocked"}),
        [],
        {},
        None,
        StageResult("planning", "blocked", "PL reports blocked"),
    ),
    (
        "health dead",
        [_pr(ready=True)],
        _pl(**{LABEL_HEALTH: "dead"}),
        [_decision()],
        {},
        None,
        StageResult("ready", "failed", "PL dead"),
    ),
    (
        "sandbox failed while queued",
        [],
        _pl(),
        [],
        {"brief_delivered": False, "sandbox_failed": True},
        None,
        StageResult("queued", "failed", "PL sandbox failed"),
    ),
    (
        "blocked beats paused",
        [],
        _pl(**{LABEL_STATE: "paused"}),
        [_decision(7)],
        {},
        None,
        StageResult("planning", "blocked", "Blocked on D-7"),
    ),
    (
        "failed beats closed-only",
        [_pr("closed")],
        _pl(**{LABEL_HEALTH: "dead"}),
        [],
        {},
        None,
        StageResult("planning", "failed", "PL dead"),
    ),
    (
        "claim ahead of gates",
        [_pr(checks="pending")],
        _pl(),
        [],
        {},
        "ready",
        StageResult("building", claim_mismatch=True),
    ),
    (
        "claim behind gates",
        [_pr(verify="pending")],
        _pl(),
        [],
        {},
        "review",
        StageResult("verify"),
    ),
    ("claim equals gates", [_pr(ready=True)], _pl(), [], {}, "ready", StageResult("ready")),
    (
        "claim merged, done",
        [_pr("merged")],
        _pl(**{LABEL_STATE: "done"}),
        [],
        {},
        "merged",
        StageResult("done"),
    ),
    (
        "claim while queued",
        [],
        None,
        [],
        {},
        "planning",
        StageResult("queued", claim_mismatch=True),
    ),
    (
        "held on its dependencies",
        [],
        None,
        [],
        {"waiting_on": ["db-version", "api-version"]},
        None,
        StageResult("queued", "waiting", "waiting on db-version, api-version"),
    ),
    (
        "dependencies listed but the PL runs",
        [],
        _pl(),
        [],
        {"waiting_on": ["db-version"]},
        None,
        StageResult("planning"),
    ),
    (
        "cancelled wins over everything",
        [_pr(checks="fail")],
        _pl(**{LABEL_HEALTH: "dead"}),
        [],
        {"status": "cancelled"},
        None,
        StageResult("building", "cancelled", "Cancelled"),
    ),
    (
        "merged into another project",
        [],
        None,
        [],
        {"status": "merged", "parent_slug": "api-v2"},
        None,
        StageResult("queued", "merged", "merged → api-v2"),
    ),
]


@pytest.mark.parametrize(
    ("prs", "pl", "decisions", "kwargs", "agent_stage", "expected"),
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_derive_stage(
    prs: list[FactoryPr],
    pl: _Conv | None,
    decisions: list[FactoryDecision],
    kwargs: dict,
    agent_stage: str | None,
    expected: StageResult,
) -> None:
    kwargs = dict(kwargs)
    project = _project(
        agent_stage, kwargs.pop("status", "active"), kwargs.pop("parent_slug", None)
    )
    result = derive_stage(project, prs, pl, decisions, **kwargs)  # type: ignore[arg-type]
    assert result == expected


def test_table_covers_every_stage_and_overlay() -> None:
    assert len(CASES) >= 20
    assert {case[-1].stage for case in CASES} == set(STAGES)
    assert {case[-1].overlay for case in CASES} - {None} == set(OVERLAYS)
    assert any(case[-1].claim_mismatch for case in CASES)


def test_pr_stage_is_ready_for_a_ready_pr_even_with_stale_gates() -> None:
    assert pr_stage(_pr(ready=True, checks="pending")) == "ready"


@pytest.mark.parametrize(
    ("review", "head", "held"),
    [
        (None, HEAD, False),
        ({"verdict": "approved", "head_sha": HEAD}, HEAD, False),
        (_CHANGES_ON_HEAD, HEAD, True),
        (_CHANGES_ON_HEAD, HEAD.upper(), True),
        (_CHANGES_ON_OLD, HEAD, False),
        ({"verdict": "changes_requested", "head_sha": None}, HEAD, True),
        (_CHANGES_ON_HEAD, None, True),
        ("changes_requested", HEAD, False),
    ],
)
def test_a_change_request_holds_only_the_head_it_was_made_on(
    review: object, head: str | None, held: bool
) -> None:
    assert changes_requested_on_head(review, head) is held  # type: ignore[arg-type]


def test_a_held_head_is_not_ready_for_a_human() -> None:
    held = _pr(ready=True, human_review=_CHANGES_ON_HEAD)
    assert ready_for_human(held) is False
    assert pr_stage(held) == "building"
    assert ready_for_human(_pr(ready=True, human_review=_CHANGES_ON_OLD)) is True
    assert ready_for_human(_pr(human_review=_CHANGES_ON_OLD)) is False


# ── After the merge: work the PRs no longer show ─────────────────

T0 = 1_793_000_000  # the project's own PR merges here
MERGED_9774 = FactoryPr(
    id="9" * 32,
    factory_id=FID,
    repo="caffeinelabs/app",
    number=9774,
    url="https://github.com/caffeinelabs/app/pull/9774",
    state="merged",
    merged_at=T0,
    closed_at=T0,
)


def _later_project(
    *,
    created_at: int = T0 - 3600,
    agent_stage: str | None = None,
    agent_stage_at: int | None = None,
    created_by: str = "atlas",
) -> FactoryProject:
    return FactoryProject(
        id="c" * 32,
        factory_id=FID,
        slug="api",
        title="API",
        created_at=created_at,
        created_by=created_by,
        agent_stage=agent_stage,
        agent_stage_at=agent_stage_at,
    )


def _task(key: str, state: str, kind: str = "implement", slug: str = "api") -> FactoryTask:
    return FactoryTask(
        id=key.ljust(32, "0")[:32],
        factory_id=FID,
        number=1,
        project_slug=slug,
        key=key,
        title=key,
        kind=kind,
        state=state,
    )


# (id, project, tasks, expected)
AFTER_MERGE: list[tuple[str, FactoryProject, list[FactoryTask], StageResult]] = [
    ("merged, nothing left", _later_project(), [], StageResult("merged")),
    (
        "PL claimed ready before the merge",
        _later_project(agent_stage="ready", agent_stage_at=T0 - 60),
        [],
        StageResult("merged"),
    ),
    (
        "PL claimed merged after the merge",
        _later_project(agent_stage="merged", agent_stage_at=T0 + 60),
        [],
        StageResult("merged"),
    ),
    (
        "only closed tasks",
        _later_project(),
        [_task("route", "done"), _task("docs", "cancelled"), _task("x", "failed")],
        StageResult("merged"),
    ),
    (
        "another project's open task",
        _later_project(),
        [_task("route", "running", slug="ux")],
        StageResult("merged"),
    ),
    (
        "a planned task left",
        _later_project(),
        [_task("docs", "planned")],
        StageResult("planning", note="PR merged · 1 task still open"),
    ),
    (
        "an implement task running",
        _later_project(),
        [_task("follow-up", "running"), _task("docs", "planned")],
        StageResult("building", note="PR merged · 2 tasks still open"),
    ),
    (
        "a validation task running",
        _later_project(),
        [_task("measure", "running", kind="test")],
        StageResult("verify", note="PR merged · 1 task still open"),
    ),
    (
        "a task out for review",
        _later_project(),
        [_task("follow-up", "review")],
        StageResult("review", note="PR merged · 1 task still open"),
    ),
    (
        "open tasks and a working claim since the merge",
        _later_project(agent_stage="verify", agent_stage_at=T0 + 60),
        [_task("docs", "planned")],
        StageResult("verify", note="PR merged · 1 task still open"),
    ),
    (
        "open tasks while the PL claims merged",
        _later_project(agent_stage="merged", agent_stage_at=T0 + 60),
        [_task("docs", "planned")],
        StageResult("planning", note="PR merged · 1 task still open", claim_mismatch=True),
    ),
    (
        "open tasks and a stale ready claim",
        _later_project(agent_stage="ready", agent_stage_at=T0 - 60),
        [_task("docs", "planned")],
        StageResult("planning", note="PR merged · 1 task still open"),
    ),
    (
        "a ready claim after the merge: its report is ready",
        _later_project(agent_stage="ready", agent_stage_at=T0 + 60),
        [],
        StageResult("ready", note="PR merged · the PL says its work is ready for you"),
    ),
    (
        "PL claims building after the merge",
        _later_project(agent_stage="building", agent_stage_at=T0 + 60),
        [],
        StageResult("building", note="PR merged · the PL says it is still building"),
    ),
    # Every PL claims building at kickoff: a running task outranks that claim.
    (
        "the kickoff claim does not hide a running check",
        _later_project(agent_stage="building", agent_stage_at=T0 + 60),
        [_task("measure", "running", kind="ops")],
        StageResult("verify", note="PR merged · 1 task still open"),
    ),
    (
        "a ready claim while an implement task runs",
        _later_project(agent_stage="ready", agent_stage_at=T0 + 60),
        [_task("follow-up", "running")],
        StageResult("building", note="PR merged · 1 task still open", claim_mismatch=True),
    ),
    # validate-build-tier: made to validate app#9774 after Luka merged it.
    (
        "made to validate an already-merged PR",
        _later_project(created_at=T0 + 600),
        [],
        StageResult("verify", note="!9774 was merged before this project started"),
    ),
    (
        "validating an already-merged PR, PL at verify",
        _later_project(created_at=T0 + 600, agent_stage="verify", agent_stage_at=T0 + 900),
        [],
        StageResult("verify", note="PR merged · the PL says it is verifying"),
    ),
    (
        "validating an already-merged PR, a test task running",
        _later_project(created_at=T0 + 600),
        [_task("recipe", "running", kind="test")],
        StageResult("verify", note="PR merged · 1 task still open"),
    ),
    # The live validate-build-tier: made after app#9774 merged, kickoff claim, a test running.
    (
        "validating an already-merged PR: kickoff claim and a test task running",
        _later_project(created_at=T0 + 600, agent_stage="building", agent_stage_at=T0 + 660),
        [_task("recipe", "running", kind="test")],
        StageResult("verify", note="PR merged · 1 task still open"),
    ),
    (
        "validation report handed over: the PL claims ready",
        _later_project(created_at=T0 + 600, agent_stage="ready", agent_stage_at=T0 + 3600),
        [],
        StageResult("ready", note="PR merged · the PL says its work is ready for you"),
    ),
    (
        "validation done: the PL claims merged",
        _later_project(created_at=T0 + 600, agent_stage="merged", agent_stage_at=T0 + 3600),
        [],
        StageResult("merged"),
    ),
    (
        "a row reconcile backfilled after the merge",
        _later_project(created_at=T0 + 600, created_by="reconcile"),
        [],
        StageResult("merged"),
    ),
]


@pytest.mark.parametrize(
    ("project", "tasks", "expected"),
    [case[1:] for case in AFTER_MERGE],
    ids=[case[0] for case in AFTER_MERGE],
)
def test_a_merged_project_that_still_works_shows_that_work(
    project: FactoryProject, tasks: list[FactoryTask], expected: StageResult
) -> None:
    result = derive_stage(project, [MERGED_9774], _pl(), [], tasks=tasks)  # type: ignore[arg-type]
    assert result == expected


def test_without_a_merge_time_only_open_tasks_count() -> None:
    unknown = FactoryPr(id="8" * 32, factory_id=FID, repo="r/r", number=7, url="u", state="merged")
    claimed = _later_project(created_at=T0, agent_stage="building", agent_stage_at=T0)
    assert work_after_merge(claimed, [unknown]) is None
    assert work_after_merge(claimed, [unknown], [_task("docs", "running")]) == (
        "building",
        "PR merged · 1 task still open",
    )


def test_a_done_pl_is_done_even_with_open_tasks() -> None:
    result = derive_stage(
        _later_project(created_at=T0 + 600),
        [MERGED_9774],
        _pl(**{LABEL_STATE: "done"}),  # type: ignore[arg-type]
        [],
        tasks=[_task("docs", "planned")],
    )
    assert result == StageResult("done")
