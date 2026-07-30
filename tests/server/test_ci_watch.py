"""Tests for the CI-watch poller logic (pure, callable-injected)."""

from __future__ import annotations

import pytest

from omnigent.server.ci_watch import (
    CiWatch,
    CiWatchRegistry,
    WatchedPr,
    _poll_once,
    aggregate_check_conclusion,
    build_watch_from_prs,
)


def _runs(*specs: tuple[str, str | None]) -> list[dict[str, object]]:
    return [{"name": f"c{i}", "status": s, "conclusion": c} for i, (s, c) in enumerate(specs)]


def test_aggregate_none_when_no_checks() -> None:
    assert aggregate_check_conclusion([]) == "none"


def test_aggregate_pending_when_any_incomplete() -> None:
    assert (
        aggregate_check_conclusion(_runs(("completed", "success"), ("in_progress", None)))
        == "pending"
    )


def test_aggregate_failure_when_any_failing() -> None:
    assert (
        aggregate_check_conclusion(_runs(("completed", "success"), ("completed", "failure")))
        == "failure"
    )


def test_aggregate_success_when_all_good() -> None:
    assert (
        aggregate_check_conclusion(_runs(("completed", "success"), ("completed", "skipped")))
        == "success"
    )


def test_build_watch_filters_malformed_prs() -> None:
    watch = build_watch_from_prs(
        "conv_1",
        "alice@example.com",
        [
            {"repo": "o/a", "number": 1, "head_ref": "feat"},
            {"repo": "o/b", "number": None, "head_ref": "x"},  # bad number
            {"repo": "o/c", "head_ref": "y"},  # missing number
        ],
    )
    assert [(p.repo, p.number, p.head_ref) for p in watch.prs] == [("o/a", 1, "feat")]


def _watch() -> CiWatch:
    return CiWatch(
        session_id="conv_1",
        user_id="alice@example.com",
        prs=[WatchedPr(repo="o/a", number=1, head_ref="feat")],
    )


@pytest.mark.asyncio
async def test_poll_wakes_on_terminal_and_marks_notified() -> None:
    woke: list[tuple[str, str]] = []

    async def resolve_token(uid: str) -> str | None:
        return "ghu_x"

    async def list_check_runs(token: str, repo: str, ref: str) -> list[dict[str, object]]:
        return _runs(("completed", "success"))

    async def wake(session_id: str, notice: str) -> bool:
        woke.append((session_id, notice))
        return True

    watch = _watch()
    done = await _poll_once(
        watch, resolve_token=resolve_token, list_check_runs=list_check_runs, wake=wake
    )
    assert done is True
    assert len(woke) == 1 and woke[0][0] == "conv_1" and "passed" in woke[0][1]
    assert watch.notified == {"o/a#1": "success"}

    # Second poll: already notified → no second wake, still terminal.
    done2 = await _poll_once(
        watch, resolve_token=resolve_token, list_check_runs=list_check_runs, wake=wake
    )
    assert done2 is True
    assert len(woke) == 1


@pytest.mark.asyncio
async def test_poll_does_not_wake_while_pending() -> None:
    woke: list[str] = []

    async def resolve_token(uid: str) -> str | None:
        return "ghu_x"

    async def list_check_runs(token: str, repo: str, ref: str) -> list[dict[str, object]]:
        return _runs(("in_progress", None))

    async def wake(session_id: str, notice: str) -> bool:
        woke.append(session_id)
        return True

    watch = _watch()
    done = await _poll_once(
        watch, resolve_token=resolve_token, list_check_runs=list_check_runs, wake=wake
    )
    assert done is False
    assert woke == []


@pytest.mark.asyncio
async def test_poll_retries_when_wake_not_delivered() -> None:
    async def resolve_token(uid: str) -> str | None:
        return "ghu_x"

    async def list_check_runs(token: str, repo: str, ref: str) -> list[dict[str, object]]:
        return _runs(("completed", "failure"))

    async def wake(session_id: str, notice: str) -> bool:
        return False  # runner unbound this tick

    watch = _watch()
    done = await _poll_once(
        watch, resolve_token=resolve_token, list_check_runs=list_check_runs, wake=wake
    )
    # Not marked notified, not terminal → will retry next tick.
    assert done is False
    assert watch.notified == {}


@pytest.mark.asyncio
async def test_poll_no_token_is_noop() -> None:
    async def resolve_token(uid: str) -> str | None:
        return None

    async def list_check_runs(token: str, repo: str, ref: str) -> list[dict[str, object]]:
        raise AssertionError("must not be called without a token")

    async def wake(session_id: str, notice: str) -> bool:
        raise AssertionError("must not wake without a token")

    done = await _poll_once(
        _watch(), resolve_token=resolve_token, list_check_runs=list_check_runs, wake=wake
    )
    assert done is False


def test_registry_arm_disarm() -> None:
    reg = CiWatchRegistry()
    assert len(reg) == 0
    reg.arm(_watch())
    assert len(reg) == 1 and reg.snapshot()[0].session_id == "conv_1"
    reg.disarm("conv_1")
    assert len(reg) == 0
    reg.disarm("conv_1")  # idempotent
