"""Factory reactions to a session's status edges.

Edges arrive on two paths:

- The ``external_session_status`` route (a harness's own Stop/StopFailure
  hooks) calls :func:`schedule_factory_status_edge`. These turn ends are
  authoritative, so they drive every reaction below.
- The runner-stream relay calls :func:`schedule_factory_relayed_status` for
  each relayed ``session.status``. This is the only path that carries
  ``blocked_on`` (claude-native's status file). Its ``idle`` edges can come
  from the PTY watcher and oscillate mid-turn, so the relay feeds only the
  blocked bookkeeping and the debounced ``failed`` alert, never a reply notice.

For factory members (labelled themselves, or through their root) this:

- alerts the supervisor (top-level) or parent (sub-agent) of a ``failed`` edge,
  at most once per 10 minutes per session;
- tells the sender of a pending ``request``/``brief`` that the turn it started
  ended, unless the target already posted back;
- remembers a ``running`` edge parked on ``blocked_on`` so the supervisor can
  escalate it to the factory root (see
  :class:`~omnigent.server.factory.supervisor.FactorySupervisor`).

Every alert and notice is also logged as an event (``alert.failed``,
``notice.reply``); an escalated blocked episode logs ``needs_human.cleared``
when it ends. Everything here is best-effort: the hooks never raise into
their caller and only queue mail, so a status edge never waits on a delivery.
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
from omnigent.server.factory import events, server_decisions
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.envelope import display_title
from omnigent.server.factory.membership import is_top_level, resolve_factory_id

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactoryEvent

_logger = logging.getLogger(__name__)

NOTIFIER_STATE_ATTR = "factory_notifier"
"""``app.state`` attribute holding the process's :class:`FactoryNotifier`."""

FAILED_ALERT_DEBOUNCE_S = 600.0
FAILED_DETAIL_MAX_CHARS = 300
NOTICE_OUTPUT_MAX_CHARS = 1200
BLOCKED_DEDUPE_LOOKBACK_S = 86400.0
"""How far back an earlier escalation of the same prompt still counts (it survives a roll)."""
NEEDS_HUMAN_KIND = "needs_human"
NEEDS_HUMAN_CLEARED_KIND = "needs_human.cleared"
"""Logged when an escalated blocked episode ends, so a later block is a new episode."""

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


def _blocked_on(status: str, value: object) -> str | None:
    if status not in _BUSY_STATUSES:
        return None
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
        f'Inspect with sys_session_get_history(conversation_id="{conv.id}").]'
    )


@dataclass
class BlockedEpisode:
    """
    A session parked on something only a human can clear.

    :param session_id: The parked session.
    :param blocked_on: What it waits for, e.g. ``"permission: Bash(rm -rf build)"``.
    :param since: Epoch seconds of the first blocked edge.
    :param escalated: Whether the supervisor already escalated it.
    :param decision_id: The decision opened for the human, expired when the episode clears.
    :param factory_id: The factory it was escalated in; its end is logged there.
    """

    session_id: str
    blocked_on: str
    since: float
    escalated: bool = False
    decision_id: str | None = None
    factory_id: str | None = None


def session_subject(session_id: str) -> str:
    """:returns: The event subject of a session, e.g. ``"session:<id>"``."""
    return f"session:{session_id}"


async def prior_escalation(
    app_state: Any, factory_id: str, episode: BlockedEpisode
) -> FactoryEvent | None:
    """
    Find an earlier ``needs_human`` escalation of the same blocked episode.

    The in-memory episode is lost on a server roll, so the event log decides:
    the newest escalation of this session for the same prompt counts unless a
    ``needs_human.cleared`` of the session followed it or its decision expired
    (both mean the episode it was for ended). Never raises.

    :param app_state: ``app.state`` carrying the factory deps.
    :param factory_id: The session's factory.
    :param episode: The episode about to be escalated.
    :returns: That event, or ``None`` when this is a new episode.
    """
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps):
        return None
    store = deps.factory_store
    since_ms = int((episode.since - BLOCKED_DEDUPE_LOOKBACK_S) * 1000)
    try:
        found = await asyncio.to_thread(
            store.find_events,
            factory_id,
            [NEEDS_HUMAN_KIND, NEEDS_HUMAN_CLEARED_KIND],
            since_ms,
            session_subject(episode.session_id),
        )
        cleared_ms = max((e.ts for e in found if e.kind == NEEDS_HUMAN_CLEARED_KIND), default=None)
        for event in reversed(found):
            data = event.data or {}
            if event.kind != NEEDS_HUMAN_KIND or data.get("reason") != "blocked":
                continue
            if cleared_ms is not None and cleared_ms >= event.ts:
                return None
            if data.get("blocked_on") != episode.blocked_on:
                continue
            decision_id = data.get("decision_id")
            decision = (
                await asyncio.to_thread(store.get_decision, decision_id) if decision_id else None
            )
            return None if decision is not None and decision.status == "expired" else event
    except Exception:  # noqa: BLE001 - when unsure, escalate (a repeat beats silence)
        _logger.warning("factory: could not check earlier escalations", exc_info=True)
    return None


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
        self._tasks: set[asyncio.Task[object]] = set()

    def wants(self, conv: Conversation, status: str, data: dict[str, Any]) -> bool:
        """:returns: Whether this edge may need work (cheap, no I/O)."""
        if status in _BUSY_STATUSES and _blocked_on(status, data.get("blocked_on")) is None:
            return conv.id in self._blocked
        if status not in _BUSY_STATUSES and status not in _TERMINAL_STATUSES:
            return False
        # A top-level session without the label is not a member; a sub-agent
        # may still inherit membership from its root.
        return not is_top_level(conv) or bool(conv.labels.get(LABEL_ID))

    def spawn(self, coro: Coroutine[Any, Any, object]) -> None:
        """Run *coro* as a tracked background task."""
        task = asyncio.get_running_loop().create_task(coro, name="factory-status-edge")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _track_blocked(self, session_id: str, blocked_on: str | None) -> BlockedEpisode | None:
        """:returns: The session's open episode after this edge, if any."""
        if blocked_on is None:
            self._end_episode(self._blocked.pop(session_id, None))
            return None
        episode = self._blocked.get(session_id)
        if episode is None:
            episode = BlockedEpisode(
                session_id=session_id, blocked_on=blocked_on, since=self._clock()
            )
            self._blocked[session_id] = episode
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

    def drop_episode(self, episode: BlockedEpisode | None) -> None:
        """Forget *episode* (gone or not a member) unless a newer edge replaced it."""
        if episode is not None and self._blocked.get(episode.session_id) is episode:
            del self._blocked[episode.session_id]
            self._end_episode(episode)

    def _end_episode(self, episode: BlockedEpisode | None) -> None:
        """
        Log the end of an escalated episode and expire its decision.

        The ``needs_human.cleared`` event tells :func:`prior_escalation` that a
        later block on the same prompt is a new episode, whatever happened to
        the decision (answered, withdrawn or never stored).
        """
        if episode is None:
            return
        try:
            if episode.escalated and episode.factory_id:
                self.spawn(
                    events.emit(
                        self._app_state,
                        episode.factory_id,
                        NEEDS_HUMAN_CLEARED_KIND,
                        subject=session_subject(episode.session_id),
                        from_id=episode.session_id,
                        summary=f"No longer waiting on a human: {episode.blocked_on}",
                        data={
                            "blocked_on": episode.blocked_on,
                            "decision_id": episode.decision_id,
                        },
                        ts=int(self._clock() * 1000),
                    )
                )
            if episode.decision_id is not None:
                self.spawn(server_decisions.expire_decision(self._app_state, episode.decision_id))
        except RuntimeError:
            _logger.debug("factory: no loop to end the episode of %s", episode.session_id)

    def prune_escalated(self, watched: set[str]) -> None:
        """
        Drop escalated episodes of sessions no longer watched.

        A closed or vanished session sends no further edge to clear its
        episode. Unescalated ones stay: the supervisor checks them when due.

        :param watched: Ids of the open members and sub-agents just loaded.
        """
        for sid in [s for s, ep in self._blocked.items() if ep.escalated and s not in watched]:
            self._end_episode(self._blocked.pop(sid))

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
        episode = self._track_blocked(conv.id, _blocked_on(status, data.get("blocked_on")))
        if episode is None and status not in _TERMINAL_STATUSES:
            return
        deps = getattr(self._app_state, FACTORY_STATE_ATTR, None)
        factory_id = (
            await asyncio.to_thread(resolve_factory_id, conv, deps.conversation_store)
            if isinstance(deps, FactoryDeps)
            else None
        )
        if not factory_id:
            self.drop_episode(episode)
            return
        if status == "failed":
            self._alert_failed(conv, factory_id, data)
        elif status == "idle":
            self._notify_reply(conv, factory_id, data)

    def relayed_edge(
        self,
        session_id: str,
        status: str,
        blocked_on: object = None,
        error_message: str | None = None,
    ) -> None:
        """
        Apply one relayed runner ``session.status`` edge (sync, no I/O).

        Blocked bookkeeping happens here, in arrival order. A new episode or
        an undebounced ``failed`` edge spawns a task that loads the session
        and checks membership. ``idle`` only closes an episode; it never
        sends a reply notice (see the module docstring).

        :param session_id: The session whose runner published the edge.
        :param status: ``running`` / ``waiting`` / ``idle`` / ``failed``.
        :param blocked_on: The edge's ``blocked_on`` field, if any.
        :param error_message: The runner's error message on a ``failed`` edge.
        """
        if status not in _BUSY_STATUSES and status not in _TERMINAL_STATUSES:
            return
        known = session_id in self._blocked
        episode = self._track_blocked(session_id, _blocked_on(status, blocked_on))
        if episode is not None and not known:
            self.spawn(self._check_relayed(session_id, status, episode, error_message))
        elif status == "failed" and not self._failed_debounced(session_id):
            self.spawn(self._check_relayed(session_id, status, None, error_message))

    async def _check_relayed(
        self,
        session_id: str,
        status: str,
        episode: BlockedEpisode | None,
        error_message: str | None,
    ) -> None:
        try:
            deps = getattr(self._app_state, FACTORY_STATE_ATTR, None)
            if not isinstance(deps, FactoryDeps):
                self.drop_episode(episode)
                return
            store = deps.conversation_store
            conv = await asyncio.to_thread(store.get_conversation, session_id)
            factory_id = await asyncio.to_thread(resolve_factory_id, conv, store) if conv else None
            if conv is None or not factory_id:
                self.drop_episode(episode)
                return
            if status == "failed":
                self._alert_failed(conv, factory_id, {"error": error_message})
        except Exception:  # noqa: BLE001 - a factory reaction must never break the relay
            _logger.warning(
                "factory: relayed status edge handling failed for %s", session_id, exc_info=True
            )

    def _failed_debounced(self, session_id: str) -> bool:
        last = self._failed_alerted_at.get(session_id)
        return last is not None and self._clock() - last < FAILED_ALERT_DEBOUNCE_S

    def _alert_failed(self, conv: Conversation, factory_id: str, data: dict[str, Any]) -> None:
        if self._failed_debounced(conv.id):
            return
        now = self._clock()
        target = alert_target_id(conv, factory_id)
        if target is None:
            return
        self._failed_alerted_at[conv.id] = now
        self._prune_debounce(now)
        from omnigent.server.factory.mailer import get_mailer

        detail = _failure_detail(data)
        get_mailer(self._app_state).notify(
            target, failed_alert_text(conv, detail), factory_id=factory_id
        )
        self.spawn(
            events.emit(
                self._app_state,
                factory_id,
                "alert.failed",
                severity="warning",
                from_id=conv.id,
                to_id=target,
                subject=session_subject(conv.id),
                summary=f'"{display_title(conv.title, conv.id)}" failed: {detail}',
                data={"detail": detail},
                ts=int(now * 1000),
            )
        )

    def _notify_reply(self, conv: Conversation, factory_id: str, data: dict[str, Any]) -> None:
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
        mailer.notify(sender_id, reply_notice_text(conv, last_output), factory_id=factory_id)
        self.spawn(
            events.emit(
                self._app_state,
                factory_id,
                "notice.reply",
                from_id=conv.id,
                to_id=sender_id,
                subject=session_subject(conv.id),
                summary=f'"{display_title(conv.title, conv.id)}" finished the requested turn'
                + (f": {excerpt(last_output, 200)}" if last_output else ""),
                ts=int(self._clock() * 1000),
            )
        )

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


# The relay has no ``app.state``; supervision registers its notifier here.
_relay_notifier: FactoryNotifier | None = None


def register_relay_notifier(notifier: FactoryNotifier) -> None:
    """
    Make *notifier* the target of :func:`schedule_factory_relayed_status`.

    :param notifier: The notifier of the app whose supervision just started.
    """
    global _relay_notifier
    _relay_notifier = notifier


def unregister_relay_notifier(notifier: FactoryNotifier) -> None:
    """
    Stop feeding relayed edges to *notifier* (a no-op if another replaced it).

    :param notifier: The notifier registered by :func:`register_relay_notifier`.
    """
    global _relay_notifier
    if _relay_notifier is notifier:
        _relay_notifier = None


def schedule_factory_relayed_status(
    session_id: str,
    status: str,
    blocked_on: object = None,
    error_message: str | None = None,
) -> None:
    """
    Relay hook: feed one runner ``session.status`` edge to the factory.

    Returns at once (no I/O) and never raises; a no-op unless
    ``OMNIGENT_FACTORY_ENABLED=1`` and supervision is running.

    :param session_id: The session whose runner published the edge.
    :param status: The relayed status, e.g. ``"running"``.
    :param blocked_on: The event's raw ``blocked_on`` field, if any.
    :param error_message: The runner's error message on a ``failed`` edge.
    """
    try:
        notifier = _relay_notifier
        if notifier is None or not factory_enabled():
            return
        notifier.relayed_edge(session_id, status, blocked_on, error_message)
    except Exception:  # noqa: BLE001 - the relay must not fail on the factory
        _logger.warning(
            "factory: could not apply relayed status edge for %s", session_id, exc_info=True
        )


__all__ = [
    "BlockedEpisode",
    "FactoryNotifier",
    "alert_target_id",
    "get_notifier",
    "on_status_edge",
    "prior_escalation",
    "register_relay_notifier",
    "schedule_factory_relayed_status",
    "schedule_factory_status_edge",
    "unregister_relay_notifier",
]
