"""Asynchronous factory mail: queue, retry, bounce, rate limits, reply registry.

``POST /v1/factory/messages`` answers 202 as soon as a message is accepted;
the :class:`Mailer` delivers it in a background task (which may wait minutes
for a managed sandbox to provision or a suspended one to wake), retries
transient failures, and bounces a final failure back to the sender. State is
in memory on one replica: a server restart drops queued mail, which agents are
told to re-ask about at their next heartbeat.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from omnigent.factory import PENDING_REPLY_KINDS
from omnigent.server.factory.delivery import DeliveryOutcome, deliver_message

_logger = logging.getLogger(__name__)

POST_RATE_ENV = "OMNIGENT_FACTORY_POST_RATE_PER_HOUR"
"""``<per sender>[,<per sender-target pair>]`` posts per hour, e.g. ``"60,20"``."""
DEFAULT_SENDER_RATE_PER_HOUR = 60
DEFAULT_PAIR_RATE_PER_HOUR = 20
RETRY_DELAYS_S: tuple[float, ...] = (60.0, 300.0)
STORM_ALERT_INTERVAL_S = 3600.0
_WINDOW_S = 3600.0

Deliver = Callable[..., Awaitable[DeliveryOutcome]]
Sleep = Callable[[float], Awaitable[Any]]


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
    """

    mail_id: str
    sender_id: str | None
    target_id: str
    target_title: str | None
    text: str
    kind: str
    restart: bool = False


def new_mail_id() -> str:
    """:returns: A fresh ``mail_<hex>`` id."""
    return f"mail_{secrets.token_hex(8)}"


def bounce_text(target_title: str | None, target_id: str, reason: str) -> str:
    """:returns: The ``[Factory delivery failure · …]`` notice for the sender."""
    title = target_title or target_id
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
        self._sender_posts: dict[str, deque[float]] = {}
        self._pair_posts: dict[tuple[str, str], deque[float]] = {}
        self._storm_alerted_at: dict[str, float] = {}
        self._pending: dict[str, tuple[str, float]] = {}
        self._last_post: dict[tuple[str, str], float] = {}
        # One delivery at a time per target keeps a brief ahead of its follow-ups.
        self._target_locks: dict[str, asyncio.Lock] = {}
        self._closed = False

    # ── rate limits ─────────────────────────────────────────────

    @staticmethod
    def _prune(window: deque[float], now: float) -> deque[float]:
        while window and window[0] <= now - _WINDOW_S:
            window.popleft()
        return window

    def try_acquire(self, sender_id: str, target_id: str) -> str | None:
        """
        Count one post against the sender and pair limits.

        :param sender_id: Posting session id.
        :param target_id: Recipient session id.
        :returns: ``None`` when allowed (and counted), else a description of
            the exceeded limit, e.g. ``"60 posts/h per sender"``.
        """
        now = self._clock()
        sender_window = self._prune(self._sender_posts.setdefault(sender_id, deque()), now)
        pair_window = self._prune(
            self._pair_posts.setdefault((sender_id, target_id), deque()), now
        )
        if len(sender_window) >= self.sender_rate_per_hour:
            return f"{self.sender_rate_per_hour} posts/h per sender"
        if len(pair_window) >= self.pair_rate_per_hour:
            return f"{self.pair_rate_per_hour} posts/h to one recipient"
        sender_window.append(now)
        pair_window.append(now)
        return None

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

    def submit(self, item: MailItem) -> None:
        """
        Queue *item* for background delivery.

        :param item: The message to deliver.
        :raises RuntimeError: After :meth:`close`.
        """
        if self._closed:
            raise RuntimeError("factory mailer is closed")
        task = asyncio.create_task(self._run(item), name=f"factory-mail-{item.mail_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _attempt(self, session_id: str, text: str, *, restart: bool) -> DeliveryOutcome:
        deliver = self._deliver or deliver_message
        try:
            return await deliver(self._app_state, session_id, text, restart=restart)
        except Exception as exc:
            _logger.exception("factory delivery to %s crashed", session_id)
            return DeliveryOutcome(False, f"internal_error: {type(exc).__name__}")

    async def _run(self, item: MailItem) -> None:
        lock = self._target_locks.setdefault(item.target_id, asyncio.Lock())
        async with lock:
            await self._deliver_with_retries(item)

    async def _deliver_with_retries(self, item: MailItem) -> None:
        delays = iter(self._retry_delays_s)
        restart = item.restart
        while True:
            outcome = await self._attempt(item.target_id, item.text, restart=restart)
            if outcome.delivered:
                if item.sender_id is not None and item.kind in PENDING_REPLY_KINDS:
                    self._pending[item.target_id] = (item.sender_id, self._clock())
                _logger.info("factory mail %s delivered to %s", item.mail_id, item.target_id)
                return
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

    def notify(self, target_id: str, text: str) -> None:
        """
        Queue a server notice (alert, storm warning) with no sender to bounce to.

        :param target_id: Recipient session id.
        :param text: The full notice text.
        """
        self.submit(
            MailItem(
                mail_id=new_mail_id(),
                sender_id=None,
                target_id=target_id,
                target_title=None,
                text=text,
                kind="alert",
            )
        )

    async def drain(self) -> None:
        """Wait for every queued delivery to finish (tests and shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

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
