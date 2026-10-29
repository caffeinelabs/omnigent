"""The loop engine: facts become iterations, budgets stop loops, decisions restart them.

Loop transitions are driven by the events the server already records
(``task.state``, ``task.bound``, ``gate.changed``, ``demo.published``, …),
emitted here through the real event log with the loop listener installed.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.entities.factory import FactoryLoop, FactoryPr, FactoryTask
from omnigent.server.factory import (
    budgets,
    decisions,
    detectors,
    events,
    issues,
    loops,
    nudge,
    pipeline,
    prs,
    tasks,
)
from omnigent.server.factory.actor import Actor
from omnigent.server.factory.control import CockpitError
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit
from tests.server.factory.conftest import FakeClock
from tests.server.factory.github_fakes import FakeCheck, FakePr

SLUG = "api-version"
REPO = "caffeinelabs/app"
H1, H2, H3, H4 = ("a" * 40, "b" * 40, "c" * 40, "d" * 40)


@pytest_asyncio.fixture()
async def cockpit(
    world: Any, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    loops.reset()
    tasks.reset()
    budgets.reset()
    pipeline.reset()
    monkeypatch.setattr(pipeline, "auto_flush", False)
    remove = loops.install()
    try:
        async for value in build_cockpit(world, db_uri, monkeypatch):
            value.store.upsert_project(
                value.fid,
                SLUG,
                title="API version",
                repo=REPO,
                base_branch="temp-main",
                charter={"goal": "GET /version", "budget_ics": 3},
                pl_session_id=world.id("pl"),
            )
            yield value
    finally:
        remove()
        loops.reset()
        tasks.reset()
        pipeline.reset()


def _pl(cockpit: Cockpit) -> Actor:
    return Actor.agent(cockpit.world.id("pl"), "project_lead", title="PL · api-version")


def set_mode(cockpit: Cockpit, mode: str) -> None:
    from omnigent.server.factory import definition

    definition.set_values(cockpit.deps, cockpit.fid, {"engine.mode": mode}, created_by="test")


async def plan(cockpit: Cockpit, *keys: str) -> None:
    items = [tasks.TaskIn(key=k, title=f"Task {k}") for k in keys]
    await tasks.upsert_tasks(cockpit.app.state, cockpit.fid, SLUG, items, _pl(cockpit))


async def start(cockpit: Cockpit, key: str, **kwargs: Any) -> dict[str, Any]:
    return await tasks.start_task(
        cockpit.app.state, cockpit.fid, SLUG, key, _pl(cockpit), **kwargs
    )


def task(cockpit: Cockpit, key: str) -> FactoryTask:
    row = cockpit.store.get_task_by_key(cockpit.fid, SLUG, key)
    assert row is not None
    return row


def loop_of(cockpit: Cockpit, kind: str, subject: str) -> FactoryLoop:
    row = cockpit.store.get_loop_by_subject(cockpit.fid, kind, subject)
    assert row is not None, (kind, subject)
    return row


def implement(cockpit: Cockpit, key: str) -> FactoryLoop:
    return loop_of(cockpit, "implement", loops.task_subject(task(cockpit, key).id))


def make_pr(cockpit: Cockpit, key: str, head: str = H1, number: int = 9631) -> FactoryPr:
    row = task(cockpit, key)
    pr, _ = cockpit.store.upsert_pr(
        cockpit.fid,
        REPO,
        number,
        project_slug=SLUG,
        head_sha=head,
        head_ref=f"factory/{SLUG}/{key}",
        base_ref="temp-main",
        task_id=row.id,
        title=f"PR for {key}",
    )
    cockpit.store.update_task(row.id, pr_id=pr.id)
    return pr


async def emit(cockpit: Cockpit, kind: str, *, to_id: str | None = None, **data: Any) -> Any:
    return await events.emit(
        cockpit.app.state, cockpit.fid, kind, project=SLUG, to_id=to_id, summary=kind, data=data
    )


async def gate(
    cockpit: Cockpit, pr: FactoryPr, name: str, to: str, head: str, **evidence: Any
) -> FactoryPr:
    current = cockpit.store.get_pr(pr.id)
    assert current is not None
    gates = dict(current.gates or {})
    gates[name] = to
    found = dict(gates.get("evidence") or {})
    if evidence:
        found[name] = evidence
    gates["evidence"] = found
    updated = cockpit.store.update_pr(pr.id, gates=gates, head_sha=head)
    assert updated is not None
    await emit(cockpit, "gate.changed", pr_id=pr.id, gate=name, to=to, head_sha=head)
    return updated


async def review(
    cockpit: Cockpit,
    pr: FactoryPr,
    head: str,
    verdict: str,
    *,
    at: int,
    blocking: int | None = None,
    findings: list[str] | None = None,
) -> None:
    evidence: dict[str, Any] = {"verdict": verdict, "sha": head, "at": at}
    if blocking is not None:
        evidence["blocking"] = blocking
    if findings is not None:
        evidence["findings"] = findings
    await gate(cockpit, pr, "review", "pass" if verdict == "APPROVE" else "fail", head, **evidence)


def kinds(cockpit: Cockpit, prefix: str = "loop.") -> list[str]:
    return [e.kind for e in cockpit.events() if e.kind.startswith(prefix)]


def loop_decisions(cockpit: Cockpit) -> list[Any]:
    return [
        d
        for d in cockpit.store.list_decisions(factory_id=cockpit.fid, limit=50)
        if d.source in ("loop", "done_check")
    ]


# ── The catalogue and budgets ──────────────────────────────────


def test_the_catalogue_has_every_loop_with_its_budget_keys() -> None:
    assert [s.ref for s in loops.CATALOGUE.values()] == [f"L{i}" for i in range(1, 10)]
    assert tuple(loops.CATALOGUE) == loops.LOOP_KINDS
    for spec in loops.CATALOGUE.values():
        assert set(spec.budget_keys) == set(loops.DEFAULT_BUDGETS[spec.kind])


def test_budgets_resolve_definition_then_charter_then_task() -> None:
    from omnigent.entities.factory import FactoryProject

    fid = "f" * 32
    restore = loops.set_budget_resolver(
        lambda _fid, kind, key: 5 if (kind, key) == ("implement", "attempts") else None
    )
    try:
        assert loops.budget(fid, "implement", "attempts") == 5
        assert loops.budget(fid, "implement", "usd") == 15.0
        project = FactoryProject(
            id="p" * 32,
            factory_id=fid,
            slug=SLUG,
            title="x",
            charter={"budgets": {"task_usd": 9, "review": {"rounds": 4}}},
        )
        row = FactoryTask(
            id="t" * 32,
            factory_id=fid,
            number=1,
            project_slug=SLUG,
            key="k",
            title="k",
            budget={"attempts": 2},
        )
        spec = loops.resolve_spec(fid, "implement", project=project, task=row)
        assert spec["attempts"] == 2 and spec["usd"] == 9.0
        assert loops.resolve_spec(fid, "review", project=project) == {"rounds": 4}
    finally:
        restore()
    assert loops.budget(fid, "implement", "attempts") == 3


@pytest.mark.parametrize(
    ("text", "default", "exit_code", "expected"),
    [
        ("code: migration failed", "env", None, "code"),
        ("env: tilt timed out", "code", None, "env"),
        ("ENV: dns", "code", None, "env"),
        ("the preview is stale", "code", None, "env"),
        ("something broke", "code", None, "code"),
        ("something broke", "env", None, "env"),
        (None, "code", 3, "env"),
    ],
)
def test_failures_classify_as_code_or_env(
    text: str | None, default: str, exit_code: int | None, expected: str
) -> None:
    assert loops.classify(text, default=default, exit_code=exit_code) == expected


# ── L1 implement ───────────────────────────────────────────────


async def test_implement_exhausts_at_three_and_escalates_once(cockpit: Cockpit) -> None:
    """Three CHANGES rounds: implement 3/3 exhausted, one loop decision, the PL mail."""
    await plan(cockpit, "route")
    await start(cockpit, "route")
    assert implement(cockpit, "route").iteration == 1
    pr = make_pr(cockpit, "route")
    now = int(time.time())
    await gate(cockpit, pr, "checks", "pass", H1)
    assert implement(cockpit, "route").state == "converged"

    await review(cockpit, pr, H1, "CHANGES", at=now, blocking=2, findings=["aaaa0001", "aaaa0002"])
    row = implement(cockpit, "route")
    assert (row.state, row.iteration) == ("running", 2)
    await review(cockpit, pr, H2, "CHANGES", at=now + 1, blocking=1, findings=["aaaa0003"])
    assert implement(cockpit, "route").iteration == 3
    await review(cockpit, pr, H3, "CHANGES", at=now + 2, blocking=1, findings=["aaaa0004"])

    row = implement(cockpit, "route")
    assert (row.state, row.iteration, loops.limit_of(row)) == ("exhausted", 3, 3)
    found = loop_decisions(cockpit)
    assert len(found) == 1
    decision = found[0]
    assert decision.assignee == "operators" and decision.blocking
    assert decision.question == "T-1 route: implement 3/3 exhausted - what next?"
    actions = [o["action"] for o in decision.options]
    assert actions == ["raise_budget", "switch_vendor", "fresh_restart", "abandon_task"]
    assert row.decision_id == decision.id
    # The review loop ran out too and rides on the same decision.
    review_loop = loop_of(cockpit, "review", loops.pr_subject(pr.id))
    assert review_loop.state == "exhausted" and review_loop.decision_id == decision.id
    mail = cockpit.mailer.texts_to(cockpit.world.id("pl"))
    assert mail[-1] == (
        f"[Factory loop · EXHAUSTED · T-1 route · implement 3/3 · {decision.ref}] "
        f"Do not dispatch route again until {decision.ref} is answered.\n"
        "Why: review CHANGES: review round 3: 1 blocking. Keep the rest of the project moving."
    )
    assert "loop.exhausted" in kinds(cockpit) and "loop.escalated" in kinds(cockpit)
    spans = cockpit.store.list_spans(cockpit.fid, kinds=["loop", "loop_iter"])
    outer = [s for s in spans if s.kind == "loop" and s.loop_id == row.id]
    assert outer and outer[0].outcome == "exhausted"
    # The Timeline names the lane by its task.
    assert outer[0].attrs["task_ref"] == "T-1"
    iters = sorted(
        (s for s in spans if s.kind == "loop_iter" and s.loop_id == row.id),
        key=lambda s: s.attempt or 0,
    )
    assert [s.attempt for s in iters] == [1, 2, 3]
    assert all(s.outcome == "fail" for s in iters)


async def test_switch_vendor_makes_the_next_attempt_run_on_codex(cockpit: Cockpit) -> None:
    await plan(cockpit, "route")
    await start(cockpit, "route")
    for _ in range(2):
        await emit(
            cockpit,
            "task.state",
            task_id=task(cockpit, "route").id,
            result="FAIL",
            note="tests red",
            **{"from": "running", "to": "running"},
        )
        attempt = task(cockpit, "route").attempts + 1
        await start(cockpit, "route")
        assert task(cockpit, "route").attempts == attempt
    await emit(
        cockpit,
        "task.state",
        task_id=task(cockpit, "route").id,
        result="BLOCKED",
        note="acceptance needs a table that does not exist",
        **{"from": "running", "to": "blocked"},
    )
    row = implement(cockpit, "route")
    assert (row.state, row.iteration, row.last_verdict) == ("exhausted", 3, "BLOCKED")
    decision = loop_decisions(cockpit)[0]
    switch = next(o for o in decision.options if o["action"] == "switch_vendor")
    assert switch["label"] == "Switch vendor → ic_codex" and switch["recommended"]

    result = await decisions.answer_decision(
        cockpit.app.state, decision.id, user_id="carol", option_id=switch["id"]
    )
    assert result.delivered_to is not None
    mail = cockpit.mailer.texts_to(cockpit.world.id("pl"))[-1]
    assert mail == (
        f"[Factory loop · SWITCH VENDOR · T-1 · implement 3→4 · ic → ic_codex by carol · "
        f"{decision.ref}] No worker of it is open; "
        'sys_factory_task(op="start", key="route") and dispatch its title_to_use and '
        "card_text to ic_codex, with a fresh context from NOTES.md."
    )
    row = implement(cockpit, "route")
    assert row.state == "running" and loops.limit_of(row) == 4
    assert "loop.budget_raised" in kinds(cockpit)

    started = await start(cockpit, "route")
    assert started["harness"] == "ic_codex" and started["title_to_use"] == "route-r3"
    row = implement(cockpit, "route")
    assert (row.state, row.iteration) == ("running", 4)
    assert task(cockpit, "route").vendor == "codex"


async def test_raise_budget_mails_the_exact_text(cockpit: Cockpit) -> None:
    await plan(cockpit, "route")
    await start(cockpit, "route")
    for _ in range(2):
        await emit(
            cockpit,
            "task.state",
            task_id=task(cockpit, "route").id,
            result="FAIL",
            note="red",
            **{"from": "running", "to": "running"},
        )
        await start(cockpit, "route")
    await emit(
        cockpit,
        "task.state",
        task_id=task(cockpit, "route").id,
        result="FAIL",
        note="still red",
        **{"from": "running", "to": "running"},
    )
    decision = loop_decisions(cockpit)[0]
    await decisions.answer_decision(
        cockpit.app.state, decision.id, user_id="carol", option_id="raise"
    )
    assert cockpit.mailer.texts_to(cockpit.world.id("pl"))[-1] == (
        f"[Factory loop · BUDGET RAISED · T-1 · implement 3→4 by carol · {decision.ref}] "
        'Dispatch route again: sys_factory_task(op="start", key="route"), then send its '
        "title_to_use and card_text verbatim."
    )
    assert task(cockpit, "route").budget == {"attempts": 4}
    assert implement(cockpit, "route").state == "running"


async def test_fresh_restart_and_abandon_mail_the_exact_text(cockpit: Cockpit) -> None:
    await plan(cockpit, "route", "docs")
    for key in ("route", "docs"):
        await start(cockpit, key)
        for _ in range(3):
            await emit(
                cockpit,
                "task.state",
                task_id=task(cockpit, key).id,
                result="FAIL",
                note=f"{key} is red",
                **{"from": "running", "to": "running"},
            )
            if implement(cockpit, key).state != "exhausted":
                await start(cockpit, key)
    route, docs = (
        next(d for d in loop_decisions(cockpit) if key in d.question) for key in ("route", "docs")
    )
    await decisions.answer_decision(
        cockpit.app.state, route.id, user_id="carol", option_id="fresh"
    )
    assert cockpit.mailer.texts_to(cockpit.world.id("pl"))[-1] == (
        f"[Factory loop · FRESH RESTART · T-1 · implement 3→4 by carol · {route.ref}] "
        "No worker of it is open; dispatch route-r3 with a fresh context from NOTES.md and "
        'this evidence: FAIL: route is red (sys_factory_task(op="start", key="route") first; '
        "send its card_text)."
    )
    await decisions.answer_decision(
        cockpit.app.state, docs.id, user_id="carol", option_id="abandon"
    )
    text = cockpit.mailer.texts_to(cockpit.world.id("pl"))[-1]
    assert text.startswith(f'[Factory decision · {docs.ref} answered by carol · "Abandon task"]')
    assert "Abandon docs: the server cancelled it." in text
    assert task(cockpit, "docs").state == "cancelled"
    assert implement(cockpit, "docs").state == "cancelled"


async def test_money_raises_are_for_owners(cockpit: Cockpit) -> None:
    await plan(cockpit, "route")
    await start(cockpit, "route")
    cockpit.store.update_task(task(cockpit, "route").id, cost_usd=15.5)
    await emit(
        cockpit,
        "task.state",
        task_id=task(cockpit, "route").id,
        result="PASS",
        **{"from": "running", "to": "running"},
    )
    row = implement(cockpit, "route")
    assert row.state == "exhausted" and row.last_reason == "$15.50 of $15.00 spent"
    assert "budget.near" in kinds(cockpit, "budget.")
    decision = loop_decisions(cockpit)[0]
    assert decision.assignee == "owners"
    raise_usd = next(o for o in decision.options if o["action"] == "raise_budget")
    assert raise_usd["owner_only"] is True
    with pytest.raises(CockpitError, match="assigned to owners"):  # carol operates
        await decisions.answer_decision(
            cockpit.app.state, decision.id, user_id="carol", option_id=raise_usd["id"]
        )
    await decisions.answer_decision(
        cockpit.app.state, decision.id, user_id="alice", option_id=raise_usd["id"]
    )
    assert task(cockpit, "route").budget == {"usd": 30.0}
    assert implement(cockpit, "route").state == "running"


async def test_observe_mode_records_but_never_decides_or_mails(cockpit: Cockpit) -> None:
    set_mode(cockpit, "observe")
    await plan(cockpit, "route")
    await start(cockpit, "route")
    for _ in range(3):
        await emit(
            cockpit,
            "task.state",
            task_id=task(cockpit, "route").id,
            result="FAIL",
            note="red",
            **{"from": "running", "to": "running"},
        )
        if implement(cockpit, "route").state != "exhausted":
            await start(cockpit, "route")
    assert implement(cockpit, "route").state == "exhausted"
    assert "loop.exhausted" in kinds(cockpit)
    assert loop_decisions(cockpit) == []
    assert not any("[Factory loop" in t for t in cockpit.mailer.texts_to(cockpit.world.id("pl")))


async def test_a_dispatch_past_the_budget_is_a_violation(cockpit: Cockpit) -> None:
    await plan(cockpit, "route")
    await start(cockpit, "route")
    for _ in range(3):
        await emit(
            cockpit,
            "task.state",
            task_id=task(cockpit, "route").id,
            result="FAIL",
            note="red",
            **{"from": "running", "to": "running"},
        )
        if implement(cockpit, "route").state != "exhausted":
            await start(cockpit, "route")
    # assist: start is still allowed, with a warning, and recorded as a violation.
    started = await start(cockpit, "route")
    assert "budget violation" in started["budget_warning"]
    assert "loop.budget_violation" in kinds(cockpit)
    issue = [
        i for i in cockpit.store.list_issues(cockpit.fid) if i.rule == "loop.budget_violation"
    ]
    assert issue and issue[0].class_ == "finding"


@pytest.mark.parametrize("mode", ["observe", "assist", "enforce"])
async def test_start_returns_409_only_in_enforce(cockpit: Cockpit, mode: str) -> None:
    await plan(cockpit, "route")
    await start(cockpit, "route")
    for _ in range(3):
        await emit(
            cockpit,
            "task.state",
            task_id=task(cockpit, "route").id,
            result="FAIL",
            note="red",
            **{"from": "running", "to": "running"},
        )
        if implement(cockpit, "route").state != "exhausted":
            await start(cockpit, "route")
    set_mode(cockpit, mode)
    if mode != "enforce":
        assert (await start(cockpit, "route"))["attempt"] == 4
        return
    with pytest.raises(CockpitError) as raised:
        await start(cockpit, "route")
    assert raised.value.status_code == 409 and raised.value.code == "budget_exhausted"
    decision = loop_decisions(cockpit)[0]
    assert raised.value.extra["decision"] == decision.ref


async def test_the_agent_route_answers_409_budget_exhausted(cockpit: Cockpit) -> None:
    await plan(cockpit, "route")
    cockpit.store.update_task(task(cockpit, "route").id, attempts=3)
    set_mode(cockpit, "enforce")
    resp = await cockpit.client.post(
        "/v1/factory/agent/tasks/route/start",
        json={"from_session_id": cockpit.world.id("pl")},
        headers={"X-Test-User": "alice"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "budget_exhausted"


async def test_a_task_closing_ends_its_loop(cockpit: Cockpit) -> None:
    await plan(cockpit, "route", "docs")
    await start(cockpit, "route")
    await start(cockpit, "docs")
    await tasks.close_task(cockpit.app.state, cockpit.fid, SLUG, "route", "done", _pl(cockpit))
    await tasks.close_task(cockpit.app.state, cockpit.fid, SLUG, "docs", "cancel", _pl(cockpit))
    assert implement(cockpit, "route").state == "converged"
    assert implement(cockpit, "docs").state == "cancelled"


# ── L2 review: dry and thrash ──────────────────────────────────


async def test_review_thrash_when_a_fixed_finding_returns(cockpit: Cockpit) -> None:
    await plan(cockpit, "route")
    cockpit.store.update_task(task(cockpit, "route").id, budget={"attempts": 6})
    await start(cockpit, "route")
    pr = make_pr(cockpit, "route")
    now = int(time.time())
    await review(cockpit, pr, H1, "CHANGES", at=now, blocking=2, findings=["0000aaaa", "0000bbbb"])
    await review(cockpit, pr, H2, "CHANGES", at=now + 1, blocking=1, findings=["0000bbbb"])
    row = loop_of(cockpit, "review", loops.pr_subject(pr.id))
    assert row.state == "running"
    await review(cockpit, pr, H3, "CHANGES", at=now + 2, blocking=1, findings=["0000aaaa"])
    row = loop_of(cockpit, "review", loops.pr_subject(pr.id))
    assert row.state == "thrashing"
    assert row.last_reason == "finding 0000aaaa came back in round 3"
    decision = loop_decisions(cockpit)[0]
    assert "Findings by round:" in (decision.context or "")
    assert cockpit.mailer.texts_to(cockpit.world.id("pl"))[-1].startswith(
        f"[Factory loop · THRASHING · PR #9631 · review 3/3 · {decision.ref}]"
    )


async def test_review_dry_when_a_round_adds_nothing_and_fixes_nothing(cockpit: Cockpit) -> None:
    await plan(cockpit, "route")
    await start(cockpit, "route")
    pr = make_pr(cockpit, "route")
    now = int(time.time())
    await review(cockpit, pr, H1, "CHANGES", at=now, blocking=2, findings=["0000aaaa", "0000bbbb"])
    await review(
        cockpit, pr, H2, "CHANGES", at=now + 1, blocking=2, findings=["0000aaaa", "0000bbbb"]
    )
    row = loop_of(cockpit, "review", loops.pr_subject(pr.id))
    assert row.state == "stalled" and row.last_reason is not None
    assert row.last_reason.startswith("review dry: round 2")


def test_dry_and_thrash_rules() -> None:
    def r(blocking: int | None, *prints: str) -> dict[str, Any]:
        return {"blocking": blocking, "findings": list(prints)}

    assert not loops.dry([r(2, "a", "b")])
    assert loops.dry([r(2, "a", "b"), r(2, "a", "b")])
    assert not loops.dry([r(2, "a", "b"), r(1, "a")])  # fewer blocking: progress
    assert not loops.dry([r(2, "a", "b"), r(2, "a", "c")])  # a new finding
    assert loops.dry([r(2), r(3)])  # no fingerprints: the count does not fall
    assert not loops.dry([r(3), r(1)])
    assert loops.thrashed([r(1, "a"), r(1, "b"), r(1, "a")]) == "a"
    assert loops.thrashed([r(1, "a"), r(1, "a"), r(1, "a")]) is None
    assert loops.thrashed([r(1, "a"), r(1, "a")]) is None


async def test_review_converges_on_approve(cockpit: Cockpit) -> None:
    await plan(cockpit, "route")
    await start(cockpit, "route")
    pr = make_pr(cockpit, "route")
    now = int(time.time())
    await review(cockpit, pr, H1, "APPROVE", at=now)
    await review(cockpit, pr, H1, "APPROVE", at=now)  # the same marker again: no new round
    row = loop_of(cockpit, "review", loops.pr_subject(pr.id))
    assert (row.state, row.iteration, row.last_verdict) == ("converged", 1, "APPROVE")


# ── L3 verify: env vs code ─────────────────────────────────────


async def test_verify_code_failures_charge_implement_env_failures_retry(cockpit: Cockpit) -> None:
    await plan(cockpit, "route")
    await start(cockpit, "route")
    pr = make_pr(cockpit, "route")
    now = int(time.time())
    await gate(
        cockpit,
        pr,
        "verify",
        "fail",
        H1,
        result="FAIL",
        sha=H1,
        at=now,
        reason="code:migration-missing",
    )
    row = loop_of(cockpit, "verify", loops.head_subject(pr.id, H1))
    assert row.state == "running" and row.spent["per_head"] == {H1[:7]: 1}
    assert implement(cockpit, "route").iteration == 2  # the author reworks
    iters = [
        s
        for s in cockpit.store.list_spans(cockpit.fid, kinds=["loop_iter"])
        if s.loop_id == row.id
    ]
    assert iters[0].attrs["class"] == "code"

    # A failed preview at the next head is the env's: it retries once, then exhausts.
    cockpit.store.update_pr(
        pr.id, head_sha=H2, preview={"state": "failed", "note": "tilt timed out"}
    )
    await emit(cockpit, "preview.reported", pr_id=pr.id, state="failed", sha=H2)
    env_loop = loop_of(cockpit, "verify", loops.head_subject(pr.id, H2))
    assert env_loop.spent["env_retries"] == 1 and env_loop.state == "running"
    assert loop_of(cockpit, "verify", loops.head_subject(pr.id, H1)).state == "superseded"
    assert implement(cockpit, "route").iteration == 2  # env failures do not charge it
    await emit(cockpit, "preview.reported", pr_id=pr.id, state="failed", sha=H2)
    env_loop = loop_of(cockpit, "verify", loops.head_subject(pr.id, H2))
    assert env_loop.state == "exhausted" and "env failures 2/1" in (env_loop.last_reason or "")
    # Verify has no decision: env_keeper is restarted by the PL.
    assert loop_decisions(cockpit) == []
    mail = cockpit.mailer.texts_to(cockpit.world.id("pl"))[-1]
    assert mail.startswith(
        f"[Factory loop · EXHAUSTED · PR #9631 @{H2[:7]} · verify 2/2] Restart env_keeper"
    )
    assert f"PREVIEW pr=9631 head={H2} base=temp-main" in mail


async def test_a_stale_preview_counts_as_an_env_failure(cockpit: Cockpit) -> None:
    await plan(cockpit, "route")
    await start(cockpit, "route")
    pr = make_pr(cockpit, "route")
    await emit(cockpit, "preview.sha_mismatch", pr_id=pr.id, probed=H4, deployed=H1)
    row = loop_of(cockpit, "verify", loops.head_subject(pr.id, H1))
    assert row.last_verdict == "STALE" and row.spent["env_retries"] == 1


# ── L4 demo per head ───────────────────────────────────────────


def _demo(cockpit: Cockpit, pr: FactoryPr, head: str, status: str, **manifest: Any) -> Any:
    return cockpit.store.create_demo(
        factory_id=cockpit.fid,
        project_slug=SLUG,
        repo=REPO,
        pr_number=pr.number,
        head_sha=head,
        session_id=cockpit.world.id("ic"),
        created_by_session=cockpit.world.id("ic"),
        status=status,
        summary=f"demo {status}",
        manifest=manifest or None,
        pr_id=pr.id,
    )


async def test_demo_loops_run_per_head(cockpit: Cockpit) -> None:
    await plan(cockpit, "route")
    await start(cockpit, "route")
    pr = make_pr(cockpit, "route")
    demo = _demo(cockpit, pr, H1, "ERROR")
    await emit(cockpit, "demo.published", demo_id=demo.id, pr_id=pr.id, status="ERROR")
    first = loop_of(cockpit, "demo", loops.head_subject(pr.id, H1))
    assert first.state == "running" and first.spent["env_retries"] == 1
    demo = _demo(cockpit, pr, H1, "ERROR", exit_code=3)
    await emit(cockpit, "demo.published", demo_id=demo.id, pr_id=pr.id, status="ERROR")
    first = loop_of(cockpit, "demo", loops.head_subject(pr.id, H1))
    assert first.state == "exhausted" and first.last_reason == f"demo ERROR 2/2 at {H1[:7]}"
    decision = loop_decisions(cockpit)[0]
    assert [o["label"] for o in decision.options][:2] == [
        "Review without a demo",
        "Fix the env, then retry",
    ]

    # A new head starts its own demo loop; FAIL charges the implement loop.
    demo = _demo(cockpit, pr, H2, "FAIL")
    await emit(cockpit, "demo.published", demo_id=demo.id, pr_id=pr.id, status="FAIL")
    assert loop_of(cockpit, "demo", loops.head_subject(pr.id, H1)).state == "superseded"
    second = loop_of(cockpit, "demo", loops.head_subject(pr.id, H2))
    assert (second.state, second.last_verdict) == ("running", "FAIL")
    assert implement(cockpit, "route").last_verdict == "DEMO FAIL"
    demo = _demo(cockpit, pr, H2, "PASS")
    await emit(cockpit, "demo.published", demo_id=demo.id, pr_id=pr.id, status="PASS")
    assert loop_of(cockpit, "demo", loops.head_subject(pr.id, H2)).state == "converged"


async def test_review_without_a_demo_mails_the_label_step(cockpit: Cockpit) -> None:
    await plan(cockpit, "route")
    await start(cockpit, "route")
    pr = make_pr(cockpit, "route")
    for _ in range(2):
        demo = _demo(cockpit, pr, H1, "ERROR")
        await emit(cockpit, "demo.published", demo_id=demo.id, pr_id=pr.id, status="ERROR")
    decision = loop_decisions(cockpit)[0]
    await decisions.answer_decision(
        cockpit.app.state, decision.id, user_id="carol", option_id="no_demo"
    )
    text = cockpit.mailer.texts_to(cockpit.world.id("pl"))[-1]
    assert text.startswith(
        f'[Factory decision · {decision.ref} answered by carol · "Review without a demo"]'
    )
    assert "label it factory:no-env" in text
    assert loop_of(cockpit, "demo", loops.head_subject(pr.id, H1)).state == "converged"


# ── L5 ci: flakes ──────────────────────────────────────────────


async def test_ci_flake_is_fail_then_pass_on_the_same_head(cockpit: Cockpit) -> None:
    await plan(cockpit, "route")
    await start(cockpit, "route")
    pr = make_pr(cockpit, "route")
    await gate(
        cockpit,
        pr,
        "checks",
        "fail",
        H1,
        failing_names=["test"],
        passing=0,
        failing=1,
        pending=0,
        total=1,
    )
    row = loop_of(cockpit, "ci", loops.head_subject(pr.id, H1))
    assert (row.state, row.last_verdict, row.last_reason) == ("running", "FAIL", "test")
    await gate(cockpit, pr, "checks", "pass", H1)
    row = loop_of(cockpit, "ci", loops.head_subject(pr.id, H1))
    assert (row.state, row.last_verdict) == ("converged", "FLAKE")
    assert row.spent["flakes"] == 1
    # A green head converges the implement loop.
    assert implement(cockpit, "route").state == "converged"
    await gate(cockpit, pr, "checks", "pass", H2)
    assert loop_of(cockpit, "ci", loops.head_subject(pr.id, H2)).last_verdict == "PASS"


# ── L6 claim ───────────────────────────────────────────────────


async def test_two_refuted_claims_in_a_row_exhaust_the_claim_loop(cockpit: Cockpit) -> None:
    await plan(cockpit, "route")
    await start(cockpit, "route")
    pr = make_pr(cockpit, "route")
    for _ in range(2):
        await emit(
            cockpit,
            "pr.claim",
            pr_id=pr.id,
            claim="ready_for_human_merge",
            head_sha=H1,
            claim_mismatch=True,
        )
    row = loop_of(cockpit, "claim", loops.pr_subject(pr.id))
    assert row.state == "exhausted" and row.spent["streak"] == 2
    decision = loop_decisions(cockpit)[0]
    assert decision.assignee == "owners"
    assert decision.options[0]["action"] == "fresh_restart"


_CHANGES_H1 = {"verdict": "changes_requested", "head_sha": H1, "by": "luka", "at": 1}


def _claim_pr(**fields: Any) -> FactoryPr:
    base: dict[str, Any] = {
        "id": "p" * 32,
        "factory_id": "f" * 32,
        "repo": REPO,
        "number": 9724,
        "url": "u",
        "head_sha": H1,
        "state": "open",
        "gates": {"draft": "pass", "checks": "pending", "review": "pass", "mergeable": "pass"},
    }
    return FactoryPr(**{**base, **fields})


@pytest.mark.parametrize(
    ("fields", "head", "expected"),
    [
        ({}, H1, ("early", ["checks"])),
        ({}, H1[:7], ("early", ["checks"])),
        (
            {"gates": {"checks": "pass", "mergeable": "pending", "verify": "pending"}},
            None,
            ("early", ["verify", "mergeable"]),
        ),
        ({"gates": {}}, H1, ("early", [])),
        ({"gates": {"checks": "fail", "review": "pending"}}, H1, ("refuted", ["checks"])),
        ({"gates": {"checks": "pass", "demo": "fail"}}, H1, ("refuted", ["demo"])),
        ({"gates": {"draft": "pending", "checks": "pending"}}, H1, ("refuted", ["draft"])),
        ({"is_draft": True}, H1, ("refuted", ["draft"])),
        ({}, H2, ("refuted", ["head"])),
        ({"state": "closed"}, H1, ("refuted", ["state"])),
        ({"ready": True, "gates": {"checks": "pass"}}, H2, ("held", [])),
        ({"human_review": _CHANGES_H1, "gates": {}}, H1, ("refuted", ["changes requested"])),
        ({"human_review": _CHANGES_H1, "ready": True}, H1, ("refuted", ["changes requested"])),
        ({"human_review": _CHANGES_H1}, H2, ("refuted", ["head"])),
        ({"human_review": _CHANGES_H1, "head_sha": H2}, H2, ("early", ["checks"])),
    ],
)
def test_a_ready_claim_is_held_early_or_refuted(
    fields: dict[str, Any], head: str | None, expected: tuple[str, list[str]]
) -> None:
    assert loops.claim_status(_claim_pr(**fields), head) == expected


async def test_an_early_claim_uses_no_attempt_and_keeps_the_streak(cockpit: Cockpit) -> None:
    await plan(cockpit, "route")
    await start(cockpit, "route")
    pr = make_pr(cockpit, "route")
    pl = cockpit.world.id("pl")

    async def claim(status: str, gates: list[str]) -> None:
        await emit(
            cockpit,
            "pr.claim",
            pr_id=pr.id,
            claim="ready_for_human_merge",
            head_sha=H1,
            claim_mismatch=True,
            claim_status=status,
            claim_gates=gates,
        )

    await claim("refuted", ["checks"])
    for _ in range(3):
        await claim("early", ["checks"])
    row = loop_of(cockpit, "claim", loops.pr_subject(pr.id))
    assert (row.state, row.iteration, row.spent["streak"]) == ("running", 1, 1)
    assert (row.last_verdict, row.last_reason) == ("EARLY", "CI pending on aaaaaaa")
    assert row.spent["early"] == 3 and row.spent["claimed_head"] == H1[:12]
    waits = [t for t in cockpit.mailer.texts_to(pl) if t.startswith("[Factory loop · WAIT")]
    assert waits == [
        "[Factory loop · WAIT · PR #9631 · CI pending on aaaaaaa] Your READY claim came early: "
        "no gate failed, so it uses no claim attempt. Do not claim again; the server marks the "
        "PR ready and sends READY FOR HUMAN MERGE when the gates pass."
    ]
    assert kinds(cockpit).count("loop.waiting") == 1
    # The second refuted claim in a row (the early ones between do not reset it) exhausts it.
    await claim("refuted", ["checks"])
    exhausted = (
        "exhausted",
        2,
        "2 READY claims in a row the server's gates or Sentinel refuted",
    )
    row = loop_of(cockpit, "claim", loops.pr_subject(pr.id))
    assert (row.state, row.spent["streak"], row.last_reason) == exhausted
    # Held on its decision: a later early claim is counted, nothing else changes.
    await claim("early", ["checks"])
    row = loop_of(cockpit, "claim", loops.pr_subject(pr.id))
    assert (row.state, row.spent["streak"], row.last_reason) == exhausted
    assert row.spent["early"] == 4
    assert len([t for t in cockpit.mailer.texts_to(pl) if "[Factory loop · WAIT" in t]) == 1


async def test_an_early_claim_mails_nothing_in_observe_mode(cockpit: Cockpit) -> None:
    set_mode(cockpit, "observe")
    await plan(cockpit, "route")
    pr = make_pr(cockpit, "route")
    await emit(
        cockpit,
        "pr.claim",
        pr_id=pr.id,
        claim="ready_for_human_merge",
        head_sha=H1,
        claim_mismatch=True,
        claim_status="early",
        claim_gates=["checks"],
    )
    assert loop_of(cockpit, "claim", loops.pr_subject(pr.id)).last_verdict == "EARLY"
    assert "loop.waiting" in kinds(cockpit)
    assert not any("[Factory loop" in t for t in cockpit.mailer.texts_to(cockpit.world.id("pl")))


# ── L6 claim end to end: the PR route, the poller, the detectors ──


@pytest.fixture()
def watched() -> Iterator[None]:
    """The detectors run on every event too (``claim.mismatch``, ``loop.*`` incidents)."""
    detectors.reset()
    issues.reset()
    nudge.reset()
    remove = detectors.install()
    try:
        yield
    finally:
        remove()
        detectors.reset()
        issues.reset()
        nudge.reset()


def _github_pr(*checks: FakeCheck) -> FakePr:
    """PR #9724 at H1: approved, no env, its required check as given."""
    now = time.time()
    fake = FakePr(
        REPO,
        9724,
        f"factory/{SLUG}/route",
        H1,
        now - 900,
        base_ref="temp-main",
        labels=["factory:no-env"],
        checks=list(checks),
    )
    fake.comment(f"<!-- factory:review sha={H1} verdict=APPROVE -->", now - 60)
    return fake


async def _poll(cockpit: Cockpit, fake: FakePr) -> FactoryPr:
    """One GitHub poller refresh of *fake*."""
    row = cockpit.store.get_pr_by_number(cockpit.fid, REPO, fake.number)
    if row is None:
        row, _ = cockpit.store.upsert_pr(
            cockpit.fid, REPO, fake.number, project_slug=SLUG, head_ref=fake.head_ref
        )
    [scope] = [s for s in prs.active_factories(cockpit.deps) if s.factory_id == cockpit.fid]
    updated = await prs.apply_refresh(
        cockpit.app.state, row, fake.graphql(), now=int(time.time()), scope=scope
    )
    assert updated is not None
    return updated


async def _claim_ready(cockpit: Cockpit, fake: FakePr) -> dict[str, Any]:
    """The PL's ``sys_factory_pr(claim="ready_for_human_merge", head_sha=…)``."""
    body = {
        "from_session_id": cockpit.world.id("pl"),
        "url": fake.url,
        "claim": "ready_for_human_merge",
        "head_sha": fake.head_sha,
    }
    resp = await cockpit.post("/v1/factory/agent/prs", body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _issue_rules(cockpit: Cockpit) -> list[str]:
    return [i.rule for i in cockpit.store.list_issues(cockpit.fid, limit=100)]


@pytest.mark.usefixtures("watched")
async def test_a_claim_while_ci_runs_waits_and_the_pr_turns_ready_without_another(
    cockpit: Cockpit,
) -> None:
    fake = _github_pr(FakeCheck("tests-rollout-scheduled", status="IN_PROGRESS"))
    pr = await _poll(cockpit, fake)
    assert (pr.ready, (pr.gates or {}).get("checks")) == (False, "pending")
    pl = cockpit.world.id("pl")

    for _ in range(3):
        body = await _claim_ready(cockpit, fake)
        assert (body["ready"], body["claim_mismatch"]) == (False, False)
        assert (body["claim_status"], body["claim_gates"]) == ("early", ["checks"])
    row = loop_of(cockpit, "claim", loops.pr_subject(pr.id))
    # No claim attempt used, nothing escalated, the PL told once.
    assert (row.state, row.iteration, row.spent.get("streak") or 0) == ("running", 0, 0)
    assert loop_decisions(cockpit) == []
    assert not {"claim.mismatch", "loop.exhausted"} & set(_issue_rules(cockpit))
    waits = [t for t in cockpit.mailer.texts_to(pl) if t.startswith("[Factory loop · WAIT")]
    assert len(waits) == 1
    assert waits[0].startswith("[Factory loop · WAIT · PR #9724 · CI pending on aaaaaaa]")
    assert "Pending: pending checks: tests-rollout-scheduled" in waits[0]
    claims = len(cockpit.events("pr.claim"))

    # CI goes green: the gates move the PR to ready, no new claim needed.
    fake.checks = [FakeCheck("tests-rollout-scheduled")]
    pr = await _poll(cockpit, fake)
    assert pr.ready
    assert len(cockpit.events("pr.claim")) == claims
    assert [e.kind for e in cockpit.events("pr.ready")] == ["pr.ready"]
    assert any("READY FOR HUMAN MERGE" in t for t in cockpit.mailer.texts_to(pl))
    row = loop_of(cockpit, "claim", loops.pr_subject(pr.id))
    assert (row.state, row.iteration, row.last_verdict) == ("running", 0, "CLAIMED")
    assert (row.spent["waiting"], row.spent["waiting_since"]) == (None, None)
    assert row.last_reason == "gates green at aaaaaaa: the early READY claim held"
    step = pipeline.next_step(
        pr, pr.gates or {}, cockpit.store.list_loops(cockpit.fid, pr_id=pr.id), None, [], None
    )
    assert step.name == pipeline.ASK_SENTINEL
    assert loop_decisions(cockpit) == []
    assert "loop.exhausted" not in _issue_rules(cockpit)


@pytest.mark.usefixtures("watched")
async def test_a_claim_on_failed_ci_is_refuted_and_counted(cockpit: Cockpit) -> None:
    fake = _github_pr(FakeCheck("tests-rollout-scheduled", conclusion="FAILURE"))
    pr = await _poll(cockpit, fake)
    for _ in range(2):
        body = await _claim_ready(cockpit, fake)
        assert (body["claim_status"], body["claim_gates"]) == ("refuted", ["checks"])
    row = loop_of(cockpit, "claim", loops.pr_subject(pr.id))
    assert (row.state, row.iteration, row.spent["streak"]) == ("exhausted", 2, 2)
    assert row.last_verdict == "MISMATCH"
    [decision] = loop_decisions(cockpit)
    assert decision.options[0]["action"] == "fresh_restart"
    assert {"claim.mismatch", "loop.exhausted"} <= set(_issue_rules(cockpit))
    pl = cockpit.world.id("pl")
    assert not any(t.startswith("[Factory loop · WAIT") for t in cockpit.mailer.texts_to(pl))


@pytest.mark.usefixtures("watched")
async def test_an_early_claim_is_refuted_when_its_check_then_fails(cockpit: Cockpit) -> None:
    """CI goes red at the head an early claim waits on: it counts after all, as before."""
    fake = _github_pr(FakeCheck("tests-rollout-scheduled", status="IN_PROGRESS"))
    pr = await _poll(cockpit, fake)
    pl = cockpit.world.id("pl")
    for i, head in enumerate((H1, H2)):
        if i:
            now = time.time()
            fake.push(
                head, now - 100, [FakeCheck("tests-rollout-scheduled", status="IN_PROGRESS")]
            )
            fake.comment(f"<!-- factory:review sha={head} verdict=APPROVE -->", now - 50)
            pr = await _poll(cockpit, fake)
        body = await _claim_ready(cockpit, fake)
        assert (body["claim_status"], body["claim_mismatch"]) == ("early", False)
        row = loop_of(cockpit, "claim", loops.pr_subject(pr.id))
        assert (row.last_verdict, row.spent["waiting"]) == ("EARLY", head[:7])
        fake.checks = [FakeCheck("tests-rollout-scheduled", conclusion="FAILURE")]
        pr = await _poll(cockpit, fake)
        assert (pr.gates or {}).get("checks") == "fail"
        row = loop_of(cockpit, "claim", loops.pr_subject(pr.id))
        assert (row.iteration, row.spent["streak"], row.last_verdict) == (i + 1, i + 1, "MISMATCH")
        assert (row.spent["waiting"], row.spent["waiting_since"]) == (None, None)
        if not i:
            assert (row.state, row.last_reason) == (
                "running",
                "READY claim at aaaaaaa refuted: checks",
            )
    assert (row.state, row.last_reason) == (
        "exhausted",
        "2 READY claims in a row the server's gates or Sentinel refuted",
    )
    [decision] = loop_decisions(cockpit)
    assert decision.options[0]["action"] == "fresh_restart"
    assert {"claim.mismatch", "loop.exhausted"} <= set(_issue_rules(cockpit))
    waits = [t for t in cockpit.mailer.texts_to(pl) if t.startswith("[Factory loop · WAIT")]
    assert [w.split("]", 1)[0] for w in waits] == [
        "[Factory loop · WAIT · PR #9724 · CI pending on aaaaaaa",
        "[Factory loop · WAIT · PR #9724 · CI pending on bbbbbbb",
    ]
    # A third early claim on the exhausted loop only counts: no WAIT, no new decision.
    fake.push(H3, time.time(), [FakeCheck("tests-rollout-scheduled", status="IN_PROGRESS")])
    await _poll(cockpit, fake)
    assert (await _claim_ready(cockpit, fake))["claim_status"] == "early"
    row = loop_of(cockpit, "claim", loops.pr_subject(pr.id))
    assert (row.state, row.spent["early"], row.spent.get("waiting")) == ("exhausted", 3, None)
    assert len(loop_decisions(cockpit)) == 1
    assert len([t for t in cockpit.mailer.texts_to(pl) if "[Factory loop · WAIT" in t]) == 2


async def _early(cockpit: Cockpit, pr: FactoryPr, head: str, gates: list[str]) -> None:
    await emit(
        cockpit,
        "pr.claim",
        pr_id=pr.id,
        claim="ready_for_human_merge",
        head_sha=head,
        claim_mismatch=True,
        claim_status="early",
        claim_gates=gates,
    )


@pytest.mark.parametrize(
    ("name", "to", "evidence"),
    [
        ("review", "fail", {"verdict": "CHANGES", "sha": H1, "at": 1, "blocking": 1}),
        ("verify", "fail", {"sha": H1, "at": 1, "reason": "code: 500 on /status"}),
        ("demo", "fail", {}),
        ("mergeable", "fail", {}),
        ("draft", "pending", {}),
    ],
)
async def test_any_gate_failing_at_the_waited_head_refutes_the_early_claim(
    cockpit: Cockpit, name: str, to: str, evidence: dict[str, Any]
) -> None:
    await plan(cockpit, "route")
    pr = make_pr(cockpit, "route")
    await gate(cockpit, pr, "checks", "pass", H1)
    if name != "draft":
        await gate(cockpit, pr, name, "pending", H1)
    await _early(cockpit, pr, H1, [name])
    assert loop_of(cockpit, "claim", loops.pr_subject(pr.id)).spent["waiting"] == "aaaaaaa"
    await gate(cockpit, pr, name, to, H1, **evidence)
    row = loop_of(cockpit, "claim", loops.pr_subject(pr.id))
    assert (row.iteration, row.spent["streak"], row.last_verdict) == (1, 1, "MISMATCH")
    assert row.last_reason == f"READY claim at aaaaaaa refuted: {name}"
    assert row.spent["waiting"] is None
    [iteration] = [e for e in cockpit.events("loop.iteration") if e.data["kind"] == "claim"]
    assert (iteration.data["late"], iteration.data["refuted"]) == (True, [name])


async def test_a_human_change_request_lapses_the_early_claim_it_outran(
    cockpit: Cockpit,
) -> None:
    """A human asks for changes while an early claim waits on CI: no strike for the PL."""
    await plan(cockpit, "route")
    pr = make_pr(cockpit, "route")
    await gate(cockpit, pr, "checks", "pending", H1)
    await _early(cockpit, pr, H1, ["checks"])
    assert loop_of(cockpit, "claim", loops.pr_subject(pr.id)).spent["waiting"] == "aaaaaaa"
    cockpit.store.update_pr(pr.id, human_review=_CHANGES_H1)
    await gate(cockpit, pr, "checks", "pass", H1)
    row = loop_of(cockpit, "claim", loops.pr_subject(pr.id))
    assert (row.iteration, row.spent.get("streak") or 0, row.last_verdict) == (0, 0, "LAPSED")
    assert row.last_reason == "changes requested at aaaaaaa: the READY claim lapsed"
    assert row.spent["waiting"] is None


async def test_a_claim_judged_on_stale_gates_is_judged_again_when_observed(
    cockpit: Cockpit,
) -> None:
    """The route said early, but CI failed before the loop saw the claim: refuted, no WAIT."""
    await plan(cockpit, "route")
    pr = make_pr(cockpit, "route")
    await gate(cockpit, pr, "checks", "fail", H1)
    await _early(cockpit, pr, H1, ["checks"])
    row = loop_of(cockpit, "claim", loops.pr_subject(pr.id))
    assert (row.iteration, row.spent["streak"], row.last_verdict) == (1, 1, "MISMATCH")
    assert row.spent["early"] == 1 and row.spent.get("waiting") is None
    assert "loop.waiting" not in kinds(cockpit)


async def test_a_new_head_or_a_closed_pr_lapses_the_wait_without_counting(
    cockpit: Cockpit,
) -> None:
    await plan(cockpit, "route")
    pr = make_pr(cockpit, "route")
    pl = cockpit.world.id("pl")
    await gate(cockpit, pr, "checks", "pending", H1)
    await _early(cockpit, pr, H1, ["checks"])
    # The IC merges the base in: CI restarts on H2, the claim at H1 lapses.
    await gate(cockpit, pr, "checks", "pending", H2)
    row = loop_of(cockpit, "claim", loops.pr_subject(pr.id))
    assert (row.state, row.iteration, row.spent.get("streak") or 0) == ("running", 0, 0)
    assert (row.last_verdict, row.last_reason) == (
        "LAPSED",
        "head moved past aaaaaaa: the READY claim lapsed",
    )
    assert row.spent["waiting"] is None
    # An early claim at the new head waits on it and says so once more.
    await _early(cockpit, pr, H2, ["checks"])
    assert loop_of(cockpit, "claim", loops.pr_subject(pr.id)).spent["waiting"] == "bbbbbbb"
    waits = [t for t in cockpit.mailer.texts_to(pl) if t.startswith("[Factory loop · WAIT")]
    assert len(waits) == 2 and "CI pending on bbbbbbb" in waits[1]
    # Closed without merge: the tick lapses the wait too.
    cockpit.store.update_pr(pr.id, state="closed")
    members = [cockpit.world.get("lead_a"), cockpit.world.get("pl")]
    await loops.tick(cockpit.app.state, members, [], now=time.time())
    row = loop_of(cockpit, "claim", loops.pr_subject(pr.id))
    assert (row.state, row.iteration, row.last_verdict) == ("running", 0, "LAPSED")
    assert row.last_reason == "PR closed: the READY claim at bbbbbbb lapsed"


async def test_an_early_claim_seen_after_the_pr_turned_ready_holds_at_once(
    cockpit: Cockpit,
) -> None:
    """The poller won the race (pr.ready first): no wait is left behind, no WAIT mail."""
    await plan(cockpit, "route")
    pr = make_pr(cockpit, "route")
    pl = cockpit.world.id("pl")
    cockpit.store.update_pr(pr.id, ready=True, gates={"checks": "pass", "draft": "pass"})
    await emit(cockpit, "pr.ready", pr_id=pr.id)
    await _early(cockpit, pr, H1, ["checks"])
    row = loop_of(cockpit, "claim", loops.pr_subject(pr.id))
    assert (row.state, row.last_verdict, row.spent.get("waiting")) == ("running", "CLAIMED", None)
    assert "loop.waiting" not in kinds(cockpit)
    # Sentinel verifies H1; the base moves on (mergeable recomputes): a re-claim waits and says so.
    await emit(cockpit, "pr.claim", pr_id=pr.id, sentinel_verdict="verified", head_sha=H1)
    assert loop_of(cockpit, "claim", loops.pr_subject(pr.id)).state == "converged"
    cockpit.store.update_pr(pr.id, ready=False, gates={"checks": "pass", "mergeable": "pending"})
    await _early(cockpit, pr, H1, ["mergeable"])
    row = loop_of(cockpit, "claim", loops.pr_subject(pr.id))
    assert (row.state, row.last_verdict, row.spent["waiting"]) == ("running", "EARLY", "aaaaaaa")
    waits = [t for t in cockpit.mailer.texts_to(pl) if t.startswith("[Factory loop · WAIT")]
    assert [w.split("]", 1)[0] for w in waits] == [
        "[Factory loop · WAIT · PR #9631 · mergeable pending on aaaaaaa"
    ]
    # Sentinel verifies again: the loop converges and keeps no wait.
    await emit(cockpit, "pr.claim", pr_id=pr.id, sentinel_verdict="verified", head_sha=H1)
    row = loop_of(cockpit, "claim", loops.pr_subject(pr.id))
    assert (row.state, row.spent["waiting"], row.spent["waiting_since"]) == (
        "converged",
        None,
        None,
    )


@pytest.mark.usefixtures("watched")
async def test_a_wait_on_a_hung_check_stalls_and_keep_waiting_raises_wait_h(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Implement converged on an earlier head, CI hangs on this one: a bounded wait, a decision."""
    clock = FakeClock()
    monkeypatch.setattr(loops, "_clock", clock)
    await plan(cockpit, "route")
    await start(cockpit, "route")
    pr = make_pr(cockpit, "route")
    pl = cockpit.world.id("pl")
    members = [cockpit.world.get("lead_a"), cockpit.world.get("lead_b"), cockpit.world.get("pl")]
    await gate(cockpit, pr, "checks", "pending", H1)
    await gate(cockpit, pr, "checks", "pass", H1)
    assert implement(cockpit, "route").state == "converged"
    await gate(cockpit, pr, "checks", "pending", H2)
    await _early(cockpit, pr, H2, ["checks"])
    subject = loops.pr_subject(pr.id)
    await loops.tick(cockpit.app.state, members, [], now=clock.advance(3600))
    # Another early claim mid-wait: no new mail, the wait still counts from the first.
    await _early(cockpit, pr, H2, ["checks"])
    assert loop_of(cockpit, "claim", subject).state == "running"
    await loops.tick(cockpit.app.state, members, [], now=clock.advance(3600))
    row = loop_of(cockpit, "claim", subject)
    assert (row.state, row.last_reason) == ("stalled", "READY claim at bbbbbbb waited 2 h on CI")
    assert (row.iteration, row.spent["early"]) == (0, 2)
    [decision] = loop_decisions(cockpit)
    assert [o["label"] for o in decision.options] == ["Keep waiting", "Pause project", "Dismiss"]
    assert decision.options[0]["recommended"]
    assert decision.question == "PR #9631: claim 0/2 stalled - what next?"
    assert "loop.stalled" in _issue_rules(cockpit)
    assert cockpit.mailer.texts_to(pl)[-1] == (
        f"[Factory loop · STALLED · PR #9631 · claim 0/2 · {decision.ref}] PR #9631 still "
        "waits on its gates: re-run a check that hung, do not claim READY again; the server "
        "sends READY FOR HUMAN MERGE when they pass.\n"
        "Why: READY claim at bbbbbbb waited 2 h on CI. Keep the rest of the project moving."
    )
    assert len([t for t in cockpit.mailer.texts_to(pl) if "[Factory loop · WAIT" in t]) == 1

    await decisions.answer_decision(
        cockpit.app.state, decision.id, user_id="carol", option_id="wait"
    )
    row = loop_of(cockpit, "claim", subject)
    assert (row.state, row.spec["wait_h"], row.spent["waiting"]) == ("running", 4, "bbbbbbb")
    assert cockpit.mailer.texts_to(pl)[-1] == (
        f"[Factory loop · BUDGET RAISED · T-1 · claim wait_h 2 h→4 h by carol · "
        f"{decision.ref}] Keep waiting for the gates of PR #9631; do not claim READY again: "
        "the server sends READY FOR HUMAN MERGE when they pass."
    )
    await loops.tick(cockpit.app.state, members, [], now=clock.advance(3600))
    assert loop_of(cockpit, "claim", subject).state == "running"
    await loops.tick(cockpit.app.state, members, [], now=clock.advance(3600))
    row = loop_of(cockpit, "claim", subject)
    assert (row.state, row.last_reason) == ("stalled", "READY claim at bbbbbbb waited 4 h on CI")
    [again] = [d for d in loop_decisions(cockpit) if d.status == "open"]
    assert again.id != decision.id
    # The check passes at last: the PR turns ready, the claim held, the stall ends unanswered.
    cockpit.store.update_pr(pr.id, ready=True, gates={"checks": "pass"})
    await emit(cockpit, "pr.ready", pr_id=pr.id)
    row = loop_of(cockpit, "claim", subject)
    assert (row.state, row.last_verdict, row.spent["waiting"]) == ("running", "CLAIMED", None)
    assert cockpit.store.get_decision(again.id).status == "expired"
    assert cockpit.mailer.texts_to(pl)[-1].startswith(
        f"[Factory loop · RESUMED · PR #9631 · claim 0/2 · {again.ref}]"
    )
    assert "loop.stalled" not in [
        i.rule for i in cockpit.store.list_issues(cockpit.fid, limit=100) if i.status == "open"
    ]


async def test_a_paused_project_does_not_stall_a_wait(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.factory import LABEL_STATE

    clock = FakeClock()
    monkeypatch.setattr(loops, "_clock", clock)
    await plan(cockpit, "route")
    pr = make_pr(cockpit, "route")
    await gate(cockpit, pr, "checks", "pending", H1)
    await _early(cockpit, pr, H1, ["checks"])
    pl = cockpit.world.get("pl")
    cockpit.world.store.set_labels(pl.id, {LABEL_STATE: "paused"})
    members = [cockpit.world.get("lead_a"), cockpit.world.get("pl")]
    await loops.tick(cockpit.app.state, members, [], now=clock.advance(3 * 3600))
    assert loop_of(cockpit, "claim", loops.pr_subject(pr.id)).state == "running"
    cockpit.world.store.set_labels(pl.id, {LABEL_STATE: "active"})
    await loops.tick(cockpit.app.state, members, [], now=clock.advance(60))
    row = loop_of(cockpit, "claim", loops.pr_subject(pr.id))
    assert (row.state, row.last_reason) == ("stalled", "READY claim at aaaaaaa waited 3 h on CI")


async def test_a_stalled_wait_that_ends_refuted_swaps_its_decision(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock()
    monkeypatch.setattr(loops, "_clock", clock)
    await plan(cockpit, "route")
    pr = make_pr(cockpit, "route")
    pl = cockpit.world.id("pl")
    members = [cockpit.world.get("lead_a"), cockpit.world.get("pl")]
    await emit(
        cockpit,
        "pr.claim",
        pr_id=pr.id,
        claim="ready_for_human_merge",
        head_sha=H1,
        claim_mismatch=True,
        claim_status="refuted",
        claim_gates=["checks"],
    )
    await gate(cockpit, pr, "checks", "pending", H1)
    await _early(cockpit, pr, H1, ["checks"])
    await loops.tick(cockpit.app.state, members, [], now=clock.advance(2 * 3600))
    [wait] = loop_decisions(cockpit)
    assert loop_of(cockpit, "claim", loops.pr_subject(pr.id)).state == "stalled"
    # The hung check finally fails: the second refuted claim in a row exhausts the loop.
    await gate(cockpit, pr, "checks", "fail", H1)
    row = loop_of(cockpit, "claim", loops.pr_subject(pr.id))
    assert (row.state, row.iteration, row.spent["streak"]) == ("exhausted", 2, 2)
    assert cockpit.store.get_decision(wait.id).status == "expired"
    [exhausted] = [d for d in loop_decisions(cockpit) if d.status == "open"]
    assert exhausted.options[0]["action"] == "fresh_restart"
    assert row.decision_id == exhausted.id
    assert cockpit.mailer.texts_to(pl)[-1].startswith(
        f"[Factory loop · EXHAUSTED · PR #9631 · claim 2/2 · {exhausted.ref}] Hold PR #9631"
    )


# ── Wall and no-progress (fake clock) ──────────────────────────


async def test_no_progress_and_wall_stall_on_a_fake_clock(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock()
    monkeypatch.setattr(loops, "_clock", clock)
    t0 = clock()
    await plan(cockpit, "route", "docs")
    await start(cockpit, "route")
    await start(cockpit, "docs")
    members = [cockpit.world.get("lead_a"), cockpit.world.get("lead_b"), cockpit.world.get("pl")]
    await loops.tick(cockpit.app.state, members, [], now=clock.advance(600))
    assert implement(cockpit, "route").state == "running"
    # docs pushes a new head after half an hour; route does nothing.
    pr = make_pr(cockpit, "docs", number=9700)
    clock.advance(1200)
    await gate(cockpit, pr, "checks", "pending", H2)
    await loops.tick(cockpit.app.state, members, [], now=clock.advance(1900))
    route = implement(cockpit, "route")
    assert route.state == "stalled" and route.last_reason == "no progress for 61 min"
    assert implement(cockpit, "docs").state == "running"
    decision = next(d for d in loop_decisions(cockpit) if "route" in d.question)
    assert decision.options[0]["label"] == "Keep waiting"
    assert cockpit.mailer.texts_to(cockpit.world.id("pl"))[-1].startswith(
        f"[Factory loop · STALLED · T-1 route · implement 1/3 · {decision.ref}]"
    )
    # Progress after the stall resumes the loop.
    await emit(
        cockpit,
        "task.state",
        task_id=task(cockpit, "route").id,
        result="PASS",
        **{"from": "running", "to": "running"},
    )
    assert implement(cockpit, "route").state == "running"
    # Unanswered, the stall's decision closes and the PL may carry on.
    assert cockpit.store.get_decision(decision.id).status == "expired"
    assert cockpit.events("loop.resumed")[-1].data["loop_id"] == implement(cockpit, "route").id
    assert cockpit.mailer.texts_to(cockpit.world.id("pl"))[-1].startswith(
        f"[Factory loop · RESUMED · T-1 route · implement 1/3 · {decision.ref}]"
    )
    # The wall budget (8 h) stalls a loop that keeps moving but never lands.
    clock.now = t0 + 8 * 3600
    await gate(cockpit, pr, "checks", "pending", H3)
    await loops.tick(cockpit.app.state, members, [], now=clock.advance(60))
    docs = implement(cockpit, "docs")
    assert docs.state == "stalled" and docs.last_reason == "running 8 h (wall budget 8 h)"
    assert docs.spent["wall_s"] == 8 * 3600 + 60
    # Progress does not give the time back: it stays stalled on its open decision.
    wall = next(d for d in loop_decisions(cockpit) if "docs" in d.question)
    await emit(
        cockpit,
        "task.state",
        task_id=task(cockpit, "docs").id,
        result="PASS",
        **{"from": "running", "to": "running"},
    )
    assert implement(cockpit, "docs").state == "stalled"
    assert cockpit.store.get_decision(wall.id).status == "open"


async def test_the_tick_mirrors_the_engine_mode_onto_project_leads(cockpit: Cockpit) -> None:
    set_mode(cockpit, "enforce")
    members = [cockpit.world.get("lead_a"), cockpit.world.get("pl")]
    await loops.tick(cockpit.app.state, members, [], now=time.time())
    assert cockpit.world.get("pl").labels[loops.ENGINE_MODE_LABEL] == "enforce"
    assert loops.ENGINE_MODE_LABEL not in cockpit.world.get("lead_b").labels


async def test_a_new_project_lead_gets_the_engine_mode_at_once(cockpit: Cockpit) -> None:
    """Without it the guard would let enforce-mode dispatches through until the next tick."""
    from omnigent.server.factory.people import mirror_member_on_create

    set_mode(cockpit, "enforce")
    assert loops.ENGINE_MODE_LABEL not in cockpit.world.get("pl").labels
    await mirror_member_on_create(cockpit.app.state, cockpit.world.get("pl"))
    assert cockpit.world.get("pl").labels[loops.ENGINE_MODE_LABEL] == "enforce"
    await mirror_member_on_create(cockpit.app.state, cockpit.world.get("lead_b"))
    assert loops.ENGINE_MODE_LABEL not in cockpit.world.get("lead_b").labels


# ── L7 plan: done check ────────────────────────────────────────


async def test_a_project_whose_tasks_all_closed_asks_mark_done(cockpit: Cockpit) -> None:
    await plan(cockpit, "route")
    await start(cockpit, "route")
    await tasks.close_task(cockpit.app.state, cockpit.fid, SLUG, "route", "done", _pl(cockpit))
    members = [cockpit.world.get("lead_a"), cockpit.world.get("pl")]
    t0 = time.time()
    await loops.tick(cockpit.app.state, members, [], now=t0)
    plan_loop = loop_of(cockpit, "plan", loops.project_subject(SLUG))
    assert plan_loop.state == "converged"
    assert loop_decisions(cockpit) == []
    await loops.tick(cockpit.app.state, members, [], now=t0 + loops.DONE_CHECK_GRACE_S + 1)
    found = loop_decisions(cockpit)
    assert len(found) == 1 and found[0].source == "done_check"
    await decisions.answer_decision(
        cockpit.app.state, found[0].id, user_id="carol", option_id="done"
    )
    text = cockpit.mailer.texts_to(cockpit.world.id("pl"))[-1]
    assert "run DONE now" in text


# ── Views and the route ────────────────────────────────────────


async def test_loops_route_and_snapshot_fields(cockpit: Cockpit) -> None:
    await plan(cockpit, "route")
    await start(cockpit, "route")
    pr = make_pr(cockpit, "route")
    await gate(cockpit, pr, "checks", "fail", H1, failing_names=["lint"])
    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/loops", headers={"X-Test-User": "alice"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["engine_mode"] == "assist"
    by_kind = {row["kind"]: row for row in body["data"]}
    assert by_kind["implement"]["task_ref"] == "T-1" and by_kind["implement"]["limit"] == 3
    assert by_kind["ci"]["pr_number"] == 9631 and by_kind["ci"]["state"] == "running"
    assert set(by_kind["implement"]["spent"]) <= {
        "attempts",
        "usd",
        "wall_s",
        "per_head",
        "env_retries",
    }
    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/loops?state=converged",
        headers={"X-Test-User": "alice"},
    )
    assert resp.json()["data"] == []
    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/loops?state=nope", headers={"X-Test-User": "alice"}
    )
    assert resp.status_code == 400
    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/loops", headers={"X-Test-User": "bob"}
    )
    assert resp.status_code in (403, 404)

    summaries, chips = loops.snapshot_fields(
        cockpit.deps, cockpit.fid, [cockpit.store.get_pr(pr.id)]
    )
    assert summaries[SLUG]["running"] == 2
    assert [c["kind"] for c in chips[pr.id.replace("-", "")]] == ["implement", "ci"]


async def test_a_human_changes_request_grants_its_own_try(cockpit: Cockpit) -> None:
    """A cockpit review names its PR by subject only; the rework never exhausts the loop."""
    await plan(cockpit, "route")
    await start(cockpit, "route")
    pr = make_pr(cockpit, "route")
    await gate(cockpit, pr, "checks", "pass", H1)
    assert implement(cockpit, "route").state == "converged"
    for _ in range(3):
        await events.emit(
            cockpit.app.state,
            cockpit.fid,
            "pr.human_review",
            project=SLUG,
            subject=f"pr:{REPO}#9631",
            summary="mia requested changes",
            data={"verdict": "changes_requested", "by": "mia (operator)", "source": "cockpit"},
        )
    row = implement(cockpit, "route")
    assert (row.state, row.iteration, loops.limit_of(row)) == ("running", 4, 6)
    assert row.last_verdict == "HUMAN CHANGES"
    assert loop_decisions(cockpit) == []


async def test_task_views_carry_their_implement_loop(cockpit: Cockpit) -> None:
    await plan(cockpit, "route", "docs")
    await start(cockpit, "route")
    body = tasks.list_view(cockpit.deps, cockpit.fid, SLUG)
    by_key = {t["key"]: t for t in body["tasks"]}
    assert by_key["route"]["loop"] == {
        "id": implement(cockpit, "route").id,
        "kind": "implement",
        "state": "running",
        "iteration": 1,
        "limit": 3,
    }
    assert by_key["docs"]["loop"] is None


# ── Answered without a loop action, or resolved without an answer ──────────


async def exhaust(cockpit: Cockpit, key: str) -> FactoryLoop:
    """Three failed tries: the implement loop of *key* is exhausted with an open decision."""
    for n in range(3):
        if n:
            await start(cockpit, key)
        await emit(
            cockpit,
            "task.state",
            task_id=task(cockpit, key).id,
            result="FAIL",
            note="red",
            **{"from": "running", "to": "running"},
        )
    row = implement(cockpit, key)
    assert row.state == "exhausted" and row.decision_id
    return row


def pl_texts(cockpit: Cockpit) -> list[str]:
    return cockpit.mailer.texts_to(cockpit.world.id("pl"))


async def test_a_pause_answer_reopens_the_decision_once_the_project_runs(
    cockpit: Cockpit,
) -> None:
    """Pause project leaves the $ stop in place: on resume a new decision asks again."""
    from omnigent.factory import LABEL_STATE

    await plan(cockpit, "route")
    await start(cockpit, "route")
    cockpit.store.update_task(task(cockpit, "route").id, cost_usd=15.5)
    await emit(
        cockpit,
        "task.state",
        task_id=task(cockpit, "route").id,
        result="PASS",
        **{"from": "running", "to": "running"},
    )
    first = loop_decisions(cockpit)[0]
    assert [o["action"] for o in first.options] == ["raise_budget", "abandon_task", "pause"]
    await decisions.answer_decision(
        cockpit.app.state, first.id, user_id="alice", option_id="pause"
    )
    pl = cockpit.world.get("pl")
    assert pl.labels[LABEL_STATE] == "paused"
    set_mode(cockpit, "enforce")
    # The answered decision is not cited: nothing would ever answer it again.
    with pytest.raises(CockpitError) as raised:
        await start(cockpit, "route")
    assert raised.value.code == "budget_exhausted"
    assert first.ref not in raised.value.message and raised.value.extra["decision"] is None
    assert "$15.50 of $15.00 spent" in raised.value.message

    members = [cockpit.world.get("lead_a"), pl]
    await loops.tick(cockpit.app.state, members, [], now=time.time())
    assert len(loop_decisions(cockpit)) == 1  # still paused: nobody is asked yet
    cockpit.world.store.set_labels(pl.id, {LABEL_STATE: "active"})
    await loops.tick(cockpit.app.state, [cockpit.world.get("lead_a"), pl], [], now=time.time())
    second = next(d for d in loop_decisions(cockpit) if d.id != first.id)
    assert second.status == "open" and implement(cockpit, "route").decision_id == second.id
    assert pl_texts(cockpit)[-1].startswith(
        f"[Factory loop · EXHAUSTED · T-1 route · implement 1/3 · {second.ref}]"
    )
    with pytest.raises(CockpitError) as raised:
        await start(cockpit, "route")
    assert raised.value.extra["decision"] == second.ref
    # The next tick does not ask a third time.
    await loops.tick(cockpit.app.state, [cockpit.world.get("lead_a"), pl], [], now=time.time())
    assert len(loop_decisions(cockpit)) == 2


@pytest.mark.parametrize(("who", "state"), [("pl", "done"), ("root", "paused")])
async def test_a_pause_answer_is_not_asked_again_while_held(
    cockpit: Cockpit, who: str, state: str
) -> None:
    """Enforce from the start: a project that ended, or a paused factory, gets no new decision."""
    from omnigent.factory import LABEL_STATE

    set_mode(cockpit, "enforce")
    await plan(cockpit, "route")
    await start(cockpit, "route")
    cockpit.store.update_task(task(cockpit, "route").id, cost_usd=15.5)
    await emit(
        cockpit,
        "task.state",
        task_id=task(cockpit, "route").id,
        result="PASS",
        **{"from": "running", "to": "running"},
    )
    first = loop_decisions(cockpit)[0]
    await decisions.answer_decision(
        cockpit.app.state, first.id, user_id="alice", option_id="pause"
    )
    pl = cockpit.world.get("pl")
    cockpit.world.store.set_labels(pl.id, {LABEL_STATE: "active"})
    target = pl.id if who == "pl" else cockpit.fid
    cockpit.world.store.set_labels(target, {LABEL_STATE: state})
    members = [cockpit.world.get("lead_a"), cockpit.world.get("pl")]
    await loops.tick(cockpit.app.state, members, [], now=time.time())
    assert [d.id for d in loop_decisions(cockpit)] == [first.id]
    with pytest.raises(CockpitError) as raised:
        await start(cockpit, "route")
    assert raised.value.extra["decision"] is None


async def test_a_dismissed_claim_decision_is_not_asked_again(cockpit: Cockpit) -> None:
    await plan(cockpit, "route")
    await start(cockpit, "route")
    pr = make_pr(cockpit, "route")
    for _ in range(2):
        await emit(
            cockpit,
            "pr.claim",
            pr_id=pr.id,
            claim="ready_for_human_merge",
            head_sha=H1,
            claim_mismatch=True,
        )
    decision = loop_decisions(cockpit)[0]
    await decisions.answer_decision(
        cockpit.app.state, decision.id, user_id="alice", option_id="dismiss"
    )
    members = [cockpit.world.get("lead_a"), cockpit.world.get("pl")]
    await loops.tick(cockpit.app.state, members, [], now=time.time())
    assert len(loop_decisions(cockpit)) == 1
    assert loop_of(cockpit, "claim", loops.pr_subject(pr.id)).state == "exhausted"


async def test_a_claim_fresh_restart_starts_the_claim_loop_over(cockpit: Cockpit) -> None:
    await plan(cockpit, "route")
    await start(cockpit, "route")
    pr = make_pr(cockpit, "route")
    for _ in range(2):
        await emit(
            cockpit,
            "pr.claim",
            pr_id=pr.id,
            claim="ready_for_human_merge",
            head_sha=H1,
            claim_mismatch=True,
        )
    decision = loop_decisions(cockpit)[0]
    await decisions.answer_decision(
        cockpit.app.state, decision.id, user_id="alice", option_id="fresh"
    )
    assert cockpit.store.get_decision(decision.id).status == "answered"
    members = [cockpit.world.get("lead_a"), cockpit.world.get("pl")]
    await loops.tick(cockpit.app.state, members, [], now=time.time())
    row = loop_of(cockpit, "claim", loops.pr_subject(pr.id))
    assert row.state == "running" and row.spent["streak"] == 0
    assert len(loop_decisions(cockpit)) == 1


async def test_a_human_changes_request_lifts_an_exhausted_loop(cockpit: Cockpit) -> None:
    """The person's try is granted: the loop runs, its decision closes, the PL may go on."""
    await plan(cockpit, "route")
    await start(cockpit, "route")
    make_pr(cockpit, "route")
    row = await exhaust(cockpit, "route")
    decision = cockpit.store.get_decision(row.decision_id)
    await events.emit(
        cockpit.app.state,
        cockpit.fid,
        "pr.human_review",
        project=SLUG,
        subject=f"pr:{REPO}#9631",
        summary="mia requested changes",
        data={"verdict": "changes_requested", "by": "mia (operator)", "source": "cockpit"},
    )
    row = implement(cockpit, "route")
    assert (row.state, row.iteration, loops.limit_of(row)) == ("running", 4, 4)
    assert cockpit.store.get_decision(decision.id).status == "expired"
    assert pl_texts(cockpit)[-1] == (
        f"[Factory loop · RESUMED · T-1 route · implement 4/4 · {decision.ref}] "
        f"{decision.ref} is closed and the hold on route is lifted: carry on (a new worker "
        'needs sys_factory_task(op="start", key="route") first).\n'
        "Why: human review: changes requested by mia (operator)."
    )
    set_mode(cockpit, "enforce")
    assert (
        loops.start_refusal(cockpit.deps, cockpit.fid, task(cockpit, "route"), "enforce") is None
    )


async def test_a_task_budget_edit_lifts_an_exhausted_loop(cockpit: Cockpit) -> None:
    await plan(cockpit, "route")
    await start(cockpit, "route")
    row = await exhaust(cockpit, "route")
    decision = cockpit.store.get_decision(row.decision_id)
    set_mode(cockpit, "enforce")
    with pytest.raises(CockpitError) as raised:
        await start(cockpit, "route")
    assert raised.value.extra["decision"] == decision.ref
    # An edit that leaves the budget alone changes nothing.
    await tasks.edit_task(
        cockpit.app.state,
        cockpit.fid,
        "T-1",
        tasks.TaskEditIn(priority=1),
        Actor.human("carol", "operator"),
        "carol (operator)",
    )
    assert implement(cockpit, "route").state == "exhausted"
    await tasks.edit_task(
        cockpit.app.state,
        cockpit.fid,
        "T-1",
        tasks.TaskEditIn(budget=tasks.TaskBudgetIn(attempts=5)),
        Actor.human("carol", "operator"),
        "carol (operator)",
    )
    row = implement(cockpit, "route")
    assert (row.state, loops.limit_of(row)) == ("running", 5)
    assert cockpit.store.get_decision(decision.id).status == "expired"
    raised_event = cockpit.events("loop.budget_raised")[-1]
    assert raised_event.data["via"] == "task_edit"
    assert pl_texts(cockpit)[-1].startswith(
        f"[Factory loop · RESUMED · T-1 route · implement 3/5 · {decision.ref}]"
    )
    assert (await start(cockpit, "route"))["attempt"] == 4


async def test_a_loop_that_ends_by_itself_expires_its_decision(cockpit: Cockpit) -> None:
    await plan(cockpit, "route")
    await start(cockpit, "route")
    row = await exhaust(cockpit, "route")
    await tasks.close_task(cockpit.app.state, cockpit.fid, SLUG, "route", "done", _pl(cockpit))
    assert implement(cockpit, "route").state == "converged"
    assert cockpit.store.get_decision(row.decision_id).status == "expired"
    assert not pl_texts(cockpit)[-1].startswith("[Factory loop · RESUMED")


async def test_an_implement_loop_rides_on_its_review_loops_decision(cockpit: Cockpit) -> None:
    """The review went bad first (thrash); the implement stop later joins its decision."""
    await plan(cockpit, "route")
    cockpit.store.update_task(task(cockpit, "route").id, budget={"attempts": 4})
    await start(cockpit, "route")
    pr = make_pr(cockpit, "route")
    now = int(time.time())
    await review(cockpit, pr, H1, "CHANGES", at=now, blocking=1, findings=["aaaa0001"])
    await review(cockpit, pr, H2, "CHANGES", at=now + 1, blocking=1, findings=["aaaa0002"])
    await review(cockpit, pr, H3, "CHANGES", at=now + 2, blocking=1, findings=["aaaa0001"])
    review_loop = loop_of(cockpit, "review", loops.pr_subject(pr.id))
    assert review_loop.state == "thrashing" and review_loop.decision_id
    assert implement(cockpit, "route").state == "running"
    await emit(
        cockpit,
        "task.state",
        task_id=task(cockpit, "route").id,
        result="FAIL",
        note="red again",
        **{"from": "running", "to": "running"},
    )
    row = implement(cockpit, "route")
    assert row.state == "exhausted" and row.decision_id == review_loop.decision_id
    assert len(loop_decisions(cockpit)) == 1


async def test_the_first_try_opens_its_iteration_span_at_dispatch(cockpit: Cockpit) -> None:
    await plan(cockpit, "route")
    await start(cockpit, "route")
    row = implement(cockpit, "route")
    spans = cockpit.store.list_spans(cockpit.fid, kinds=["loop_iter"])
    first = [s for s in spans if s.loop_id == row.id]
    assert len(first) == 1 and first[0].attempt == 1 and first[0].ended_at is None
    started = first[0].started_at
    await emit(
        cockpit,
        "task.state",
        task_id=task(cockpit, "route").id,
        result="FAIL",
        note="red",
        **{"from": "running", "to": "running"},
    )
    closed = next(
        s
        for s in cockpit.store.list_spans(cockpit.fid, kinds=["loop_iter"])
        if s.loop_id == row.id
    )
    assert closed.outcome == "fail" and closed.started_at == started
    assert closed.ended_at is not None and closed.ended_at >= started
