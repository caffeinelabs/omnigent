"""
What an agent sees of the node a Room thread is about (cockpit v3 threads per node).

A person talks to a node where it is: ``factory`` is the product, a project's
thread its project, a PR's its change, and ``need:``, ``feature:``, ``study:``
and ``task:`` keys a node of those levels (:mod:`.node_refs`). The agent that
answers gets the node's header in the delivery, so it steers from the facts the
person sees: the breadcrumb, why the node exists, its status line (the blocker
path ending at whoever must act), its roll-up and the open asks under it
(:func:`header_lines`). ``sys_factory_node(op="read", ref)`` returns the same
node page as data (:func:`agent_page`); the header names it, as an option, since
an older host may not have the tool.

The header is on unless a factory's root carries ``omnigent.factory.node_header
= off`` (:func:`header_on`): the switch for a factory whose agents should keep
the old envelopes. Building it never fails a delivery: any error leaves the
header out (:func:`delivery_header`).

The tree is the factory as its own agents see it (every member readable,
:func:`full_tree`), memoized like the cockpit's (:func:`.nodes.tree_for`).
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import LABEL_PREFIX, THREAD_PR_PREFIX
from omnigent.server.factory import node_refs, nodes
from omnigent.server.factory.context import factory_deps
from omnigent.stores.factory_store import norm_id

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactoryDecision

_logger = logging.getLogger(__name__)

LABEL_NODE_HEADER: Final = f"{LABEL_PREFIX}node_header"
"""Root label: ``off`` leaves the node header out of this factory's Room deliveries."""
OFF_VALUES: Final = frozenset({"off", "0", "false", "no"})
ASKS_MAX: Final = 3
"""Open asks the header names (the most blocking first)."""
WHY_MAX: Final = 300
LINE_MAX: Final = 400
TOOL: Final = "sys_factory_node"
_TAG_RE: Final = re.compile(r"<[^>]+>")


def header_on(root: Conversation) -> bool:
    """:returns: Whether this factory's Room deliveries carry the node header."""
    value = (root.labels or {}).get(LABEL_NODE_HEADER) or ""
    return value.strip().lower() not in OFF_VALUES


def plain(html_text: Any, limit: int = WHY_MAX) -> str:
    """Product words without their markup, on one line, clipped."""
    text = html.unescape(_TAG_RE.sub("", str(html_text or "")))
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def thread_node(thread: str, pr_ids: Mapping[str, str] | None = None) -> str | None:
    """
    The node a Room thread is about: ``factory`` -> ``product``, ``improvements``
    -> ``fx``, ``project:<slug>``, a node thread's ref; a PR thread through
    *pr_ids* (PR row id -> its node ref). ``discovery`` is about no one node.
    """
    if thread.startswith(THREAD_PR_PREFIX):
        try:
            return (pr_ids or {}).get(norm_id(thread[len(THREAD_PR_PREFIX) :]))
        except ValueError:
            return None
    return node_refs.thread_ref(thread)


async def full_tree(app_state: Any, root: Conversation) -> nodes.Tree:
    """The factory's level tree as its own agents see it: every member readable."""
    from omnigent.server.factory.snapshot import get_snapshot

    deps = factory_deps(app_state)
    raw = await get_snapshot(app_state, root)
    readable = set(raw.top_level_of) | {norm_id(root.id)}
    return await nodes.tree_for(deps, root, raw, readable, app_state=app_state)


def _segment(segment: Mapping[str, Any]) -> str:
    sep = str(segment.get("sep") or "→")
    decision = segment.get("decision")
    if segment.get("person") and isinstance(decision, Mapping):
        verb = str(decision.get("verb") or "answer")
        named = str(decision.get("id") or "")
        tail = f" ({named})" if named and named not in verb and not decision.get("grouped") else ""
        return f"{sep} needs a person: {verb}{tail}"
    words = [
        str(w) for w in (segment.get("label"), segment.get("title") or segment.get("ref")) if w
    ]
    return f"{sep} {' '.join(words)}".rstrip()


def status_words(line: Mapping[str, Any]) -> str:
    """A ``StatusLine`` in plain words: its label, the path, the detail."""
    parts = [str(line.get("label") or "").rstrip(":")]
    moving = line.get("moving")
    if isinstance(moving, Mapping):
        what = moving.get("title") or moving.get("ref") or ""
        parts.append(f"{what}: {moving.get('activity') or ''}".strip(": "))
    segments = [s for s in line.get("segments") or [] if isinstance(s, Mapping)]
    if segments:
        parts.append(" ".join(_segment(s) for s in segments))
    if line.get("detail"):
        parts.append(str(line["detail"]))
    held = [
        h.get("title") or h.get("ref")
        for h in line.get("also_holds") or []
        if isinstance(h, Mapping)
    ]
    text = ": ".join(p for p in parts[:1] if p)
    rest = " ".join(p for p in parts[1:] if p)
    out = f"{text}: {rest}" if text and rest else text or rest
    if held:
        out += f" · also holds {', '.join(str(h) for h in held[:3])}"
    return plain(out, LINE_MAX)


def _money(value: Any) -> str:
    return f"${float(value):,.2f}"


def rollup_words(rollup: Mapping[str, Any], level_name: str = "") -> str | None:
    """The roll-up in one line: progress, cost of budget, burn, ETA, what is in the base env."""
    parts: list[str] = []
    progress = rollup.get("progress")
    if isinstance(progress, Mapping) and progress.get("what") != "not planned":
        what = str(progress.get("what") or "")
        parts.append(f"{what} {progress.get('label')}".strip())
        if progress.get("secondary") is not None:
            parts.append(f"build {round(float(progress['secondary']) * 100)} %")
    elif isinstance(progress, Mapping):
        parts.append("no tasks planned yet")
    cost = rollup.get("cost")
    if cost:
        budget = rollup.get("budget")
        parts.append(f"{_money(cost)} of {_money(budget)}" if budget else f"{_money(cost)} spent")
    if rollup.get("burn"):
        parts.append(f"{_money(rollup['burn'])}/h")
    if rollup.get("eta"):
        parts.append(f"ETA {rollup['eta']}")
    base = rollup.get("in_base_env")
    if isinstance(base, Mapping) and base.get("sha"):
        parts.append(f"in the base env @{base['sha']}")
    counts = [
        f"{c.get('count')} {str(c.get('plural') or c.get('name') or '').lower()}"
        for c in rollup.get("counts") or []
        if isinstance(c, Mapping) and c.get("count")
    ]
    if counts:
        parts.append(", ".join(counts))
    if rollup.get("moving_anywhere") is False and level_name:
        parts.append(f"nothing under this {level_name.lower()} is moving")
    return " · ".join(parts) or None


def _asks(tree: nodes.Tree, node: Any) -> list[FactoryDecision]:
    """Open decisions at or under *node*, the blocking ones first, then the oldest."""
    found: dict[str, FactoryDecision] = {}
    for at in [node, *tree.subtree(node.ref)]:
        for decision in tree.decisions_at.get(at.ref, []):
            found.setdefault(decision.id, decision)
    return sorted(found.values(), key=lambda d: (not tree.blocks(d), d.number))


def header_lines(tree: nodes.Tree, ref: str) -> list[str]:
    """
    *ref*'s header as an agent turn carries it (empty when the tree has no such node).

    :returns: ``--- T-16, as the cockpit shows it ---``, then Path, Why, Status,
        Roll-up, Open asks and how to read the whole page.
    """
    found = tree.lookup(ref)
    if found is None:
        return []
    node = tree.fold(found)
    level = tree.level_name(node.level)
    title = f"{node.tag} · {node.title}" if node.tag and node.tag != node.title else node.title
    lines = [f"--- This thread is about {level} {title} ({node.ref}), as the cockpit shows it ---"]
    crumbs = [a.title for a in tree.ancestors(node.ref)]
    if crumbs:
        lines.append(plain("Path: " + " › ".join([*crumbs, node.title]), LINE_MAX))
    why = plain(tree.node_full(node).get("why_html"))
    if why:
        lines.append(f"Why: {why}")
    lines.append(f"Status: {status_words(tree.status(node))}")
    root = node.ref in (node_refs.PRODUCT, node_refs.FX)
    if rolled := rollup_words(tree.rollup(node), level if root else ""):
        lines.append(f"Roll-up: {rolled}")
    asks = _asks(tree, node)
    if asks:
        words = [tree.paths.decision_ask(d).person_words() for d in asks[:ASKS_MAX]]
        more = f" +{len(asks) - ASKS_MAX} more" if len(asks) > ASKS_MAX else ""
        lines.append(plain(f"Open asks: {' · '.join(words)}{more}", LINE_MAX))
    lines.append(
        f'More: {TOOL}(op="read", ref="{node.ref}") returns this node\'s page as data '
        "(children, chips, verbs), when your tools have it."
    )
    return lines


def header_text(tree: nodes.Tree, ref: str, limit: int) -> str | None:
    """:func:`header_lines` within *limit* chars (the last lines go first); ``None`` when none."""
    lines = header_lines(tree, ref)
    while lines and len("\n".join(lines)) > limit:
        lines.pop(-2 if len(lines) > 2 else -1)
    return "\n".join(lines) or None


async def delivery_header(app_state: Any, factory_id: str, thread: str, limit: int) -> str | None:
    """
    The node header a Room delivery in *thread* carries, within *limit* chars.

    :returns: ``None`` when the factory switched it off, the thread is about no
        one node, or anything failed (a delivery never waits on the header).
    """
    try:
        deps = factory_deps(app_state)
        from omnigent.server.factory.control import get_conv

        root = await get_conv(deps, factory_id)
        if root is None or not header_on(root):
            return None
        tree = await full_tree(app_state, root)
        ref = thread_node(thread, tree.pr_refs_by_id())
        return header_text(tree, ref, limit) if ref else None
    except Exception:  # noqa: BLE001 - the delivery goes out without the header
        _logger.warning("factory room: no node header for %s", thread, exc_info=True)
        return None


async def agent_page(
    app_state: Any,
    root: Conversation,
    readable: Iterable[str],
    ref: str,
    *,
    cursor: str | None = None,
) -> dict[str, Any] | None:
    """
    ``sys_factory_node(op="read", ref)``: the node page as the caller's runner may read it.

    :returns: The ``NodePage`` (verbs disabled: agents act through proposals), or
        ``None`` for a ref the factory has no node for.
    :raises ValueError: On a cursor no page handed out.
    """
    from omnigent.server.factory.snapshot import get_snapshot

    deps = factory_deps(app_state)
    raw = await get_snapshot(app_state, root)
    tree = await nodes.tree_for(deps, root, raw, readable, app_state=app_state)
    page = tree.page(ref, cursor=cursor, can_edit=False)
    if page is None:
        return None
    for verb in page.get("verbs") or []:
        verb["reason"] = "Agents propose changes (sys_factory_project); people use the verbs."
    return page


__all__ = [
    "LABEL_NODE_HEADER",
    "agent_page",
    "delivery_header",
    "full_tree",
    "header_lines",
    "header_on",
    "header_text",
    "plain",
    "rollup_words",
    "status_words",
    "thread_node",
]
