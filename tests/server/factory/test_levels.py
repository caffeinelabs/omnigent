"""The levels block of the definition and ``GET /factories/{fid}/levels``.

Level keys and order are code; names, plurals, visibility, who answers, verbs,
authority, column titles, ``tiles_max`` and folding are data in the versioned
``structure`` block. Only owners change it and no agent proposes it. What is
hidden is computed per factory. Every value here is sample data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from omnigent.server.factory import definition, levels, tuning
from omnigent.server.factory import definition_blocks as blocks
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.definition_helpers import (
    definition_cockpit,
    propose,
    put,
    url,
    version,
)

REPO = "sample-org/sample-app"


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in definition_cockpit(world, db_uri, monkeypatch):
        yield value


async def _levels(cockpit: Cockpit, user: str = "alice") -> httpx.Response:
    return await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/levels", headers={"X-Test-User": user}
    )


def _level(body: dict[str, Any], key: str, tree: str = "product") -> dict[str, Any]:
    return next(row for row in body["trees"][tree]["levels"] if row["key"] == key)


def _change(path: str, value: Any) -> dict[str, Any]:
    return {"op": "replace", "path": path, "value": value}


# ── Defaults ───────────────────────────────────────────────────


def test_the_defaults_are_the_ladder_of_the_spec() -> None:
    doc = blocks.StructureDef().model_dump(mode="json")
    names = {key: doc["levels"][key]["name"] for key in blocks.PRODUCT_LEVELS}
    assert names == {
        "product": "Product",
        "goal": "Goal",
        "need": "Need",
        "feature": "Feature",
        "project": "Project",
        "task": "Task",
        "change": "Change",
        "run": "Run",
    }
    assert [doc["levels"][k]["name"] for k in blocks.FACTORY_LEVELS] == ["Factory", "Area", "Item"]
    assert doc["levels"]["goal"]["show"] == "when_any"
    assert doc["levels"]["change"]["show"] == "inline"
    assert doc["levels"]["run"]["debug"] is True
    assert doc["home"] == {"product": "feature", "factory": "area"}
    assert doc["responders"] == {
        "product": "atlas",
        "goal": "atlas",
        "need": "scout",
        "feature": "atlas",
        "study": "scout",
        "project": "lead",
        "task": "lead",
        "change": "lead",
        "run": None,
        "fx": "sentinel",
        "area": "sentinel",
        "item": "sentinel",
    }
    assert doc["verbs"]["project"] == ["talk", "add", "start", "team", "auto", "pause", "move"]
    assert doc["verbs"]["run"] == []
    assert doc["order"] == {
        "cols": ["First", "Then", "After that", "Last"],
        "later": "Later (held)",
    }
    assert (doc["tiles_max"], doc["fold_single_child"]) == (12, "implicit_only")
    # Authority per level: goals by people only, tasks by the lead at once, projects by autonomy.
    assert doc["levels"]["goal"]["authority"] == {
        "create": ["owner"],
        "move": [],
        "approval": "owner",
    }
    assert doc["levels"]["task"]["authority"]["approval"] == "none"
    assert doc["levels"]["project"]["authority"]["approval"] == "autonomy"
    assert doc["levels"]["need"]["authority"]["approval"] == "ledger"
    # A study sits on the feature level, but Scout starts one within the need's research budget.
    assert doc["kind_authority"] == {
        "study": {"create": ["scout"], "move": [], "approval": "budget"}
    }
    # No kind repeats its level's name: those kinds read the level (see /levels).
    assert not set(doc["kinds"]) & set(blocks.KIND_IS_LEVEL)
    assert set(blocks.KIND_IS_LEVEL.values()) <= set(blocks.PRODUCT_LEVELS + blocks.FACTORY_LEVELS)


def test_an_untouched_structure_is_not_stored() -> None:
    doc = definition.default_doc()
    assert "structure" in doc
    assert "structure" not in definition.stored_form(doc)
    doc["structure"]["levels"]["feature"]["name"] = "Outcome"
    assert definition.stored_form(doc)["structure"]["levels"]["feature"]["name"] == "Outcome"


def test_a_rename_stored_on_items_moves_to_the_areas() -> None:
    # A structure stored while levels were renamed from every factory item (lists replace).
    old = definition.default_doc()["structure"]
    old["levels"]["feature"]["name"] = "Outcome"
    old["verbs"].update(area=["talk"], item=["talk", "rename"])

    verbs = definition.effective({"structure": old})["structure"]["verbs"]

    assert (verbs["area"], verbs["item"]) == (["talk", "rename"], ["talk"])
    assert definition.effective({"structure": old})["structure"]["levels"]["feature"]["name"] == (
        "Outcome"
    )
    # Areas not stored keep their default (which renames); a structure without it stays.
    assert blocks.upgrade({"structure": {"verbs": {"item": ["rename", "talk"]}}}) == {
        "structure": {"verbs": {"item": ["talk"]}}
    }
    kept = {"structure": {"verbs": {"area": ["talk"], "item": ["talk"]}}}
    assert blocks.upgrade(kept) == kept


async def test_get_levels_returns_both_trees_with_defaults(cockpit: Cockpit) -> None:
    resp = await _levels(cockpit)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    product = body["trees"]["product"]
    assert (product["root"], product["name"], product["home"]) == ("product", "Product", "feature")
    assert [row["key"] for row in product["levels"]] == list(blocks.PRODUCT_LEVELS[1:])
    assert _level(body, "goal") == {
        "key": "goal",
        "name": "Goal",
        "plural": "Goals",
        "show": "when_any",
        # No goals in the definition: the level is hidden in this factory.
        "hidden": True,
        "authority": {"create": ["owner"], "move": [], "approval": "owner"},
    }
    assert _level(body, "change")["show"] == "inline"
    assert _level(body, "run")["debug"] is True
    # Nothing renders empty: ``show`` "always", ``debug`` false and ``hidden`` false are left out.
    assert set(_level(body, "need")) == {"key", "name", "plural", "authority"}
    factory = body["trees"]["factory"]
    assert (factory["root"], factory["name"], factory["home"]) == ("fx", "Factory", "area")
    assert [row["key"] for row in factory["levels"]] == ["area", "item"]
    assert body["responders"]["need"] == "scout" and body["responders"]["run"] is None
    assert body["verbs"]["feature"] == ["talk", "add", "auto", "pause", "move", "name"]
    assert body["kinds"]["adhoc"] == "Task (no ticket)"
    # Kinds named after their level read its name; the factory root is the fx level.
    assert (body["kinds"]["feature"], body["kinds"]["task"], body["kinds"]["factory"]) == (
        "Feature",
        "Task",
        "Factory",
    )
    assert body["kind_authority"]["study"] == {
        "create": ["scout"],
        "move": [],
        "approval": "budget",
    }
    assert body["order"]["cols"][0] == "First"
    assert (body["tiles_max"], body["fold_single_child"]) == (12, "implicit_only")
    assert body["version"] == version(cockpit)


async def test_anyone_who_reads_the_factory_reads_its_levels(cockpit: Cockpit) -> None:
    carol = await _levels(cockpit, user="carol")
    bob = await _levels(cockpit, user="bob")
    nowhere = await cockpit.client.get(
        "/v1/factory/factories/not-a-factory/levels", headers={"X-Test-User": "alice"}
    )

    assert carol.status_code == 200
    assert bob.status_code in (403, 404)
    assert nowhere.status_code == 404


# ── Renaming and hiding ────────────────────────────────────────


async def test_a_rename_by_put_definition_shows_in_levels(cockpit: Cockpit) -> None:
    before = version(cockpit)

    resp = await put(
        cockpit,
        _change("structure.levels.feature.name", "Outcome"),
        _change("structure.levels.feature.plural", "Outcomes"),
        _change("structure.levels.project.name", "Workstream"),
        _change("structure.levels.project.plural", "Workstreams"),
        note="our words (sample)",
    )

    assert resp.status_code == 200, resp.text
    body = (await _levels(cockpit)).json()
    assert body["version"] == version(cockpit) == before + 1
    assert (_level(body, "feature")["name"], _level(body, "feature")["plural"]) == (
        "Outcome",
        "Outcomes",
    )
    assert _level(body, "project")["name"] == "Workstream"
    # One change renames the level and the kind named after it.
    assert (body["kinds"]["feature"], body["kinds"]["project"]) == ("Outcome", "Workstream")
    assert body["kinds"]["study"] == "Study"
    # The keys never change; only what people read does.
    assert [row["key"] for row in body["trees"]["product"]["levels"]][2:4] == [
        "feature",
        "project",
    ]


async def test_only_owners_change_the_structure(cockpit: Cockpit) -> None:
    carol = await put(cockpit, _change("structure.levels.task.name", "Ticket"), user="carol")

    assert carol.status_code == 403
    assert definition.owner_only("structure.levels.task.name")
    assert definition.classify("structure.levels.task.name", "Task", "Ticket") == "B"
    assert _level((await _levels(cockpit)).json(), "task")["name"] == "Task"


@pytest.mark.parametrize("seat_key", ["lead_a", "lead_b"])
async def test_no_agent_proposes_the_structure(cockpit: Cockpit, seat_key: str) -> None:
    resp = await propose(
        cockpit, key=seat_key, changes=[_change("structure.levels.feature.name", "Outcome")]
    )

    assert resp.status_code == 403, resp.text
    assert error_code(resp) == "path_not_allowed"
    assert "the levels" in resp.json()["error"]["message"]
    assert cockpit.store.list_proposals(cockpit.fid) == []
    for seat in ("atlas", "sentinel", "scout", "project_lead"):
        assert not blocks.agent_may_propose(seat, "structure.verbs.task")
    assert tuning._klass([{"path": "structure.tiles_max", "before": 12, "after": 6}]) == "B"


@pytest.mark.parametrize(
    ("change", "status", "code"),
    [
        (_change("structure.levels.feature.name", "Project"), 422, "invalid_definition"),
        (_change("structure.levels.feature.plural", "projects"), 422, "invalid_definition"),
        (_change("structure.levels.feature.name", ""), 422, "invalid_definition"),
        (_change("structure.levels.goal.show", "sometimes"), 422, "invalid_definition"),
        (_change("structure.verbs.task", ["talk", "fly"]), 422, "invalid_definition"),
        (_change("structure.verbs.task", ["talk", "talk"]), 422, "invalid_definition"),
        (_change("structure.responders.need", "someone"), 422, "invalid_definition"),
        (_change("structure.levels.task.authority.create", ["lead", "lead"]), 422, None),
        (_change("structure.home.product", "run"), 422, "invalid_definition"),
        (_change("structure.order.cols", []), 422, "invalid_definition"),
        (_change("structure.tiles_max", 0), 422, "out_of_bounds"),
        (_change("structure.tiles_max", 61), 422, "out_of_bounds"),
        (_change("structure.levels.feature.key", "outcome"), 422, "tier0"),
        (_change("structure.levels.area_two.name", "Zone"), 422, "tier0"),
        # A kind named after its level is renamed with the level, never on its own.
        (_change("structure.kinds.feature", "Outcome"), 422, None),
        (_change("structure.kind_authority.study.approval", "whenever"), 422, None),
    ],
)
async def test_bad_structure_changes_are_refused(
    cockpit: Cockpit, change: dict[str, Any], status: int, code: str | None
) -> None:
    resp = await put(cockpit, change)

    assert resp.status_code == status, resp.text
    if code is not None:
        assert error_code(resp) == code


async def test_the_preview_of_a_rename_says_no_agent_proposes_it(cockpit: Cockpit) -> None:
    resp = await cockpit.client.post(
        url(cockpit, "/preview"),
        json={"changes": [_change("structure.levels.feature.name", "Outcome")]},
        headers={"X-Test-User": "alice"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["who"] == (
        "Owners (class B). You are an owner, so it applies when you press Apply. "
        "No agent proposes it."
    )
    (effect,) = [e for e in body["effects"] if e["kind"] == "data"]
    assert effect["paths"] == ["structure.levels.feature.name"]
    assert effect["text"].startswith("Level names and settings change how the cockpit shows work")


# ── Hidden per factory ─────────────────────────────────────────


async def test_goals_show_once_the_factory_has_one(cockpit: Cockpit) -> None:
    hidden = _level((await _levels(cockpit)).json(), "goal")
    resp = await put(
        cockpit,
        {"op": "add", "path": "strategy.goals", "value": {"statement": "More teams ship weekly"}},
    )

    assert resp.status_code == 200, resp.text
    shown = _level((await _levels(cockpit)).json(), "goal")
    assert hidden.get("hidden") is True
    assert "hidden" not in shown


async def test_hidden_when_empty_counts_implicit_parents(cockpit: Cockpit) -> None:
    resp = await put(
        cockpit,
        _change("structure.levels.need.show", "when_any"),
        _change("structure.levels.task.show", "when_any"),
        _change("structure.levels.run.show", "when_many"),
    )
    assert resp.status_code == 200, resp.text

    # The seeded project has no tasks: tasks are hidden, and so are runs; the
    # project still needs a feature and a need above it, so need shows.
    first = (await _levels(cockpit)).json()
    assert _level(first, "need").get("hidden") is None
    assert _level(first, "task")["hidden"] is True
    assert _level(first, "run")["hidden"] is True

    cockpit.store.upsert_task(cockpit.fid, "api-version", "route", session_ids=[_hex(1)])
    cockpit.store.upsert_task(cockpit.fid, "api-version", "docs", session_ids=[_hex(2)])
    then = (await _levels(cockpit)).json()
    assert "hidden" not in _level(then, "task")
    assert "hidden" not in _level(then, "run")


def _hex(n: int) -> str:
    return f"{n:032x}"


async def test_presence_gives_every_level_an_implicit_parent(cockpit: Cockpit) -> None:
    doc = definition.default_doc()
    cockpit.store.upsert_pr(
        cockpit.fid, REPO, 9630, url="https://example.invalid/9630", project_slug="api-version"
    )

    counts = levels.presence(cockpit.deps, cockpit.fid, doc)

    # One project and one PR stored; the PR's task and the project's feature
    # and need are implicit, so each of those levels has a node.
    assert counts["project"] == 1 and counts["change"] == 1
    assert counts["task"] == 1 and counts["feature"] == 1 and counts["need"] == 1
    assert counts["goal"] == 0 and counts["run"] == 0
    assert counts["item"] is None


@pytest.mark.parametrize(
    ("show", "count", "hidden"),
    [
        ("always", 0, False),
        ("inline", 0, False),
        ("when_any", 0, True),
        ("when_any", 1, False),
        ("when_many", 1, True),
        ("when_many", 2, False),
        ("when_any", None, False),
    ],
)
def test_hidden_rules(show: str, count: int | None, hidden: bool) -> None:
    assert levels.hidden(show, count) is hidden
