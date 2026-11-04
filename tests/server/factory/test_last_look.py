"""Since your last look on the sample factory at 13:40 (S10).

A person last looked at 12:30. Since then D-49 opened, six PRs merged (two of
them wave 1's, four the shortlist's), T-17 finished and connectors-core's
stage moved; the server also closed an alarm and another ask that blocks
nothing opened. The roll-up names the three most significant changes in the
node's subtree; each child names its own. Every name is sample data.
"""

from __future__ import annotations

from typing import Any

import pytest

from omnigent.entities.factory import FactoryEvent
from omnigent.server.factory import last_look
from omnigent.server.factory.node_views import validate_page
from omnigent.server.factory.nodes import Tree, assert_levels
from tests.server.factory.node_fixtures import (
    AT_1303,
    AT_1325,
    FID,
    NOW,
    REPO,
    SHORTLIST,
    factory_1340,
    hex_id,
)

LOOKED: int = NOW - 70 * 60
"""12:30."""


def _event(n: int, kind: str, at: int, **fields: Any) -> FactoryEvent:
    return FactoryEvent(
        id=hex_id(60_000 + n), factory_id=FID, ts=at * 1000, kind=kind, summary=kind, **fields
    )


def _merged(n: int, number: int, slug: str, at: int) -> FactoryEvent:
    return _event(
        n,
        "pr.merged",
        at,
        project_slug=slug,
        subject=f"pr:{REPO}#{number}",
        data={"pr_id": hex_id(number)},
    )


def events_since_1230() -> list[FactoryEvent]:
    """:returns: What the factory recorded between 12:00 and 13:40."""
    shortlist = zip((9826, 9828, 9829, 9827), SHORTLIST, strict=True)
    return [
        _merged(1, 9882, "connectors-core", AT_1325),
        _merged(2, 9881, "skill-discovery", AT_1303),
        *(_merged(3 + i, n, slug, AT_1325) for i, (n, slug) in enumerate(shortlist)),
        _event(10, "decision.opened", AT_1303 + 120, subject="decision:D-49"),
        _event(11, "decision.opened", LOOKED + 600, subject="decision:D-43"),
        _event(12, "task.done", AT_1325 - 60, subject="task:T-17", project_slug="connectors-core"),
        _event(
            13,
            "stage.changed",
            AT_1325,
            project_slug="connectors-core",
            data={"from": "ready", "to": "merged"},
        ),
        _event(
            14,
            "decision.answered",
            AT_1303 + 60,
            subject="decision:D-46",
            project_slug="skill-discovery",
            data={"by_kind": "server"},
        ),
        _event(15, "task.done", AT_1303, subject="task:T-16", project_slug="assistant-core"),
        _event(16, "pr.merged", LOOKED - 600, data={"pr_id": hex_id(9876)}),
        _event(17, "message.delivered", AT_1325, project_slug="assistant-core"),
    ]


@pytest.fixture()
def tree() -> Tree:
    return assert_levels(Tree(factory_1340()))


def _page(tree: Tree, ref: str, events: list[FactoryEvent] | None = None) -> dict[str, Any]:
    page = tree.page(ref)
    assert page is not None, ref
    last_look.annotate(tree, page, events_since_1230() if events is None else events, LOOKED)
    return validate_page(page)


def _changed(page: dict[str, Any]) -> dict[str, list[str]]:
    kids = page["children"]
    return {k["ref"]: k["changed"] for k in [*kids["items"], *kids["ghosts"]] if k.get("changed")}


def test_the_root_names_the_three_most_significant_changes(tree: Tree) -> None:
    since = _page(tree, "product")["rollup"]["since"]

    # A new ask that holds work first, then the merges (six read as one), then a task done.
    assert since == {
        "at": "2026-11-03T12:30:00Z",
        "items": ["D-49 new", "6 merged", "T-17 done"],
        "more": 1,
    }


def test_since_items_are_the_changes_in_the_subtree(tree: Tree) -> None:
    feature = _page(tree, "I-1")

    assert feature["rollup"]["since"]["items"] == [
        "D-49 new",
        "#9882 merged 13:25",
        "#9881 merged 13:03",
    ]
    assert feature["rollup"]["since"]["more"] == 2
    assert _changed(feature) == {
        "connectors-core": ["#9882 merged 13:25", "T-17 done", "Connectors core: merged"],
        "skill-discovery": ["#9881 merged 13:03"],
        "live-search-fix": ["D-49 new"],
    }
    # Nothing changed under assistant-core: no since line, nothing empty.
    core = _page(tree, "assistant-core")
    assert "since" not in core["rollup"] and _changed(core) == {}


def test_noise_and_what_did_not_happen_say_nothing(tree: Tree) -> None:
    texts = [c.text for c in last_look.changes(tree, events_since_1230())]

    # An alarm the server closed, an ask that blocks nothing, a builder going
    # idle on a task that is not done and a chat line are not changes.
    assert "D-46 answered" not in texts and "D-43 new" not in texts
    assert "T-16 done" not in texts and len(texts) == len(set(texts))


def test_a_viewer_who_cannot_read_a_project_hears_nothing_of_it() -> None:
    tree = assert_levels(Tree(factory_1340(), hidden={"connectors-core", *SHORTLIST}))

    since = _page(tree, "product")["rollup"]["since"]

    assert since["items"] == ["D-49 new", "#9881 merged 13:03"]
    assert since["more"] == 0


def test_the_last_look_is_the_nodes_own_else_its_nearest_ancestors(tree: Tree) -> None:
    core = tree.nodes["assistant-core"]

    assert last_look.mark_refs(tree, core) == [
        "assistant-core",
        "product",
        f"ask:{'a' * 31}1",
        "I-1",
    ]
    assert last_look.last_look(tree, core, {}) is None
    assert last_look.last_look(tree, core, {"product": 1, "I-1": 2}) == 2
    assert last_look.last_look(tree, core, {"assistant-core": 3, "I-1": 2}) == 3


def test_events_before_the_last_look_are_not_changes(tree: Tree) -> None:
    page = _page(tree, "product", [e for e in events_since_1230() if e.ts <= LOOKED * 1000])

    assert "since" not in page["rollup"] and _changed(page) == {}
