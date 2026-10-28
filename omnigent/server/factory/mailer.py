"""Asynchronous factory mail: queue, retry, bounce, rate limits, reply registry.

``POST /v1/factory/messages`` answers 202 as soon as a message is accepted;
the :class:`Mailer` delivers it in a background task (which may wait minutes
for a managed sandbox to provision or a suspended one to wake), retries
transient failures, and bounces a final failure back to the sender. Queues are
in memory on one replica; every message is also logged to the factory event
log (see :mod:`~omnigent.server.factory.mail_events`), so mail a server
restart dropped is replayed on the next start.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from omnigent.factory import PENDING_REPLY_KINDS
from omnigent.server.factory import mail_events
from omnigent.server.factory.delivery import DeliveryOutcome, deliver_message
from omnigent.server.factory.envelope import display_title

_logger = logging.getLogger(__name__)

POST_RATE_ENV = "OMNIGENT_FACTORY_POST_RATE_PER_HOUR"
"""``<per sender>[,<per sender-target pair>]`` posts per hour, e.g. ``"60,20"``."""
DEFAULT_SENDER_RATE_PER_HOUR = 60
DEFAULT_PAIR_RATE_PER_HOUR = 20
RETRY_DELAYS_S: tuple[float, ...] = (60.0, 300.0)
STORM_ALERT_INTERVAL_S = 3600.0
RATE_LIMITED_EVENT_INTERVAL_S = 60.0
"""At most one ``rate_limited`` event per sender and recipient per interval."""
_WINDOW_S = 3600.0

Deliver = Callable[..., Awaitable[DeliveryOutcome]]
Sleep = Callable[[float], Awaitable[Any]]

# Mail kinds other modules send (the Room's "chat"), each told when one of its items lands.
_KIND_HOOKS: dict[str, Callable[[Any, MailItem, float], None]] = {}


def register_kind(kind: str, on_delivered: Callable[[Any, MailItem, float], None]) -> None:
    """Call ``on_delivered(app_state, item, attempt_started_at)`` after each delivered *kind*."""
    _KIND_HOOKS[kind] = on_delivered


def rate_limits_from_env() -> tuple[int, int]:
    """
    Read the per-sender and per-pair hourly post limits.

    A single number sets the sender limit and scales the pair limit by the
    default 1:3 ratio; ``"<sender>,<pair>"`` sets both. Bad values fall back
    to the defaults.

    :returns: ``(sender_per_hour, pair_per_hour)``.
    """
    raw = os.environ.get(POST_RATE_ENV, "").strip()
    if not raw:
        return DEFAULT_SENDER_RATE_PER_HOUR, DEFAULT_PAIR_RATE_PER_HOUR
    try:
        parts = [int(p) for p in raw.split(",")]
    except ValueError:
        _logger.warning("Ignoring malformed %s=%r", POST_RATE_ENV, raw)
        return DEFAULT_SENDER_RATE_PER_HOUR, DEFAULT_PAIR_RATE_PER_HOUR
    sender = max(1, parts[0])
    pair = max(1, parts[1]) if len(parts) > 1 else max(1, sender // 3)
    return sender, pair


@dataclass
class MailItem:
    """
    One queued message.

    :param mail_id: Identifier returned to the poster, e.g. ``"mail_ab12"``.
    :param sender_id: Posting session id, or ``None`` for server notices.
    :param target_id: Recipient session id.
    :param target_title: Recipient title, for the bounce text.
    :param text: The full enveloped message.
    :param kind: Message kind, e.g. ``"request"``.
    :param restart: Stop the target before delivering.
    :param factory_id: The factory it belongs to (resolved from the target when unset).
    :param project: Project slug for the event log.
    :param replayed: Re-submitted from the event log after a restart.
    :param created_by: The human the message is attributed to, e.g. a Room author.
    :param thread: The Room thread it belongs to, e.g. ``"project:db-version"``.
    """

    mail_id: str
    sender_id: str | None
    target_id: str
    target_title: str | None
    text: str
    kind: str
    restart: bool = False
    factory_id: str | None = None
    project: str | None = None
    replayed: bool = False
    created_by: str | None = None
    thread: str | None = None


def new_mail_id() -> str:
    """:returns: A fresh ``mail_<hex>`` id."""
    return f"mail_{secrets.token_hex(8)}"


def bounce_text(target_title: str | None, target_id: str, reason: str) -> str:
    """:returns: The ``[Factory delivery failure · …]`` notice for the sender."""
    title = display_title(target_title, target_id)
    return (
        f'[Factory delivery failure · message to "{title}" ({target_id}) undeliverable: {reason}]'
    )


class Mailer:
    """
    Background delivery with retries, bounces, rate limits and reply tracking.

    Tasks are held in a strong-ref set (asyncio keeps only weak refs) and
    cancelled by :meth:`close`.
    """

    def __init__(
        self,
        app_state: Any,
        *,
        deliver: Deliver | None = None,
        retry_delays_s: Sequence[float] = RETRY_DELAYS_S,
        sleep: Sleep = asyncio.sleep,
        clock: Callable[[], float] = time.time,
        sender_rate_per_hour: int | None = None,
        pair_rate_per_hour: int | None = None,
    ) -> None:
        """
        :param app_state: ``app.state`` passed through to delivery.
        :param deliver: Delivery function; defaults to
            :func:`~omnigent.server.factory.delivery.deliver_message`.
        :param retry_delays_s: Waits before each retry of a transient failure.
        :param sleep: Awaitable sleep (injectable for tests).
        :param clock: Wall clock in epoch seconds (injectable for tests).
        :param sender_rate_per_hour: Override the env/default sender limit.
        :param pair_rate_per_hour: Override the env/default pair limit.
        """
        env_sender, env_pair = rate_limits_from_env()
        self._app_state = app_state
        self._deliver = deliver
        self._retry_delays_s = tuple(retry_delays_s)
        self._sleep = sleep
        self._clock = clock
        self.sender_rate_per_hour = sender_rate_per_hour or env_sender
        self.pair_rate_per_hour = pair_rate_per_hour or env_pair
        self._tasks: set[asyncio.Task[None]] = set()
        self._queued: dict[str, float] = {}
        self._sender_posts: dict[str, deque[float]] = {}
        self._pair_posts: dict[tuple[str, str], deque[float]] = {}
        self._storm_alerted_at: dict[str, float] = {}
        self._refusal_logged_at: dict[tuple[str, str], float] = {}
        self._pending: dict[str, tuple[str, float]] = {}
        self._last_post: dict[tuple[str, str], float] = {}
        # One delivery at a time per target keeps a brief ahead of its follow-ups.
        self._target_locks: dict[str, asyncio.Lock] = {}
        self._target_users: dict[str, int] = {}
        self._swept_at = clock()
        self._closed = False

    # ── rate limits ─────────────────────────────────────────────

    @staticmethod
    def _prune(window: deque[float], now: float) -> deque[float]:
        while window and window[0] <= now - _WINDOW_S:
            window.popleft()
        return window

    def _sweep(self, now: float) -> None:
        """Drop rate windows and storm marks idle for a full window (hourly)."""
        if now - self._swept_at < _WINDOW_S:
            return
        self._swept_at = now
        for sender_id in [k for k, w in self._sender_posts.items() if not self._prune(w, now)]:
            del self._sender_posts[sender_id]
        for pair in [k for k, w in self._pair_posts.items() if not self._prune(w, now)]:
            del self._pair_posts[pair]
        for factory_id in [
            f for f, at in self._storm_alerted_at.items() if now - at >= STORM_ALERT_INTERVAL_S
        ]:
            del self._storm_alerted_at[factory_id]
        for pair in [
            k
            for k, at in self._refusal_logged_at.items()
            if now - at >= RATE_LIMITED_EVENT_INTERVAL_S
        ]:
            del self._refusal_logged_at[pair]

    def try_acquire(
        self, sender_id: str, target_id: str, *, factory_id: str | None = None
    ) -> str | None:
        """
        Count one post against the sender and pair limits.

        :param sender_id: Posting session id.
        :param target_id: Recipient session id.
        :param factory_id: Logs a refusal as ``rate_limited`` in this factory.
        :returns: ``None`` when allowed (and counted), else a description of
            the exceeded limit, e.g. ``"60 posts/h per sender"``.
        """
        now = self._clock()
        self._sweep(now)
        sender_window = self._prune(self._sender_posts.setdefault(sender_id, deque()), now)
        pair_window = self._prune(
            self._pair_posts.setdefault((sender_id, target_id), deque()), now
        )
        limit = None
        if len(sender_window) >= self.sender_rate_per_hour:
            limit = f"{self.sender_rate_per_hour} posts/h per sender"
        elif len(pair_window) >= self.pair_rate_per_hour:
            limit = f"{self.pair_rate_per_hour} posts/h to one recipient"
        if limit is not None:
            if (
                factory_id
                and not self._closed
                and self._claim_refusal_log(sender_id, target_id, now)
            ):
                self._spawn(
                    mail_events.record_rate_limited(
                        self._app_state, factory_id, sender_id, target_id, limit, self._ms()
                    )
                )
            return limit
        sender_window.append(now)
        pair_window.append(now)
        return None

    def _claim_refusal_log(self, sender_id: str, target_id: str, now: float) -> bool:
        """A looping sender logs one refusal per recipient per minute, not one per post."""
        last = self._refusal_logged_at.get((sender_id, target_id))
        if last is not None and now - last < RATE_LIMITED_EVENT_INTERVAL_S:
            return False
        self._refusal_logged_at[(sender_id, target_id)] = now
        return True

    def claim_storm_alert(self, factory_id: str) -> bool:
        """
        Allow one storm alert per factory per hour.

        :param factory_id: Factory root session id.
        :returns: ``True`` when the caller should send the alert now.
        """
        now = self._clock()
        last = self._storm_alerted_at.get(factory_id)
        if last is not None and now - last < STORM_ALERT_INTERVAL_S:
            return False
        self._storm_alerted_at[factory_id] = now
        return True

    # ── reply registry ──────────────────────────────────────────

    def record_post(self, sender_id: str, target_id: str, kind: str) -> None:
        """
        Note an accepted post for the reply registry.

        A ``reply`` from a target to the sender of its pending request clears
        that pending entry (the sender already has its answer).

        :param sender_id: Posting session id.
        :param target_id: Recipient session id.
        :param kind: Message kind.
        """
        self._last_post[(sender_id, target_id)] = self._clock()
        if kind == "reply":
            pending = self._pending.get(sender_id)
            if pending is not None and pending[0] == target_id:
                del self._pending[sender_id]

    def pop_pending_reply(self, target_id: str) -> tuple[str, float] | None:
        """
        Take the pending request awaiting *target_id*'s answer.

        :param target_id: The session that received a request/brief.
        :returns: ``(sender_id, delivered_at)`` or ``None``.
        """
        return self._pending.pop(target_id, None)

    def last_post(self, sender_id: str, target_id: str) -> float | None:
        """
        :param sender_id: Posting session id.
        :param target_id: Recipient session id.
        :returns: Epoch seconds of the last accepted post, or ``None``.
        """
        return self._last_post.get((sender_id, target_id))

    # ── delivery ────────────────────────────────────────────────

    def _spawn(self, coro: Any, name: str = "factory-mail-event") -> asyncio.Task[None]:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _log_after(
        self, previous: asyncio.Task[None] | None, record: Callable[[], Awaitable[None]]
    ) -> None:
        """Write an event once *previous* (the queued event) is written; never blocks delivery."""

        async def _write() -> None:
            if previous is not None:
                await asyncio.gather(previous, return_exceptions=True)
            await record()

        self._spawn(_write())

    def submit(self, item: MailItem) -> None:
        """
        Queue *item* for background delivery.

        :param item: The message to deliver.
        :raises RuntimeError: After :meth:`close`.
        """
        if self._closed:
            raise RuntimeError("factory mailer is closed")
        self._queued.setdefault(item.mail_id, self._clock())
        self._spawn(self._run(item), name=f"factory-mail-{item.mail_id}")

    def _ms(self) -> int:
        return int(self._clock() * 1000)

    def is_queued(self, mail_id: str) -> bool:
        """:returns: Whether *mail_id* is waiting for or in delivery."""
        return mail_id in self._queued

    def queue_stats(self) -> tuple[int, float | None]:
        """:returns: ``(messages not yet delivered or bounced, age of the oldest in seconds)``."""
        if not self._queued:
            return 0, None
        return len(self._queued), max(0.0, self._clock() - min(self._queued.values()))

    async def replay_undelivered(self) -> int:
        """
        Re-submit mail a previous server process queued but never finished.

        :returns: How many messages were re-submitted.
        """
        return await mail_events.replay_undelivered(self._app_state, self)

    async def _attempt(
        self, session_id: str, text: str, *, restart: bool, created_by: str | None = None
    ) -> DeliveryOutcome:
        deliver = self._deliver or deliver_message
        extra = {"created_by": created_by} if created_by else {}
        try:
            return await deliver(self._app_state, session_id, text, restart=restart, **extra)
        except Exception as exc:
            _logger.exception("factory delivery to %s crashed", session_id)
            return DeliveryOutcome(False, f"internal_error: {type(exc).__name__}")

    @asynccontextmanager
    async def target_slot(self, target_id: str) -> AsyncIterator[None]:
        """
        Hold *target_id*'s delivery slot (one delivery at a time per target).

        :param target_id: Recipient session id.
        """
        lock = self._target_locks.setdefault(target_id, asyncio.Lock())
        self._target_users[target_id] = self._target_users.get(target_id, 0) + 1
        try:
            async with lock:
                yield
        finally:
            self._target_users[target_id] -= 1
            if not self._target_users[target_id]:
                del self._target_users[target_id]
                self._target_locks.pop(target_id, None)

    async def _run(self, item: MailItem) -> None:
        queued = (
            None
            if item.replayed
            else self._spawn(mail_events.record_queued(self._app_state, item, self._ms()))
        )
        try:
            async with self.target_slot(item.target_id):
                reason = await self._deliver_with_retries(item)
        finally:
            self._queued.pop(item.mail_id, None)
        done_at = self._ms()
        if reason is None:
            self._log_after(
                queued, lambda: mail_events.record_delivered(self._app_state, item, done_at)
            )
            return
        self._log_after(
            queued, lambda: mail_events.record_bounced(self._app_state, item, reason, done_at)
        )
        # Bounce outside the target's lock: waking the sender can take minutes.
        await self._bounce(item, reason)

    async def _deliver_with_retries(self, item: MailItem) -> str | None:
        """:returns: ``None`` once delivered, else the final failure reason."""
        delays = iter(self._retry_delays_s)
        restart = item.restart
        while True:
            attempted_at = self._clock()
            outcome = await self._attempt(
                item.target_id, item.text, restart=restart, created_by=item.created_by
            )
            if outcome.delivered:
                if item.sender_id is not None and item.kind in PENDING_REPLY_KINDS:
                    self._pending[item.target_id] = (item.sender_id, self._clock())
                if (hook := _KIND_HOOKS.get(item.kind)) is not None:
                    try:
                        hook(self._app_state, item, attempted_at)
                    except Exception:  # noqa: BLE001 - a kind hook never fails a delivery
                        _logger.warning("factory mail %s hook failed", item.mail_id, exc_info=True)
                _logger.info("factory mail %s delivered to %s", item.mail_id, item.target_id)
                return None
            # A restart's stop already ran; retries only redeliver.
            restart = False
            delay = next(delays, None) if outcome.retryable else None
            if delay is None:
                break
            _logger.info(
                "factory mail %s to %s failed (%s); retrying in %.0fs",
                item.mail_id,
                item.target_id,
                outcome.reason,
                delay,
            )
            await self._sleep(delay)
        reason = outcome.reason or "unknown"
        _logger.warning(
            "factory mail %s to %s undeliverable: %s", item.mail_id, item.target_id, reason
        )
        return reason

    async def _bounce(self, item: MailItem, reason: str) -> None:
        if item.sender_id is None or item.sender_id == item.target_id:
            return
        bounce = await self._attempt(
            item.sender_id,
            bounce_text(item.target_title, item.target_id, reason),
            restart=False,
        )
        if not bounce.delivered:
            _logger.warning(
                "factory bounce for mail %s to sender %s failed: %s",
                item.mail_id,
                item.sender_id,
                bounce.reason,
            )

    def notify(
        self,
        target_id: str,
        text: str,
        *,
        factory_id: str | None = None,
        project: str | None = None,
    ) -> str:
        """
        Queue a server notice (alert, storm warning) with no sender to bounce to.

        :param target_id: Recipient session id.
        :param text: The full notice text.
        :param factory_id: The factory it concerns (resolved from the target when unset).
        :param project: Project slug for the event log.
        :returns: The notice's mail id.
        """
        mail_id = new_mail_id()
        self.submit(
            MailItem(
                mail_id=mail_id,
                sender_id=None,
                target_id=target_id,
                target_title=None,
                text=text,
                kind="alert",
                factory_id=factory_id,
                project=project,
            )
        )
        return mail_id

    async def drain(self) -> None:
        """Wait for every queued delivery to finish (tests and shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
            # A gather of finished tasks returns without yielding; let their
            # done-callbacks prune the set.
            await asyncio.sleep(0)

    async def close(self) -> None:
        """Stop accepting mail and cancel in-flight deliveries."""
        self._closed = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()


MAILER_STATE_ATTR = "factory_mailer"
"""``app.state`` attribute holding the process's :class:`Mailer`."""


def get_mailer(app_state: Any) -> Mailer:
    """
    Return the app's mailer, creating it on first use.

    :param app_state: ``app.state`` (or a test stand-in).
    :returns: The shared :class:`Mailer`.
    """
    mailer = getattr(app_state, MAILER_STATE_ATTR, None)
    if not isinstance(mailer, Mailer):
        mailer = Mailer(app_state)
        setattr(app_state, MAILER_STATE_ATTR, mailer)
    return mailer
