"""The "now" poller: NowState from recorded harness items, the presence gate, the
60-session cap, the repeat detector, the quiet flag and the frame throttle."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from omnigent.entities.conversation import (
    ConversationItem,
    NewConversationItem,
    parse_item_data,
)
from omnigent.server.factory import activity_now, detectors, issues, schemas, snapshot
from omnigent.server.factory.activity_now import (
    ActivityPoller,
    FrameThrottle,
    NowRegistry,
    RepeatTracker,
    activity_lines,
    args_hash,
    build_now,
    is_failed_output,
    parse_calls,
    tool_arg,
)
from tests.server.factory.conftest import FactoryWorld, FakeClock

FIXTURES = Path(__file__).parent / "fixtures" / "now_items.json"
BASE = 1_800_000_000


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    activity_now.registry.reset()
    detectors.reset()
    issues.reset()
    snapshot.reset_cache()
    yield
    activity_now.registry.reset()
    detectors.reset()
    issues.reset()
    snapshot.reset_cache()


def _recorded(harness: str) -> list[dict[str, Any]]:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))[harness]


def _items(harness: str, base: int = BASE, upto: int | None = None) -> list[ConversationItem]:
    """The recorded items as stored, oldest first, ``created_at = base + offset``."""
    rows = _recorded(harness)[:upto]
    return [
        ConversationItem(
            id=f"item{n:04d}",
            type=row["type"],
            status="completed",
            response_id=row["response_id"],
            created_at=base + row["offset_s"],
            data=parse_item_data(row["type"], row["data"]),
        )
        for n, row in enumerate(rows)
    ]


def _append(world: FactoryWorld, key: str, harness: str, upto: int | None = None) -> None:
    rows = _recorded(harness)[:upto]
    world.store.append(
        world.id(key),
        [
            NewConversationItem(
                type=row["type"],
                response_id=row["response_id"],
                data=parse_item_data(row["type"], row["data"]),
            )
            for row in rows
        ],
    )


# ── NowState from recorded items ───────────────────────────────


def test_now_state_from_claude_native_items(world: FactoryWorld) -> None:
    items = _items("claude_native")
    conv = world.get("ic")

    state = build_now(conv, items, status="running", now=BASE + 600, quiet_s=600)

    assert state["status"] == "running"
    assert state["tool"] == "Bash"
    # The secret in the command is redacted and the argument fits 80 chars.
    assert state["tool_arg"].startswith("NPM_TOKEN=[REDACTED] pnpm -F caffeine-api db:seed")
    assert "notarealtoken" not in state["tool_arg"]
    assert len(state["tool_arg"]) <= 80
    assert state["tool_since"] == BASE + 563
    assert state["last_event_at"] == BASE + 563
    assert state["flags"] == []
    assert schemas.NowState.model_validate(state).tool == "Bash"


def test_now_state_from_codex_native_items(world: FactoryWorld) -> None:
    items = _items("codex_native")

    state = build_now(world.get("reviewer"), items, status="running", now=BASE + 200, quiet_s=600)

    # The shell wrapper codex reports is dropped from the argument.
    assert (state["tool"], state["tool_arg"], state["tool_since"]) == (
        "shell",
        "pnpm -F web lint",
        BASE + 130,
    )
    assert schemas.NowState.model_validate(state).tool_arg == "pnpm -F web lint"


def test_an_idle_agent_has_no_current_tool_and_running_one_goes_quiet(
    world: FactoryWorld,
) -> None:
    items = _items("claude_native")
    conv = world.get("ic")

    idle = build_now(conv, items, status="idle", now=BASE + 600, quiet_s=600)
    assert idle["tool"] is None and idle["flags"] == []

    quiet = build_now(conv, items, status="running", now=BASE + 563 + 600, quiet_s=600)
    assert quiet["flags"] == ["quiet"]
    waiting = build_now(conv, items, status="waiting", now=BASE + 563 + 600, quiet_s=600)
    assert waiting["flags"] == []


def test_tool_args_failures_and_call_keys() -> None:
    assert tool_arg('{"file_path": "/w/app/src/x.ts"}') == "/w/app/src/x.ts"
    assert tool_arg('{"command": "/bin/zsh -lc \'pnpm test\'", "cwd": "/w"}') == "pnpm test"
    patch = '{"changes": [{"path": "/w/a.ts"}, {"path": "/w/b.ts"}]}'
    assert tool_arg(patch) == "/w/a.ts +1 more"
    assert tool_arg("{}") is None
    assert len(tool_arg(json.dumps({"command": "x" * 300})) or "") == 80

    assert is_failed_output("Exit code 1\n FAIL")
    assert is_failed_output("<tool_use_error>File has not been read yet</tool_use_error>")
    assert is_failed_output("FAIL Versions.test.tsx\n[exit code: 2]")
    assert not is_failed_output("Exit code 0")
    assert not is_failed_output("The file has been updated.")
    assert not is_failed_output("ok\n[exit code: 0]")

    same = args_hash("Bash", '{"command": "pnpm  test", "description": "a", "timeout": 1}')
    assert same == args_hash("Bash", '{"command": "pnpm test", "description": "b"}')
    assert same != args_hash("Bash", '{"command": "pnpm lint"}')
    assert same != args_hash("Shell", '{"command": "pnpm test"}')


def test_activity_lines_from_recorded_items() -> None:
    lines = activity_lines(
        _items("claude_native"),
        session_id="s1",
        title="ic:route-r1",
        role="ic",
        project="db-version",
    )

    kinds = [line["kind"] for line in lines]
    # A server-stamped user message is not a human line; thinking, tools, failures and replies are.
    assert kinds[0] == "thinking"
    assert kinds.count("tool") == 8
    assert kinds.count("result") == 5
    assert kinds.count("message") == 1
    failed = [line for line in lines if line["kind"] == "result"]
    assert all(line["failed"] and line["tool"] == "Bash" for line in failed)
    assert all(len(line["text"]) <= activity_now.LINE_TEXT_MAX for line in lines)
    assert lines[-1]["text"].startswith("Bash NPM_TOKEN=[REDACTED]")


# ── Repeat detector ────────────────────────────────────────────


def test_repeat_detector_counts_identical_failures_in_the_window() -> None:
    calls = parse_calls(_items("claude_native"))
    tracker = RepeatTracker(window_s=1800)
    tracker.observe(calls)
    tracker.observe(calls)  # the same calls seen again do not count twice

    key, count, arg = tracker.worst(BASE + 600) or ("", 0, None)
    assert count == 5 and arg == "pnpm -F caffeine-api test" and key.startswith("Bash:")
    # Failures older than the window drop out.
    assert tracker.worst(BASE + 129 + 1800 + 1) == (key, 4, arg)
    assert tracker.worst(BASE + 533 + 1800) is None


def test_a_success_of_the_same_call_clears_its_repeats() -> None:
    items = _items("claude_native")
    tracker = RepeatTracker(window_s=1800)
    tracker.observe(parse_calls(items))
    ok = [
        ConversationItem(
            id="ok1",
            type="function_call",
            status="completed",
            response_id="r",
            created_at=BASE + 700,
            data=parse_item_data(
                "function_call",
                {
                    "agent": "ic",
                    "name": "Bash",
                    "arguments": '{"command":"pnpm -F caffeine-api test"}',
                    "call_id": "toolu_ok",
                },
            ),
        ),
        ConversationItem(
            id="ok2",
            type="function_call_output",
            status="completed",
            response_id="r",
            created_at=BASE + 740,
            data=parse_item_data(
                "function_call_output", {"call_id": "toolu_ok", "output": "Tests 12 passed"}
            ),
        ),
    ]

    tracker.observe(parse_calls([*items, *ok]))

    assert tracker.worst(BASE + 750) is None


# ── The poller ─────────────────────────────────────────────────


class _Statuses:
    """Stand-in for the supervisor's child status / the relay status cache."""

    def __init__(self, world: FactoryWorld) -> None:
        self.world = world
        self.running: set[str] = set()

    def __call__(self, conv: Any, _child_status: Any) -> str:
        return "running" if conv.id in self.running else "idle"


@pytest.fixture()
def statuses(world: FactoryWorld, monkeypatch: pytest.MonkeyPatch) -> _Statuses:
    found = _Statuses(world)
    monkeypatch.setattr(activity_now, "_raw_status", found)
    return found


def _poller(world: FactoryWorld, presence: int = 1, **kwargs: Any) -> tuple[ActivityPoller, list]:
    """A poller where only the seeded factory (Lead A's) has *presence* viewers."""
    frames: list[str] = []
    fid = world.id("lead_a")
    poller = ActivityPoller(
        world.app_state,
        presence=lambda f: presence if f == fid else 0,
        publish=frames.append,
        now_registry=NowRegistry(),
        **kwargs,
    )
    return poller, frames


async def test_the_poll_runs_only_while_someone_watches(
    world: FactoryWorld, statuses: _Statuses
) -> None:
    statuses.running.add(world.id("ic"))
    _append(world, "ic", "claude_native")
    fid = world.id("lead_a")

    nobody, frames = _poller(world, presence=0)
    await nobody.tick()
    assert nobody.registry.peek(fid) is None
    assert nobody.reads_last_tick == {} and frames == []

    watched, frames = _poller(world, presence=1)
    await watched.tick()
    states = watched.registry.states(fid)
    assert states[world.id("ic")]["tool"] == "Bash"
    assert states[world.id("pl")]["status"] == "idle"
    assert len(frames) == 1
    # Only the factory someone watches was polled (the other factory has nobody).
    assert set(watched.reads_last_tick) == {fid.replace("-", "")}


async def test_the_poll_reads_at_most_60_sessions(
    world: FactoryWorld, statuses: _Statuses, monkeypatch: pytest.MonkeyPatch
) -> None:
    for n in range(70):
        child = world.make_session(
            f"c{n}", agent="pl", title=f"ic:t{n}", parent="pl", sub_agent_name="ic"
        )
        statuses.running.add(child.id)
    reads: list[str] = []
    original = world.store.list_items

    def spy(conversation_id: str, *args: Any, **kwargs: Any) -> Any:
        reads.append(conversation_id)
        return original(conversation_id, *args, **kwargs)

    monkeypatch.setattr(world.store, "list_items", spy)
    poller, _ = _poller(world)

    await poller.poll_factory(world.id("lead_a"))

    assert len(reads) == activity_now.MAX_SESSIONS == 60
    # Every agent still gets a NowState; the unread ones without a tool.
    assert len(poller.registry.states(world.id("lead_a"))) >= 70


async def test_a_repeating_agent_raises_agent_repeat_once_and_clears(
    world: FactoryWorld, statuses: _Statuses
) -> None:
    ic = world.id("ic")
    fid = world.id("lead_a")
    statuses.running.add(ic)
    _append(world, "ic", "claude_native")
    poller, _ = _poller(world)

    await poller.poll_factory(fid)
    await poller.poll_factory(fid)

    state = poller.registry.states(fid)[ic]
    assert "repeat" in state["flags"]
    store = world.deps.factory_store
    repeats = store.find_events(fid, "agent.repeat", 0)
    assert len(repeats) == 1
    assert repeats[0].data["count"] == 5 and repeats[0].from_id == ic
    [incident] = store.list_issues(fid, class_="incident")
    assert (incident.rule, incident.target, incident.status) == (
        "agent.repeat",
        f"member:{ic}",
        "open",
    )
    assert incident.ref == "INC-1" and incident.severity == "warning"

    # The same call now passes: the condition clears and the server resolves it.
    world.store.append(
        ic,
        [
            NewConversationItem(
                type="function_call",
                response_id="r9",
                data=parse_item_data(
                    "function_call",
                    {
                        "agent": "ic",
                        "name": "Bash",
                        "arguments": '{"command":"pnpm -F caffeine-api test"}',
                        "call_id": "toolu_pass",
                    },
                ),
            ),
            NewConversationItem(
                type="function_call_output",
                response_id="r9",
                data=parse_item_data(
                    "function_call_output", {"call_id": "toolu_pass", "output": "12 passed"}
                ),
            ),
        ],
    )
    await poller.poll_factory(fid)

    assert "repeat" not in poller.registry.states(fid)[ic]["flags"]
    [incident] = store.list_issues(fid, class_="incident")
    assert (incident.status, incident.resolved_by) == ("resolved", "server")


async def test_a_repeat_resolves_when_its_agent_is_closed(
    world: FactoryWorld, statuses: _Statuses
) -> None:
    ic = world.id("ic")
    fid = world.id("lead_a")
    statuses.running.add(ic)
    _append(world, "ic", "claude_native")
    poller, _ = _poller(world)
    await poller.poll_factory(fid)
    [incident] = world.deps.factory_store.list_issues(fid)
    assert (incident.rule, incident.status) == ("agent.repeat", "open")

    # The PL closes the worker (e.g. after a redispatch): the poll drops it.
    world.store.set_labels(ic, {"omnigent.closed": "true"})
    await poller.poll_factory(fid)

    [incident] = world.deps.factory_store.list_issues(fid)
    assert (incident.status, incident.resolved_by) == ("resolved", "server")
    [resolved] = world.deps.factory_store.find_events(fid, "issue.resolved", 0)
    assert resolved.data["reason"] == "agent closed"


async def test_a_still_repeating_agent_is_re_raised_to_stay_fresh(
    world: FactoryWorld, statuses: _Statuses
) -> None:
    ic = world.id("ic")
    fid = world.id("lead_a")
    statuses.running.add(ic)
    _append(world, "ic", "claude_native")
    clock = FakeClock(time.time())
    poller, _ = _poller(world, clock=clock)
    await poller.poll_factory(fid)
    await poller.poll_factory(fid)
    [incident] = world.deps.factory_store.list_issues(fid)
    assert incident.count == 1  # the same count is not re-raised every poll

    clock.advance(detectors.RERAISE_S + 1)
    await poller.poll_factory(fid)

    [incident] = [i for i in world.deps.factory_store.list_issues(fid) if i.rule == "agent.repeat"]
    assert incident.count == 2 and incident.status == "open"
    assert len(world.deps.factory_store.find_events(fid, "agent.repeat", 0)) == 1


async def test_no_database_reads_while_nobody_watches_any_factory(
    world: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_reads(_store: Any) -> list[Any]:
        raise AssertionError("listed factory roots with nobody watching")

    monkeypatch.setattr(snapshot, "list_factory_roots", _no_reads)
    poller = ActivityPoller(
        world.app_state,
        presence=lambda _fid: 1,
        publish=lambda _f: None,
        now_registry=NowRegistry(),
        anyone_watching=lambda: False,
    )

    await poller.tick()

    assert poller.reads_last_tick == {}


async def test_a_quiet_running_agent_raises_agent_quiet_and_clears(
    world: FactoryWorld, statuses: _Statuses
) -> None:
    ic = world.id("ic")
    fid = world.id("lead_a")
    statuses.running.add(ic)
    _append(world, "ic", "claude_native", upto=4)
    clock = FakeClock(time.time() + 11 * 60)
    poller, _ = _poller(world, clock=clock)

    await poller.poll_factory(fid)

    assert poller.registry.states(fid)[ic]["flags"] == ["quiet"]
    [incident] = world.deps.factory_store.list_issues(fid)
    assert (incident.rule, incident.status) == ("agent.quiet", "open")

    statuses.running.discard(ic)
    await poller.poll_factory(fid)

    [incident] = world.deps.factory_store.list_issues(fid)
    assert incident.status == "resolved"


async def test_the_snapshot_members_carry_now(world: FactoryWorld, statuses: _Statuses) -> None:
    ic = world.id("ic")
    statuses.running.add(ic)
    _append(world, "ic", "codex_native")
    poller = ActivityPoller(world.app_state, presence=lambda _fid: 1, publish=lambda _f: None)

    await poller.poll_factory(world.id("lead_a"))
    raw = await snapshot.build_snapshot(world.app_state, world.get("lead_a"))

    member = next(m for m in raw.body["members"] if m["session_id"] == ic)
    assert member["now"]["tool"] == "shell"
    assert member["now"]["tool_arg"] == "pnpm -F web lint"
    others = [m for m in raw.body["members"] if m["session_id"] != ic]
    assert all(m["now"] is None or m["now"]["tool"] is None for m in others)


async def test_the_ring_keeps_the_newest_lines(world: FactoryWorld, statuses: _Statuses) -> None:
    ic = world.id("ic")
    fid = world.id("lead_a")
    statuses.running.add(ic)
    poller, _ = _poller(world)
    _append(world, "ic", "claude_native")

    await poller.poll_factory(fid)
    first = poller.registry.lines(fid)
    await poller.poll_factory(fid)

    assert poller.registry.lines(fid) == first
    assert first and all(line["session_id"] == ic for line in first)
    assert first[-1]["kind"] == "tool" and first[-1]["tool"] == "Bash"


# ── Frame throttle ─────────────────────────────────────────────


async def test_at_most_one_now_frame_per_interval() -> None:
    sent: list[tuple[str, float]] = []
    throttle = FrameThrottle(lambda fid: sent.append((fid, time.monotonic())), interval_s=0.2)

    assert throttle.request("f1") is True
    assert throttle.request("f1") is False
    assert throttle.request("f1") is False
    assert throttle.request("f2") is True
    assert [fid for fid, _ in sent] == ["f1", "f2"]

    await asyncio.sleep(0.3)

    # The deferred requests became exactly one more frame, no sooner than the interval.
    assert [fid for fid, _ in sent] == ["f1", "f2", "f1"]
    assert sent[2][1] - sent[0][1] >= 0.19
    throttle.cancel()


async def test_unchanged_polls_send_no_frame(world: FactoryWorld, statuses: _Statuses) -> None:
    statuses.running.add(world.id("ic"))
    _append(world, "ic", "codex_native")
    poller, frames = _poller(world)
    fid = world.id("lead_a")

    assert await poller.poll_factory(fid) is True
    assert await poller.poll_factory(fid) is False
    assert len(frames) == 1
