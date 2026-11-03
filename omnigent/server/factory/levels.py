"""
The cockpit's levels for one factory (``GET /factories/{fid}/levels``).

Both trees come from the definition's ``structure`` block (names, visibility,
who answers, verbs, authority); the keys and their order are code
(:data:`.definition_blocks.TREES`). What is hidden is computed per factory: a
level shown ``when_any`` is hidden while no node sits on it, one shown
``when_many`` while at most one does. A level whose count is not known yet is
never hidden.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import PROJECT_KIND_WORK
from omnigent.server.factory import definition, node_refs
from omnigent.server.factory.charter import project_kind
from omnigent.server.factory.definition_blocks import PRODUCT_LEVELS, TREES

if TYPE_CHECKING:
    from omnigent.server.factory.context import FactoryDeps

_IMPLIED: Final = PRODUCT_LEVELS[2:]
"""Levels where a node with no stored parent gets an implicit one (need down to run)."""


def presence(deps: FactoryDeps, factory_id: str, doc: Mapping[str, Any]) -> dict[str, int | None]:
    """
    How many nodes sit on each level of the factory now (synchronous; store reads).

    Stored rows are counted, then every level from need down gets at least one
    node when the level below has any (its implicit parent). Goals add a "No
    goal yet" node when a need has none. ``None``: not counted (never hidden).
    """
    store = deps.factory_store
    work = {
        p.slug for p in store.list_projects(factory_id) if project_kind(p) == PROJECT_KIND_WORK
    }
    tasks = [t for t in store.list_tasks(factory_id) if t.project_slug in work]
    prs = [p for p in store.list_prs(factory_id) if p.project_slug in work]
    opportunities = store.list_opportunities(factory_id, limit=5000)
    goals = (doc.get("strategy") or {}).get("goals") or []
    counts: dict[str, int | None] = {
        "product": 1,
        "goal": len(goals),
        "need": len(opportunities),
        "feature": len(node_refs.stored_features(store, factory_id)),
        "project": len(work),
        "task": len(tasks),
        "change": len(prs),
        "run": sum(len(t.session_ids or []) for t in tasks),
        "fx": 1,
        "area": len(node_refs.FACTORY_AREAS),
        "item": None,
    }
    for parent, child in reversed(list(itertools.pairwise(_IMPLIED))):
        if counts[child] and not counts[parent]:
            counts[parent] = 1
    needs = counts["need"] or 0
    if goals and (any(o.goal is None for o in opportunities) or needs > len(opportunities)):
        counts["goal"] = len(goals) + 1
    return counts


def hidden(show: str, count: int | None) -> bool:
    """Whether a level shown *show* is hidden with *count* nodes on it."""
    if count is None:
        return False
    if show == "when_any":
        return count == 0
    if show == "when_many":
        return count <= 1
    return False


def view(
    structure: Mapping[str, Any], counts: Mapping[str, int | None], version: int
) -> dict[str, Any]:
    """
    The ladder of both trees, as ``GET …/levels`` returns it.

    A tree lists its levels below the root, top down; ``show`` is left out
    when ``always``, and ``debug`` and ``hidden`` when false.
    """
    levels = structure["levels"]
    trees: dict[str, Any] = {}
    for tree, keys in TREES.items():
        root, *below = keys
        rows = []
        for key in below:
            level = levels[key]
            row: dict[str, Any] = {"key": key, "name": level["name"], "plural": level["plural"]}
            if level["show"] != "always":
                row["show"] = level["show"]
            if level["debug"]:
                row["debug"] = True
            if hidden(level["show"], counts.get(key)):
                row["hidden"] = True
            row["authority"] = level["authority"]
            rows.append(row)
        trees[tree] = {
            "root": root,
            "name": levels[root]["name"],
            "home": structure["home"][tree],
            "levels": rows,
        }
    return {
        "trees": trees,
        "responders": structure["responders"],
        "verbs": structure["verbs"],
        "kinds": structure["kinds"],
        "order": structure["order"],
        "tiles_max": structure["tiles_max"],
        "fold_single_child": structure["fold_single_child"],
        "version": version,
    }


def for_factory(deps: FactoryDeps, factory_id: str) -> dict[str, Any]:
    """:returns: The levels of *factory_id* in the definition in force (synchronous)."""
    resolved = definition.definition_for(deps, factory_id)
    if resolved is None:
        raise ValueError(f"{factory_id} is not a factory root")
    counts = presence(deps, factory_id, resolved.doc)
    return view(resolved.doc["structure"], counts, resolved.version)


__all__ = ["for_factory", "hidden", "presence", "view"]
