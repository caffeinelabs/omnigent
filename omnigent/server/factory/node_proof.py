"""
Proof on the cockpit's node pages: the recorded demos (calm rule 10, "Proof is
never hidden").

Every recorded demo the viewer may read belongs to one node of the level tree:

- a PR's demo to its change (``PR-n``);
- an acceptance run's recording (a demo of no PR that names its initiative)
  to that feature (``I-n``);
- a demo of a PR the tree shows no change for (closed, or not tracked) to its
  project.

A demo of a project the viewer cannot read is left out with the project, as
its PRs are; a demo whose files belong to a session they cannot read is left
out when the facts are loaded (:func:`readable_demos`).

A node's demos are its subtree's, newest first. The page shows one of them by
default (``NodePage.proof.latest``: "Latest demo · PASS · 0:14 ▶"):

- a change, and the task that owns it: the demo of the PR's current head, else
  its newest (``stale``: it ran at an earlier commit);
- a project: its newest across its PRs, current heads first;
- a feature: its newest acceptance-run recording, else its newest project demo.

Other levels show no row; the product's Home links each feature's latest
from that feature's row (``NodeSummary.demo``: one "▶" chip). The rest sit
behind "n more demos": ``GET …/n/{ref}/demos`` (:func:`demos_page`), newest
first. PARTIAL and FAIL say what was partial in one line (``partial_note``).

Nothing here names a product: titles are the demos' own, made short.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from omnigent.server.factory import node_names
from omnigent.stores.factory_store import norm_id

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryDemo, FactoryPr
    from omnigent.server.factory.nodes import _N, Tree

READ_MAX: Final = 1000
"""The newest demos one projection reads (older ones are history no page shows)."""
PAGE_SIZE: Final = 20
"""Demos per page of ``GET …/n/{ref}/demos``."""
PAGE_MAX: Final = 100
"""The most a caller may ask for in one page."""
LATEST_LEVELS: Final = frozenset({"feature", "project", "task", "change"})
"""Levels whose page shows the latest demo as a row (a task shows its change's)."""
CHIP_LEVELS: Final = frozenset({"feature"})
"""Levels whose rows and tiles carry the "▶" chip (Home lists features; a node
shown in place of its folded implicit feature carries it too)."""
VERDICTS: Final = ("PASS", "PARTIAL", "FAIL", "ERROR")
_NOTE_MAX: Final = 140
_FILE_ROUTE: Final = "/v1/factory/demos/{demo}/files/{file}"


@dataclass(frozen=True)
class Demo:
    """
    One demo as the tree places it.

    :param row: The stored demo.
    :param home: The node it belongs to (a change, a feature or a project).
    :param current: Recorded at its PR's current head (always, for a demo of no PR).
    :param run: The acceptance run it records (``None`` for a PR demo).
    :param tag: Its PR as the tree tags it (``#9876``), when it has one.
    """

    row: FactoryDemo
    home: str
    current: bool = True
    run: int | None = None
    tag: str | None = None


@dataclass(frozen=True)
class Index:
    """
    Every placed demo (newest first) and, per node, the demos of its subtree.

    :param demos: Placed demos, newest first.
    :param under: Node ref -> positions in *demos* of its subtree's demos (ascending).
    """

    demos: tuple[Demo, ...]
    under: Mapping[str, tuple[int, ...]]

    def of(self, ref: str) -> list[Demo]:
        """:returns: The demos of *ref*'s subtree, newest first."""
        return [self.demos[i] for i in self.under.get(ref, ())]


def readable_demos(
    demos: Iterable[FactoryDemo], readable: Iterable[str] | None, root_id: str
) -> list[FactoryDemo]:
    """
    The demos a viewer may play: their files belong to a session the viewer
    may read (the file route checks the same), newest first.

    :param readable: The sessions the viewer may read (``None``: every one, an agent).
    :param root_id: The factory's root, which every viewer of it may read.
    """
    rows = sorted(demos, key=lambda d: (d.created_at, d.id), reverse=True)
    if readable is None:
        return rows
    sessions = {norm_id(s) for s in readable} | {norm_id(root_id)}
    return [d for d in rows if _bare(d.session_id) in sessions]


def _bare(value: str | None) -> str | None:
    try:
        return norm_id(value) if value else None
    except ValueError:
        return None


def _acceptance_run(demo: FactoryDemo) -> tuple[str, int | None] | None:
    """``(initiative ref, run number)`` for an acceptance run's recording, else ``None``."""
    accepted = (demo.manifest or {}).get("acceptance")
    if not isinstance(accepted, Mapping) or not accepted.get("initiative"):
        return None
    run = accepted.get("run")
    return str(accepted["initiative"]), run if isinstance(run, int) else None


def _pr_of(
    tree: Tree, demo: FactoryDemo, by_number: Mapping[tuple[str, int], FactoryPr]
) -> FactoryPr | None:
    if (pr_id := _bare(demo.pr_id)) and pr_id in tree.pr_rows:
        return tree.pr_rows[pr_id]
    return by_number.get((demo.repo.lower(), demo.pr_number))


def _project_home(tree: Tree, slug: str | None) -> str | None:
    """The node a demo of no change lands on: its project, when the tree shows it."""
    if not slug or slug in tree.hidden:
        return None
    node = tree.lookup(slug)
    return node.ref if node is not None and node.level == "project" else None


def _place(
    tree: Tree, demo: FactoryDemo, by_number: Mapping[tuple[str, int], FactoryPr]
) -> Demo | None:
    if demo.project_slug and demo.project_slug in tree.hidden:
        return None
    if (accepted := _acceptance_run(demo)) is not None:
        feature = tree.lookup(accepted[0])
        if feature is not None and feature.level == "feature":
            return Demo(demo, feature.ref, run=accepted[1])
        return None
    if not demo.pr_number:
        home = _project_home(tree, demo.project_slug)
        return Demo(demo, home) if home else None
    pr = _pr_of(tree, demo, by_number)
    refs = tree.pr_refs_by_id()
    change = refs.get(norm_id(pr.id)) if pr is not None else None
    if pr is not None and change is not None and change in tree.nodes:
        node = tree.nodes[change]
        current = bool(pr.head_sha) and (demo.head_sha or "").lower() == pr.head_sha.lower()
        return Demo(demo, change, current=current or not pr.head_sha, tag=node.tag)
    home = _project_home(tree, (pr.project_slug if pr is not None else None) or demo.project_slug)
    return Demo(demo, home, tag=f"#{demo.pr_number}") if home else None


def index(tree: Tree) -> Index:
    """Every demo the tree's viewer may play, placed on its node (made once per tree)."""

    def make() -> Index:
        by_number: dict[tuple[str, int], FactoryPr] = {}
        for pr in sorted(tree.facts.prs, key=lambda p: p.created_at):
            by_number[(pr.repo.lower(), pr.number)] = pr
        placed = [
            found
            for demo in tree.facts.demos
            if (found := _place(tree, demo, by_number)) is not None
        ]
        under: dict[str, list[int]] = {}
        for position, demo in enumerate(placed):
            under.setdefault(demo.home, []).append(position)
            for ancestor in tree.ancestors(demo.home):
                under.setdefault(ancestor.ref, []).append(position)
        return Index(tuple(placed), {ref: tuple(found) for ref, found in under.items()})

    return tree.memo("proof", "index", make)


def _pick(demos: Sequence[Demo]) -> Demo | None:
    """The newest demo recorded at its PR's current head, else the newest."""
    return next((d for d in demos if d.current), demos[0] if demos else None)


def latest(tree: Tree, node: _N) -> Demo | None:
    """The demo *node*'s page shows by default (``None``: no row at its level)."""
    if node.level not in LATEST_LEVELS:
        return None
    demos = index(tree).of(node.ref)
    if node.level == "feature":
        runs = [d for d in demos if d.run is not None or d.home == node.ref]
        return runs[0] if runs else _pick(demos)
    # A project's and a change's proof is its PRs' (an acceptance run is its feature's).
    return _pick([d for d in demos if d.run is None])


def _iso(seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(seconds)))


def duration_words(seconds: float | None) -> str | None:
    """``0:14`` from ``14.2`` seconds (``None`` when not recorded)."""
    if seconds is None or seconds < 0:
        return None
    whole = round(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


def _one_line(text: Any, limit: int = _NOTE_MAX) -> str:
    clean = " ".join(str(text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def _steps(demo: FactoryDemo) -> list[Mapping[str, Any]]:
    steps = (demo.manifest or {}).get("steps")
    return [s for s in steps if isinstance(s, Mapping)] if isinstance(steps, list) else []


def _step_words(step: Mapping[str, Any], index: int) -> tuple[str, str]:
    n = step.get("n")
    number = n if isinstance(n, int) and not isinstance(n, bool) else index
    return str(number), _one_line(step.get("note") or step.get("title") or "", _NOTE_MAX)


def partial_note(demo: FactoryDemo) -> str | None:
    """
    What was partial, in one line, for a demo that did not pass: its first
    failed step ("Step 6: live search fails on the base env"; the verdict beside
    it says it failed), else the steps it could not run, else its summary's
    first sentence.
    """
    if demo.status == "PASS":
        return None
    steps = list(enumerate(_steps(demo), start=1))
    failed = [(i, s) for i, s in steps if str(s.get("status") or "").lower() == "fail"]
    skipped = [(i, s) for i, s in steps if str(s.get("status") or "").lower() == "skipped"]
    if failed:
        number, words = _step_words(failed[0][1], failed[0][0])
        more = f" (+{len(failed) - 1} more)" if len(failed) > 1 else ""
        return _one_line(f"Step {number}{f': {words}' if words else ' failed'}{more}")
    if skipped:
        number, words = _step_words(skipped[0][1], skipped[0][0])
        if len(skipped) == 1:
            return _one_line(f"Step {number} could not run{f': {words}' if words else ''}")
        return _one_line(f"{len(skipped)} steps could not run, first: {words or f'step {number}'}")
    summary = " ".join(str(demo.summary or "").split())
    first = summary.split(". ")[0].rstrip(".") if summary else ""
    return _one_line(first) if first else None


def short_title(demo: Demo) -> str:
    """The demo's title in a few words; an acceptance run is "Acceptance run 2"."""
    if demo.run is not None:
        return f"Acceptance run {demo.run}"
    text = demo.row.title or demo.row.summary or ""
    words = node_names.fallback(text) if text.strip() else ""
    return words or (f"Demo of {demo.tag}" if demo.tag else "Demo")


def _file_url(demo: FactoryDemo, entry: Mapping[str, Any]) -> str | None:
    file_id = entry.get("file_id") or entry.get("id")
    if not file_id:
        return None
    return _FILE_ROUTE.format(demo=demo.id, file=file_id)


def _media(demo: FactoryDemo) -> tuple[str | None, str | None]:
    """``(poster url, video url)``: the poster, else the first step's screenshot."""
    files = [f for f in demo.files or [] if isinstance(f, Mapping)]
    video = next((f for f in files if f.get("kind") == "video"), None)
    poster = next((f for f in files if f.get("kind") == "poster"), None)
    if poster is None:
        shots = [f for f in files if f.get("kind") == "screenshot"]
        by_step = sorted(
            (f for f in shots if isinstance(f.get("step"), int)), key=lambda f: f["step"]
        )
        poster = by_step[0] if by_step else (shots[0] if shots else None)
    return (
        _file_url(demo, poster) if poster is not None else None,
        _file_url(demo, video) if video is not None else None,
    )


def _where(tree: Tree, demo: Demo) -> dict[str, Any]:
    """
    Where a demo sits, for the factory's Demos list: its feature and project
    (``{ref, title}``, short names) and, for a PR demo, whether the PR merged
    (``merged_at``, epoch seconds).
    """
    out: dict[str, Any] = {}
    home = tree.nodes.get(demo.home)
    chain = [*tree.ancestors(demo.home), *([home] if home is not None else [])]
    for level in ("feature", "project"):
        hit = next((n for n in reversed(chain) if n.level == level), None)
        if hit is not None:
            shown = tree.fold(hit)
            out[level] = {"ref": shown.ref, "title": shown.title}
    pr = tree.pr_rows.get(_bare(demo.row.pr_id) or "")
    if pr is not None and pr.state == "merged" and pr.merged_at:
        out["merged_at"] = int(pr.merged_at)
    return out


def view(demo: Demo, tree: Tree | None = None) -> dict[str, Any]:
    """
    A ``DemoRef``: one demo as a page row or a list line shows it.

    :param tree: When given, also where it sits (:func:`_where`), for the Demos list.
    """
    row = demo.row
    manifest = row.manifest or {}
    seconds = manifest.get("duration_s")
    seconds = float(seconds) if isinstance(seconds, int | float) else None
    poster, video = _media(row)
    verdict = row.status if row.status in VERDICTS else "ERROR"
    out: dict[str, Any] = {
        "id": row.id,
        "verdict": verdict,
        "title": short_title(demo),
        "ref": demo.home,
        "at": _iso(row.created_at),
    }
    full = " ".join(str(row.title or "").split())
    if full and full != out["title"]:
        out["full_title"] = _one_line(full, 256)
    if seconds is not None:
        out["duration_s"] = round(seconds, 1)
        out["duration"] = duration_words(seconds)
    if poster:
        out["poster_url"] = poster
    if video:
        out["video_url"] = video
    if row.pr_number:
        out["pr_number"] = row.pr_number
    if demo.tag:
        out["pr_tag"] = demo.tag
    if demo.run is not None:
        out["acceptance_run"] = demo.run
    if not demo.current:
        out["stale"] = True
    steps = [str(s.get("status") or "").lower() for s in _steps(row)]
    if steps:
        out["steps"] = {"passed": steps.count("pass"), "total": len(steps)}
    if note := partial_note(row):
        out["partial_note"] = note
    if tree is not None:
        out.update(_where(tree, demo))
    return out


def proof(tree: Tree, node: _N) -> dict[str, Any] | None:
    """``NodePage.proof``: the latest demo (where its level shows one) and the subtree's count."""
    demos = index(tree).of(node.ref)
    if not demos:
        return None
    out: dict[str, Any] = {"count": len(demos)}
    if (shown := latest(tree, node)) is not None:
        out["latest"] = view(shown)
    return out


def _feature_row(tree: Tree, node: _N) -> bool:
    """A feature, or a node shown in a feature's place (its implicit feature folds into it)."""
    if node.level in CHIP_LEVELS:
        return True
    return any(a.level in CHIP_LEVELS and tree.folded(a) for a in tree.ancestors(node.ref))


def chip(tree: Tree, node: _N) -> dict[str, Any] | None:
    """``NodeSummary.demo``: a feature row's "▶" (its latest demo, in a few fields)."""
    if not _feature_row(tree, node) or (shown := latest(tree, node)) is None:
        return None
    found = view(shown)
    out: dict[str, Any] = {"id": found["id"], "verdict": found["verdict"]}
    if found.get("duration"):
        out["duration"] = found["duration"]
    return out


def demos_page(
    tree: Tree, node: _N, *, cursor: str | None = None, limit: int = PAGE_SIZE
) -> dict[str, Any]:
    """
    ``GET …/n/{ref}/demos``: the subtree's demos, newest first, *limit* a page.

    :param cursor: ``next`` of a previous page.
    :raises ValueError: On a cursor no page handed out.
    """
    demos = index(tree).of(node.ref)
    offset = 0
    if cursor:
        if not cursor.isdigit() or int(cursor) > len(demos):
            raise ValueError(f"unknown cursor {cursor!r}")
        offset = int(cursor)
    size = max(1, min(limit, PAGE_MAX))
    shown = demos[offset : offset + size]
    out: dict[str, Any] = {
        "ref": node.ref,
        "items": [view(d, tree) for d in shown],
        "total": len(demos),
    }
    if (shown_latest := latest(tree, node)) is not None:
        out["latest"] = shown_latest.row.id
    if offset + size < len(demos):
        out["next"] = str(offset + size)
    return out


__all__ = [
    "CHIP_LEVELS",
    "LATEST_LEVELS",
    "PAGE_MAX",
    "PAGE_SIZE",
    "READ_MAX",
    "Demo",
    "Index",
    "chip",
    "demos_page",
    "duration_words",
    "index",
    "latest",
    "partial_note",
    "proof",
    "readable_demos",
    "short_title",
    "view",
]
