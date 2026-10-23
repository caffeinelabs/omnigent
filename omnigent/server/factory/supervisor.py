"""FactorySupervisor: the non-LLM root of factory liveness.

Runs in the server (started by :func:`~omnigent.server.factory.start_factory_services`
when ``OMNIGENT_FACTORY_ENABLED=1``) and survives every agent, pod and runner.
Each tick it reads the top-level members of every factory and applies:

- **S1 keepalive**: ``GET /health`` through an active member's live runner
  tunnel every ``OMNIGENT_FACTORY_KEEPALIVE_S``. Any tunnel request counts as
  runner activity, so this keeps idle sandboxes alive for zero tokens.
- **S2 heartbeat**: an LLM nudge to an active, non-running member idle for at
  least its ``heartbeat_s`` label. Delivery wakes or relaunches the session;
  ``last_beat`` is written to a label so a server restart keeps the cadence.
  Three failed beats within an hour mark it ``health=dead`` and raise a
  ``[NEEDS HUMAN]`` (at most hourly).
- **S3 stuck**: one alert per episode for a member or sub-agent that reads
  ``running`` with no progress for ``OMNIGENT_FACTORY_STUCK_S``. Never cancels.
- **S5 blocked**: a session parked on ``blocked_on`` (see
  :mod:`~omnigent.server.factory.notify`) for ``OMNIGENT_FACTORY_BLOCKED_ESCALATE_S``
  is escalated to the factory root once.

State beyond the labels is in memory on one replica.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from omnigent.factory import (
    HEALTH_DEAD,
    HEALTH_OK,
    LABEL_HEALTH,
    LABEL_HEARTBEAT_S,
    LABEL_ID,
    LABEL_LAST_BEAT,
    LABEL_STATE,
    LABEL_SUPERVISOR,
    MAX_HEARTBEAT_S,
    MIN_HEARTBEAT_S,
    STATE_ACTIVE,
)
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.delivery import DeliveryOutcome, deliver_message
from omnigent.server.factory.envelope import display_title
from omnigent.server.factory.mailer import get_mailer
from omnigent.server.factory.membership import is_top_level, resolve_factory_id
from omnigent.server.factory.notify import (
    NOTIFIER_STATE_ATTR,
    FactoryNotifier,
    alert_target_id,
    get_notifier,
    iso_utc,
    parse_iso,
)
from omnigent.server.routes._sessions.helpers import _session_status_from_cache
from omnigent.server.routes._sessions.orchestration import _get_runner_client
from omnigent.util.session_lifecycle import is_session_closed

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.stores import ConversationStore

_logger = logging.getLogger(__name__)

TICK_ENV = "OMNIGENT_FACTORY_TICK_S"
KEEPALIVE_ENV = "OMNIGENT_FACTORY_KEEPALIVE_S"
STUCK_ENV = "OMNIGENT_FACTORY_STUCK_S"
BLOCKED_ESCALATE_ENV = "OMNIGENT_FACTORY_BLOCKED_ESCALATE_S"
DEFAULT_TICK_S = 60.0
DEFAULT_KEEPALIVE_S = 600.0
DEFAULT_STUCK_S = 5400.0
DEFAULT_BLOCKED_ESCALATE_S = 1800.0

KEEPALIVE_TIMEOUT_S = 10.0
DEAD_AFTER_FAILURES = 3
FAILURE_WINDOW_S = 3600.0
DEAD_ALERT_INTERVAL_S = 3600.0
BEAT_RETRY_S = 300.0
"""Wait after a failed heartbeat before the next attempt (each may relaunch a sandbox)."""

# Sub-agents checked for stalls per member; a PL runs well under this many.
_MAX_CHILDREN_PER_MEMBER = 200

SUPERVISOR_STATE_ATTR = "factory_supervisor"
"""``app.state`` attribute holding the running :class:`FactorySupervisor`."""

Deliver = Callable[..., Awaitable[DeliveryOutcome]]
Sleep = Callable[[float], Awaitable[Any]]


def _env_seconds(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if value <= 0:
        _logger.warning("Ignoring invalid %s=%r; using %s", name, raw, default)
        return default
    return value


@dataclass(frozen=True)
class SupervisorConfig:
    """
    Supervisor timings in seconds.

    :param tick_s: Interval between ticks.
    :param keepalive_s: Minimum interval between keepalives of one session.
    :param stuck_s: ``running`` with no progress this long is an alert.
    :param blocked_escalate_s: Parked on ``blocked_on`` this long is escalated.
    """

    tick_s: float = DEFAULT_TICK_S
    keepalive_s: float = DEFAULT_KEEPALIVE_S
    stuck_s: float = DEFAULT_STUCK_S
    blocked_escalate_s: float = DEFAULT_BLOCKED_ESCALATE_S

    @classmethod
    def from_env(cls) -> SupervisorConfig:
        """:returns: Timings from the ``OMNIGENT_FACTORY_*_S`` env vars, else defaults."""
        return cls(
            tick_s=_env_seconds(TICK_ENV, DEFAULT_TICK_S),
            keepalive_s=_env_seconds(KEEPALIVE_ENV, DEFAULT_KEEPALIVE_S),
            stuck_s=_env_seconds(STUCK_ENV, DEFAULT_STUCK_S),
            blocked_escalate_s=_env_seconds(BLOCKED_ESCALATE_ENV, DEFAULT_BLOCKED_ESCALATE_S),
        )


def heartbeat_text(n: int, now: float) -> str:
    """:returns: The ``[Factory heartbeat #<n> · <iso>]`` nudge."""
    return (
        f"[Factory heartbeat #{n} · {iso_utc(now)}] "
        'Run load_skill("factory-heartbeat") and follow it. Reply only if something changed.'
    )


def format_duration(seconds: float) -> str:
    """:returns: e.g. ``"1h32m"``, ``"2d3h"`` or ``"45m"``."""
    minutes = max(0, int(seconds // 60))
    days, rem = divmod(minutes, 24 * 60)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days}d{hours}h"
    if hours:
        return f"{hours}h{mins:02d}m"
    return f"{mins}m"


def stuck_alert_text(conv: Conversation, idle_s: float) -> str:
    """:returns: The ``[Factory alert · … running with no progress …]`` text."""
    return (
        f'[Factory alert · "{display_title(conv.title, conv.id)}" ({conv.id}) running with no '
        f"progress for {format_duration(idle_s)}. Inspect with sys_session_get_history; "
        "nudge or cancel if wedged.]"
    )


def dead_alert_text(conv: Conversation, reason: str) -> str:
    """:returns: The ``[NEEDS HUMAN · relaunch … failed 3× …]`` text."""
    return (
        f'[NEEDS HUMAN · relaunch of "{display_title(conv.title, conv.id)}" failed '
        f"{DEAD_AFTER_FAILURES}× ({reason})]"
    )


def blocked_escalation_text(title: str, since: float, blocked_on: str) -> str:
    """:returns: The ``[NEEDS HUMAN · "<title>" waiting since <ts>: <blocked_on>]`` text."""
    return f'[NEEDS HUMAN · "{title}" waiting since {iso_utc(since)}: {blocked_on}]'


def _heartbeat_interval(conv: Conversation) -> int | None:
    """:returns: The beat interval clamped to the allowed range, or ``None`` when off."""
    try:
        value = int(conv.labels.get(LABEL_HEARTBEAT_S, "0"))
    except ValueError:
        return None
    if value <= 0:
        return None
    return min(max(value, MIN_HEARTBEAT_S), MAX_HEARTBEAT_S)


def _is_running(conv: Conversation) -> bool:
    return _session_status_from_cache(conv.id, conv.live_status) == "running"


def _load_members(store: ConversationStore) -> list[Conversation]:
    """:returns: Open top-level sessions of every factory, oldest first."""
    ids = store.list_conversation_ids_with_label(LABEL_ID)
    rows = store.get_conversations(ids)
    return [
        rows[i]
        for i in ids
        if i in rows
        and is_top_level(rows[i])
        and rows[i].labels.get(LABEL_ID)
        and not is_session_closed(rows[i].labels, rows[i].title)
    ]


def _load_children(store: ConversationStore, members: list[Conversation]) -> list[Conversation]:
    children: list[Conversation] = []
    for member in members:
        page = store.list_conversations(
            limit=_MAX_CHILDREN_PER_MEMBER,
            kind="sub_agent",
            parent_conversation_id=member.id,
            order="asc",
        )
        children.extend(c for c in page.data if not is_session_closed(c.labels, c.title))
    return children


class FactorySupervisor:
    """
    Periodic, deterministic supervision of every factory on this server.

    :meth:`tick` runs one pass and is public so tests and operators can force
    one; :meth:`start` runs it every ``tick_s`` until :meth:`stop`.
    Heartbeat deliveries run as tracked background tasks (a relaunch can take
    minutes) with at most one in flight per session.
    """

    def __init__(
        self,
        app_state: Any,
        *,
        config: SupervisorConfig | None = None,
        deliver: Deliver | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        """
        :param app_state: ``app.state`` with the factory deps registered.
        :param config: Timings; defaults to :meth:`SupervisorConfig.from_env`.
        :param deliver: Heartbeat delivery; defaults to
            :func:`~omnigent.server.factory.delivery.deliver_message`.
        :param clock: Wall clock in epoch seconds (injectable for tests).
        :param sleep: Awaitable sleep between ticks (injectable for tests).
        """
        self._app_state = app_state
        self.config = config or SupervisorConfig.from_env()
        self._deliver = deliver
        self._clock = clock
        self._sleep = sleep
        self._loop_task: asyncio.Task[None] | None = None
        self._beat_tasks: dict[str, asyncio.Task[None]] = {}
        self._keepalive_at: dict[str, float] = {}
        self._beat_counts: dict[str, int] = {}
        self._beat_failures: dict[str, deque[float]] = {}
        self._last_beat_failure: dict[str, float] = {}
        self._dead_alerted_at: dict[str, float] = {}
        self._stuck_alerted: dict[str, int] = {}

    # ── lifecycle ───────────────────────────────────────────────

    def start(self) -> None:
        """Start ticking every ``tick_s`` (the first tick waits one interval)."""
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.get_running_loop().create_task(
                self._run(), name="factory-supervisor"
            )

    async def _run(self) -> None:
        while True:
            await self._sleep(self.config.tick_s)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("factory supervisor tick failed")

    async def stop(self) -> None:
        """Stop ticking and cancel in-flight heartbeats."""
        tasks = [t for t in (self._loop_task, *self._beat_tasks.values()) if t is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._loop_task = None
        self._beat_tasks.clear()

    async def drain(self) -> None:
        """Wait for in-flight heartbeat deliveries (tests and forced ticks)."""
        while self._beat_tasks:
            await asyncio.gather(*list(self._beat_tasks.values()), return_exceptions=True)
            # A gather of finished tasks returns without yielding; let their
            # done-callbacks prune the map.
            await asyncio.sleep(0)

    # ── one pass ────────────────────────────────────────────────

    def _store(self) -> ConversationStore | None:
        deps = getattr(self._app_state, FACTORY_STATE_ATTR, None)
        return deps.conversation_store if isinstance(deps, FactoryDeps) else None

    async def tick(self) -> None:
        """Run S1-S3 and the blocked escalation once over every factory."""
        store = self._store()
        if store is None:
            return
        now = self._clock()
        members = await asyncio.to_thread(_load_members, store)
        await asyncio.gather(*(self._keepalive(m, now) for m in members))
        for member in members:
            self._maybe_beat(member, now)
        children = await asyncio.to_thread(_load_children, store, members)
        self._check_stuck(members, children, now)
        await self._escalate_blocked(store, now)
        self._forget_departed({m.id for m in members}, {c.id for c in children})

    # ── S1 keepalive ────────────────────────────────────────────

    async def _keepalive(self, conv: Conversation, now: float) -> None:
        if conv.labels.get(LABEL_STATE) != STATE_ACTIVE:
            return
        last = self._keepalive_at.get(conv.id)
        if last is not None and now - last < self.config.keepalive_s:
            return
        try:
            client = await _get_runner_client(
                conv.id, getattr(self._app_state, "runner_router", None), conversation=conv
            )
            if client is None:
                return
            self._keepalive_at[conv.id] = now
            await client.get("/health", timeout=KEEPALIVE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - a missed keepalive is retried next interval
            _logger.debug("factory keepalive of %s failed: %s", conv.id, exc)

    # ── S2 heartbeat ────────────────────────────────────────────

    def _beat_due(self, conv: Conversation, now: float) -> bool:
        if conv.labels.get(LABEL_STATE) != STATE_ACTIVE or conv.id in self._beat_tasks:
            return False
        interval = _heartbeat_interval(conv)
        if interval is None or _is_running(conv):
            return False
        last_failure = self._last_beat_failure.get(conv.id)
        if last_failure is not None and now - last_failure < BEAT_RETRY_S:
            return False
        last_beat = parse_iso(conv.labels.get(LABEL_LAST_BEAT)) or 0.0
        return now - max(last_beat, float(conv.updated_at)) >= interval

    def _maybe_beat(self, conv: Conversation, now: float) -> None:
        if not self._beat_due(conv, now):
            return
        task = asyncio.get_running_loop().create_task(
            self._beat(conv, now), name=f"factory-heartbeat-{conv.id}"
        )
        self._beat_tasks[conv.id] = task
        task.add_done_callback(lambda t, sid=conv.id: self._beat_finished(sid, t))

    def _beat_finished(self, session_id: str, task: asyncio.Task[None]) -> None:
        if self._beat_tasks.get(session_id) is task:
            del self._beat_tasks[session_id]

    async def _beat(self, conv: Conversation, now: float) -> None:
        n = self._beat_counts.get(conv.id, 0) + 1
        deliver = self._deliver or deliver_message
        try:
            outcome = await deliver(self._app_state, conv.id, heartbeat_text(n, now))
        except Exception as exc:  # noqa: BLE001 - counted as a failed beat
            _logger.warning("factory heartbeat to %s crashed", conv.id, exc_info=True)
            outcome = DeliveryOutcome(False, f"internal_error: {type(exc).__name__}")
        try:
            if outcome.delivered:
                await self._beat_delivered(conv, n)
            else:
                await self._beat_failed(conv, outcome.reason or "unknown")
        except Exception:  # noqa: BLE001 - bookkeeping must not kill the task silently
            _logger.warning("factory heartbeat bookkeeping for %s failed", conv.id, exc_info=True)

    async def _beat_delivered(self, conv: Conversation, n: int) -> None:
        self._beat_counts[conv.id] = n
        self._beat_failures.pop(conv.id, None)
        self._last_beat_failure.pop(conv.id, None)
        labels = {LABEL_LAST_BEAT: iso_utc(self._clock())}
        if conv.labels.get(LABEL_HEALTH) == HEALTH_DEAD:
            # It answered again, so it is no longer dead.
            labels[LABEL_HEALTH] = HEALTH_OK
            self._dead_alerted_at.pop(conv.id, None)
        store = self._store()
        if store is not None:
            await asyncio.to_thread(store.set_labels, conv.id, labels)
        _logger.info("factory heartbeat #%d delivered to %s", n, conv.id)

    async def _beat_failed(self, conv: Conversation, reason: str) -> None:
        now = self._clock()
        self._last_beat_failure[conv.id] = now
        window = self._beat_failures.setdefault(conv.id, deque())
        while window and window[0] <= now - FAILURE_WINDOW_S:
            window.popleft()
        window.append(now)
        _logger.warning(
            "factory heartbeat to %s failed (%s); %d failure(s) in the last hour",
            conv.id,
            reason,
            len(window),
        )
        if len(window) < DEAD_AFTER_FAILURES:
            return
        alerted = self._dead_alerted_at.get(conv.id)
        if alerted is not None and now - alerted < DEAD_ALERT_INTERVAL_S:
            return
        self._dead_alerted_at[conv.id] = now
        store = self._store()
        if store is not None:
            await asyncio.to_thread(store.set_labels, conv.id, {LABEL_HEALTH: HEALTH_DEAD})
        factory_id = conv.labels.get(LABEL_ID)
        targets = dict.fromkeys(t for t in (conv.labels.get(LABEL_SUPERVISOR), factory_id) if t)
        targets.pop(conv.id, None)
        mailer = get_mailer(self._app_state)
        for target in targets:
            mailer.notify(target, dead_alert_text(conv, reason))

    # ── S3 stuck ────────────────────────────────────────────────

    def _check_stuck(
        self,
        members: list[Conversation],
        children: list[Conversation],
        now: float,
    ) -> None:
        roots = {m.id: m.labels[LABEL_ID] for m in members}
        mailer = None
        for conv in (*members, *children):
            stalled_s = now - float(conv.updated_at)
            if not _is_running(conv) or stalled_s < self.config.stuck_s:
                self._stuck_alerted.pop(conv.id, None)
                continue
            # One alert per episode: progress moves updated_at and starts a new one.
            if self._stuck_alerted.get(conv.id) == conv.updated_at:
                continue
            self._stuck_alerted[conv.id] = conv.updated_at
            factory_id = roots.get(conv.id) or roots.get(conv.parent_conversation_id or "")
            target = alert_target_id(conv, factory_id) if factory_id else None
            if target is None:
                continue
            mailer = mailer or get_mailer(self._app_state)
            mailer.notify(target, stuck_alert_text(conv, stalled_s))

    # ── S5 blocked on a human ───────────────────────────────────

    async def _escalate_blocked(self, store: ConversationStore, now: float) -> None:
        notifier = getattr(self._app_state, NOTIFIER_STATE_ATTR, None)
        if not isinstance(notifier, FactoryNotifier):
            return
        for episode in notifier.blocked_due(now, self.config.blocked_escalate_s):
            episode.escalated = True
            conv = await asyncio.to_thread(store.get_conversation, episode.session_id)
            factory_id = await asyncio.to_thread(resolve_factory_id, conv, store) if conv else None
            if conv is None or not factory_id:
                notifier.forget_blocked(episode.session_id)
                continue
            # A parked root shows its own prompt; typing into it could answer that prompt.
            if factory_id == conv.id:
                continue
            get_mailer(self._app_state).notify(
                factory_id,
                blocked_escalation_text(
                    display_title(conv.title, conv.id), episode.since, episode.blocked_on
                ),
            )

    # ── housekeeping ────────────────────────────────────────────

    def _forget_departed(self, member_ids: set[str], child_ids: set[str]) -> None:
        for state in (
            self._keepalive_at,
            self._beat_counts,
            self._beat_failures,
            self._last_beat_failure,
            self._dead_alerted_at,
        ):
            for sid in [s for s in state if s not in member_ids]:
                del state[sid]
        watched = member_ids | child_ids
        for sid in [s for s in self._stuck_alerted if s not in watched]:
            del self._stuck_alerted[sid]


def start_supervision(app_state: Any) -> Callable[[], Awaitable[None]]:
    """
    Start the supervisor and the status-edge notifier on *app_state*.

    :param app_state: ``app.state`` with the factory deps registered.
    :returns: An async callable that stops both and detaches them.
    """
    notifier = get_notifier(app_state)
    supervisor = FactorySupervisor(app_state)
    setattr(app_state, SUPERVISOR_STATE_ATTR, supervisor)
    supervisor.start()
    _logger.info("factory supervisor started (%s)", supervisor.config)

    async def _stop() -> None:
        await supervisor.stop()
        await notifier.close()
        if getattr(app_state, SUPERVISOR_STATE_ATTR, None) is supervisor:
            setattr(app_state, SUPERVISOR_STATE_ATTR, None)
        if getattr(app_state, NOTIFIER_STATE_ATTR, None) is notifier:
            setattr(app_state, NOTIFIER_STATE_ATTR, None)

    return _stop


def get_supervisor(app_state: Any) -> FactorySupervisor | None:
    """:returns: The running supervisor of *app_state*, if one was started."""
    supervisor = getattr(app_state, SUPERVISOR_STATE_ATTR, None)
    return supervisor if isinstance(supervisor, FactorySupervisor) else None


__all__ = [
    "FactorySupervisor",
    "SupervisorConfig",
    "get_supervisor",
    "start_supervision",
]
