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

Recording is dark by default: it runs only with ``OMNIGENT_FACTORY_STATE_SEGMENTS=1``,
or, when that is unset, with the new cockpit (``OMNIGENT_FACTORY_COCKPIT_V2=1``).
Segments older than :data:`RETENTION_S` are pruned daily (the strip reads at
most 7 days and a day of lookback).

:func:`segments` reads them back for one session and window, with the open one
clipped to the session's last activity when it no longer runs.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import LABEL_ID, cockpit_v2_enabled
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.membership import is_top_level

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactorySpan
    from omnigent.stores.factory_store import FactoryStore

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
STATE_SEGMENTS_ENV: Final = "OMNIGENT_FACTORY_STATE_SEGMENTS"
KEY_PREFIX: Final = "state:"
RETENTION_S: Final = 30 * 86400
"""Segments older than this are pruned (the strip reads at most 7 days plus a day)."""
ACTIVITY_SCAN: Final = 50
"""Newest items read to find when a segment left open by an earlier process really ended."""
_WHO_MAX: Final = 10_000
_BUSY: Final = frozenset({"running", "waiting"})


def recording_enabled() -> bool:
    """
    Whether status edges write segments.

    ``OMNIGENT_FACTORY_STATE_SEGMENTS`` decides when set (``1`` on, anything
    else off); unset, recording follows the new cockpit's flag.
    """
    raw = os.environ.get(STATE_SEGMENTS_ENV, "").strip()
    if raw:
        return raw == "1"
    return cockpit_v2_enabled()


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


def _is_input(item: Any) -> bool:
    return item.type == "message" and getattr(item.data, "role", None) == "user"


def last_activity_ms(
    deps: FactoryDeps, session_id: str, before_ms: int, *, skip_input: bool = False
) -> int | None:
    """
    When the session last did something before *before_ms* (synchronous; never raises).

    :param skip_input: Pass over user messages: a new turn's input lands just
        before its busy edge and is not the old turn's activity.
    :returns: The newest such item's ``created_at`` in epoch ms, or ``None``.
    """
    try:
        page = deps.conversation_store.list_items(session_id, limit=ACTIVITY_SCAN, order="desc")
    except Exception:  # noqa: BLE001 - the caller falls back to the segment's start
        _logger.debug("factory states: no items for %s", session_id, exc_info=True)
        return None
    for item in page.data:
        at = int(item.created_at) * 1000
        if at >= before_ms or (skip_input and _is_input(item)):
            continue
        return at
    return None


class StateRecorder:
    """
    Per-process segment writer: what is open for each session, and ordered writes.

    A busy edge opens a segment (or switches working and waiting); an idle or
    failed edge closes it after :data:`DEBOUNCE_S` unless the session is busy
    again by then, so a mid-turn lull does not split one turn. Writes run in
    worker threads, one session at a time in arrival order.

    A segment this process did not open (a restart or deploy left it open)
    ends at the session's last activity before the edge that finds it, not at
    the edge. Shutdown writes the pending closes at their idle edge's moment
    (:meth:`flush`).

    It assumes one server process sees all of a session's edges (today's single
    replica): with several replicas, a turn's start and end can land on
    different processes and a segment stays open until the next edge on the
    process that holds it.
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
        self._closing: dict[str, tuple[asyncio.TimerHandle, str, int]] = {}
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
        if state is None or not recording_enabled() or self._not_member(session_id):
            return
        if len(self._seen) > _WHO_MAX:
            self._seen.clear()
        first = session_id not in self._seen
        self._seen.add(session_id)
        at_ms = int(self._clock() * 1000)
        if state in KIND_BY_STATE:
            pending = self._closing.pop(session_id, None)
            if pending is not None:
                pending[0].cancel()
            held = self._open.get(session_id)
            if held == state:
                return
            self._open[session_id] = state
            # Nothing held here: whatever is still open was left by an earlier process.
            self._spawn(session_id, state, at_ms, stale=held is None)
            return
        if session_id not in self._open:
            if first:
                # A segment a server roll left open is closed at the first turn end seen.
                self._spawn(session_id, state, at_ms, stale=True)
            return
        if session_id in self._closing:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        timer = loop.call_later(self._debounce_s, self._close_due, session_id, state, at_ms)
        self._closing[session_id] = (timer, state, at_ms)

    def _close_due(self, session_id: str, state: str, at_ms: int) -> None:
        self._closing.pop(session_id, None)
        self._open.pop(session_id, None)
        self._spawn(session_id, state, at_ms)

    def _not_member(self, session_id: str) -> bool:
        cached = self._who.get(session_id)
        if cached is None or cached[1] is not None:
            return False
        return time.monotonic() - cached[0] < NOT_MEMBER_TTL_S

    def _spawn(self, session_id: str, state: str, at_ms: int, *, stale: bool = False) -> None:
        try:
            task = asyncio.get_running_loop().create_task(
                self._apply(session_id, state, at_ms, stale), name="factory-state-segment"
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

    async def _apply(self, session_id: str, state: str, at_ms: int, stale: bool) -> None:
        deps = getattr(self._app_state, FACTORY_STATE_ATTR, None)
        if not isinstance(deps, FactoryDeps):
            return
        try:
            async with self._lock(session_id):
                await asyncio.to_thread(self.write, deps, session_id, state, at_ms, stale=stale)
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

    def write(
        self, deps: FactoryDeps, session_id: str, state: str, at_ms: int, *, stale: bool = False
    ) -> None:
        """
        Close the session's open segment, then open *state*'s (synchronous).

        :param stale: The open segment was not opened by this process: it ends
            at the session's last activity before *at_ms*, not at *at_ms*.
        """
        from omnigent.server.factory.telemetry import spans

        conv = deps.conversation_store.get_conversation(session_id)
        who = self._identify(deps, conv) if conv is not None else None
        if conv is None or who is None:
            self._open.pop(session_id, None)
            return
        store = deps.factory_store
        cost_now = _cost(conv)
        outcome = spans.FAIL if state == FAILED else spans.OK
        stale_end: int | None = None
        for kind in SEGMENT_KINDS:
            for span in store.list_open_spans(who.factory_id, kind, session_id=session_id):
                if not (span.dedupe_key or "").startswith(KEY_PREFIX):
                    continue
                attrs = dict(span.attrs or {})
                end = at_ms
                if stale:
                    if stale_end is None:
                        stale_end = self._stale_end(deps, session_id, state, at_ms)
                    end = stale_end
                    attrs["closed_late"] = True
                if cost_now is not None:
                    attrs["cost_end"] = cost_now
                store.update_span(
                    span.id, ended_at=max(end, span.started_at), outcome=outcome, attrs=attrs
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

    @staticmethod
    def _stale_end(deps: FactoryDeps, session_id: str, state: str, at_ms: int) -> int:
        """
        Where a segment an earlier process left open ends: the session's last activity.

        A busy edge skips the new turn's input and, knowing nothing, ends it
        where it began (``0``, raised to its start); a turn end with no items
        keeps its own moment.
        """
        busy = state in KIND_BY_STATE
        found = last_activity_ms(deps, session_id, at_ms, skip_input=busy)
        if found is None:
            return 0 if busy else at_ms
        return min(found, at_ms)

    async def drain(self, timeout_s: float = 5.0) -> None:
        """Wait for pending closes and every scheduled write (tests and shutdown)."""
        deadline = time.monotonic() + timeout_s
        while (self._tasks or self._closing) and time.monotonic() < deadline:
            if self._tasks:
                await asyncio.gather(*list(self._tasks), return_exceptions=True)
            await asyncio.sleep(0.005)

    async def flush(self, timeout_s: float = 5.0) -> None:
        """Write every pending close at its idle edge's moment, then wait for the writes."""
        pending = list(self._closing.items())
        self._closing.clear()
        for session_id, (timer, state, at_ms) in pending:
            timer.cancel()
            self._open.pop(session_id, None)
            self._spawn(session_id, state, at_ms)
        await self.drain(timeout_s)

    def close(self) -> None:
        """Drop pending closes without writing them (tests)."""
        for timer, _state, _at in self._closing.values():
            timer.cancel()
        self._closing.clear()


def get_recorder(app_state: Any) -> StateRecorder:
    """:returns: The app's :class:`StateRecorder`, created on first use."""
    recorder = getattr(app_state, RECORDER_STATE_ATTR, None)
    if not isinstance(recorder, StateRecorder):
        recorder = StateRecorder(app_state)
        setattr(app_state, RECORDER_STATE_ATTR, recorder)
    return recorder


async def flush(app_state: Any) -> None:
    """Write the pending closes of the app's recorder, if it has one (server shutdown)."""
    recorder = getattr(app_state, RECORDER_STATE_ATTR, None)
    if isinstance(recorder, StateRecorder):
        await recorder.flush()


def prune(store: FactoryStore, now_ms: int) -> int:
    """Delete segments that ended more than :data:`RETENTION_S` ago (synchronous)."""
    return store.prune_spans(
        now_ms - RETENTION_S * 1000, list(SEGMENT_KINDS), key_prefix=KEY_PREFIX
    )


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
    if span.kind not in SEGMENT_KINDS or not (span.dedupe_key or "").startswith(KEY_PREFIX):
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

    Reads the newest rows first, so a cap drops the oldest, never the latest hours.

    :param lookback_ms: How far before *since_ms* a still-overlapping segment may have begun.
    """
    rows = deps.factory_store.list_spans(
        factory_id,
        kinds=list(SEGMENT_KINDS),
        since_ms=since_ms - lookback_ms,
        until_ms=until_ms,
        session_id=session_id,
        limit=limit,
        newest_first=True,
    )
    out = [seg for row in reversed(rows) if (seg := from_span(row)) is not None]
    return [s for s in out if (s.end is None or s.end > since_ms) and s.start < until_ms]


_STARTED: dict[str, tuple[float, int | None]] = {}
_STARTED_TTL_S: Final = 600.0
_STARTED_MISS_TTL_S: Final = 60.0


def recording_start(deps: FactoryDeps, factory_id: str) -> int | None:
    """
    When the factory's oldest kept segment began, epoch ms (cached; synchronous).

    One indexed read per kind, so it never walks the table.

    :returns: ``None`` while nothing is recorded.
    """
    now = time.monotonic()
    cached = _STARTED.get(factory_id)
    if cached is not None:
        ttl = _STARTED_TTL_S if cached[1] is not None else _STARTED_MISS_TTL_S
        if now - cached[0] < ttl:
            return cached[1]
    starts = [
        rows[0].started_at
        for kind in SEGMENT_KINDS
        if (rows := deps.factory_store.list_spans(factory_id, kinds=[kind], limit=1))
    ]
    found = min(starts) if starts else None
    if len(_STARTED) > _WHO_MAX:
        _STARTED.clear()
    _STARTED[factory_id] = (now, found)
    return found


def reset_cache() -> None:
    """Forget cached recording starts (tests)."""
    _STARTED.clear()


def coverage(
    recorded_from_ms: int | None, born_ms: int | None, since_ms: int
) -> tuple[int | None, bool]:
    """
    How much of a window the segments can speak for.

    :param recorded_from_ms: When the factory began recording (:func:`recording_start`).
    :param born_ms: When the session was created; before that it did not exist.
    :param since_ms: The window's start.
    :returns: ``(covered_from, partial)``: when the record for this session
        begins (``None`` while nothing is recorded) and whether some of the
        window it existed in predates the recording.
    """
    if recorded_from_ms is None:
        return None, True
    existed_from = max(since_ms, born_ms or since_ms)
    return max(recorded_from_ms, born_ms or 0), recorded_from_ms > existed_from


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
    "KEY_PREFIX",
    "KIND_BY_STATE",
    "RECORDER_STATE_ATTR",
    "RETENTION_S",
    "SEGMENT_KINDS",
    "STATE_SEGMENTS_ENV",
    "STRIP_STATES",
    "STUCK",
    "WAITING",
    "WORKING",
    "Segment",
    "StateRecorder",
    "bucket_index",
    "clip_open",
    "compose_strip",
    "coverage",
    "flush",
    "from_span",
    "get_recorder",
    "last_activity_ms",
    "prune",
    "recording_enabled",
    "recording_start",
    "reset_cache",
    "segments",
    "spend_series",
    "state_of",
    "totals",
]
