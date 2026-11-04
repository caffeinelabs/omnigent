"""Needs you and Find on the sample factory at 13:40 (S9).

Needs you ranks every ask by the work it holds: D-45 (T-16, and through
assistant-core the panel), the merges on ``main`` as one ask, D-49, and then
the two that block nothing (C-14, C-15). The alarms the server closed come
with their causes and the factory tree's asks are a count. Find reads refs
(``T-16``, ``#9876``), what a decision or proposal is about (``C-16``) and
titles, each with its breadcrumb. Every name is sample data.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from omnigent.server.factory import node_sheets
from omnigent.server.factory.node_views import FindView, NeedsYouView, validate
from omnigent.server.factory.nodes import Tree, assert_levels
from tests.server.factory.node_fixtures import ASK_MESSAGE, factory_1340, scale_factory

ASK = f"ask:{ASK_MESSAGE}"


@pytest.fixture()
def tree() -> Tree:
    return assert_levels(Tree(factory_1340()))


def _needs(tree: Tree, which: str = "product") -> dict[str, Any]:
    return validate(NeedsYouView, node_sheets.needs_you(tree, which))  # type: ignore[arg-type]


def _find(tree: Tree, q: str, **kwargs: Any) -> list[tuple[str, str, str | None]]:
    view = validate(FindView, node_sheets.find(tree, q, **kwargs))
    return [(i["ref"], i["match"], i.get("via")) for i in view["items"]]


# ── Needs you ──────────────────────────────────────────────────


def test_needs_you_ranks_by_what_each_ask_holds(tree: Tree) -> None:
    view = _needs(tree)

    assert [i["decision"]["id"] for i in view["items"]] == ["D-45", "merge:main", "D-49"]
    # C-14 and C-15 changed projects that finished since: they ask nothing any more.
    assert view["blocks_nothing"] == []
    d45, merges, d49 = view["items"]
    # D-45 holds T-16 and, through assistant-core, the panel that waits on it.
    assert d45["holds"] == ["T-16", "Assistant panel UX"]
    assert d45["weight"] > merges["weight"] > d49["weight"] == 0
    assert [c["ref"] for c in d45["breadcrumb"]] == ["product", ASK, "I-1", "assistant-core"]
    assert d45["decision"]["row_id"] and d45["decision"]["actions"][0]["label"] == "Apply"


def test_each_ask_comes_with_the_row_your_move_shows_for_it(tree: Tree) -> None:
    view = _needs(tree)
    home = tree.page("product") or {}
    shown = {i["id"]: i for i in home["your_move"]["items"]}

    rows = [i["move"] for i in view["items"] + view["blocks_nothing"]]
    # Short names and one action, as the home page's Your move words them.
    assert [r["id"] for r in rows] == ["D-45", "merge:main", "D-49"]
    assert rows[0] == {**shown["D-45"], "decision": rows[0]["decision"]}
    # In plain words, naming where each ask lives (as Home's inbox does).
    assert rows[0]["sentence"].startswith("Atlas wants Assistant core to")
    assert rows[0]["where"] == {"ref": "assistant-core", "title": "Assistant core"}
    assert (rows[0]["object_name"], rows[0]["unblocks"]) == (
        "Update assistant core",
        "Agent assistant (sample)",
    )
    assert rows[1]["unblocks"] == "Build tier drafts 3"
    factory = _needs(tree, "factory")
    assert factory["blocks_nothing"][0]["move"]["blocks_nothing"] is True
    assert [i["move"]["verb"] for i in factory["items"]] == ["Apply rule", "Apply rule"]


def test_merges_on_one_human_merged_base_are_one_item(tree: Tree) -> None:
    merges = _needs(tree)["items"][1]

    assert merges["decision"]["grouped"] is True
    assert merges["decision"]["verb"] == "Merge #9856 and #9857"
    # The PRs no task owns are what the merge holds, by number.
    assert merges["holds"] == ["#9856", "#9857"]
    assert merges["decision"]["age"] == "1h 40m"  # ready since 12:00
    assert merges["decision"]["external_url"].endswith("/pull/9856")
    # The project sits under two folded implicit parents; the breadcrumb flags them.
    assert [(c["ref"], c.get("folded")) for c in merges["breadcrumb"]] == [
        ("product", None),
        ("need:from-C-12", True),
        ("feature:from-C-12", True),
        ("build-tier-drafts-3", None),
    ]


def test_the_closed_alarms_come_with_their_causes_and_the_other_tree_is_a_count(
    tree: Tree,
) -> None:
    product = _needs(tree)
    factory = _needs(tree, "factory")

    assert [(a["id"], a.get("real_ask")) for a in product["closed_alarms"]] == [
        ("D-47", None),
        ("D-46", None),
        ("D-48", "D-45"),
    ]
    assert product["closed_alarms"][2]["cause"].startswith("T-16 is held on purpose")
    # The rules (C-17, C-18) and Sentinel's ask (D-37) wait in the factory tree.
    assert product["other_tree"] == 3
    assert [i["decision"]["id"] for i in factory["items"]] == ["D-50", "D-51"]
    assert [i["decision"]["id"] for i in factory["blocks_nothing"]] == ["D-37"]
    assert factory["other_tree"] == 3 and "closed_alarms" not in factory


def test_after_d45_is_answered_needs_you_holds_two(tree: Tree) -> None:
    facts = factory_1340()
    facts.decisions = [d for d in facts.decisions if d.ref != "D-45"]
    facts.proposals = [p for p in facts.proposals if p.ref != "C-16"]
    facts.tasks = [
        dataclasses.replace(t, state="running", hold=None) if t.ref == "T-16" else t
        for t in facts.tasks
    ]
    after = assert_levels(Tree(facts))

    view = _needs(after)

    assert [i["decision"]["id"] for i in view["items"]] == ["merge:main", "D-49"]
    assert len(view["items"]) + len(view["blocks_nothing"]) == 2


def test_a_viewer_who_cannot_read_a_project_is_not_asked_about_it() -> None:
    tree = assert_levels(Tree(factory_1340(), hidden={"build-tier-drafts-3"}))

    assert [i["decision"]["id"] for i in _needs(tree)["items"]] == ["D-45", "D-49"]


# ── Find ───────────────────────────────────────────────────────


def test_find_reads_task_pr_and_proposal_refs(tree: Tree) -> None:
    assert _find(tree, "T-16")[0] == ("T-16", "ref", None)
    assert _find(tree, "t-16")[0] == ("T-16", "ref", None)
    assert _find(tree, "#9876") == [("PR-9876", "ref", None)]
    assert _find(tree, "PR-9876") == [("PR-9876", "ref", None)]
    # A proposal finds the node its decision is about.
    assert _find(tree, "C-16") == [("assistant-core", "ask", "D-45")]
    assert _find(tree, "D-45") == [("assistant-core", "ask", "D-45")]
    assert _find(tree, "D-46") == [("D-46", "ref", None)]
    assert _find(tree, "C-11")[0] == ("I-1", "proposal", "C-11")


def test_find_reads_titles_with_breadcrumbs(tree: Tree) -> None:
    view = validate(FindView, node_sheets.find(tree, "panel"))
    item = view["items"][0]

    assert (item["ref"], item["match"], item["level_name"]) == (
        "assistant-panel-ux",
        "title",
        "Project",
    )
    assert [c["ref"] for c in item["breadcrumb"]] == ["product", ASK, "I-1"]
    # A title that starts with the words comes before one that only holds them.
    assert _find(tree, "skill")[0][0] == "skill-discovery"
    assert _find(tree, "no such words (sample)") == []


def test_an_empty_find_lists_the_work_that_holds_an_ask(tree: Tree) -> None:
    assert _find(tree, "") == [
        ("I-1", "needs", None),
        ("assistant-core", "needs", None),
        ("live-search-fix", "needs", None),
    ]
    assert len(_find(tree, "a", limit=2)) == 2


def test_done_work_is_still_found(tree: Tree) -> None:
    facts = factory_1340()
    facts.now += 40 * 86400
    later = assert_levels(Tree(facts))

    assert _find(later, "connectors-core")[0] == ("connectors-core", "ref", None)
    assert _find(later, "#9882") == [("PR-9882", "ref", None)]


def test_find_at_scale_caps_its_answer() -> None:
    tree = assert_levels(Tree(scale_factory()))

    found = _find(tree, "task", limit=500)

    assert len(found) == node_sheets.FIND_MAX
    assert _find(tree, "T-360") == [("T-360", "ref", None)]
