"""The level projection (``nodes.py``) on the sample factory at 13:40 and at scale.

Every projection here passes :func:`~omnigent.server.factory.nodes.assert_levels`:
one parent per node, exactly one level up, in its own tree. The pages are the
shapes the web's node source reads (``node_views.NodePageView``); the JSON the
web's contract test renders is written from :data:`WEB_PAGES` here
(``OMNIGENT_UPDATE_NODE_PAGES=1`` rewrites it). Every name is sample data.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from omnigent.server.factory import definition, nodes
from omnigent.server.factory.node_views import validate_level, validate_page
from omnigent.server.factory.nodes import NO_GOAL, PAGE_SIZE, LevelError, Tree, assert_levels
from tests.server.factory.node_fixtures import (
    ASK_MESSAGE,
    NOW,
    SHORTLIST,
    SHORTLIST_MESSAGE,
    WAVE_1,
    factory_1340,
    hex_id,
    scale_factory,
    structure,
)

WEB_FIXTURES = Path(__file__).resolve().parents[3] / "web/src/components/factory/__fixtures__"
WEB_FIXTURE = WEB_FIXTURES / "nodePages.server.json"
WEB_LEVEL_FIXTURE = WEB_FIXTURES / "nodeLevels.server.json"
UPDATE_ENV = "OMNIGENT_UPDATE_NODE_PAGES"
ASK = f"ask:{ASK_MESSAGE}"
PICK = f"ask:{SHORTLIST_MESSAGE}"
WEB_PAGES = (
    "product",
    ASK,
    "I-1",
    "assistant-core",
    "assistant-panel-ux",
    "T-16",
    "PR-9876",
    "build-tier-drafts-3",
    "feature:from-C-13",
    "fx",
    "fx:rules",
    "fx:environments",
    "fx:health",
    "fx:definition",
    "base-env",
)
WEB_LEVELS = (("product", "feature"), ("product", "project"), ("I-1", "task"))
"""Level views the web's contract test reads (``<ref>/<level>``): Home lists features."""


@pytest.fixture()
def tree() -> Tree:
    return assert_levels(Tree(factory_1340()))


def _refs(items: list[dict[str, Any]]) -> list[str]:
    return [i["ref"] for i in items]


def _page(tree: Tree, ref: str, **kwargs: Any) -> dict[str, Any]:
    page = tree.page(ref, **kwargs)
    assert page is not None, ref
    return validate_page(page)


def _level(tree: Tree, ref: str, level: str, **kwargs: Any) -> dict[str, Any]:
    view = tree.level_view(ref, level, **kwargs)
    assert view is not None, (ref, level)
    return validate_level(view)


# ── The one-parent rule ────────────────────────────────────────


def test_the_sample_keeps_one_parent_one_level_up(tree: Tree) -> None:
    assert nodes.check_levels(tree) == []
    for node in tree.nodes.values():
        if node.parent is not None:
            parent = tree.nodes[node.parent]
            assert tree.level_index(parent) == tree.level_index(node) - 1, node.ref
            assert parent.tree == node.tree


@pytest.mark.parametrize(
    "variant",
    ["goals", "hidden", "never_fold", "renamed", "scale", "small_scale", "empty"],
)
def test_every_projection_keeps_the_rule(variant: str) -> None:
    facts = factory_1340()
    hidden: frozenset[str] = frozenset()
    if variant == "goals":
        facts.goals = [{"id": "G-1", "statement": "More teams ship weekly (sample)"}]
    elif variant == "hidden":
        hidden = frozenset({"assistant-core", "ux-slash-focus"})
    elif variant == "never_fold":
        facts.structure = {**structure(), "fold_single_child": "never"}
    elif variant == "renamed":
        doc = structure()
        doc["levels"]["feature"].update(name="Outcome", plural="Outcomes")
        doc["levels"]["project"].update(name="Workstream", plural="Workstreams")
        facts.structure = doc
    elif variant == "scale":
        facts = scale_factory()
    elif variant == "small_scale":
        facts = scale_factory(features=2, projects=3, tasks=7)
    else:
        facts = dataclasses.replace(
            facts, initiatives=[], projects=[], tasks=[], prs=[], proposals=[], decisions=[]
        )
    tree = assert_levels(Tree(facts, hidden=hidden))
    for ref in list(tree.nodes):
        _page(tree, ref)


def test_a_node_out_of_place_is_caught(tree: Tree) -> None:
    tree.nodes["T-16"].parent = "I-1"
    tree.nodes["base-env"].parent = "assistant-core"

    with pytest.raises(LevelError) as caught:
        assert_levels(tree)

    assert "T-16 (task) sits under I-1 (feature), not one level up" in str(caught.value)
    assert "base-env: parent assistant-core is in the product tree" in str(caught.value)


# ── Luka's factory at 13:40 (sample) ───────────────────────────


def test_goal_is_hidden_and_needs_hang_off_the_product(tree: Tree) -> None:
    page = _page(tree, "product")

    assert tree.ladder["product"] == [
        "product",
        "need",
        "feature",
        "project",
        "task",
        "change",
        "run",
    ]
    assert [lv["level"] for lv in page["levels_below"]] == [
        "need",
        "feature",
        "project",
        "task",
        "change",
        "run",
    ]
    assert all(lv["count"] or lv["later"] for lv in page["levels_below"])
    assert page["levels_below"][-1]["debug"] is True
    assert "No goals are set yet, so that level is hidden" in page["node"]["why_html"]
    assert page["home"] == "feature"
    assert page["children"]["level"] == "need"
    assert not [n for n in tree.nodes.values() if n.level == "goal"]


def test_needs_without_a_goal_go_under_no_goal_yet_once_goals_exist() -> None:
    facts = factory_1340()
    facts.goals = [{"id": "G-1", "statement": "More teams ship weekly (sample)"}]
    tree = assert_levels(Tree(facts))

    assert tree.ladder["product"][:3] == ["product", "goal", "need"]
    assert tree.nodes[ASK].parent == NO_GOAL
    page = _page(tree, "product")
    assert _refs(page["children"]["items"]) == ["G-1", NO_GOAL]
    assert page["children"]["items"][1]["implicit"] is True


def test_build_tier_drafts_folds_into_its_implicit_parents(tree: Tree) -> None:
    root = _page(tree, "product")
    [tile] = [i for i in root["children"]["items"] if i["ref"] == "build-tier-drafts-3"]
    page = _page(tree, "build-tier-drafts-3")

    assert tile["level"] == "project" and tile["base"] == "main"
    assert tile["folded_from"] == ["feature:from-C-12", "need:from-C-12"]
    assert root["children"]["via_fold"] == 1
    assert [(c["ref"], c.get("folded")) for c in page["breadcrumb"]] == [
        ("product", None),
        ("need:from-C-12", True),
        ("feature:from-C-12", True),
    ]
    # An implicit parent's ref answers as its only child.
    for ref in ("feature:from-C-12", "need:from-C-12"):
        assert _page(tree, ref)["node"]["ref"] == "build-tier-drafts-3"
    # Its two PRs no task owns: implicit adhoc tasks, each folded into its PR.
    assert _refs(page["children"]["items"]) == ["PR-9856", "PR-9857"]
    assert all(i["state"] == "waiting" for i in page["children"]["items"])


def test_the_shortlist_is_one_implicit_feature_with_four_projects(tree: Tree) -> None:
    page = _page(tree, "feature:from-C-13")

    assert page["node"]["implicit"] is True and page["node"]["kind"] == "proposal"
    assert page["node"]["title"].startswith("From C-13")
    assert [c["ref"] for c in page["breadcrumb"]] == ["product", PICK]
    assert _refs(page["children"]["items"]) == list(SHORTLIST)
    assert page["children"]["columns"] is None
    assert page["node"]["state"] == "done" and page["status"]["label"] == "Done"
    assert page["node"]["progress"] == {"value": 1.0, "label": "4/4", "what": "projects"}
    assert "name" in [v["verb"] for v in page["verbs"]]
    need = _page(tree, PICK)["node"]
    assert need["kind"] == "ask" and need["evidence"]["items"][0]["kind"] == "request"
    assert need["evidence"]["items"][0]["text"].startswith("Your shortlist pick")


def test_base_env_lands_in_the_factory_tree(tree: Tree) -> None:
    page = _page(tree, "base-env")
    product = _page(tree, "product")

    assert [c["ref"] for c in page["breadcrumb"]] == ["fx", "fx:environments"]
    assert page["node"]["level"] == "item" and page["node"]["kind"] == "env"
    assert "results" not in page
    assert "base-env" not in [n.ref for n in tree.subtree("product")]
    assert product["rollup"]["counts"][1]["count"] == 9  # the product's projects only
    # Parked in planning with its lead idle: it waits, it does not claim to move.
    assert page["node"]["state"] == "waiting"
    assert page["status"]["detail"] == "in planning · nobody is working on it"
    assert _page(tree, "fx:environments")["node"]["state"] == "waiting"
    assert _page(tree, "fx")["rollup"]["moving_anywhere"] is True  # Atlas works
    assert product["rollup"]["moving_anywhere"] is False


def test_a_project_between_stages_moves_only_while_someone_works() -> None:
    facts = factory_1340()
    views = dict(facts.views)
    views["base-env"] = {**views["base-env"], "pl": {"title": "Mae", "running": True}}
    views["build-tier-drafts-3"] = {
        **views["build-tier-drafts-3"],
        "stage": "building",
        "waiting_on": [],
        "team": [{"session_id": hex_id(950), "title": "Kai", "running": True}],
    }
    facts.views = views
    tree = assert_levels(Tree(facts))

    assert _page(tree, "base-env")["node"]["state"] == "moving"
    drafts = _page(tree, "build-tier-drafts-3")
    # Its PRs wait for a person to merge them; the project itself has Kai working.
    assert drafts["node"]["state"] == "waiting"
    assert tree.nodes["build-tier-drafts-3"].own == "moving"


def test_a_finished_project_is_done_whatever_tasks_it_left() -> None:
    facts = factory_1340()
    left = [
        dataclasses.replace(
            facts.tasks[0],
            id=hex_id(230 + n),
            number=30 + n,
            project_slug="ux-status-page",
            key=f"left-{n}",
            title=f"Left over {n}",
            state=state,
            pr_id=None,
            session_ids=[],
            deps=[],
        )
        for n, state in enumerate(("planned", "failed"))
    ]
    done = dataclasses.replace(
        facts.tasks[0],
        id=hex_id(240),
        number=40,
        project_slug="ux-status-page",
        key="page",
        title="Status page",
        state="done",
        pr_id=None,
        session_ids=[],
        deps=[],
    )
    facts.tasks = [*facts.tasks, *left, done]
    tree = assert_levels(Tree(facts))

    project = _page(tree, "ux-status-page")
    feature = _page(tree, "feature:from-C-13")

    assert project["node"]["state"] == "done"
    assert project["status"]["detail"] == "closed · 2 unfinished tasks left as history"
    # T-40 and the implicit task of its merged PR; the two left over count nowhere.
    assert project["node"]["progress"] == {"value": 1.0, "label": "2/2", "what": "tasks"}
    assert feature["node"]["state"] == "done"
    assert feature["node"]["progress"] == {"value": 1.0, "label": "4/4", "what": "projects"}
    rows = {i["ref"]: i for i in project["children"]["items"]}
    assert {rows[r]["state"] for r in ("T-30", "T-31", "T-40")} == {"done"}
    assert rows["T-31"]["status_compact"]["label"] == "Closed"
    assert rows["T-31"]["status_compact"]["detail"] == "failed when its project closed"
    assert "broken" not in str(_page(tree, "product")["levels_below"])


def test_assistant_core(tree: Tree) -> None:
    page = _page(tree, "assistant-core")

    assert page["node"]["state"] == "needs"  # D-45 (Apply C-16) blocks it
    assert page["node"]["needs"] == {"open": 1, "blocking": 1}
    assert [c["ref"] for c in page["breadcrumb"]] == ["product", ASK, "I-1"]
    assert page["chips"] == [
        {
            "kind": "needed by",
            "ref": "assistant-panel-ux",
            "title": "assistant-panel-ux",
            "state": "waiting",
        }
    ]
    assert [lv["level"] for lv in page["levels_below"]] == ["task", "change", "run"]
    children = page["children"]
    assert children["columns"] == [{"title": "First", "col": 0}, {"title": "Then", "col": 1}]
    assert [(i["ref"], i["state"], i["col"]) for i in children["items"]] == [
        ("T-15", "done", 0),
        ("T-16", "waiting", 1),
    ]
    assert children["tiles"] is False
    # Task progress by weight: T-15 (120 min) done of 180.
    assert page["node"]["progress"] == {"value": 0.6667, "label": "1/2", "what": "tasks"}
    assert page["rollup"]["cost"] == 25.13 and page["rollup"]["budget"] == 150.0
    assert page["thread"] == {"key": "project:assistant-core", "unread": 0, "responder": "Juno"}
    # The status line is the blocker path (test_node_status.py has every case).
    assert (page["status"]["kind"], page["status"]["label"]) == ("needs", "Not moving:")
    assert page["status"]["decision"]["id"] == "D-45"


def test_agent_assistant(tree: Tree) -> None:
    page = _page(tree, "I-1")
    children = page["children"]

    assert page["node"]["progress"] == {
        "value": 0.3333,
        "label": "2/6",
        "what": "acceptance",
        "secondary": 0.6667,
        "growing": True,
    }
    steps = page["node"]["acceptance"]["steps"]
    assert [s["result"] for s in steps] == ["none", "pass", "pass", "none", "none", "fail"]
    assert _refs(children["items"]) == list(WAVE_1)
    assert [i["col"] for i in children["items"]] == [0, 1, 1, 1]
    assert children["columns"] == [{"title": "First", "col": 0}, {"title": "Then", "col": 1}]
    assert [(i["ref"], i["col"], i["held_for"]) for i in children["later"]] == [
        ("ads-integration", "later", "wave 2: ads network and growth"),
        ("growth-seo", "later", "wave 2: ads network and growth"),
        ("sales-skill", "later", "wave 3: sales and promotion"),
        ("promotion-skill", "later", "wave 3: sales and promotion"),
    ]
    assert children["later_title"] == "Later (held)"
    [ghost] = children["ghosts"]
    assert (ghost["ref"], ghost["state"], ghost["tag"]) == ("live-search-fix", "ghost", "C-19")
    assert children["total"] == 9
    # D-45 on assistant-core, D-49 on its ghost; C-14 / C-15 block nothing.
    assert page["node"]["needs"] == {"open": 4, "blocking": 2}
    assert page["node"]["state"] == "needs"
    assert page["thread"]["key"] == "feature:I-1"


def test_the_panel_waits_on_assistant_core(tree: Tree) -> None:
    page = _page(tree, "assistant-panel-ux")

    assert page["node"]["state"] == "waiting"
    assert page["node"]["progress"] == {
        "value": 0,
        "label": "–",
        "what": "not planned",
        "growing": True,
    }
    first, met = page["chips"]
    assert first == {
        "kind": "depends on",
        "ref": "assistant-core",
        "title": "assistant-core",
        "state": "needs you",
        "reason": "needs the session API and per-turn debit",
    }
    assert (met["title"], met["state"], met["tone"]) == ("3 met links", "3 had no reason", "warn")
    assert [c["ref"] for c in met["list"][0]["chips"]] == list(SHORTLIST[:3])
    assert met["list"][0]["chips"][0]["state"] == "done · no reason given, across features"
    assert page["levels_below"] == []


def test_the_product(tree: Tree) -> None:
    page = _page(tree, "product")

    assert _refs(page["children"]["items"]) == [ASK, "build-tier-drafts-3", PICK]
    assert page["rollup"]["counts"] == [
        {"key": "feature", "name": "Feature", "plural": "Features", "count": 3},
        {"key": "project", "name": "Project", "plural": "Projects", "count": 9, "later": 4},
    ]
    assert page["results"] == {
        "merged": 7,
        "in_base_env": 7,
        "open": ["PR-9856", "PR-9857"],
        "readouts": "not measured",
    }
    assert page["node"]["needs"] == {"open": 4, "blocking": 2}
    assert page["rollup"]["in_base_env"] == {"sha": "817480d", "try_it": True}
    assert page["rollup"]["moving_anywhere"] is False
    assert page["breadcrumb"] == [] and page["thread"]["key"] == "factory"


def test_a_task_and_its_change(tree: Tree) -> None:
    task = _page(tree, "T-16")
    done = _page(tree, "T-15")
    change = _page(tree, "PR-9876")

    assert task["node"]["state"] == "waiting"  # held for C-16
    assert task["breadcrumb"][-1]["ref"] == "assistant-core"
    assert done["node"]["in_base_env"] is True
    assert _refs(done["children"]["items"]) == ["PR-9876"]
    assert change["breadcrumb"][-1] == {
        "ref": "T-15",
        "level": "task",
        "level_name": "Task",
        "title": "Sessions, tool registry, flagged chat API",
        "state": "done",
        "tag": "T-15",
    }
    assert change["thread"]["key"] == f"pr:{hex_id(9876)}"
    [run] = change["children"]["items"]
    assert run["ref"] == f"run:{hex_id(701)}" and run["level"] == "run"
    assert _page(tree, run["ref"])["thread"]["key"] == "task:T-15"


def test_the_factory_tree(tree: Tree) -> None:
    page = _page(tree, "fx")
    rules = _page(tree, "fx:rules")

    assert _refs(page["children"]["items"]) == [
        "fx:rules",
        "fx:team",
        "fx:environments",
        "fx:health",
        "fx:definition",
    ]
    assert _refs(rules["children"]["items"]) == ["C-17", "C-18"]
    assert rules["node"]["state"] == "needs"  # D-50 and D-51
    assert page["thread"] == {"key": "improvements", "unread": 0, "responder": "Sentinel"}
    assert page["rollup"]["team"] == {"working": [{"name": "Atlas", "working": True}], "idle": 2}
    # Every area says in one line what it holds; none is an empty tile.
    for area in page["children"]["items"]:
        assert _page(tree, area["ref"])["node"]["why_html"], area["ref"]


def test_the_team_draws_working_agents_and_counts_idle_ones(tree: Tree) -> None:
    team = _page(tree, "fx:team")

    # Atlas works; Sentinel is idle but an open ask (D-37) names it; Juno is only counted.
    assert [i["title"] for i in team["children"]["items"]] == ["Atlas", "Sentinel"]
    assert team["node"]["idle"] == 2 and team["node"]["short"] == "5 members · 1 running"
    assert team["node"]["working"] == [{"name": "Atlas", "working": True}]
    assert _page(tree, "fx:environments")["node"]["short"] == (
        "Base env sample-main @817480d · ready"
    )
    assert _page(tree, "fx:definition")["node"]["short"] == "version 14"


def test_the_environments_area_shows_the_base_env(tree: Tree) -> None:
    env = _page(tree, "fx:environments")["node"]["env"]

    assert env == _page(tree, "product")["node"]["env"]
    assert (env["branch"], env["sha"], env["app_url"]) == (
        "sample-main",
        "817480d",
        "https://app.sample.test",
    )
    # The env chip's sheet opens the area that shows it; `ref` is the project setting it up.
    assert (env["area"], env["ref"]) == ("fx:environments", "base-env")
    assert "env" not in _page(tree, "fx:health")["node"]


def test_levels_are_renamed_from_the_definition_area_only(tree: Tree) -> None:
    verbs = _page(tree, "fx:definition")["verbs"]

    assert [v["verb"] for v in verbs] == ["talk", "rename"]
    assert {k: verbs[1][k] for k in ("label", "key", "enabled")} == {
        "label": "Rename levels",
        "key": "r",
        "enabled": True,
    }
    # No other node of the factory tree renames anything (rules, envs, alarms, agents).
    for ref in ("fx", "fx:rules", "fx:health", "C-17", "base-env", "D-48"):
        assert "rename" not in [v["verb"] for v in _page(tree, ref)["verbs"]], ref
    readers = _page(tree, "fx:definition", can_edit=False)
    assert not readers["verbs"][1]["enabled"] and readers["verbs"][1]["reason"]
    # The page shows the levels to every viewer, whether or not they rename them.
    assert readers["node"]["levels"] is True
    assert "levels" not in _page(tree, "fx:rules")["node"]


def test_a_definition_stored_with_rename_on_items_still_renames_from_its_area() -> None:
    old = definition.default_doc()["structure"]
    old["levels"]["feature"]["name"] = "Outcome"
    old["verbs"].update(area=["talk"], item=["talk", "rename"])
    facts = factory_1340()
    facts.structure = definition.effective({"structure": old})["structure"]
    tree = Tree(facts)

    assert [v["verb"] for v in _page(tree, "fx:definition")["verbs"]] == ["talk", "rename"]
    assert "rename" not in [v["verb"] for v in _page(tree, "C-17")["verbs"]]


def test_in_base_env_needs_every_merged_change() -> None:
    facts = factory_1340()
    late = dataclasses.replace(
        facts.prs[0],
        id=hex_id(9890),
        number=9890,
        project_slug="connectors-core",
        task_id=None,
        merged_at=NOW,
        merged_sha="9890000" + "0" * 33,
        title="After the deploy",
    )
    facts.prs = [*facts.prs, late]
    tree = assert_levels(Tree(facts))

    product = _page(tree, "product")
    connectors = _page(tree, "connectors-core")

    assert product["results"]["merged"] == 8 and product["results"]["in_base_env"] == 7
    assert "in_base_env" not in product["rollup"]
    assert "in_base_env" not in connectors["rollup"]
    assert _page(tree, "skill-discovery")["rollup"]["in_base_env"] == {
        "sha": "817480d",
        "try_it": False,
    }


def test_a_pr_number_two_repos_share_keeps_one_home_each() -> None:
    facts = factory_1340()
    other = dataclasses.replace(
        facts.prs[0],
        id=hex_id(19876),
        repo="sample-org/other-app",
        task_id=None,
        project_slug="connectors-core",
        merged_sha="abc1234" + "0" * 33,
        title="Same number, other repo",
    )
    closed = dataclasses.replace(
        facts.prs[1],
        id=hex_id(29882),
        repo="sample-org/third-app",
        state="closed",
        merged_at=None,
        merged_sha=None,
    )
    facts.prs = [other, *facts.prs, closed]
    tree = assert_levels(Tree(facts))

    mine = _page(tree, "PR-9876")
    theirs = _page(tree, f"commit:{other.merged_sha}")
    connectors = _page(tree, "connectors-core")

    assert mine["breadcrumb"][-1]["ref"] == "T-15"
    assert mine["thread"]["key"] == f"pr:{hex_id(9876)}"
    assert theirs["node"]["tag"] == "sample-org/other-app#9876"
    assert theirs["thread"]["key"] == f"pr:{hex_id(19876)}"
    # A closed row never takes the number: PR-9882 is still the merged one.
    assert _page(tree, "PR-9882")["node"]["title"] == "connectors-core"
    assert connectors["results"]["merged"] == 2
    assert _page(tree, "product")["results"]["merged"] == 8


def test_unknown_refs_have_no_page_and_spellings_resolve(tree: Tree) -> None:
    assert tree.page("T-99") is None
    assert tree.page("not a ref") is None
    assert _page(tree, "task:T-16")["node"]["ref"] == "T-16"
    assert _page(tree, "project:assistant-core")["node"]["ref"] == "assistant-core"


def test_a_viewer_who_cannot_read_a_project_sees_nothing_of_it() -> None:
    tree = assert_levels(Tree(factory_1340(), hidden={"assistant-core"}))

    assert tree.page("assistant-core") is None and tree.page("T-16") is None
    page = _page(tree, "I-1")
    assert "assistant-core" not in _refs(page["children"]["items"])
    assert page["node"]["needs"] == {"open": 3, "blocking": 1}  # D-45 is not shown
    assert _page(tree, "product")["node"]["needs"] == {"open": 3, "blocking": 1}
    assert _page(tree, "assistant-panel-ux")["chips"][0]["title"] == "3 met links"


def test_readers_see_the_verbs_but_cannot_use_them(tree: Tree) -> None:
    verbs = _page(tree, "assistant-core", can_edit=False)["verbs"]

    assert [v["verb"] for v in verbs] == ["talk", "add", "team", "auto", "pause", "move"]
    assert all(not v["enabled"] and v["reason"] for v in verbs)


def test_renamed_levels_name_every_page() -> None:
    facts = factory_1340()
    doc = structure()
    doc["levels"]["feature"].update(name="Outcome", plural="Outcomes")
    facts.structure = doc
    tree = Tree(facts)

    page = _page(tree, "I-1")
    assert page["node"]["level_name"] == "Outcome" and page["node"]["kind_name"] == "Outcome"
    assert _page(tree, "product")["levels_below"][1]["name"] == "Outcomes"


# ── Paging and speed ───────────────────────────────────────────


def test_children_page_at_60_grouped_by_state() -> None:
    tree = assert_levels(Tree(scale_factory(features=1, projects=1, tasks=130)))

    first = _page(tree, "proj-00")
    second = _page(tree, "proj-00", cursor=first["children"]["next"])
    third = _page(tree, "proj-00", cursor=second["children"]["next"])

    assert [len(p["children"]["items"]) for p in (first, second, third)] == [60, 60, 10]
    assert (first["children"]["next"], second["children"]["next"]) == ("60", "120")
    assert "next" not in third["children"]
    assert all(p["children"]["total"] == 130 for p in (first, second, third))
    assert first["children"]["grouped_by_state"] is True
    states = [i["state"] for p in (first, second, third) for i in p["children"]["items"]]
    ranks = [nodes.STATE_RANK[s] for s in states]
    assert ranks == sorted(ranks, reverse=True)
    assert len({i["ref"] for p in (first, second, third) for i in p["children"]["items"]}) == 130
    with pytest.raises(ValueError):
        tree.page("proj-00", cursor="7")


def test_every_page_of_a_13_45_360_factory_is_fast() -> None:
    facts = scale_factory()
    timings: list[float] = []
    started = time.perf_counter()
    tree = assert_levels(Tree(facts))
    build = time.perf_counter() - started
    for ref in list(tree.nodes):
        begin = time.perf_counter()
        page = _page(tree, ref)
        timings.append(time.perf_counter() - begin)
        assert len(page["children"]["items"]) <= PAGE_SIZE
    timings.sort()
    p95 = timings[int(len(timings) * 0.95)]

    assert sum(1 for n in tree.nodes.values() if n.level == "feature") == 13
    assert sum(1 for n in tree.nodes.values() if n.level == "project") == 45
    assert sum(1 for n in tree.nodes.values() if n.level == "task") == 360
    assert build + p95 < 0.3, (build, p95)


# ── Level views and asks that hold work ────────────────────────


def test_home_lists_every_feature_under_the_need_it_came_from(tree: Tree) -> None:
    home = _page(tree, "product")
    view = _level(tree, "product", home["home"])

    groups = {g["parent"]["ref"]: _refs(g["items"]) for g in view["groups"]}
    assert groups == {
        ASK: ["I-1"],
        PICK: ["feature:from-C-13"],
        "need:from-C-12": ["build-tier-drafts-3"],
    }
    # The ask with the most at stake comes first; the folded need keeps its crumb.
    assert view["groups"][0]["parent"]["ref"] == ASK
    folded = next(g for g in view["groups"] if g["parent"]["ref"] == "need:from-C-12")
    assert folded["parent"]["folded"] is True and folded["parent"]["implicit"] is True
    assert view["via_fold"] == 1 and view["total"] == 3
    assert next(g for g in view["groups"] if g["parent"]["ref"] == ASK)["parent"]["evidence"]


def test_a_level_view_folds_groups_that_are_all_done(tree: Tree) -> None:
    view = _level(tree, "product", "project")

    done = [g["parent"]["ref"] for g in view["groups"] if g.get("folded_done")]
    assert done == ["feature:from-C-13"]
    assert "done_groups" not in view  # one all-done group folds in place
    wave = next(g for g in view["groups"] if g["parent"]["ref"] == "I-1")
    assert wave["later"] and wave["items"][0]["state"] != "done"
    assert tree.level_view("T-16", "project") is None
    assert tree.level_view("nope", "task") is None
    with pytest.raises(ValueError):
        tree.level_view("product", "task", cursor="I-1@7")


def test_a_proposals_decision_holds_what_it_proposes(tree: Tree) -> None:
    """Proposal decisions are stored as blocking nothing; the tree reads what waits on them."""
    decisions = {d.ref: d for d in tree.facts.decisions}

    assert not any(d.blocking for d in decisions.values() if d.source == "proposal")
    held = {ref: tree.blocks(d) for ref, d in decisions.items()}
    assert held == {
        "D-45": True,  # C-16 changes assistant-core, which is active
        "D-43": False,  # C-14 and C-15 change finished projects
        "D-44": False,
        "D-49": True,  # C-19 adds a project
        "D-50": True,  # rules
        "D-51": True,
        "D-37": False,  # an agent's ask that says it blocks nothing
    }


def test_level_views_of_a_13_45_360_factory_are_fast_and_paged() -> None:
    tree = assert_levels(Tree(scale_factory()))

    timings: dict[str, float] = {}
    for level in ("feature", "project", "task"):
        started = time.perf_counter()
        view = _level(tree, "product", level)
        timings[level] = time.perf_counter() - started
        assert all(len(g["items"]) <= PAGE_SIZE for g in view["groups"]), level
        assert view["total"] == sum(
            1 for n in tree.at_level("product", level) if not n.ghost and not n.later
        )

    assert max(timings.values()) < 0.3, timings


# ── The web's contract fixture ─────────────────────────────────


def _web_pages() -> dict[str, Any]:
    tree = assert_levels(Tree(factory_1340()))
    return {ref: _page(tree, ref) for ref in WEB_PAGES}


def _web_levels() -> dict[str, Any]:
    tree = assert_levels(Tree(factory_1340()))
    return {f"{ref}/{level}": _level(tree, ref, level) for ref, level in WEB_LEVELS}


@pytest.mark.parametrize(
    ("fixture", "build"), [(WEB_FIXTURE, _web_pages), (WEB_LEVEL_FIXTURE, _web_levels)]
)
def test_the_web_fixture_is_what_the_server_returns(fixture: Path, build: Any) -> None:
    """The web's ``nodeWire.server.test.tsx`` renders these through the live source."""
    text = json.dumps(build(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if os.environ.get(UPDATE_ENV):
        fixture.write_text(text, encoding="utf-8")
    assert fixture.exists(), f"run with {UPDATE_ENV}=1 to write {fixture.name}"
    assert json.loads(fixture.read_text(encoding="utf-8")) == json.loads(text), (
        f"the node API changed: rerun with {UPDATE_ENV}=1 and check the web test"
    )
