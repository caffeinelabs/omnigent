"""
Your move (``NodePage.your_move``): what the viewer must do at a node, first.

At most three rows, each a verb, a short object, one primary action and what
it unblocks, ranked like Needs you (by the work each holds; asks that block
nothing last), the rest counted in ``more``; only the asks this viewer
answers; and the page carries each ask once (every other copy is marked
``in_your_move``). The live case is the product page from the person's
screenshot. Every name is sample data.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.entities.factory import FactoryNodeName
from omnigent.server.auth import LEVEL_EDIT, LEVEL_OWNER, LEVEL_READ
from omnigent.server.factory import initiatives, node_names, nodes, snapshot
from omnigent.server.factory.node_views import validate_page
from omnigent.server.factory.nodes import Tree, assert_levels
from omnigent.server.factory.your_move import Viewer, split_verb
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.node_fixtures import (
    ASK_MESSAGE,
    DRAFTS_MESSAGE,
    FID,
    factory_1340,
    factory_live,
)
from tests.server.factory.projects_helpers import header_cockpit

OWNER = Viewer("ana", LEVEL_OWNER)
LIVE_NAMES = {
    f"ask:{ASK_MESSAGE}": "Chat agent for every team",
    f"ask:{DRAFTS_MESSAGE}": "Third drafts round",
    "feature:from-C-11": "Agent assistant",
    "build-tier-drafts-3": "Build-tier drafts",
    "assistant-core": "Assistant core",
}


def _page(tree: Tree, ref: str, viewer: Viewer | None = OWNER) -> dict[str, Any]:
    page = tree.page(ref, viewer=viewer)
    assert page is not None, ref
    return validate_page(page)


def _rows(page: dict[str, Any]) -> list[tuple[str, str, str | None, str]]:
    return [
        (i["verb"], i["object"], i.get("unblocks"), i["action"]["label"])
        for i in page["your_move"]["items"]
    ]


def _decisions(value: Any, out: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Every ``DecisionRef`` of a page outside its Your move."""
    found = [] if out is None else out
    if isinstance(value, dict):
        if {"id", "verb", "actions", "asker"} <= value.keys():
            found.append(value)
        for key, child in value.items():
            if key != "your_move":
                _decisions(child, found)
    elif isinstance(value, list):
        for child in value:
            _decisions(child, found)
    return found


def _live() -> Tree:
    facts = factory_live()
    base = Tree(factory_live())
    facts.names = {
        ref: FactoryNodeName(
            FID,
            ref,
            name,
            node_names.digest(base.nodes[ref].source or base.nodes[ref].full_title or ""),
        )
        for ref, name in LIVE_NAMES.items()
    }
    return assert_levels(Tree(facts))


# ── The live case ──────────────────────────────────────────────


def test_the_live_product_page_opens_with_three_moves() -> None:
    page = _page(_live(), "product")

    assert _rows(page) == [
        ("Apply", "C-16", "Agent assistant", "Apply"),
        ("Merge", "#9856 and #9857", "Build-tier drafts", "Review and merge"),
        ("Answer", "D-49", None, "Follow-up project now"),
    ]
    apply, merge, question = page["your_move"]["items"]
    assert (apply["id"], apply["unblocks_refs"]) == ("D-45", ["feature:from-C-11"])
    assert apply["action"] == {"label": "Apply", "option": "apply"}
    assert apply["decision"]["row_id"] and apply["weight"] > merge["weight"] > 0
    assert merge["id"] == "merge:main" and merge["action"]["url"].endswith("/pull/9856")
    assert question["action"] == {"label": "Follow-up project now", "option": "follow-up"}
    assert question["about"] == "Start a follow-up project to fix"
    # C-14 and C-15 hold nothing: behind "+2 more".
    assert page["your_move"]["more"] == 2


def test_the_live_product_page_asks_each_thing_once() -> None:
    page = _page(_live(), "product")
    moves = {i["id"] for i in page["your_move"]["items"]}

    repeats = [d for d in _decisions(page) if d["id"] in moves]
    assert repeats, "the status line and the tiles still carry the asks"
    assert all(d.get("in_your_move") is True for d in repeats)
    assert page["status"]["decision"]["in_your_move"] is True
    others = [d for d in _decisions(page) if d["id"] not in moves]
    assert all("in_your_move" not in d for d in others)


def test_marking_repeats_leaves_the_trees_lines_alone() -> None:
    tree = _live()
    _page(tree, "product")
    wave = tree.nodes["feature:from-C-11"]

    # The child's line is memoized and shared by other pages: the mark is on a copy.
    assert "in_your_move" not in tree.status(wave, compact=True).get("decision", {})
    child = _page(tree, "feature:from-C-11")
    assert child["status"]["decision"]["in_your_move"] is True


# ── Ranking, scope and viewer ──────────────────────────────────


def test_a_page_asks_what_its_subtree_holds_and_what_holds_it() -> None:
    tree = assert_levels(Tree(factory_1340()))

    core = _page(tree, "assistant-core")
    panel = _page(tree, "assistant-panel-ux")
    task = _page(tree, "T-16")
    drafts = _page(tree, "build-tier-drafts-3")
    done = _page(tree, "ux-status-page")

    assert _rows(core) == [("Apply", "C-16", "T-16 and assistant-panel-ux", "Apply")]
    # The panel's own path ends at C-16, which sits on assistant-core: still its move.
    assert [i["id"] for i in panel["your_move"]["items"]] == ["D-45"]
    assert task["your_move"]["items"][0]["ref"] == "assistant-core"
    # What the merge acts on is no news: it unblocks nothing else here.
    assert _rows(drafts) == [("Merge", "#9856 and #9857", None, "Review and merge")]
    assert done["your_move"] == {"items": [], "more": 0}


def test_asks_that_block_nothing_come_last() -> None:
    tree = assert_levels(Tree(factory_1340()))

    feature = _page(tree, "I-1")

    assert [i["id"] for i in feature["your_move"]["items"]] == ["D-45", "D-49", "D-43"]
    assert feature["your_move"]["items"][-1]["blocks_nothing"] is True
    assert feature["your_move"]["more"] == 1


def test_only_what_this_viewer_answers() -> None:
    facts = factory_1340()
    facts.decisions = [
        dataclasses.replace(d, assignee="ben") if d.number == 45 else d for d in facts.decisions
    ]
    tree = assert_levels(Tree(facts))

    mine = _page(tree, "product", Viewer("ana", LEVEL_OWNER))
    bens = _page(tree, "product", Viewer("ben", LEVEL_EDIT))
    reader = _page(tree, "product", Viewer("cy", LEVEL_READ))
    agent = _page(tree, "product", None)

    assert [i["id"] for i in mine["your_move"]["items"]] == ["merge:main", "D-49", "D-43"]
    assert bens["your_move"]["items"][0]["id"] == "D-45"
    assert reader["your_move"] == {"items": [], "more": 0}
    assert [i["id"] for i in agent["your_move"]["items"]][:2] == ["D-45", "merge:main"]


@pytest.mark.parametrize(
    ("verb", "split"),
    [
        ("Apply C-16", ("Apply", "C-16")),
        ("Apply rule C-17", ("Apply rule", "C-17")),
        ("Merge #9856 and #9857", ("Merge", "#9856 and #9857")),
        ("Answer D-49", ("Answer", "D-49")),
        ("Restart", ("Restart", "")),
    ],
)
def test_a_verb_splits_into_verb_and_object(verb: str, split: tuple[str, str]) -> None:
    assert split_verb(verb) == split


# ── Over the API ───────────────────────────────────────────────


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    initiatives.register()
    nodes.reset_cache()
    snapshot.reset_cache()
    async for value in header_cockpit(world, db_uri, monkeypatch):
        yield value
    nodes.reset_cache()
    snapshot.reset_cache()


async def test_each_viewer_gets_their_own_move(cockpit: Cockpit) -> None:
    cockpit.store.create_decision(
        cockpit.fid,
        dedupe_key="sample-question",
        question="Ship the sample now?",
        source="agent",
        blocking=True,
        options=[
            {"id": "yes", "label": "Ship it", "recommended": True},
            {"id": "no", "label": "Wait"},
        ],
        assignee="owners",
    )

    async def moves(user: str) -> list[str]:
        nodes.reset_cache()
        resp = await cockpit.client.get(
            f"/v1/factory/factories/{cockpit.fid}/n/product", headers={"X-Test-User": user}
        )
        assert resp.status_code == 200, resp.text
        page = validate_page(resp.json())
        return [
            f"{i['verb']} {i['object']} · {i['action']['label']}"
            for i in page["your_move"]["items"]
        ]

    owner = await moves("alice")
    editor = await moves("carol")

    assert owner == ["Answer D-1 · Ship it"]
    # Asked of the owners: an editor has nothing to do here.
    assert editor == []
