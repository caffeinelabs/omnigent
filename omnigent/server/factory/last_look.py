"""
Since your last look: what changed under a node since a person last looked at it.

A person's last look at a node is a mark (``factory_node_reads``: one row per
person per node, moved forward by ``PUT …/n/{ref}/seen`` or ``GET …?seen=1``).
A node they never opened reads the mark of its nearest ancestor they did: what
they saw there summed up everything below it.

:func:`annotate` reads the events since that mark and writes them onto a node
page: ``rollup.since`` holds the three most significant changes in the node's
subtree (``{at, items, more}``) and each child its own (``changed``). Each
event is placed on the node it is about (a PR's change, a task, a decision's
node, a project); events about work the viewer cannot read find no node and
say nothing. Significance, most first: a new ask that holds work, a merge or
a failure, work added, a change applied, an answer, a task done, a stage
moved. More than two merges read as one ("4 merged").

Nothing here names a product: the words come from the factory's rows.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from omnigent.server.factory import node_refs
from omnigent.stores.factory_store import norm_id

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryEvent
    from omnigent.server.factory.nodes import _N, Tree
    from omnigent.stores.factory_store import FactoryStore

RANKS: Final[dict[str, int]] = {
    "decision.opened": 6,
    "pr.merged": 5,
    "pr.auto_merged": 5,
    "task.failed": 5,
    "project.added": 4,
    "initiative.created": 4,
    "proposal.applied": 3,
    "decision.answered": 2,
    "task.done": 1,
    "stage.changed": 0,
}
"""The event kinds a last look reads, most significant first."""
EVENTS_MAX: Final = 500
"""The newest events read since a mark (a mark days old reads the latest of them)."""
SHOWN: Final = 3
"""Changes a roll-up names; ``more`` counts the rest."""
CHANGED_MAX: Final = 9
"""Changes a child names at most."""
_MERGES_NAMED: Final = 2
_MERGED_RANK: Final = RANKS["pr.merged"]


@dataclass(frozen=True)
class Change:
    """
    One significant change, placed on the node it is about.

    :param at: When it happened (epoch milliseconds).
    :param node: The node it is about, as shown (folded).
    :param above: That node and every ancestor (the subtrees it belongs to).
    :param merged: A merge (several read as one).
    """

    at: int
    rank: int
    text: str
    node: str
    above: frozenset[str]
    merged: bool = False


def last_look(tree: Tree, node: _N, marks: Mapping[str, int]) -> int | None:
    """
    :param marks: Node ref -> this person's last look (epoch seconds).
    :returns: Their last look at *node*, else at its nearest ancestor, else ``None``.
    """
    for found in [node, *reversed(tree.ancestors(node.ref))]:
        if found.ref in marks:
            return int(marks[found.ref])
    return None


def mark_refs(tree: Tree, node: _N) -> list[str]:
    """:returns: The refs whose marks :func:`last_look` reads for *node*."""
    return [node.ref, *(a.ref for a in tree.ancestors(node.ref))]


def _hhmm(ms: int) -> str:
    return time.strftime("%H:%M", time.gmtime(ms // 1000))


def _bare(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return norm_id(value)
    except ValueError:
        return None


class _Placer:
    """Places events on the nodes of one tree."""

    def __init__(self, tree: Tree) -> None:
        self.tree = tree
        self.task_of: dict[str, str] = {}
        for ref, row in tree.task_rows.items():
            for session in row.session_ids or []:
                if (sid := _bare(session)) and ref in tree.nodes:
                    self.task_of[sid] = ref
        self.open = {d.ref: d for d in tree.facts.decisions}

    def project(self, slug: str | None) -> _N | None:
        ref = self.tree.project_ref(slug)
        return self.tree.nodes.get(ref) if ref else None

    def subject(self, event: FactoryEvent) -> _N | None:
        return self.tree.lookup(event.subject) if event.subject else None

    def change(self, event: FactoryEvent) -> Change | None:
        rank = RANKS.get(event.kind)
        if rank is None:
            return None
        found = self._place(event)
        if found is None:
            return None
        node, text = found
        tree = self.tree
        shown = tree.fold(node)
        above = frozenset({node.ref, shown.ref, *(a.ref for a in tree.ancestors(node.ref))})
        merged = event.kind in ("pr.merged", "pr.auto_merged")
        return Change(event.ts, rank, text, shown.ref, above, merged)

    def _place(self, event: FactoryEvent) -> tuple[_N, str] | None:
        tree = self.tree
        data = event.data or {}
        kind = event.kind
        if kind in ("pr.merged", "pr.auto_merged"):
            ref = tree.pr_node(_bare(data.get("pr_id")))
            if ref is None:
                return None
            node = tree.nodes[ref]
            return node, f"{node.tag or ref} merged {_hhmm(event.ts)}"
        if kind in ("task.done", "task.failed"):
            task = self._task(event)
            if task is None:
                return None
            if kind == "task.done" and tree.state(task.ref) != "done":
                return None  # its session went idle; the task is not done
            words = "done" if kind == "task.done" else "failed"
            return task, f"{task.tag or task.ref} {words}"
        decision_ref = self._decision_ref(event)
        if kind == "decision.opened":
            decision = self.open.get(decision_ref or "")
            at = tree.decision_node.get(decision.id) if decision is not None else None
            if decision is None or at is None or not tree.blocks(decision):
                return None
            return tree.nodes[at], f"{decision.ref} new"
        if kind == "decision.answered":
            if data.get("by_kind") == "server" or decision_ref is None:
                return None
            node = self.project(event.project_slug)
            return (node, f"{decision_ref} answered") if node is not None else None
        if kind == "proposal.applied":
            parsed = node_refs.parse(node_refs.normalize(event.subject or "") or "")
            if parsed is None or parsed.kind != "proposal":
                return None
            refs = tree.proposal_nodes(parsed.text)
            return (tree.nodes[refs[0]], f"{parsed.text} applied") if refs else None
        if kind in ("project.added", "initiative.created"):
            node = self.subject(event)
            if node is None or node.level not in ("project", "feature"):
                return None
            return node, f"{node.title} added"
        if kind == "stage.changed":
            node = self.project(event.project_slug)
            to = str(data.get("to") or "").replace("_", " ")
            return (node, f"{node.title}: {to}") if node is not None and to else None
        return None

    def _task(self, event: FactoryEvent) -> _N | None:
        found = self.subject(event)
        if found is not None and found.level == "task":
            return found
        ref = self.task_of.get(_bare(event.to_id) or "")
        return self.tree.nodes.get(ref) if ref else None

    @staticmethod
    def _decision_ref(event: FactoryEvent) -> str | None:
        parsed = node_refs.parse(node_refs.normalize(event.subject or "") or "")
        return parsed.text if parsed is not None and parsed.kind == "decision" else None


def changes(tree: Tree, events: Iterable[FactoryEvent]) -> list[Change]:
    """The events as changes on the nodes they are about, newest first, one per text."""
    placer = _Placer(tree)
    out: dict[tuple[str, str], Change] = {}
    for event in sorted(events, key=lambda e: (e.ts, e.id), reverse=True):
        found = placer.change(event)
        if found is not None and (found.node, found.text) not in out:
            out[(found.node, found.text)] = found
    return list(out.values())


def texts(found: Sequence[Change]) -> list[str]:
    """Changes in words, most significant first (then newest); three merges or more read as one."""
    merges = [c for c in found if c.merged]
    rows: list[tuple[int, int, str]] = [(c.rank, c.at, c.text) for c in found if not c.merged]
    if len(merges) > _MERGES_NAMED:
        rows.append((_MERGED_RANK, max(c.at for c in merges), f"{len(merges)} merged"))
    else:
        rows += [(c.rank, c.at, c.text) for c in merges]
    rows.sort(key=lambda r: (-r[0], -r[1]))
    return [r[2] for r in rows]


def since(found: Sequence[Change], ref: str, seen_at: int) -> dict[str, Any] | None:
    """``Rollup.since`` for the node *ref*: its subtree's changes, three named."""
    mine = texts([c for c in found if ref in c.above])
    if not mine:
        return None
    return {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seen_at)),
        "items": mine[:SHOWN],
        "more": max(0, len(mine) - SHOWN),
    }


def annotate(
    tree: Tree, page: dict[str, Any], events: Iterable[FactoryEvent], seen_at: int
) -> dict[str, Any]:
    """
    Write what changed since *seen_at* onto *page* (a ``NodePage`` this tree
    built): ``rollup.since`` and each child's ``changed``. Nothing is written
    where nothing changed.
    """
    cutoff = seen_at * 1000
    found = changes(tree, (e for e in events if e.ts > cutoff))
    if not found:
        return page
    ref = page["node"]["ref"]
    if (summary := since(found, ref, seen_at)) is not None:
        page["rollup"]["since"] = summary
    children = page["children"]
    for item in [*children["items"], *children["later"], *children["ghosts"]]:
        mine = texts([c for c in found if item["ref"] in c.above])
        if mine:
            item["changed"] = mine[:CHANGED_MAX]
    return page


def read_events(store: FactoryStore, factory_id: str, seen_at: int) -> list[FactoryEvent]:
    """The newest events a last look reads since *seen_at* (epoch seconds)."""
    return list(
        store.find_events(
            factory_id,
            list(RANKS),
            (seen_at + 1) * 1000,
            limit=EVENTS_MAX,
            newest_first=True,
        )
    )


__all__ = [
    "CHANGED_MAX",
    "EVENTS_MAX",
    "RANKS",
    "SHOWN",
    "Change",
    "annotate",
    "changes",
    "last_look",
    "mark_refs",
    "read_events",
    "since",
    "texts",
]
