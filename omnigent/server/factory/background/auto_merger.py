"""The server's auto-merger: merges ready factory PRs into owner-set targets.

Every ``OMNIGENT_FACTORY_AUTO_MERGE_S`` seconds (default 60; ``0`` turns it
off), and at once on ``nudge("auto_merge")`` (the PR poller nudges it when a
PR into a target becomes ready), for every active factory whose product
profile names targets (``product.delivery.auto_merge``):

1. pick the stored PRs :func:`.auto_merge.local_refusal` accepts (open, into
   a target, ready on their head, not a draft);
2. read each from GitHub (:func:`.auto_merge.merge_query`) and merge only when
   :func:`.auto_merge.github_refusal` finds nothing: same head, open, not a
   draft, ``MERGEABLE``, no change request, every check on the head green;
3. squash-merge it with the stored head as the expected head, using the
   factory owner's GitHub connection (the PR poller's), then record
   ``pr.auto_merged`` (the Room shows it in the PR's and the project's
   threads), clear the PR's merge incident and refresh the PR at once so the
   usual merged flow runs (notices, stage, dependents).

A GitHub error backs the PR off (5 min, doubling up to an hour) and raises
one incident (``pr.auto_merge_failed`` on ``pr:<id>``, for owners); it never
retries in a tight loop. A PR that is not green yet simply waits for the next
pass. Agents never merge: this is the only code that does, and only where an
owner named the repo and base.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any, Final

import httpx

from omnigent.entities.factory import FactoryPr
from omnigent.factory_gates import READY_GATES
from omnigent.server.factory import auto_merge, events, issues, live, nudge
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
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

_logger = logging.getLogger(__name__)

AUTO_MERGE_ENV: Final = "OMNIGENT_FACTORY_AUTO_MERGE_S"
DEFAULT_INTERVAL_S: Final = 60.0
NUDGE_TOPIC: Final = auto_merge.NUDGE_TOPIC
BACKOFF_START_S: Final = 300.0
BACKOFF_MAX_S: Final = 3600.0
INCIDENT_RULE: Final = "pr.auto_merge_failed"
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
        # PR row id -> (retry at, delay): a PR whose merge failed waits this long.
        self._backoff: dict[str, tuple[float, float]] = {}
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
        targets = await asyncio.to_thread(auto_merge.factory_targets, deps, scope.factory_id)
        if not targets:
            return []
        rows = await asyncio.to_thread(deps.factory_store.list_prs, scope.factory_id)
        now = self._clock()
        candidates: list[FactoryPr] = []
        for pr in rows:
            reason = auto_merge.local_refusal(pr, targets)
            if reason is not None:
                self.waiting.pop(pr.id, None)
                self._backoff.pop(pr.id, None)
                continue
            backoff = self._backoff.get(pr.id)
            if backoff is not None and backoff[0] > now:
                self.waiting[pr.id] = (
                    f"backing off after a GitHub error ({int(backoff[0] - now)}s)"
                )
                continue
            candidates.append(pr)
        if not candidates or not scope.owner:
            return []
        token = await resolve_access_token(scope.owner, store=store, client=client)
        if token is None:
            for pr in candidates:
                self.waiting[pr.id] = "GitHub not connected for the factory owner"
            return []
        merged: list[str] = []
        for pr in candidates:
            if await self._try(scope, pr, targets, token, client, now):
                merged.append(pr.id)
        return merged

    async def _try(
        self,
        scope: FactoryScope,
        pr: FactoryPr,
        targets: auto_merge.Targets,
        token: str,
        client: Any,
        now: float,
    ) -> bool:
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
        reason = auto_merge.github_refusal(pr, node, targets)
        if reason is not None:
            self._wait(pr, reason)
            return False
        try:
            answer = await client.merge_pull(token, pr.repo, pr.number, sha=pr.head_sha)
        except _CALL_ERRORS as exc:
            await self._failed(scope, pr, f"the merge failed: {exc}", exc, now)
            return False
        await self._merged(scope, pr, node or {}, answer)
        return True

    def _wait(self, pr: FactoryPr, reason: str) -> None:
        if self.waiting.get(pr.id) != reason:
            _logger.info("factory: PR %s#%s waits to auto-merge: %s", pr.repo, pr.number, reason)
        self.waiting[pr.id] = reason

    async def _failed(
        self, scope: FactoryScope, pr: FactoryPr, message: str, exc: Exception, now: float
    ) -> None:
        """Back the PR off and raise (or re-raise) its one merge incident."""
        previous = self._backoff.get(pr.id)
        delay = BACKOFF_START_S if previous is None else min(BACKOFF_MAX_S, previous[1] * 2)
        retry_after = getattr(exc, "retry_after", None)
        if isinstance(retry_after, int | float):
            delay = min(BACKOFF_MAX_S, max(delay, float(retry_after)))
        self._backoff[pr.id] = (now + delay, delay)
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

    async def _merged(
        self, scope: FactoryScope, pr: FactoryPr, node: dict[str, Any], answer: dict[str, Any]
    ) -> None:
        """Record the merge, clear its incident and let the poller run the merged flow."""
        self._backoff.pop(pr.id, None)
        self.waiting.pop(pr.id, None)
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
        await issues.clear_issue(
            self._app_state,
            scope.factory_id,
            INCIDENT_RULE,
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
    "AutoMerger",
    "get_merger",
    "interval_from_env",
    "start",
]
