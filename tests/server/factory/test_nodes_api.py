"""``GET /v1/factory/factories/{fid}/n/{ref}``: the node pages over a real store.

A person's ask planned as one initiative (two projects), a task with a merged
PR, a PR no task owns and the seeded project that predates every initiative.
Pages come back in the contract's shapes (``node_views``), one parent per node;
readers get the verbs disabled; projects a viewer cannot read are left out;
a recorded write drops the 5 s memo; and every page of a 13 / 45 / 360
factory answers under 300 ms at p95. Every name is sample data.
"""

from __future__ import annotations

import calendar
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from omnigent.server.auth import LEVEL_READ
from omnigent.server.factory import events, initiatives, node_lenses, nodes, snapshot
from omnigent.server.factory.node_views import (
    FindView,
    LineageView,
    MoneyView,
    NeedsYouView,
    SeenView,
    TimelineView,
    validate,
    validate_level,
    validate_page,
)
from omnigent.stores.factory_store import norm_id
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import BASE, REPO, header_cockpit, project, propose

ASK = "Build an in-app helper (sample): a side panel and a core behind it."


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    initiatives.register()
    nodes.reset_cache()
    snapshot.reset_cache()
    check = nodes.check_levels

    def strict(tree: nodes.Tree) -> list[str]:
        """Every projection the route builds keeps one parent, one level up (or the test fails)."""
        bad = check(tree)
        assert not bad, bad
        return bad

    monkeypatch.setattr(nodes, "check_levels", strict)
    async for value in header_cockpit(world, db_uri, monkeypatch):
        yield value
    nodes.reset_cache()
    snapshot.reset_cache()


async def _get(cockpit: Cockpit, ref: str, user: str = "alice", **params: Any) -> httpx.Response:
    return await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/n/{ref}",
        params=params,
        headers={"X-Test-User": user},
    )


async def _page(cockpit: Cockpit, ref: str, user: str = "alice", **params: Any) -> dict[str, Any]:
    resp = await _get(cockpit, ref, user, **params)
    assert resp.status_code == 200, resp.text
    return validate_page(resp.json())


async def _plan(cockpit: Cockpit) -> str:
    message = cockpit.store.append_chat(
        cockpit.fid,
        thread="factory",
        author_kind="human",
        author="alice",
        author_role="owner",
        body=ASK,
    )
    resp = await propose(
        cockpit,
        {"op": "create", "project": project("panel")},
        {"op": "create", "project": project("core")},
        reply_to=f"chat_{message.id}",
        thread="factory",
        analysis={
            "initiative": {
                "title": "Sample helper",
                "acceptance": [{"step": "Open the panel", "expect": "it opens", "owner": "panel"}],
                "skeleton": "panel",
                "user_facing": ["panel"],
            }
        },
    )
    assert resp.json()["status"] == "applied", resp.text
    task, _ = cockpit.store.upsert_task(
        cockpit.fid, "core", "api", title="Core API", estimate_min=90
    )
    pr, _ = cockpit.store.upsert_pr(
        cockpit.fid,
        REPO,
        9001,
        project_slug="core",
        state="merged",
        task_id=task.id,
        title="Core API",
    )
    cockpit.store.update_task(task.id, state="done", pr_id=pr.id)
    cockpit.store.upsert_pr(
        cockpit.fid, REPO, 9002, project_slug="api-version", title="Version route"
    )
    return message.id


async def test_the_pages_of_a_planned_ask(cockpit: Cockpit) -> None:
    message = await _plan(cockpit)

    product = await _page(cockpit, "product")
    feature = await _page(cockpit, "I-1")
    core = await _page(cockpit, "core")
    seeded = await _page(cockpit, "api-version")

    ask = next(i for i in product["children"]["items"] if i["kind"] == "ask")
    # A person's ask shows under a short name (its first words until the factory names
    # it); its full text stays in its details.
    assert ask["ref"] == f"ask:{message}" and ask["full_title"] == ASK
    assert (ask["title"], ask["named"]) == ("Build an in-app helper (sample)", "words")
    assert [c["ref"] for c in feature["breadcrumb"]] == ["product", ask["ref"]]
    assert [i["ref"] for i in feature["children"]["items"]] == ["core", "panel"]
    assert feature["node"]["acceptance"]["steps"][0]["result"] == "none"
    assert core["node"]["progress"] == {"value": 1.0, "label": "1/1", "what": "tasks"}
    assert [i["ref"] for i in core["children"]["items"]] == ["T-1"]
    assert core["children"]["items"][0]["state"] == "done"
    # The seeded project came from no proposal: its implicit feature and need fold.
    assert [(c["ref"], c.get("folded")) for c in seeded["breadcrumb"]] == [
        ("product", None),
        ("need:by-server", True),
        ("feature:by-server", True),
    ]
    assert seeded["children"]["items"][0]["ref"] == "PR-9002"
    assert product["node"]["level"] == "product" and product["home"] == "feature"
    assert product["results"]["merged"] == 1 and product["results"]["open"] == ["PR-9002"]
    assert "in_base_env" not in product["results"]  # no base env: no count, not a zero
    assert message


async def test_unknown_refs_and_cursors_are_refused(cockpit: Cockpit) -> None:
    missing = await _get(cockpit, "T-404")
    bad = await _get(cockpit, "product", children_page="7")
    stranger = await _get(cockpit, "product", user="bob")

    assert (missing.status_code, error_code(missing)) == (404, "unknown_node")
    assert (bad.status_code, error_code(bad)) == (400, "bad_cursor")
    assert stranger.status_code == 404


async def test_readers_see_verbs_disabled_and_editors_use_them(cockpit: Cockpit) -> None:
    cockpit.permissions.ensure_user("dave")
    cockpit.permissions.grant("dave", cockpit.fid, LEVEL_READ)

    reader = await _page(cockpit, "product", user="dave")
    editor = await _page(cockpit, "api-version", user="carol")
    hidden = await _get(cockpit, "api-version", user="dave")

    assert reader["verbs"] and not any(v["enabled"] for v in reader["verbs"])
    assert all(v["enabled"] for v in editor["verbs"])
    # The project's lead is not readable to this reader: the project is left out.
    assert (hidden.status_code, error_code(hidden)) == (404, "unknown_node")
    assert reader["children"]["total"] == 0


async def test_a_reader_sees_only_the_members_decisions_and_cost_they_may_read(
    cockpit: Cockpit,
) -> None:
    store, world = cockpit.store, cockpit.world
    cockpit.permissions.ensure_user("dave")
    cockpit.permissions.grant("dave", cockpit.fid, LEVEL_READ)
    world.store.set_session_usage(world.id("lead_a"), {"total_cost_usd": 1.5})
    world.store.set_session_usage(world.id("pl"), {"total_cost_usd": 4.0})
    pl = world.id("pl")
    store.create_decision(
        cockpit.fid,
        dedupe_key="pl-asks",
        question="Which migrations table? (sample)",
        asked_by_session_id=pl,
        subject=f"member:{pl}",
        blocking=True,
    )

    owner_team = await _page(cockpit, "fx:team")
    reader_team = await _page(cockpit, "fx:team", user="dave")
    owner = await _page(cockpit, "product")
    reader = await _page(cockpit, "product", user="dave")
    raw = await snapshot.get_snapshot(cockpit.app.state, world.get("lead_a"))
    readable = await snapshot.readable_for("dave", raw, cockpit.deps)
    cut = snapshot.filter_snapshot(raw, readable)

    # The owner sees the PL (its open ask draws it); the reader sees neither it nor its ask.
    assert f"member:{pl}" in [i["ref"] for i in owner_team["children"]["items"]]
    assert f"member:{pl}" not in [i["ref"] for i in reader_team["children"]["items"]]
    assert "PL · api-version" not in str(reader_team)
    assert (await _get(cockpit, f"member:{pl}", user="dave")).status_code == 404
    assert owner_team["node"]["needs"]["open"] == 1 and reader_team["node"]["needs"]["open"] == 0
    # The reader's cost is what they may read, not the whole factory's.
    assert owner["rollup"]["cost"] == round(raw.body["factory"]["cost_usd"], 2) == 5.5
    assert reader["rollup"]["cost"] == round(cut["factory"]["cost_usd"] or 0.0, 2) == 1.5
    assert "burn" not in reader["rollup"]


async def test_the_thread_counts_this_viewers_unread(cockpit: Cockpit) -> None:
    for body in ("one (sample)", "two (sample)"):
        cockpit.store.append_chat(
            cockpit.fid,
            thread="project:api-version",
            author_kind="agent",
            author=cockpit.world.id("pl"),
            body=body,
        )

    page = await _page(cockpit, "api-version")

    assert page["thread"]["key"] == "project:api-version"
    assert page["thread"]["unread"] == 2


async def test_a_recorded_write_drops_the_memo(cockpit: Cockpit) -> None:
    before = await _page(cockpit, "api-version")
    cockpit.store.upsert_task(cockpit.fid, "api-version", "route", title="Route")
    cached = await _page(cockpit, "api-version")
    snapshot.reset_cache()
    await events.emit(
        cockpit.app.state, cockpit.fid, "task.created", subject="task:T-1", summary="T-1 Route"
    )
    after = await _page(cockpit, "api-version")

    assert before["children"]["total"] == cached["children"]["total"] == 0
    assert [i["ref"] for i in after["children"]["items"]] == ["T-1"]
    assert after["version"] != before["version"]


async def _level(
    cockpit: Cockpit, ref: str, level: str, user: str = "alice", **params: Any
) -> httpx.Response:
    return await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/n/{ref}/level/{level}",
        params=params,
        headers={"X-Test-User": user},
    )


async def test_home_lists_the_features_under_the_needs_they_came_from(cockpit: Cockpit) -> None:
    message = await _plan(cockpit)

    resp = await _level(cockpit, "product", "feature")
    tasks = await _level(cockpit, "I-1", "task")
    above = await _level(cockpit, "T-1", "project")
    bad = await _level(cockpit, "product", "feature", cursor="x")
    missing = await _level(cockpit, "T-404", "task")

    assert resp.status_code == 200, resp.text
    view = validate_level(resp.json())
    groups = {g["parent"]["ref"]: [i["ref"] for i in g["items"]] for g in view["groups"]}
    # The planned ask holds its feature; the seeded project shows in place of
    # its implicit feature, under its implicit need (both fold).
    assert groups == {f"ask:{message}": ["I-1"], "need:by-server": ["api-version"]}
    assert view["total"] == 2 and view["via_fold"] == 1 and view["tiles"] is True
    assert view["level_name"] == "Features" and view["parent_level_name"] == "needs"
    implicit = next(g for g in view["groups"] if g["parent"]["ref"] == "need:by-server")
    assert implicit["parent"]["implicit"] is True and implicit["parent"]["folded"] is True
    task_view = validate_level(tasks.json())
    assert [(g["parent"]["ref"], [i["ref"] for i in g["items"]]) for g in task_view["groups"]] == [
        ("core", ["T-1"])
    ]
    assert task_view["tiles"] is False and task_view["parent_level_name"] == "projects"
    assert (above.status_code, error_code(above)) == (404, "unknown_level")
    assert (bad.status_code, error_code(bad)) == (400, "bad_cursor")
    assert (missing.status_code, error_code(missing)) == (404, "unknown_node")


async def test_a_level_group_pages_at_60(cockpit: Cockpit) -> None:
    for t in range(75):
        cockpit.store.upsert_task(cockpit.fid, "api-version", f"k{t}", title=f"Task {t}")

    first = validate_level((await _level(cockpit, "product", "task")).json())
    group = first["groups"][0]
    rest = validate_level((await _level(cockpit, "product", "task", cursor=group["next"])).json())

    assert len(group["items"]) == 60 and group["next"] == "api-version@60"
    assert [g["parent"]["ref"] for g in rest["groups"]] == ["api-version"]
    assert len(rest["groups"][0]["items"]) == 15 and "next" not in rest["groups"][0]
    assert {i["ref"] for i in group["items"]}.isdisjoint(
        i["ref"] for i in rest["groups"][0]["items"]
    )


async def test_a_persons_proposal_holds_its_project_and_an_answer_clears_it(
    cockpit: Cockpit,
) -> None:
    """
    A proposal's decision is stored as blocking nothing; the node pages count
    it as needing a person while it changes an active project, carry the row
    id the answer route takes, and read again once it is answered.
    """
    filed = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/proposals",
        {
            "changes": [
                {"op": "update", "slug": "api-version", "patch": {"constraints": "Keep it fast."}}
            ],
            "rationale": "a sample change",
        },
    )
    assert filed.status_code in (200, 201), filed.text
    ref = filed.json()["decision"]
    row = cockpit.store.get_decision_by_number(cockpit.fid, int(ref.removeprefix("D-")))
    assert row is not None and row.blocking is False

    before = await _page(cockpit, "api-version")
    answered = await cockpit.post(
        f"/v1/factory/decisions/{before['status']['decision']['row_id']}/answer",
        {"option_id": "apply"},
    )
    after = await _page(cockpit, "api-version")

    assert before["node"]["state"] == "needs" and before["node"]["needs"] == {
        "open": 1,
        "blocking": 1,
    }
    assert before["status"]["ends"] == "person"
    assert before["status"]["decision"]["id"] == ref
    assert before["status"]["decision"]["row_id"] == norm_id(row.id)
    assert answered.status_code == 200, answered.text
    assert after["node"]["needs"] == {"open": 0, "blocking": 0}
    assert after["status"].get("decision") is None


async def test_every_page_of_a_13_45_360_factory_answers_under_300ms(cockpit: Cockpit) -> None:
    store, fid = cockpit.store, cockpit.fid
    slugs = [f"proj-{p:02d}" for p in range(45)]
    for f in range(13):
        mine = slugs[f::13]
        store.create_initiative(
            fid,
            title=f"Feature {f + 1} (sample)",
            ask="",
            plan={"projects": mine, "acceptance": [{"n": 1, "step": "It works"}]},
        )
    for p, slug in enumerate(slugs):
        before = slugs[p - 13] if p >= 13 else None
        store.upsert_project(
            fid,
            slug,
            title=slug,
            repo=REPO,
            base_branch=BASE,
            charter={"goal": f"Build {slug}.", "depends_on": [before] if before else []},
        )
    states = ("done", "running", "ready", "planned", "review", "blocked")
    for t in range(360):
        store.upsert_task(
            fid,
            slugs[t % 45],
            f"k{t}",
            title=f"Task {t}",
            state=states[t % 6],
            estimate_min=30 + t % 60,
        )
    refs = [
        "product",
        *(f"I-{n}" for n in range(1, 14)),
        *slugs,
        *(f"T-{n}" for n in range(1, 361, 4)),
    ]

    timings: list[float] = []
    for ref in refs:
        started = time.perf_counter()
        page = await _page(cockpit, ref)
        timings.append(time.perf_counter() - started)
        assert len(page["children"]["items"]) <= nodes.PAGE_SIZE
    timings.sort()

    p95 = timings[int(len(timings) * 0.95)]
    median, worst = timings[len(timings) // 2], timings[-1]
    print(f"node pages: {len(timings)}, p50 {median:.4f} s, p95 {p95:.4f} s, max {worst:.4f} s")
    assert p95 < 0.3, (p95, timings[-5:])


async def test_a_cold_build_with_history_answers_under_300ms(cockpit: Cockpit) -> None:
    """Asks of applied proposals are read in one batch, and only those live nodes quote."""
    store, fid = cockpit.store, cockpit.fid
    slugs = [f"proj-{p:02d}" for p in range(45)]
    for p, slug in enumerate(slugs):
        store.upsert_project(fid, slug, title=slug, repo=REPO, base_branch=BASE)
    for n in range(150):
        message = store.append_chat(
            fid,
            thread="factory",
            author_kind="human",
            author="alice",
            author_role="owner",
            body=f"Ask {n} (sample)",
        )
        store.create_proposal(
            fid,
            kind="projects",
            title=f"Plan {n} (sample)",
            author_kind="agent",
            author=cockpit.world.id("lead_a"),
            status="applied",
            on_behalf_of="alice",
            reply_to_chat=message.id,
            result={"created": [slugs[n % 45]]},
        )
    states = ("done", "running", "ready", "planned")
    for t in range(600):
        task, _ = store.upsert_task(
            fid, slugs[t % 45], f"k{t}", title=f"Task {t}", state=states[t % 4], estimate_min=45
        )
        if t < 450:
            pr, _ = store.upsert_pr(
                fid,
                REPO,
                10_000 + t,
                project_slug=slugs[t % 45],
                state="merged" if t % 3 else "open",
                task_id=task.id,
                title=f"Task {t}",
            )
            store.update_task(task.id, pr_id=pr.id)
    await _page(cockpit, "fx")  # warms the snapshot
    nodes.reset_cache()

    started = time.perf_counter()
    page = await _page(cockpit, "product")
    cold = time.perf_counter() - started

    print(f"cold node page with history: {cold:.4f} s")
    assert page["results"]["merged"] == 300
    assert cold < 0.3, cold


# ── Lenses, sheets and last looks ──────────────────────────────


async def _read(cockpit: Cockpit, path: str, user: str = "alice", **params: Any) -> httpx.Response:
    return await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/{path}",
        params=params,
        headers={"X-Test-User": user},
    )


async def test_the_lenses_of_a_planned_ask(cockpit: Cockpit) -> None:
    message = await _plan(cockpit)

    lineage = await _read(cockpit, "n/T-1/lineage")
    money = await _read(cockpit, "n/I-1/money")
    timeline = await _read(cockpit, "n/I-1/timeline", **{"from": "2026-01-01T00:00:00Z"})
    window = await _read(cockpit, "n/I-1/timeline")
    missing = await _read(cockpit, "n/T-404/lineage")
    stranger = await _read(cockpit, "n/I-1/money", user="bob")

    assert lineage.status_code == 200, lineage.text
    view = validate(LineageView, lineage.json())
    assert [a["ref"] for a in view["ancestors"]] == ["product", f"ask:{message}", "I-1", "core"]
    assert view["here"]["ref"] == "T-1" and view["results"]["merged"] == 1
    assert [r["ref"] for r in validate(MoneyView, money.json())["rows"]] == []
    assert (timeline.status_code, error_code(timeline)) == (400, "bad_window")
    assert window.status_code == 200, window.text
    axis = validate(TimelineView, window.json())["axis"]
    # Without `from` the timeline reads the last day.
    assert _epoch(axis[1]) - _epoch(axis[0]) <= 24 * 3600
    assert (missing.status_code, error_code(missing)) == (404, "unknown_node")
    assert stranger.status_code == 404


async def test_timelines_of_one_projection_read_the_spans_once(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _plan(cockpit)
    read = node_lenses.timeline_spans
    calls: list[tuple[Any, ...]] = []

    def counted(*args: Any) -> Any:
        calls.append(args)
        return read(*args)

    monkeypatch.setattr(node_lenses, "timeline_spans", counted)
    # Each page asks whether it has lanes: navigating (and coming back) reads the store once.
    for ref in ("I-1", "product", "I-1", "core"):
        resp = await _read(cockpit, f"n/{ref}/timeline")
        assert resp.status_code == 200, resp.text
    assert len(calls) == 1
    # A write drops the projection, and the spans with it.
    nodes.invalidate(cockpit.fid)
    assert (await _read(cockpit, "n/I-1/timeline")).status_code == 200
    assert len(calls) == 2


def _epoch(iso: str) -> int:
    return calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))


@pytest.mark.parametrize(
    "window",
    [
        {"to": "100000000000000000"},  # past int64 milliseconds
        {"to": "9999-01-01T00:00:00Z"},
        {"from": "1", "to": "3600"},  # 1970
        {"from": "1999-12-31T00:00:00Z", "to": "1999-12-31T12:00:00Z"},
        {"from": "yesterday"},
    ],
)
async def test_a_timeline_window_outside_2000_to_next_week_is_refused(
    cockpit: Cockpit, window: dict[str, str]
) -> None:
    await _plan(cockpit)

    resp = await _read(cockpit, "n/I-1/timeline", **window)

    assert (resp.status_code, error_code(resp)) == (400, "bad_window"), resp.text


async def test_needs_you_and_find_over_a_real_store(cockpit: Cockpit) -> None:
    await _plan(cockpit)
    filed = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/proposals",
        {
            "changes": [
                {"op": "update", "slug": "api-version", "patch": {"constraints": "Keep it fast."}}
            ],
            "rationale": "a sample change",
        },
    )
    assert filed.status_code in (200, 201), filed.text

    needs = await _read(cockpit, "needs-you")
    factory = await _read(cockpit, "needs-you", tree="factory")
    bad = await _read(cockpit, "needs-you", tree="nope")
    task = await _read(cockpit, "find", q="T-1")
    pr = await _read(cockpit, "find", q="#9001")
    proposal = await _read(cockpit, "find", q=filed.json()["proposal"]["ref"])

    assert needs.status_code == 200, needs.text
    view = validate(NeedsYouView, needs.json())
    assert [i["decision"]["id"] for i in view["items"]] == [filed.json()["decision"]]
    assert view["items"][0]["breadcrumb"][-1]["ref"] == "api-version"
    assert validate(NeedsYouView, factory.json())["other_tree"] == 1
    assert (bad.status_code, error_code(bad)) == (400, "bad_tree")
    assert [i["ref"] for i in validate(FindView, task.json())["items"]][:1] == ["T-1"]
    assert [i["ref"] for i in validate(FindView, pr.json())["items"]] == ["PR-9001"]
    assert [i["ref"] for i in validate(FindView, proposal.json())["items"]] == ["api-version"]


async def test_a_level_view_filters_by_state(cockpit: Cockpit) -> None:
    await _plan(cockpit)

    done = await _level(cockpit, "product", "task", state="done")
    words = await _level(cockpit, "product", "task", q="core api")
    bad = await _level(cockpit, "product", "task", state="sideways")

    assert [g["parent"]["ref"] for g in validate_level(done.json())["groups"]] == ["core"]
    assert validate_level(words.json())["total"] == 1
    assert (bad.status_code, error_code(bad)) == (400, "bad_state")


async def test_last_looks_are_per_person_and_name_what_changed(cockpit: Cockpit) -> None:
    await _plan(cockpit)
    store, fid = cockpit.store, cockpit.fid
    now = int(time.time())
    store.mark_node_seen(fid, "alice", "product", now - 3600)
    pr = store.get_pr_by_number(fid, REPO, 9001)
    assert pr is not None
    await events.emit(
        cockpit.app.state,
        fid,
        "pr.merged",
        project="core",
        subject=f"pr:{REPO}#9001",
        summary="PR #9001 merged (sample)",
        data={"pr_id": pr.id},
        ts=(now - 60) * 1000,
    )

    alice = await _page(cockpit, "product")
    feature = await _page(cockpit, "I-1")
    carol = await _page(cockpit, "product", user="carol", seen=1)
    marked = await cockpit.client.put(
        f"/v1/factory/factories/{fid}/n/I-1/seen", headers={"X-Test-User": "alice"}
    )
    missing = await cockpit.client.put(
        f"/v1/factory/factories/{fid}/n/T-404/seen", headers={"X-Test-User": "alice"}
    )
    again = await _page(cockpit, "I-1")

    # Alice looked at the product an hour ago: the merge since then is named, there and below.
    merged = f"#9001 merged {time.strftime('%H:%M', time.gmtime(now - 60))}"
    since = alice["rollup"]["since"]
    assert since["items"][0] == merged
    assert {*since["items"][1:], "panel added", "Sample helper added", "core added"} == {
        "panel added",
        "Sample helper added",
        "core added",
    }
    # Planning the ask added the feature and two projects and applied C-1: five changes.
    assert len(since["items"]) + since["more"] == 5
    assert feature["rollup"]["since"] == since
    changed = {k["ref"]: k["changed"] for k in feature["children"]["items"] if k.get("changed")}
    # C-1 started the feature: it is the feature's change, not one of its projects'.
    assert changed == {"core": [merged, "core added"], "panel": ["panel added"]}
    # Carol never looked: nothing is "since"; seen=1 marks her look for next time.
    assert "since" not in carol["rollup"]
    assert [r.node_ref for r in store.list_node_reads(fid, "carol")] == ["product"]
    assert marked.status_code == 200, marked.text
    assert validate(SeenView, marked.json())["ref"] == "I-1"
    assert (missing.status_code, error_code(missing)) == (404, "unknown_node")
    assert "since" not in again["rollup"]
    assert {r.node_ref for r in store.list_node_reads(fid, "alice")} == {"product", "I-1"}
