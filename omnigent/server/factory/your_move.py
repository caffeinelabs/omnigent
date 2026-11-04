"""
Your move: what the viewer must do at a node, first on every page.

A node page opens with at most :data:`ITEMS_MAX` rows, each a verb, a short
object, one primary action and a few words on what it unblocks ("Apply C-16 ·
unblocks Agent assistant · [Apply]"); the rest is a count (``more``). The rows
are the asks of the node's subtree and the asks its blocker path (and its
children's) ends at, cut to the ones this viewer answers, ranked as the Needs
you sheet ranks them (:func:`.node_sheets.needs_you`): the asks that hold work
by the weight they hold, then the oldest; asks that block nothing last.

The page shows each ask once: every other place the page carries one of these
asks (the status line, a child's line, a segment) is marked ``in_your_move``
(:func:`mark_repeats`), so the cockpit draws no second button, banner or card.

Above the feature level, Your move also says where each initiative of several
waves under the node is (``waves``: "Agent assistant: wave 1 done · wave 2
started", :func:`wave_lines`), so the person sees the next wave move, or the
one input it is blocked on, without opening the feature.

Pure: it reads a built :class:`~omnigent.server.factory.nodes.Tree`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from omnigent.server.auth import LEVEL_EDIT
from omnigent.server.factory import decision_routing, node_names
from omnigent.server.factory.definition_blocks import PRODUCT_TREE
from omnigent.server.factory.initiative_view import (
    initiative_ask,
    plan_of,
    wave_phrase,
    wave_state,
)
from omnigent.server.factory.node_status import decision_proposal

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryDecision
    from omnigent.server.factory.node_status import Ask
    from omnigent.server.factory.nodes import _N, Tree

ITEMS_MAX: Final = 3
"""Rows a page shows; the rest are ``more``."""
NAMED_MAX: Final = 2
"""Nodes an "unblocks" names before ``+n``."""
_CODE_START: Final = ("C-", "D-", "#", "PR-", "T-", "I-", "O-", "R-", "G-")
WAVE_LEVELS: Final = frozenset({"product", "goal", "need", "feature"})
"""Levels whose Your move says where their initiatives' waves are."""


@dataclass(frozen=True)
class Viewer:
    """
    Who reads the page.

    :param user_id: ``None``: an agent or the server (every ask counts).
    :param level: Their level on the factory root.
    """

    user_id: str | None
    level: int

    def answers(self, decision: FactoryDecision | None) -> bool:
        """Whether this viewer answers *decision* (``None``: a merge, which editors make)."""
        if self.user_id is None:
            return True
        if decision is None:
            return self.level >= LEVEL_EDIT
        return decision_routing.is_mine(decision, self.user_id, self.level)


def _asks(tree: Tree) -> list[tuple[Ask, str, int, frozenset[str]]]:
    """
    Every open ask: ``(ask, the node it sits on, when asked, the nodes it sits on)``;
    a group of merges sits on each of its PRs' nodes.
    """

    def make() -> list[tuple[Ask, str, int, frozenset[str]]]:
        paths = tree.paths
        out: list[tuple[Ask, str, int, frozenset[str]]] = []
        for ref, decisions in tree.decisions_at.items():
            for decision in decisions:
                ask = paths.decision_ask(decision)
                out.append((ask, ref, int(decision.created_at), frozenset({ref})))
        for ask, prs in paths.merge_asks():
            sits = frozenset(r for pr in prs if (r := tree.pr_node(pr.id)) is not None)
            if not sits:
                continue
            at = ask.at if ask.at in tree.nodes else sorted(sits)[0]
            out.append((ask, at, ask.since or 0, sits | {at}))
        return out

    return tree.memo("your_move", "asks", make)


def _decisions(tree: Tree) -> dict[str, FactoryDecision]:
    return tree.memo("your_move", "decisions", lambda: {d.ref: d for d in tree.facts.decisions})


def candidates(tree: Tree, node: _N) -> list[tuple[Ask, str, int]]:
    """
    The asks that concern *node*: the ones in its subtree, and the ones its own
    blocker path and its children's end at (what holds it, wherever that sits).
    """
    under = {node.ref, *(n.ref for n in tree.subtree(node.ref))}
    found: dict[str, tuple[Ask, str, int]] = {}
    for ask, at, asked, sits in _asks(tree):
        if sits & under:
            found.setdefault(ask.id, (ask, at, asked))
    for kid in [node, *tree.shown_children(node.ref)]:
        if kid.ghost or kid.later:
            continue
        path = tree.paths.path(kid.ref)
        if path.end == "person" and path.ask is not None and path.ask.id not in found:
            ask = path.ask
            at = ask.at if ask.at in tree.nodes else kid.ref
            found[ask.id] = (ask, at, ask.since or 0)
    return list(found.values())


def ranked(tree: Tree, node: _N, viewer: Viewer | None = None) -> list[tuple[Ask, str]]:
    """The asks of :func:`candidates` this viewer answers, most held work first."""
    decisions = _decisions(tree)
    held: list[tuple[float, int, str, Ask, str]] = []
    idle: list[tuple[int, str, Ask, str]] = []
    for ask, at, asked in candidates(tree, node):
        if viewer is not None and not viewer.answers(decisions.get(ask.id)):
            continue
        if ask.blocking:
            held.append((-tree.paths.weight(ask), asked, ask.id, ask, at))
        else:
            idle.append((asked, ask.id, ask, at))
    held.sort(key=lambda e: e[:3])
    idle.sort(key=lambda e: e[:2])
    return [(e[-2], e[-1]) for e in held] + [(e[-2], e[-1]) for e in idle]


def your_move(tree: Tree, node: _N, viewer: Viewer | None = None) -> dict[str, Any]:
    """
    ``NodePage.your_move``: ``{items, more, waves?}``; ``items`` empty means nothing
    needs this viewer here ("Nothing needs you"); ``waves`` (:func:`wave_lines`)
    is left out when no initiative of several waves sits under the node.
    """
    found = ranked(tree, node, viewer)
    out: dict[str, Any] = {
        "items": [item(tree, node, ask, at) for ask, at in found[:ITEMS_MAX]],
        "more": max(0, len(found) - ITEMS_MAX),
    }
    if waves := wave_lines(tree, node):
        out["waves"] = waves
    return out


def _wave_lines(tree: Tree) -> dict[str, dict[str, str]]:
    """Initiative ref -> its wave line, for every open initiative of several waves."""
    projects = {
        p.slug: {"stage": p.stage, "status": p.status}
        for p in tree.facts.projects
        if p.slug not in tree.hidden
    }
    decisions = [
        {
            "status": d.status,
            "subject": d.subject,
            "question": d.question,
            "ref": d.ref,
            "created_at": d.created_at,
        }
        for d in tree.facts.decisions
    ]
    out: dict[str, dict[str, str]] = {}
    for row in tree.facts.initiatives:
        node = tree.nodes.get(row.ref)
        if row.status != "open" or node is None:
            continue
        state = wave_state(plan_of(row), projects)
        phrase = wave_phrase(state, initiative_ask(decisions, row.ref))
        if phrase:
            out[row.ref] = {"ref": row.ref, "title": node.title, "line": f"{node.title}: {phrase}"}
    return out


def wave_lines(tree: Tree, node: _N) -> list[dict[str, str]]:
    """
    Where each initiative of several waves at or under *node* is, as one line
    (``{ref, title, line}``: "Agent assistant: wave 1 done · wave 2 started"),
    for a product, goal, need or feature page.
    """
    if node.tree != PRODUCT_TREE or node.level not in WAVE_LEVELS:
        return []
    lines = tree.memo("your_move", "waves", lambda: _wave_lines(tree))
    return [
        line
        for ref, line in lines.items()
        if ref == node.ref or node.ref in {a.ref for a in tree.ancestors(ref)}
    ]


def split_verb(verb: str) -> tuple[str, str]:
    """``Apply C-16`` -> ``("Apply", "C-16")``; ``Merge #1 and #2`` -> ``("Merge", "#1 and #2")``.

    The object starts at the first code (``C-``, ``D-``, ``#`` …); without one it is empty.
    """
    tokens = verb.split()
    for index, token in enumerate(tokens[1:], start=1):
        if token.startswith(_CODE_START):
            return " ".join(tokens[:index]), " ".join(tokens[index:])
    return (tokens[0], " ".join(tokens[1:])) if tokens else ("", "")


def _action(ask: Ask, decision: FactoryDecision | None) -> dict[str, Any]:
    """The one primary action: the recommended (or applying) answer, a merge link, or "Answer"."""
    if decision is not None and decision.options:
        options = [o for o in decision.options if isinstance(o, Mapping) and o.get("id")]
        pick = next((o for o in options if o.get("recommended")), None)
        if pick is None and decision.source == "proposal":
            pick = next((o for o in options if o.get("id") == "apply"), None)
        if pick is not None:
            return {"label": str(pick.get("label") or pick["id"]), "option": str(pick["id"])}
        return {"label": "Answer"}
    if ask.actions:
        option, label = ask.actions[0]
        out: dict[str, Any] = {"label": label, "option": option}
        if ask.external_url:
            out["url"] = ask.external_url
        return out
    return {"label": "Answer"}


def _listed_level(tree: Tree, node: _N) -> str | None:
    """The level the page lists under *node*: a root's home level, else none (its children)."""
    if node.parent is None:
        return tree.home.get(node.tree) or (tree.ladder[node.tree][1:2] or [None])[0]
    return None


def _shown_as(tree: Tree, node: _N, ref: str) -> str:
    """Where *ref* shows on *node*'s page: the child (or home-level node) holding it, folded."""
    found = tree.nodes.get(ref)
    if found is None:
        return ref
    chain = [*tree.ancestors(ref), found]
    refs = [n.ref for n in chain]
    if node.ref not in refs:
        return tree.fold(found).ref
    below = chain[refs.index(node.ref) + 1 :]
    if not below:
        return node.ref
    listed = _listed_level(tree, node)
    pick = next((n for n in below if n.level == listed), None) if listed else None
    return tree.fold(pick or below[0]).ref


def unblocks(tree: Tree, node: _N, ask: Ask) -> list[str]:
    """What answering *ask* lets move, as the refs *node*'s page shows it by."""
    out: dict[str, None] = {}
    for ref in tree.paths.held_refs(ask):
        out[_shown_as(tree, node, ref)] = None
    return list(out)


def _named_in(tree: Tree, ref: str, obj: str) -> bool:
    """Whether the row's object already names *ref* ("Merge #1 and #2" names #1)."""
    return tree.paths.name(ref) in obj.split()


def _held(tree: Tree, node: _N, ask: Ask, at: str, obj: str) -> list[str]:
    """
    What a blocking *ask* lets move, by the refs *node*'s page shows, this
    page's own node first. What the row's object names is no news ("Merge #1
    and #2 · unblocks #1 and #2"): when that is all it holds, or it holds no
    task yet (a proposed project's apply), it holds the product node the ask
    sits on (a project waiting on its merges: "this project"), a proposed
    node's parent. A factory change holds no product work: its object says it.
    """
    shown = unblocks(tree, node, ask)
    refs = [r for r in shown if not _named_in(tree, r, obj)]
    found = tree.nodes.get(at)
    if not refs and (shown or (found is not None and found.tree == PRODUCT_TREE)):
        while (
            found is not None and found.parent and (found.ghost or _named_in(tree, found.ref, obj))
        ):
            found = tree.nodes.get(found.parent)
        if found is not None and found.parent is not None:
            refs = [_shown_as(tree, node, found.ref)]
    return sorted(refs, key=lambda r: r != node.ref)


def _name(tree: Tree, node: _N, ref: str) -> str:
    """How a row names *ref*: by its short title, or "this task" on its own page."""
    if ref == node.ref:
        return f"this {tree.level_name(node.level).lower()}"
    found = tree.nodes.get(ref)
    return found.title if found is not None else ref


def _names(tree: Tree, node: _N, refs: Iterable[str]) -> str | None:
    named = [_name(tree, node, r) for r in refs]
    if not named:
        return None
    if len(named) <= NAMED_MAX:
        return " and ".join(named)
    return f"{', '.join(named[:NAMED_MAX])} +{len(named) - NAMED_MAX}"


def item(tree: Tree, node: _N, ask: Ask, at: str) -> dict[str, Any]:
    """One row: ``{id, ref, verb, object, object_name, unblocks, action, decision, weight}``."""
    decision = _decisions(tree).get(ask.id)
    verb, obj = split_verb(ask.verb)
    refs = _held(tree, node, ask, at, obj) if ask.blocking else []
    out: dict[str, Any] = {
        "id": ask.id,
        "ref": tree.fold(tree.nodes[at]).ref if at in tree.nodes else at,
        "verb": verb,
        "object": obj or ask.id,
        "action": _action(ask, decision),
        "decision": ask.to_json(),
        "weight": round(tree.paths.weight(ask), 1) if ask.blocking else 0.0,
    }
    if decision is not None and decision.source == "proposal":
        proposal = decision_proposal(decision, tree.proposals_by_ref)
        if proposal is not None and (name := tree.proposal_name(proposal)):
            out["object_name"] = name
    if (words := _names(tree, node, refs)) is not None:
        out["unblocks"] = words
        out["unblocks_ref"] = next((r for r in refs if r != node.ref), refs[0])
        out["unblocks_refs"] = refs
    if decision is not None and decision.source != "proposal" and decision.question:
        out["about"] = node_names.question_words(decision.question)
    if not ask.blocking:
        out["blocks_nothing"] = True
    return out


def _is_decision(value: Mapping[str, Any]) -> bool:
    return {"id", "verb", "actions", "asker"} <= value.keys()


def mark_repeats(page: dict[str, Any], ids: Iterable[str]) -> dict[str, Any]:
    """
    *page* with ``in_your_move`` on every other ``DecisionRef`` naming one of *ids*.

    Copy on write: the tree's memoized lines that *page* shares are never changed.
    """
    wanted = frozenset(ids)

    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            changed = {
                key: new
                for key, child in value.items()
                if key != "your_move" and (new := walk(child)) is not child
            }
            hit = _is_decision(value) and value.get("id") in wanted
            if not changed and not hit:
                return value
            out = {**value, **changed}
            if hit:
                out["in_your_move"] = True
            return out
        if isinstance(value, list):
            new = [walk(child) for child in value]
            return new if any(a is not b for a, b in zip(new, value, strict=True)) else value
        return value

    return walk(page) if wanted else page


__all__ = [
    "ITEMS_MAX",
    "WAVE_LEVELS",
    "Viewer",
    "candidates",
    "item",
    "mark_repeats",
    "ranked",
    "split_verb",
    "unblocks",
    "wave_lines",
    "your_move",
]
