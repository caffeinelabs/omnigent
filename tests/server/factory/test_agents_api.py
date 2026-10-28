"""The fleet API: one row per agent with its live "now", task, loop, mail,
decisions, issues and cost; one agent's detail; viewer filtering."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from omnigent.entities.conversation import NewConversationItem, parse_item_data
from omnigent.server.factory import activity_now, detectors, events, issues, snapshot
from omnigent.server.factory.activity_now import ActivityPoller
from omnigent.server.factory.telemetry import cost as cost_ledger
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit
from tests.server.factory.conftest import FactoryWorld

FIXTURES = Path(__file__).parent / "fixtures" / "now_items.json"


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    for reset in (activity_now.registry.reset, detectors.reset, issues.reset, cost_ledger.reset):
        reset()
    snapshot.reset_cache()
    yield
    for reset in (activity_now.registry.reset, detectors.reset, issues.reset, cost_ledger.reset):
        reset()
    snapshot.reset_cache()


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in build_cockpit(world, db_uri, monkeypatch):
        yield value


def _append(world: FactoryWorld, key: str, harness: str) -> None:
    rows = json.loads(FIXTURES.read_text(encoding="utf-8"))[harness]
    world.store.append(
        world.id(key),
        [
            NewConversationItem(
                type=r["type"],
                response_id=r["response_id"],
                data=parse_item_data(r["type"], r["data"]),
            )
            for r in rows
        ],
    )


async def _poll(cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch, running: set[str]) -> None:
    monkeypatch.setattr(
        activity_now,
        "_raw_status",
        lambda conv, _cs: "running" if conv.id in running else "idle",
    )
    poller = ActivityPoller(cockpit.app.state, presence=lambda _f: 1, publish=lambda _f: None)
    await poller.poll_factory(cockpit.fid)
    snapshot.reset_cache()


async def _get(cockpit: Cockpit, path: str, user: str = "carol") -> Any:
    return await cockpit.client.get(path, headers={"X-Test-User": user})


async def test_fleet_rows_show_each_agent_with_its_now(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = cockpit.world
    ic, reviewer, pl = w.id("ic"), w.id("reviewer"), w.id("pl")
    _append(w, "ic", "claude_native")
    _append(w, "reviewer", "codex_native")
    store = cockpit.store
    task, _ = store.upsert_task(
        cockpit.fid, "api-version", "task-1", title="Version route", session_ids=[ic], attempts=3
    )
    store.upsert_loop(
        cockpit.fid,
        "implement",
        f"task:{task.id}",
        task_id=task.id,
        state="exhausted",
        iteration=3,
        spec={"attempts": 3},
    )
    store.create_decision(
        cockpit.fid, dedupe_key="d", question="Which db?", asked_by_session_id=pl
    )
    await events.emit(
        cockpit.app.state,
        cockpit.fid,
        "message.queued",
        from_id=pl,
        to_id=ic,
        subject="mail:m1",
        summary="next step",
        data={"mail_id": "m1", "kind": "request"},
    )
    await _poll(cockpit, monkeypatch, {ic, reviewer})

    resp = await _get(cockpit, f"/v1/factory/factories/{cockpit.fid}/agents")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    rows = {r["session_id"]: r for r in body["data"]}
    # carol reads the root and the PL (with its sub-agents), not Lead B.
    assert set(rows) == {w.id("lead_a"), pl, ic, reviewer, w.id("helper")}
    row = rows[ic]
    assert (row["role"], row["harness"], row["project"], row["status"]) == (
        "ic",
        "claude-native",
        "api-version",
        "running",
    )
    assert row["now"]["tool"] == "Bash" and row["now"]["tool_since"] is not None
    assert row["now"]["tool_arg"].startswith("NPM_TOKEN=[REDACTED]")
    assert "repeat" in row["flags"] and row["issues"] == ["INC-1"]
    assert row["task"]["ref"] == f"T-{task.number}" and row["task"]["attempts"] == 3
    assert row["loop"] == {
        "id": row["loop"]["id"],
        "kind": "implement",
        "state": "exhausted",
        "iteration": 3,
        "limit": 3,
    }
    assert row["mail_in"] == 1 and rows[pl]["mail_out"] == 1
    assert rows[pl]["decisions_pending"] == 1
    assert rows[reviewer]["harness"] == "codex-native"
    assert rows[reviewer]["now"]["tool_arg"] == "pnpm -F web lint"
    assert rows[pl]["now"]["status"] == "idle" and rows[pl]["now"]["tool"] is None
    assert body["live"]["fresh"] is True
    assert body["lines"] and {line["session_id"] for line in body["lines"]} <= set(rows)

    filtered = await _get(
        cockpit, f"/v1/factory/factories/{cockpit.fid}/agents?project=api-version&role=reviewer"
    )
    assert [r["session_id"] for r in filtered.json()["data"]] == [reviewer]


async def test_one_agent_has_tools_mail_tasks_issues_and_cost(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = cockpit.world
    ic, pl = w.id("ic"), w.id("pl")
    _append(w, "ic", "claude_native")
    cockpit.store.upsert_task(cockpit.fid, "api-version", "task-1", session_ids=[ic])
    for day, usd in (("2026-10-26", 1.5), ("2026-10-27", 2.25)):
        cockpit.store.add_cost_daily(
            cockpit.fid,
            project_slug="api-version",
            role="ic",
            agent_name="ic",
            model="sonnet",
            day_utc=day,
            cost_usd=usd,
        )
    monkeypatch.setattr(cost_ledger, "utc_day", lambda _when=None: "2026-10-20")
    await events.emit(
        cockpit.app.state,
        cockpit.fid,
        "message.queued",
        from_id=pl,
        to_id=ic,
        subject="mail:m1",
        summary="please rebase",
        data={"mail_id": "m1", "kind": "request"},
    )
    await _poll(cockpit, monkeypatch, {ic})

    resp = await _get(cockpit, f"/v1/factory/factories/{cockpit.fid}/agents/{ic}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["agent"]["session_id"] == ic
    tools = body["tools"]
    assert tools[0]["tool"] == "Bash" and tools[0]["ended_at"] is None
    failed = [t for t in tools if t["failed"]]
    assert len(failed) == 5 and failed[0]["output_head"].startswith("Exit code 1")
    assert {t["tool"] for t in tools} == {"Bash", "Edit", "Read"}
    assert [m["direction"] for m in body["mail"]] == ["in"]
    assert body["mail"][0]["peer_title"] == "PL · api-version"
    assert [t["key"] for t in body["tasks"]] == ["task-1"]
    assert [i["rule"] for i in body["issues"]] == ["agent.repeat"]
    assert [(c["day"], c["cost_usd"]) for c in body["cost_series"]] == [
        ("2026-10-26", 1.5),
        ("2026-10-27", 2.25),
    ]


async def test_viewers_only_see_agents_they_may_read(cockpit: Cockpit) -> None:
    w = cockpit.world

    stranger = await _get(cockpit, f"/v1/factory/factories/{cockpit.fid}/agents", user="bob")
    other = await _get(cockpit, f"/v1/factory/factories/{cockpit.fid}/agents/{w.id('lead_b')}")
    unknown = await _get(cockpit, f"/v1/factory/factories/{cockpit.fid}/agents/{w.id('plain')}")
    owner = await _get(cockpit, f"/v1/factory/factories/{cockpit.fid}/agents", user="alice")

    assert stranger.status_code == 404
    assert other.status_code == 404
    assert unknown.status_code == 404
    assert w.id("lead_b") in {r["session_id"] for r in owner.json()["data"]}
    # Before any poll the rows carry no live state yet.
    assert all(r["now"] is None for r in owner.json()["data"])
