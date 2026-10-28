"""``GET /v1/factory/factories/{fid}/graph?kind=work|bus|flow|org``.

The work graph from stored tasks (ready set, critical path, dependency,
serial and external edges, project ``depends_on``), the bus graph from
fixture ``message.*`` events and open decisions, the flow graph from fixture
spans, the org graph at any depth, and READ-scoped visibility.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from omnigent.server.auth import LEVEL_READ
from omnigent.server.factory import binder, events, snapshot, tasks
from omnigent.server.factory.actor import Actor
from omnigent.server.factory.api.graph import org_graph, parse_window
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.telemetry.spans import write_span
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit, error_code

SLUG = "api-version"
REPO = "caffeinelabs/app"


@pytest_asyncio.fixture()
async def cockpit(
    world: Any, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    binder.reset()
    tasks.reset()
    snapshot.reset_cache()
    async for value in build_cockpit(world, db_uri, monkeypatch):
        value.store.upsert_project(
            value.fid,
            SLUG,
            title="API version",
            repo=REPO,
            base_branch="temp-main",
            charter={"goal": "GET /version", "budget_ics": 3, "depends_on": ["db-schema"]},
            pl_session_id=world.id("pl"),
        )
        value.store.upsert_project(value.fid, "db-schema", title="DB schema", repo=REPO)
        yield value
    binder.reset()
    tasks.reset()
    snapshot.reset_cache()


async def _graph(cockpit: Cockpit, user: str = "alice", **params: Any) -> httpx.Response:
    return await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/graph",
        params=params,
        headers={"X-Test-User": user},
    )


async def _plan(cockpit: Cockpit) -> None:
    actor = Actor.agent(cockpit.world.id("pl"), "project_lead")
    items = [
        tasks.TaskIn(key="route", estimate_min=60, scope=["services/api/**"]),
        tasks.TaskIn(key="tests", deps=["route"], estimate_min=20),
        tasks.TaskIn(key="api-docs", scope=["services/api/README.md"], estimate_min=10),
        tasks.TaskIn(key="migrate", deps=["project:db-schema"], estimate_min=200),
    ]
    await tasks.upsert_tasks(cockpit.app.state, cockpit.fid, SLUG, items, actor)


def test_window_parsing() -> None:
    assert parse_window(None, "bus") == 3600
    assert parse_window(None, "flow") == 7 * 86400
    assert parse_window("30m", "bus") == 1800
    assert parse_window("2d", "bus") == 172800
    assert parse_window("900", "bus") == 900
    for bad in ("1y", "-1h", "0", "100d"):
        with pytest.raises(CockpitError):
            parse_window(bad, "bus")


async def test_work_graph(cockpit: Cockpit) -> None:
    await _plan(cockpit)
    resp = await _graph(cockpit, kind="work")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "work"
    projects = {p["slug"]: p for p in body["projects"]}
    api = projects[SLUG]
    assert api["ready"] == ["route", "api-docs"]
    assert api["critical_path"] == ["migrate"]
    assert api["critical_path_min"] == 200.0
    assert api["blocked"] == [{"key": "migrate", "waiting_on": ["project:db-schema"]}]
    assert api["waves"] == [["route"], ["tests", "api-docs"]]
    assert api["summary"]["total"] == 4
    ids = {t["key"]: t["id"] for t in body["tasks"]}
    edges = {(e["from"], e["to"], e["kind"]) for e in body["edges"]}
    assert (ids["route"], ids["tests"], "dep") in edges
    assert (ids["route"], ids["api-docs"], "serial") in edges
    assert body["external_edges"] == [
        {"from": "project:db-schema", "to": ids["migrate"], "kind": "project", "met": False}
    ]
    assert body["project_edges"] == [
        {"from": "db-schema", "to": SLUG, "kind": "depends_on", "met": False}
    ]
    assert body["layers"][ids["tests"]] == 2
    assert {w["code"] for w in body["warnings"]} == {"scope_overlap"}

    # A merged PR of the dependency releases the waiting task.
    cockpit.store.upsert_pr(cockpit.fid, REPO, 1, project_slug="db-schema", state="merged")
    await tasks.refresh_ready(cockpit.app.state, cockpit.fid)
    again = (await _graph(cockpit, kind="work", project=SLUG)).json()
    [api] = again["projects"]
    assert "migrate" in api["ready"]
    assert again["external_edges"][0]["met"] is True
    assert again["project_edges"][0]["met"] is True


async def test_bus_graph_aggregates_messages_and_waits(cockpit: Cockpit) -> None:
    pl, lead = cockpit.world.id("pl"), cockpit.fid
    now = int(time.time() * 1000)
    fixture = [
        ("message.queued", pl, lead, "request", "m1", now - 600_000),
        ("message.delivered", pl, lead, "request", "m1", now - 599_000),
        ("message.queued", lead, pl, "reply", "m2", now - 540_000),
        ("message.queued", pl, lead, "request", "m3", now - 300_000),
        ("message.bounced", pl, lead, "request", "m3", now - 299_000),
        ("message.queued", pl, lead, "info", "old", now - 3 * 3600_000),
    ]
    for kind, src, dst, msg_kind, mail_id, ts in fixture:
        await events.emit(
            cockpit.app.state,
            cockpit.fid,
            kind,
            from_id=src,
            to_id=dst,
            summary=mail_id,
            data={"mail_id": mail_id, "kind": msg_kind},
            ts=ts,
        )
    await events.emit(
        cockpit.app.state,
        cockpit.fid,
        "alert.stuck",
        from_id=cockpit.world.id("ic"),
        to_id=pl,
        summary="stuck",
        ts=now - 1000,
    )
    cockpit.store.create_decision(
        cockpit.fid, dedupe_key="q", question="Which DB?", asked_by_session_id=pl
    )
    snapshot.reset_cache()
    resp = await _graph(cockpit, kind="bus", window="1h")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["window_s"] == 3600
    msg = {(e["from"], e["to"]): e for e in body["edges"] if e["kind"] == "message"}
    forward = msg[(pl, lead)]
    assert forward["count"] == 2  # the 3-hour-old info is outside the window
    assert forward["bounced"] == 1 and forward["unanswered"] == 1
    assert forward["p50_reply_s"] == 60.0
    kinds = {(e["from"], e["to"], e["kind"]) for e in body["edges"]}
    assert (lead, pl, "spawn") in kinds
    assert (cockpit.world.id("lead_b"), pl, "supervise") in kinds
    assert (cockpit.world.id("ic"), pl, "alert") in kinds
    waits = [e for e in body["edges"] if e["kind"] == "waits_on"]
    assert waits == [
        {
            "from": pl,
            "to": "human:owners",
            "kind": "waits_on",
            "label": "D-1",
            "blocking": True,
            "since": waits[0]["since"],
        }
    ]
    nodes = {n["id"]: n for n in body["nodes"]}
    assert nodes["human:owners"]["kind"] == "human"
    assert nodes[pl]["role"] == "project_lead"


async def test_flow_graph_from_spans(cockpit: Cockpit) -> None:
    store, fid = cockpit.store, cockpit.fid
    t0 = int(time.time() * 1000) - 3600_000
    m = 60_000

    def span(kind: str, name: str, key: str, end: int, outcome: str, **kw: Any) -> None:
        write_span(
            store,
            fid,
            kind=kind,
            name=name,
            dedupe_key=key,
            started_at=t0,
            ended_at=t0 + end,
            outcome=outcome,
            project_slug=SLUG,
            **kw,
        )

    span("stage", "building", "s1", 20 * m, "ok")
    for i in range(3):
        span("gate", "review", f"g{i}", m, "fail", pr_id="a" * 32)
    span("gate", "checks", "c1", m, "fail", pr_id="a" * 32)
    span("gate", "demo", "d1", 5 * m, "ok", pr_id="a" * 32)
    span("task", "ic:route", "t1", m, "ok", role="ic", cost_usd=3.25)
    resp = await _graph(cockpit, kind="flow", project=SLUG)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    nodes = {n["id"]: n for n in body["nodes"]}
    assert nodes["build"]["p50_dwell_s"] == 1200.0 and nodes["build"]["usd"] == 3.25
    assert nodes["demo"]["p50_dwell_s"] == 300.0
    back = {(e["from"], e["label"]): e["count"] for e in body["back_edges"]}
    assert back == {("review", "changes"): 3, ("build", "CI fail"): 1}


async def test_org_graph_has_no_depth_limit(cockpit: Cockpit) -> None:
    world = cockpit.world
    deep = world.make_session(
        "deep", agent="pl", title="helper:deeper", parent="ic", sub_agent_name="helper"
    )
    resp = await _graph(cockpit, kind="org")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    nodes = {n["id"]: n for n in body["nodes"]}
    assert nodes[deep.id]["depth"] == 2 and nodes[deep.id]["project"] == SLUG
    edges = {(e["from"], e["to"], e["kind"]) for e in body["edges"]}
    assert (world.id("ic"), deep.id, "spawn") in edges
    assert (world.id("lead_a"), world.id("pl"), "spawn") in edges
    groups = {g["project"]: g for g in body["groups"]}
    assert world.id("pl") in groups[SLUG]["members"]
    assert body["truncated"] is False


async def test_graph_scope_and_errors(cockpit: Cockpit) -> None:
    assert error_code(await _graph(cockpit, kind="nope")) == "invalid_kind"
    assert error_code(await _graph(cockpit, kind="bus", window="1y")) == "invalid_window"
    assert (await _graph(cockpit, user="bob", kind="work")).status_code in (403, 404)
    # A viewer who reads the root but not the PL sees no api-version tasks.
    cockpit.permissions.grant("bob", cockpit.fid, LEVEL_READ)
    await _plan(cockpit)
    snapshot.reset_cache()
    body = (await _graph(cockpit, user="bob", kind="work")).json()
    assert SLUG not in {p["slug"] for p in body["projects"]}
    assert body["tasks"] == []
    hidden = await _graph(cockpit, user="bob", kind="work", project=SLUG)
    assert hidden.status_code == 404 and error_code(hidden) == "unknown_project"


def test_flow_counts_now_leave_hidden_projects_out() -> None:
    from omnigent.server.factory.api.graph import _now_counts

    body = {
        "projects": [
            {"slug": SLUG, "stage": "building"},
            {"slug": "secret", "stage": "building"},
            {"slug": "db-schema", "stage": "planning"},
        ],
        "prs": [
            {"project_slug": SLUG, "state": "open", "gates": {"checks": "pass"}},
            {"project_slug": "secret", "state": "open", "gates": {"checks": "pass"}},
        ],
    }
    assert _now_counts(body, None, 0, set()) == {"build": 2, "plan": 1, "review": 2}
    assert _now_counts(body, None, 0, {"secret"}) == {"build": 1, "plan": 1, "review": 1}


async def test_org_graph_reads_each_members_tree_in_few_queries(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = cockpit.world
    for n in range(4):
        world.make_session(
            f"w{n}", agent="pl", title=f"helper:w{n}", parent="ic", sub_agent_name="helper"
        )
    calls: list[dict[str, Any]] = []
    original = world.store.list_conversations

    def counting(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(world.store, "list_conversations", counting)
    readable = {conv.id for conv in world.sessions.values()}
    body = org_graph(cockpit.deps, cockpit.fid, readable)
    assert {world.id(f"w{n}") for n in range(4)} <= {n["id"] for n in body["nodes"]}
    # One read per member's spawn tree, not one per node.
    tree_reads = [c.get("root_conversation_id") for c in calls]
    assert world.id("pl") in tree_reads and len(tree_reads) == len(set(tree_reads))
    assert len(tree_reads) < len(body["nodes"])
