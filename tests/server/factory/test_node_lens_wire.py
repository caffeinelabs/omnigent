"""The lenses, sheets and last looks as the web will read them (S8, S9, S10).

The JSON the web's lenses (W7), sheets (W8), since lines (W10) and the
factory tree's levels block (W9) render against is written from here to
``nodeLenses.server.json`` beside the node pages' fixture
(``OMNIGENT_UPDATE_NODE_PAGES=1`` rewrites it; prettier in ``web/`` then
formats it), so the two sides change together. The check
compares the JSON, not its layout. Every name is sample data.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from omnigent.server.factory import last_look, levels, node_lenses, node_sheets
from omnigent.server.factory.node_views import (
    FindView,
    LineageView,
    MoneyView,
    NeedsYouView,
    TimelineView,
    validate,
    validate_page,
)
from omnigent.server.factory.nodes import Tree, assert_levels
from tests.server.factory.node_fixtures import NOW, factory_1340, spans_1340, structure
from tests.server.factory.test_last_look import LOOKED, events_since_1230

WEB_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "web/src/components/factory/__fixtures__/nodeLenses.server.json"
)
UPDATE_ENV = "OMNIGENT_UPDATE_NODE_PAGES"


def _build() -> dict[str, Any]:
    tree = assert_levels(Tree(factory_1340()))
    page = tree.page("I-1")
    assert page is not None
    last_look.annotate(tree, page, events_since_1230(), LOOKED)
    validate_page(page)
    kids = page["children"]
    return {
        "lineage/T-16": validate(LineageView, node_lenses.lineage(tree, "T-16") or {}),
        "lineage/I-1": validate(LineageView, node_lenses.lineage(tree, "I-1") or {}),
        "lineage/fx:health": validate(LineageView, node_lenses.lineage(tree, "fx:health") or {}),
        "timeline/assistant-core": validate(
            TimelineView,
            node_lenses.timeline(tree, "assistant-core", spans_1340(), last_look=NOW - 70 * 60)
            or {},
        ),
        "money/product": validate(MoneyView, node_lenses.money(tree, "product") or {}),
        "timeline/I-1": validate(
            TimelineView,
            node_lenses.timeline(tree, "I-1", spans_1340(), last_look=NOW - 70 * 60) or {},
        ),
        "money/I-1": validate(MoneyView, node_lenses.money(tree, "I-1") or {}),
        "needs-you/product": validate(NeedsYouView, node_sheets.needs_you(tree, "product")),
        "needs-you/factory": validate(NeedsYouView, node_sheets.needs_you(tree, "factory")),
        "find/C-16": validate(FindView, node_sheets.find(tree, "C-16")),
        "find/panel": validate(FindView, node_sheets.find(tree, "panel")),
        # The sample has no goal yet: that level is hidden (the Definition's levels block).
        "levels": levels.view(structure(), {"goal": 0}, 14),
        "since/I-1": {
            "since": page["rollup"]["since"],
            "changed": {
                k["ref"]: k["changed"]
                for k in [*kids["items"], *kids["later"], *kids["ghosts"]]
                if k.get("changed")
            },
        },
    }


def test_the_web_fixture_is_what_the_server_returns() -> None:
    text = json.dumps(_build(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if os.environ.get(UPDATE_ENV):
        WEB_FIXTURE.write_text(text, encoding="utf-8")
    assert WEB_FIXTURE.exists(), f"run with {UPDATE_ENV}=1 to write {WEB_FIXTURE.name}"
    assert json.loads(WEB_FIXTURE.read_text(encoding="utf-8")) == json.loads(text), (
        f"the lenses or sheets changed: rerun with {UPDATE_ENV}=1 and check the web"
    )
