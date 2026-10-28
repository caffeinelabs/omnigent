"""Project and factory dollar budgets: notices, decisions and auto-pause by engine mode."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.server.factory import budgets, decisions, loops
from omnigent.server.factory.control import CockpitError
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit

SLUG = "api-version"


@pytest_asyncio.fixture()
async def cockpit(
    world: Any, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    budgets.reset()
    loops.reset()
    async for value in build_cockpit(world, db_uri, monkeypatch):
        value.store.upsert_project(
            value.fid,
            SLUG,
            title="API version",
            repo="caffeinelabs/app",
            charter={"goal": "GET /version", "budgets": {"project_usd": 100}},
            pl_session_id=world.id("pl"),
        )
        yield value
    budgets.reset()


def set_mode(cockpit: Cockpit, mode: str) -> None:
    from omnigent.server.factory import definition

    definition.set_values(cockpit.deps, cockpit.fid, {"engine.mode": mode}, created_by="test")


def spend(cockpit: Cockpit, usd: float, *, project: str = SLUG, now: float | None = None) -> None:
    cockpit.store.add_cost_daily(
        cockpit.fid,
        project_slug=project,
        role="ic",
        agent_name="ic",
        model="sonnet",
        day_utc=budgets.utc_day(now or time.time()),
        cost_usd=usd,
    )


def budget_decisions(cockpit: Cockpit) -> list[Any]:
    return [
        d
        for d in cockpit.store.list_decisions(factory_id=cockpit.fid, limit=50)
        if d.dedupe_key.startswith("budget:")
    ]


async def test_eighty_percent_is_a_notice_once(cockpit: Cockpit) -> None:
    spend(cockpit, 81)
    now = time.time()
    assert await budgets.check(cockpit.app.state, cockpit.fid, now=now) == ["budget.near"]
    assert await budgets.check(cockpit.app.state, cockpit.fid, now=now) == []
    event = cockpit.events("budget.near")[-1]
    assert event.subject == "budget:project:api-version"
    assert event.data["usd"] == 81 and event.data["limit"] == 100
    assert event.summary == "project api-version spent $81.00 of $100 (81 %)"
    budgets.reset()  # a restarted server reads what already fired from the event log
    assert await budgets.check(cockpit.app.state, cockpit.fid, now=now) == []


@pytest.mark.parametrize("mode", ["observe", "assist", "enforce"])
async def test_auto_pause_only_in_enforce(cockpit: Cockpit, mode: str) -> None:
    set_mode(cockpit, mode)
    spend(cockpit, 101)
    now = time.time()
    assert await budgets.check(cockpit.app.state, cockpit.fid, now=now) == ["budget.exceeded"]
    pl = cockpit.world.get("pl")
    paused = pl.labels.get(F.LABEL_STATE) == F.STATE_PAUSED
    assert paused is (mode == "enforce")
    pauses = cockpit.events("control.pause")
    if mode == "enforce":
        assert pauses and pauses[-1].data["note"] == "budget"
        assert pauses[-1].data["by"] == "server (budget)"
        text = cockpit.mailer.texts_to(pl.id)[-1]
        assert text.startswith("[Factory control · PAUSE · by server (budget)]")
    else:
        assert pauses == []
    found = budget_decisions(cockpit)
    if mode == "assist":
        assert len(found) == 1 and found[0].assignee == "owners"
        assert [o["action"] for o in found[0].options] == ["pause", "raise_budget", "dismiss"]
    else:
        assert found == []
    assert (
        cockpit.events("budget.exceeded")[-1].data["action"]
        == {
            "observe": None,
            "assist": "decision",
            "enforce": "pause",
        }[mode]
    )


async def test_the_factory_day_budget(cockpit: Cockpit) -> None:
    set_mode(cockpit, "enforce")
    now = time.time()
    spend(cockpit, 410, project="", now=now)
    assert await budgets.check(cockpit.app.state, cockpit.fid, now=now) == ["budget.exceeded"]
    event = cockpit.events("budget.exceeded")[-1]
    assert event.subject == "budget:factory" and event.data["period"] == budgets.utc_day(now)
    root = cockpit.world.get("lead_a")
    assert root.labels.get(F.LABEL_STATE) == F.STATE_PAUSED
    # Tomorrow is a new day.
    assert await budgets.check(cockpit.app.state, cockpit.fid, now=now + 86400) == []


async def test_owners_raise_a_project_budget(cockpit: Cockpit) -> None:
    set_mode(cockpit, "assist")
    spend(cockpit, 120)
    await budgets.check(cockpit.app.state, cockpit.fid, now=time.time())
    decision = budget_decisions(cockpit)[0]
    with pytest.raises(CockpitError, match="assigned to owners"):  # money is for owners
        await decisions.answer_decision(
            cockpit.app.state, decision.id, user_id="carol", option_id="raise"
        )
    await decisions.answer_decision(
        cockpit.app.state, decision.id, user_id="alice", option_id="raise"
    )
    assert budgets.project_limit(cockpit.deps, cockpit.fid, SLUG) == 200
    text = cockpit.mailer.texts_to(cockpit.world.id("pl"))[-1]
    assert text == (
        f"[Factory loop · BUDGET RAISED · api-version · project $100→$200 by alice · "
        f"{decision.ref}] Carry on; the project's spend is now measured against the new cap."
    )
    assert cockpit.events("budget.raised")[-1].data["limit"] == 200
    # The new cap is checked afresh.
    assert await budgets.check(cockpit.app.state, cockpit.fid, now=time.time()) == []
    spend(cockpit, 50)
    assert await budgets.check(cockpit.app.state, cockpit.fid, now=time.time()) == ["budget.near"]
