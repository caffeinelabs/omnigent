"""The mailer's trail in the event log, and replay of mail a server roll dropped."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from omnigent import factory as F
from omnigent.server.factory import mail_events
from omnigent.server.factory.delivery import DeliveryOutcome
from omnigent.server.factory.envelope import format_envelope
from omnigent.server.factory.mailer import (
    MAILER_STATE_ATTR,
    RATE_LIMITED_EVENT_INTERVAL_S,
    Mailer,
    MailItem,
)
from omnigent.stores.factory_store import FactoryStore
from tests.server.factory.conftest import FactoryApi, FactoryWorld, FakeClock, FakeDeliver


def _store(world: FactoryWorld) -> FactoryStore:
    return world.app_state.factory.factory_store


def _item(world: FactoryWorld, mail_id: str = "mail_0123456789abcdef") -> MailItem:
    pl = world.id("pl")
    return MailItem(
        mail_id=mail_id,
        sender_id=pl,
        target_id=world.id("lead_a"),
        target_title="Atlas",
        text=format_envelope(
            sender_id=pl,
            sender_title="PL · api-version",
            sender_role="project_lead",
            sender_project="api-version",
            kind="request",
            body="please review #9630",
            mail_id=mail_id,
        ),
        kind="request",
    )


def _kinds(world: FactoryWorld, mail_id: str) -> list[str]:
    events = _store(world).find_events(None, "message.*", 0, f"mail:{mail_id}")
    return [e.kind for e in events]


async def _wait_for(check: Callable[[], bool], timeout: float = 5.0) -> None:
    async def _poll() -> None:
        while not check():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), timeout)


async def test_a_submit_logs_queued_then_delivered(
    world: FactoryWorld, fake_deliver: FakeDeliver, no_sleep: Callable[[float], Any]
) -> None:
    mailer = Mailer(world.app_state, deliver=fake_deliver, sleep=no_sleep)
    item = _item(world)

    mailer.submit(item)
    assert mailer.queue_stats()[0] == 1
    await mailer.drain()

    [queued, delivered] = _store(world).find_events(None, "message.*", 0)
    assert (queued.kind, delivered.kind) == ("message.queued", "message.delivered")
    assert queued.factory_id == world.id("lead_a")
    assert queued.subject == delivered.subject == f"mail:{item.mail_id}"
    assert queued.from_id == world.id("pl") and queued.to_id == world.id("lead_a")
    assert queued.project_slug == "api-version"
    assert queued.data is not None
    assert queued.data["text"] == item.text
    assert queued.data["excerpt"] == "please review #9630"
    assert queued.data["mail_id"] == item.mail_id and queued.data["kind"] == "request"
    assert queued.summary == "please review #9630"
    assert delivered.data == {"mail_id": item.mail_id, "kind": "request"}
    assert mailer.queue_stats() == (0, None)


async def test_a_new_mailer_replays_undelivered_mail_once_with_the_same_id(
    world: FactoryWorld, no_sleep: Callable[[float], Any]
) -> None:
    stuck = FakeDeliver(gate=asyncio.Event())  # never opens: the "old process" dies mid-delivery
    old = Mailer(world.app_state, deliver=stuck, sleep=no_sleep)
    item = _item(world)
    old.submit(item)
    await _wait_for(lambda: _kinds(world, item.mail_id) == ["message.queued"])
    await old.close()

    fresh = FakeDeliver()
    restarted = Mailer(world.app_state, deliver=fresh, sleep=no_sleep)
    assert await restarted.replay_undelivered() == 1
    await restarted.drain()

    assert fresh.calls == [(world.id("lead_a"), item.text, False)]
    assert f"· id={item.mail_id} ·" in fresh.calls[0][1]
    assert _kinds(world, item.mail_id) == ["message.queued", "message.delivered"]

    # Delivered now: a later start finds nothing to replay.
    again = Mailer(world.app_state, deliver=fresh, sleep=no_sleep)
    assert await again.replay_undelivered() == 0
    await again.drain()
    assert len(fresh.calls) == 1


async def test_delivered_and_bounced_mail_is_not_replayed(
    world: FactoryWorld, fake_deliver: FakeDeliver, no_sleep: Callable[[float], Any]
) -> None:
    lead_a = world.id("lead_a")
    fake_deliver.outcomes[lead_a] = [
        DeliveryOutcome(True),
        DeliveryOutcome(False, "session_closed"),
    ]
    mailer = Mailer(world.app_state, deliver=fake_deliver, sleep=no_sleep)
    mailer.submit(_item(world, "mail_aaaaaaaaaaaaaaaa"))
    mailer.submit(_item(world, "mail_bbbbbbbbbbbbbbbb"))
    await mailer.drain()

    assert _kinds(world, "mail_bbbbbbbbbbbbbbbb") == ["message.queued", "message.bounced"]
    [bounced] = _store(world).find_events(None, "message.bounced", 0)
    assert bounced.severity == "warning"
    assert bounced.data is not None and bounced.data["reason"] == "session_closed"

    restarted = Mailer(world.app_state, deliver=fake_deliver, sleep=no_sleep)
    assert await restarted.replay_undelivered() == 0


async def test_replay_skips_mail_older_than_two_hours(
    world: FactoryWorld, no_sleep: Callable[[float], Any]
) -> None:
    stuck = FakeDeliver(gate=asyncio.Event())
    old = Mailer(world.app_state, deliver=stuck, sleep=no_sleep)
    item = _item(world)
    old.submit(item)
    await _wait_for(lambda: _kinds(world, item.mail_id) == ["message.queued"])
    await old.close()
    [queued] = _store(world).find_events(None, "message.queued", 0)

    later = queued.ts + mail_events.REPLAY_WINDOW_S * 1000 + 1
    replayed = await mail_events.replay_undelivered(
        world.app_state, Mailer(world.app_state, deliver=FakeDeliver(), sleep=no_sleep), now=later
    )

    assert replayed == 0


async def test_a_notice_resolves_its_factory_from_the_target(
    world: FactoryWorld, fake_deliver: FakeDeliver, no_sleep: Callable[[float], Any]
) -> None:
    mailer = Mailer(world.app_state, deliver=fake_deliver, sleep=no_sleep)

    mail_id = mailer.notify(world.id("ic"), "[Factory alert · something]")
    await mailer.drain()

    [queued, delivered] = _store(world).find_events(world.id("lead_a"), "message.*", 0)
    assert queued.subject == delivered.subject == f"mail:{mail_id}"
    assert queued.from_id is None and queued.to_id == world.id("ic")
    # A sub-agent's project is its PL's.
    assert queued.project_slug == "api-version"


async def test_a_refused_post_is_logged_as_rate_limited(
    world: FactoryWorld, fake_deliver: FakeDeliver, no_sleep: Callable[[float], Any]
) -> None:
    mailer = Mailer(
        world.app_state,
        deliver=fake_deliver,
        sleep=no_sleep,
        sender_rate_per_hour=1,
        pair_rate_per_hour=1,
    )
    pl, lead = world.id("pl"), world.id("lead_a")

    assert mailer.try_acquire(pl, lead, factory_id=lead) is None
    assert mailer.try_acquire(pl, lead, factory_id=lead) == "1 posts/h per sender"
    await mailer.drain()

    [event] = _store(world).find_events(lead, "rate_limited", 0)
    assert (event.from_id, event.to_id, event.severity) == (pl, lead, "warning")
    assert event.data == {"limit": "1 posts/h per sender"}


async def test_a_looping_sender_logs_one_refusal_per_recipient_per_minute(
    world: FactoryWorld, fake_deliver: FakeDeliver, no_sleep: Callable[[float], Any]
) -> None:
    clock = FakeClock()
    mailer = Mailer(
        world.app_state, deliver=fake_deliver, sleep=no_sleep, clock=clock, sender_rate_per_hour=1
    )
    pl, lead, ic = world.id("pl"), world.id("lead_a"), world.id("ic")
    assert mailer.try_acquire(pl, lead, factory_id=lead) is None

    for _ in range(20):
        assert mailer.try_acquire(pl, lead, factory_id=lead) is not None
    assert mailer.try_acquire(pl, ic, factory_id=lead) is not None
    clock.advance(RATE_LIMITED_EVENT_INTERVAL_S)
    assert mailer.try_acquire(pl, lead, factory_id=lead) is not None
    await mailer.drain()

    refused = _store(world).find_events(lead, "rate_limited", 0)
    assert [e.to_id for e in refused] == [lead, ic, lead]


async def test_the_route_envelope_carries_the_mail_id(
    world: FactoryWorld, api: FactoryApi
) -> None:
    pl = world.id("pl")

    resp = await api.client.post(
        "/v1/factory/messages",
        json={"from_session_id": pl, "to": "lead", "message": "hi", "kind": "info"},
    )
    await api.mailer.drain()

    mail_id = resp.json()["mail_id"]
    [text] = api.deliver.texts_to(world.id("lead_a"))
    assert text.splitlines()[0].endswith(
        f'· kind=info · id={mail_id} · reply: sys_session_post(to="{pl}")]'
    )
    store = api.app.state.factory.factory_store
    [queued] = store.find_events(world.id("lead_a"), "message.queued", 0)
    assert queued.data["mail_id"] == mail_id and queued.project_slug == "api-version"


@pytest.mark.parametrize(("value", "replays"), [(None, True), ("1", True), ("0", False)])
async def test_factory_services_replay_on_start_unless_switched_off(
    world: FactoryWorld,
    no_sleep: Callable[[float], Any],
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
    replays: bool,
) -> None:
    from omnigent.server.factory import start_factory_services

    monkeypatch.delenv(F.FACTORY_ENABLED_ENV, raising=False)
    if value is None:
        monkeypatch.delenv(mail_events.MAIL_REPLAY_ENV, raising=False)
    else:
        monkeypatch.setenv(mail_events.MAIL_REPLAY_ENV, value)
    stuck = FakeDeliver(gate=asyncio.Event())
    old = Mailer(world.app_state, deliver=stuck, sleep=no_sleep)
    item = _item(world)
    old.submit(item)
    await _wait_for(lambda: _kinds(world, item.mail_id) == ["message.queued"])
    await old.close()

    fresh = FakeDeliver()
    setattr(world.app_state, MAILER_STATE_ATTR, Mailer(world.app_state, deliver=fresh))
    stop = await start_factory_services(world.app_state)
    await getattr(world.app_state, MAILER_STATE_ATTR).drain()
    await stop()

    assert len(fresh.calls) == (1 if replays else 0)
