"""Level views and lenses on the sample factory at 13:40 (S8).

One level under a node is grouped under its parents (all-done groups folded,
filters by state and words); the lineage reads top to bottom with each
ancestor's one line; the timeline draws one lane per child with work as bars
and waits as gaps, amber only where a person is the cause, and never joins
two lanes; money lists the children's cost against their budgets. Every name
is sample data.
"""

from __future__ import annotations

import dataclasses
import itertools
from typing import Any

import pytest

from omnigent.server.factory import node_lenses
from omnigent.server.factory.node_lenses import line_words
from omnigent.server.factory.node_views import (
    LineageView,
    MoneyView,
    TimelineView,
    validate,
    validate_level,
)
from omnigent.server.factory.nodes import Tree, assert_levels
from tests.server.factory.node_fixtures import (
    ASK_MESSAGE,
    NOW,
    factory_1340,
    hex_id,
    scale_factory,
    snapshot_waits,
    spans_1340,
)

ASK = f"ask:{ASK_MESSAGE}"


@pytest.fixture()
def tree() -> Tree:
    return assert_levels(Tree(factory_1340()))


def _hm(iso: str) -> str:
    return iso[11:16]


def _lanes(view: dict[str, Any]) -> dict[str, list[tuple[str, str, str, str]]]:
    return {
        lane["ref"]: [(_hm(b["from"]), _hm(b["to"]), b["kind"], b["label"]) for b in lane["bars"]]
        for lane in view["lanes"]
    }


def _timeline(tree: Tree, ref: str, **kwargs: Any) -> dict[str, Any]:
    view = node_lenses.timeline(tree, ref, spans_1340(), **kwargs)
    assert view is not None
    return validate(TimelineView, view)


# ── Level views ────────────────────────────────────────────────


def test_tasks_under_the_product_are_grouped_by_project(tree: Tree) -> None:
    view = validate_level(tree.level_view("product", "task") or {})

    groups = {g["parent"]["ref"]: [i["ref"] for i in g["items"]] for g in view["groups"]}
    # Each project is a section title; the task that holds work leads its group.
    assert groups["assistant-core"] == ["T-16", "T-15"]
    assert groups["connectors-core"] == ["T-17"]
    assert view["groups"][0]["parent"]["ref"] == "assistant-core"
    assert view["groups"][0]["parent"]["ancestors"] == [
        "I want an assistant in the app that helps with building, and later with "
        "discovery and sales too.",
        "Agent assistant (sample)",
    ]
    assert view["parent_level_name"] == "projects" and view["tiles"] is False
    # Groups whose work is all done fold, and are counted in one line.
    folded = [g["parent"]["ref"] for g in view["groups"] if g.get("folded_done")]
    assert "assistant-core" not in folded and "connectors-core" in folded
    assert view["done_groups"]["count"] == len(folded) == 6
    assert view["done_groups"]["noun"] == "projects all done"
    assert view["done_groups"]["in_base_env"] is True


def test_a_level_view_filters_by_state_and_words(tree: Tree) -> None:
    waiting = validate_level(tree.level_view("product", "task", state="waiting") or {})
    words = validate_level(tree.level_view("product", "task", q="  DEBIT ") or {})
    nothing = validate_level(tree.level_view("product", "task", q="no such words") or {})

    assert {g["parent"]["ref"]: [i["ref"] for i in g["items"]] for g in waiting["groups"]} == {
        "assistant-core": ["T-16"],
        "build-tier-drafts-3": ["PR-9856", "PR-9857"],
    }
    assert waiting["total"] == 3 and "done_groups" not in waiting
    assert [(g["parent"]["ref"], [i["ref"] for i in g["items"]]) for g in words["groups"]] == [
        ("assistant-core", ["T-16"])
    ]
    assert nothing["groups"] == [] and nothing["total"] == 0


# ── Lineage ────────────────────────────────────────────────────


def test_the_lineage_of_t16_reads_product_need_feature_project(tree: Tree) -> None:
    view = validate(LineageView, node_lenses.lineage(tree, "T-16") or {})

    assert [(a["ref"], a["level"]) for a in view["ancestors"]] == [
        ("product", "product"),
        (ASK, "need"),
        ("I-1", "feature"),
        ("assistant-core", "project"),
    ]
    product, need, feature, project = view["ancestors"]
    assert product["why_html"] == ("Builds <b>sample-org/sample-app</b> into <b>sample-main</b>.")
    assert need["evidence"] == "an ask · no user evidence yet"
    assert feature["links"] == [
        {"kind": "approved by", "ref": "C-11", "title": "Agent assistant, wave 1"}
    ]
    assert feature["progress"]["label"] == "2/6" and feature["progress"]["what"] == "acceptance"
    # Each row carries its one line; the task's own is the ask that holds it.
    assert line_words(project["line"]) == "Needs you: Apply C-16 · via T-16"
    assert view["here"]["ref"] == "T-16" and view["here"]["tag"] == "T-16"
    assert line_words(view["here"]["line"]) == "Held for C-16: needs you: Apply C-16"
    assert view["results"]["merged"] == 0


def test_the_lineage_of_a_feature_counts_what_is_below_and_where_it_landed(tree: Tree) -> None:
    view = validate(LineageView, node_lenses.lineage(tree, "I-1") or {})
    folded = validate(LineageView, node_lenses.lineage(tree, "need:from-C-12") or {})

    assert [(b["level"], b["count"]) for b in view["below"]] == [
        ("project", 4),
        ("task", 4),
        ("change", 3),
        ("run", 1),
    ]
    assert view["results"]["merged"] == 3 and view["results"]["in_base_env"] == 3
    # A folded implicit parent answers as its only child, its ancestors flagged.
    assert folded["here"]["ref"] == "build-tier-drafts-3"
    assert [(a["ref"], a.get("folded")) for a in folded["ancestors"][1:]] == [
        ("need:from-C-12", True),
        ("feature:from-C-12", True),
    ]
    assert folded["ancestors"][2]["links"] == [
        {"kind": "from", "ref": "C-12", "title": "Third round of build-tier drafts"}
    ]
    assert node_lenses.lineage(tree, "nope") is None


def test_the_factory_tree_has_a_lineage_but_no_results(tree: Tree) -> None:
    view = validate(LineageView, node_lenses.lineage(tree, "C-17") or {})

    assert [a["ref"] for a in view["ancestors"]] == ["fx", "fx:rules"]
    assert "results" not in view


# ── Timeline ───────────────────────────────────────────────────


def test_the_timeline_of_agent_assistant_shows_the_panel_waiting_all_day(tree: Tree) -> None:
    view = _timeline(tree, "I-1", last_look=NOW - 70 * 60)
    lanes = _lanes(view)

    assert view["axis"] == ["2026-11-03T06:00:00Z", "2026-11-03T13:40:00Z"]
    assert view["last_look"] == "2026-11-03T12:30:00Z" and view["now"] == view["axis"][1]
    # The panel was queued all day, waiting on another node: grey, never amber.
    assert lanes["assistant-panel-ux"] == [
        ("06:01", "13:40", "wait", "Waits on assistant-core · held for C-16")
    ]
    # assistant-core worked until T-16 was held for C-16; since then it waits on a person.
    assert lanes["assistant-core"] == [
        ("06:01", "06:12", "work", "planning"),
        ("06:12", "06:55", "work", "T-15"),
        ("06:55", "07:46", "work", "T-16"),
        ("07:46", "13:40", "you", "Needs you: Apply C-16 · via T-16"),
    ]
    # Finished projects: queued, then worked (now done); merged closes the lane.
    assert lanes["connectors-core"] == [
        ("06:01", "06:55", "wait", "queued"),
        ("06:55", "13:25", "done", "building"),
    ]
    # The proposed project waits on its ask; held later waves draw nothing.
    assert lanes["live-search-fix"] == [("13:05", "13:40", "you", "proposed · D-49")]
    assert list(lanes) == [
        "assistant-core",
        "connectors-core",
        "skill-discovery",
        "assistant-panel-ux",
        "live-search-fix",
    ]


def test_an_alarm_the_server_closed_is_never_a_persons_wait(tree: Tree) -> None:
    lanes = _lanes(_timeline(tree, "assistant-core"))

    # D-48 (a stall alarm on T-16, closed by the server) leaves T-16's bars untouched.
    assert lanes["T-16"] == [
        ("06:55", "07:46", "work", "T-16"),
        ("07:46", "13:40", "you", "Held for C-16: needs you: Apply C-16"),
    ]
    assert lanes["T-15"] == [("06:12", "06:55", "done", "T-15")]


def test_a_merge_into_an_auto_merge_target_waits_on_the_server() -> None:
    facts = factory_1340()
    facts.targets = frozenset({("sample-org/sample-app", "main")})
    facts.prs = [
        dataclasses.replace(p, head_ref=f"factory/{p.project_slug}/part-{p.number}")
        if p.state == "open"
        else p
        for p in facts.prs
    ]
    tree = assert_levels(Tree(snapshot_waits(facts)))

    lanes = _lanes(_timeline(tree, "build-tier-drafts-3"))

    assert lanes["PR-9856"] == [
        ("12:00", "13:40", "wait", "Waiting: waits for the server to merge it")
    ]


def test_a_window_clips_the_bars_and_a_lane_with_nothing_in_it_is_left_out(tree: Tree) -> None:
    start, end = NOW - 7 * 3600, NOW - 6 * 3600  # 06:40 to 07:40
    view = _timeline(tree, "I-1", start=start, end=end)
    lanes = _lanes(view)

    assert view["axis"] == ["2026-11-03T06:40:00Z", "2026-11-03T07:40:00Z"]
    assert lanes["assistant-core"] == [
        ("06:40", "06:55", "work", "T-15"),
        ("06:55", "07:40", "work", "T-16"),
    ]
    assert "live-search-fix" not in lanes  # proposed at 13:05
    assert _timeline(tree, "I-1", start=NOW - 7 * 86400, end=NOW - 6 * 86400)["lanes"] == []


def test_no_lane_reaches_into_another(tree: Tree) -> None:
    """The levels rule as data: lanes are rows of bars; nothing links two of them."""
    view = _timeline(tree, "product")

    for lane in view["lanes"]:
        bars = lane["bars"]
        assert set(lane) == {"ref", "title", "state", "bars"}
        assert all(set(b) == {"from", "to", "kind", "label"} for b in bars)
        assert all(a["to"] <= b["from"] for a, b in itertools.pairwise(bars))


# ── Money ──────────────────────────────────────────────────────


def test_money_lists_cost_against_budget_most_first(tree: Tree) -> None:
    feature = validate(MoneyView, node_lenses.money(tree, "I-1") or {})
    product = validate(MoneyView, node_lenses.money(tree, "product") or {})

    assert [(r["ref"], r["cost"], r.get("budget")) for r in feature["rows"]] == [
        ("assistant-core", 25.13, 150.0),
        ("connectors-core", 14.2, None),
        ("skill-discovery", 11.8, None),
        ("assistant-panel-ux", 0.0, 150.0),
    ]
    assert feature["rows"][0]["burn"] == 2.5
    assert feature["total"] == 51.13 and "rest" not in feature and "budget" not in feature
    # The product spent more than its needs hold: leads and agents work for no one need.
    assert product["total"] == 117.13 and product["rest"] == 326.87
    assert node_lenses.money(tree, "nope") is None


def test_lenses_at_scale_are_fast() -> None:
    import time

    tree = assert_levels(Tree(scale_factory()))
    spans = [
        dataclasses.replace(s, id=hex_id(90_000 + i), project_slug=f"proj-{i % 45:02d}")
        for i, s in enumerate(spans_1340() * 20)
    ]

    started = time.perf_counter()
    for ref in ("product", "I-1", "proj-00"):
        validate(TimelineView, node_lenses.timeline(tree, ref, spans) or {})
        validate(LineageView, node_lenses.lineage(tree, ref) or {})
        validate(MoneyView, node_lenses.money(tree, ref) or {})
    took = time.perf_counter() - started

    assert took < 0.9, took
