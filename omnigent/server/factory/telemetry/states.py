"""Durable per-agent state segments, so an agent's last 24 hours survive without a viewer.

A segment is a span on the agent's session, opened and closed by its status edges:

- ``turn`` spans named ``working``: the session runs a turn;
- ``wait`` spans named ``person`` (``attrs.on = "person"``): the turn is parked
  on a person's approval or answer (``blocked_on``).

Idle is the gap between segments. Busy edges come from the relayed runner
status (Claude's own status file, or PTY activity) and the status route; turn
ends from the status route's harness hooks (``Stop``/``StopFailure``) and the
relay. An idle edge closes a segment only after a short quiet spell, so a
mid-turn lull does not split a turn. Each segment keeps the session's
cumulative cost when it opened and closed (``attrs.cost_start``/``cost_end``),
its vendor and its model.

:func:`segments` reads them back for one session and window, with the open one
clipped to the session's last activity when it no longer runs.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import LABEL_ID
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.membership import is_top_level

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactorySpan

_logger = logging.getLogger(__name__)

RECORDER_STATE_ATTR: Final = "factory_state_recorder"
WORKING: Final = "working"
WAITING: Final = "waiting"
IDLE: Final = "idle"
FAILED: Final = "failed"
KIND_BY_STATE: Final[dict[str, str]] = {WORKING: "turn", WAITING: "wait"}
NAME_BY_STATE: Final[dict[str, str]] = {WORKING: "working", WAITING: "person"}
SEGMENT_KINDS: Final = ("turn", "wait")
WHO_TTL_S: Final = 60.0
NOT_MEMBER_TTL_S: Final = 600.0
"""A session found outside every factory is not looked up again for this long."""
DEBOUNCE_S: Final = 20.0
"""An idle edge closes a segment only if the session stays idle this long (mid-turn lulls)."""
_WHO_MAX: Final = 10_000
_BUSY: Final = frozenset({"running", "waiting"})


def state_of(status: str, blocked_on: object = None) -> str | None:
    """
    The segment state a status edge starts.

    :param status: ``running`` / ``waiting`` / ``idle`` / ``failed``.
    :param blocked_on: Why a running turn is parked, if it is.
    :returns: ``working``, ``waiting``, ``idle`` or ``failed``; ``None`` for others.
    """
    if status in _BUSY:
        parked = isinstance(blocked_on, str) and blocked_on.strip()
        return WAITING if parked else WORKING
    if status == "idle":
        return IDLE
    if status == "failed":
        return FAILED
    return None


@dataclass(frozen=True)
class _Who:
    factory_id: str
    project: str | None
    role: str | None
    agent_name: str | None
    vendor: str | None
    model: str | None


def _who(deps: FactoryDeps, conv: Conversation) -> _Who | None:
    from omnigent.server.factory.identity import vendor_model
    from omnigent.server.factory.telemetry import cost

    found = cost.attribution(deps, conv)
    if found is None:
        return None
    vendor, model = vendor_model(deps, conv)
    return _Who(
        factory_id=found.factory_id,
        project=found.project or None,
        role=found.role or None,
        agent_name=found.agent_name or None,
        vendor=vendor,
        model=model,
    )


def _cost(conv: Conversation) -> float | None:
    raw = (conv.session_usage or {}).get("total_cost_usd")
    return round(float(raw), 6) if isinstance(raw, int | float) else None


class StateRecorder:
    """
    Per-process segment writer: what is open for each session, and ordered writes.

    A busy edge opens a segment (or switches working and waiting); an idle or
    failed edge closes it after :data:`DEBOUNCE_S` unless the session is busy
    again by then, so a mid-turn lull does not split one turn. Writes run in
    worker threads, one session at a time in arrival order.
    """

    def __init__(
        self,
        app_state: Any,
        *,
        clock: Callable[[], float] = time.time,
        debounce_s: float = DEBOUNCE_S,
    ) -> None:
        self._app_state = app_state
        self._clock = clock
        self._debounce_s = debounce_s
        self._open: dict[str, str] = {}
        self._closing: dict[str, asyncio.TimerHandle] = {}
        self._seen: set[str] = set()
        self._who: dict[str, tuple[float, _Who | None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[object]] = set()

    def current(self, session_id: str) -> str | None:
        """:returns: The state of the segment this process holds open for *session_id*."""
        return self._open.get(session_id)

    def on_status_edge(self, conv: Conversation, status: str, data: dict[str, Any]) -> None:
        """One edge from the status route (harness hooks); sync, no I/O."""
        if is_top_level(conv) and not conv.labels.get(LABEL_ID):
            return
        self.edge(conv.id, status, data.get("blocked_on"))

    def on_relayed(self, session_id: str, status: str, blocked_on: object) -> None:
        """One relayed runner ``session.status`` edge; sync, no I/O."""
        self.edge(session_id, status, blocked_on)

    def edge(self, session_id: str, status: str, blocked_on: object = None) -> None:
        """Apply one status edge of *session_id* (sync, no I/O)."""
        state = state_of(status, blocked_on)
        if state is None or self._not_member(session_id):
            return
        if len(self._seen) > _WHO_MAX:
            self._seen.clear()
        first = session_id not in self._seen
        self._seen.add(session_id)
        at_ms = int(self._clock() * 1000)
        if state in KIND_BY_STATE:
            timer = self._closing.pop(session_id, None)
            if timer is not None:
                timer.cancel()
            if self._open.get(session_id) == state:
                return
            self._open[session_id] = state
            self._spawn(session_id, state, at_ms)
            return
        if session_id not in self._open:
            if first:
                # A segment a server roll left open is closed at the first turn end seen.
                self._spawn(session_id, state, at_ms)
            return
        if session_id in self._closing:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._closing[session_id] = loop.call_later(
            self._debounce_s, self._close_due, session_id, state, at_ms
        )

    def _close_due(self, session_id: str, state: str, at_ms: int) -> None:
        self._closing.pop(session_id, None)
        self._open.pop(session_id, None)
        self._spawn(session_id, state, at_ms)

    def _not_member(self, session_id: str) -> bool:
        cached = self._who.get(session_id)
        if cached is None or cached[1] is not None:
            return False
        return time.monotonic() - cached[0] < NOT_MEMBER_TTL_S

    def _spawn(self, session_id: str, state: str, at_ms: int) -> None:
        try:
            task = asyncio.get_running_loop().create_task(
                self._apply(session_id, state, at_ms), name="factory-state-segment"
            )
        except RuntimeError:
            _logger.debug("factory states: no loop for %s", session_id)
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _lock(self, session_id: str) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            if len(self._locks) > _WHO_MAX:
                self._locks = {k: v for k, v in self._locks.items() if v.locked()}
            lock = self._locks[session_id] = asyncio.Lock()
        return lock

    async def _apply(self, session_id: str, state: str, at_ms: int) -> None:
        deps = getattr(self._app_state, FACTORY_STATE_ATTR, None)
        if not isinstance(deps, FactoryDeps):
            return
        try:
            async with self._lock(session_id):
                await asyncio.to_thread(self.write, deps, session_id, state, at_ms)
        except Exception:  # noqa: BLE001 - a lost segment only thins the strip
            _logger.warning("factory states: could not record %s", session_id, exc_info=True)

    def _identify(self, deps: FactoryDeps, conv: Conversation) -> _Who | None:
        now = time.monotonic()
        cached = self._who.get(conv.id)
        if cached is not None and now - cached[0] < WHO_TTL_S:
            return cached[1]
        found = _who(deps, conv)
        if len(self._who) >= _WHO_MAX:
            self._who.clear()
        self._who[conv.id] = (now, found)
        return found

    def write(self, deps: FactoryDeps, session_id: str, state: str, at_ms: int) -> None:
        """Close the session's open segment at *at_ms*, then open *state*'s (synchronous)."""
        from omnigent.server.factory.telemetry import spans

        conv = deps.conversation_store.get_conversation(session_id)
        who = self._identify(deps, conv) if conv is not None else None
        if conv is None or who is None:
            self._open.pop(session_id, None)
            return
        store = deps.factory_store
        cost_now = _cost(conv)
        outcome = spans.FAIL if state == FAILED else spans.OK
        for kind in SEGMENT_KINDS:
            for span in store.list_open_spans(who.factory_id, kind, session_id=session_id):
                if not (span.dedupe_key or "").startswith("state:"):
                    continue
                attrs = dict(span.attrs or {})
                if cost_now is not None:
                    attrs["cost_end"] = cost_now
                store.update_span(
                    span.id, ended_at=max(at_ms, span.started_at), outcome=outcome, attrs=attrs
                )
        kind = KIND_BY_STATE.get(state)
        if kind is None:
            return
        attrs: dict[str, Any] = {"segment": state, "cost_start": cost_now}
        if state == WAITING:
            attrs["on"] = "person"
        spans.write_span(
            store,
            who.factory_id,
            kind=kind,
            name=NAME_BY_STATE[state],
            dedupe_key=spans.state_key(session_id, at_ms),
            started_at=at_ms,
            attrs=attrs,
            vendor=who.vendor,
            model=who.model,
            ctx=spans.span_context(deps, who.factory_id),
            session_id=session_id,
            project_slug=who.project,
            role=who.role,
            agent_name=who.agent_name,
        )

    async def drain(self, timeout_s: float = 5.0) -> None:
        """Wait for pending closes and every scheduled write (tests and shutdown)."""
        deadline = time.monotonic() + timeout_s
        while (self._tasks or self._closing) and time.monotonic() < deadline:
            if self._tasks:
                await asyncio.gather(*list(self._tasks), return_exceptions=True)
            await asyncio.sleep(0.005)

    def close(self) -> None:
        """Drop pending closes (shutdown)."""
        for timer in self._closing.values():
            timer.cancel()
        self._closing.clear()


def get_recorder(app_state: Any) -> StateRecorder:
    """:returns: The app's :class:`StateRecorder`, created on first use."""
    recorder = getattr(app_state, RECORDER_STATE_ATTR, None)
    if not isinstance(recorder, StateRecorder):
        recorder = StateRecorder(app_state)
        setattr(app_state, RECORDER_STATE_ATTR, recorder)
    return recorder


# ── Reading ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Segment:
    """
    One recorded stretch of a session, in epoch milliseconds.

    :param state: ``working`` or ``waiting``.
    :param end: ``None`` while open.
    :param cost_start: Cumulative session cost when it opened.
    :param cost_end: … when it closed.
    """

    state: str
    start: int
    end: int | None
    outcome: str
    cost_start: float | None = None
    cost_end: float | None = None


def _num(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def from_span(span: FactorySpan) -> Segment | None:
    """:returns: The segment a span records, or ``None`` for other spans."""
    if span.kind not in SEGMENT_KINDS or not (span.dedupe_key or "").startswith("state:"):
        return None
    attrs = span.attrs or {}
    state = attrs.get("segment")
    if state not in (WORKING, WAITING):
        state = WORKING if span.kind == "turn" else WAITING
    return Segment(
        state=str(state),
        start=span.started_at,
        end=span.ended_at,
        outcome=span.outcome,
        cost_start=_num(attrs.get("cost_start")),
        cost_end=_num(attrs.get("cost_end")),
    )


def segments(
    deps: FactoryDeps,
    factory_id: str,
    session_id: str,
    since_ms: int,
    until_ms: int,
    *,
    lookback_ms: int = 86_400_000,
    limit: int = 5000,
) -> list[Segment]:
    """
    One session's segments that overlap ``[since_ms, until_ms)``, oldest first (synchronous).

    :param lookback_ms: How far before *since_ms* a still-overlapping segment may have begun.
    """
    rows = deps.factory_store.list_spans(
        factory_id,
        kinds=list(SEGMENT_KINDS),
        since_ms=since_ms - lookback_ms,
        until_ms=until_ms,
        session_id=session_id,
        limit=limit,
    )
    out = [seg for row in rows if (seg := from_span(row)) is not None]
    return [s for s in out if (s.end is None or s.end > since_ms) and s.start < until_ms]


def clip_open(
    found: Sequence[Segment], running: bool, last_activity_ms: int | None
) -> list[Segment]:
    """
    End an open segment the session no longer runs at its last activity.

    :param running: Whether the session runs (or waits) right now.
    :param last_activity_ms: Its last activity, epoch ms.
    """
    out: list[Segment] = []
    for seg in found:
        if seg.end is None and not running:
            end = max(seg.start, last_activity_ms or seg.start)
            seg = Segment(seg.state, seg.start, end, "ok", seg.cost_start, seg.cost_end)
        out.append(seg)
    return out


# ── The strip ──────────────────────────────────────────────────

STUCK: Final = "stuck"
CHECKS: Final = "checks"
STRIP_STATES: Final = (WAITING, STUCK, WORKING, CHECKS, IDLE)
"""Strip states, strongest first: a parked prompt beats stuck, stuck beats working, …"""
_RANK: Final = {state: len(STRIP_STATES) - i for i, state in enumerate(STRIP_STATES)}

Interval = tuple[int, int]


def _clip(interval: Interval, since_ms: int, until_ms: int) -> Interval | None:
    start, end = max(interval[0], since_ms), min(interval[1], until_ms)
    return (start, end) if end > start else None


def compose_strip(
    since_ms: int, until_ms: int, layers: Mapping[str, Sequence[Interval]]
) -> list[dict[str, Any]]:
    """
    One row of strip pieces over ``[since_ms, until_ms)``: the strongest layer wins each moment.

    :param layers: State (``working``, ``waiting``, ``stuck``, ``checks``) → intervals in ms.
    :returns: ``[{state, start, end}]`` oldest first, adjacent same states merged;
        uncovered time is ``idle``.
    """
    clipped = {
        state: [c for iv in found if (c := _clip(iv, since_ms, until_ms)) is not None]
        for state, found in layers.items()
    }
    points = sorted(
        {since_ms, until_ms, *(t for found in clipped.values() for iv in found for t in iv)}
    )
    pieces: list[dict[str, Any]] = []
    for start, end in itertools.pairwise(points):
        if end <= start:
            continue
        mid = (start + end) / 2
        state = max(
            (st for st, found in clipped.items() if any(a <= mid < b for a, b in found)),
            key=lambda st: _RANK.get(st, 0),
            default=IDLE,
        )
        if pieces and pieces[-1]["state"] == state and pieces[-1]["end"] == start:
            pieces[-1]["end"] = end
        else:
            pieces.append({"state": state, "start": start, "end": end})
    return pieces


def totals(pieces: Sequence[dict[str, Any]]) -> dict[str, int]:
    """:returns: Seconds per strip state (every state present, zero when absent)."""
    out = dict.fromkeys(STRIP_STATES, 0)
    for piece in pieces:
        out[piece["state"]] = out.get(piece["state"], 0) + (piece["end"] - piece["start"]) // 1000
    return out


def bucket_index(ts_ms: float, since_ms: int, bucket_ms: int, n: int) -> int | None:
    """:returns: The bucket *ts_ms* falls in, or ``None`` outside the window."""
    if ts_ms < since_ms:
        return None
    index = int((ts_ms - since_ms) // bucket_ms)
    return index if index < n else None


def spend_series(
    found: Sequence[Segment], since_ms: int, until_ms: int, bucket_ms: int
) -> list[float]:
    """
    Spend per bucket from the cumulative costs segments recorded when they opened and closed.

    A segment's own growth is spread over its time; growth between two segments
    (a cost that landed after its turn ended) goes to the later one's start.
    """
    n = max(1, -(-(until_ms - since_ms) // bucket_ms))
    out = [0.0] * n
    previous_end: float | None = None
    for seg in sorted(found, key=lambda s: s.start):
        if previous_end is not None and seg.cost_start is not None:
            gap = seg.cost_start - previous_end
            index = bucket_index(seg.start, since_ms, bucket_ms, n)
            if gap > 0 and index is not None:
                out[index] += gap
        if seg.cost_start is not None and seg.cost_end is not None:
            grown = seg.cost_end - seg.cost_start
            end = seg.end if seg.end is not None else until_ms
            span = max(1, end - seg.start)
            for i in range(n):
                lo = since_ms + i * bucket_ms
                overlap = min(end, lo + bucket_ms) - max(seg.start, lo)
                if overlap > 0 and grown > 0:
                    out[i] += grown * overlap / span
        last = seg.cost_end if seg.cost_end is not None else seg.cost_start
        previous_end = last if last is not None else previous_end
    return [round(v, 4) for v in out]


__all__ = [
    "CHECKS",
    "IDLE",
    "KIND_BY_STATE",
    "RECORDER_STATE_ATTR",
    "SEGMENT_KINDS",
    "STRIP_STATES",
    "STUCK",
    "WAITING",
    "WORKING",
    "Segment",
    "StateRecorder",
    "bucket_index",
    "clip_open",
    "compose_strip",
    "from_span",
    "get_recorder",
    "segments",
    "spend_series",
    "state_of",
    "totals",
]
