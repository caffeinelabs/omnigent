"""CI-watch: wake an agent session when its PRs' CI concludes.

A session "arms" a watch (see the ``.../ci-watch`` route) naming the PRs it
opened; a background poller then checks each PR's head-ref check runs and, when
they reach a terminal state (all checks completed), injects a ``[CI] …`` message
into that session — starting a continuation turn so the agent reacts (fix, re-run,
merge). This is the server-side analog of an agent "sleeping until CI is done":
detection is a poll, the wake is the standard message-dispatch primitive
(:func:`omnigent.server.routes._sessions.orchestration.wake_session_with_notice`).

The watch registry is in-memory (per server process); an armed watch is dropped
on restart. Durability (rehydrate from ``session_state``) is a follow-up.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger(__name__)

# Terminal check-run conclusions that mean "CI failed" (vs. success/neutral).
_FAILING_CONCLUSIONS = frozenset(
    {"failure", "cancelled", "timed_out", "action_required", "stale", "startup_failure"}
)

_DEFAULT_POLL_INTERVAL_S = 45.0


@dataclass
class WatchedPr:
    """One PR a session is watching for CI completion."""

    repo: str
    number: int
    head_ref: str


@dataclass
class CiWatch:
    """An armed CI watch for a single session."""

    session_id: str
    # The user whose GitHub token polls check status (the arming caller).
    user_id: str
    prs: list[WatchedPr]
    # Per-PR last terminal conclusion already notified: "success" | "failure".
    notified: dict[str, str] = field(default_factory=dict)


class CiWatchRegistry:
    """In-memory set of armed CI watches, keyed by session id."""

    def __init__(self) -> None:
        self._watches: dict[str, CiWatch] = {}

    def arm(self, watch: CiWatch) -> None:
        """Arm (or replace) the watch for a session."""
        self._watches[watch.session_id] = watch

    def disarm(self, session_id: str) -> None:
        """Drop a session's watch (idempotent)."""
        self._watches.pop(session_id, None)

    def snapshot(self) -> list[CiWatch]:
        """Return the current watches (copy of the values)."""
        return list(self._watches.values())

    def __len__(self) -> int:
        return len(self._watches)


def aggregate_check_conclusion(runs: Sequence[dict[str, object]]) -> str:
    """Reduce a ref's check runs to one status.

    :param runs: Check-run dicts (``status``/``conclusion``) from
        :meth:`GitHubAppClient.list_check_runs`.
    :returns: ``"none"`` (no checks), ``"pending"`` (some not completed),
        ``"failure"`` (any terminal failing conclusion), else ``"success"``.
    """
    if not runs:
        return "none"
    if any(run.get("status") != "completed" for run in runs):
        return "pending"
    if any(run.get("conclusion") in _FAILING_CONCLUSIONS for run in runs):
        return "failure"
    return "success"


def _pr_key(pr: WatchedPr) -> str:
    return f"{pr.repo}#{pr.number}"


def _format_notice(pr: WatchedPr, conclusion: str) -> str:
    verb = "passed" if conclusion == "success" else "failed"
    return (
        f"[CI] Checks on {_pr_key(pr)} ({pr.head_ref}) {verb}. "
        "This is an automated CI update — review the result and continue "
        "(fix failures, re-run, or merge) as appropriate."
    )


async def _poll_once(
    watch: CiWatch,
    *,
    resolve_token: Callable[[str], Awaitable[str | None]],
    list_check_runs: Callable[[str, str, str], Awaitable[list[dict[str, object]]]],
    wake: Callable[[str, str], Awaitable[bool]],
) -> bool:
    """Poll one session's watched PRs; wake on new terminal transitions.

    :returns: ``True`` when every watched PR has reached a terminal state (the
        caller then disarms the watch); ``False`` while any PR is still pending.
    """
    token = await resolve_token(watch.user_id)
    if token is None:
        # No usable token (disconnected / refresh failed) — keep the watch; a
        # later poll may succeed. Treat as not-yet-terminal.
        return False
    all_terminal = True
    for pr in watch.prs:
        key = _pr_key(pr)
        if key in watch.notified:
            continue  # already notified for this PR's terminal state
        try:
            runs = await list_check_runs(token, pr.repo, pr.head_ref)
        except Exception as exc:  # noqa: BLE001 - poll must never crash the loop
            _logger.warning("ci-watch: check-runs failed for %s: %s", key, exc)
            all_terminal = False
            continue
        conclusion = aggregate_check_conclusion(runs)
        if conclusion in ("success", "failure"):
            woke = await wake(watch.session_id, _format_notice(pr, conclusion))
            # Only mark notified once the wake actually delivered, so a transient
            # unbound-runner miss retries on the next tick.
            if woke:
                watch.notified[key] = conclusion
            else:
                all_terminal = False
        else:
            # "pending" or "none" (no checks yet) → still waiting.
            all_terminal = False
    return all_terminal


async def run_ci_watch_poller(
    registry: CiWatchRegistry,
    *,
    resolve_token: Callable[[str], Awaitable[str | None]],
    list_check_runs: Callable[[str, str, str], Awaitable[list[dict[str, object]]]],
    wake: Callable[[str, str], Awaitable[bool]],
    interval_s: float = _DEFAULT_POLL_INTERVAL_S,
) -> None:
    """Background loop: poll every armed watch and wake sessions on CI completion.

    Runs until cancelled (mounted as a lifespan task). Each tick polls all
    armed watches; a watch whose PRs are all terminal is disarmed. The callable
    injection (``resolve_token`` / ``list_check_runs`` / ``wake``) keeps this
    loop free of direct store/client imports and trivially testable.

    :param registry: The shared :class:`CiWatchRegistry`.
    :param resolve_token: ``user_id -> access token | None``.
    :param list_check_runs: ``(token, full_name, ref) -> [check-run dicts]``.
    :param wake: ``(session_id, notice) -> delivered?``.
    :param interval_s: Seconds between polling sweeps.
    """
    _logger.info("ci-watch poller started (interval=%.0fs)", interval_s)
    while True:
        await asyncio.sleep(interval_s)
        for watch in registry.snapshot():
            try:
                done = await _poll_once(
                    watch,
                    resolve_token=resolve_token,
                    list_check_runs=list_check_runs,
                    wake=wake,
                )
            except Exception:
                _logger.exception("ci-watch: poll failed for session %s", watch.session_id)
                continue
            if done:
                _logger.info("ci-watch: all PRs terminal for %s; disarming", watch.session_id)
                registry.disarm(watch.session_id)


def build_watch_from_prs(session_id: str, user_id: str, prs: Sequence[dict[str, Any]]) -> CiWatch:
    """Build a :class:`CiWatch` from resolved session-PR dicts.

    :param session_id: The session to wake.
    :param user_id: The arming caller (token owner for polling).
    :param prs: PR dicts carrying ``repo``, ``number``, ``head_ref`` (the shape
        the session-PR listing returns).
    :returns: The watch (empty ``prs`` when none are watchable).
    """
    watched: list[WatchedPr] = []
    for pr in prs:
        repo = pr.get("repo")
        number = pr.get("number")
        head_ref = pr.get("head_ref")
        if isinstance(repo, str) and isinstance(number, int) and isinstance(head_ref, str):
            watched.append(WatchedPr(repo=repo, number=number, head_ref=head_ref))
    return CiWatch(session_id=session_id, user_id=user_id, prs=watched)
