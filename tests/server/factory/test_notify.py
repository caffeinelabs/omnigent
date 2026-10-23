"""Status-edge reactions: failed alerts, reply notices, blocked escalation, scoping."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from omnigent import factory as F
from omnigent.server.factory.mailer import MAILER_STATE_ATTR, Mailer, MailItem, new_mail_id
from omnigent.server.factory.notify import (
    FAILED_ALERT_DEBOUNCE_S,
    NOTIFIER_STATE_ATTR,
    get_notifier,
    iso_utc,
    on_status_edge,
    schedule_factory_status_edge,
)
from omnigent.server.factory.supervisor import DEFAULT_BLOCKED_ESCALATE_S
from tests.server.factory.conftest import FactoryWorld, FakeDeliver, Supervision
from tests.server.helpers import create_test_agent

BLOCKED_ON = "permission: Bash(rm -rf build)"


async def _edge(s: Supervision, key: str, status: str, **data: Any) -> None:
    """Fire the route hook for one status edge of a seeded session and settle."""
    schedule_factory_status_edge(s.world.app_state, s.world.get(key), status, data)
    await s.settle()


async def _deliver_request(
    s: Supervision, sender: str, target: str, kind: str = "request"
) -> None:
    """Accept and deliver a factory post, as ``POST /v1/factory/messages`` would."""
    s.mailer.record_post(sender, target, kind)
    s.mailer.submit(
        MailItem(
            mail_id=new_mail_id(),
            sender_id=sender,
            target_id=target,
            target_title=None,
            text=f"[Factory message · kind={kind}] please add GET /version",
            kind=kind,
        )
    )
    await s.mailer.drain()


def _quiet(s: Supervision) -> None:
    """Switch member heartbeats off so supervisor ticks deliver only what a test checks."""
    for key in ("lead_a", "pl"):
        s.world.store.set_labels(s.world.id(key), {F.LABEL_HEARTBEAT_S: "0"})


# ── failed ──────────────────────────────────────────────────────


async def test_failed_alerts_the_supervisor_with_debounce(supervision: Supervision) -> None:
    w, clock = supervision.world, supervision.clock
    pl, lead_b = w.id("pl"), w.id("lead_b")
    output = "API Error: 529 overloaded\n" + "x" * 400

    await _edge(supervision, "pl", "failed", output=output)
    detail = " ".join(output.split())[:300] + "…"
    assert supervision.deliver.calls == [
        (lead_b, f'[Factory alert · "PL · api-version" ({pl}) failed: {detail}]', False)
    ]

    clock.advance(FAILED_ALERT_DEBOUNCE_S - 1)
    await _edge(supervision, "pl", "failed", output="again")
    assert len(supervision.deliver.calls) == 1

    clock.advance(1)
    await _edge(supervision, "pl", "failed", output="still failing")
    assert supervision.deliver.texts_to(lead_b)[-1] == (
        f'[Factory alert · "PL · api-version" ({pl}) failed: still failing]'
    )


async def test_failed_sub_agent_alerts_its_parent(supervision: Supervision) -> None:
    w = supervision.world

    await _edge(supervision, "ic", "failed", output="harness boot failed")

    assert supervision.deliver.calls == [
        (
            w.id("pl"),
            f'[Factory alert · "ic:task-1" ({w.id("ic")}) failed: harness boot failed]',
            False,
        )
    ]


async def test_failed_without_detail_says_so(supervision: Supervision) -> None:
    await _edge(supervision, "lead_b", "failed")

    assert supervision.deliver.texts_to(supervision.world.id("lead_a")) == [
        f'[Factory alert · "Lead B · Sentinel" ({supervision.world.id("lead_b")}) '
        "failed: no error detail]"
    ]


async def test_failed_debounce_is_per_session(supervision: Supervision) -> None:
    w = supervision.world

    await _edge(supervision, "pl", "failed", output="one")
    await _edge(supervision, "lead_a", "failed", output="two")

    assert len(supervision.deliver.texts_to(w.id("lead_b"))) == 2


# ── reply notice ────────────────────────────────────────────────


async def test_idle_after_a_request_notifies_the_sender(supervision: Supervision) -> None:
    w, clock = supervision.world, supervision.clock
    lead_a, pl = w.id("lead_a"), w.id("pl")
    await _deliver_request(supervision, lead_a, pl)

    clock.advance(120)
    output = "Opened PR #12.\n" + "y" * 1500
    await _edge(supervision, "pl", "idle", output=output)

    last = " ".join(output.split())[:1200] + "…"
    assert supervision.deliver.texts_to(lead_a) == [
        f'[Factory notice · "PL · api-version" ({pl}) finished the turn your request started '
        f"(idle). Last output: «{last}». "
        f'Inspect with sys_session_get_history(session_id="{pl}").]'
    ]

    # The request is answered once; later turns are the target's own business.
    await _edge(supervision, "pl", "idle", output="more")
    assert len(supervision.deliver.texts_to(lead_a)) == 1


async def test_a_brief_also_expects_a_notice(supervision: Supervision) -> None:
    w = supervision.world
    await _deliver_request(supervision, w.id("lead_a"), w.id("pl"), kind="brief")

    await _edge(supervision, "pl", "idle", output="plan written")

    assert len(supervision.deliver.texts_to(w.id("lead_a"))) == 1


@pytest.mark.parametrize("answer_kind", ["reply", "info"])
async def test_no_notice_when_the_target_already_posted_back(
    supervision: Supervision, answer_kind: str
) -> None:
    w, clock = supervision.world, supervision.clock
    lead_a, pl = w.id("lead_a"), w.id("pl")
    await _deliver_request(supervision, lead_a, pl)

    clock.advance(30)
    supervision.mailer.record_post(pl, lead_a, answer_kind)
    clock.advance(30)
    await _edge(supervision, "pl", "idle", output="done")

    assert supervision.deliver.texts_to(lead_a) == []


async def test_an_older_post_back_does_not_count_as_a_reply(supervision: Supervision) -> None:
    w, clock = supervision.world, supervision.clock
    lead_a, pl = w.id("lead_a"), w.id("pl")
    supervision.mailer.record_post(pl, lead_a, "info")
    clock.advance(60)
    await _deliver_request(supervision, lead_a, pl)

    await _edge(supervision, "pl", "idle", output="done")

    assert len(supervision.deliver.texts_to(lead_a)) == 1


async def test_info_posts_expect_no_notice(supervision: Supervision) -> None:
    w = supervision.world
    await _deliver_request(supervision, w.id("lead_a"), w.id("pl"), kind="info")

    await _edge(supervision, "pl", "idle", output="noted")

    assert supervision.deliver.texts_to(w.id("lead_a")) == []


async def test_heartbeat_and_human_started_turns_produce_no_notice(
    supervision: Supervision,
) -> None:
    w, clock = supervision.world, supervision.clock
    clock.advance(600)
    await supervision.tick()  # heartbeat into the PL
    assert len(supervision.deliver.texts_to(w.id("pl"))) == 1

    await _edge(supervision, "pl", "running")
    await _edge(supervision, "pl", "idle", output="swept my ICs")
    # A human typed into Lead A; its turn ends.
    await _edge(supervision, "lead_a", "idle", output="hello human")

    assert [sid for sid, _, _ in supervision.deliver.calls] == [w.id("pl")]


# ── blocked on a human ──────────────────────────────────────────


async def test_blocked_session_escalates_to_the_root_once(supervision: Supervision) -> None:
    w, clock = supervision.world, supervision.clock
    lead_a = w.id("lead_a")
    _quiet(supervision)
    since = clock.now
    await _edge(supervision, "pl", "running", blocked_on=BLOCKED_ON)
    clock.advance(300)
    # A repeated blocked edge keeps the original start.
    await _edge(supervision, "pl", "running", blocked_on=BLOCKED_ON)

    clock.advance(DEFAULT_BLOCKED_ESCALATE_S - 301)
    await supervision.tick()
    assert supervision.deliver.calls == []

    clock.advance(1)
    await supervision.tick()
    await supervision.tick()
    assert supervision.deliver.calls == [
        (
            lead_a,
            f'[NEEDS HUMAN · "PL · api-version" waiting since {iso_utc(since)}: {BLOCKED_ON}]',
            False,
        )
    ]


async def test_blocked_sub_agent_escalates_to_the_root(supervision: Supervision) -> None:
    w, clock = supervision.world, supervision.clock
    _quiet(supervision)
    await _edge(supervision, "ic", "running", blocked_on=BLOCKED_ON)

    clock.advance(DEFAULT_BLOCKED_ESCALATE_S)
    await supervision.tick()

    assert [sid for sid, _, _ in supervision.deliver.calls] == [w.id("lead_a")]
    assert supervision.deliver.calls[0][1].startswith('[NEEDS HUMAN · "ic:task-1" waiting since')


@pytest.mark.parametrize("clearing", [("running", {}), ("idle", {"output": "ok"}), ("failed", {})])
async def test_the_next_unblocked_edge_clears_the_episode(
    supervision: Supervision, clearing: tuple[str, dict[str, Any]]
) -> None:
    w, clock = supervision.world, supervision.clock
    _quiet(supervision)
    await _edge(supervision, "ic", "running", blocked_on=BLOCKED_ON)
    status, data = clearing
    await _edge(supervision, "ic", status, **data)

    clock.advance(DEFAULT_BLOCKED_ESCALATE_S)
    await supervision.tick()

    assert supervision.deliver.texts_to(w.id("lead_a")) == []
    assert supervision.notifier.blocked_episode(w.id("ic")) is None


async def test_a_blocked_root_is_not_escalated_to_itself(supervision: Supervision) -> None:
    clock = supervision.clock
    _quiet(supervision)
    await _edge(supervision, "lead_a", "running", blocked_on=BLOCKED_ON)

    clock.advance(DEFAULT_BLOCKED_ESCALATE_S)
    await supervision.tick()

    assert supervision.deliver.calls == []


# ── scoping and switches ────────────────────────────────────────


async def test_non_members_are_untouched(supervision: Supervision) -> None:
    w, clock = supervision.world, supervision.clock
    _quiet(supervision)
    w.make_session(
        "plain_child", agent="plain", title="plain child", parent="plain", sub_agent_name="x"
    )
    # Even a pending entry for a non-member is left alone.
    await _deliver_request(supervision, w.id("lead_a"), w.id("plain"))
    supervision.deliver.calls.clear()

    for key in ("plain", "plain_child"):
        await _edge(supervision, key, "failed", output="boom")
        await _edge(supervision, key, "idle", output="done")
        await _edge(supervision, key, "running", blocked_on=BLOCKED_ON)
    clock.advance(DEFAULT_BLOCKED_ESCALATE_S)
    await supervision.tick()

    assert supervision.deliver.calls == []
    assert supervision.notifier.blocked_episode(w.id("plain")) is None
    assert supervision.notifier.blocked_episode(w.id("plain_child")) is None
    assert supervision.mailer.pop_pending_reply(w.id("plain")) is not None


async def test_flag_off_is_a_no_op(
    world: FactoryWorld,
    fake_deliver: FakeDeliver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(F.FACTORY_ENABLED_ENV, raising=False)
    mailer = Mailer(world.app_state, deliver=fake_deliver)
    setattr(world.app_state, MAILER_STATE_ATTR, mailer)

    schedule_factory_status_edge(world.app_state, world.get("pl"), "failed", {"output": "boom"})
    await on_status_edge(world.app_state, world.get("pl"), "failed", {"output": "boom"})
    await mailer.drain()

    assert getattr(world.app_state, NOTIFIER_STATE_ATTR, None) is None
    assert fake_deliver.calls == []
    await mailer.close()


async def test_without_factory_routes_the_hook_is_a_no_op(
    world: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(F.FACTORY_ENABLED_ENV, "1")
    state = SimpleNamespace()

    schedule_factory_status_edge(state, world.get("pl"), "failed", {"output": "boom"})

    assert getattr(state, NOTIFIER_STATE_ATTR, None) is None


async def test_the_hook_never_raises(
    supervision: Supervision, monkeypatch: pytest.MonkeyPatch
) -> None:
    schedule_factory_status_edge(supervision.world.app_state, object(), "failed", {})  # type: ignore[arg-type]

    def _broken(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("store down")

    monkeypatch.setattr("omnigent.server.factory.notify.resolve_factory_id", _broken)
    await _edge(supervision, "ic", "failed", output="boom")

    assert supervision.deliver.calls == []


# ── through the real status route ───────────────────────────────


async def _post_status(client: httpx.AsyncClient, session_id: str, data: dict[str, Any]) -> None:
    resp = await client.post(
        f"/v1/sessions/{session_id}/events",
        json={"type": "external_session_status", "data": data},
    )
    assert resp.status_code == 202, resp.text


async def _create_session(
    client: httpx.AsyncClient, agent_id: str, title: str, labels: dict[str, str]
) -> str:
    resp = await client.post(
        "/v1/sessions", json={"agent_id": agent_id, "title": title, "labels": labels}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _factory_pair(app: Any, client: httpx.AsyncClient) -> tuple[str, str, FakeDeliver]:
    """Create a root and a supervised member; record what the mailer delivers."""
    deliver = FakeDeliver()
    setattr(app.state, MAILER_STATE_ATTR, Mailer(app.state, deliver=deliver))
    agent = await create_test_agent(client)
    root = await _create_session(client, agent["id"], "Atlas", {})
    member = await _create_session(
        client,
        agent["id"],
        "PL · api-version",
        {
            F.LABEL_ID: root,
            F.LABEL_ROLE: F.ROLE_PROJECT_LEAD,
            F.LABEL_SUPERVISOR: root,
            F.LABEL_STATE: F.STATE_ACTIVE,
        },
    )
    return root, member, deliver


async def test_status_route_hook_is_inert_when_the_flag_is_unset(
    app: Any, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(F.FACTORY_ENABLED_ENV, raising=False)
    _root, member, deliver = await _factory_pair(app, client)

    await _post_status(client, member, {"status": "failed", "output": "boom"})
    mailer = getattr(app.state, MAILER_STATE_ATTR)
    await mailer.drain()

    assert getattr(app.state, NOTIFIER_STATE_ATTR, None) is None
    assert deliver.calls == []
    await mailer.close()


async def test_status_route_alerts_through_the_hook(
    app: Any, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(F.FACTORY_ENABLED_ENV, "1")
    root, member, deliver = await _factory_pair(app, client)

    await _post_status(client, member, {"status": "failed", "output": "turn crashed"})
    notifier = get_notifier(app.state)
    mailer = getattr(app.state, MAILER_STATE_ATTR)
    await notifier.drain()
    await mailer.drain()

    assert deliver.calls == [
        (root, f'[Factory alert · "PL · api-version" ({member}) failed: turn crashed]', False)
    ]
    await notifier.close()
    await mailer.close()
