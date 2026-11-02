"""S11: a project that only waits for a human merge gives its dev env back.

The supervisor asks the PL to take the dev env down once every open PR is
ready for a human and nothing else is left (previews stay up), and to bring
it back when a human asks for changes, a gate fails or a task starts.
"""

from __future__ import annotations

from typing import Any

import pytest

from omnigent import factory as F
from omnigent.entities.factory import FactoryPr, FactoryProject, FactoryTask
from omnigent.server.factory import idle_env
from omnigent.server.factory.idle_env import (
    DOWN_KIND,
    NEED_IDLE,
    NEED_WAIT,
    NEED_WORK,
    UP_KIND,
    assess,
    down_notice_text,
    up_notice_text,
)
from omnigent.server.factory.supervisor import FactorySupervisor, SupervisorConfig
from omnigent.server.routes._sessions.common import _session_status_cache
from omnigent.util.session_lifecycle import CLOSED_LABEL_KEY, CLOSED_LABEL_VALUE
from tests.server.factory.conftest import Supervision

SLUG = "api-version"
REPO = "caffeinelabs/app"
HEAD = "7767f0d1c2a94b8e3f50d6a71b2c9e84f03d5a16"
AFTER_S = idle_env.DEFAULT_IDLE_ENV_S
READY_GATES = {
    "draft": "pass",
    "base": "pass",
    "checks": "pass",
    "review": "pass",
    "verify": "pass",
    "mergeable": "pass",
    "protected_paths": "pass",
    "demo": "pass",
}
PREVIEW = {
    "name": "p9630",
    "state": "ready",
    "env_id": "abc-p9630",
    "urls": {"app": "https://app-abc-p9630.envs.sandbox.caffeinelabs.io"},
    "deployed": {"sha": HEAD},
}
DOWN_NOTICE = (
    "[Factory notice · dev env idle · take it down] Project api-version only waits for a "
    "human merge: PR #9630 is ready and no task is open. Send env_keeper DOWN reason=idle. "
    "It takes down the dev env only: keep every preview up for review. The server tells you "
    "when work comes back."
)
UP_NOTICE = (
    "[Factory notice · dev env needed · bring it up] Project api-version has work again: "
    "luka asked for changes on PR #9630. Send env_keeper UP base=temp-main so the IC has "
    "its dev env."
)


# ── assess (pure) ──────────────────────────────────────────────


def _project(**fields: Any) -> FactoryProject:
    base: dict[str, Any] = {
        "id": "1" * 32,
        "factory_id": "f" * 32,
        "slug": SLUG,
        "title": SLUG,
        "base_branch": "temp-main",
    }
    return FactoryProject(**{**base, **fields})


def _pr(number: int = 9630, **fields: Any) -> FactoryPr:
    base: dict[str, Any] = {
        "id": f"{number:032x}",
        "factory_id": "f" * 32,
        "repo": REPO,
        "number": number,
        "url": f"https://github.com/{REPO}/pull/{number}",
        "head_sha": HEAD,
        "project_slug": SLUG,
        "gates": dict(READY_GATES),
        "ready": True,
        "ready_at": 1000 + number,
        "preview": dict(PREVIEW),
    }
    return FactoryPr(**{**base, **fields})


def _task(number: int, state: str, pr: FactoryPr | None = None, **fields: Any) -> FactoryTask:
    base: dict[str, Any] = {
        "id": f"{number:032x}"[::-1],
        "factory_id": "f" * 32,
        "number": number,
        "project_slug": SLUG,
        "key": f"task-{number}",
        "title": f"task {number}",
        "state": state,
        "pr_id": pr.id if pr is not None else None,
    }
    return FactoryTask(**{**base, **fields})


def test_every_pr_ready_and_nothing_left_is_idle() -> None:
    first, second = _pr(9630), _pr(9631)
    tasks = [_task(1, "review", first), _task(2, "review", second), _task(3, "done")]

    need = assess(_project(), [second, first], tasks)

    assert need.need == NEED_IDLE
    assert need.prs == (9630, 9631)
    # Idle since the last PR turned ready.
    assert need.since == second.ready_at


@pytest.mark.parametrize(
    ("prs", "tasks", "busy", "reason"),
    [
        ([], [], (), "no open PR"),
        (
            [_pr(), _pr(9631, ready=False, gates={**READY_GATES, "checks": "pending"})],
            [],
            (),
            "PR #9631 is not ready yet",
        ),
        ([_pr()], [_task(4, "planned")], (), "task T-4 task-4 is planned"),
        ([_pr()], [_task(4, "blocked")], (), "task T-4 task-4 is blocked"),
        # A task in review whose PR is not one of the ready ones still has work to show.
        ([_pr()], [_task(4, "review")], (), "task T-4 task-4 is review"),
        ([_pr()], [], ("ic:docs-fix",), "ic:docs-fix is running"),
        ([_pr(state="merged")], [], (), "no open PR"),
    ],
)
def test_anything_short_of_idle_waits(
    prs: list[FactoryPr], tasks: list[FactoryTask], busy: tuple[str, ...], reason: str
) -> None:
    need = assess(_project(), prs, tasks, busy=busy)

    assert (need.need, need.reason) == (NEED_WAIT, reason)


@pytest.mark.parametrize(
    ("pr", "tasks", "reason"),
    [
        (
            _pr(
                ready=False,
                human_review={"verdict": "changes_requested", "by": "luka", "head_sha": HEAD},
            ),
            [],
            "luka asked for changes on PR #9630",
        ),
        (
            _pr(ready=False, gates={**READY_GATES, "checks": "fail", "verify": "fail"}),
            [],
            "PR #9630 failed checks, verify",
        ),
        (
            _pr(ready=False, gates={**READY_GATES, "draft": "fail"}),
            [],
            "PR #9630 is a draft again",
        ),
        (_pr(), [_task(5, "ready")], "task T-5 task-5 is ready"),
        (_pr(), [_task(5, "running")], "task T-5 task-5 is running"),
    ],
)
def test_change_requests_failed_gates_and_tasks_are_work(
    pr: FactoryPr, tasks: list[FactoryTask], reason: str
) -> None:
    need = assess(_project(), [pr], tasks)

    assert (need.need, need.reason) == (NEED_WORK, reason)


def test_a_reworked_task_still_running_on_its_ready_pr_is_idle() -> None:
    """A re-dispatch moves its task to running, and nothing moves it back once the PR is ready."""
    pr = _pr()

    need = assess(_project(), [pr], [_task(1, "running", pr)])

    assert need.need == NEED_IDLE
    assert need.reason == "PR #9630 only waits for a human merge"


def test_a_running_task_on_a_pr_that_is_not_ready_is_work() -> None:
    pr = _pr(ready=False, gates={**READY_GATES, "checks": "pending"})

    need = assess(_project(), [pr], [_task(1, "running", pr)])

    assert (need.need, need.reason) == (NEED_WORK, "task T-1 task-1 is running")


def test_a_change_request_on_an_older_head_is_not_work() -> None:
    held = {"verdict": "changes_requested", "by": "luka", "head_sha": "0" * 40}

    assert assess(_project(), [_pr(human_review=held)], []).need == NEED_IDLE


def test_other_projects_are_ignored() -> None:
    theirs = _pr(9700, project_slug="other", ready=False, gates={"checks": "fail"})
    their_task = _task(9, "running", project_slug="other")

    assert assess(_project(), [_pr(), theirs], [their_task]).need == NEED_IDLE


def test_notice_texts() -> None:
    project = _project()
    idle = assess(project, [_pr()], [])
    work = assess(
        project,
        [_pr(human_review={"verdict": "changes_requested", "by": "luka", "head_sha": HEAD})],
        [],
    )

    assert down_notice_text(project, idle) == DOWN_NOTICE
    assert up_notice_text(project, work) == UP_NOTICE
    two = assess(project, [_pr(9630), _pr(9631)], [])
    assert "PRs #9630 and #9631 are ready" in down_notice_text(project, two)


@pytest.mark.parametrize(
    ("raw", "expected"), [(None, 900.0), ("1800", 1800.0), ("0", None), ("soon", 900.0)]
)
def test_idle_env_reads_the_environment(
    monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: float | None
) -> None:
    if raw is None:
        monkeypatch.delenv(idle_env.IDLE_ENV_ENV, raising=False)
    else:
        monkeypatch.setenv(idle_env.IDLE_ENV_ENV, raw)

    assert idle_env.idle_env_s() == expected
    assert SupervisorConfig.from_env().idle_env_s == expected


# ── The supervisor ─────────────────────────────────────────────


@pytest.fixture()
def quiet(supervision: Supervision) -> Supervision:
    """
    The supervision world with heartbeats off and the PL's team down to its IC.

    The IC (``ic:task-1``) implements T-1 ``task-1``, whose PR :func:`_ready_pr` opens.
    """
    world = supervision.world
    for key in ("lead_a", "pl"):
        world.store.set_labels(world.id(key), {F.LABEL_HEARTBEAT_S: "0"})
    for key in ("reviewer", "helper"):
        world.store.set_labels(world.id(key), {CLOSED_LABEL_KEY: CLOSED_LABEL_VALUE})
    return supervision


def _store(s: Supervision) -> Any:
    return s.world.app_state.factory.factory_store


def _fid(s: Supervision) -> str:
    return s.world.id("lead_a")


def _report_env(s: Supervision, state: str) -> None:
    _store(s).upsert_project(
        _fid(s),
        SLUG,
        base_branch="temp-main",
        pl_session_id=s.world.id("pl"),
        env={"reported": {"state": state, "env_id": "abc", "urls": {}, "at": 1}},
    )


def _ready_pr(s: Supervision, ready_at: float, number: int = 9630) -> Any:
    """An open PR ready for a human merge, with a live preview, and its task in review."""
    pr, _ = _store(s).upsert_pr(
        _fid(s),
        REPO,
        number,
        project_slug=SLUG,
        head_sha=HEAD,
        gates=dict(READY_GATES),
        ready=True,
        ready_at=int(ready_at),
        preview=dict(PREVIEW),
    )
    _store(s).upsert_task(_fid(s), SLUG, "task-1", state="review", pr_id=pr.id)
    return pr


def _notices(s: Supervision) -> list[str]:
    return [t for t in s.deliver.texts_to(s.world.id("pl")) if "dev env" in t.split("]", 1)[0]]


async def test_ready_for_human_merge_takes_the_dev_env_down_once(quiet: Supervision) -> None:
    s, clock = quiet, quiet.clock
    _report_env(s, "ready")
    pr = _ready_pr(s, clock.now)

    clock.advance(AFTER_S - 60)
    await s.tick()
    assert _notices(s) == []

    clock.advance(60)
    await s.tick()
    assert _notices(s) == [DOWN_NOTICE]

    # Later ticks stay quiet, also after a server roll.
    clock.advance(AFTER_S)
    await s.tick()
    restarted = FactorySupervisor(
        s.world.app_state, config=SupervisorConfig(), deliver=s.deliver, clock=clock
    )
    await restarted.tick()
    await s.settle()
    assert _notices(s) == [DOWN_NOTICE]

    [event] = _store(s).find_events(_fid(s), DOWN_KIND, 0)
    assert event.to_id == s.world.id("pl") and event.subject == "env:api-version"
    assert event.project_slug == SLUG and event.severity == "notice"
    assert event.data["prs"] == [9630]
    # Previews untouched: the server only asked; the PR's preview row is as it was.
    assert _store(s).get_pr(pr.id).preview == pr.preview


async def test_a_change_request_brings_the_dev_env_back(quiet: Supervision) -> None:
    s, clock = quiet, quiet.clock
    _report_env(s, "ready")
    pr = _ready_pr(s, clock.now)
    clock.advance(AFTER_S)
    await s.tick()
    assert _notices(s) == [DOWN_NOTICE]
    _report_env(s, "down")  # env_keeper DOWN reason=idle

    # Still only waiting: nothing to bring back.
    clock.advance(AFTER_S)
    await s.tick()
    assert _notices(s) == [DOWN_NOTICE]

    _store(s).update_pr(
        pr.id,
        ready=False,
        human_review={"verdict": "changes_requested", "by": "luka", "head_sha": HEAD, "at": 1},
    )
    clock.advance(60)
    await s.tick()
    assert _notices(s) == [DOWN_NOTICE, UP_NOTICE]

    # One ask per stretch, while env_keeper brings it up.
    clock.advance(60)
    await s.tick()
    assert _notices(s) == [DOWN_NOTICE, UP_NOTICE]
    [up] = _store(s).find_events(_fid(s), UP_KIND, 0)
    assert up.data["reason"] == "luka asked for changes on PR #9630"
    assert _store(s).get_pr(pr.id).preview == pr.preview


async def test_a_new_task_brings_the_dev_env_back(quiet: Supervision) -> None:
    s, clock = quiet, quiet.clock
    _report_env(s, "ready")
    _ready_pr(s, clock.now)
    clock.advance(AFTER_S)
    await s.tick()
    _report_env(s, "down")

    _store(s).upsert_task(_fid(s), SLUG, "badge", title="ENV badge", state="ready")
    clock.advance(60)
    await s.tick()

    [_, up] = _notices(s)
    assert up.startswith("[Factory notice · dev env needed · bring it up] Project api-version")
    assert "task T-2 badge is ready" in up and "UP base=temp-main" in up


async def test_idle_again_after_the_work_asks_again(quiet: Supervision) -> None:
    s, clock = quiet, quiet.clock
    _report_env(s, "ready")
    pr = _ready_pr(s, clock.now)
    clock.advance(AFTER_S)
    await s.tick()
    _report_env(s, "down")
    task, _ = _store(s).upsert_task(_fid(s), SLUG, "badge", state="ready")
    clock.advance(60)
    await s.tick()
    assert len(_notices(s)) == 2

    # The task is dropped before any new head: the PR is still ready since long ago,
    # but the env is coming back, so the grace counts from the ask to bring it up.
    _store(s).update_task(task.id, state="cancelled")
    _report_env(s, "starting")
    clock.advance(60)
    await s.tick()
    assert len(_notices(s)) == 2

    clock.advance(AFTER_S)
    await s.tick()
    assert _notices(s)[2:] == [DOWN_NOTICE]
    assert _store(s).get_pr(pr.id).preview == pr.preview


async def test_a_dev_env_the_server_did_not_take_down_stays_down(quiet: Supervision) -> None:
    s, clock = quiet, quiet.clock
    _report_env(s, "down")
    _store(s).upsert_task(_fid(s), SLUG, "badge", state="ready")

    clock.advance(AFTER_S)
    await s.tick()

    assert _notices(s) == []


@pytest.mark.parametrize("pl_state", [F.STATE_PAUSED, F.STATE_DONE])
async def test_a_paused_or_done_pl_is_left_alone(quiet: Supervision, pl_state: str) -> None:
    s, clock = quiet, quiet.clock
    s.world.store.set_labels(s.world.id("pl"), {F.LABEL_STATE: pl_state})
    _report_env(s, "ready")
    _ready_pr(s, clock.now)

    clock.advance(10 * AFTER_S)
    await s.tick()

    assert _notices(s) == []


async def test_the_integration_env_is_never_taken_down(quiet: Supervision) -> None:
    s, clock = quiet, quiet.clock
    _report_env(s, "ready")
    _store(s).upsert_project(_fid(s), SLUG, charter={"kind": "integration"})
    _ready_pr(s, clock.now)

    clock.advance(10 * AFTER_S)
    await s.tick()

    assert _notices(s) == []


async def test_a_running_ic_keeps_the_dev_env(
    quiet: Supervision, monkeypatch: pytest.MonkeyPatch
) -> None:
    s, clock = quiet, quiet.clock
    _report_env(s, "ready")
    _ready_pr(s, clock.now)
    monkeypatch.setitem(_session_status_cache, s.world.id("ic"), "running")

    clock.advance(10 * AFTER_S)
    await s.tick()

    assert _notices(s) == []


async def test_off_without_a_wait(quiet: Supervision) -> None:
    s, clock = quiet, quiet.clock
    _report_env(s, "ready")
    _ready_pr(s, clock.now)
    off = FactorySupervisor(
        s.world.app_state,
        config=SupervisorConfig(idle_env_s=None),
        deliver=s.deliver,
        clock=clock,
    )

    clock.advance(10 * AFTER_S)
    await off.tick()
    await s.settle()

    assert _notices(s) == []
