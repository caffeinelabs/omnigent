"""The PR pipeline as a graph: the next step per head, NEXT hints and READY waves."""

from __future__ import annotations

import itertools
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.entities.factory import FactoryLoop, FactoryPr
from omnigent.server.factory import loops, pipeline, tasks
from omnigent.server.factory.actor import Actor
from omnigent.server.factory.pipeline import Step, hint_text, next_step
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit

HEAD = "5e1c2d4" + "a" * 33
OLD = "1" * 40
SLUG = "api-version"
REPO = "caffeinelabs/app"


def _pr(**fields: Any) -> FactoryPr:
    base: dict[str, Any] = {
        "id": "p" * 32,
        "factory_id": "f" * 32,
        "repo": REPO,
        "number": 9631,
        "url": "https://github.com/caffeinelabs/app/pull/9631",
        "head_sha": HEAD,
        "state": "open",
        "is_draft": False,
    }
    return FactoryPr(**{**base, **fields})


def _gates(**states: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "draft": "pass",
        "checks": "pass",
        "review": "pass",
        "verify": "pass",
        "demo": "pass",
        "mergeable": "pass",
        "base": "pass",
        "protected_paths": "pass",
    }
    out.update(states)
    out["evidence"] = {"checks": {"failing_names": ["caffeine-api test", "lint"]}}
    return out


READY_PREVIEW = {
    "state": "ready",
    "deployed": {"sha": HEAD},
    "urls": {"app": "https://app-p1.envs.example", "api": "https://api-p1.envs.example"},
}


def _loop(kind: str, state: str = "running", **spent: Any) -> FactoryLoop:
    return FactoryLoop(
        id="l" * 32, factory_id="f" * 32, kind=kind, subject="x", state=state, spent=spent
    )


# ── next_step ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("pr", "gates", "loops_", "preview", "sentinel", "step", "cause"),
    [
        pytest.param(
            _pr(state="merged"),
            _gates(),
            [],
            None,
            None,
            "NONE",
            "the PR is not open",
            id="merged",
        ),
        pytest.param(
            _pr(is_draft=True),
            _gates(draft="pending"),
            [],
            None,
            None,
            "NONE",
            "a draft: its author is still coding",
            id="draft",
        ),
        pytest.param(
            _pr(),
            _gates(),
            [_loop("implement", "exhausted")],
            None,
            None,
            "WAIT_HUMAN",
            "implement exhausted",
            id="loop-exhausted",
        ),
        pytest.param(
            _pr(ready=True),
            _gates(),
            [_loop("ci", "exhausted")],
            READY_PREVIEW,
            None,
            "HANDOVER",
            "all gates pass",
            id="ci-loop-is-not-a-human-wait",
        ),
        pytest.param(
            _pr(), _gates(checks="fail"), [], None, None, "FIX_CI", "CI failed", id="checks-fail"
        ),
        pytest.param(
            _pr(),
            _gates(checks="pending"),
            [],
            None,
            None,
            "NONE",
            "CI is running",
            id="checks-pending",
        ),
        pytest.param(
            _pr(),
            _gates(review="pending"),
            [],
            None,
            None,
            "REVIEW",
            "CI green",
            id="review-pending",
        ),
        pytest.param(
            _pr(),
            _gates(review="fail"),
            [],
            None,
            None,
            "NONE",
            "review CHANGES: the author reworks",
            id="review-changes",
        ),
        pytest.param(
            _pr(),
            _gates(verify="pending"),
            [],
            None,
            None,
            "PREVIEW",
            "review APPROVE",
            id="no-preview",
        ),
        pytest.param(
            _pr(),
            _gates(verify="pending"),
            [],
            {**READY_PREVIEW, "deployed": {"sha": OLD}},
            None,
            "PREVIEW",
            "review APPROVE",
            id="preview-at-an-old-head",
        ),
        pytest.param(
            _pr(),
            _gates(verify="pending"),
            [],
            READY_PREVIEW,
            None,
            "NONE",
            "env_keeper deploys and verifies the head",
            id="preview-ready-verifying",
        ),
        pytest.param(
            _pr(),
            _gates(verify="pending"),
            [],
            {**READY_PREVIEW, "state": "deploying"},
            None,
            "NONE",
            "env_keeper deploys and verifies the head",
            id="deploying",
        ),
        pytest.param(
            _pr(),
            _gates(verify="fail"),
            [],
            READY_PREVIEW,
            None,
            "NONE",
            "verify FAIL: the author fixes the head",
            id="verify-fail",
        ),
        pytest.param(
            _pr(),
            _gates(demo="pending"),
            [],
            READY_PREVIEW,
            None,
            "DEMO",
            "verify PASS",
            id="demo-pending",
        ),
        pytest.param(
            _pr(),
            _gates(demo="pending"),
            [],
            None,
            None,
            "PREVIEW",
            "verify PASS · the preview is not at the head",
            id="demo-needs-a-preview",
        ),
        pytest.param(
            _pr(),
            _gates(demo="fail"),
            [],
            READY_PREVIEW,
            None,
            "NONE",
            "demo FAIL: the author fixes the head",
            id="demo-fail",
        ),
        pytest.param(
            _pr(ready=False),
            _gates(mergeable="fail"),
            [],
            READY_PREVIEW,
            None,
            "NONE",
            "a gate is pending or failing",
            id="conflicts",
        ),
        pytest.param(
            _pr(ready=True),
            _gates(),
            [],
            READY_PREVIEW,
            None,
            "HANDOVER",
            "all gates pass",
            id="handover",
        ),
        pytest.param(
            _pr(ready=True),
            _gates(verify="skip", demo="skip"),
            [],
            None,
            None,
            "HANDOVER",
            "all gates pass",
            id="no-env-handover",
        ),
        pytest.param(
            _pr(ready=True),
            _gates(),
            [_loop("claim", claimed_head=HEAD[:12])],
            READY_PREVIEW,
            None,
            "ASK_SENTINEL",
            "claimed READY",
            id="claimed",
        ),
        pytest.param(
            _pr(ready=True),
            _gates(),
            [_loop("claim", claimed_head=OLD[:12])],
            READY_PREVIEW,
            None,
            "HANDOVER",
            "all gates pass",
            id="claimed-old-head",
        ),
        pytest.param(
            _pr(ready=True),
            _gates(),
            [],
            READY_PREVIEW,
            {"verdict": "verified", "head_sha": HEAD},
            "WAIT_HUMAN",
            "Sentinel verified: a human merges",
            id="verified",
        ),
    ],
)
def test_next_step_table(
    pr: FactoryPr,
    gates: dict[str, Any],
    loops_: list[FactoryLoop],
    preview: dict[str, Any] | None,
    sentinel: dict[str, Any] | None,
    step: str,
    cause: str,
) -> None:
    found = next_step(pr, gates, loops_, preview, [], sentinel)
    assert (found.name, found.cause) == (step, cause)
    if step == "FIX_CI":
        assert found.args == {"checks": "caffeine-api test, lint"}
    if step == "DEMO":
        assert found.args["app"] == READY_PREVIEW["urls"]["app"]


_STATES = ("pass", "pending", "fail", "skip")


def _expected(checks: str, review: str, verify: str, demo: str) -> str:
    """The reference precedence: the first gate in pipeline order that is not done decides."""
    if checks == "fail":
        return "FIX_CI"
    if checks != "pass":
        return "NONE"
    if review == "pending":
        return "REVIEW"
    if review != "pass":
        return "NONE"
    for state in (verify, demo):
        if state == "pending":
            return "PREVIEW"  # no preview at the head yet
        if state == "fail":
            return "NONE"
    return "HANDOVER"


@pytest.mark.parametrize(
    ("checks", "review", "verify", "demo"), list(itertools.product(_STATES, repeat=4))
)
def test_next_step_for_every_gate_combination(
    checks: str, review: str, verify: str, demo: str
) -> None:
    gates = _gates(checks=checks, review=review, verify=verify, demo=demo)
    ready = all(s in ("pass", "skip") for s in (checks, review, verify, demo))
    found = next_step(_pr(ready=ready), gates, [], None, [], None)
    assert found.name == _expected(checks, review, verify, demo)
    assert found.head == HEAD


def test_hint_texts() -> None:
    pr = _pr()
    step = Step("PREVIEW", HEAD, "review APPROVE", {"pr": 9631})
    assert hint_text(pr, step, base="temp-main") == (
        f"[Factory engine · NEXT · PR #9631 @5e1c2d4 · review APPROVE → PREVIEW pr=9631 "
        f"head={HEAD} base=temp-main]"
    )
    assert hint_text(pr, Step("FIX_CI", HEAD, "CI failed", {"checks": "lint"}), base="x") == (
        "[Factory engine · NEXT · PR #9631 @5e1c2d4 · CI failed → FIX_CI checks=lint]"
    )
    assert hint_text(pr, Step("REVIEW", HEAD, "CI green"), base="x").endswith(
        f"CI green → REVIEW head={HEAD}]"
    )
    demo = Step("DEMO", HEAD, "verify PASS", {"pr": pr.url, "app": "A", "api": "B"})
    assert hint_text(pr, demo, base="x").endswith(f"→ DEMO pr={pr.url} sha={HEAD} app=A api=B]")
    assert pipeline.ready_text("db-version", 2, [("T-14", "db-row"), ("T-16", "docs")]) == (
        "[Factory engine · READY · db-version · wave 2 · T-14 db-row, T-16 docs] "
        'call sys_factory_task(op="ready") for the cards'
    )


# ── The engine (cockpit) ───────────────────────────────────────


@pytest_asyncio.fixture()
async def cockpit(
    world: Any, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    pipeline.reset()
    loops.reset()
    tasks.reset()
    monkeypatch.setattr(pipeline, "auto_flush", False)
    async for value in build_cockpit(world, db_uri, monkeypatch):
        value.store.upsert_project(
            value.fid,
            SLUG,
            title="API version",
            repo=REPO,
            base_branch="temp-main",
            charter={"goal": "GET /version", "budget_ics": 2},
            pl_session_id=world.id("pl"),
        )
        yield value
    pipeline.reset()


def _store_pr(cockpit: Cockpit, number: int, **gates: str) -> FactoryPr:
    pr, _ = cockpit.store.upsert_pr(
        cockpit.fid,
        REPO,
        number,
        project_slug=SLUG,
        head_sha=HEAD,
        head_ref=f"factory/{SLUG}/t{number}",
        base_ref="temp-main",
        gates=_gates(**gates),
        title=f"PR {number}",
    )
    return pr


def _next_mail(cockpit: Cockpit) -> list[str]:
    return [t for t in cockpit.mailer.texts_to(cockpit.world.id("pl")) if "Factory engine" in t]


async def test_next_hints_wait_the_grace_then_go_once(cockpit: Cockpit) -> None:
    pr = _store_pr(cockpit, 9631, review="pending")
    t0 = time.time()
    step = await pipeline.consider_pr(cockpit.app.state, cockpit.fid, pr.id, now=t0)
    assert step is not None and step.name == "REVIEW"
    assert await pipeline.flush_due(cockpit.app.state, t0 + 29) == 0
    assert await pipeline.flush_due(cockpit.app.state, t0 + 30) == 1
    assert _next_mail(cockpit) == [
        f"[Factory engine · NEXT · PR #9631 @5e1c2d4 · CI green → REVIEW head={HEAD}]"
    ]
    event = cockpit.events("engine.next")[-1]
    assert event.to_id == cockpit.world.id("pl") and event.data["coalesced"] == 1
    assert event.data["hints"][0]["step"] == "REVIEW"
    # The same step for the same head is never sent twice.
    await pipeline.consider_pr(cockpit.app.state, cockpit.fid, pr.id, now=t0 + 40)
    assert await pipeline.flush_due(cockpit.app.state, t0 + 400) == 0
    assert len(_next_mail(cockpit)) == 1


async def test_next_hints_coalesce_per_pl_over_the_window(cockpit: Cockpit) -> None:
    first = _store_pr(cockpit, 9631, review="pending")
    t0 = time.time()
    await pipeline.consider_pr(cockpit.app.state, cockpit.fid, first.id, now=t0)
    await pipeline.flush_due(cockpit.app.state, t0 + 30)
    # Two more PRs move inside the 120 s window: they wait for it and go in one mail.
    second = _store_pr(cockpit, 9632, review="pending")
    third = _store_pr(cockpit, 9633, verify="pending")
    fourth = _store_pr(cockpit, 9634, checks="fail")  # the poller's notice carries FIX_CI
    await pipeline.consider_pr(cockpit.app.state, cockpit.fid, second.id, now=t0 + 40)
    await pipeline.consider_pr(cockpit.app.state, cockpit.fid, third.id, now=t0 + 45)
    step = await pipeline.consider_pr(cockpit.app.state, cockpit.fid, fourth.id, now=t0 + 46)
    assert step is not None and step.name == "FIX_CI"
    assert pipeline.due_at(cockpit.world.id("pl")) == t0 + 30 + pipeline.WAKE_COALESCE_S
    assert await pipeline.flush_due(cockpit.app.state, t0 + 100) == 0
    assert await pipeline.flush_due(cockpit.app.state, t0 + 150) == 1
    mails = _next_mail(cockpit)
    assert len(mails) == 2
    assert mails[1].splitlines() == [
        f"[Factory engine · NEXT · PR #9632 @5e1c2d4 · CI green → REVIEW head={HEAD}]",
        f"[Factory engine · NEXT · PR #9633 @5e1c2d4 · review APPROVE → PREVIEW pr=9633 "
        f"head={HEAD} base=temp-main]",
    ]
    assert cockpit.events("engine.next")[-1].data["coalesced"] == 2


async def test_a_hint_the_pl_already_acted_on_is_dropped(cockpit: Cockpit) -> None:
    pr = _store_pr(cockpit, 9631, verify="pending")
    t0 = time.time()
    await pipeline.consider_pr(cockpit.app.state, cockpit.fid, pr.id, now=t0)
    # The PL sent PREVIEW itself before the grace ended: the preview deploys at the head.
    cockpit.store.update_pr(pr.id, preview={"state": "deploying", "deployed": {"sha": HEAD}})
    assert await pipeline.flush_due(cockpit.app.state, t0 + 30) == 0
    assert _next_mail(cockpit) == []


async def test_observe_mode_mails_no_hints(cockpit: Cockpit) -> None:
    cockpit.world.store.set_labels(cockpit.fid, {tasks.LABEL_ENGINE_MODE: "observe"})
    pr = _store_pr(cockpit, 9631, review="pending")
    t0 = time.time()
    await pipeline.consider_pr(cockpit.app.state, cockpit.fid, pr.id, now=t0)
    assert await pipeline.flush_due(cockpit.app.state, t0 + 30) == 0
    assert _next_mail(cockpit) == [] and cockpit.events("engine.next") == []


async def test_the_event_listener_recomputes_on_gate_changes(cockpit: Cockpit) -> None:
    from omnigent.server.factory import events

    pr = _store_pr(cockpit, 9631, review="pending")
    remove = pipeline.install()
    try:
        await events.emit(
            cockpit.app.state,
            cockpit.fid,
            "gate.changed",
            project=SLUG,
            summary="x",
            data={"pr_id": pr.id, "gate": "checks", "to": "pass", "head_sha": HEAD},
        )
    finally:
        remove()
    queued = pipeline._state.queue[cockpit.world.id("pl").replace("-", "")]
    assert [h.step for h in queued.values()] == ["REVIEW"]


async def test_ready_waves_wait_the_grace_and_do_not_repeat(cockpit: Cockpit) -> None:
    actor = Actor.agent(cockpit.world.id("pl"), "project_lead", title="PL")
    items = [tasks.TaskIn(key=k, title=k) for k in ("db-row", "docs", "ui")]
    items[2] = tasks.TaskIn(key="ui", title="ui", deps=["db-row"])
    await tasks.upsert_tasks(cockpit.app.state, cockpit.fid, SLUG, items, actor)
    t0 = time.time()
    pipeline.queue_ready(cockpit.fid, SLUG, t0)
    await pipeline.flush_due(cockpit.app.state, t0 + 10)
    assert _next_mail(cockpit) == []
    await pipeline.flush_due(cockpit.app.state, t0 + pipeline.READY_GRACE_S)
    assert _next_mail(cockpit) == [
        "[Factory engine · READY · api-version · wave 1 · T-1 db-row, T-2 docs] "
        'call sys_factory_task(op="ready") for the cards'
    ]
    event = cockpit.events("engine.ready_wave")[-1]
    assert event.data["tasks"] == ["db-row", "docs"] and event.data["wave"] == 1
    # The same wave is not sent again; no free slot means no wave.
    pipeline.queue_ready(cockpit.fid, SLUG, t0 + 200)
    await pipeline.flush_due(cockpit.app.state, t0 + 400)
    assert len(_next_mail(cockpit)) == 1
    for key in ("db-row", "docs"):
        await tasks.start_task(cockpit.app.state, cockpit.fid, SLUG, key, actor)
    assert pipeline.ready_wave(cockpit.deps, cockpit.fid, SLUG) is None


async def test_ready_waves_are_not_mailed_in_observe(cockpit: Cockpit) -> None:
    cockpit.world.store.set_labels(cockpit.fid, {tasks.LABEL_ENGINE_MODE: "observe"})
    actor = Actor.agent(cockpit.world.id("pl"), "project_lead", title="PL")
    await tasks.upsert_tasks(
        cockpit.app.state, cockpit.fid, SLUG, [tasks.TaskIn(key="a", title="a")], actor
    )
    t0 = time.time()
    pipeline.queue_ready(cockpit.fid, SLUG, t0)
    await pipeline.flush_due(cockpit.app.state, t0 + pipeline.READY_GRACE_S)
    assert _next_mail(cockpit) == []
