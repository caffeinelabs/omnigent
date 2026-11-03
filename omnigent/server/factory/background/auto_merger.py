"""The server's auto-merger: merges ready factory PRs into owner-set targets.

Every ``OMNIGENT_FACTORY_AUTO_MERGE_S`` seconds (default 60; ``0`` turns it
off), and at once on ``nudge("auto_merge")`` (the PR poller nudges it when a
PR into a target turns ready or its head or gates change), for every active
factory that is not paused and whose product profile names targets
(``product.delivery.auto_merge``):

1. pick the stored PRs :func:`.auto_merge.local_refusal` accepts (open, into
   a target, from a factory branch of an active project, ready on their head,
   not a draft) whose project lead is not paused;
2. read each from GitHub (:func:`.auto_merge.merge_query`) and merge only when
   :func:`.auto_merge.github_refusal` finds nothing: same head in the same
   repository, open, not a draft, ``MERGEABLE``, no change request, nothing
   changed since the poller's last read, trusted review and verify markers,
   every check on the head green;
3. re-read the stored row, the targets and the brakes, then squash-merge it
   with the stored head as the expected head, using the factory owner's
   GitHub connection (the PR poller's), record ``pr.auto_merged`` (the Room
   shows it in the PR's and the project's threads), clear the PR's incidents
   and refresh the PR at once so the usual merged flow runs.

Nothing waits silently. A GitHub error backs the PR off (5 min, doubling up
to an hour) and raises ``pr.auto_merge_failed`` on ``pr:<id>``; a ready PR
that does not merge for a reason a person must fix (a failing check, a fork's
head, an untrusted marker, GitHub not connected, …), or for any reason for
:data:`WAIT_INCIDENT_S`, raises ``pr.auto_merge_waiting``. Both are owners'
incidents and clear once the PR merges, closes, leaves the targets, is no
longer ready or gets a new head. Agents never merge: this is the only code
that does, and only where an owner named the repo and base.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, Final

import httpx

from omnigent.entities.factory import FactoryIssue, FactoryPr, FactoryProject
from omnigent.factory import LABEL_STATE, STATE_PAUSED
from omnigent.factory_gates import READY_GATES
from omnigent.server.factory import auto_merge, events, issues, live, nudge
from omnigent.server.factory.auto_merge import Refusal
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.control import find_project_pl
from omnigent.server.factory.prs import (
    FactoryScope,
    active_factories,
    head_checks,
    pr_alias,
    pr_subject,
    refresh_now,
)
from omnigent.server.github_app_client import GitHubApiError
from omnigent.server.github_identity import resolve_access_token
from omnigent.stores.factory_store import norm_id

_logger = logging.getLogger(__name__)

AUTO_MERGE_ENV: Final = "OMNIGENT_FACTORY_AUTO_MERGE_S"
DEFAULT_INTERVAL_S: Final = 60.0
NUDGE_TOPIC: Final = auto_merge.NUDGE_TOPIC
BACKOFF_START_S: Final = 300.0
BACKOFF_MAX_S: Final = 3600.0
WAIT_INCIDENT_S: Final = 1800.0
"""A ready PR that waits this long, for any reason, raises its incident (checks finish sooner)."""
QUIET_S: Final = 600.0
"""How long a PR refused for a lasting reason is not read again while its row stays the same."""
INCIDENT_RULE: Final = auto_merge.FAILED_RULE
WAITING_RULE: Final = auto_merge.WAITING_RULE
EVENT_KIND: Final = "pr.auto_merged"
_CALL_ERRORS: Final = (GitHubApiError, httpx.HTTPError, ValueError)

MERGER_STATE_ATTR: Final = "factory_auto_merger"
"""``app.state`` attribute holding the running :class:`AutoMerger`."""

StopFn = Callable[[], Awaitable[None]]


def interval_from_env() -> float:
    """:returns: ``OMNIGENT_FACTORY_AUTO_MERGE_S`` (default 60; ``<= 0`` means off)."""
    raw = os.environ.get(AUTO_MERGE_ENV, "").strip()
    try:
        return float(raw) if raw else DEFAULT_INTERVAL_S
    except ValueError:
        _logger.warning("factory: ignoring invalid %s=%r", AUTO_MERGE_ENV, raw)
        return DEFAULT_INTERVAL_S


def _key(pr_id: str) -> str:
    try:
        return norm_id(pr_id)
    except ValueError:
        return pr_id


def _row_key(pr: FactoryPr) -> tuple[Any, ...]:
    """What a stored row says that can change a refusal (``last_polled_at`` aside)."""
    return (
        pr.state,
        pr.head_sha,
        pr.head_ref,
        pr.base_ref,
        pr.ready_at,
        json.dumps(pr.gates, sort_keys=True, default=str),
        json.dumps(pr.human_review, sort_keys=True, default=str),
    )


@dataclass(frozen=True)
class _Backoff:
    retry_at: float
    delay: float
    head_sha: str


class AutoMerger:
    """
    Merges ready factory PRs into owner-set targets.

    :meth:`run_once` is one pass (tests drive it directly); :meth:`start`
    runs it on the interval and on nudges until :meth:`stop`.
    """

    def __init__(
        self,
        app_state: Any,
        *,
        client: Any = None,
        github_store: Any = None,
        clock: Callable[[], float] = time.time,
        interval_s: float | None = None,
    ) -> None:
        """
        :param app_state: ``app.state`` carrying the factory deps.
        :param client: The GitHub client; defaults to ``app_state.github_client``.
        :param github_store: The GitHub connection store; defaults to ``app_state.github_store``.
        :param clock: Wall clock in epoch seconds (injectable for tests).
        :param interval_s: Seconds between passes; defaults to :func:`interval_from_env`.
        """
        self._app_state = app_state
        self._client = client
        self._github_store = github_store
        self._clock = clock
        self.interval_s = interval_from_env() if interval_s is None else interval_s
        self._task: asyncio.Task[None] | None = None
        # PR row id -> its merge back-off (a GitHub error on that head).
        self._backoff: dict[str, _Backoff] = {}
        # PR row id -> (first refused at, head): how long a ready PR has waited.
        self._since: dict[str, tuple[float, str]] = {}
        # PR row id -> the reason its waiting incident was last raised with.
        self._raised: dict[str, str] = {}
        # PR row id -> (row key, until, refusal): a lasting refusal not read again meanwhile.
        self._quiet: dict[str, tuple[tuple[Any, ...], float, Refusal]] = {}
        self.waiting: dict[str, str] = {}
        """PR row id -> why it did not merge on the last pass (for logs and tests)."""

    # ── lifecycle ───────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        """Whether merging is on (``OMNIGENT_FACTORY_AUTO_MERGE_S > 0``)."""
        return self.interval_s > 0

    def start(self) -> None:
        """Run a pass now, then every :attr:`interval_s` or on a nudge."""
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(
                self._run(), name="factory-auto-merger"
            )

    async def stop(self) -> None:
        """Stop merging."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("factory: auto-merge pass failed")
            await nudge.wait(NUDGE_TOPIC, self.interval_s)

    # ── one pass ────────────────────────────────────────────────

    def _deps(self) -> FactoryDeps | None:
        deps = getattr(self._app_state, FACTORY_STATE_ATTR, None)
        return deps if isinstance(deps, FactoryDeps) else None

    def _github(self) -> tuple[Any, Any]:
        client = self._client or getattr(self._app_state, "github_client", None)
        store = self._github_store or getattr(self._app_state, "github_store", None)
        return client, store

    async def run_once(self) -> list[str]:
        """
        Merge every candidate PR of every active factory that GitHub reports green.

        :returns: The row ids of the PRs merged in this pass.
        """
        deps = self._deps()
        client, store = self._github()
        if deps is None or not self.enabled or client is None or store is None:
            return []
        merged: list[str] = []
        for scope in await asyncio.to_thread(active_factories, deps):
            try:
                merged += await self._factory(deps, scope, client, store)
            except Exception:  # one factory must not stop the others
                _logger.exception("factory %s: auto-merge pass failed", scope.factory_id)
        return merged

    async def _factory(
        self, deps: FactoryDeps, scope: FactoryScope, client: Any, store: Any
    ) -> list[str]:
        fid = scope.factory_id
        targets = await asyncio.to_thread(auto_merge.factory_targets, deps, fid)
        open_incidents = await asyncio.to_thread(_open_incidents, deps, fid)
        if not targets and not open_incidents:
            return []
        rows = await asyncio.to_thread(deps.factory_store.list_prs, fid)
        await self._reconcile(scope, open_incidents, rows, targets)
        if not targets:
            return []
        factory_paused = scope.root.labels.get(LABEL_STATE) == STATE_PAUSED
        now = self._clock()
        ready: list[tuple[FactoryPr, FactoryProject | None]] = []
        for pr in rows:
            project = scope.projects.get(pr.project_slug or "")
            if auto_merge.local_refusal(pr, targets, project) is not None:
                self._forget(pr.id)
                continue
            ready.append((pr, project))
        if not ready:
            return []
        paused = (
            {pr.project_slug or "" for pr, _ in ready}
            if factory_paused
            else await asyncio.to_thread(_paused_slugs, deps, fid, (p for p, _ in ready))
        )
        candidates: list[tuple[FactoryPr, FactoryProject | None]] = []
        for pr, project in ready:
            if (pr.project_slug or "") in paused:
                # The owner's brakes hold merges too; the wait restarts on resume.
                self.waiting[pr.id] = "factory paused" if factory_paused else "project paused"
                self._since.pop(pr.id, None)
                continue
            backoff = self._backoff.get(pr.id)
            if backoff is not None and backoff.head_sha != pr.head_sha:
                self._backoff.pop(pr.id, None)  # a new head gets a fresh try
            elif backoff is not None and backoff.retry_at > now:
                self.waiting[pr.id] = (
                    f"backing off after a GitHub error ({int(backoff.retry_at - now)}s)"
                )
                continue
            candidates.append((pr, project))
        if not candidates:
            return []
        token = None
        if scope.owner:
            token = await resolve_access_token(scope.owner, store=store, client=client)
        if token is None:
            refusal = Refusal(
                "GitHub is not connected for the factory owner"
                if scope.owner
                else "the factory has no owner whose GitHub connection merges",
                lasting=True,
            )
            for pr, _ in candidates:
                await self._refused(scope, pr, refusal, now, read=False)
            return []
        logins = await _owner_logins(store, scope.owner)
        merged: list[str] = []
        for pr, _ in candidates:
            if await self._try(deps, scope, pr, targets, token, logins, client, now):
                merged.append(pr.id)
        return merged

    async def _try(
        self,
        deps: FactoryDeps,
        scope: FactoryScope,
        pr: FactoryPr,
        targets: auto_merge.Targets,
        token: str,
        logins: frozenset[str],
        client: Any,
        now: float,
    ) -> bool:
        quiet = self._quiet.get(pr.id)
        if quiet is not None and quiet[0] == _row_key(pr) and quiet[1] > now:
            # A lasting refusal on the same stored row: no GitHub read until it changes.
            await self._refused(scope, pr, quiet[2], now, read=False)
            return False
        self._quiet.pop(pr.id, None)
        owner_name, _, name = pr.repo.partition("/")
        try:
            result = await client.graphql(
                token,
                auto_merge.merge_query(pr.number),
                {"owner": owner_name, "name": name},
                allow_partial=True,
            )
        except _CALL_ERRORS as exc:
            await self._failed(scope, pr, f"reading the PR failed: {exc}", exc, now)
            return False
        repository = result.data.get("repository")
        node = repository.get(pr_alias(pr.number)) if isinstance(repository, dict) else None
        refusal = auto_merge.github_refusal(pr, node, targets, logins)
        if refusal is None:
            stale = await self._recheck(deps, scope, pr)
            refusal = Refusal(stale) if stale is not None else None
        if refusal is not None:
            await self._refused(scope, pr, refusal, now)
            return False
        try:
            answer = await client.merge_pull(token, pr.repo, pr.number, sha=pr.head_sha)
        except _CALL_ERRORS as exc:
            await self._failed(scope, pr, f"the merge failed: {exc}", exc, now)
            return False
        await self._merged(scope, pr, node or {}, answer)
        return True

    async def _recheck(self, deps: FactoryDeps, scope: FactoryScope, pr: FactoryPr) -> str | None:
        """Why the stored state, read again right before the merge, no longer allows it."""
        fid = scope.factory_id
        store = deps.factory_store
        fresh = await asyncio.to_thread(store.get_pr, pr.id)
        if fresh is None:
            return "the PR is no longer tracked"
        targets = await asyncio.to_thread(auto_merge.factory_targets, deps, fid, fresh=True)
        slug = fresh.project_slug
        project = await asyncio.to_thread(store.get_project, fid, slug) if slug else None
        reason = auto_merge.local_refusal(fresh, targets, project)
        if reason is not None:
            return reason
        if fresh.head_sha != pr.head_sha:
            return "head moved since the pass began"
        return await asyncio.to_thread(_brake, deps, fid, slug)

    def _wait(self, pr: FactoryPr, reason: str) -> None:
        if self.waiting.get(pr.id) != reason:
            _logger.info("factory: PR %s#%s waits to auto-merge: %s", pr.repo, pr.number, reason)
        self.waiting[pr.id] = reason

    async def _refused(
        self,
        scope: FactoryScope,
        pr: FactoryPr,
        refusal: Refusal,
        now: float,
        *,
        read: bool = True,
    ) -> None:
        """
        Note why *pr* waits; owners hear of a lasting or long wait once per reason.

        :param read: *refusal* comes from a GitHub read just now (so it may ask
            the poller to read again, and a lasting one quiets later reads).
        """
        self._wait(pr, refusal.text)
        if read and refusal.refresh:
            await refresh_now(self._app_state, pr.id)
        if read and refusal.lasting:
            self._quiet[pr.id] = (_row_key(pr), now + QUIET_S, refusal)
        since = self._since.get(pr.id)
        if since is None or since[1] != pr.head_sha:
            since = (now, pr.head_sha)
            self._since[pr.id] = since
        waited = now - since[0]
        if not refusal.lasting and waited < WAIT_INCIDENT_S:
            return
        if self._raised.get(pr.id) == refusal.text:
            return
        raised = await issues.raise_issue(
            self._app_state,
            scope.factory_id,
            rule=WAITING_RULE,
            target=f"pr:{pr.id}",
            summary=(
                f"PR #{pr.number} into {pr.base_ref} is ready but the factory does not merge "
                f"it: {refusal.text}"
            )[:240]
            + "; fix that, or merge or close it on GitHub",
            severity="warning",
            project=pr.project_slug,
            evidence={
                "pr_id": pr.id,
                "repo": pr.repo,
                "number": pr.number,
                "base": pr.base_ref,
                "head_sha": pr.head_sha,
                "reason": refusal.text,
                "waited_s": int(waited),
            },
            assignee=issues.ASSIGN_OWNERS,
        )
        if raised is not None:
            self._raised[pr.id] = refusal.text

    async def _failed(
        self, scope: FactoryScope, pr: FactoryPr, message: str, exc: Exception, now: float
    ) -> None:
        """Back the PR off and raise (or re-raise) its one merge incident."""
        previous = self._backoff.get(pr.id)
        delay = BACKOFF_START_S if previous is None else min(BACKOFF_MAX_S, previous.delay * 2)
        retry_after = getattr(exc, "retry_after", None)
        if isinstance(retry_after, int | float):
            delay = min(BACKOFF_MAX_S, max(delay, float(retry_after)))
        self._backoff[pr.id] = _Backoff(now + delay, delay, pr.head_sha)
        self._wait(pr, message)
        _logger.warning(
            "factory: auto-merge of %s#%s failed (retry in %ss): %s",
            pr.repo,
            pr.number,
            int(delay),
            message,
        )
        await issues.raise_issue(
            self._app_state,
            scope.factory_id,
            rule=INCIDENT_RULE,
            target=f"pr:{pr.id}",
            summary=(
                f"Auto-merge of PR #{pr.number} into {pr.base_ref} failed: {message}"[:240]
                + f"; the factory retries in {int(delay // 60)} min"
            ),
            severity="warning",
            project=pr.project_slug,
            evidence={
                "pr_id": pr.id,
                "repo": pr.repo,
                "number": pr.number,
                "base": pr.base_ref,
                "head_sha": pr.head_sha,
                "status": getattr(exc, "status_code", None),
                "retry_in_s": int(delay),
            },
            assignee=issues.ASSIGN_OWNERS,
        )

    def _forget(self, pr_id: str) -> None:
        for book in (self.waiting, self._backoff, self._since, self._raised, self._quiet):
            book.pop(pr_id, None)

    async def _reconcile(
        self,
        scope: FactoryScope,
        open_incidents: Iterable[FactoryIssue],
        rows: Iterable[FactoryPr],
        targets: auto_merge.Targets,
    ) -> None:
        """Clear the merge incidents of PRs that merged, closed, left the targets or moved on."""
        by_id = {_key(pr.id): pr for pr in rows}
        for issue in open_incidents:
            kind, _, pr_id = (issue.target or "").partition(":")
            if kind != "pr":
                continue
            row = by_id.get(_key(pr_id))
            reason = _clear_reason(issue, row, targets, scope)
            if reason is None:
                continue
            await issues.clear_issue(
                self._app_state, scope.factory_id, issue.rule, issue.target, reason=reason
            )
            if row is not None:
                self._raised.pop(row.id, None)

    async def _merged(
        self, scope: FactoryScope, pr: FactoryPr, node: dict[str, Any], answer: dict[str, Any]
    ) -> None:
        """Record the merge, clear its incidents and let the poller run the merged flow."""
        self._forget(pr.id)
        merged_sha = str(answer.get("sha") or "").lower() or None
        gates = {name: (pr.gates or {}).get(name) for name in (*READY_GATES, "demo")}
        summary = (
            f"PR #{pr.number} merged by the factory into {pr.base_ref}: an auto-merge "
            "target, every gate passed and every check is green"
        )
        await events.emit(
            self._app_state,
            scope.factory_id,
            EVENT_KIND,
            severity="notice",
            project=pr.project_slug,
            subject=pr_subject(pr.repo, pr.number),
            summary=summary,
            data={
                "pr_id": pr.id,
                "by": "server",
                "by_kind": "server",
                "target": {"repo": pr.repo, "base": pr.base_ref},
                "head_sha": pr.head_sha,
                "merged_sha": merged_sha,
                "method": "squash",
                "gates": {k: v for k, v in gates.items() if v is not None},
                "checks": len(head_checks(node)),
            },
        )
        for rule in auto_merge.INCIDENT_RULES:
            await issues.clear_issue(
                self._app_state,
                scope.factory_id,
                rule,
                f"pr:{pr.id}",
                reason="merged by the factory",
            )
        await refresh_now(self._app_state, pr.id)
        live.announce_factory_changed(self._app_state, scope.factory_id, ("snapshot",))
        _logger.info(
            "factory %s: auto-merged %s#%s into %s as %s",
            scope.factory_id,
            pr.repo,
            pr.number,
            pr.base_ref,
            (merged_sha or "?")[:7],
        )


def _open_incidents(deps: FactoryDeps, factory_id: str) -> list[FactoryIssue]:
    return [i for i in issues.open_issues(deps, factory_id) if i.rule in auto_merge.INCIDENT_RULES]


def _clear_reason(
    issue: FactoryIssue,
    row: FactoryPr | None,
    targets: auto_merge.Targets,
    scope: FactoryScope,
) -> str | None:
    """Why a merge incident no longer applies (``None``: it still does)."""
    if row is None:
        return "the PR is no longer tracked"
    project = scope.projects.get(row.project_slug or "")
    reason = auto_merge.local_refusal(row, targets, project)
    if reason is not None:
        return reason
    evidence = issue.evidence if isinstance(issue.evidence, dict) else {}
    head = evidence.get("head_sha")
    if isinstance(head, str) and head and head.lower() != (row.head_sha or "").lower():
        return "the PR has a new head"
    return None


def _paused_slugs(deps: FactoryDeps, factory_id: str, prs: Iterable[FactoryPr]) -> set[str]:
    """:returns: The projects among *prs* whose project lead is paused."""
    paused: set[str] = set()
    for slug in {pr.project_slug for pr in prs if pr.project_slug}:
        pl = find_project_pl(deps, factory_id, slug)
        if pl is not None and pl.labels.get(LABEL_STATE) == STATE_PAUSED:
            paused.add(slug)
    return paused


def _brake(deps: FactoryDeps, factory_id: str, slug: str | None) -> str | None:
    """:returns: ``"factory paused"`` or ``"project paused"`` when a brake holds, else ``None``."""
    root = deps.conversation_store.get_conversation(factory_id)
    if root is not None and root.labels.get(LABEL_STATE) == STATE_PAUSED:
        return "factory paused"
    pl = find_project_pl(deps, factory_id, slug) if slug else None
    if pl is not None and pl.labels.get(LABEL_STATE) == STATE_PAUSED:
        return "project paused"
    return None


async def _owner_logins(store: Any, owner: str | None) -> frozenset[str]:
    """:returns: The factory owner's GitHub login (lowercase), which the agents act as."""
    if not owner:
        return frozenset()
    try:
        connection = await asyncio.to_thread(store.get, owner)
    except Exception:  # noqa: BLE001 - only repo members' markers count then
        _logger.warning("factory: could not read the owner's GitHub login", exc_info=True)
        return frozenset()
    login = getattr(connection, "github_login", None)
    return frozenset({login.lower()}) if isinstance(login, str) and login else frozenset()


def get_merger(app_state: Any) -> AutoMerger | None:
    """:returns: The running merger of *app_state*, if started."""
    merger = getattr(app_state, MERGER_STATE_ATTR, None)
    return merger if isinstance(merger, AutoMerger) else None


async def start(app_state: Any) -> StopFn:
    """Start the auto-merger (a background service; see :mod:`..background`)."""
    merger = AutoMerger(app_state)
    setattr(app_state, MERGER_STATE_ATTR, merger)
    if merger.enabled:
        merger.start()

    async def _stop() -> None:
        await merger.stop()
        if getattr(app_state, MERGER_STATE_ATTR, None) is merger:
            setattr(app_state, MERGER_STATE_ATTR, None)

    return _stop


__all__ = [
    "AUTO_MERGE_ENV",
    "BACKOFF_MAX_S",
    "BACKOFF_START_S",
    "DEFAULT_INTERVAL_S",
    "EVENT_KIND",
    "INCIDENT_RULE",
    "NUDGE_TOPIC",
    "QUIET_S",
    "WAITING_RULE",
    "WAIT_INCIDENT_S",
    "AutoMerger",
    "get_merger",
    "interval_from_env",
    "start",
]
