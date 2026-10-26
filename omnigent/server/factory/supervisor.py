"""FactorySupervisor: the non-LLM root of factory liveness.

Runs in the server (started by :func:`~omnigent.server.factory.start_factory_services`
when ``OMNIGENT_FACTORY_ENABLED=1``) and survives every agent, pod and runner.
Each tick it reads the top-level members of every factory and applies:

- **S1 keepalive**: ``GET /health`` through an active member's live runner
  tunnel every ``OMNIGENT_FACTORY_KEEPALIVE_S``. Any tunnel request counts as
  runner activity, so this keeps idle sandboxes alive for zero tokens. A paused
  or done PL is kept alive too while an open PR of its project has a live
  factory preview (unless its ``keep_previews`` label is ``0``).
- **S2 heartbeat**: an LLM nudge to an active, non-running member idle for at
  least its ``heartbeat_s`` label. Delivery wakes or relaunches the session;
  ``last_beat`` and ``beat_n`` are written to labels so a server restart keeps
  the cadence and the numbering. With quiet beats on, a due beat nothing
  needs is skipped (see :mod:`~omnigent.server.factory.quiet_beats`).
  Three failed beats within an hour mark it ``health=dead``, raise a
  ``[NEEDS HUMAN]`` (at most hourly, stamped in ``dead_alerted_at``) and open
  one decision for the human per episode.
- **S3 stuck**: one alert per episode for a member or sub-agent that reads
  ``running`` with no progress for ``OMNIGENT_FACTORY_STUCK_S``. Never cancels.
- **S5 blocked**: a session parked on ``blocked_on`` (see
  :mod:`~omnigent.server.factory.notify`) for ``OMNIGENT_FACTORY_BLOCKED_ESCALATE_S``
  is escalated once (also across a server roll): to the factory root, or to
  the root's supervisor when the root itself is parked.
- **S9 preview TTL**: a ready preview no human looked at for
  ``OMNIGENT_FACTORY_PREVIEW_TTL_H`` gets one park notice to its PL per window.

Each tick also logs what it saw to the factory event log: heartbeats, alerts,
and the PL ↔ sub-agent traffic (``dispatch``, ``task.done|failed``) read off
the child list. Other state is in memory on one replica.
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
    LABEL_BEAT_N,
    LABEL_DEAD_ALERTED_AT,
    LABEL_HEALTH,
    LABEL_HEARTBEAT_S,
    LABEL_ID,
    LABEL_KEEP_PREVIEWS,
    LABEL_LAST_BEAT,
    LABEL_PROJECT,
    LABEL_STATE,
    LABEL_SUPERVISOR,
    MAX_HEARTBEAT_S,
    MIN_HEARTBEAT_S,
    STATE_ACTIVE,
)
from omnigent.server.factory import events, previews, quiet_beats, server_decisions, status
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.delivery import DeliveryOutcome, deliver_message
from omnigent.server.factory.envelope import display_title
from omnigent.server.factory.mailer import get_mailer
from omnigent.server.factory.membership import is_top_level, resolve_factory_id
from omnigent.server.factory.notify import (
    NOTIFIER_STATE_ATTR,
    BlockedEpisode,
    FactoryNotifier,
    alert_target_id,
    get_notifier,
    iso_utc,
    parse_iso,
    prior_escalation,
    register_relay_notifier,
    session_subject,
    unregister_relay_notifier,
)
from omnigent.server.routes._sessions.helpers import _session_status_from_cache
from omnigent.server.routes._sessions.orchestration import _get_runner_client
from omnigent.util.session_lifecycle import is_session_closed

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.stores import ConversationStore
    from omnigent.stores.factory_store import FactoryStore

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
DISPATCH_RECENT_S = 900.0
"""A sub-agent first seen this soon after its creation is logged as a new dispatch."""
PREVIEW_TTL_CHECK_S = 600.0

# Newest sub-agents checked for stalls per member; a PL runs well under this many.
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
    :param quiet_beats: Skip due beats nothing needs; :meth:`from_env` turns it
        on unless ``OMNIGENT_FACTORY_QUIET_BEATS=0``.
    :param preview_ttl_h: Hours a ready preview may go without human attention
        before its PL is asked to park it; ``None`` turns parking off.
    """

    tick_s: float = DEFAULT_TICK_S
    keepalive_s: float = DEFAULT_KEEPALIVE_S
    stuck_s: float = DEFAULT_STUCK_S
    blocked_escalate_s: float = DEFAULT_BLOCKED_ESCALATE_S
    quiet_beats: bool = False
    preview_ttl_h: float | None = previews.DEFAULT_PREVIEW_TTL_H

    @classmethod
    def from_env(cls) -> SupervisorConfig:
        """:returns: Timings from the ``OMNIGENT_FACTORY_*`` env vars, else defaults."""
        return cls(
            tick_s=_env_seconds(TICK_ENV, DEFAULT_TICK_S),
            keepalive_s=_env_seconds(KEEPALIVE_ENV, DEFAULT_KEEPALIVE_S),
            stuck_s=_env_seconds(STUCK_ENV, DEFAULT_STUCK_S),
            blocked_escalate_s=_env_seconds(BLOCKED_ESCALATE_ENV, DEFAULT_BLOCKED_ESCALATE_S),
            quiet_beats=quiet_beats.quiet_beats_enabled(),
            preview_ttl_h=previews.preview_ttl_h(),
        )


def heartbeat_text(n: int, now: float, delta: str | None = None) -> str:
    """
    :param delta: What changed since the last beat, e.g. ``"2 changes since #11: • …"``.
    :returns: The ``[Factory heartbeat #<n> · <iso>][ <delta>]`` nudge.
    """
    head = f"[Factory heartbeat #{n} · {iso_utc(now)}]"
    if delta:
        head = f"{head} {delta}\n"
    else:
        head += " "
    return (
        f"{head}"
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


def _int_label(conv: Conversation, key: str) -> int:
    try:
        return int(conv.labels.get(key) or 0)
    except ValueError:
        return 0


def _factory_store(app_state: Any) -> FactoryStore | None:
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    return deps.factory_store if isinstance(deps, FactoryDeps) else None


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
    """:returns: Open sub-agents of *members*, newest first per member."""
    children: list[Conversation] = []
    for member in members:
        # Newest first: re-dispatches (``<task>-r<k>``) must not fall off the page.
        page = store.list_conversations(
            limit=_MAX_CHILDREN_PER_MEMBER,
            kind="sub_agent",
            parent_conversation_id=member.id,
            order="desc",
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
        self._beat_failures: dict[str, deque[float]] = {}
        self._last_beat_failure: dict[str, float] = {}
        self._stuck_alerted: dict[str, int] = {}
        self._quiet_until: dict[str, float] = {}
        self._skips: dict[str, dict[str, int]] = {}
        self._skips_since: float | None = None
        self._child_status: dict[str, str] = {}
        self._ttl_checked_at: float | None = None

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
            except Exception as exc:
                _logger.exception("factory supervisor tick failed")
                status.set_status("supervisor", ok=False, error=type(exc).__name__)

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
        """Run S1-S3, the blocked escalation and the preview TTL once over every factory."""
        store = self._store()
        if store is None:
            return
        now = self._clock()
        members = await asyncio.to_thread(_load_members, store)
        children = await asyncio.to_thread(_load_children, store, members)
        await asyncio.gather(*(self._keepalive(m, now) for m in members))
        await self._track_children(members, children, now)
        stalled = self._stalled_parents(children, now)
        for member in members:
            self._maybe_beat(member, now, member.id in stalled)
        await self._check_stuck(members, children, now)
        await self._escalate_blocked(store, now)
        await self._park_idle_previews(members, now)
        await self._report_skips(now)
        self._forget_departed({m.id for m in members}, {c.id for c in children})
        status.set_status("supervisor", last_tick_at=int(now), ok=True, error=None)

    async def _emit(self, factory_id: str | None, kind: str, **kwargs: Any) -> None:
        if factory_id:
            ts = int(self._clock() * 1000)
            await events.emit(self._app_state, factory_id, kind, ts=ts, **kwargs)

    # ── S1 keepalive ────────────────────────────────────────────

    async def _holds_live_preview(self, conv: Conversation) -> bool:
        """A paused/done PL whose open PR still has a live preview keeps its pod."""
        project = conv.labels.get(LABEL_PROJECT)
        factory_id = conv.labels.get(LABEL_ID)
        store = _factory_store(self._app_state)
        if conv.labels.get(LABEL_KEEP_PREVIEWS) == "0" or not (project and factory_id and store):
            return False
        prs = await asyncio.to_thread(store.list_prs, factory_id, ["open"])
        return previews.has_live_preview(prs, project)

    async def _keepalive(self, conv: Conversation, now: float) -> None:
        last = self._keepalive_at.get(conv.id)
        if last is not None and now - last < self.config.keepalive_s:
            return
        try:
            if conv.labels.get(LABEL_STATE) != STATE_ACTIVE and not await self._holds_live_preview(
                conv
            ):
                return
            client = await _get_runner_client(
                conv.id, getattr(self._app_state, "runner_router", None), conversation=conv
            )
            if client is None:
                return
            self._keepalive_at[conv.id] = now
            await client.get("/health", timeout=KEEPALIVE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - a missed keepalive is retried next interval
            _logger.debug("factory keepalive of %s failed: %s", conv.id, exc)

    # ── PL ↔ sub-agent traffic ─────────────────────────────────

    async def _dispatch_logged(self, factory_id: str, child: Conversation) -> bool:
        store = _factory_store(self._app_state)
        if store is None:
            return True
        found = await asyncio.to_thread(
            store.find_events,
            factory_id,
            "dispatch",
            (int(child.created_at) - 60) * 1000,
            session_subject(child.id),
            limit=1,
        )
        return bool(found)

    async def _track_children(
        self, members: list[Conversation], children: list[Conversation], now: float
    ) -> None:
        """Log new sub-agents as ``dispatch`` and their running → idle/failed edges."""
        parents = {m.id: m for m in members}
        for child in children:
            parent = parents.get(child.parent_conversation_id or "")
            current = _session_status_from_cache(child.id, child.live_status)
            previous = self._child_status.get(child.id)
            self._child_status[child.id] = current
            if parent is None:
                continue
            factory_id = parent.labels.get(LABEL_ID)
            title = display_title(child.title, child.id)
            common: dict[str, Any] = {
                "project": parent.labels.get(LABEL_PROJECT),
                "from_id": parent.id,
                "to_id": child.id,
                "subject": session_subject(child.id),
            }
            task = " ".join((child.task_summary or "").split())
            if previous is None:
                if now - child.created_at > DISPATCH_RECENT_S or not factory_id:
                    continue
                if await self._dispatch_logged(factory_id, child):
                    continue
                await self._emit(
                    factory_id,
                    "dispatch",
                    summary=f"{title} · {task}" if task else title,
                    data={"agent": child.sub_agent_name, "task": task or None},
                    **common,
                )
            elif previous == "running" and current == "idle":
                await self._emit(
                    factory_id,
                    "task.done",
                    summary=f"{title} finished" + (f" · {task}" if task else ""),
                    **common,
                )
            elif previous != "failed" and current == "failed":
                await self._emit(
                    factory_id,
                    "task.failed",
                    severity="warning",
                    summary=f"{title} failed" + (f" · {task}" if task else ""),
                    **common,
                )

    def _stalled_parents(self, children: list[Conversation], now: float) -> set[str]:
        """Members with a sub-agent running without progress for half the stuck time."""
        half = self.config.stuck_s / 2
        return {
            c.parent_conversation_id
            for c in children
            if c.parent_conversation_id and _is_running(c) and now - c.updated_at >= half
        }

    # ── S2 heartbeat ────────────────────────────────────────────

    def _beat_due(self, conv: Conversation, now: float) -> bool:
        if conv.labels.get(LABEL_STATE) != STATE_ACTIVE or conv.id in self._beat_tasks:
            return False
        interval = _heartbeat_interval(conv)
        if interval is None or _is_running(conv):
            return False
        if now < self._quiet_until.get(conv.id, 0.0):
            return False
        last_failure = self._last_beat_failure.get(conv.id)
        if last_failure is not None and now - last_failure < BEAT_RETRY_S:
            return False
        last_beat = parse_iso(conv.labels.get(LABEL_LAST_BEAT)) or 0.0
        return now - max(last_beat, float(conv.updated_at)) >= interval

    def _maybe_beat(self, conv: Conversation, now: float, stalled_child: bool = False) -> None:
        if not self._beat_due(conv, now):
            return
        task = asyncio.get_running_loop().create_task(
            self._beat(conv, now, stalled_child), name=f"factory-heartbeat-{conv.id}"
        )
        self._beat_tasks[conv.id] = task
        task.add_done_callback(lambda t, sid=conv.id: self._beat_finished(sid, t))

    def _beat_finished(self, session_id: str, task: asyncio.Task[None]) -> None:
        if self._beat_tasks.get(session_id) is task:
            del self._beat_tasks[session_id]

    async def _plan_beat(
        self, conv: Conversation, now: float, stalled_child: bool
    ) -> quiet_beats.BeatPlan:
        last_beat = parse_iso(conv.labels.get(LABEL_LAST_BEAT))
        store = _factory_store(self._app_state)
        factory_id = conv.labels.get(LABEL_ID)
        if last_beat is None or store is None or not factory_id:
            return quiet_beats.BeatPlan(skip=False)
        since_ms = int(last_beat * 1000)
        newest = await asyncio.to_thread(
            store.list_events, factory_id, None, quiet_beats.EVENT_SCAN_LIMIT
        )
        since = [e for e in reversed(newest) if e.ts >= since_ms]
        return quiet_beats.plan_beat(
            now=now,
            last_beat=last_beat,
            interval=float(_heartbeat_interval(conv) or 0),
            beat_n=_int_label(conv, LABEL_BEAT_N),
            relevant=quiet_beats.relevant_events(conv, since),
            stalled_child=stalled_child,
        )

    def _record_skip(self, conv: Conversation, now: float) -> None:
        self._quiet_until[conv.id] = now + float(_heartbeat_interval(conv) or 0)
        counts = self._skips.setdefault(conv.labels.get(LABEL_ID, ""), {})
        counts[conv.id] = counts.get(conv.id, 0) + 1
        if self._skips_since is None:
            self._skips_since = now

    async def _report_skips(self, now: float) -> None:
        """Log skipped beats as one ``heartbeat.skipped`` event per factory per hour."""
        since = self._skips_since
        if since is None or now - since < quiet_beats.SKIP_EVENT_INTERVAL_S:
            return
        skips, self._skips, self._skips_since = self._skips, {}, None
        for factory_id, counts in skips.items():
            total = sum(counts.values())
            await self._emit(
                factory_id,
                "heartbeat.skipped",
                summary=f"{total} heartbeat{'s' if total != 1 else ''} skipped · no change",
                data={"counts": counts, "since": int(since), "until": int(now)},
            )

    async def _beat(self, conv: Conversation, now: float, stalled_child: bool = False) -> None:
        n = _int_label(conv, LABEL_BEAT_N) + 1
        delta = None
        deliver = self._deliver or deliver_message
        try:
            if self.config.quiet_beats:
                plan = await self._plan_beat(conv, now, stalled_child)
                if plan.skip:
                    self._record_skip(conv, now)
                    return
                delta = plan.delta
            # Queue behind mail still being delivered to this target.
            async with get_mailer(self._app_state).target_slot(conv.id):
                outcome = await deliver(self._app_state, conv.id, heartbeat_text(n, now, delta))
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
        self._beat_failures.pop(conv.id, None)
        self._last_beat_failure.pop(conv.id, None)
        self._quiet_until.pop(conv.id, None)
        labels = {LABEL_LAST_BEAT: iso_utc(self._clock()), LABEL_BEAT_N: str(n)}
        recovered = conv.labels.get(LABEL_HEALTH) == HEALTH_DEAD
        if recovered:
            # It answered again, so it is no longer dead.
            labels[LABEL_HEALTH] = HEALTH_OK
        store = self._store()
        if store is not None:
            await asyncio.to_thread(store.set_labels, conv.id, labels)
            if LABEL_DEAD_ALERTED_AT in conv.labels:
                await asyncio.to_thread(store.delete_label, conv.id, LABEL_DEAD_ALERTED_AT)
        factory_id = conv.labels.get(LABEL_ID)
        title = display_title(conv.title, conv.id)
        await self._emit(
            factory_id,
            "heartbeat.delivered",
            project=conv.labels.get(LABEL_PROJECT),
            to_id=conv.id,
            subject=session_subject(conv.id),
            summary=f"Heartbeat #{n} delivered to {title}",
            data={"n": n},
        )
        if recovered and factory_id:
            await server_decisions.expire_by_key(
                self._app_state, factory_id, server_decisions.relaunch_failed_key(conv.id)
            )
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
        factory_id = conv.labels.get(LABEL_ID)
        project = conv.labels.get(LABEL_PROJECT)
        title = display_title(conv.title, conv.id)
        await self._emit(
            factory_id,
            "heartbeat.failed",
            severity="warning",
            project=project,
            to_id=conv.id,
            subject=session_subject(conv.id),
            summary=f"Heartbeat to {title} failed: {reason}",
            data={"reason": reason, "failures": len(window)},
        )
        if len(window) < DEAD_AFTER_FAILURES:
            return
        alerted = _int_label(conv, LABEL_DEAD_ALERTED_AT)
        if alerted and now - alerted < DEAD_ALERT_INTERVAL_S:
            return
        store = self._store()
        if store is not None:
            await asyncio.to_thread(
                store.set_labels,
                conv.id,
                {LABEL_HEALTH: HEALTH_DEAD, LABEL_DEAD_ALERTED_AT: str(int(now))},
            )
        targets = dict.fromkeys(t for t in (conv.labels.get(LABEL_SUPERVISOR), factory_id) if t)
        targets.pop(conv.id, None)
        if factory_id:
            await self._emit(
                factory_id,
                "needs_human",
                severity="critical",
                project=project,
                from_id=conv.id,
                subject=session_subject(conv.id),
                summary=f"{title}: relaunch failed {DEAD_AFTER_FAILURES}× ({reason})",
                data={"reason": "relaunch_failed", "detail": reason},
            )
            if conv.labels.get(LABEL_HEALTH) != HEALTH_DEAD:
                await server_decisions.open_relaunch_failed_decision(
                    self._app_state, conv, factory_id, reason, DEAD_AFTER_FAILURES
                )
        if not targets:
            _logger.warning(
                "factory session %s is dead (%s) and has no supervisor to tell", conv.id, reason
            )
            return
        mailer = get_mailer(self._app_state)
        for target in targets:
            mailer.notify(
                target, dead_alert_text(conv, reason), factory_id=factory_id, project=project
            )

    # ── S3 stuck ────────────────────────────────────────────────

    async def _check_stuck(
        self,
        members: list[Conversation],
        children: list[Conversation],
        now: float,
    ) -> None:
        roots = {m.id: m.labels[LABEL_ID] for m in members}
        projects = {m.id: m.labels.get(LABEL_PROJECT) for m in members}
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
            owner = conv.id if conv.id in roots else conv.parent_conversation_id or ""
            factory_id = roots.get(owner)
            target = alert_target_id(conv, factory_id) if factory_id else None
            if target is None:
                continue
            mailer = mailer or get_mailer(self._app_state)
            mailer.notify(target, stuck_alert_text(conv, stalled_s), factory_id=factory_id)
            await self._emit(
                factory_id,
                "alert.stuck",
                severity="warning",
                project=projects.get(owner),
                from_id=conv.id,
                to_id=target,
                subject=session_subject(conv.id),
                summary=(
                    f"{display_title(conv.title, conv.id)} running with no progress "
                    f"for {format_duration(stalled_s)}"
                ),
                data={"stalled_s": int(stalled_s)},
            )

    # ── S5 blocked on a human ───────────────────────────────────

    def _notifier(self) -> FactoryNotifier | None:
        notifier = getattr(self._app_state, NOTIFIER_STATE_ATTR, None)
        return notifier if isinstance(notifier, FactoryNotifier) else None

    async def _escalate_blocked(self, store: ConversationStore, now: float) -> None:
        notifier = self._notifier()
        if notifier is None:
            return
        for episode in notifier.blocked_due(now, self.config.blocked_escalate_s):
            # Claimed before the first await so an overlapping tick skips it.
            episode.escalated = True
            try:
                await self._escalate_episode(store, notifier, episode)
            except Exception:  # noqa: BLE001 - retried next tick; others still escalate
                episode.escalated = False
                _logger.warning(
                    "factory: escalating blocked session %s failed",
                    episode.session_id,
                    exc_info=True,
                )

    async def _escalate_episode(
        self, store: ConversationStore, notifier: FactoryNotifier, episode: BlockedEpisode
    ) -> None:
        conv = await asyncio.to_thread(store.get_conversation, episode.session_id)
        factory_id = await asyncio.to_thread(resolve_factory_id, conv, store) if conv else None
        if conv is None or not factory_id or is_session_closed(conv.labels, conv.title):
            notifier.drop_episode(episode)
            return
        # A parked root shows its own prompt; typing into it could answer that
        # prompt, so its supervisor (the peer lead) hears instead.
        target = factory_id if factory_id != conv.id else conv.labels.get(LABEL_SUPERVISOR, "")
        if not target or target == conv.id:
            _logger.warning(
                "factory root %s is blocked on %r with no supervisor to tell",
                conv.id,
                episode.blocked_on,
            )
            return
        prior = await prior_escalation(self._app_state, factory_id, episode)
        if prior is not None:
            # Escalated before a server roll: keep that episode's start and decision.
            data = prior.data or {}
            episode.since = float(data.get("blocked_since") or episode.since)
            episode.decision_id = data.get("decision_id") or episode.decision_id
            return
        root = (
            conv
            if is_top_level(conv)
            else await asyncio.to_thread(store.get_conversation, conv.root_conversation_id)
        )
        project = (root or conv).labels.get(LABEL_PROJECT)
        title = display_title(conv.title, conv.id)
        get_mailer(self._app_state).notify(
            target,
            blocked_escalation_text(title, episode.since, episode.blocked_on),
            factory_id=factory_id,
            project=project,
        )
        opened = await server_decisions.open_blocked_decision(
            self._app_state, conv, factory_id, episode.blocked_on, iso_utc(episode.since), project
        )
        episode.decision_id = opened[0].id if opened else None
        await self._emit(
            factory_id,
            "needs_human",
            severity="critical",
            project=project,
            from_id=conv.id,
            to_id=target,
            subject=session_subject(conv.id),
            summary=f"{title} waiting on a human: {episode.blocked_on}",
            data={
                "reason": "blocked",
                "blocked_on": episode.blocked_on,
                "blocked_since": episode.since,
                "decision_id": episode.decision_id,
            },
        )

    # ── S9 preview TTL ──────────────────────────────────────────

    async def _park_idle_previews(self, members: list[Conversation], now: float) -> None:
        """Ask a PL to park a ready preview no human looked at for the TTL (once per window)."""
        ttl_h = self.config.preview_ttl_h
        store = _factory_store(self._app_state)
        if ttl_h is None or store is None:
            return
        if self._ttl_checked_at is not None and now - self._ttl_checked_at < PREVIEW_TTL_CHECK_S:
            return
        self._ttl_checked_at = now
        by_factory: dict[str, list[Conversation]] = {}
        for member in members:
            by_factory.setdefault(member.labels[LABEL_ID], []).append(member)
        for factory_id, factory_members in by_factory.items():
            try:
                await self._park_factory_previews(store, factory_id, factory_members, now, ttl_h)
            except Exception:  # noqa: BLE001 - one factory's store error must not stop the rest
                _logger.warning(
                    "factory: preview TTL check of %s failed", factory_id, exc_info=True
                )

    async def _park_factory_previews(
        self,
        store: FactoryStore,
        factory_id: str,
        members: list[Conversation],
        now: float,
        ttl_h: float,
    ) -> None:
        from omnigent.server.factory.snapshot import pick_pl

        ttl_s = ttl_h * 3600
        prs = await asyncio.to_thread(store.list_prs, factory_id, ["open"])
        ready = [p for p in prs if previews.preview_state(p.preview) == "ready" and p.project_slug]
        if not ready:
            return
        answered = await asyncio.to_thread(
            store.list_decisions, status="answered", factory_id=factory_id, limit=200
        )
        for pr in ready:
            attention = previews.attention_at(pr, answered)
            pl = pick_pl(None, pr.project_slug or "", members)
            if attention is None or now - attention < ttl_s or pl is None:
                continue
            since_ms = int(max(attention, now - ttl_s) * 1000)
            subject = previews.pr_subject(pr)
            notified = await asyncio.to_thread(
                store.find_events, factory_id, previews.TTL_EVENT_KIND, since_ms, subject, limit=1
            )
            if notified:
                continue
            get_mailer(self._app_state).notify(
                pl.id,
                previews.ttl_notice_text(pr, ttl_h),
                factory_id=factory_id,
                project=pr.project_slug,
            )
            await self._emit(
                factory_id,
                previews.TTL_EVENT_KIND,
                severity="notice",
                project=pr.project_slug,
                to_id=pl.id,
                subject=subject,
                summary=(
                    f"Preview {previews.preview_name(pr)} idle {format_duration(now - attention)}"
                    " · asked the PL to park it"
                ),
                data={"pr_id": pr.id, "attention_at": int(attention), "ttl_h": ttl_h},
            )

    # ── housekeeping ────────────────────────────────────────────

    def _forget_departed(self, member_ids: set[str], child_ids: set[str]) -> None:
        for state in (
            self._keepalive_at,
            self._beat_failures,
            self._last_beat_failure,
            self._quiet_until,
        ):
            for sid in [s for s in state if s not in member_ids]:
                del state[sid]
        watched = member_ids | child_ids
        for sid in [s for s in self._stuck_alerted if s not in watched]:
            del self._stuck_alerted[sid]
        for sid in [s for s in self._child_status if s not in child_ids]:
            del self._child_status[sid]
        notifier = self._notifier()
        if notifier is not None:
            notifier.prune_escalated(watched)


def start_supervision(app_state: Any) -> Callable[[], Awaitable[None]]:
    """
    Start the supervisor and the status-edge notifier on *app_state*.

    :param app_state: ``app.state`` with the factory deps registered.
    :returns: An async callable that stops both and detaches them.
    """
    notifier = get_notifier(app_state)
    register_relay_notifier(notifier)
    supervisor = FactorySupervisor(app_state)
    setattr(app_state, SUPERVISOR_STATE_ATTR, supervisor)
    supervisor.start()
    _logger.info("factory supervisor started (%s)", supervisor.config)

    async def _stop() -> None:
        unregister_relay_notifier(notifier)
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
