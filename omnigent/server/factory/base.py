"""The base watcher: a factory's base branch moved on GitHub.

The PR poller's batched GraphQL query also reads ``refs/heads/<base>`` of
every ``(repo, base)`` an active factory watches: each project's base and the
factory's default base. :func:`observe` compares that head with the one
recorded on the factory root (label ``omnigent.factory.base_head.<key>``).
The first sighting is only recorded; a change emits ``base.moved``, mails the
integration PL to redeploy its live env and every work PL with an open PR on
that base to sync, and runs the :func:`on_base_moved` hooks. Merges nobody
registered with the factory are caught the same way.

The base env's deployed commit is a stored fact too (label
``omnigent.factory.base_env.<key>``, :class:`DeployedHead`): when the
integration project's env reports ready at a sha, the watcher places that sha
in the base's history (when it saw it become the head). :func:`contains` then
answers "is this merge in the base env" by ancestry: a merge into a linear base
is in every head the watcher saw after it landed, and in no head before.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from omnigent.entities import Conversation
from omnigent.entities.factory import FactoryProject
from omnigent.factory import (
    LABEL_BASE_ENV_PREFIX,
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
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.mailer import get_mailer
from omnigent.server.factory.prs import FactoryScope, project_lead_id

_logger = logging.getLogger(__name__)

SYNC_NOTICE_MIN_GAP_S: Final = 1800
"""Seconds between two sync notices to one work PL for one base (busy bases move often)."""
MOVES_LOOKBACK_S: Final = 14 * 24 * 3600
"""How far back the watcher's ``base.moved`` history is read to place a deployed sha."""
_SHA_RE: Final = re.compile(r"^[0-9a-f]{7,40}$")
_SHA_MIN: Final = 7

BaseKey = tuple[str, str]
"""``(repo, base)``, e.g. ``("sample-org/sample-app", "develop")``."""


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
    :param from_at: When the server saw the previous head (``None`` when unknown);
        PRs merged since then are in this move too.
    """

    factory_id: str
    repo: str
    base: str
    from_sha: str
    to_sha: str
    at: int
    pr: int | None = None
    from_at: int | None = None


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


# ── The base env's deployed commit ─────────────────────────────


@dataclass(frozen=True)
class DeployedHead:
    """
    What the base env runs, placed in its base's history.

    :param sha: The deployed commit as the env reported it (7-40 hex), widened
        to the full head sha once the watcher saw that head.
    :param at: When the env first reported it ready at this commit.
    :param head_at: When the watcher saw this commit become the base head (an
        observed move); ``None`` when it never did (a first sighting, or the
        base moved twice between two polls).
    """

    sha: str
    at: int
    head_at: int | None = None

    def to_json(self) -> dict[str, Any]:
        """:returns: ``FactoryBaseView.deployed``."""
        return {"sha": self.sha, "at": self.at, "head_at": self.head_at}


def deployed_label(repo: str, base: str) -> str:
    """:returns: The root label recording what the base env of ``repo#base`` runs."""
    digest = hashlib.sha256(f"{repo.lower()}#{base}".encode()).hexdigest()[:12]
    return f"{LABEL_BASE_ENV_PREFIX}{digest}"


def _short_sha(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text if _SHA_RE.fullmatch(text) else None


def same_commit(a: str | None, b: str | None) -> bool:
    """:returns: Whether two shas (either may be short, at least 7 hex) name one commit."""
    if not a or not b or len(a) < _SHA_MIN or len(b) < _SHA_MIN:
        return False
    a, b = a.lower(), b.lower()
    return a.startswith(b) or b.startswith(a)


def read_deployed(labels: Mapping[str, str], repo: str, base: str) -> DeployedHead | None:
    """:returns: What the base env of ``repo#base`` runs, as recorded, else ``None``."""
    raw = labels.get(deployed_label(repo, base))
    parts = raw.split(" ", 3) if raw else []
    if len(parts) != 4 or _short_sha(parts[0]) is None:
        return None
    if parts[3].lower() not in _pair_tags(repo, base):
        return None
    try:
        head_at, at = int(parts[1]), int(parts[2])
    except ValueError:
        return None
    return DeployedHead(parts[0].lower(), at, head_at or None)


def deployed_value(deployed: DeployedHead, repo: str, base: str) -> str:
    """:returns: The label value recording *deployed* (the pair hashed when too long)."""
    head = f"{deployed.sha} {deployed.head_at or 0} {deployed.at}"
    value = f"{head} {repo}#{base}"
    return value if len(value) <= LABEL_VALUE_MAX_LEN else f"{head} {_pair_tags(repo, base)[1]}"


def place(
    sha: str, head: BaseHead | None, moves: Sequence[tuple[str, int]]
) -> tuple[str, int | None]:
    """
    Where *sha* sits in the base's history the watcher saw.

    :param head: The head recorded now.
    :param moves: ``(to sha, at)`` of the base's recorded moves.
    :returns: ``(the full sha when known, when it became the head)``; the time is
        ``None`` when the watcher never saw it move there.
    """
    found = [(to, at) for to, at in moves if same_commit(to, sha)]
    if found:
        to, at = min(found, key=lambda pair: pair[1])
        return to, at
    if head is not None and same_commit(head.sha, sha):
        return head.sha, head.moved_at
    return sha, None


def contains(
    deployed: DeployedHead | None, *, merged_sha: str | None, merged_at: int | None
) -> bool:
    """
    Whether the base env runs a merge into its base: the deployed commit is
    the merge, or the watcher saw the deployed commit become the head after
    the merge landed (a linear base holds every earlier merge). A deployed
    commit the watcher never saw become the head is placed on its next move
    or report; until then only the merge itself counts (when the env first
    reported says nothing about which merges its build had).
    """
    if deployed is None or not merged_at:
        return False
    if same_commit(deployed.sha, merged_sha):
        return True
    if deployed.head_at is None:
        return False
    return int(merged_at) <= int(deployed.head_at)


def _base_moves(
    deps: FactoryDeps, factory_id: str, repo: str, base: str, now: int
) -> list[tuple[str, int]]:
    rows = deps.factory_store.find_events(
        factory_id, "base.moved", (now - MOVES_LOOKBACK_S) * 1000, newest_first=True, limit=500
    )
    out: list[tuple[str, int]] = []
    for row in rows:
        data = row.data or {}
        if str(data.get("repo") or "").lower() != repo.lower() or data.get("base") != base:
            continue
        to = _short_sha(data.get("to"))
        at = data.get("at")
        if to is not None:
            out.append((to, int(at) if isinstance(at, int | float) else int(row.ts // 1000)))
    return out


def record_deployed(
    deps: FactoryDeps, factory_id: str, repo: str, base: str, sha: str, now: int
) -> DeployedHead | None:
    """
    Record that the base env of ``repo#base`` runs *sha* (synchronous).

    A sha already recorded keeps its first report time; its place in the
    history is filled in when it was missing.

    :returns: What is recorded now, or ``None`` for a sha that is not a commit.
    """
    short = _short_sha(sha)
    root = deps.conversation_store.get_conversation(factory_id)
    if short is None or root is None:
        return None
    known = read_deployed(root.labels, repo, base)
    if known is not None and same_commit(known.sha, short) and known.head_at:
        return known
    full, head_at = place(short, read_head(root.labels, repo, base), [])
    if head_at is None:
        full, head_at = place(short, None, _base_moves(deps, factory_id, repo, base, now))
    at = known.at if known is not None and same_commit(known.sha, short) else now
    record = DeployedHead(full, at, head_at)
    if record != known:
        value = deployed_value(record, repo, base)
        deps.conversation_store.set_labels(factory_id, {deployed_label(repo, base): value})
    return record


def _placed_on_move(
    labels: Mapping[str, str], repo: str, base: str, oid: str, at: int
) -> dict[str, str]:
    """The deployed record's new value when *oid* is the commit the env already runs."""
    known = read_deployed(labels, repo, base)
    if known is None or known.head_at or not same_commit(known.sha, oid):
        return {}
    placed = DeployedHead(oid, known.at, at)
    return {deployed_label(repo, base): deployed_value(placed, repo, base)}


async def on_event(app_state: Any, event: Any) -> None:
    """The base env reported ready at a commit: record it as the deployed head."""
    if event.kind != "env.reported" or not event.project_slug:
        return
    data = event.data or {}
    sha = data.get("sha")
    if data.get("state") != "ready" or not isinstance(sha, str) or not sha:
        return
    deps = factory_deps(app_state)

    def run() -> None:
        project = deps.factory_store.get_project(event.factory_id, event.project_slug)
        if project is None or project_kind(project) != PROJECT_KIND_INTEGRATION:
            return
        if not project.repo:
            return
        record_deployed(
            deps,
            event.factory_id,
            project.repo,
            project_base_branch(project),
            sha,
            int(event.ts // 1000),
        )

    try:
        await asyncio.to_thread(run)
    except Exception:  # noqa: BLE001 - the next report records it
        _logger.warning(
            "factory: base env sha not recorded for %s", event.factory_id, exc_info=True
        )


def register() -> None:
    """Record the base env's deployed commit as env reports arrive (idempotent)."""
    if on_event not in events._listeners:
        events.add_listener(on_event)


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
    """``[Factory notice · base develop moved to <sha7> · sync open branches: …]``."""
    return (
        f"[Factory notice · base {base} moved to {sha[:7]} · sync open branches: "
        f"git merge origin/{base}] Have each open PR's author merge origin/{base} into its "
        "branch and push; skip a branch already up to date."
    )


def redeploy_notice(repo: str, base: str, before: str, after: str) -> str:
    """``[Factory notice · base develop moved <old7> → <new7>]`` for the integration PL."""
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
    move = BaseMove(scope.factory_id, repo, base, known.sha, oid, now, pr, known.moved_at)
    notices = await asyncio.to_thread(_notices, deps, scope, move)
    record = {key: head_value(BaseHead(oid, now), repo, base)}
    # The env may already run this commit: it now has a place in the history.
    record.update(_placed_on_move(labels, repo, base, oid, now))
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
            "at": now,
            "pr": pr,
            "notified": [target for target, _, _ in notices],
            # The Room names the repo in its base line when there is more than one.
            "repos_watched": len({r.lower() for r, _ in watched_bases(scope)}),
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
        if project.status != "active":
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
    deployed = read_deployed(root.labels, repo, branch) if integration is not None else None
    return {
        "repo": repo,
        "branch": branch,
        "head_sha": head.sha if head else None,
        "moved_at": head.moved_at if head else None,
        "env": envs.get(integration.slug) if integration is not None else None,
        "env_project": integration.slug if integration is not None else None,
        "deployed": deployed.to_json() if deployed is not None else None,
    }


register()


__all__ = [
    "MOVES_LOOKBACK_S",
    "SYNC_NOTICE_MIN_GAP_S",
    "BaseHead",
    "BaseMove",
    "BaseMovedHook",
    "DeployedHead",
    "base_view",
    "bases_by_repo",
    "contains",
    "default_base",
    "deployed_label",
    "deployed_value",
    "head_label",
    "head_value",
    "integration_projects",
    "observe",
    "on_base_moved",
    "on_event",
    "place",
    "read_deployed",
    "read_head",
    "record_deployed",
    "redeploy_notice",
    "register",
    "reset",
    "same_commit",
    "sync_notice",
    "watched_bases",
]
