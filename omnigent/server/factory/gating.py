"""Project dependencies: hold a project until what it ``depends_on`` is met.

A project with unmet dependencies stays queued with the overlay
"waiting on …" and emits ``project.held``. When a dependency reaches the
project's ``depends_mode`` (``merged``: its PR merged into the base, the
default; ``ready``: ready for human merge), ``project.released`` fires and the
server spawns its PL. Release runs on ``stage.changed``, ``pr.merged`` and
``pr.ready`` events, on ``base.moved`` (the base watcher's hook) and on the
minute sweep. The checks here (dependencies, cycles, scope overlap) are pure.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

from omnigent.entities import Conversation
from omnigent.entities.factory import FactoryEvent, FactoryPr, FactoryProject
from omnigent.factory import STAGE_DONE, STAGE_MERGED, STAGE_READY
from omnigent.server.factory import events
from omnigent.server.factory.charter import (
    DEPENDS_READY,
    SPAWN_SERVER,
    project_base_branch,
    project_depends_on,
    spawn_owner,
)
from omnigent.server.factory.context import factory_deps

_logger = logging.getLogger(__name__)

RELEASE_EVENTS: Final = frozenset({"stage.changed", "pr.merged", "pr.ready"})
"""Events after which a held project may be released."""
_HOLD_EVENTS: Final = ("project.held", "project.released")
_GLOB_CHARS: Final = re.compile(r"[*?\[{]")

_releasing: set[str] = set()


# ── Pure checks ────────────────────────────────────────────────


def resolve_alias(slug: str, projects: Mapping[str, FactoryProject]) -> FactoryProject | None:
    """
    The project that now carries *slug*'s work: itself, or the project it was merged into.

    :returns: The row, or ``None`` for an unknown slug.
    """
    seen: set[str] = set()
    row = projects.get(slug)
    while row is not None and row.status == "merged" and row.parent_slug:
        if row.slug in seen:
            break
        seen.add(row.slug)
        row = projects.get(row.parent_slug)
    return row


def dependency_met(project: FactoryProject, prs: Sequence[FactoryPr], mode: str) -> bool:
    """
    Whether *project* satisfies a dependent's *mode*.

    ``merged``: its PRs are merged and none is open, or it is merged or done.
    ``ready``: that, or one of its open PRs is ready for human merge.
    """
    if project.status == "done":
        return True
    if project.status == "cancelled":
        return False
    own = [p for p in prs if p.project_slug == project.slug]
    open_prs = [p for p in own if p.state == "open"]
    merged = any(p.state == "merged" for p in own) and not open_prs
    if merged or project.stage in (STAGE_MERGED, STAGE_DONE):
        return True
    if mode == DEPENDS_READY:
        return any(p.ready for p in open_prs) or project.stage == STAGE_READY
    return False


def unmet_dependencies(
    project: FactoryProject,
    projects: Mapping[str, FactoryProject],
    prs: Sequence[FactoryPr],
) -> list[str]:
    """
    The slugs *project* still waits for, in charter order.

    :param projects: Every project of the factory by slug.
    :param prs: The factory's PRs.
    """
    depends_on, mode = project_depends_on(project)
    out: list[str] = []
    for slug in depends_on:
        target = resolve_alias(slug, projects)
        if target is None or not dependency_met(target, prs, mode):
            out.append(slug)
    return out


def find_cycle(graph: Mapping[str, Iterable[str]]) -> list[str] | None:
    """
    A dependency cycle in *graph*, if any.

    :param graph: Slug -> the slugs it depends on (unknown slugs are ignored).
    :returns: The cycle as a path that ends where it starts, e.g. ``["a", "b", "a"]``.
    """
    edges = {node: [d for d in deps if d in graph] for node, deps in graph.items()}
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for dep in edges[node]:
            if state.get(dep) == 1:
                return [*stack[stack.index(dep) :], dep]
            if dep not in state and (found := visit(dep)) is not None:
                return found
        stack.pop()
        state[node] = 2
        return None

    for node in sorted(edges):
        if node not in state and (found := visit(node)) is not None:
            return found
    return None


def _glob_prefix(pattern: str) -> tuple[str, ...]:
    """The literal leading path segments of a glob, e.g. ``a/b/**`` -> ``("a", "b")``."""
    parts: list[str] = []
    for part in pattern.strip().strip("/").split("/"):
        if not part or _GLOB_CHARS.search(part):
            break
        parts.append(part)
    return tuple(parts)


def globs_overlap(a: str, b: str) -> bool:
    """Whether two scope globs may match a common file (one literal prefix contains the other)."""
    pa, pb = _glob_prefix(a), _glob_prefix(b)
    short = min(len(pa), len(pb))
    return pa[:short] == pb[:short]


def scope_overlaps(
    slug: str,
    scope: Sequence[str],
    others: Iterable[tuple[str, Sequence[str]]],
) -> list[str]:
    """
    Warnings for *slug*'s scope globs that overlap another project's on the same base.

    :param others: ``(slug, scope_paths)`` of the other projects on the same ``repo#base``.
    :returns: e.g. ``["scope_overlap: api-version services/api/** ~ db-version services/**"]``.
    """
    out: list[str] = []
    for other, other_scope in others:
        if other == slug:
            continue
        for mine in scope:
            hit = next((theirs for theirs in other_scope if globs_overlap(mine, theirs)), None)
            if hit is not None:
                out.append(f"scope_overlap: {slug} {mine} ~ {other} {hit}")
                break
    return out


def repo_base(project: FactoryProject) -> str:
    """:returns: ``owner/repo#base`` (lowercased repo), the key scope overlap compares within."""
    return f"{(project.repo or '').lower()}#{project_base_branch(project)}"


# ── Hold and release ───────────────────────────────────────────


def project_subject(slug: str) -> str:
    """:returns: The event subject of a project, e.g. ``"project:db-version"``."""
    return f"project:{slug}"


def _is_held(deps: Any, factory_id: str, slug: str) -> bool:
    """Whether the newest hold event of *slug* is a ``project.held``."""
    rows = deps.factory_store.find_events(
        factory_id, list(_HOLD_EVENTS), 0, project_subject(slug), limit=1000
    )
    return bool(rows) and rows[-1].kind == "project.held"


async def hold(
    app_state: Any,
    root: Conversation,
    project: FactoryProject,
    waiting_on: Sequence[str],
    *,
    data: Mapping[str, Any] | None = None,
) -> None:
    """Record that *project* waits for *waiting_on* (once per hold)."""
    deps = factory_deps(app_state)
    if await asyncio.to_thread(_is_held, deps, root.id, project.slug):
        return
    _, mode = project_depends_on(project)
    await events.emit(
        app_state,
        root.id,
        "project.held",
        severity="notice",
        project=project.slug,
        subject=project_subject(project.slug),
        summary=f"{project.slug} waits on {', '.join(waiting_on)} ({mode})",
        data={"waiting_on": list(waiting_on), "depends_mode": mode, **(data or {})},
    )


def _candidates(deps: Any, factory_id: str) -> list[tuple[FactoryProject, list[str]]]:
    """
    Server-owned active projects whose PL the server still has to start or finish.

    A project whose PL was spawned and later closed is left to recovery.

    :returns: ``(project, its unmet dependencies)`` pairs.
    """
    from omnigent.server.factory import spawn
    from omnigent.server.factory.control import find_project_pl

    store = deps.factory_store
    rows = store.list_projects(factory_id)
    by_slug = {p.slug: p for p in rows}
    prs = store.list_prs(factory_id)
    out: list[tuple[FactoryProject, list[str]]] = []
    for project in rows:
        if project.status != "active" or spawn_owner(project) != SPAWN_SERVER:
            continue
        pl = find_project_pl(deps, factory_id, project.slug, include_done=True)
        if pl is None and project.pl_session_id:
            continue
        if pl is not None:
            if not spawn.needs_finishing(deps, factory_id, pl):
                continue
            out.append((project, []))
            continue
        out.append((project, unmet_dependencies(project, by_slug, prs)))
    return out


async def release_ready(app_state: Any, factory_id: str, *, trigger: str) -> list[str]:
    """
    Spawn the PLs of held projects whose dependencies are now met.

    Server-owned projects without a PL and without a hold are (re)started too,
    which finishes a spawn interrupted by a restart. One pass per factory at a time.

    :param trigger: What prompted the pass, e.g. ``"pr.merged"`` or ``"sweep"``.
    :returns: The slugs released or started.
    """
    from omnigent.server.factory import spawn

    if factory_id in _releasing:
        return []
    _releasing.add(factory_id)
    try:
        deps = factory_deps(app_state)
        root = await asyncio.to_thread(deps.conversation_store.get_conversation, factory_id)
        if root is None:
            return []
        candidates = await asyncio.to_thread(_candidates, deps, root.id)
        started: list[str] = []
        for project, unmet in candidates:
            if spawn.in_flight(root.id, project.slug):
                continue
            if unmet:
                await hold(app_state, root, project, unmet)
                continue
            if await asyncio.to_thread(_is_held, deps, root.id, project.slug):
                await events.emit(
                    app_state,
                    root.id,
                    "project.released",
                    severity="notice",
                    project=project.slug,
                    subject=project_subject(project.slug),
                    summary=f"{project.slug} released: its dependencies are met ({trigger})",
                    data={"trigger": trigger},
                )
            await spawn.launch(app_state, root, project, trigger=trigger)
            started.append(project.slug)
        return started
    finally:
        _releasing.discard(factory_id)


async def _on_event(app_state: Any, event: FactoryEvent) -> None:
    """Re-check held projects after a stage change, a merge or a ready PR."""
    if event.kind in RELEASE_EVENTS:
        await release_ready(app_state, event.factory_id, trigger=event.kind)


async def _on_base_moved(app_state: Any, move: Any) -> None:
    await release_ready(app_state, move.factory_id, trigger="base.moved")


def register() -> None:
    """Listen for the events and base moves that may release a held project (idempotent)."""
    from omnigent.server.factory import base

    if _on_event not in events._listeners:
        events.add_listener(_on_event)
    if _on_base_moved not in base._hooks:
        base.on_base_moved(_on_base_moved)


register()


__all__ = [
    "RELEASE_EVENTS",
    "dependency_met",
    "find_cycle",
    "globs_overlap",
    "hold",
    "project_subject",
    "register",
    "release_ready",
    "repo_base",
    "resolve_alias",
    "scope_overlaps",
    "unmet_dependencies",
]
