"""
Your move: what the viewer must do at a node, first on every page.

A node page opens with at most :data:`ITEMS_MAX` rows; the rest are a count
(``more``) and the next few rows (``rest``). Each row is an ask in plain words
(:mod:`.ask_words`: "Atlas wants Connectors core to also edit 3 more files",
answers named by their outcome) plus what answering it unblocks, only when
that is true now ("unblocks Debit each turn"). The rows are ranked as the
Needs you sheet ranks them (:func:`.node_sheets.needs_you`): the asks that
hold work by the weight they hold, then the oldest; asks that block nothing
last. Only the asks this viewer answers count.

Your move belongs to a level (calm rule 8):

- A page lists only the asks whose subject is its own node or one of its
  direct children (where the ask sits, :meth:`.nodes.Tree.decision_node`; a
  group of merges sits on its project and its PRs).
- An ask deeper down is one line per child (``below``: "Assistant core · 2
  need you", linking to that child), never its row again.
- A root (Home) is the inbox: the top asks across its whole tree, each naming
  where it lives (``where``).
- A page with nothing of its own whose blocker path ends at an ask elsewhere
  says where that ask lives (``held_by``), so a held node never just reads
  "Nothing needs you".

So an ask appears on Home and on the page that owns it, never on every level
in between.

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

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, TypeAlias

from omnigent.server.auth import LEVEL_EDIT
from omnigent.server.factory import ask_words, decision_routing, node_names
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
REST_MAX: Final = 12
"""Rows past the first three a page carries (``rest``, shown behind "+n more")."""
BELOW_MAX: Final = 6
"""Children a page names with asks under them (``below``); the rest are ``below_more``."""
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


Found: TypeAlias = tuple["Ask", str, int, frozenset[str]]
"""An ask, the node it sits on, when it was asked, and every node it sits on."""


def _candidates(tree: Tree, node: _N) -> list[Found]:
    under = {node.ref, *(n.ref for n in tree.subtree(node.ref))}
    found: dict[str, Found] = {}
    for ask, at, asked, sits in _asks(tree):
        if sits & under:
            found.setdefault(ask.id, (ask, at, asked, sits))
    if node.parent is not None:
        return list(found.values())
    # The inbox also asks what holds its tree from elsewhere (its blocker paths' ends).
    for kid in [node, *tree.shown_children(node.ref)]:
        if kid.ghost or kid.later:
            continue
        path = tree.paths.path(kid.ref)
        if path.end == "person" and path.ask is not None and path.ask.id not in found:
            ask = path.ask
            at = ask.at if ask.at in tree.nodes else kid.ref
            found[ask.id] = (ask, at, ask.since or 0, frozenset({at}))
    return list(found.values())


def candidates(tree: Tree, node: _N) -> list[tuple[Ask, str, int]]:
    """
    The asks that concern *node*: the ones in its subtree; a root's also the
    ones its own blocker path and its children's end at (what holds it,
    wherever that sits).
    """
    return [(ask, at, asked) for ask, at, asked, _sits in _candidates(tree, node)]


def _ranked(tree: Tree, node: _N, viewer: Viewer | None) -> list[Found]:
    decisions = _decisions(tree)
    held: list[tuple[float, int, str, Found]] = []
    idle: list[tuple[int, str, Found]] = []
    for entry in _candidates(tree, node):
        ask, _at, asked, _sits = entry
        if viewer is not None and not viewer.answers(decisions.get(ask.id)):
            continue
        if ask.blocking:
            held.append((-tree.paths.weight(ask), asked, ask.id, entry))
        else:
            idle.append((asked, ask.id, entry))
    held.sort(key=lambda e: e[:3])
    idle.sort(key=lambda e: e[:2])
    return [e[-1] for e in held] + [e[-1] for e in idle]


def ranked(tree: Tree, node: _N, viewer: Viewer | None = None) -> list[tuple[Ask, str]]:
    """The asks of :func:`candidates` this viewer answers, most held work first."""
    return [(ask, at) for ask, at, _asked, _sits in _ranked(tree, node, viewer)]


_HERE: Final = ""
""":func:`_place` for a node this page lists an ask of: its own, or a direct child."""


def _place(tree: Tree, node: _N, ref: str) -> str | None:
    """
    Where an ask sitting on *ref* belongs from *node*'s page: :data:`_HERE` (the
    page's node or one of its children), the child holding it (deeper down),
    or ``None`` (outside the page's subtree). Folded implicit nodes count as
    the node they fold into.
    """
    found = tree.nodes.get(ref)
    if found is None:
        return None
    folded = tree.fold(found)
    if folded.ref == node.ref:
        return _HERE
    if node.ref not in {a.ref for a in tree.ancestors(ref)}:
        return None
    child = _shown_as(tree, node, ref)
    return _HERE if child == folded.ref else child


def _by_level(
    tree: Tree, node: _N, found: list[Found]
) -> tuple[list[tuple[Ask, str]], dict[str, int]]:
    """``(the asks this page lists, asks per child for the ones deeper down)``."""
    here: list[tuple[Ask, str]] = []
    below: dict[str, int] = {}
    for ask, at, _asked, sits in found:
        places = {_place(tree, node, ref) for ref in sorted(sits)}
        if _HERE in places:
            here.append((ask, at))
            continue
        for kid in sorted(p for p in places if p):
            below[kid] = below.get(kid, 0) + 1
    return here, below


def _held_by(tree: Tree, node: _N, viewer: Viewer | None) -> dict[str, str] | None:
    """
    The node an ask that holds *node* lives on, when that is another page
    ("Held for an ask in Assistant core"); ``None`` when nothing holds it there
    or this viewer does not answer that ask.
    """
    path = tree.paths.path(node.ref)
    ask = path.ask
    if path.end != "person" or ask is None or ask.at is None:
        return None
    if viewer is not None and not viewer.answers(_decisions(tree).get(ask.id)):
        return None
    found = tree.nodes.get(ask.at)
    if found is None:
        return None
    lives = tree.fold(found)
    if lives.ref == node.ref:
        return None
    return {"ref": lives.ref, "title": lives.title}


def your_move(tree: Tree, node: _N, viewer: Viewer | None = None) -> dict[str, Any]:
    """
    ``NodePage.your_move``: ``{items, more, rest?, below?, below_more?, held_by?,
    waves?}``; ``items`` empty with no ``below`` means nothing needs this viewer
    here ("Nothing needs you"); ``waves`` (:func:`wave_lines`) is left out when no
    initiative of several waves sits under the node.
    """
    found = _ranked(tree, node, viewer)
    inbox = node.parent is None
    if inbox:
        here, below = [(ask, at) for ask, at, _a, _s in found], {}
    else:
        here, below = _by_level(tree, node, found)
    out: dict[str, Any] = {
        "items": [item(tree, node, ask, at) for ask, at in here[:ITEMS_MAX]],
        "more": max(0, len(here) - ITEMS_MAX),
    }
    if len(here) > ITEMS_MAX:
        out["rest"] = [
            item(tree, node, ask, at) for ask, at in here[ITEMS_MAX : ITEMS_MAX + REST_MAX]
        ]
    if below:
        kids = list(below.items())
        out["below"] = [
            {"ref": ref, "title": tree.nodes[ref].title, "count": count}
            for ref, count in kids[:BELOW_MAX]
        ]
        if len(kids) > BELOW_MAX:
            out["below_more"] = len(kids) - BELOW_MAX
    elif not here and not inbox and (held := _held_by(tree, node, viewer)) is not None:
        out["held_by"] = held
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
            "blocking": d.blocking,
        }
        for d in tree.facts.decisions
    ]
    out: dict[str, dict[str, str]] = {}
    for row in tree.facts.initiatives:
        node = tree.nodes.get(row.ref)
        if row.status != "open" or node is None:
            continue
        state = wave_state(plan_of(row), projects)
        asked = initiative_ask(decisions, row.ref)
        phrase = wave_phrase(
            state, asked.words if asked else None, blocking=bool(asked and asked.blocking)
        )
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


def _action(
    ask: Ask, decision: FactoryDecision | None, answers: Sequence[Mapping[str, Any]] = ()
) -> dict[str, Any]:
    """
    The one primary action: the recommended (or applying) answer, a merge link,
    or "Answer"; named by its outcome as the row's answers name it ("Yes, allow it").
    """
    out = _raw_action(ask, decision)
    named = {str(a["option"]): str(a["label"]) for a in answers}
    if out.get("option") in named:
        out["label"] = named[out["option"]]
    return out


def _raw_action(ask: Ask, decision: FactoryDecision | None) -> dict[str, Any]:
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
    What a blocking *ask* lets move now, by the refs *node*'s page shows, this
    page's own node first. What the row's object names is no news ("Merge #1
    and #2 · unblocks #1 and #2"): when that is all it holds, it holds the
    product node the ask sits on (a project waiting on its merges: "this
    project"). An ask on a proposed node (a new project's apply) holds nothing
    that exists yet, and a factory change no product work: they unblock nothing.
    """
    shown = unblocks(tree, node, ask)
    refs = [r for r in shown if not _named_in(tree, r, obj)]
    found = tree.nodes.get(at)
    if found is not None and found.ghost and not refs:
        return []
    if not refs and (shown or (found is not None and found.tree == PRODUCT_TREE)):
        while found is not None and found.parent and _named_in(tree, found.ref, obj):
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
    """
    One row: ``{id, ref, sentence, answers, where?, unblocks?, decision, weight}``,
    and the older ``verb``, ``object``, ``object_name`` and ``action``.

    ``sentence`` and ``answers`` are the ask in plain words (:mod:`.ask_words`);
    ``where`` names the node it sits on when that is not *node* (Home names
    where each ask lives); ``unblocks`` only what answering it frees now.
    """
    decision = _decisions(tree).get(ask.id)
    verb, obj = split_verb(ask.verb)
    said = ask_words.words(tree, ask)
    refs = _held(tree, node, ask, at, obj) if ask.blocking else []
    # What the sentence names already is no news ("Atlas wants Assistant core to …").
    news = [r for r in refs if _name(tree, node, r).lower() not in said.sentence.lower()]
    refs = news or refs
    ref = tree.fold(tree.nodes[at]).ref if at in tree.nodes else at
    out: dict[str, Any] = {
        "id": ask.id,
        "ref": ref,
        "sentence": said.sentence,
        "answers": said.answers,
        "verb": verb,
        "object": obj or ask.id,
        "action": _action(ask, decision, said.answers),
        "decision": ask.to_json(),
        "weight": round(tree.paths.weight(ask), 1) if ask.blocking else 0.0,
    }
    if ref != node.ref and ref in tree.nodes:
        out["where"] = {"ref": ref, "title": tree.nodes[ref].title}
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
    "BELOW_MAX",
    "ITEMS_MAX",
    "REST_MAX",
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
