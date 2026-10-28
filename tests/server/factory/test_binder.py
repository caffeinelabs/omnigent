"""The binder: dispatched titles and PR branches tie work to tasks, even unreported.

The title table (``<key>``, ``<key>-r<k>``, ``review-<key>``, ``demo-<key>``,
team names, anything else → an ad-hoc task with ``task.unplanned``), the
supervisor tick hook, and PRs: a ``factory/<slug>/<key>`` branch links its
task, an open PR moves it to review and a human merge closes it.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.entities import Conversation
from omnigent.entities.factory import FactoryTask
from omnigent.server.factory import binder, prs, tasks
from omnigent.server.factory.actor import Actor
from omnigent.server.factory.binder import Binding, parse_title
from omnigent.server.factory.telemetry.spans import task_key
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit
from tests.server.factory.github_fakes import FakePr

SLUG = "api-version"
REPO = "caffeinelabs/app"
KEYS = {"route", "tests", "docs"}


@pytest_asyncio.fixture()
async def cockpit(
    world: Any, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    binder.reset()
    tasks.reset()
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
    binder.reset()
    tasks.reset()


async def _plan(cockpit: Cockpit) -> None:
    items = [
        tasks.TaskIn(key="route", title="GET /version", scope=["services/api/**"]),
        tasks.TaskIn(key="tests", deps=["route"]),
        tasks.TaskIn(key="docs", kind="docs"),
    ]
    actor = Actor.agent(cockpit.world.id("pl"), "project_lead", title="PL · api-version")
    await tasks.upsert_tasks(cockpit.app.state, cockpit.fid, SLUG, items, actor)


def _child(cockpit: Cockpit, title: str, agent: str = "ic") -> Conversation:
    key = f"child-{title}-{agent}"
    return cockpit.world.make_session(
        key, agent="pl", title=f"{agent}:{title}", parent="pl", sub_agent_name=agent
    )


def _task(cockpit: Cockpit, key: str) -> FactoryTask:
    row = cockpit.store.get_task_by_key(cockpit.fid, SLUG, key)
    assert row is not None, key
    return row


# ── The title table ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("title", "agent", "expected"),
    [
        ("ic:route", "ic", Binding("implement", "route", 1)),
        ("ic:route-r1", "ic", Binding("implement", "route", 2)),
        ("ic_codex:route-r2", "ic_codex", Binding("implement", "route", 3)),
        ("reviewer:review-route", "reviewer", Binding("review", "route")),
        ("ic:review-route-r1", "ic", Binding("review", "route")),
        ("demo:demo-route", "demo", Binding("demo", "route")),
        ("env_keeper:env", "env_keeper", Binding("team")),
        ("env_keeper:anything", "env_keeper", Binding("team")),
        ("demo:demo", "demo", Binding("team")),
        ("ic:Fix the login bug", "ic", Binding("implement", "fix-the-login-bug", planned=False)),
        ("ic:surprise-r1", "ic", Binding("implement", "surprise", 2, planned=False)),
        ("reviewer:review-ghost", "reviewer", Binding("review", "ghost", planned=False)),
        ("ic:route:closed:conv_abc", "ic", Binding("implement", "route", 1)),
    ],
)
def test_binder_title_table(title: str, agent: str, expected: Binding) -> None:
    assert parse_title(title, KEYS, agent=agent) == expected


def test_a_task_named_like_a_retry_matches_itself_first() -> None:
    assert parse_title("ic:fix-r2", {"fix-r2", "fix"}, agent="ic") == Binding(
        "implement", "fix-r2"
    )


# ── Sub-agents ─────────────────────────────────────────────────


async def test_implement_workers_bind_and_count_attempts(cockpit: Cockpit) -> None:
    await _plan(cockpit)
    first = _child(cockpit, "route")
    assert (await binder.bind_session(cockpit.app.state, first)).key == "route"  # type: ignore[union-attr]
    row = _task(cockpit, "route")
    assert row.state == "running" and row.attempts == 1 and row.vendor == "claude"
    assert row.session_ids == [first.id] and row.started_at is not None

    retry = _child(cockpit, "route-r2", agent="ic_codex")
    await binder.bind_session(cockpit.app.state, retry)
    row = _task(cockpit, "route")
    assert row.attempts == 3 and row.vendor == "codex"
    assert row.session_ids == [first.id, retry.id]

    # Binding is idempotent: a second observation changes nothing.
    assert await binder.bind_session(cockpit.app.state, retry) is None
    assert _task(cockpit, "route").session_ids == [first.id, retry.id]

    bound = cockpit.events("task.bound")
    assert [e.data["loop"] for e in bound] == ["implement", "implement"]
    assert bound[1].data["attempt"] == 3
    states = [(e.data["from"], e.data["to"]) for e in cockpit.events("task.state")]
    assert ("ready", "running") in states
    # The sub-agent's run span carries the task.
    span = next(
        s
        for s in cockpit.store.list_spans(cockpit.fid, kinds=["task"])
        if s.dedupe_key == task_key(first.id)
    )
    assert span.task_id == row.id and (span.attrs or {})["task_key"] == "route"
    assert (span.attrs or {})["loop"] == "implement" and span.attempt == 1


async def test_review_and_demo_bind_without_attempts_and_team_never_binds(
    cockpit: Cockpit,
) -> None:
    await _plan(cockpit)
    review = _child(cockpit, "review-route", agent="reviewer")
    demo = _child(cockpit, "demo-route", agent="demo")
    env = _child(cockpit, "env", agent="env_keeper")
    for child in (review, demo, env):
        await binder.bind_session(cockpit.app.state, child)
    row = _task(cockpit, "route")
    assert row.session_ids == [review.id, demo.id]
    assert row.attempts == 0 and row.state == "ready"
    assert [e.data["loop"] for e in cockpit.events("task.bound")] == ["review", "demo"]
    assert not any(env.id in (t.session_ids or []) for t in cockpit.store.list_tasks(cockpit.fid))


async def test_unknown_titles_become_adhoc_tasks_with_a_warning(cockpit: Cockpit) -> None:
    await _plan(cockpit)
    child = _child(cockpit, "Hotfix the CORS header")
    task = await binder.bind_session(cockpit.app.state, child)
    assert task is not None
    assert (task.key, task.kind, task.created_via, task.state) == (
        "hotfix-the-cors-header",
        "adhoc",
        "server",
        "running",
    )
    assert task.session_ids == [child.id] and task.attempts == 1
    [warning] = cockpit.events("task.unplanned")
    assert warning.severity == "warning"
    assert warning.data["session_id"] == child.id
    assert warning.data["by_kind"] == "server"
    created = [e for e in cockpit.events("task.created") if e.data["key"] == task.key]
    assert created and created[0].data["via"] == "server"


async def test_a_pl_that_registered_nothing_still_gets_its_work_tracked(cockpit: Cockpit) -> None:
    child = _child(cockpit, "route")
    task = await binder.bind_session(cockpit.app.state, child)
    assert task is not None and task.key == "route" and task.kind == "adhoc"
    # Registering the task afterwards adopts the ad-hoc row.
    await _plan(cockpit)
    row = _task(cockpit, "route")
    assert row.kind == "implement" and row.session_ids == [child.id]


async def test_dispatch_events_and_ticks_bind(cockpit: Cockpit) -> None:
    from omnigent.server.factory import events

    await _plan(cockpit)
    remove = binder.install()
    try:
        child = _child(cockpit, "tests")
        await events.emit(
            cockpit.app.state,
            cockpit.fid,
            "dispatch",
            project=SLUG,
            from_id=cockpit.world.id("pl"),
            to_id=child.id,
            summary="ic:tests",
        )
        assert _task(cockpit, "tests").session_ids == [child.id]
    finally:
        remove()
    late = _child(cockpit, "docs")
    members = [cockpit.world.get(k) for k in ("lead_a", "lead_b", "pl")]
    await binder.on_tick(cockpit.app.state, members, [late, cockpit.world.get("helper")])
    assert _task(cockpit, "docs").session_ids == [late.id]
    # The helper sub-agent ("helper:x") has an unknown title: an ad-hoc task.
    assert cockpit.store.get_task_by_key(cockpit.fid, SLUG, "x") is not None


async def test_leads_children_are_never_bound(cockpit: Cockpit) -> None:
    planner = cockpit.world.make_session(
        "planner", agent="lead", title="planner:plan-1", parent="lead_a", sub_agent_name="planner"
    )
    assert await binder.bind_session(cockpit.app.state, planner) is None
    assert cockpit.store.list_tasks(cockpit.fid) == []


# ── PRs ────────────────────────────────────────────────────────


def _scope(cockpit: Cockpit) -> prs.FactoryScope:
    [scope] = [s for s in prs.active_factories(cockpit.deps) if s.factory_id == cockpit.fid]
    return scope


async def _refresh(cockpit: Cockpit, fake: FakePr) -> Any:
    row = cockpit.store.get_pr_by_number(cockpit.fid, REPO, fake.number)
    if row is None:
        row, _ = cockpit.store.upsert_pr(
            cockpit.fid, REPO, fake.number, project_slug=SLUG, head_ref=fake.head_ref
        )
    updated = await prs.apply_refresh(
        cockpit.app.state, row, fake.graphql(), now=int(time.time()), scope=_scope(cockpit)
    )
    assert updated is not None
    return cockpit.store.get_pr(updated.id)


async def test_pr_branch_links_the_task_and_a_human_merge_closes_it(cockpit: Cockpit) -> None:
    await _plan(cockpit)
    route = _child(cockpit, "route")
    await binder.bind_session(cockpit.app.state, route)
    now = time.time()
    fake = FakePr(REPO, 9630, f"factory/{SLUG}/route", "a" * 40, now - 60, base_ref="temp-main")

    pr = await _refresh(cockpit, fake)
    row = _task(cockpit, "route")
    assert pr.task_id == row.id and row.pr_id == pr.id
    assert row.state == "review"
    assert _task(cockpit, "tests").state == "planned"

    fake.state, fake.merged_at, fake.merged_by, fake.merge_sha = "MERGED", now, "luka", "b" * 40
    await _refresh(cockpit, fake)
    row = _task(cockpit, "route")
    assert row.state == "done" and row.closed_at is not None
    done = [e for e in cockpit.events("task.state") if e.data["to"] == "done"]
    assert done[-1].data["merged_by"] == "luka" and done[-1].data["by_kind"] == "server"
    # Its dependent became ready without any PL action.
    assert _task(cockpit, "tests").state == "ready"


async def test_a_retry_branch_and_the_reported_task_also_link(cockpit: Cockpit) -> None:
    await _plan(cockpit)
    fake = FakePr(REPO, 9631, f"factory/{SLUG}/docs-r1", "c" * 40, time.time() - 30)
    pr = await _refresh(cockpit, fake)
    assert pr.task_id == _task(cockpit, "docs").id
    other, _ = cockpit.store.upsert_pr(
        cockpit.fid, REPO, 9632, project_slug=SLUG, head_ref="feature/x", task="tests"
    )
    fake2 = FakePr(REPO, 9632, "feature/x", "d" * 40, time.time() - 30)
    await prs.apply_refresh(
        cockpit.app.state, other, fake2.graphql(), now=int(time.time()), scope=_scope(cockpit)
    )
    pr2 = cockpit.store.get_pr(other.id)
    assert pr2 is not None and pr2.task_id == _task(cockpit, "tests").id


async def test_an_unplanned_factory_branch_becomes_an_adhoc_task(cockpit: Cockpit) -> None:
    fake = FakePr(REPO, 9640, f"factory/{SLUG}/side-quest", "e" * 40, time.time() - 30)
    pr = await _refresh(cockpit, fake)
    task = cockpit.store.get_task_by_key(cockpit.fid, SLUG, "side-quest")
    assert task is not None and task.kind == "adhoc" and task.state == "review"
    assert pr.task_id == task.id
    [warning] = cockpit.events("task.unplanned")
    assert warning.data["pr_number"] == 9640


async def test_discovered_prs_link_through_the_poller_hook(cockpit: Cockpit) -> None:
    await _plan(cockpit)
    row, _ = cockpit.store.upsert_pr(
        cockpit.fid, REPO, 9650, project_slug=SLUG, head_ref=f"factory/{SLUG}/docs"
    )
    await binder.on_pr(cockpit.app.state, None, row)
    assert cockpit.store.get_pr(row.id).task_id == _task(cockpit, "docs").id  # type: ignore[union-attr]
    assert _task(cockpit, "docs").state == "review"
