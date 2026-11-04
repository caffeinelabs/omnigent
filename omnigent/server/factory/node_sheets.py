"""
Two sheets over the whole level tree: Needs you, and Find.

:func:`needs_you` (``GET /factories/{fid}/needs-you``) lists every ask a
person owes in one tree, ranked by the work each one holds (the same weight
the blocker paths rank by: the estimates of the tasks it holds plus the
projects that wait on where it sits), each with its breadcrumb and what it
holds. Ready PRs a person merges into one base are one ask; asks that block
nothing are grouped apart and never ranked; the alarms the server closed
are listed once with their causes (product tree); the other tree's count
says what waits there.

:func:`find` (``GET /factories/{fid}/find``) finds a node by ref or title
(``T-16``, ``#9876``, ``C-16``, ``D-45``, words of a title) and returns each
with its breadcrumb. A decision or proposal finds the node it is about.
Done work stays in the tree, so it is found however long ago it closed.

Both read a built :class:`~omnigent.server.factory.nodes.Tree` (pure).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Final, Literal

from omnigent.server.factory import node_refs, your_move
from omnigent.server.factory.definition_blocks import FACTORY_TREE, PRODUCT_TREE

if TYPE_CHECKING:
    from omnigent.server.factory.node_status import Ask
    from omnigent.server.factory.nodes import _N, Tree

TreeName = Literal["product", "factory"]
TREE_KEYS: Final[dict[str, str]] = {"product": PRODUCT_TREE, "factory": FACTORY_TREE}
"""``?tree=`` -> the tree it names."""
FIND_LIMIT: Final = 20
FIND_MAX: Final = 50
QUERY_MAX: Final = 120
_FIND_LEVELS: Final = frozenset({"feature", "project", "task"})
"""What an empty query lists: the work a person holds up, at the levels people steer."""
_NUMBERED_RE: Final = re.compile(r"^([a-z]{1,2})-(\d{1,9})$", re.IGNORECASE)


def _crumbs(tree: Tree, node: _N) -> list[dict[str, Any]]:
    """The breadcrumb of *node*, root first, ending at the node itself."""
    return [tree.crumb(a) for a in [*tree.ancestors(node.ref), node]]


def _tree_of(tree: Tree, ref: str | None) -> str | None:
    node = tree.nodes.get(ref or "")
    return node.tree if node is not None else None


def _asks(tree: Tree) -> list[tuple[Ask, str, int]]:
    """Every open ask in the tree: ``(ask, the node it sits on, when it was asked)``."""
    paths = tree.paths
    out: list[tuple[Ask, str, int]] = []
    for ref, decisions in tree.decisions_at.items():
        for decision in decisions:
            out.append((paths.decision_ask(decision), ref, int(decision.created_at)))
    for ask, prs in paths.merge_asks():
        node = next(
            (ref for pr in prs if (ref := tree.pr_node(pr.id)) is not None),
            None,
        )
        if node is None:
            continue
        at = ask.at if ask.at in tree.nodes else node
        out.append((ask, at, ask.since or 0))
    return out


def _item(tree: Tree, ask: Ask, at: str, root: _N) -> dict[str, Any]:
    node = tree.nodes[at]
    return {
        "decision": ask.to_json(),
        "breadcrumb": _crumbs(tree, node),
        "holds": tree.paths.holds(ask) if ask.blocking else [],
        "weight": round(tree.paths.weight(ask), 1) if ask.blocking else 0.0,
        # The row Your move shows for it on the tree's root page (short names, one action).
        "move": your_move.item(tree, root, ask, at),
    }


def needs_you(tree: Tree, which: TreeName = "product") -> dict[str, Any]:
    """
    ``GET /factories/{fid}/needs-you?tree=``: what a person owes in one tree.

    ``items`` are the asks that hold work, most weight first (then oldest);
    ``blocks_nothing`` the asks that hold nothing, oldest first, in the same
    shape (their ``holds`` empty); each with ``move``, the row Your move shows
    for it on the tree's root page; ``closed_alarms`` (product tree) the alarms
    the server closed today with their causes; ``other_tree`` how many asks
    wait in the other tree.
    """
    key = TREE_KEYS[which]
    root = tree.nodes[node_refs.FX if which == "factory" else node_refs.PRODUCT]
    held: list[tuple[float, int, str, dict[str, Any]]] = []
    idle: list[tuple[int, str, dict[str, Any]]] = []
    other = 0
    for ask, at, asked in _asks(tree):
        if _tree_of(tree, at) != key:
            other += 1
            continue
        item = _item(tree, ask, at, root)
        if ask.blocking:
            held.append((-item["weight"], asked, ask.id, item))
        else:
            idle.append((asked, ask.id, item))
    out: dict[str, Any] = {
        "tree": which,
        "items": [entry[-1] for entry in sorted(held, key=lambda e: e[:3])],
        "blocks_nothing": [entry[-1] for entry in sorted(idle, key=lambda e: e[:2])],
        "other_tree": other,
        "version": tree.version,
    }
    if which == "product" and tree.closed_alarms:
        out["closed_alarms"] = [a.to_json() for a in tree.closed_alarms]
    return out


# ── Find ───────────────────────────────────────────────────────


def _hit(tree: Tree, node: _N, match: str, via: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ref": node.ref,
        "level": node.level,
        "level_name": tree.level_name(node.level),
        "title": node.title,
        "state": tree.state(node.ref),
        "breadcrumb": [tree.crumb(a) for a in tree.ancestors(node.ref)],
        "match": match,
    }
    if node.tag:
        out["tag"] = node.tag
    if node.implicit:
        out["implicit"] = True
    if via:
        out["via"] = via
    return out


def _spellings(q: str) -> list[str]:
    """*q* as typed, and with a code's letters upper-cased (``t-16`` -> ``T-16``)."""
    out = [q]
    if match := _NUMBERED_RE.match(q):
        out.append(f"{match.group(1).upper()}-{match.group(2)}")
    return out


def _by_ref(tree: Tree, q: str) -> list[tuple[_N, str, str | None]]:
    """Nodes *q* names as a ref, a decision or a proposal: ``(node, match, via)``."""
    out: list[tuple[_N, str, str | None]] = []
    for text in _spellings(q):
        found = tree.lookup(text)
        if found is not None:
            out.append((tree.fold(found), "ref", None))
            continue
        for decision in tree.facts.decisions:
            if text not in (decision.ref, node_refs.normalize(decision.subject or "")):
                continue
            at = tree.decision_node.get(decision.id)
            if at in tree.nodes:
                out.append((tree.nodes[at], "ask", decision.ref))
        parsed = node_refs.parse(text)
        if parsed is not None and parsed.kind == "proposal":
            for ref in tree.proposal_nodes(parsed.text):
                out.append((tree.nodes[ref], "proposal", parsed.text))
    return out


def _by_title(tree: Tree, q: str) -> list[tuple[int, int, str, _N]]:
    """Nodes whose title or code holds *q*: prefix matches first, then higher levels."""
    words = q.lower()
    out: list[tuple[int, int, str, _N]] = []
    for node in tree.nodes.values():
        if node.parent is None or node.level == "run" or tree.folded(node):
            continue
        title = node.title.lower()
        tag = (node.tag or "").lower()
        full = (node.full_title or "").lower()
        if (
            words not in title
            and words not in full
            and words not in tag
            and words != node.ref.lower()
        ):
            continue
        prefix = 0 if title.startswith(words) or tag == words else 1
        out.append((prefix, max(tree.level_index(node), 0), node.title.lower(), node))
    out.sort(key=lambda e: e[:3])
    return out


def find(tree: Tree, q: str, *, limit: int = FIND_LIMIT) -> dict[str, Any]:
    """
    ``GET /factories/{fid}/find?q=``: nodes by ref or title, each with its breadcrumb.

    Refs first (``T-16``, ``#9876``, ``PR-9876``, a slug), then what a
    decision (``D-45``) or proposal (``C-16``) is about, then titles that
    start with the words, then titles that hold them (higher levels first).
    An empty query lists the work that holds a person's ask, most held first.
    """
    query = " ".join(q.split())[:QUERY_MAX]
    cap = max(1, min(limit, FIND_MAX))
    hits: dict[str, dict[str, Any]] = {}

    def add(node: _N, match: str, via: str | None = None) -> None:
        if node.ref not in hits and len(hits) < cap:
            hits[node.ref] = _hit(tree, node, match, via)

    if not query:
        for node in _holding(tree):
            add(node, "needs")
    else:
        for node, match, via in _by_ref(tree, query):
            add(node, match, via)
        for *_key, node in _by_title(tree, query):
            add(node, "title")
    return {"q": query, "items": list(hits.values()), "version": tree.version}


def _holding(tree: Tree) -> Iterable[_N]:
    """Feature, project and task nodes with a blocking ask in their subtree, most held first."""
    found = [
        node
        for node in tree.nodes.values()
        if node.level in _FIND_LEVELS
        and node.tree == PRODUCT_TREE
        and not tree.folded(node)
        and tree.needs(node.ref)[1]
    ]
    found.sort(key=lambda n: (tree.level_index(n), -tree.needs(n.ref)[1], n.title.lower()))
    return found


__all__ = ["FIND_LIMIT", "FIND_MAX", "TREE_KEYS", "find", "needs_you"]
