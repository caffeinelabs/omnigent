"""A project lead's structured update, and the project's health computed from facts.

The lead posts ``sys_factory_set(update={health, done, next, blocker})`` on
itself (``PATCH /v1/factory/sessions/{id}`` with ``update``); the server keeps
the latest one on the project row and the snapshot shows it next to the health
the facts give. Facts override a lead's "on track" when they say worse; a stale
update gives way to the facts.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from omnigent.entities.factory import FactoryProject
from omnigent.factory import COCKPIT_V2_ENV
from omnigent.server.factory import lead_update, nudge, snapshot, status
from omnigent.server.factory.charter import UPDATE_LINE, render_pl_charter
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.schemas import FactorySnapshot
from omnigent.stores.factory_store import FactoryStore
from tests.server.factory.conftest import FactoryApi, FactoryWorld
from tests.server.factory.test_agent_state_routes import PROJECT, REPO, add_team_roles

# ── Parsing ────────────────────────────────────────────────────


def test_an_update_is_trimmed_to_one_line_per_field() -> None:
    parsed = lead_update.parse_update(
        {"health": "on_track", "done": "  T-3   merged\n", "next": "T-4 review", "blocker": ""}
    )

    assert parsed == {"health": "on_track", "done": "T-3 merged", "next": "T-4 review"}


@pytest.mark.parametrize(
    ("raw", "field"),
    [
        ("on track", "update"),
        ({"done": "x"}, "health"),
        ({"health": "great", "done": "x"}, "health"),
        ({"health": "on_track"}, "done"),
        ({"health": "on_track", "done": 3}, "done"),
        ({"health": "on_track", "done": "x" * 281}, "done"),
        ({"health": "on_track", "done": "x", "eta": "soon"}, "eta"),
    ],
)
def test_bad_updates_name_their_field(raw: Any, field: str) -> None:
    with pytest.raises(lead_update.UpdateError) as caught:
        lead_update.parse_update(raw)
    assert caught.value.field == field


# ── Health from facts ──────────────────────────────────────────


def _project(**extra: Any) -> dict[str, Any]:
    return {
        "slug": "csv-export",
        "status": "active",
        "kind": "work",
        "overlay": None,
        "cost_usd": 12.0,
        "team_spec": {"budget_usd": 60},
        **extra,
    }


def _pr(number: int, checks: str, state: str = "open") -> dict[str, Any]:
    return {"number": number, "state": state, "gates": {"checks": checks}}


def test_green_checks_and_room_in_the_budget_are_on_track() -> None:
    health, facts, reasons = lead_update.computed_health(
        _project(), [_pr(12, "pass"), _pr(9, "fail", state="merged")], []
    )

    assert (health, facts, reasons) == ("on_track", ["checks green", "budget at 20 %"], [])


def test_each_fact_says_how_far_off_track() -> None:
    failing = lead_update.computed_health(_project(), [_pr(12, "fail")], [])
    near = lead_update.computed_health(_project(cost_usd=50.0), [], [])
    over = lead_update.computed_health(_project(cost_usd=61.0), [], [])
    stuck = lead_update.computed_health(_project(), [], [{"stuck": True}])
    stopped = lead_update.computed_health(_project(overlay="failed"), [], [])
    blocked = lead_update.computed_health(_project(overlay="blocked"), [], [])

    assert failing[0] == "at_risk" and failing[2] == ["checks failing on #12"]
    assert near[0] == "at_risk" and near[2] == ["budget at 83 %"]
    assert over[0] == "off_track" and over[2] == ["over budget (102 %)"]
    assert stuck[0] == "at_risk" and stuck[2] == ["1 member stuck"]
    assert stopped[0] == "off_track" and stopped[2] == ["the project stopped"]
    assert blocked[0] == "at_risk" and blocked[2] == ["an open question blocks it"]


def test_facts_override_a_lead_who_says_on_track_but_never_flatter_one() -> None:
    now = 10_000
    said = {"health": "on_track", "done": "T-3 merged", "at": now - 60, "by": "sid"}
    worried = {**said, "health": "at_risk"}

    _, overridden = lead_update.views(_project(), said, [_pr(12, "fail")], [], now=now)
    _, agreed = lead_update.views(_project(), said, [_pr(12, "pass")], [], now=now)
    _, cautious = lead_update.views(_project(), worried, [_pr(12, "pass")], [], now=now)

    assert overridden is not None and agreed is not None and cautious is not None
    assert (overridden["health"], overridden["source"], overridden["agrees"]) == (
        "at_risk",
        "facts",
        False,
    )
    assert (agreed["health"], agreed["source"], agreed["agrees"]) == ("on_track", "lead", True)
    assert (cautious["health"], cautious["source"], cautious["agrees"]) == (
        "at_risk",
        "lead",
        False,
    )


def test_a_stale_update_gives_way_to_the_facts() -> None:
    now = 100_000
    old = {"health": "at_risk", "next": "T-4", "at": now - lead_update.STALE_AFTER_S - 1}

    update, health = lead_update.views(_project(), old, [_pr(12, "pass")], [], now=now)

    assert update is not None and update["health"] == "at_risk"
    assert health is not None
    assert (health["health"], health["source"], health["stale"]) == ("on_track", "facts", True)


def test_no_update_and_closed_projects() -> None:
    update, health = lead_update.views(_project(), None, [], [], now=1)
    closed = lead_update.views(_project(status="cancelled"), None, [], [], now=1)
    shared = lead_update.views(_project(kind="integration"), None, [], [], now=1)

    assert update is None and health is not None
    assert (health["source"], health["agrees"]) == ("facts", None)
    assert closed == (None, None) and shared == (None, None)


def test_the_charter_budget_counts_when_the_team_spec_has_none() -> None:
    _, health = lead_update.views(
        _project(team_spec=None, cost_usd=45.0), None, [], [], budget_usd=50.0, now=1
    )

    assert health is not None and health["facts"] == ["budget at 90 %"]
    assert health["budget_usd"] == 50.0
    _, spec = lead_update.views(_project(cost_usd=45.0), None, [], [], budget_usd=50.0, now=1)
    assert spec is not None and spec["budget_usd"] != 50.0


# ── The route ──────────────────────────────────────────────────


@pytest_asyncio.fixture()
async def store(
    world: FactoryWorld, api: FactoryApi, tmp_path: Path
) -> AsyncIterator[FactoryStore]:
    add_team_roles(world, tmp_path)
    factory_store = factory_deps(api.app.state).factory_store
    factory_store.upsert_project(
        world.id("lead_a"),
        PROJECT,
        title="API version endpoint (sample)",
        repo=REPO,
        base_branch="main",
        charter={"goal": "GET /version returns the sha", "dod": ["curl /version"]},
        pl_session_id=world.id("pl"),
    )
    nudge.reset()
    status.reset()
    snapshot.reset_cache()
    yield factory_store
    nudge.reset()
    status.reset()
    snapshot.reset_cache()


def _post(api: FactoryApi, world: FactoryWorld, key: str, target: str, update: Any) -> Any:
    return api.client.patch(
        f"/v1/factory/sessions/{world.id(target)}",
        json={"from_session_id": world.id(key), "update": update},
    )


async def test_the_lead_posts_its_update_on_itself(
    api: FactoryApi, world: FactoryWorld, store: FactoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(COCKPIT_V2_ENV, "1")
    body = {"health": "on_track", "done": "T-3 merged", "next": "T-4 in review"}

    resp = await _post(api, world, "pl", "pl", body)

    assert resp.status_code == 200, resp.text
    assert resp.json()["update"]["health"] == "on_track"
    row = store.get_project(world.id("lead_a"), PROJECT)
    assert row is not None and row.lead_update is not None
    assert {k: row.lead_update[k] for k in ("health", "done", "next")} == body
    assert row.lead_update["by"] == world.id("pl")
    [event] = [e for e in store.list_events(world.id("lead_a")) if e.kind == "project.update"]
    assert event.project_slug == PROJECT and event.data["health"] == "on_track"
    assert event.summary.endswith(f"posted an update on {PROJECT}: on track")


async def test_without_the_new_cockpit_an_update_is_kept_but_not_logged(
    api: FactoryApi, world: FactoryWorld, store: FactoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(COCKPIT_V2_ENV, raising=False)

    resp = await _post(api, world, "pl", "pl", {"health": "at_risk", "blocker": "CI is red"})

    assert resp.status_code == 200, resp.text
    row = store.get_project(world.id("lead_a"), PROJECT)
    assert row is not None and row.lead_update is not None
    assert row.lead_update["health"] == "at_risk"
    assert [e for e in store.list_events(world.id("lead_a")) if e.kind == "project.update"] == []


def test_the_charter_asks_for_updates_only_with_the_new_cockpit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = FactoryProject(
        id="p1",
        factory_id="f1",
        slug=PROJECT,
        title="API version endpoint (sample)",
        repo=REPO,
        charter={"goal": "GET /version returns the sha"},
    )

    monkeypatch.delenv(COCKPIT_V2_ENV, raising=False)
    assert "sys_factory_set(update=" not in render_pl_charter(row)
    monkeypatch.setenv(COCKPIT_V2_ENV, "1")
    assert UPDATE_LINE in render_pl_charter(row).splitlines()


async def test_only_the_projects_lead_posts_its_update(
    api: FactoryApi, world: FactoryWorld, store: FactoryStore
) -> None:
    body = {"health": "on_track", "done": "x"}
    calls = [("lead_a", "pl"), ("ic", "ic"), ("lead_a", "lead_a"), ("pl", "ic")]

    for key, target in calls:
        resp = await _post(api, world, key, target, body)
        assert resp.status_code == 403, (key, target)
        assert resp.json()["error"]["code"] == "not_your_project"
    bad = await _post(api, world, "pl", "pl", {"health": "fine", "done": "x"})

    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_update"
    row = store.get_project(world.id("lead_a"), PROJECT)
    assert row is not None and row.lead_update is None


async def test_the_snapshot_shows_the_update_and_the_facts(
    api: FactoryApi, world: FactoryWorld, store: FactoryStore
) -> None:
    fid = world.id("lead_a")
    store.upsert_pr(
        fid,
        REPO,
        9630,
        project_slug=PROJECT,
        state="open",
        gates={"checks": "fail", "evidence": {}},
    )
    await _post(api, world, "pl", "pl", {"health": "on_track", "next": "T-4 in review"})

    raw = await snapshot.build_snapshot(api.app.state, world.get("lead_a"))

    view = FactorySnapshot.model_validate(raw.body)
    [card] = [p for p in view.projects if p.slug == PROJECT]
    assert card.update is not None and card.update.next == "T-4 in review"
    assert card.health is not None
    assert (card.health.health, card.health.source, card.health.agrees) == (
        "at_risk",
        "facts",
        False,
    )
    assert card.health.reasons == ["checks failing on #9630"]
