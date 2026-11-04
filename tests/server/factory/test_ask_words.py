"""
Asks in plain words (calm rule 9): "what does apply even mean?"

A row says who wants what and what a yes does, and its buttons name the
outcome ("Yes, allow it", "No"), never "Apply", an id or the agent's jargon.
The factory words each ask in the background with the cheap title model the
short names use, again only when the ask's text changes; until then the rows
say it (a proposal's ops and the project's short name). Every name is sample
data.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import Any

import pytest

from omnigent.entities.factory import FactoryDecision, FactoryNodeName, FactoryProposal
from omnigent.server.auth import LEVEL_OWNER
from omnigent.server.factory import ask_words, node_names
from omnigent.server.factory.node_views import validate_page
from omnigent.server.factory.nodes import NodeFacts, Tree, assert_levels
from omnigent.server.factory.your_move import Viewer
from omnigent.stores.factory_store import InMemoryFactoryStore
from tests.server.factory.node_fixtures import (
    AT_0746,
    FID,
    factory_1340,
    factory_live,
    hex_id,
    snapshot_waits,
)

OWNER = Viewer("ana", LEVEL_OWNER)


def _facts_with(*ops: dict[str, Any], kind: str = "projects", title: str = "") -> NodeFacts:
    """The sample factory with C-16 (D-45) proposing *ops* on assistant-core."""
    facts = factory_1340()
    facts.proposals = [
        dataclasses.replace(p, ops=list(ops), kind=kind, title=title or p.title)
        if p.number == 16
        else p
        for p in facts.proposals
    ]
    return snapshot_waits(facts)


def _row(tree: Tree, ask: str, ref: str = "product") -> dict[str, Any]:
    page = validate_page(tree.page(ref, viewer=OWNER) or {})
    moves = page["your_move"]
    return next(i for i in [*moves["items"], *moves.get("rest", [])] if i["id"] == ask)


def _sentence(*ops: dict[str, Any], kind: str = "projects", title: str = "") -> str:
    tree = assert_levels(Tree(_facts_with(*ops, kind=kind, title=title)))
    return str(_row(tree, "D-45", "assistant-core")["sentence"])


# ── The rows' words ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("op", "sentence"),
    [
        (
            {"op": "update", "slug": "assistant-core", "patch": {"scope_paths": ["a/**", "b/**"]}},
            "Atlas wants Assistant core to also edit 2 more files",
        ),
        (
            {"op": "update", "slug": "assistant-core", "patch": {"goal": "Something else."}},
            "Atlas wants Assistant core to change its goal",
        ),
        (
            {"op": "update", "slug": "assistant-core", "patch": {"budget_ics": 7}},
            "Atlas wants Assistant core to get a different budget",
        ),
        (
            {"op": "update", "slug": "assistant-core", "patch": {"priority": 1}},
            "Atlas wants Assistant core to move down the queue",
        ),
        (
            {
                "op": "update",
                "slug": "assistant-core",
                "patch": {"depends_on": ["skill-discovery"]},
            },
            "Atlas wants Assistant core to wait for Skill discovery",
        ),
        ({"op": "cancel", "slug": "assistant-core"}, "Atlas wants to stop Assistant core"),
        (
            {"op": "split", "slug": "assistant-core", "into": [{"slug": "a"}, {"slug": "b"}]},
            "Atlas wants to split Assistant core into 2 projects",
        ),
        (
            {"op": "reorder", "slug": "assistant-core"},
            "Atlas wants Assistant core to go first",
        ),
    ],
)
def test_a_proposal_says_what_a_yes_changes(op: dict[str, Any], sentence: str) -> None:
    assert _sentence(op) == sentence


def test_a_proposal_names_its_answers_by_their_outcome() -> None:
    tree = assert_levels(Tree(_facts_with({"op": "cancel", "slug": "assistant-core"})))
    row = _row(tree, "D-45", "assistant-core")

    assert row["answers"] == [
        {"option": "apply", "label": "Yes, stop it", "primary": True},
        {"option": "reject", "label": "No"},
        {"option": "edit", "label": "Change it first", "more": True},
    ]
    # The original wording and the ids stay with the ask's details.
    assert row["decision"]["id"] == "D-45" and row["decision"]["subject"] == "C-16"
    assert [a["label"] for a in row["decision"]["actions"]] == ["Apply", "Edit & apply", "Reject"]


def test_a_new_project_and_a_factory_rule_in_plain_words() -> None:
    tree = assert_levels(Tree(factory_1340()))

    follow_up = _row(tree, "D-49")
    rule = _row(tree, "D-50", "fx")

    assert follow_up["sentence"] == "Atlas wants a new project: Fix live search"
    assert follow_up["answers"][0] == {
        "option": "apply",
        "label": "Yes, start it",
        "primary": True,
    }
    assert rule["sentence"] == (
        "Atlas wants to add a factory rule: spike unproven external capabilities first"
    )
    assert rule["answers"][0]["label"] == "Yes, add the rule"


def test_a_question_keeps_its_answers_without_jargon_or_codes() -> None:
    facts = factory_live()
    widen = FactoryDecision(
        id=hex_id(551),
        factory_id=FID,
        number=51,
        question="How should users reach the Assistant? Atlas recommends a flagged tab.",
        source="agent",
        blocking=False,
        options=[
            {"id": "tab", "label": "Widen scope: flagged tab in header", "recommended": True},
            {"id": "menu", "label": "Menu item only (C-12 style)"},
        ],
        assignee="owners",
        created_at=AT_0746,
        updated_at=AT_0746,
    )
    facts.decisions = [d for d in facts.decisions if d.number != 51] + [widen]
    tree = assert_levels(Tree(facts))

    row = _row(tree, "D-51")

    assert row["sentence"] == "An agent asks: how should users reach the Assistant?"
    assert [a["label"] for a in row["answers"]] == ["Flagged tab in header", "Menu item only"]
    assert row["answers"][0]["primary"] is True


def test_a_merge_says_what_goes_where() -> None:
    tree = assert_levels(Tree(factory_1340()))

    row = _row(tree, "merge:main")

    assert row["sentence"] == "Nova wants Build tier drafts 3 merged into main (2 changes)"
    assert row["answers"][0]["label"] == "Review and merge"
    assert row["answers"][0]["url"].endswith("/pull/9856")


# ── The factory's words ────────────────────────────────────────


def _worded(facts: NodeFacts, ask: str, words: str, labels: str | None = None) -> Tree:
    """*facts* with the factory's words for *ask*, made from its current text."""
    tree = Tree(facts)
    want = next(w for w in tree.unnamed if w.ref == ask_words.ref_of(ask))
    names = {want.ref: FactoryNodeName(FID, want.ref, words, want.hash)}
    if labels is not None:
        key = want.ref + node_names.LABELS_SUFFIX
        names[key] = FactoryNodeName(FID, key, labels, want.hash)
    facts.names = {**facts.names, **names}
    return assert_levels(Tree(facts))


def test_the_factorys_words_win_while_the_ask_says_the_same() -> None:
    facts = factory_live()
    tree = _worded(
        facts,
        "D-49",
        "Atlas wants a follow-up project to fix live search",
        "Yes, start it | Not now | Undo the discovery change",
    )

    row = _row(tree, "D-49")

    assert row["sentence"] == "Atlas wants a follow-up project to fix live search"
    assert [a["label"] for a in row["answers"]] == [
        "Yes, start it",
        "Not now",
        "Undo the discovery change",
    ]
    assert row["action"]["label"] == "Yes, start it"
    assert not any(w.ref == "plain:D-49" for w in tree.unnamed)
    # The ask changed: its rows' words show and the factory words it again.
    facts.decisions = [
        dataclasses.replace(d, question="Start the live search fix now?") if d.number == 49 else d
        for d in facts.decisions
    ]
    changed = assert_levels(Tree(facts))
    assert _row(changed, "D-49")["sentence"] == "An agent asks: start the live search fix now?"
    assert any(w.ref == "plain:D-49" for w in changed.unnamed)


def test_words_holding_an_id_never_show() -> None:
    tree = _worded(factory_live(), "D-45", "Apply C-16 to update assistant-core now")

    assert _row(tree, "D-45")["sentence"].startswith("Atlas wants Assistant core to")


@pytest.mark.parametrize(
    ("raw", "labels", "parsed"),
    [
        (
            "Atlas wants Connectors core to also edit the env-schema files.",
            0,
            (
                "Atlas wants Connectors core to also edit the env-schema files",
                None,
            ),
        ),
        (
            '"Juno asks whether to retry on 429" || Retry | Leave it',
            2,
            (
                "Juno asks whether to retry on 429",
                ["Retry", "Leave it"],
            ),
        ),
        ("Juno asks whether to retry || Retry", 2, ("Juno asks whether to retry", None)),
        ("Apply C-15 now please", 0, None),
        ("Too short", 0, None),
        ("Juno asks about T-16 || Yes | No", 2, None),
    ],
)
def test_the_models_words_are_checked(
    raw: str, labels: int, parsed: tuple[str, list[str] | None] | None
) -> None:
    assert node_names.parse_ask(raw, labels) == parsed


async def test_the_factory_words_each_ask_once_in_the_background() -> None:
    node_names.reset()
    calls: list[str] = []

    async def namer(text: str) -> str:
        calls.append(text)
        if "Answers:" in text:
            return "Atlas wants a follow-up to fix live search || Yes, start it | Later | Undo it"
        return "Atlas wants the work to go ahead as planned"

    store = InMemoryFactoryStore()
    state = SimpleNamespace(**{node_names.NAMER_ATTR: namer})
    root: Any = SimpleNamespace(
        id=FID, agent_id=None, harness_override=None, model_override=None, sub_agent_name=None
    )
    deps: Any = SimpleNamespace(factory_store=store)
    wants = [w for w in Tree(factory_live()).unnamed if w.kind == "ask"]

    task = node_names.schedule(state, deps, root, wants)
    assert task is not None
    await task
    stored = {r.node_ref: r for r in store.list_node_names(FID)}

    assert len(calls) == len(wants)
    assert stored["plain:D-49"].name == "Atlas wants a follow-up to fix live search"
    assert stored["plain:D-49#labels"].name == "Yes, start it | Later | Undo it"
    assert stored["plain:D-45"].name == "Atlas wants the work to go ahead as planned"
    assert "plain:D-45#labels" not in stored
    # Read back: the rows show them.
    facts = factory_live()
    facts.names = stored
    tree = assert_levels(Tree(facts))
    assert [a["label"] for a in _row(tree, "D-49")["answers"]] == [
        "Yes, start it",
        "Later",
        "Undo it",
    ]
    assert not [w for w in tree.unnamed if w.kind == "ask"]
    node_names.reset()


def test_a_proposal_without_its_project_still_reads() -> None:
    facts = factory_1340()
    gone = FactoryProposal(
        id=hex_id(320),
        factory_id=FID,
        number=20,
        kind="projects",
        title="Update ghost-project",
        status="pending",
        ops=[{"op": "update", "slug": "ghost-project", "patch": {"dod": ["One", "Two"]}}],
        author="atlas",
        created_at=AT_0746,
    )
    facts.proposals = [*facts.proposals, gone]
    tree = Tree(snapshot_waits(facts))
    decision = FactoryDecision(
        id=hex_id(570),
        factory_id=FID,
        number=70,
        question="Atlas proposes C-20",
        source="proposal",
        subject="proposal:C-20",
        created_at=AT_0746,
    )
    ask = tree.paths.decision_ask(decision)

    assert ask_words.fallback_sentence(tree, ask, decision, gone) == (
        "Atlas wants Ghost project to deliver 2 more things"
    )
