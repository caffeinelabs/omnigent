"""The base watcher: a factory's base branch moved on GitHub.

The PR poller's batched GraphQL query also reads ``refs/heads/<base>`` of
every ``(repo, base)`` an active factory watches: each project's base and the
factory's default base. :func:`observe` compares that head with the one
recorded on the factory root (label ``omnigent.factory.base_head.<key>``).
The first sighting is only recorded; a change emits ``base.moved``, mails the
integration PL to redeploy its live env and every work PL with an open PR on
that base to sync, and runs the :func:`on_base_moved` hooks. Merges nobody
registered with the factory are caught the same way.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from omnigent.entities import Conversation
from omnigent.entities.factory import FactoryProject
from omnigent.factory import (
    LABEL_BASE_HEAD_PREFIX,
    LABEL_DEFAULT_BASE,
    LABEL_DEFAULT_REPO,
    LABEL_VALUE_MAX_LEN,
    PROJECT_KIND_INTEGRATION,
    STAGE_DONE,
    is_full_sha,
)
from omnigent.server.factory import events
from omnigent.server.factory.charter import project_base_branch, project_kind
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.mailer import get_mailer
from omnigent.server.factory.prs import FactoryScope, project_lead_id

_logger = logging.getLogger(__name__)

SYNC_NOTICE_MIN_GAP_S: Final = 1800
"""Seconds between two sync notices to one work PL for one base (busy bases move often)."""

BaseKey = tuple[str, str]
"""``(repo, base)``, e.g. ``("caffeinelabs/app", "temp-main")``."""


@dataclass(frozen=True)
class BaseHead:
    """
    A base head recorded on the factory root.

    :param sha: The head commit.
    :param moved_at: Epoch seconds the server saw it move there; ``None`` for
        the first sighting.
    """

    sha: str
    moved_at: int | None = None


@dataclass(frozen=True)
class BaseMove:
    """
    One move of a watched base, as :func:`on_base_moved` hooks receive it.

    :param pr: The tracked PR whose merge commit is the new head, if any.
    """

    factory_id: str
    repo: str
    base: str
    from_sha: str
    to_sha: str
    at: int
    pr: int | None = None


BaseMovedHook = Callable[[Any, BaseMove], Awaitable[None] | None]
"""Called with ``(app_state, move)`` after ``base.moved`` is recorded."""

_hooks: list[BaseMovedHook] = []
_sync_noticed: dict[tuple[str, str, str], float] = {}


def on_base_moved(hook: BaseMovedHook) -> Callable[[], None]:
    """
    Run *hook* after every recorded base move (e.g. to release held projects).

    Hook errors are logged and swallowed.

    :returns: A callable that removes the hook.
    """
    _hooks.append(hook)

    def _remove() -> None:
        if hook in _hooks:
            _hooks.remove(hook)

    return _remove


def reset() -> None:
    """Forget hooks and sync-notice times (tests)."""
    _hooks.clear()
    _sync_noticed.clear()


# ── Recorded heads ─────────────────────────────────────────────


def head_label(repo: str, base: str) -> str:
    """:returns: The root label recording the head of ``repo#base``."""
    digest = hashlib.sha256(f"{repo.lower()}#{base}".encode()).hexdigest()[:12]
    return f"{LABEL_BASE_HEAD_PREFIX}{digest}"


def _pair_tags(repo: str, base: str) -> tuple[str, str]:
    """``(repo#base, sha256:<hex>)``: the pair a head value names, plain or hashed."""
    plain = f"{repo.lower()}#{base.lower()}"
    return plain, f"sha256:{hashlib.sha256(plain.encode()).hexdigest()}"


def read_head(labels: Mapping[str, str], repo: str, base: str) -> BaseHead | None:
    """
    The head of ``repo#base`` recorded in *labels*.

    :returns: ``None`` when nothing (or something unreadable) is recorded.
    """
    raw = labels.get(head_label(repo, base))
    parts = raw.split(" ", 2) if raw else []
    if len(parts) != 3 or not is_full_sha(parts[0]):
        return None
    if parts[2].lower() not in _pair_tags(repo, base):
        return None
    try:
        moved_at = int(parts[1])
    except ValueError:
        return None
    return BaseHead(parts[0], moved_at or None)


def head_value(head: BaseHead, repo: str, base: str) -> str:
    """:returns: The label value recording *head* (the pair hashed when too long)."""
    value = f"{head.sha} {head.moved_at or 0} {repo}#{base}"
    if len(value) <= LABEL_VALUE_MAX_LEN:
        return value
    return f"{head.sha} {head.moved_at or 0} {_pair_tags(repo, base)[1]}"


def default_base(root: Conversation) -> BaseKey | None:
    """:returns: The factory's default ``(repo, base)``, when it has one."""
    repo = root.labels.get(LABEL_DEFAULT_REPO)
    base = root.labels.get(LABEL_DEFAULT_BASE)
    return (repo, base) if repo and base else None


def integration_projects(projects: Iterable[FactoryProject]) -> list[FactoryProject]:
    """:returns: The integration (live base env) projects, oldest slug order kept."""
    return [p for p in projects if project_kind(p) == PROJECT_KIND_INTEGRATION]


def watched_bases(scope: FactoryScope) -> list[BaseKey]:
    """
    Every ``(repo, base)`` a factory watches: its default and each project's base.

    :returns: Distinct pairs (repo compared case-insensitively), default first.
    """
    pairs: dict[tuple[str, str], BaseKey] = {}
    default = default_base(scope.root)
    candidates = [default] if default else []
    candidates += [(p.repo, project_base_branch(p)) for p in scope.projects.values() if p.repo]
    for repo, base in candidates:
        pairs.setdefault((repo.lower(), base), (repo, base))
    return list(pairs.values())


def bases_by_repo(scopes: Iterable[FactoryScope]) -> dict[str, list[str]]:
    """:returns: Lowercased repo -> the sorted bases any of *scopes* watches there."""
    out: dict[str, set[str]] = {}
    for scope in scopes:
        for repo, base in watched_bases(scope):
            out.setdefault(repo.lower(), set()).add(base)
    return {repo: sorted(bases) for repo, bases in out.items()}


# ── Observing heads ────────────────────────────────────────────


def sync_notice(base: str, sha: str) -> str:
    """``[Factory notice · base temp-main moved to <sha7> · sync open branches: …]``."""
    return (
        f"[Factory notice · base {base} moved to {sha[:7]} · sync open branches: "
        f"git merge origin/{base}] Have each open PR's author merge origin/{base} into its "
        "branch and push; skip a branch already up to date."
    )


def redeploy_notice(repo: str, base: str, before: str, after: str) -> str:
    """``[Factory notice · base temp-main moved <old7> → <new7>]`` for the integration PL."""
    return (
        f"[Factory notice · base {base} moved {before[:7]} → {after[:7]}] Redeploy your "
        f"live env of {repo} at the new head (KIND: integration)."
    )


async def observe(
    app_state: Any,
    scopes: Sequence[FactoryScope],
    repo: str,
    heads: Mapping[str, str | None],
    *,
    now: int,
) -> list[BaseMove]:
    """
    Record the heads read for *repo* and act on every factory whose base moved.

    :param scopes: The active factories of the owner whose token read *heads*.
    :param repo: ``owner/name`` the heads belong to.
    :param heads: Base branch -> head oid (``None`` when the branch is missing).
    :param now: Epoch seconds.
    :returns: The moves recorded.
    """
    moves: list[BaseMove] = []
    for scope in scopes:
        for watched_repo, base in watched_bases(scope):
            oid = heads.get(base)
            if watched_repo.lower() != repo.lower() or not is_full_sha(oid):
                continue
            assert isinstance(oid, str)
            try:
                move = await _observe_one(app_state, scope, watched_repo, base, oid, now)
            except Exception:  # noqa: BLE001 - one factory must not stop the others
                _logger.warning(
                    "factory: base %s#%s of %s not recorded",
                    repo,
                    base,
                    scope.factory_id,
                    exc_info=True,
                )
                continue
            if move is not None:
                moves.append(move)
    return moves


async def _observe_one(
    app_state: Any, scope: FactoryScope, repo: str, base: str, oid: str, now: int
) -> BaseMove | None:
    deps = factory_deps(app_state)
    root = await asyncio.to_thread(deps.conversation_store.get_conversation, scope.factory_id)
    labels = root.labels if root is not None else scope.root.labels
    known = read_head(labels, repo, base)
    if known is not None and known.sha == oid:
        return None
    key = head_label(repo, base)
    if known is None:
        # The first sighting is the baseline, not a move.
        first = {key: head_value(BaseHead(oid), repo, base)}
        await asyncio.to_thread(deps.conversation_store.set_labels, scope.factory_id, first)
        return None
    # Read before recording: a failed read leaves the move for the next poll.
    pr = await asyncio.to_thread(_merged_pr, deps, scope.factory_id, repo, oid)
    move = BaseMove(scope.factory_id, repo, base, known.sha, oid, now, pr)
    notices = await asyncio.to_thread(_notices, deps, scope, move)
    record = {key: head_value(BaseHead(oid, now), repo, base)}
    await asyncio.to_thread(deps.conversation_store.set_labels, scope.factory_id, record)
    mailer = get_mailer(app_state)
    for target, text, throttle in notices:
        if throttle is not None:
            _sync_noticed[throttle] = move.at
        try:
            mailer.notify(target, text)
        except RuntimeError:
            _logger.warning("factory: mailer closed; dropped base notice for %s", target)
    via = f" (#{pr})" if pr else ""
    await events.emit(
        app_state,
        scope.factory_id,
        "base.moved",
        severity="notice",
        subject=f"base:{repo}#{base}",
        summary=f"base {base} moved {known.sha[:7]} → {oid[:7]}{via}",
        data={
            "repo": repo,
            "base": base,
            "from": known.sha,
            "to": oid,
            "pr": pr,
            "notified": [target for target, _, _ in notices],
        },
    )
    for hook in list(_hooks):
        try:
            result = hook(app_state, move)
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - a hook bug must not stop the watcher
            _logger.warning("factory: base-moved hook failed", exc_info=True)
    return move


def _merged_pr(deps: Any, factory_id: str, repo: str, sha: str) -> int | None:
    """The tracked PR whose merge commit is *sha*, if any."""
    for pr in deps.factory_store.list_prs(factory_id, ["merged"]):
        if pr.repo.lower() == repo.lower() and pr.merged_sha == sha:
            return pr.number
    return None


_Notice = tuple[str, str, tuple[str, str, str] | None]
"""``(PL session, text, sync-throttle key or None)``."""


def _notices(deps: Any, scope: FactoryScope, move: BaseMove) -> list[_Notice]:
    """A notice for every PL of a live project on the moved base (nothing is recorded)."""
    open_prs = deps.factory_store.list_prs(scope.factory_id, ["open"])
    out: list[_Notice] = []
    for project in scope.projects.values():
        if not project.repo or project.repo.lower() != move.repo.lower():
            continue
        if project_base_branch(project) != move.base or project.stage == STAGE_DONE:
            continue
        pl = project_lead_id(deps, scope.factory_id, project)
        if pl is None:
            continue
        if project_kind(project) == PROJECT_KIND_INTEGRATION:
            text = redeploy_notice(move.repo, move.base, move.from_sha, move.to_sha)
            out.append((pl, text, None))
            continue
        if not any(
            p.project_slug == project.slug and (p.base_ref or move.base) == move.base
            for p in open_prs
        ):
            continue
        key = (pl, move.repo.lower(), move.base)
        last = _sync_noticed.get(key)
        if last is not None and move.at - last < SYNC_NOTICE_MIN_GAP_S:
            continue
        out.append((pl, sync_notice(move.base, move.to_sha), key))
    return out


def base_view(
    root: Conversation,
    projects: Sequence[FactoryProject],
    envs: Mapping[str, Mapping[str, Any] | None],
) -> dict[str, Any] | None:
    """
    The snapshot's ``factory.base``: the default base, its head and the live env at it.

    :param projects: The factory's project rows.
    :param envs: Project slug -> its snapshot ``env`` (an ``EnvView`` or ``None``).
    :returns: ``FactoryBaseView`` JSON, or ``None`` without a default base or
        an integration project.
    """
    integration = next(iter(integration_projects(projects)), None)
    key = default_base(root)
    if key is None and integration is not None and integration.repo:
        key = (integration.repo, project_base_branch(integration))
    if key is None:
        return None
    repo, branch = key
    head = read_head(root.labels, repo, branch)
    return {
        "repo": repo,
        "branch": branch,
        "head_sha": head.sha if head else None,
        "moved_at": head.moved_at if head else None,
        "env": envs.get(integration.slug) if integration is not None else None,
        "env_project": integration.slug if integration is not None else None,
    }


__all__ = [
    "SYNC_NOTICE_MIN_GAP_S",
    "BaseHead",
    "BaseMove",
    "BaseMovedHook",
    "base_view",
    "bases_by_repo",
    "default_base",
    "head_label",
    "head_value",
    "integration_projects",
    "observe",
    "on_base_moved",
    "read_head",
    "redeploy_notice",
    "reset",
    "sync_notice",
    "watched_bases",
]
