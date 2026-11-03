"""``GET /v1/factory/factories/{fid}/n/{ref}``: the node pages over a real store.

A person's ask planned as one initiative (two projects), a task with a merged
PR, a PR no task owns and the seeded project that predates every initiative.
Pages come back in the contract's shapes (``node_views``), one parent per node;
readers get the verbs disabled; projects a viewer cannot read are left out;
a recorded write drops the 5 s memo; and every page of a 13 / 45 / 360
factory answers under 300 ms at p95. Every name is sample data.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.server.auth import LEVEL_READ
from omnigent.server.factory import events, initiatives, nodes, snapshot
from omnigent.server.factory.node_views import validate_page
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryApi, FactoryWorld
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
    assert ask["ref"] == f"ask:{message}" and ask["title"] == ASK
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


async def test_preflight_says_whether_v3_reads_the_node_api(
    authed_api: FactoryApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(F.NODE_API_ENV, raising=False)
    off = await authed_api.client.get("/v1/factory/preflight", headers={"X-Test-User": "alice"})
    monkeypatch.setenv(F.NODE_API_ENV, "1")
    on = await authed_api.client.get("/v1/factory/preflight", headers={"X-Test-User": "alice"})

    assert off.json()["node_api"] is False and on.json()["node_api"] is True


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
