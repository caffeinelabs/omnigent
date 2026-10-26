"""Decisions the server opens for the human: relaunch failed, blocked on approval, storms."""

from __future__ import annotations

from typing import Any

import pytest

from omnigent import factory as F
from omnigent.server.factory.delivery import DeliveryOutcome
from omnigent.server.factory.notify import (
    FactoryNotifier,
    register_relay_notifier,
    schedule_factory_status_edge,
)
from omnigent.server.factory.supervisor import (
    DEFAULT_BLOCKED_ESCALATE_S,
    FactorySupervisor,
    SupervisorConfig,
)
from tests.server.factory.conftest import FactoryApi, FactoryWorld, Supervision

PL_HEARTBEAT_S = 600
BLOCKED_ON = "permission: Bash(git push --force)"


def _store(s: Supervision) -> Any:
    return s.world.app_state.factory.factory_store


def _decisions(s: Supervision, source: str) -> list[Any]:
    rows = _store(s).list_decisions(factory_id=s.world.id("lead_a"), limit=100)
    return [d for d in rows if d.source == source]


def _quiet_leads(s: Supervision) -> None:
    for lead in ("lead_a", "lead_b"):
        s.world.store.set_labels(s.world.id(lead), {F.LABEL_HEARTBEAT_S: "0"})


async def _fail_three_times(s: Supervision) -> None:
    for _ in range(3):
        await s.tick()
        s.clock.advance(300)


# ── S2b relaunch failed ────────────────────────────────────────


async def test_relaunch_failed_opens_exactly_one_decision_per_episode(
    supervision: Supervision,
) -> None:
    s, w = supervision, supervision.world
    pl = w.id("pl")
    _quiet_leads(s)
    s.deliver.outcomes[pl] = [DeliveryOutcome(False, "runner_unavailable")] * 6

    s.clock.advance(PL_HEARTBEAT_S)
    await _fail_three_times(s)
    [decision] = _decisions(s, "relaunch_failed")
    assert decision.status == "open" and decision.blocking
    assert decision.asked_by_session_id == pl and decision.project_slug == "api-version"
    assert decision.question == '"PL · api-version" relaunch failed 3× (runner_unavailable)'
    assert [(o["id"], o.get("action"), o.get("target")) for o in decision.options] == [
        ("restart", "restart", f"member:{pl}"),
        ("relaunch", "relaunch", f"member:{pl}"),
        ("pause", "pause", "project:api-version"),
        ("dismiss", "dismiss", None),
    ]
    [opened] = _store(s).find_events(w.id("lead_a"), "decision.opened", 0)
    assert opened.subject == f"decision:{decision.ref}"

    # Still dead an hour later: the NEEDS HUMAN text repeats, the decision does not.
    s.clock.advance(3600)
    await _fail_three_times(s)
    assert len(s.deliver.texts_to(w.id("lead_b"))) == 2
    assert len(_decisions(s, "relaunch_failed")) == 1

    # It answers a beat again: the episode ends and its decision expires.
    s.clock.advance(PL_HEARTBEAT_S)
    await s.tick()
    [expired] = _decisions(s, "relaunch_failed")
    assert expired.status == "expired"
    assert len(_store(s).find_events(w.id("lead_a"), "decision.expired", 0)) == 1

    # A new death is a new episode.
    s.deliver.outcomes[pl] = [DeliveryOutcome(False, "runner_unavailable")] * 3
    s.clock.advance(PL_HEARTBEAT_S)
    await _fail_three_times(s)
    assert sorted(d.status for d in _decisions(s, "relaunch_failed")) == ["expired", "open"]


async def test_relaunch_failed_survives_a_supervisor_restart(supervision: Supervision) -> None:
    s, w = supervision, supervision.world
    pl = w.id("pl")
    _quiet_leads(s)
    s.deliver.outcomes[pl] = [DeliveryOutcome(False, "runner_unavailable")] * 9
    s.clock.advance(PL_HEARTBEAT_S)
    await _fail_three_times(s)

    restarted = FactorySupervisor(
        w.app_state, config=SupervisorConfig(), deliver=s.deliver, clock=s.clock
    )
    s.clock.advance(3600)
    for _ in range(3):
        await restarted.tick()
        await restarted.drain()
        s.clock.advance(300)
    await s.settle()

    assert len(_decisions(s, "relaunch_failed")) == 1


# ── S5 blocked on an approval ──────────────────────────────────


async def _edge(s: Supervision, key: str, status: str, **data: Any) -> None:
    schedule_factory_status_edge(s.world.app_state, s.world.get(key), status, data)
    await s.settle()


async def test_blocked_on_approval_opens_a_decision_that_expires_when_it_clears(
    supervision: Supervision,
) -> None:
    s, w = supervision, supervision.world
    pl, lead_a = w.id("pl"), w.id("lead_a")
    w.store.set_labels(pl, {F.LABEL_HEARTBEAT_S: "0"})
    _quiet_leads(s)
    await _edge(s, "pl", "running", blocked_on=BLOCKED_ON)

    s.clock.advance(DEFAULT_BLOCKED_ESCALATE_S)
    await s.tick()

    [decision] = _decisions(s, "blocked_approval")
    assert decision.status == "open" and decision.asked_by_session_id == pl
    assert decision.question == '"PL · api-version" is waiting on a human approval'
    assert decision.context is not None and BLOCKED_ON in decision.context
    assert [(o["id"], o.get("href"), o.get("action")) for o in decision.options] == [
        ("open", "/inbox", None),
        ("dismiss", None, "dismiss"),
    ]
    [needs] = _store(s).find_events(lead_a, "needs_human", 0)
    assert needs.data is not None and needs.data["decision_id"] == decision.id
    assert len(s.deliver.texts_to(lead_a)) == 1  # the NEEDS HUMAN text is unchanged

    await _edge(s, "pl", "idle", output="done")

    [cleared] = _decisions(s, "blocked_approval")
    assert cleared.status == "expired"


async def test_blocked_dedupe_survives_a_notifier_restart(supervision: Supervision) -> None:
    s, w = supervision, supervision.world
    pl, lead_a = w.id("pl"), w.id("lead_a")
    w.store.set_labels(pl, {F.LABEL_HEARTBEAT_S: "0"})
    _quiet_leads(s)
    await _edge(s, "pl", "running", blocked_on=BLOCKED_ON)
    s.clock.advance(DEFAULT_BLOCKED_ESCALATE_S)
    await s.tick()
    assert len(s.deliver.texts_to(lead_a)) == 1

    # A server roll: the notifier and supervisor forget the episode, the edge arrives again.
    notifier = FactoryNotifier(w.app_state, clock=s.clock)
    w.app_state.factory_notifier = notifier
    register_relay_notifier(notifier)
    s.notifier = notifier
    s.supervisor = FactorySupervisor(
        w.app_state, config=SupervisorConfig(), deliver=s.deliver, clock=s.clock
    )
    await _edge(s, "pl", "running", blocked_on=BLOCKED_ON)
    s.clock.advance(DEFAULT_BLOCKED_ESCALATE_S)
    await s.tick()
    await s.tick()

    assert len(s.deliver.texts_to(lead_a)) == 1
    assert len(_store(s).find_events(lead_a, "needs_human", 0)) == 1
    [decision] = _decisions(s, "blocked_approval")
    assert decision.status == "open"

    # The restored episode still expires its decision when it clears.
    await _edge(s, "pl", "idle", output="ok")
    assert _decisions(s, "blocked_approval")[0].status == "expired"


async def test_a_new_prompt_after_a_cleared_episode_escalates_again(
    supervision: Supervision,
) -> None:
    s, w = supervision, supervision.world
    lead_a = w.id("lead_a")
    w.store.set_labels(w.id("pl"), {F.LABEL_HEARTBEAT_S: "0"})
    _quiet_leads(s)
    for _ in range(2):
        await _edge(s, "pl", "running", blocked_on=BLOCKED_ON)
        s.clock.advance(DEFAULT_BLOCKED_ESCALATE_S)
        await s.tick()
        await _edge(s, "pl", "idle", output="ok")

    assert len(s.deliver.texts_to(lead_a)) == 2
    assert sorted(d.status for d in _decisions(s, "blocked_approval")) == ["expired", "expired"]


# ── Storm ──────────────────────────────────────────────────────


@pytest.mark.parametrize("posts", [3])
async def test_a_message_storm_opens_a_pause_decision(
    world: FactoryWorld, api: FactoryApi, posts: int
) -> None:
    api.mailer.sender_rate_per_hour = 1
    pl, lead_a = world.id("pl"), world.id("lead_a")
    statuses = []
    for _ in range(posts):
        resp = await api.client.post(
            "/v1/factory/messages",
            json={"from_session_id": pl, "to": "lead", "message": "ping", "kind": "info"},
        )
        statuses.append(resp.status_code)
    await api.mailer.drain()

    assert statuses == [202, 429, 429]
    store = api.app.state.factory.factory_store
    [decision] = store.list_decisions(factory_id=lead_a)
    assert decision.source == "storm" and not decision.blocking
    assert decision.asked_by_session_id == pl
    assert [(o["id"], o.get("action"), o.get("target")) for o in decision.options] == [
        ("pause", "pause", f"member:{pl}"),
        ("dismiss", "dismiss", None),
    ]
    assert len(store.find_events(lead_a, "rate_limited", 0)) == 2
