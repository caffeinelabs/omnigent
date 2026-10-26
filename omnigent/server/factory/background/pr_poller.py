"""The GitHub PR poller: the cockpit's gates, merges and closures come from here.

Every ``OMNIGENT_FACTORY_PR_POLL_S`` seconds (default 60; ``0`` turns it off),
and at once on ``nudge("prs")``, for every active factory:

- **discovery** (every 5 min per owner and repo): page ``GET /repos/{repo}/pulls``
  newest first until a PR predates the factory, and track PRs whose head branch
  is ``factory/<project slug>/<task>``;
- **refresh**: one read-only GraphQL query per repo over its open PRs (and
  those that ended in the last 10 min), applied by :mod:`..prs`.

It uses the factory owner's GitHub connection and never writes to GitHub.
401/403/429 back off exponentially per owner (up to 10 min); under 500
remaining API points the cadence slows 5×.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Final

import httpx

from omnigent.entities.factory import FactoryPr
from omnigent.factory_gates import parse_time
from omnigent.server.factory import events, nudge, status
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.prs import (
    NUDGE_TOPIC,
    FactoryScope,
    active_factories,
    apply_refresh,
    parse_factory_branch,
    pr_alias,
    pr_subject,
    refresh_query,
)
from omnigent.server.github_app_client import GitHubApiError
from omnigent.server.github_identity import resolve_access_token
from omnigent.stores.factory_store import TERMINAL_PR_WINDOW_S, FactoryStore

_logger = logging.getLogger(__name__)

PR_POLL_ENV: Final = "OMNIGENT_FACTORY_PR_POLL_S"
DEFAULT_PR_POLL_S: Final = 60.0
DISCOVERY_INTERVAL_S: Final = 300.0
DISCOVERY_MAX_PAGES: Final = 5
REFRESH_BATCH: Final = 20
LOW_RATE_REMAINING: Final = 500
SLOW_FACTOR: Final = 5
BACKOFF_START_S: Final = 60.0
BACKOFF_MAX_S: Final = 600.0
BACKOFF_STATUSES: Final = frozenset({401, 403, 429})

POLLER_STATE_ATTR: Final = "factory_pr_poller"
"""``app.state`` attribute holding the running :class:`PrPoller`."""

StopFn = Callable[[], Awaitable[None]]


def poll_interval_from_env() -> float:
    """:returns: ``OMNIGENT_FACTORY_PR_POLL_S`` (default 60; ``<= 0`` means off)."""
    raw = os.environ.get(PR_POLL_ENV, "").strip()
    try:
        return float(raw) if raw else DEFAULT_PR_POLL_S
    except ValueError:
        _logger.warning("factory: ignoring invalid %s=%r", PR_POLL_ENV, raw)
        return DEFAULT_PR_POLL_S


class _OwnerFailure(Exception):
    """One owner's GitHub calls failed this run (the reason is the message)."""


class PrPoller:
    """
    Polls GitHub for every active factory's PRs.

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
        poll_s: float | None = None,
    ) -> None:
        """
        :param app_state: ``app.state`` carrying the factory deps.
        :param client: A :class:`~omnigent.server.github_app_client.GitHubAppClient`;
            defaults to ``app_state.github_client``.
        :param github_store: The GitHub connection store; defaults to
            ``app_state.github_store``.
        :param clock: Wall clock in epoch seconds (injectable for tests).
        :param poll_s: Poll interval; defaults to :func:`poll_interval_from_env`.
        """
        self._app_state = app_state
        self._client = client
        self._github_store = github_store
        self._clock = clock
        self.poll_s = poll_interval_from_env() if poll_s is None else poll_s
        self._task: asyncio.Task[None] | None = None
        self._last_discovery: dict[tuple[str, str], float] = {}
        self._backoff: dict[str, tuple[float, float]] = {}
        self._low_rate: set[str] = set()
        self._soft_errors: list[str] = []
        self.remaining: int | None = None

    # ── lifecycle ───────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        """Whether polling is on (``OMNIGENT_FACTORY_PR_POLL_S > 0``)."""
        return self.poll_s > 0

    @property
    def interval(self) -> float:
        """Seconds between passes, 5× while any owner is low on API budget."""
        return self.poll_s * (SLOW_FACTOR if self._low_rate else 1)

    def start(self) -> None:
        """Run a pass now, then every :attr:`interval` or on a nudge."""
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(
                self._run(), name="factory-pr-poller"
            )

    async def stop(self) -> None:
        """Stop polling."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    async def _run(self) -> None:
        forced: set[str] = set()
        while True:
            try:
                await self.run_once(forced=forced)
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("factory: PR poll failed")
            forced = await nudge.wait(NUDGE_TOPIC, self.interval) or set()

    # ── one pass ────────────────────────────────────────────────

    def _deps(self) -> FactoryDeps | None:
        deps = getattr(self._app_state, FACTORY_STATE_ATTR, None)
        return deps if isinstance(deps, FactoryDeps) else None

    @property
    def _store(self) -> FactoryStore:
        deps = self._deps()
        if deps is None:
            raise RuntimeError("factory routes are not registered on this app")
        return deps.factory_store

    def _github(self) -> tuple[Any, Any]:
        client = self._client or getattr(self._app_state, "github_client", None)
        store = self._github_store or getattr(self._app_state, "github_store", None)
        return client, store

    async def run_once(self, *, forced: set[str] | None = None) -> None:
        """
        Discover and refresh every active factory's PRs once.

        :param forced: PR row ids to refresh even when not due (nudged keys).
        """
        deps = self._deps()
        if deps is None:
            return
        now = self._clock()
        client, store = self._github()
        if not self.enabled or client is None or store is None:
            reason = "PR polling is off" if not self.enabled else "GitHub App not configured"
            status.set_status("github", state="off", error=reason, last_poll_at=None)
            return
        scopes = await asyncio.to_thread(active_factories, deps)
        by_owner: dict[str | None, list[FactoryScope]] = {}
        for scope in scopes:
            by_owner.setdefault(scope.owner, []).append(scope)
        outcomes: list[str] = []
        errors: list[str] = []
        for owner, owned in by_owner.items():
            outcome, error = await self._poll_owner(
                owner, owned, client, store, now, forced or set()
            )
            outcomes.append(outcome)
            if error:
                errors.append(error)
        self._report(outcomes, errors, now)

    def _report(self, outcomes: Sequence[str], errors: Sequence[str], now: float) -> None:
        if outcomes and all(o == "disconnected" for o in outcomes):
            state = "disconnected"
        elif any(o != "ok" for o in outcomes):
            state = "degraded"
        else:
            state = "ok"
        status.set_status(
            "github",
            state=state,
            last_poll_at=int(now),
            error="; ".join(errors) or None,
            remaining=self.remaining,
            rate_limit_remaining=self.remaining,
        )

    async def _poll_owner(
        self,
        owner: str | None,
        scopes: list[FactoryScope],
        client: Any,
        store: Any,
        now: float,
        forced: set[str],
    ) -> tuple[str, str | None]:
        """:returns: ``(outcome, error)``; outcome is ``ok``/``disconnected``/``degraded``."""
        backoff = self._backoff.get(owner or "")
        if backoff is not None and backoff[0] > now:
            wait = int(backoff[0] - now)
            return "degraded", f"GitHub backoff for {owner}: retrying in {wait}s"
        token = await resolve_access_token(owner, store=store, client=client) if owner else None
        if token is None:
            message = f"GitHub not connected for {owner or 'the factory owner'}"
            await self._mark_errors(scopes, message, now)
            return "disconnected", message
        self._soft_errors = []
        try:
            await self._discover(owner or "", token, scopes, client, now)
            await self._refresh(owner or "", token, scopes, client, now, forced)
        except _OwnerFailure as exc:
            return "degraded", str(exc)
        self._backoff.pop(owner or "", None)
        if self._soft_errors:
            return "degraded", "; ".join(self._soft_errors)
        return "ok", None

    async def _failure(
        self, owner: str, exc: Exception, scopes: list[FactoryScope], now: float
    ) -> _OwnerFailure:
        code = exc.status_code if isinstance(exc, GitHubApiError) else None
        if code in BACKOFF_STATUSES:
            previous = self._backoff.get(owner)
            delay = BACKOFF_START_S if previous is None else min(BACKOFF_MAX_S, previous[1] * 2)
            retry_after = getattr(exc, "retry_after", None)
            if isinstance(retry_after, int | float):
                delay = min(BACKOFF_MAX_S, max(delay, float(retry_after)))
            self._backoff[owner] = (now + delay, delay)
            message = f"GitHub returned {code}; backing off {int(delay)}s"
        else:
            message = f"GitHub request failed: {exc}"
        _logger.warning("factory: PR poll for %s failed: %s", owner, message)
        await self._mark_errors(scopes, message, now)
        return _OwnerFailure(message)

    async def _mark_errors(self, scopes: list[FactoryScope], message: str, now: float) -> None:
        """Set ``poll_error`` on the scopes' due PRs (only where it changes)."""
        store = self._store
        for scope in scopes:
            prs = await asyncio.to_thread(store.list_prs, scope.factory_id)
            for pr in prs:
                if self._due(pr, now) and pr.poll_error != message:
                    await asyncio.to_thread(store.update_pr, pr.id, poll_error=message[:512])

    # ── discovery ───────────────────────────────────────────────

    async def _discover(
        self, owner: str, token: str, scopes: list[FactoryScope], client: Any, now: float
    ) -> None:
        repos: dict[str, list[FactoryScope]] = {}
        for scope in scopes:
            for project in scope.projects.values():
                if project.repo:
                    repos.setdefault(project.repo, [])
                    if scope not in repos[project.repo]:
                        repos[project.repo].append(scope)
        every = DISCOVERY_INTERVAL_S * (SLOW_FACTOR if self._low_rate else 1)
        for repo, repo_scopes in repos.items():
            key = (owner, repo.lower())
            last = self._last_discovery.get(key)
            if last is not None and now - last < every:
                continue
            await self._discover_repo(owner, repo, repo_scopes, token, client, now, scopes)
            self._last_discovery[key] = now

    async def _discover_repo(
        self,
        owner: str,
        repo: str,
        scopes: list[FactoryScope],
        token: str,
        client: Any,
        now: float,
        owner_scopes: list[FactoryScope],
    ) -> None:
        oldest_start = min(scope.started_at for scope in scopes)
        for page in range(1, DISCOVERY_MAX_PAGES + 1):
            try:
                pulls = await client.list_pulls(token, repo, state="all", page=page)
            except (GitHubApiError, httpx.HTTPError) as exc:
                if isinstance(exc, GitHubApiError) and exc.status_code in BACKOFF_STATUSES:
                    raise await self._failure(owner, exc, owner_scopes, now) from exc
                # One unreadable repo (a typo, no access) must not stop the others.
                _logger.warning("factory: discovering %s PRs failed: %s", repo, exc)
                self._soft_errors.append(f"discovering {repo} PRs failed: {exc}")
                return
            older = False
            for pull in pulls:
                created = parse_time(pull.get("created_at"))
                created_at = int(created.timestamp()) if created is not None else 0
                if created_at < oldest_start:
                    older = True
                    break
                await self._track_discovered(repo, pull, created_at, scopes)
            if older or len(pulls) < 100:
                return

    async def _track_discovered(
        self, repo: str, pull: dict[str, Any], created_at: int, scopes: list[FactoryScope]
    ) -> None:
        head = pull.get("head") if isinstance(pull.get("head"), dict) else {}
        base = pull.get("base") if isinstance(pull.get("base"), dict) else {}
        number = pull.get("number")
        if not isinstance(number, int):
            return
        if pull.get("state") == "closed" and not pull.get("merged_at"):
            return
        candidates = [
            scope
            for scope in scopes
            if scope.started_at <= created_at
            and parse_factory_branch(head.get("ref"), _repo_slugs(scope, repo)) is not None
        ]
        if not candidates:
            return
        scope = max(candidates, key=lambda s: s.started_at)
        parsed = parse_factory_branch(head.get("ref"), _repo_slugs(scope, repo))
        if parsed is None:
            return
        slug, task = parsed
        store = self._store
        existing = await asyncio.to_thread(store.get_pr_by_number, scope.factory_id, repo, number)
        if existing is not None:
            if existing.project_slug is None:
                await asyncio.to_thread(
                    store.update_pr, existing.id, project_slug=slug, task=existing.task or task
                )
            return
        row, created = await asyncio.to_thread(
            store.upsert_pr,
            scope.factory_id,
            repo,
            number,
            source="discovered",
            project_slug=slug,
            task=task[:64],
            url=str(pull.get("html_url") or f"https://github.com/{repo}/pull/{number}"),
            title=str(pull.get("title") or "")[:512],
            head_ref=str(head.get("ref") or "")[:255],
            base_ref=str(base.get("ref") or "")[:255],
            head_sha=str(head.get("sha") or "").lower()[:40],
            is_draft=pull.get("draft") is True,
        )
        if created:
            await events.emit(
                self._app_state,
                scope.factory_id,
                "pr.discovered",
                project=slug,
                subject=pr_subject(repo, number),
                summary=f"PR #{number} found on branch {head.get('ref')}",
                data={"pr_id": row.id, "task": task, "url": row.url},
            )

    # ── refresh ─────────────────────────────────────────────────

    def _due(self, pr: FactoryPr, now: float) -> bool:
        if pr.last_polled_at is not None and pr.last_polled_at > now - self.interval:
            return False
        if pr.state == "open":
            return True
        ended = pr.merged_at or pr.closed_at or 0
        return ended >= now - TERMINAL_PR_WINDOW_S

    async def _refresh(
        self,
        owner: str,
        token: str,
        scopes: list[FactoryScope],
        client: Any,
        now: float,
        forced: set[str],
    ) -> None:
        store = self._store
        groups: dict[str, list[tuple[FactoryScope, FactoryPr]]] = {}
        for scope in scopes:
            for pr in await asyncio.to_thread(store.list_prs, scope.factory_id):
                if pr.id in forced or self._due(pr, now):
                    groups.setdefault(pr.repo.lower(), []).append((scope, pr))
        for items in groups.values():
            for start in range(0, len(items), REFRESH_BATCH):
                await self._refresh_batch(
                    owner, token, items[start : start + REFRESH_BATCH], client, now, scopes
                )

    async def _refresh_batch(
        self,
        owner: str,
        token: str,
        items: list[tuple[FactoryScope, FactoryPr]],
        client: Any,
        now: float,
        scopes: list[FactoryScope],
    ) -> None:
        repo = items[0][1].repo
        owner_name, _, name = repo.partition("/")
        numbers = sorted({pr.number for _, pr in items})
        try:
            result = await client.graphql(
                token,
                refresh_query(numbers),
                {"owner": owner_name, "name": name},
                allow_partial=True,
            )
        except (GitHubApiError, httpx.HTTPError) as exc:
            raise await self._failure(owner, exc, scopes, now) from exc
        await self._note_rate_limit(owner, result.rate_limit, scopes)
        repository = result.data.get("repository")
        repository = repository if isinstance(repository, dict) else {}
        bases = {
            str(node.get("baseRefName") or "")
            for node in repository.values()
            if isinstance(node, dict) and node.get("baseRefName")
        }
        contexts = await self._required_contexts(token, repo, bases, client)
        for scope, pr in items:
            node = repository.get(pr_alias(pr.number))
            if not isinstance(node, dict):
                await asyncio.to_thread(
                    self._store.update_pr,
                    pr.id,
                    last_polled_at=int(now),
                    poll_error="PR not found on GitHub (deleted, or no access)",
                )
                continue
            try:
                await apply_refresh(
                    self._app_state,
                    pr,
                    node,
                    now=int(now),
                    scope=scope,
                    required_contexts=contexts.get(str(node.get("baseRefName") or ""), ()),
                )
            except Exception:
                _logger.exception("factory: applying the refresh of %s failed", pr.id)

    async def _required_contexts(
        self, token: str, repo: str, bases: set[str], client: Any
    ) -> dict[str, frozenset[str]]:
        """Required status contexts per base branch (cached by the client for 1 h)."""
        out: dict[str, frozenset[str]] = {}
        for base in sorted(bases):
            try:
                rules = await client.branch_rules(token, repo, base)
            except (GitHubApiError, httpx.HTTPError) as exc:
                _logger.info("factory: %s#%s branch rules unreadable: %s", repo, base, exc)
                continue
            out[base] = rules.contexts
        return out

    async def _note_rate_limit(
        self, owner: str, rate_limit: Any, scopes: list[FactoryScope]
    ) -> None:
        remaining = getattr(rate_limit, "remaining", None)
        if not isinstance(remaining, int):
            return
        self.remaining = remaining
        if remaining >= LOW_RATE_REMAINING:
            self._low_rate.discard(owner)
            return
        if owner in self._low_rate:
            return
        self._low_rate.add(owner)
        for scope in scopes:
            await events.emit(
                self._app_state,
                scope.factory_id,
                "factory.warning",
                severity="warning",
                summary=(
                    f"GitHub API budget low ({remaining} left): PR polling slowed "
                    f"{SLOW_FACTOR}× until it recovers"
                ),
                data={"remaining": remaining, "reset_at": getattr(rate_limit, "reset_at", None)},
            )


def _repo_slugs(scope: FactoryScope, repo: str) -> set[str]:
    return {
        slug
        for slug, project in scope.projects.items()
        if project.repo and project.repo.lower() == repo.lower()
    }


def get_poller(app_state: Any) -> PrPoller | None:
    """:returns: The running poller of *app_state*, if started."""
    poller = getattr(app_state, POLLER_STATE_ATTR, None)
    return poller if isinstance(poller, PrPoller) else None


async def start(app_state: Any) -> StopFn:
    """Start the PR poller (a background service; see :mod:`..background`)."""
    poller = PrPoller(app_state)
    setattr(app_state, POLLER_STATE_ATTR, poller)
    if poller.enabled:
        poller.start()
    else:
        status.set_status("github", state="off", error="PR polling is off")

    async def _stop() -> None:
        await poller.stop()
        if getattr(app_state, POLLER_STATE_ATTR, None) is poller:
            setattr(app_state, POLLER_STATE_ATTR, None)

    return _stop


__all__ = [
    "DEFAULT_PR_POLL_S",
    "PR_POLL_ENV",
    "PrPoller",
    "get_poller",
    "poll_interval_from_env",
    "start",
]
