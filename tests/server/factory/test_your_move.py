"""
Your move (``NodePage.your_move``): what the viewer must do at a node, first.

At most three rows, each an ask in plain words (who wants what, answers named
by their outcome) and what it unblocks when that is true now, ranked like
Needs you (by the work each holds; asks that block nothing last), the rest
counted in ``more`` and carried in ``rest``; only the asks this viewer
answers; and the page carries each ask once (every other copy is marked
``in_your_move``).

Your move belongs to a level: a page lists the asks of its own node and its
direct children, one line per child for the asks deeper down, and Home is the
inbox, each row naming where it lives. The live case is the person's
screenshot, where the product, the feature and the project pages showed the
same rows. Every name is sample data.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.entities.factory import FactoryDecision, FactoryNodeName
from omnigent.server.auth import LEVEL_EDIT, LEVEL_OWNER, LEVEL_READ
from omnigent.server.factory import initiatives, node_names, nodes, snapshot
from omnigent.server.factory.node_views import validate_page
from omnigent.server.factory.nodes import Tree, assert_levels
from omnigent.server.factory.your_move import Viewer, split_verb
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.node_fixtures import (
    ASK_MESSAGE,
    AT_1303,
    DRAFTS_MESSAGE,
    FID,
    factory_1340,
    factory_live,
    hex_id,
    snapshot_waits,
)
from tests.server.factory.projects_helpers import header_cockpit

OWNER = Viewer("ana", LEVEL_OWNER)
DRAFTS = "build-tier-drafts-3"
PAGES = ("product", "I-1", "assistant-core", "assistant-panel-ux", "T-16")
SLUG = re.compile(r"\b[a-z0-9]+(?:-[a-z0-9]+)+\b")
"""A kebab-case slug ("assistant-panel-ux") in a row's words."""
CODE = re.compile(r"\b(?:T|I|PR|C|D)-\d+\b|#\d+")
"""A bare code ("T-16", "PR-9856") in a row's words."""
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


def _ids(page: dict[str, Any]) -> list[str]:
    moves = page["your_move"]
    return [i["id"] for i in [*moves["items"], *moves.get("rest", [])]]


def _labels(item: dict[str, Any]) -> list[str]:
    """The answers a row shows (the rest wait in its details)."""
    return [a["label"] for a in item["answers"] if not a.get("more")]


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
        ("Apply", "C-16", "Agent assistant", "Yes, allow it"),
        ("Merge", "#9856 and #9857", "Build-tier drafts", "Review and merge"),
        ("Answer", "D-49", None, "Follow-up project now"),
    ]
    apply, merge, question = page["your_move"]["items"]
    assert (apply["id"], apply["unblocks_refs"]) == ("D-45", ["feature:from-C-11"])
    assert apply["action"] == {"label": "Yes, allow it", "option": "apply"}
    assert apply["decision"]["row_id"] and apply["weight"] > merge["weight"] > 0
    assert merge["id"] == "merge:main" and merge["action"]["url"].endswith("/pull/9856")
    assert question["action"] == {"label": "Follow-up project now", "option": "follow-up"}
    assert question["about"] == "Start a follow-up project"
    # What applying C-16 changes, in a few words; a merge and a question need none.
    assert apply["object_name"] == "Update assistant core"
    assert "object_name" not in merge and "object_name" not in question
    # C-14 and C-15 changed projects that finished since: they ask nothing any more.
    assert page["your_move"]["more"] == 0 and "rest" not in page["your_move"]
    assert "D-43" not in _ids(page) and "D-44" not in _ids(page)


def test_the_live_product_page_speaks_plainly() -> None:
    """Who wants what and what a yes does; buttons name the outcome; no ids, no Apply."""
    page = _page(_live(), "product")
    apply, merge, question = page["your_move"]["items"]

    assert apply["sentence"] == (
        "Atlas wants Assistant core to also ensure each turn debits the user's credits"
    )
    assert _labels(apply) == ["Yes, allow it", "No"]
    assert [a["label"] for a in apply["answers"] if a.get("more")] == ["Change it first"]
    assert merge["sentence"] == "Nova wants Build-tier drafts merged into main (2 changes)"
    assert merge["answers"] == [
        {
            "option": "merge",
            "label": "Review and merge",
            "primary": True,
            "url": merge["action"]["url"],
        }
    ]
    assert question["sentence"] == "An agent asks: start a follow-up project to fix it?"
    # The recommended answer first; a code in an answer's own words is dropped.
    assert _labels(question) == ["Follow-up project now", "Park it", "Revert"]
    slugs = {p.slug for p in factory_live().projects}
    for item in page["your_move"]["items"]:
        words = " ".join([item["sentence"], *(a["label"] for a in item["answers"])])
        assert not CODE.search(words), words
        assert not any(slug in words for slug in slugs), words
        assert "Apply" not in words
        assert sum(1 for a in item["answers"] if a.get("primary")) == 1
    # Home names where each ask lives (the drafts' project, the ask's own node).
    assert (apply["where"]["ref"], merge["where"]["ref"]) == ("assistant-core", DRAFTS)
    assert "where" not in question


def test_the_live_pages_no_longer_show_the_same_rows() -> None:
    """The screenshot: the product, feature and project pages listed the same asks."""
    tree = _live()

    home = _ids(_page(tree, "product"))
    feature = _ids(_page(tree, "feature:from-C-11"))
    project = _ids(_page(tree, "assistant-core"))
    task = _page(tree, "T-16")

    assert home == ["D-45", "merge:main", "D-49"]
    # The feature: its children's asks only (C-16 changes its child assistant-core).
    assert feature == ["D-45"]
    assert project == ["D-45"]
    # The held task lists nothing of its own: it says where the ask that holds it lives.
    assert task["your_move"]["items"] == []
    assert task["your_move"]["held_by"] == {"ref": "assistant-core", "title": "Assistant core"}
    assert len({tuple(home), tuple(feature)}) == 2


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


def test_a_page_asks_what_it_and_its_children_hold() -> None:
    tree = assert_levels(Tree(factory_1340()))

    core = _page(tree, "assistant-core")
    panel = _page(tree, "assistant-panel-ux")
    task = _page(tree, "T-16")
    done = _page(tree, "ux-status-page")

    # Named as the rows below name them: the task by its title, the project by its words.
    assert _rows(core) == [
        ("Apply", "C-16", "Debit each turn and Assistant panel UX", "Yes, allow it")
    ]
    # C-16 sits on assistant-core: the panel and the held task only point there.
    assert panel["your_move"]["items"] == [] and task["your_move"]["items"] == []
    assert panel["your_move"]["held_by"]["ref"] == "assistant-core"
    assert task["your_move"]["held_by"]["ref"] == "assistant-core"
    assert done["your_move"] == {"items": [], "more": 0}


def test_a_page_names_itself_this_project_and_never_by_a_slug_or_a_code() -> None:
    tree = assert_levels(Tree(factory_1340()))

    drafts = _page(tree, DRAFTS)["your_move"]["items"][0]
    feature = next(i for i in _page(tree, "I-1")["your_move"]["items"] if i["id"] == "D-45")

    assert drafts["unblocks"] == "this project"
    # The sentence names Assistant core already: what else moves is the news.
    assert feature["unblocks"] == "Assistant panel UX"
    for ref in ("product", "I-1", "assistant-core", "assistant-panel-ux", "T-16", DRAFTS):
        for item in _page(tree, ref)["your_move"]["items"]:
            words = item.get("unblocks") or ""
            assert not SLUG.search(words) and not CODE.search(words), (ref, words)


def _ask(number: int, subject: str, **extra: Any) -> FactoryDecision:
    return FactoryDecision(
        id=hex_id(500 + number),
        factory_id=FID,
        number=number,
        question=extra.pop("question", f"Question {number} (sample)?"),
        source="agent",
        blocking=extra.pop("blocking", True),
        subject=subject,
        options=[{"id": "yes", "label": "Go ahead"}, {"id": "no", "label": "Not now"}],
        assignee="owners",
        created_at=AT_1303,
        updated_at=AT_1303,
        **extra,
    )


def _with(*asks: FactoryDecision) -> Tree:
    facts = factory_1340()
    facts.decisions = [*facts.decisions, *asks]
    return assert_levels(Tree(snapshot_waits(facts)))


def test_a_feature_owned_ask_shows_on_home_and_the_feature_only() -> None:
    tree = _with(_ask(60, "I-1", question="Ship the assistant to a first team now?"))

    shown = {ref: "D-60" in _ids(_page(tree, ref)) for ref in PAGES}

    assert shown == {
        "product": True,
        "I-1": True,
        "assistant-core": False,
        "assistant-panel-ux": False,
        "T-16": False,
    }
    feature = next(i for i in _page(tree, "I-1")["your_move"]["items"] if i["id"] == "D-60")
    # Its own page needs no "where"; Home names it.
    assert "where" not in feature
    home = next(i for i in _page(tree, "product")["your_move"]["items"] if i["id"] == "D-60")
    assert home["where"] == {"ref": "I-1", "title": "Agent assistant (sample)"}


def test_an_ask_deeper_down_is_one_line_per_child() -> None:
    tree = _with(
        _ask(61, "T-16", question="Debit before or after the reply?"),
        _ask(62, "T-16", question="Round the debit up?"),
    )

    feature = _page(tree, "I-1")["your_move"]
    project = _page(tree, "assistant-core")["your_move"]
    task = _page(tree, "T-16")["your_move"]

    # The feature: not the task's asks, one line for the project they live under.
    assert not {"D-61", "D-62"} & set(_ids({"your_move": feature}))
    assert feature["below"] == [{"ref": "assistant-core", "title": "Assistant core", "count": 2}]
    # The project lists its task's asks; the task its own.
    assert {"D-61", "D-62"} <= set(_ids({"your_move": project}))
    assert {"D-61", "D-62"} <= set(_ids({"your_move": task}))
    assert "below" not in project and "below" not in task


def test_the_merges_a_project_waits_on_unblock_that_project() -> None:
    tree = assert_levels(Tree(factory_1340()))

    drafts = _page(tree, DRAFTS)["your_move"]["items"]
    change = _page(tree, "PR-9856")["your_move"]["items"]
    home = _page(tree, "product")["your_move"]["items"]

    # What the merge acts on (#9856, #9857) is no news: it holds the project they build.
    assert _rows({"your_move": {"items": drafts}}) == [
        ("Merge", "#9856 and #9857", "this project", "Review and merge")
    ]
    assert drafts[0]["weight"] > 0 and "blocks_nothing" not in drafts[0]
    assert change[0]["unblocks"] == "Build tier drafts 3"
    assert next(i for i in home if i["id"] == "merge:main")["unblocks"] == "Build tier drafts 3"


def test_applying_a_proposed_project_unblocks_nothing_yet() -> None:
    """C-19 adds a project: nothing that exists waits on it, so no row says it unblocks."""
    tree = assert_levels(Tree(factory_1340()))

    def row(ref: str) -> dict[str, Any]:
        return next(i for i in _page(tree, ref)["your_move"]["items"] if i["id"] == "D-49")

    for ref in ("product", "I-1", "live-search-fix"):
        assert "unblocks" not in row(ref), ref
    assert row("I-1")["sentence"] == "Atlas wants a new project: Fix live search"
    # A factory change holds no product work: its object says what it changes.
    rules = _page(tree, "fx")["your_move"]["items"]
    assert [(i["id"], i.get("unblocks")) for i in rules if i["verb"] == "Apply rule"] == [
        ("D-50", None),
        ("D-51", None),
    ]


def test_an_ask_the_factory_itself_made_is_asked_by_its_lead() -> None:
    facts = factory_1340()
    # The root session (the factory's own id) wrote C-19, a member's session C-16.
    member = next(m for m in facts.members if m.get("session_id") and m.get("title"))
    facts.proposals = [
        dataclasses.replace(p, author=FID, author_kind="agent")
        if p.number == 19
        else dataclasses.replace(p, author=str(member["session_id"]), author_kind="agent")
        if p.number == 16
        else p
        for p in facts.proposals
    ]
    tree = assert_levels(Tree(facts))

    items = _page(tree, "I-1")["your_move"]["items"]
    askers = {i["id"]: i["decision"]["asker"] for i in items}

    assert askers["D-49"] == tree.responder(tree.nodes["product"]) == "Atlas"
    assert askers["D-45"] == member["title"]
    # The sentence says who wants it by the same name.
    assert next(i for i in items if i["id"] == "D-45")["sentence"].startswith(member["title"])
    assert not any(re.fullmatch(r"[0-9a-f]{32}", a) for a in askers.values())


def test_the_strip_counts_the_rows_that_say_needs_you() -> None:
    tree = assert_levels(Tree(factory_1340()))
    home = _page(tree, "product")
    level = tree.level_view("product", "feature") or {}

    tiles = [i for g in level["groups"] for i in g["items"]]
    rows = [i["ref"] for i in tiles if i["status_compact"]["kind"] == "needs"]
    features = next(c for c in home["levels_below"] if c["level"] == "feature")
    # The drafts project (shown as a feature) waits on its merges: its row says
    # "Needs you", and so does the strip.
    assert rows == ["I-1", DRAFTS]
    assert features["needs"] == len(rows) == features["by_state"]["needs"]


def test_asks_that_block_nothing_come_last() -> None:
    tree = _with(_ask(63, "I-1", blocking=False, question="Rename the feature?"))

    feature = _page(tree, "I-1")

    assert [i["id"] for i in feature["your_move"]["items"]] == ["D-45", "D-49", "D-63"]
    assert feature["your_move"]["items"][-1]["blocks_nothing"] is True
    assert "unblocks" not in feature["your_move"]["items"][-1]
    assert feature["your_move"]["more"] == 0


def test_more_rows_wait_in_rest_and_count_as_shown() -> None:
    tree = _with(*(_ask(70 + n, "I-1", question=f"Question {n} (sample)?") for n in range(3)))

    page = _page(tree, "I-1")
    moves = page["your_move"]

    assert len(moves["items"]) == 3 and moves["more"] == 2
    assert [i["id"] for i in moves["rest"]] == _ids(page)[3:]
    rest = {i["id"] for i in moves["rest"]}
    marked = [d for d in _decisions(page) if d["id"] in rest]
    assert all(d.get("in_your_move") is True for d in marked)


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

    assert [i["id"] for i in mine["your_move"]["items"]] == ["merge:main", "D-49"]
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
    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/n/product", headers={"X-Test-User": "alice"}
    )
    [row] = resp.json()["your_move"]["items"]
    assert row["sentence"] == "An agent asks: ship the sample now?"
    assert [a["label"] for a in row["answers"]] == ["Ship it", "Wait"]
    # Asked of the owners: an editor has nothing to do here.
    assert editor == []
