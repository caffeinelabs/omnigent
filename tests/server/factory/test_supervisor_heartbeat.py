"""S2 heartbeat: LLM nudges to idle active members, restart-safe, with a dead alarm."""

from __future__ import annotations

import asyncio
import logging

import pytest

from omnigent import factory as F
from omnigent.server.factory.delivery import DeliveryOutcome
from omnigent.server.factory.notify import NOTIFIER_STATE_ATTR, FactoryNotifier, iso_utc
from omnigent.server.factory.supervisor import (
    FactorySupervisor,
    SupervisorConfig,
    get_supervisor,
)
from omnigent.server.routes._sessions.common import _session_status_cache
from tests.server.factory.conftest import FactoryWorld, Supervision

PL_HEARTBEAT_S = 600  # the seeded PL's heartbeat_s label


def _beat(n: int, at: float) -> str:
    return (
        f"[Factory heartbeat #{n} · {iso_utc(at)}] "
        'Run load_skill("factory-heartbeat") and follow it. Reply only if something changed.'
    )


async def test_due_beat_is_delivered_and_last_beat_written(supervision: Supervision) -> None:
    w, clock = supervision.world, supervision.clock
    pl = w.id("pl")

    await supervision.tick()
    assert supervision.deliver.calls == []

    first = clock.advance(PL_HEARTBEAT_S)
    await supervision.tick()
    assert supervision.deliver.calls == [(pl, _beat(1, first), False)]
    assert w.get("pl").labels[F.LABEL_LAST_BEAT] == iso_utc(first)

    # Not due again until heartbeat_s after that beat.
    clock.advance(PL_HEARTBEAT_S - 60)
    await supervision.tick()
    assert len(supervision.deliver.calls) == 1

    second = clock.advance(60)
    await supervision.tick()
    assert supervision.deliver.texts_to(pl) == [_beat(1, first), _beat(2, second)]


async def test_each_member_follows_its_own_interval(supervision: Supervision) -> None:
    w, clock = supervision.world, supervision.clock

    clock.advance(3600)  # Lead A's heartbeat_s
    await supervision.tick()

    beaten = {sid for sid, _, _ in supervision.deliver.calls}
    # Lead B has no heartbeat_s label, the other factory's lead has no state,
    # and sub-agents are never beaten by the server.
    assert beaten == {w.id("pl"), w.id("lead_a")}


async def test_running_sessions_are_skipped(
    supervision: Supervision, monkeypatch: pytest.MonkeyPatch
) -> None:
    pl = supervision.world.id("pl")
    monkeypatch.setitem(_session_status_cache, pl, "running")
    supervision.clock.advance(PL_HEARTBEAT_S)

    await supervision.tick()
    assert supervision.deliver.texts_to(pl) == []

    # A session waiting on an elicitation reads as running too.
    monkeypatch.setitem(_session_status_cache, pl, "waiting")
    await supervision.tick()
    assert supervision.deliver.texts_to(pl) == []

    monkeypatch.setitem(_session_status_cache, pl, "idle")
    await supervision.tick()
    assert len(supervision.deliver.texts_to(pl)) == 1


async def test_failed_sessions_are_beaten(
    supervision: Supervision, monkeypatch: pytest.MonkeyPatch
) -> None:
    pl = supervision.world.id("pl")
    monkeypatch.setitem(_session_status_cache, pl, "failed")
    supervision.clock.advance(PL_HEARTBEAT_S)

    await supervision.tick()

    assert len(supervision.deliver.texts_to(pl)) == 1


@pytest.mark.parametrize("value", ["0", "-5", "soon"])
async def test_heartbeat_off_is_skipped(supervision: Supervision, value: str) -> None:
    w = supervision.world
    w.store.set_labels(w.id("pl"), {F.LABEL_HEARTBEAT_S: value})
    supervision.clock.advance(7 * 86400)

    await supervision.tick()

    assert supervision.deliver.texts_to(w.id("pl")) == []


async def test_tiny_intervals_are_clamped_to_the_minimum(supervision: Supervision) -> None:
    w, clock = supervision.world, supervision.clock
    w.store.set_labels(w.id("pl"), {F.LABEL_HEARTBEAT_S: "5"})

    clock.advance(60)
    await supervision.tick()
    assert supervision.deliver.texts_to(w.id("pl")) == []

    clock.advance(F.MIN_HEARTBEAT_S - 60)
    await supervision.tick()
    assert len(supervision.deliver.texts_to(w.id("pl"))) == 1


@pytest.mark.parametrize("state", [F.STATE_PAUSED, F.STATE_DONE])
async def test_paused_and_done_sessions_are_never_beaten(
    supervision: Supervision, state: str
) -> None:
    w = supervision.world
    w.store.set_labels(w.id("pl"), {F.LABEL_STATE: state})
    supervision.clock.advance(7 * 86400)

    await supervision.tick()

    assert supervision.deliver.texts_to(w.id("pl")) == []


async def test_closed_sessions_are_never_beaten(supervision: Supervision) -> None:
    w = supervision.world
    w.store.set_labels(w.id("pl"), {"omnigent.closed": "true"})
    supervision.clock.advance(7 * 86400)

    await supervision.tick()

    assert supervision.deliver.texts_to(w.id("pl")) == []


async def test_restart_safe_reads_the_last_beat_label(supervision: Supervision) -> None:
    w, clock = supervision.world, supervision.clock
    pl = w.id("pl")
    beat_at = clock.advance(PL_HEARTBEAT_S)
    w.store.set_labels(pl, {F.LABEL_LAST_BEAT: iso_utc(beat_at)})

    # A fresh supervisor (e.g. after a server restart) has no memory of the beat.
    restarted = FactorySupervisor(
        w.app_state, config=SupervisorConfig(), deliver=supervision.deliver, clock=clock
    )
    clock.advance(PL_HEARTBEAT_S - 1)
    await restarted.tick()
    await restarted.drain()
    assert supervision.deliver.texts_to(pl) == []

    clock.advance(1)
    await restarted.tick()
    await restarted.drain()
    assert len(supervision.deliver.texts_to(pl)) == 1


async def test_recent_activity_defers_the_beat(supervision: Supervision) -> None:
    w, clock = supervision.world, supervision.clock
    pl = w.id("pl")
    # A beat long ago, but the session itself moved recently (updated_at).
    w.store.set_labels(pl, {F.LABEL_LAST_BEAT: iso_utc(clock.now - 10 * PL_HEARTBEAT_S)})

    clock.advance(PL_HEARTBEAT_S - 60)
    await supervision.tick()
    assert supervision.deliver.texts_to(pl) == []

    clock.advance(60)
    await supervision.tick()
    assert len(supervision.deliver.texts_to(pl)) == 1


async def test_a_slow_delivery_is_not_doubled(supervision: Supervision) -> None:
    pl = supervision.world.id("pl")
    gate = asyncio.Event()
    supervision.deliver.gate = gate
    supervision.clock.advance(PL_HEARTBEAT_S)

    await supervision.supervisor.tick()
    supervision.clock.advance(60)
    await supervision.supervisor.tick()
    gate.set()
    await supervision.settle()

    assert len(supervision.deliver.texts_to(pl)) == 1


async def test_three_failures_in_an_hour_mark_dead_and_need_a_human_once(
    supervision: Supervision,
) -> None:
    w, clock = supervision.world, supervision.clock
    pl, lead_a, lead_b = w.id("pl"), w.id("lead_a"), w.id("lead_b")
    down = DeliveryOutcome(False, "runner_unavailable", retryable=True)
    supervision.deliver.outcomes[pl] = [down] * 10
    # Keep the leads quiet so only the PL's beats and alerts are delivered.
    for lead in (lead_a, lead_b):
        w.store.set_labels(lead, {F.LABEL_HEARTBEAT_S: "0"})

    clock.advance(PL_HEARTBEAT_S)
    await supervision.tick()
    assert len(supervision.deliver.texts_to(pl)) == 1
    # A failed beat is retried after a pause, not on every tick.
    clock.advance(60)
    await supervision.tick()
    assert len(supervision.deliver.texts_to(pl)) == 1

    clock.advance(300)
    await supervision.tick()
    assert F.LABEL_HEALTH not in w.get("pl").labels
    assert supervision.deliver.texts_to(lead_b) == []

    clock.advance(300)
    await supervision.tick()
    assert len(supervision.deliver.texts_to(pl)) == 3
    assert w.get("pl").labels[F.LABEL_HEALTH] == F.HEALTH_DEAD
    alert = '[NEEDS HUMAN · relaunch of "PL · api-version" failed 3× (runner_unavailable)]'
    # Its supervisor (Lead B) and the factory root (Lead A) both hear about it.
    assert supervision.deliver.texts_to(lead_b) == [alert]
    assert supervision.deliver.texts_to(lead_a) == [alert]

    # Further failures within the hour raise no new alert.
    for _ in range(3):
        clock.advance(300)
        await supervision.tick()
    assert len(supervision.deliver.texts_to(pl)) == 6
    assert supervision.deliver.texts_to(lead_b) == [alert]

    # An hour after the alert, a still-dead session alerts again.
    clock.advance(2700)
    await supervision.tick()
    assert len(supervision.deliver.texts_to(pl)) == 7
    assert supervision.deliver.texts_to(lead_b) == [alert, alert]


async def test_a_dead_root_without_a_supervisor_is_logged(
    supervision: Supervision, caplog: pytest.LogCaptureFixture
) -> None:
    w, clock = supervision.world, supervision.clock
    lead_a = w.id("lead_a")
    w.store.delete_label(lead_a, F.LABEL_SUPERVISOR)
    w.store.set_labels(w.id("pl"), {F.LABEL_HEARTBEAT_S: "0"})
    supervision.deliver.outcomes[lead_a] = [DeliveryOutcome(False, "runner_unavailable")] * 3

    clock.advance(3600)
    with caplog.at_level(logging.WARNING, logger="omnigent.server.factory.supervisor"):
        for _ in range(3):
            await supervision.tick()
            clock.advance(300)

    assert w.get("lead_a").labels[F.LABEL_HEALTH] == F.HEALTH_DEAD
    assert [sid for sid, _, _ in supervision.deliver.calls] == [lead_a] * 3
    assert any("no supervisor to tell" in r.getMessage() for r in caplog.records)


async def test_a_beat_waits_for_mail_still_being_delivered(supervision: Supervision) -> None:
    pl = supervision.world.id("pl")
    supervision.clock.advance(PL_HEARTBEAT_S)
    slot = supervision.mailer.target_slot(pl)
    await slot.__aenter__()  # e.g. a brief still waiting on a managed launch

    await supervision.supervisor.tick()
    for _ in range(5):
        await asyncio.sleep(0)
    assert supervision.deliver.texts_to(pl) == []

    await slot.__aexit__(None, None, None)
    await supervision.settle()
    assert len(supervision.deliver.texts_to(pl)) == 1


async def test_a_delivered_beat_clears_dead(supervision: Supervision) -> None:
    w, clock = supervision.world, supervision.clock
    pl = w.id("pl")
    w.store.set_labels(pl, {F.LABEL_HEALTH: F.HEALTH_DEAD})

    clock.advance(PL_HEARTBEAT_S)
    await supervision.tick()

    assert w.get("pl").labels[F.LABEL_HEALTH] == F.HEALTH_OK


async def test_a_crashing_delivery_counts_as_a_failure(supervision: Supervision) -> None:
    w, clock = supervision.world, supervision.clock
    pl = w.id("pl")

    async def _boom(*_args: object, **_kwargs: object) -> DeliveryOutcome:
        raise RuntimeError("ladder bug")

    supervisor = FactorySupervisor(
        w.app_state, config=SupervisorConfig(), deliver=_boom, clock=clock
    )
    clock.advance(PL_HEARTBEAT_S)
    for _ in range(3):
        await supervisor.tick()
        await supervisor.drain()
        clock.advance(300)
    await supervision.mailer.drain()

    labels = w.get("pl").labels
    assert F.LABEL_LAST_BEAT not in labels
    assert labels[F.LABEL_HEALTH] == F.HEALTH_DEAD
    assert supervision.deliver.texts_to(w.id("lead_b")) == [
        '[NEEDS HUMAN · relaunch of "PL · api-version" failed 3× (internal_error: RuntimeError)]'
    ]
    assert supervision.deliver.texts_to(pl) == []


async def test_start_ticks_until_stopped(supervision: Supervision) -> None:
    sleeps: list[float] = []
    ticked = asyncio.Event()

    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) > 1:
            ticked.set()
            await asyncio.Event().wait()

    supervisor = FactorySupervisor(
        supervision.world.app_state,
        config=SupervisorConfig(tick_s=5),
        deliver=supervision.deliver,
        clock=supervision.clock,
        sleep=_sleep,
    )
    supervision.clock.advance(PL_HEARTBEAT_S)
    supervisor.start()
    await asyncio.wait_for(ticked.wait(), timeout=5)
    await supervisor.drain()
    await supervisor.stop()

    assert sleeps == [5, 5]
    assert len(supervision.deliver.texts_to(supervision.world.id("pl"))) == 1


def test_config_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNIGENT_FACTORY_TICK_S", "15")
    monkeypatch.setenv("OMNIGENT_FACTORY_KEEPALIVE_S", "120")
    monkeypatch.setenv("OMNIGENT_FACTORY_STUCK_S", "nope")
    monkeypatch.setenv("OMNIGENT_FACTORY_BLOCKED_ESCALATE_S", "-1")

    assert SupervisorConfig.from_env() == SupervisorConfig(
        tick_s=15, keepalive_s=120, stuck_s=5400, blocked_escalate_s=1800
    )


async def test_factory_services_run_the_supervisor_only_when_enabled(
    world: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.server.factory import notify, start_factory_services

    monkeypatch.delenv(F.FACTORY_ENABLED_ENV, raising=False)
    stop = await start_factory_services(world.app_state)
    assert get_supervisor(world.app_state) is None
    assert notify._relay_notifier is None
    await stop()

    monkeypatch.setenv(F.FACTORY_ENABLED_ENV, "1")
    monkeypatch.setenv("OMNIGENT_FACTORY_TICK_S", "30")
    stop = await start_factory_services(world.app_state)
    supervisor = get_supervisor(world.app_state)
    assert supervisor is not None
    assert supervisor.config.tick_s == 30
    notifier = getattr(world.app_state, NOTIFIER_STATE_ATTR)
    assert isinstance(notifier, FactoryNotifier)
    # The runner-stream relay (no app.state) feeds this notifier.
    assert notify._relay_notifier is notifier
    await stop()
    assert get_supervisor(world.app_state) is None
    assert getattr(world.app_state, NOTIFIER_STATE_ATTR) is None
    assert notify._relay_notifier is None
