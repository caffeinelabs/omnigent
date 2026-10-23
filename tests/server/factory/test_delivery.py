"""The delivery ladder, with the session-route helpers monkeypatched.

Each scenario asserts which rungs ran and in what order: rendezvous with a
managed launch, wake/relaunch through ``ensure_runner_connected``, sub-agent
heal via the root, init after a relaunch, relay, dispatch, and the Stop
helpers that a restart runs first. A final group covers the mailer's retries
and bounce when delivery keeps failing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.factory import delivery
from omnigent.server.factory.delivery import DeliveryOutcome, deliver_message
from omnigent.server.factory.mailer import Mailer, MailItem
from omnigent.server.managed_hosts import ManagedLaunchTracker
from omnigent.server.routes._sessions.common import (
    _intentional_stop_sessions,
    _interrupt_fenced_sessions,
)
from tests.server.factory.conftest import FactoryWorld, FakeDeliver

CLIENT: Any = object()  # stands in for the runner's httpx client


class Ladder:
    """Monkeypatched helpers that record each rung in call order."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, world: FactoryWorld) -> None:
        self.world = world
        self.calls: list[tuple[str, Any]] = []
        self.bound: dict[str, Any] = {}
        self.ensure_result: dict[str, Any] = {}
        self.heal_result: Any = None
        self.native = False
        self.dispatch_error: BaseException | None = None
        self.dispatched: list[dict[str, Any]] = []
        self.stop_delivered = True
        self.host_stop_delivered = True
        self.marker_during_host_stop: bool | None = None
        for name in (
            "_get_runner_client",
            "ensure_runner_connected",
            "_heal_subagent_runner_binding_via_parent",
            "_ensure_runner_session_initialized",
            "_ensure_runner_relay_ready",
            "_dispatch_session_event_to_runner",
            "_stop_session_via_runner",
            "_stop_session_host_runner",
            "_is_native_terminal_session",
        ):
            monkeypatch.setattr(delivery, name, getattr(self, name))

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    async def _get_runner_client(self, session_id: str, runner_router: Any, **_: Any) -> Any:
        self.calls.append(("get_client", session_id))
        return self.bound.get(session_id)

    async def ensure_runner_connected(self, *, session_id: str, conv: Any, **kw: Any) -> Any:
        self.calls.append(("ensure", session_id))
        client = self.ensure_result.get(session_id)
        if client is not None:
            self.bound[session_id] = client
        return client, conv

    async def _heal_subagent_runner_binding_via_parent(
        self, child_conv: Any, runner_router: Any, tunnel_registry: Any, store: Any
    ) -> Any:
        self.calls.append(("heal", child_conv.id))
        return self.heal_result

    async def _ensure_runner_session_initialized(
        self, session_id: str, conv: Any, client: Any, store: Any, **kw: Any
    ) -> bool:
        self.calls.append(("init", kw))
        return True

    async def _ensure_runner_relay_ready(self, session_id: str, *args: Any) -> None:
        self.calls.append(("relay", session_id))

    async def _dispatch_session_event_to_runner(
        self, session_id: str, conv: Any, body: Any, store: Any, client: Any, **kw: Any
    ) -> None:
        self.calls.append(("dispatch", session_id))
        if self.dispatch_error is not None:
            raise self.dispatch_error
        self.dispatched.append(
            {"session_id": session_id, "text": body.data["content"][0]["text"], **kw}
        )

    async def _stop_session_via_runner(self, session_id: str, runner_router: Any) -> bool:
        self.calls.append(("stop", session_id))
        return self.stop_delivered

    async def _stop_session_host_runner(
        self, session_id: str, host_id: str, runner_id: str, host_registry: Any
    ) -> bool:
        self.calls.append(("stop_host", (host_id, runner_id)))
        self.marker_during_host_stop = session_id in _intentional_stop_sessions
        return self.host_stop_delivered

    def _is_native_terminal_session(self, conv: Any) -> bool:
        return self.native


@pytest.fixture()
def ladder(monkeypatch: pytest.MonkeyPatch, world: FactoryWorld) -> Ladder:
    return Ladder(monkeypatch, world)


# ── ladder scenarios ────────────────────────────────────


async def test_idle_connected_session_is_dispatched_directly(
    world: FactoryWorld, ladder: Ladder
) -> None:
    pl = world.id("pl")
    ladder.bound[pl] = CLIENT

    outcome = await deliver_message(world.app_state, pl, "hello PL")

    assert outcome == DeliveryOutcome(True)
    assert ladder.names() == ["get_client", "relay", "dispatch"]
    sent = ladder.dispatched[0]
    assert sent["text"] == "hello PL"
    assert sent["agent_name"] == "factory-pl"
    assert sent["has_mcp_servers"] is False
    assert sent["native_terminal_ready"] is False
    assert sent["background_titles_enabled"] is False


async def test_managed_launch_in_flight_is_awaited(world: FactoryWorld, ladder: Ladder) -> None:
    pl = world.id("pl")
    tracker = ManagedLaunchTracker()
    tracker.begin(pl)
    world.app_state.managed_launches = tracker

    task = asyncio.create_task(deliver_message(world.app_state, pl, "brief"))
    await asyncio.sleep(0.05)
    assert not task.done()
    assert "dispatch" not in ladder.names()

    # The launch settles with the runner up and bound.
    ladder.bound[pl] = CLIENT
    tracker.finish(pl)
    outcome = await asyncio.wait_for(task, timeout=5)

    assert outcome.delivered
    assert ladder.names() == ["get_client", "get_client", "relay", "dispatch"]


async def test_failed_managed_launch_is_retryable_failure(
    world: FactoryWorld, ladder: Ladder
) -> None:
    pl = world.id("pl")
    tracker = ManagedLaunchTracker()
    tracker.begin(pl)
    tracker.fail(pl, "managed sandbox launch failed: quota")
    world.app_state.managed_launches = tracker

    outcome = await deliver_message(world.app_state, pl, "brief")

    assert outcome == DeliveryOutcome(False, ErrorCode.RUNNER_UNAVAILABLE, retryable=True)
    assert "dispatch" not in ladder.names()


async def test_suspended_managed_host_relaunches_then_inits_then_dispatches(
    world: FactoryWorld, ladder: Ladder
) -> None:
    pl = world.make_session(
        "pl_host",
        agent="pl",
        title="PL on a sleeping sandbox",
        runner_id="runner_old",
        host_id="0123456789abcdef0123456789abcdef",
    ).id
    ladder.ensure_result[pl] = CLIENT

    outcome = await deliver_message(world.app_state, pl, "wake up")

    assert outcome.delivered
    assert ladder.names() == ["get_client", "ensure", "init", "relay", "dispatch"]
    init_kwargs = ladder.calls[2][1]
    assert init_kwargs["suppress_recovery_turn"] is True
    assert init_kwargs["initializer"] is world.app_state.runner_session_initializer
    assert ladder.dispatched[0]["native_terminal_ready"] is True


async def test_sub_agent_heals_via_its_root(world: FactoryWorld, ladder: Ladder) -> None:
    ic, pl = world.id("ic"), world.id("pl")
    ladder.heal_result = CLIENT
    ladder.native = True

    outcome = await deliver_message(world.app_state, ic, "please rebase")

    assert outcome.delivered
    assert ladder.names() == [
        "get_client",
        "ensure",  # the sub-agent itself: nothing to wake
        "ensure",  # its root (the PL) gets woken first
        "heal",
        "init",  # native terminal sub-agent needs its terminal back
        "relay",
        "dispatch",
    ]
    assert [sid for name, sid in ladder.calls if name == "ensure"] == [ic, pl]


async def test_sdk_sub_agent_heal_skips_init(world: FactoryWorld, ladder: Ladder) -> None:
    ladder.heal_result = CLIENT
    ladder.native = False

    outcome = await deliver_message(world.app_state, world.id("ic"), "ping")

    assert outcome.delivered
    assert "init" not in ladder.names()


async def test_no_runner_anywhere_is_runner_unavailable(
    world: FactoryWorld, ladder: Ladder
) -> None:
    outcome = await deliver_message(world.app_state, world.id("pl"), "anyone?")

    assert outcome == DeliveryOutcome(False, "runner_unavailable", retryable=True)
    assert "dispatch" not in ladder.names()


async def test_restart_runs_the_stop_helpers_first(world: FactoryWorld, ladder: Ladder) -> None:
    host_id = "fedcba9876543210fedcba9876543210"
    pl = world.make_session(
        "pl_restart",
        agent="pl",
        title="Wedged PL",
        runner_id="runner_wedged",
        host_id=host_id,
    ).id
    ladder.ensure_result[pl] = CLIENT

    class _Registry:
        def get(self, runner_id: str) -> None:
            return None

    world.app_state.tunnel_registry = _Registry()
    try:
        outcome = await deliver_message(world.app_state, pl, "restarting you", restart=True)
    finally:
        _intentional_stop_sessions.discard(pl)
        _interrupt_fenced_sessions.discard(pl)

    assert outcome.delivered
    assert ladder.names()[:2] == ["stop", "stop_host"]
    assert ladder.calls[1][1] == (host_id, "runner_wedged")
    assert ladder.marker_during_host_stop is True
    assert ladder.names()[2:] == ["get_client", "ensure", "init", "relay", "dispatch"]


async def test_restart_discards_markers_when_stops_do_not_land(
    world: FactoryWorld, ladder: Ladder
) -> None:
    pl = world.make_session(
        "pl_restart2",
        agent="pl",
        title="PL",
        runner_id="runner_x",
        host_id="00000000000000000000000000000001",
    ).id
    ladder.stop_delivered = False
    ladder.host_stop_delivered = False
    ladder.bound[pl] = CLIENT

    outcome = await deliver_message(world.app_state, pl, "restart", restart=True)

    assert outcome.delivered
    assert pl not in _intentional_stop_sessions
    assert pl not in _interrupt_fenced_sessions


async def test_closed_and_missing_sessions_are_not_retried(
    world: FactoryWorld, ladder: Ladder
) -> None:
    from omnigent.util.session_lifecycle import CLOSED_LABEL_KEY, CLOSED_LABEL_VALUE

    ic = world.id("ic")
    world.store.set_labels(ic, {CLOSED_LABEL_KEY: CLOSED_LABEL_VALUE})

    closed = await deliver_message(world.app_state, ic, "x")
    missing = await deliver_message(world.app_state, "0" * 32, "x")

    assert closed == DeliveryOutcome(False, "session_closed")
    assert missing == DeliveryOutcome(False, "session_not_found")
    assert ladder.calls == []


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (httpx.ConnectError("boom"), DeliveryOutcome(False, "transport_error", retryable=True)),
        (
            OmnigentError("down", code=ErrorCode.RUNNER_UNAVAILABLE),
            DeliveryOutcome(False, "runner_unavailable", retryable=True),
        ),
        (
            OmnigentError("bad", code=ErrorCode.INVALID_INPUT),
            DeliveryOutcome(False, "invalid_input", retryable=False),
        ),
    ],
)
async def test_dispatch_errors_become_outcomes(
    world: FactoryWorld,
    ladder: Ladder,
    error: BaseException,
    expected: DeliveryOutcome,
) -> None:
    pl = world.id("pl")
    ladder.bound[pl] = CLIENT
    ladder.dispatch_error = error

    assert await deliver_message(world.app_state, pl, "x") == expected


# ── failure → retries → bounce ──────────────────────────


def _item(world: FactoryWorld, kind: str = "request") -> MailItem:
    return MailItem(
        mail_id="mail_1",
        sender_id=world.id("lead_a"),
        target_id=world.id("pl"),
        target_title="PL · api-version",
        text="[Factory message · …]\nplease report",
        kind=kind,
    )


async def test_retries_then_bounces_to_sender(
    world: FactoryWorld,
    fake_deliver: FakeDeliver,
    no_sleep: Callable[[float], Any],
) -> None:
    pl, lead = world.id("pl"), world.id("lead_a")
    down = DeliveryOutcome(False, "runner_unavailable", retryable=True)
    fake_deliver.outcomes[pl] = [down, down, down]
    mailer = Mailer(world.app_state, deliver=fake_deliver, sleep=no_sleep)

    mailer.submit(_item(world))
    await mailer.drain()

    assert len(fake_deliver.texts_to(pl)) == 3
    assert no_sleep.delays == [60.0, 300.0]  # type: ignore[attr-defined]
    assert fake_deliver.texts_to(lead) == [
        f'[Factory delivery failure · message to "PL · api-version" ({pl}) '
        "undeliverable: runner_unavailable]"
    ]
    assert mailer.pop_pending_reply(pl) is None


async def test_permanent_failure_bounces_without_retry(
    world: FactoryWorld,
    fake_deliver: FakeDeliver,
    no_sleep: Callable[[float], Any],
) -> None:
    pl = world.id("pl")
    fake_deliver.outcomes[pl] = [DeliveryOutcome(False, "session_closed")]
    mailer = Mailer(world.app_state, deliver=fake_deliver, sleep=no_sleep)

    mailer.submit(_item(world))
    await mailer.drain()

    assert len(fake_deliver.texts_to(pl)) == 1
    assert no_sleep.delays == []  # type: ignore[attr-defined]
    assert "undeliverable: session_closed" in fake_deliver.texts_to(world.id("lead_a"))[0]


async def test_retry_success_records_pending_reply(
    world: FactoryWorld,
    fake_deliver: FakeDeliver,
    no_sleep: Callable[[float], Any],
) -> None:
    pl, lead = world.id("pl"), world.id("lead_a")
    fake_deliver.outcomes[pl] = [DeliveryOutcome(False, "transport_error", retryable=True)]
    mailer = Mailer(world.app_state, deliver=fake_deliver, sleep=no_sleep, clock=lambda: 1000.0)

    mailer.submit(_item(world, kind="brief"))
    await mailer.drain()

    assert len(fake_deliver.texts_to(pl)) == 2
    assert fake_deliver.texts_to(lead) == []
    assert mailer.pop_pending_reply(pl) == (lead, 1000.0)
    assert mailer.pop_pending_reply(pl) is None


async def test_restart_is_only_attempted_once(
    world: FactoryWorld,
    fake_deliver: FakeDeliver,
    no_sleep: Callable[[float], Any],
) -> None:
    pl = world.id("pl")
    fake_deliver.outcomes[pl] = [DeliveryOutcome(False, "runner_unavailable", retryable=True)]
    mailer = Mailer(world.app_state, deliver=fake_deliver, sleep=no_sleep)
    item = _item(world)
    item.restart = True

    mailer.submit(item)
    await mailer.drain()

    assert [restart for sid, _, restart in fake_deliver.calls if sid == pl] == [True, False]
