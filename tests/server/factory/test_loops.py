"""The loop engine: facts become iterations, budgets stop loops, decisions restart them.

Loop transitions are driven by the events the server already records
(``task.state``, ``task.bound``, ``gate.changed``, ``demo.published``, …),
emitted here through the real event log with the loop listener installed.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.entities.factory import FactoryLoop, FactoryPr, FactoryTask
from omnigent.server.factory import (
    budgets,
    decisions,
    events,
    loops,
    pipeline,
    tasks,
)
from omnigent.server.factory.actor import Actor
from omnigent.server.factory.control import CockpitError
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit
from tests.server.factory.conftest import FakeClock

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
    cockpit.world.store.set_labels(cockpit.fid, {tasks.LABEL_ENGINE_MODE: mode})


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
    # The wall budget (8 h) stalls a loop that keeps moving but never lands.
    clock.now = t0 + 8 * 3600
    await gate(cockpit, pr, "checks", "pending", H3)
    await loops.tick(cockpit.app.state, members, [], now=clock.advance(60))
    docs = implement(cockpit, "docs")
    assert docs.state == "stalled" and docs.last_reason == "running 8 h (wall budget 8 h)"
    assert docs.spent["wall_s"] == 8 * 3600 + 60


async def test_the_tick_mirrors_the_engine_mode_onto_project_leads(cockpit: Cockpit) -> None:
    set_mode(cockpit, "enforce")
    members = [cockpit.world.get("lead_a"), cockpit.world.get("pl")]
    await loops.tick(cockpit.app.state, members, [], now=time.time())
    assert cockpit.world.get("pl").labels[loops.ENGINE_MODE_LABEL] == "enforce"
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
