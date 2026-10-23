"""Factory reactions to a session's status edges.

The ``external_session_status`` route calls :func:`schedule_factory_status_edge`
once per edge. For factory members (labelled themselves, or through their root)
it:

- alerts the supervisor (top-level) or parent (sub-agent) of a ``failed`` edge,
  at most once per 10 minutes per session;
- tells the sender of a pending ``request``/``brief`` that the turn it started
  ended, unless the target already posted back;
- remembers a ``running`` edge parked on ``blocked_on`` so the supervisor can
  escalate it to the factory root (see
  :class:`~omnigent.server.factory.supervisor.FactorySupervisor`).

Everything here is best-effort: the hook never raises into the route and only
queues mail, so a status edge never waits on a delivery.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from omnigent.factory import LABEL_ID, LABEL_SUPERVISOR, factory_enabled
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.envelope import display_title
from omnigent.server.factory.membership import is_top_level, resolve_factory_id

if TYPE_CHECKING:
    from omnigent.entities import Conversation

_logger = logging.getLogger(__name__)

NOTIFIER_STATE_ATTR = "factory_notifier"
"""``app.state`` attribute holding the process's :class:`FactoryNotifier`."""

FAILED_ALERT_DEBOUNCE_S = 600.0
FAILED_DETAIL_MAX_CHARS = 300
NOTICE_OUTPUT_MAX_CHARS = 1200

_TERMINAL_STATUSES = frozenset({"idle", "failed"})
_BUSY_STATUSES = frozenset({"running", "waiting"})


def iso_utc(ts: float) -> str:
    """:returns: *ts* (epoch seconds) as ``"2026-10-23T18:22:28Z"``."""
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: str | None) -> float | None:
    """:returns: Epoch seconds of an ISO-8601 timestamp, or ``None`` when unparseable."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def excerpt(text: str, limit: int) -> str:
    """:returns: *text* on one line, cut to *limit* chars (``…`` marks a cut)."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def alert_target_id(conv: Conversation, factory_id: str) -> str | None:
    """
    Return who hears about *conv*'s failures and stalls.

    :param conv: A factory member.
    :param factory_id: Its factory root session id.
    :returns: The parent (sub-agent), else the supervisor label, else the
        factory root; ``None`` when that would be *conv* itself.
    """
    if not is_top_level(conv):
        return conv.parent_conversation_id
    supervisor = conv.labels.get(LABEL_SUPERVISOR)
    if supervisor and supervisor != conv.id:
        return supervisor
    return factory_id if factory_id != conv.id else None


def _blocked_on(status: str, data: dict[str, Any]) -> str | None:
    if status not in _BUSY_STATUSES:
        return None
    value = data.get("blocked_on")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _failure_detail(data: dict[str, Any]) -> str:
    output = data.get("output")
    if isinstance(output, str) and output.strip():
        return excerpt(output, FAILED_DETAIL_MAX_CHARS)
    error = data.get("error")
    if isinstance(error, dict):
        error = error.get("message")
    if isinstance(error, str) and error.strip():
        return excerpt(error, FAILED_DETAIL_MAX_CHARS)
    return "no error detail"


def failed_alert_text(conv: Conversation, detail: str) -> str:
    """:returns: ``[Factory alert · "<title>" (<id>) failed: <detail>]``."""
    return f'[Factory alert · "{display_title(conv.title, conv.id)}" ({conv.id}) failed: {detail}]'


def reply_notice_text(conv: Conversation, output: str) -> str:
    """:returns: The ``[Factory notice · …]`` telling a sender its request's turn ended."""
    return (
        f'[Factory notice · "{display_title(conv.title, conv.id)}" ({conv.id}) finished the turn '
        f"your request started (idle). Last output: «{output}». "
        f'Inspect with sys_session_get_history(session_id="{conv.id}").]'
    )


@dataclass
class BlockedEpisode:
    """
    A session parked on something only a human can clear.

    :param session_id: The parked session.
    :param blocked_on: What it waits for, e.g. ``"permission: Bash(rm -rf build)"``.
    :param since: Epoch seconds of the first blocked edge.
    :param escalated: Whether the supervisor already escalated it.
    """

    session_id: str
    blocked_on: str
    since: float
    escalated: bool = False


class FactoryNotifier:
    """
    Per-process state behind the status hook: debounces, blocked episodes, tasks.

    Tasks are held in a strong-ref set (asyncio keeps only weak refs) and
    cancelled by :meth:`close`.
    """

    def __init__(self, app_state: Any, *, clock: Callable[[], float] = time.time) -> None:
        """
        :param app_state: ``app.state`` carrying the factory deps and mailer.
        :param clock: Wall clock in epoch seconds (injectable for tests).
        """
        self._app_state = app_state
        self._clock = clock
        self._failed_alerted_at: dict[str, float] = {}
        self._blocked: dict[str, BlockedEpisode] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def wants(self, conv: Conversation, status: str, data: dict[str, Any]) -> bool:
        """:returns: Whether this edge may need work (cheap, no I/O)."""
        if status in _BUSY_STATUSES and _blocked_on(status, data) is None:
            return conv.id in self._blocked
        if status not in _BUSY_STATUSES and status not in _TERMINAL_STATUSES:
            return False
        # A top-level session without the label is not a member; a sub-agent
        # may still inherit membership from its root.
        return not is_top_level(conv) or bool(conv.labels.get(LABEL_ID))

    def spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        """Run *coro* as a tracked background task."""
        task = asyncio.get_running_loop().create_task(coro, name="factory-status-edge")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _track_blocked(
        self, conv: Conversation, status: str, data: dict[str, Any]
    ) -> BlockedEpisode | None:
        """:returns: The session's open episode after this edge, if any."""
        blocked_on = _blocked_on(status, data)
        if blocked_on is None:
            self._blocked.pop(conv.id, None)
            return None
        episode = self._blocked.get(conv.id)
        if episode is None:
            episode = BlockedEpisode(
                session_id=conv.id, blocked_on=blocked_on, since=self._clock()
            )
            self._blocked[conv.id] = episode
        else:
            episode.blocked_on = blocked_on
        return episode

    def blocked_due(self, now: float, after_s: float) -> list[BlockedEpisode]:
        """
        :param now: Current epoch seconds.
        :param after_s: How long an episode must last before escalation.
        :returns: Episodes older than *after_s* not yet escalated.
        """
        return [
            ep for ep in self._blocked.values() if not ep.escalated and now - ep.since >= after_s
        ]

    def blocked_episode(self, session_id: str) -> BlockedEpisode | None:
        """:returns: The open blocked episode of *session_id*, if any."""
        return self._blocked.get(session_id)

    def forget_blocked(self, session_id: str) -> None:
        """Drop *session_id*'s episode (the session is gone or not a member)."""
        self._blocked.pop(session_id, None)

    async def handle(self, conv: Conversation, status: str, data: dict[str, Any]) -> None:
        """
        React to one status edge of *conv*.

        Blocked bookkeeping runs before the first ``await`` so edges of one
        session apply in arrival order.

        :param conv: The session whose status changed.
        :param status: ``idle`` / ``running`` / ``waiting`` / ``failed``.
        :param data: The edge's (enriched) ``data``; ``output`` carries the
            last assistant text on terminal edges.
        """
        episode = self._track_blocked(conv, status, data)
        if episode is None and status not in _TERMINAL_STATUSES:
            return
        deps = getattr(self._app_state, FACTORY_STATE_ATTR, None)
        factory_id = (
            await asyncio.to_thread(resolve_factory_id, conv, deps.conversation_store)
            if isinstance(deps, FactoryDeps)
            else None
        )
        if not factory_id:
            # Not a member: forget the episode unless a newer edge replaced it.
            if episode is not None and self._blocked.get(conv.id) is episode:
                del self._blocked[conv.id]
            return
        if status == "failed":
            self._alert_failed(conv, factory_id, data)
        elif status == "idle":
            self._notify_reply(conv, data)

    def _alert_failed(self, conv: Conversation, factory_id: str, data: dict[str, Any]) -> None:
        now = self._clock()
        last = self._failed_alerted_at.get(conv.id)
        if last is not None and now - last < FAILED_ALERT_DEBOUNCE_S:
            return
        target = alert_target_id(conv, factory_id)
        if target is None:
            return
        self._failed_alerted_at[conv.id] = now
        self._prune_debounce(now)
        from omnigent.server.factory.mailer import get_mailer

        get_mailer(self._app_state).notify(target, failed_alert_text(conv, _failure_detail(data)))

    def _notify_reply(self, conv: Conversation, data: dict[str, Any]) -> None:
        from omnigent.server.factory.mailer import get_mailer

        mailer = get_mailer(self._app_state)
        pending = mailer.pop_pending_reply(conv.id)
        if pending is None:
            return
        sender_id, requested_at = pending
        replied_at = mailer.last_post(conv.id, sender_id)
        if replied_at is not None and replied_at >= requested_at:
            return
        output = data.get("output")
        last_output = excerpt(output, NOTICE_OUTPUT_MAX_CHARS) if isinstance(output, str) else ""
        mailer.notify(sender_id, reply_notice_text(conv, last_output))

    def _prune_debounce(self, now: float) -> None:
        stale = [
            sid
            for sid, at in self._failed_alerted_at.items()
            if now - at >= FAILED_ALERT_DEBOUNCE_S
        ]
        for sid in stale:
            del self._failed_alerted_at[sid]

    async def drain(self) -> None:
        """Wait for every scheduled edge task to finish (tests and shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
            # A gather of finished tasks returns without yielding; let their
            # done-callbacks prune the set.
            await asyncio.sleep(0)

    async def close(self) -> None:
        """Cancel in-flight edge tasks."""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()


def get_notifier(app_state: Any) -> FactoryNotifier:
    """
    Return the app's notifier, creating it on first use.

    :param app_state: ``app.state`` (or a test stand-in).
    :returns: The shared :class:`FactoryNotifier`.
    """
    notifier = getattr(app_state, NOTIFIER_STATE_ATTR, None)
    if not isinstance(notifier, FactoryNotifier):
        notifier = FactoryNotifier(app_state)
        setattr(app_state, NOTIFIER_STATE_ATTR, notifier)
    return notifier


async def on_status_edge(
    app_state: Any,
    conv: Conversation,
    status: str,
    data: dict[str, Any],
) -> None:
    """
    Apply the factory's reaction to one status edge; never raises.

    Alerts and notices are queued on the mailer, so this returns without
    waiting for any delivery.

    :param app_state: ``app.state`` with the factory deps registered.
    :param conv: The session whose status changed.
    :param status: The published status, e.g. ``"failed"``.
    :param data: The edge's ``data`` (``output``, ``blocked_on``, …).
    """
    if not factory_enabled():
        return
    try:
        await get_notifier(app_state).handle(conv, status, data)
    except Exception:  # noqa: BLE001 - a factory reaction must never break the edge
        _logger.warning(
            "factory: status edge handling failed for %s", getattr(conv, "id", None), exc_info=True
        )


def schedule_factory_status_edge(
    app_state: Any,
    conv: Conversation,
    status: str,
    data: dict[str, Any],
) -> None:
    """
    Status-route hook: schedule :func:`on_status_edge` as a background task.

    Returns at once (no I/O) and never raises; a no-op unless
    ``OMNIGENT_FACTORY_ENABLED=1`` and the factory routes are registered.

    :param app_state: ``request.app.state``.
    :param conv: The session whose status changed.
    :param status: The published status.
    :param data: The edge's ``data``.
    """
    try:
        if not factory_enabled():
            return
        if not isinstance(getattr(app_state, FACTORY_STATE_ATTR, None), FactoryDeps):
            return
        notifier = get_notifier(app_state)
        if notifier.wants(conv, status, data):
            notifier.spawn(on_status_edge(app_state, conv, status, dict(data)))
    except Exception:  # noqa: BLE001 - the status route must not fail on the factory
        _logger.warning(
            "factory: could not schedule status edge for %s",
            getattr(conv, "id", None),
            exc_info=True,
        )


__all__ = [
    "BlockedEpisode",
    "FactoryNotifier",
    "alert_target_id",
    "get_notifier",
    "on_status_edge",
    "schedule_factory_status_edge",
]
