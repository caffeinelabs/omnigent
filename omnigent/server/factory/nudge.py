"""Wake-ups for the factory's background loops.

A route or tool that knows something changed (``sys_factory_pr`` registered a
PR, a human clicked "I merged it") calls :func:`nudge`; the loop that owns the
topic sleeps in :func:`wait` and runs at once instead of waiting out its
interval. A nudge that lands while the loop is busy is kept, so the next
:func:`wait` returns immediately. Each topic is meant to have one consumer.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from dataclasses import dataclass, field

_lock = threading.Lock()


@dataclass
class _Topic:
    pending: bool = False
    keys: set[str] = field(default_factory=set)
    waiters: set[tuple[asyncio.Event, asyncio.AbstractEventLoop]] = field(default_factory=set)


_topics: dict[str, _Topic] = {}


def nudge(topic: str, key: str | None = None) -> None:
    """
    Wake the loop waiting on *topic* (or make its next wait return at once).

    Safe to call from any thread.

    :param topic: e.g. ``"prs"`` or ``"envs"``.
    :param key: What changed, e.g. a PR row id; collected for the waiter.
    """
    with _lock:
        state = _topics.setdefault(topic, _Topic())
        state.pending = True
        if key is not None:
            state.keys.add(key)
        waiters = list(state.waiters)
    for event, loop in waiters:
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:
            # The waiter's loop has closed; nothing to wake.
            continue


def _take(state: _Topic) -> set[str] | None:
    if not state.pending:
        return None
    keys = set(state.keys)
    state.pending = False
    state.keys.clear()
    return keys


async def wait(topic: str, timeout: float) -> set[str] | None:
    """
    Sleep until *topic* is nudged or *timeout* seconds pass.

    :param topic: e.g. ``"prs"``.
    :param timeout: Seconds to wait at most.
    :returns: The keys nudged since the last wait (an empty set for keyless
        nudges), or ``None`` when the timeout passed without a nudge.
    """
    event = asyncio.Event()
    entry = (event, asyncio.get_running_loop())
    with _lock:
        state = _topics.setdefault(topic, _Topic())
        taken = _take(state)
        if taken is not None:
            return taken
        state.waiters.add(entry)
    try:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(event.wait(), timeout)
    finally:
        with _lock:
            state.waiters.discard(entry)
            taken = _take(state)
    return taken


def reset() -> None:
    """Forget every pending nudge (tests)."""
    with _lock:
        _topics.clear()


__all__ = ["nudge", "reset", "wait"]
