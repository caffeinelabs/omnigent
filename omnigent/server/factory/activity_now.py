"""Live "now" for every agent: what each one is doing, as the server observes it.

A poller, not a hot-path tap. Every :data:`POLL_S`, and only for factories
someone has open (``presence_count > 0``), it reads the newest
:data:`ITEMS_PER_SESSION` conversation items of each running member and
sub-agent (at most :data:`MAX_SESSIONS` reads per tick; the status comes from
the supervisor's child status) and builds a ``NowState`` per agent: current
tool and argument, since when, last event, model, cost and $/h, flags.

It also keeps the last :data:`RING_MAX` activity lines per factory (the
Overview's "Live now"), detects an agent repeating the same failing tool call
(``agent.repeat``) or gone quiet while running, and publishes a ``now`` frame
at most every :data:`FRAME_INTERVAL_S`.

Spike S7: claude-native transcripts are forwarded every 0.25 s and codex-native
posts a command's call at ``item/started``, so items land within about a
second; the poll costs about 1 ms per session (60 sessions × 3000 items on
SQLite: ~50 ms per tick). No side-channel tap is needed.

All state is in memory on one replica and rebuilt by the next poll.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from omnigent.cli_diagnostics import redact_secrets
from omnigent.factory import LABEL_PROJECT
from omnigent.server.factory import live
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.envelope import display_title
from omnigent.server.factory.membership import factory_members, is_top_level, resolve_role
from omnigent.stores.conversation_store import runner_seen_is_fresh
from omnigent.stores.factory_store import norm_id
from omnigent.util.session_lifecycle import is_session_closed

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.conversation import ConversationItem

_logger = logging.getLogger(__name__)

POLL_S: Final = 10.0
ITEMS_PER_SESSION: Final = 20
MAX_SESSIONS: Final = 60
"""Item reads per factory per tick."""
RING_MAX: Final = 200
FRAME_INTERVAL_S: Final = 2.0
TOOL_ARG_MAX: Final = 80
LINE_TEXT_MAX: Final = 160
COST_WINDOW_S: Final = 1800.0
"""$/h is the spend over (at most) this window."""
MIN_RATE_SPAN_S: Final = 60.0
EVICT_AFTER_S: Final = 900.0
"""A factory nobody watched for this long drops its cache."""
_SEEN_MAX: Final = 400
_CHILDREN_PER_MEMBER: Final = 200

STATUS_RUNNING: Final = "running"
STATUS_WAITING: Final = "waiting"
STATUS_IDLE: Final = "idle"
STATUS_FAILED: Final = "failed"
FLAG_REPEAT: Final = "repeat"
FLAG_QUIET: Final = "quiet"
FLAG_DISCONNECTED: Final = "disconnected"

LABEL_CONTEXT_TOKENS: Final = "omnigent.last_context_tokens"
LABEL_CONTEXT_WINDOW: Final = "omnigent.last_context_window"

# The main argument of a tool call, by preference.
_ARG_KEYS: Final = (
    "command",
    "cmd",
    "file_path",
    "path",
    "pattern",
    "url",
    "query",
    "skill",
    "title",
    "description",
    "prompt",
)
# Arguments that change between otherwise identical calls.
_VOLATILE_ARG_KEYS: Final = frozenset({"description", "timeout", "cwd", "run_in_background"})
_SHELL_WRAP_RE: Final = re.compile(r"^/bin/(?:ba|z)?sh -l?c '(.*)'$", re.DOTALL)
# claude-native: "Exit code 1 …" / "<tool_use_error>"; codex-native: "… [exit code: 1]".
_FAILED_OUTPUT_RES: Final = (
    re.compile(r"^\s*Exit code [1-9][0-9]*\b"),
    re.compile(r"^\s*<tool_use_error>"),
    re.compile(r"\[exit code: [1-9][0-9]*\]\s*$"),
    re.compile(r"^\s*Error: "),
)


# ── Pure parsing (fixtures drive these) ────────────────────────


def _one_line(text: str, limit: int) -> str:
    flat = " ".join(redact_secrets(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _arguments(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {"_raw": raw}
    return parsed if isinstance(parsed, dict) else {"_raw": parsed}


def _unwrap_shell(command: str) -> str:
    match = _SHELL_WRAP_RE.match(command.strip())
    return match.group(1) if match else command


def tool_arg(arguments: str) -> str | None:
    """
    The main argument of a tool call, redacted, on one line, ≤ 80 chars.

    :param arguments: The call's JSON arguments, e.g. ``'{"command": "pnpm test"}'``.
    :returns: e.g. ``"pnpm test"``, or ``None`` when the call has no text argument.
    """
    args = _arguments(arguments)
    for key in (*_ARG_KEYS, "_raw"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            text = _unwrap_shell(value) if key in ("command", "cmd") else value
            return _one_line(text, TOOL_ARG_MAX)
    changes = args.get("changes")
    if isinstance(changes, list):
        # codex-native apply_patch: the files it touches.
        paths = [c.get("path") for c in changes if isinstance(c, dict) and c.get("path")]
        if paths:
            more = f" +{len(paths) - 1} more" if len(paths) > 1 else ""
            return _one_line(f"{paths[0]}{more}", TOOL_ARG_MAX)
    for value in args.values():
        if isinstance(value, str) and value.strip():
            return _one_line(value, TOOL_ARG_MAX)
    return None


def args_hash(name: str, arguments: str) -> str:
    """
    A stable key for "the same call": the tool and its normalized arguments.

    :returns: ``"<tool>:<12 hex>"``; whitespace and volatile keys
        (``description``, ``timeout``, ``cwd``) do not change it.
    """

    def norm(value: Any) -> Any:
        if isinstance(value, str):
            return " ".join(_unwrap_shell(value).split())
        if isinstance(value, dict):
            return {k: norm(v) for k, v in sorted(value.items()) if k not in _VOLATILE_ARG_KEYS}
        if isinstance(value, list):
            return [norm(v) for v in value]
        return value

    body = json.dumps(norm(_arguments(arguments)), sort_keys=True, separators=(",", ":"))
    return f"{name}:{hashlib.sha1(body.encode()).hexdigest()[:12]}"


def is_failed_output(output: str) -> bool:
    """:returns: Whether a tool result reads as a failure (non-zero exit or tool error)."""
    return any(pattern.search(output or "") for pattern in _FAILED_OUTPUT_RES)


@dataclass(frozen=True)
class ToolCall:
    """
    One tool call seen in a session's items.

    :param key: :func:`args_hash` of the call (repeat detection).
    :param started_at: Epoch seconds the call was recorded.
    :param ended_at: Epoch seconds of its result, ``None`` while it runs.
    :param failed: Whether the result reads as a failure; ``None`` while it runs.
    """

    call_id: str
    name: str
    arg: str | None
    key: str
    started_at: int
    ended_at: int | None = None
    failed: bool | None = None
    output_head: str | None = None
    output_item_id: str | None = None


def parse_calls(items: Sequence[ConversationItem]) -> list[ToolCall]:
    """
    Pair ``function_call`` items with their outputs.

    :param items: Items oldest first (as stored; same-second items keep that order).
    :returns: Calls oldest first; a call whose output is outside *items* reads as running.
    """
    ordered = sorted(items, key=lambda i: i.created_at)
    outputs: dict[str, ConversationItem] = {}
    for item in ordered:
        if item.type == "function_call_output":
            outputs[getattr(item.data, "call_id", "")] = item
    calls: list[ToolCall] = []
    for item in ordered:
        if item.type != "function_call":
            continue
        data = item.data
        call_id = getattr(data, "call_id", "")
        name = getattr(data, "name", "") or "tool"
        arguments = getattr(data, "arguments", "") or ""
        out = outputs.get(call_id)
        text = getattr(out.data, "output", "") if out is not None else None
        calls.append(
            ToolCall(
                call_id=call_id,
                name=name,
                arg=tool_arg(arguments),
                key=args_hash(name, arguments),
                started_at=item.created_at,
                ended_at=out.created_at if out is not None else None,
                failed=is_failed_output(text or "") if out is not None else None,
                output_head=_one_line(text, LINE_TEXT_MAX) if text else None,
                output_item_id=out.id if out is not None else None,
            )
        )
    return calls


def current_call(calls: Sequence[ToolCall], status: str) -> ToolCall | None:
    """:returns: The newest call still running, while the session is running."""
    if status not in (STATUS_RUNNING, STATUS_WAITING):
        return None
    for call in reversed(calls):
        if call.ended_at is None:
            return call
    return None


def _text_of(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts = [b.get("text") for b in content if isinstance(b, dict)]
    return " ".join(p for p in parts if isinstance(p, str))


def activity_lines(
    items: Sequence[ConversationItem],
    *,
    session_id: str,
    title: str,
    role: str | None,
    project: str | None,
) -> list[dict[str, Any]]:
    """
    The "Live now" lines of some items: tool calls, failed results, replies, thinking.

    :param items: Items oldest first.
    :returns: Lines oldest first: ``{ts, session_id, title, role, project, kind,
        tool, text, failed}`` with ``kind`` one of ``tool`` / ``result`` /
        ``message`` / ``thinking`` / ``human``.
    """
    calls = {c.call_id: c for c in parse_calls(items)}
    lines: list[dict[str, Any]] = []

    def line(item: ConversationItem, kind: str, text: str, **extra: Any) -> None:
        lines.append(
            {
                "id": item.id,
                "ts": item.created_at,
                "session_id": session_id,
                "title": title,
                "role": role,
                "project": project,
                "kind": kind,
                "tool": extra.get("tool"),
                "text": _one_line(text, LINE_TEXT_MAX),
                "failed": extra.get("failed"),
            }
        )

    for item in sorted(items, key=lambda i: i.created_at):
        data = item.data
        if item.type == "function_call":
            call = calls.get(getattr(data, "call_id", ""))
            name = getattr(data, "name", "tool")
            arg = call.arg if call is not None else None
            line(item, "tool", f"{name} {arg}" if arg else name, tool=name)
        elif item.type == "function_call_output":
            output = getattr(data, "output", "") or ""
            if is_failed_output(output):
                call = calls.get(getattr(data, "call_id", ""))
                tool = call.name if call is not None else None
                line(item, "result", output, tool=tool, failed=True)
        elif item.type == "reasoning":
            text = _text_of(getattr(data, "content", None)) or _text_of(
                getattr(data, "summary", None)
            )
            if text.strip():
                line(item, "thinking", text)
        elif item.type == "message":
            text = _text_of(getattr(data, "content", None))
            if not text.strip() or getattr(data, "is_meta", False):
                continue
            if getattr(data, "role", "") == "assistant":
                line(item, "message", text)
            elif item.created_by:
                line(item, "human", text)
    return lines


def context_pct(labels: Mapping[str, str]) -> float | None:
    """:returns: Context window use in percent, only when the harness reported both numbers."""
    try:
        used = float(labels.get(LABEL_CONTEXT_TOKENS) or 0)
        window = float(labels.get(LABEL_CONTEXT_WINDOW) or 0)
    except ValueError:
        return None
    if used <= 0 or window <= 0:
        return None
    return round(min(100.0, used * 100.0 / window), 1)


def own_cost(conv: Conversation) -> float | None:
    """:returns: The session's own priced spend, ``None`` when unpriced."""
    raw = (conv.session_usage or {}).get("total_cost_usd")
    return round(float(raw), 4) if isinstance(raw, int | float) else None


def build_now(
    conv: Conversation,
    items: Sequence[ConversationItem] | None,
    *,
    status: str,
    now: float,
    quiet_s: float,
    usd_per_h: float | None = None,
    turn_n: int | None = None,
    flags: Iterable[str] = (),
) -> dict[str, Any]:
    """
    One agent's ``NowState`` JSON.

    :param items: Its newest items, or ``None`` when they were not read this tick.
    :param status: ``idle`` / ``running`` / ``waiting`` / ``failed``.
    :param quiet_s: Running with no new item this long sets the ``quiet`` flag.
    :param flags: Flags decided elsewhere (``repeat``, ``disconnected``).
    """
    calls = parse_calls(items or [])
    tool = current_call(calls, status)
    newest = max((i.created_at for i in items or []), default=None)
    last_event = newest if newest is not None else int(conv.updated_at)
    out_flags = [f for f in (FLAG_REPEAT, FLAG_DISCONNECTED) if f in set(flags)]
    if status == STATUS_RUNNING and now - last_event >= quiet_s:
        out_flags.insert(0, FLAG_QUIET)
    return {
        "session_id": conv.id,
        "status": status,
        "turn_n": turn_n,
        "tool": tool.name if tool is not None else None,
        "tool_arg": tool.arg if tool is not None else None,
        "tool_since": tool.started_at if tool is not None else None,
        "last_event_at": last_event,
        "model": conv.reported_model or conv.model_override,
        "cost_usd": own_cost(conv),
        "usd_per_h": usd_per_h,
        "ctx_pct": context_pct(conv.labels),
        "flags": out_flags,
    }


# ── Repeat detection ───────────────────────────────────────────


@dataclass
class RepeatTracker:
    """
    Failing calls per ``(tool, args)`` key for one session.

    :param window_s: How far back failures count.
    """

    window_s: float
    failures: dict[str, deque[tuple[float, str | None]]] = field(default_factory=dict)
    counted: deque[str] = field(default_factory=lambda: deque(maxlen=_SEEN_MAX))

    def observe(self, calls: Iterable[ToolCall]) -> None:
        """Count each finished call once: a failure adds, a success of the same call clears."""
        known = set(self.counted)
        for call in calls:
            if call.ended_at is None or call.call_id in known:
                continue
            self.counted.append(call.call_id)
            known.add(call.call_id)
            if call.failed:
                self.failures.setdefault(call.key, deque()).append((call.ended_at, call.arg))
            else:
                self.failures.pop(call.key, None)

    def worst(self, now: float) -> tuple[str, int, str | None] | None:
        """:returns: ``(key, failures in the window, argument)`` of the most repeated call."""
        best: tuple[str, int, str | None] | None = None
        for key in list(self.failures):
            window = self.failures[key]
            while window and window[0][0] <= now - self.window_s:
                window.popleft()
            if not window:
                del self.failures[key]
                continue
            if best is None or len(window) > best[1]:
                best = (key, len(window), window[-1][1])
        return best


# ── Per-factory state ──────────────────────────────────────────


@dataclass
class _Track:
    """What the poller remembers about one session between ticks."""

    seen: deque[str] = field(default_factory=lambda: deque(maxlen=_SEEN_MAX))
    responses: set[str] = field(default_factory=set)
    costs: deque[tuple[float, float]] = field(default_factory=deque)
    repeats: RepeatTracker | None = None
    repeat_raised: str | None = None
    repeat_count: int = 0
    repeat_at: float = 0.0
    quiet_raised: bool = False
    role: str | None = None
    role_known: bool = False


@dataclass
class FactoryNow:
    """
    The live state of one factory.

    :param states: Session id -> ``NowState`` JSON.
    :param ring: The newest activity lines, oldest first.
    :param polled_at: Epoch seconds of the last poll.
    """

    states: dict[str, dict[str, Any]] = field(default_factory=dict)
    ring: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=RING_MAX))
    tracks: dict[str, _Track] = field(default_factory=dict)
    polled_at: float | None = None


class NowRegistry:
    """Every factory's live state on this replica."""

    def __init__(self) -> None:
        self._factories: dict[str, FactoryNow] = {}

    def get(self, factory_id: str) -> FactoryNow:
        """:returns: *factory_id*'s state, created empty on first use."""
        return self._factories.setdefault(norm_id(factory_id), FactoryNow())

    def peek(self, factory_id: str) -> FactoryNow | None:
        """:returns: *factory_id*'s state, or ``None`` before its first poll."""
        try:
            return self._factories.get(norm_id(factory_id))
        except ValueError:
            return None

    def states(self, factory_id: str) -> dict[str, dict[str, Any]]:
        """:returns: Session id -> ``NowState`` (empty before the first poll)."""
        found = self.peek(factory_id)
        return dict(found.states) if found is not None else {}

    def lines(self, factory_id: str, limit: int = RING_MAX) -> list[dict[str, Any]]:
        """:returns: The newest activity lines, oldest first."""
        found = self.peek(factory_id)
        return list(found.ring)[-limit:] if found is not None else []

    def evict_idle(self, now: float) -> None:
        """Drop factories no one polled for :data:`EVICT_AFTER_S`."""
        for fid in [
            f
            for f, s in self._factories.items()
            if s.polled_at is not None and now - s.polled_at > EVICT_AFTER_S
        ]:
            del self._factories[fid]

    def reset(self) -> None:
        """Forget everything (tests)."""
        self._factories.clear()


registry = NowRegistry()
"""The process's live state."""


def now_states(factory_id: str) -> dict[str, dict[str, Any]]:
    """:returns: Session id -> ``NowState`` of *factory_id* (the snapshot's ``members[].now``)."""
    return registry.states(factory_id)


def factory_usd_per_h(factory_id: str) -> float | None:
    """:returns: The sum of the agents' $/h at the last poll, ``None`` when unpriced."""
    rates = [s["usd_per_h"] for s in registry.states(factory_id).values() if s.get("usd_per_h")]
    return round(sum(rates), 2) if rates else None


# ── Frame throttle ─────────────────────────────────────────────


class FrameThrottle:
    """
    At most one ``now`` frame per factory every :data:`FRAME_INTERVAL_S`.

    A request inside the interval is deferred to its end (one pending per factory).

    :param publish: Sends one frame for a factory id.
    :param clock: Monotonic seconds.
    """

    def __init__(
        self,
        publish: Callable[[str], None],
        *,
        interval_s: float = FRAME_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._publish = publish
        self._interval = interval_s
        self._clock = clock
        self._last: dict[str, float] = {}
        self._pending: dict[str, asyncio.TimerHandle] = {}

    def request(self, factory_id: str) -> bool:
        """
        Publish now, or once the interval since the last frame has passed.

        :returns: ``True`` when the frame went out now.
        """
        now = self._clock()
        last = self._last.get(factory_id)
        if last is None or now - last >= self._interval:
            self._fire(factory_id)
            return True
        if factory_id not in self._pending:
            loop = asyncio.get_running_loop()
            delay = self._interval - (now - last)
            self._pending[factory_id] = loop.call_later(delay, self._fire, factory_id)
        return False

    def _fire(self, factory_id: str) -> None:
        self._pending.pop(factory_id, None)
        self._last[factory_id] = self._clock()
        try:
            self._publish(factory_id)
        except Exception:  # noqa: BLE001 - the cockpit falls back to polling
            _logger.warning("factory: now frame for %s failed", factory_id, exc_info=True)

    def cancel(self) -> None:
        """Drop pending frames."""
        for handle in self._pending.values():
            handle.cancel()
        self._pending.clear()


# ── The poller ─────────────────────────────────────────────────


@dataclass(frozen=True)
class _Session:
    conv: Conversation
    top: Conversation
    status: str


def _raw_status(conv: Conversation, child_status: Callable[[str], str | None]) -> str:
    from omnigent.server.routes._sessions.helpers import (
        _session_status_cache,
        _session_status_from_cache,
    )

    status = child_status(conv.id) or _session_status_from_cache(conv.id, conv.live_status)
    fine = _session_status_cache.get(conv.id) or conv.live_status
    if status == STATUS_RUNNING and fine == STATUS_WAITING:
        return STATUS_WAITING
    return status


def _load_sessions(
    deps: FactoryDeps, factory_id: str, child_status: Callable[[str], str | None]
) -> list[_Session]:
    """Open members, then their open sub-agents (newest first per member)."""
    store = deps.conversation_store
    members = [
        m
        for m in factory_members(factory_id, store)
        if not m.archived and not is_session_closed(m.labels, m.title)
    ]
    members.sort(key=lambda m: (m.id != factory_id, m.created_at))
    out = [_Session(m, m, _raw_status(m, child_status)) for m in members]
    for member in members:
        page = store.list_conversations(
            limit=_CHILDREN_PER_MEMBER,
            kind="sub_agent",
            parent_conversation_id=member.id,
            order="desc",
        )
        out.extend(
            _Session(c, member, _raw_status(c, child_status))
            for c in page.data
            if not c.archived and not is_session_closed(c.labels, c.title)
        )
    return out


def pick_reads(sessions: Sequence[_Session], limit: int = MAX_SESSIONS) -> list[_Session]:
    """
    The sessions whose items are read this tick: running ones, members first,
    then the most recently active sub-agents, at most *limit*.
    """
    running = [s for s in sessions if s.status in (STATUS_RUNNING, STATUS_WAITING)]
    running.sort(key=lambda s: (not is_top_level(s.conv), -s.conv.updated_at))
    return running[:limit]


class ActivityPoller:
    """
    The "now" poller of every factory on this server.

    :param app_state: ``app.state`` carrying the factory deps.
    :param presence: Viewers of a factory (the poll runs only above zero).
    :param clock: Wall clock in epoch seconds.
    :param sleep: Awaitable sleep between ticks.
    :param publish: Sends one ``now`` frame for a factory id.
    """

    def __init__(
        self,
        app_state: Any,
        *,
        presence: Callable[[str], int] | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        publish: Callable[[str], None] | None = None,
        now_registry: NowRegistry | None = None,
        anyone_watching: Callable[[], bool] | None = None,
    ) -> None:
        from omnigent.server.factory.presence import anyone_present, presence_count

        self._app_state = app_state
        self._presence = presence or presence_count
        # With an injected per-factory count, every root is checked against it.
        self._anyone = anyone_watching or (anyone_present if presence is None else None)
        self._clock = clock
        self._sleep = sleep
        self.registry = now_registry or registry
        self.throttle = FrameThrottle(publish or self._announce)
        self._task: asyncio.Task[None] | None = None
        self.reads_last_tick: dict[str, int] = {}

    def _announce(self, factory_id: str) -> None:
        live.announce_factory_changed(self._app_state, factory_id, ["now"])

    def _deps(self) -> FactoryDeps | None:
        deps = getattr(self._app_state, FACTORY_STATE_ATTR, None)
        return deps if isinstance(deps, FactoryDeps) else None

    def _child_status(self, session_id: str) -> str | None:
        from omnigent.server.factory.supervisor import get_supervisor

        supervisor = get_supervisor(self._app_state)
        return supervisor.child_status(session_id) if supervisor is not None else None

    # ── lifecycle ──

    def start(self) -> None:
        """Poll every :data:`POLL_S` until :meth:`stop`."""
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(
                self._run(), name="factory-activity-now"
            )

    async def _run(self) -> None:
        while True:
            await self._sleep(POLL_S)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("factory: activity poll failed")

    async def stop(self) -> None:
        """Stop polling."""
        self.throttle.cancel()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    # ── one tick ──

    async def tick(self) -> None:
        """Poll every factory someone is watching."""
        from omnigent.server.factory.snapshot import list_factory_roots

        deps = self._deps()
        if deps is None:
            return
        self.reads_last_tick = {}
        if self._anyone is not None and not self._anyone():
            # Nobody has any cockpit open: no database reads at all.
            self.registry.evict_idle(self._clock())
            return
        roots = await asyncio.to_thread(list_factory_roots, deps.conversation_store)
        for root in roots:
            if self._presence(root.id) <= 0:
                continue
            try:
                await self.poll_factory(root.id)
            except Exception:  # noqa: BLE001 - one factory must not stop the others
                _logger.warning("factory: now poll of %s failed", root.id, exc_info=True)
        self.registry.evict_idle(self._clock())

    async def poll_factory(self, factory_id: str) -> bool:
        """
        Poll one factory now.

        :returns: Whether anything an observer sees changed (a frame was requested).
        """
        from omnigent.server.factory import detectors

        deps = self._deps()
        if deps is None:
            return False
        now = self._clock()
        sessions = await asyncio.to_thread(_load_sessions, deps, factory_id, self._child_status)
        reads = pick_reads(sessions)
        store = deps.conversation_store

        def _read() -> dict[str, list[ConversationItem]]:
            # The newest page, put back in stored (chronological) order.
            return {
                s.conv.id: list(
                    reversed(
                        store.list_items(s.conv.id, limit=ITEMS_PER_SESSION, order="desc").data
                    )
                )
                for s in reads
            }

        items = await asyncio.to_thread(_read)
        self.reads_last_tick[norm_id(factory_id)] = len(items)
        state = self.registry.get(factory_id)
        unknown = [
            s.conv for s in sessions if not state.tracks.setdefault(s.conv.id, _Track()).role_known
        ]
        # A session's role never changes: resolve it (a spec lookup) once.
        found = await asyncio.to_thread(lambda: {c.id: resolve_role(c, deps) for c in unknown})
        for sid, role in found.items():
            state.tracks[sid].role, state.tracks[sid].role_known = role, True
        roles = {s.conv.id: state.tracks[s.conv.id].role for s in sessions}
        quiet_s = detectors.threshold(factory_id, "quiet_m") * 60
        window_s = detectors.threshold(factory_id, "repeat_window_m") * 60
        repeat_n = int(detectors.threshold(factory_id, "repeat_tool_n"))
        down = await asyncio.to_thread(self._disconnected, [s.conv for s in sessions], now)
        changed = False
        fresh: dict[str, dict[str, Any]] = {}
        for session in sessions:
            conv = session.conv
            sid = conv.id
            track = state.tracks.setdefault(sid, _Track())
            got = items.get(sid)
            flags: list[str] = []
            if sid in down:
                flags.append(FLAG_DISCONNECTED)
            if got is not None:
                changed |= self._remember_lines(state, track, session, got, roles.get(sid))
                track.responses.update(i.response_id for i in got if i.response_id)
                if track.repeats is None or track.repeats.window_s != window_s:
                    track.repeats = RepeatTracker(window_s)
                track.repeats.observe(parse_calls(got))
            worst = track.repeats.worst(now) if track.repeats is not None else None
            repeating = worst is not None and worst[1] >= repeat_n
            if repeating:
                flags.append(FLAG_REPEAT)
            rate = self._rate(track, conv, now)
            view = build_now(
                conv,
                got,
                status=session.status,
                now=now,
                quiet_s=quiet_s,
                usd_per_h=rate,
                turn_n=len(track.responses) or None,
                flags=flags,
            )
            if got is None and sid in state.states:
                # Not read this tick: keep the last observed tool of a still-running agent.
                view = self._carry(state.states[sid], view)
            fresh[sid] = view
            if _visible(state.states.get(sid)) != _visible(view):
                changed = True
            await self._detect(
                factory_id, session, roles.get(sid), track, view, worst if repeating else None
            )
        for sid in [s for s in state.tracks if s not in fresh]:
            await self._forget(factory_id, sid, state.tracks.pop(sid))
        if set(state.states) != set(fresh):
            changed = True
        state.states = fresh
        state.polled_at = now
        if changed:
            self.throttle.request(norm_id(factory_id))
        return changed

    def _remember_lines(
        self,
        state: FactoryNow,
        track: _Track,
        session: _Session,
        got: Sequence[ConversationItem],
        role: str | None,
    ) -> bool:
        seen = set(track.seen)
        new = [i for i in got if i.id not in seen]
        if not new:
            return False
        track.seen.extend(i.id for i in new)
        conv = session.conv
        lines = activity_lines(
            new,
            session_id=conv.id,
            title=display_title(conv.title, conv.id),
            role=role,
            project=session.top.labels.get(LABEL_PROJECT),
        )
        if seen or len(state.ring) < RING_MAX:
            # The first read of a session only backfills an empty ring.
            merged = sorted([*state.ring, *lines], key=lambda line: line["ts"])
            state.ring.clear()
            state.ring.extend(merged[-RING_MAX:])
        return True

    @staticmethod
    def _carry(previous: Mapping[str, Any], view: dict[str, Any]) -> dict[str, Any]:
        if view["status"] not in (STATUS_RUNNING, STATUS_WAITING):
            return view
        for key in ("tool", "tool_arg", "tool_since", "turn_n"):
            if view.get(key) is None:
                view[key] = previous.get(key)
        view["last_event_at"] = max(
            view.get("last_event_at") or 0, previous.get("last_event_at") or 0
        )
        return view

    def _rate(self, track: _Track, conv: Conversation, now: float) -> float | None:
        cost = own_cost(conv)
        if cost is None:
            return None
        samples = track.costs
        if not samples or samples[-1][1] != cost or now - samples[-1][0] >= POLL_S:
            samples.append((now, cost))
        while len(samples) > 1 and samples[0][0] < now - COST_WINDOW_S:
            samples.popleft()
        first_t, first_c = samples[0]
        span = now - first_t
        if span < MIN_RATE_SPAN_S:
            return None
        return round(max(0.0, cost - first_c) * 3600.0 / span, 2)

    def _disconnected(self, convs: Sequence[Conversation], now: float) -> set[str]:
        """Sessions bound to a runner whose tunnel is gone (here and on the row)."""
        registry_ = getattr(self._app_state, "tunnel_registry", None)
        if registry_ is None:
            return set()
        return {
            c.id
            for c in convs
            if c.runner_id
            and registry_.get(c.runner_id) is None
            and not runner_seen_is_fresh(c.runner_last_seen, now=int(now))
        }

    async def _forget(self, factory_id: str, sid: str, track: _Track) -> None:
        """A session left the poll (closed, archived, gone): resolve what it raised."""
        from omnigent.server.factory import detectors

        target = f"member:{norm_id(sid)}"
        if track.repeat_raised is not None:
            await detectors.clear(
                self._app_state, factory_id, "agent.repeat", target, "agent closed"
            )
        if track.quiet_raised:
            await detectors.clear(
                self._app_state, factory_id, "agent.quiet", target, "agent closed"
            )

    async def _detect(
        self,
        factory_id: str,
        session: _Session,
        role: str | None,
        track: _Track,
        view: Mapping[str, Any],
        repeat: tuple[str, int, str | None] | None,
    ) -> None:
        from omnigent.server.factory import detectors

        conv = session.conv
        project = session.top.labels.get(LABEL_PROJECT)
        if repeat is not None:
            key, count, arg = repeat
            first = track.repeat_raised != key
            now = self._clock()
            # Re-raise a still-repeating agent now and then so its last_seen stays fresh.
            due = now - track.repeat_at >= detectors.RERAISE_S
            if first or count > track.repeat_count or due:
                await detectors.agent_repeat(
                    self._app_state,
                    factory_id,
                    conv,
                    role=role,
                    project=project,
                    tool=key.split(":", 1)[0],
                    arg=arg,
                    count=count,
                    first=first,
                )
                track.repeat_at = now
            track.repeat_raised, track.repeat_count = key, count
        elif track.repeat_raised is not None:
            track.repeat_raised, track.repeat_count = None, 0
            await detectors.clear(
                self._app_state, factory_id, "agent.repeat", f"member:{norm_id(conv.id)}"
            )
        quiet = FLAG_QUIET in view["flags"]
        if quiet and not track.quiet_raised:
            track.quiet_raised = True
            await detectors.agent_quiet(
                self._app_state,
                factory_id,
                conv,
                project=project,
                idle_s=time_since(view.get("last_event_at"), self._clock()),
            )
        elif not quiet and track.quiet_raised:
            track.quiet_raised = False
            await detectors.clear(
                self._app_state, factory_id, "agent.quiet", f"member:{norm_id(conv.id)}"
            )


def time_since(ts: int | None, now: float) -> int:
    """:returns: Whole seconds from *ts* to *now*, ``0`` without a stamp."""
    return max(0, int(now - ts)) if ts else 0


def _visible(state: Mapping[str, Any] | None) -> tuple[Any, ...] | None:
    """What a frame reports about one agent (cost and rate move every tick, so not them)."""
    if state is None:
        return None
    return (
        state.get("status"),
        state.get("tool"),
        state.get("tool_arg"),
        state.get("tool_since"),
        state.get("last_event_at"),
        tuple(state.get("flags") or ()),
    )


POLLER_STATE_ATTR: Final = "factory_activity_now"
"""``app.state`` attribute holding the running :class:`ActivityPoller`."""


def start(app_state: Any) -> Callable[[], Awaitable[None]]:
    """
    Start the poller on *app_state* (factory enabled).

    :returns: An async callable that stops it.
    """
    poller = ActivityPoller(app_state)
    setattr(app_state, POLLER_STATE_ATTR, poller)
    poller.start()
    _logger.info("factory activity poller started (every %ss while watched)", POLL_S)

    async def _stop() -> None:
        await poller.stop()
        if getattr(app_state, POLLER_STATE_ATTR, None) is poller:
            setattr(app_state, POLLER_STATE_ATTR, None)

    return _stop


def get_poller(app_state: Any) -> ActivityPoller | None:
    """:returns: The running poller of *app_state*, if any."""
    poller = getattr(app_state, POLLER_STATE_ATTR, None)
    return poller if isinstance(poller, ActivityPoller) else None


__all__ = [
    "FLAG_DISCONNECTED",
    "FLAG_QUIET",
    "FLAG_REPEAT",
    "ITEMS_PER_SESSION",
    "MAX_SESSIONS",
    "POLL_S",
    "RING_MAX",
    "ActivityPoller",
    "FactoryNow",
    "FrameThrottle",
    "NowRegistry",
    "RepeatTracker",
    "ToolCall",
    "activity_lines",
    "args_hash",
    "build_now",
    "context_pct",
    "current_call",
    "factory_usd_per_h",
    "get_poller",
    "is_failed_output",
    "now_states",
    "own_cost",
    "parse_calls",
    "pick_reads",
    "registry",
    "start",
    "tool_arg",
]
