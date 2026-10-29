"""Issues and detectors: every rule (positive, negative, dedupe, auto-resolve),
the lifecycle routes and their levels, "Report a problem", the incident page,
the snapshot badge and the Slack webhook that no read route ever shows."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio
from fastapi.routing import APIRoute

from omnigent import factory as F
from omnigent.entities.factory import FactoryEvent, FactoryIssue
from omnigent.runner.identity import RUNNER_TUNNEL_TOKEN_HEADER
from omnigent.server.auth import LEVEL_READ
from omnigent.server.factory import detectors, events, issues, snapshot
from omnigent.server.factory.api import issues as issue_routes
from omnigent.server.factory.mailer import get_mailer
from omnigent.server.factory.origin import require_human_origin
from omnigent.stores.artifact_store.local import LocalArtifactStore
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit, error_code
from tests.server.factory.conftest import FactoryWorld

HEAD = "9be04c1" + "a" * 33
WEBHOOK = "https://hooks.slack.com/services/T000/B000/s3cretWebhookPath"


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    detectors.reset()
    issues.reset()
    snapshot.reset_cache()
    yield
    detectors.reset()
    issues.reset()
    snapshot.reset_cache()


def _fid(world: FactoryWorld) -> str:
    return world.id("lead_a")


def _issues(world: FactoryWorld, rule: str | None = None) -> list[FactoryIssue]:
    rows = world.deps.factory_store.list_issues(_fid(world), limit=500)
    return [r for r in rows if rule is None or r.rule == rule]


def _one(world: FactoryWorld, rule: str) -> FactoryIssue:
    [row] = _issues(world, rule)
    return row


async def _tick(world: FactoryWorld, now: float | None = None) -> None:
    members = [world.get(k) for k in ("lead_a", "lead_b", "pl")]
    children = [world.get(k) for k in ("ic", "reviewer", "helper")]
    await detectors.tick_factory(
        world.app_state, _fid(world), members, children, now or time.time()
    )


async def _emit(world: FactoryWorld, kind: str, **kwargs: Any) -> FactoryEvent:
    kwargs.setdefault("summary", kind)
    event = await events.emit(world.app_state, _fid(world), kind, **kwargs)
    assert event is not None
    await detectors.on_event(world.app_state, event)
    return event


def _member(world: FactoryWorld, key: str) -> str:
    return f"member:{world.id(key)}"


# ── member.dead ────────────────────────────────────────────────


class _Registry:
    def __init__(self) -> None:
        self.live: set[str] = set()

    def get(self, key: str) -> object | None:
        return object() if key in self.live else None


async def test_member_dead_from_the_health_label_and_relaunch_failure(world: FactoryWorld) -> None:
    pl = world.id("pl")
    await _tick(world)
    assert _issues(world) == []  # negative: everyone healthy

    await _emit(
        world, "needs_human", from_id=pl, severity="critical", data={"reason": "relaunch_failed"}
    )
    world.store.set_labels(pl, {F.LABEL_HEALTH: F.HEALTH_DEAD})
    await _tick(world)

    row = _one(world, "member.dead")
    assert (row.target, row.severity, row.assignee, row.class_) == (
        _member(world, "pl"),
        "critical",
        "owners",
        "incident",
    )
    assert row.ref == "INC-1"
    opened = world.deps.factory_store.find_events(_fid(world), "issue.opened", 0)
    assert len(opened) == 1  # dedupe: the tick re-saw it without a second opening

    world.store.set_labels(pl, {F.LABEL_HEALTH: F.HEALTH_OK})
    await _emit(world, "heartbeat.delivered", to_id=pl)

    row = _one(world, "member.dead")
    assert (row.status, row.resolved_by) == ("resolved", "server")


async def test_member_dead_when_its_pod_is_gone_for_two_ticks(world: FactoryWorld) -> None:
    tunnels, hosts = _Registry(), _Registry()
    world.app_state.tunnel_registry = tunnels
    world.app_state.host_registry = hosts
    tunnels.live.update({"runner_pl"})
    for key in ("lead_a", "lead_b"):
        world.store.set_runner_id(world.id(key), f"runner_{key}")
        tunnels.live.add(f"runner_{key}")
    await _tick(world)
    assert _issues(world) == []  # negative: the runner is up

    tunnels.live.discard("runner_pl")  # the PL pod was killed
    await _tick(world)
    assert _issues(world) == []  # one tick may be a reconnect
    await _tick(world)
    row = _one(world, "member.dead")
    assert row.status == "open" and "offline for 2 ticks" in row.summary

    tunnels.live.add("runner_pl")
    await _tick(world)
    assert _one(world, "member.dead").status == "resolved"


# ── agent.repeat / agent.quiet ─────────────────────────────────


async def test_agent_repeat_dedupes_and_emits_once(world: FactoryWorld) -> None:
    ic = world.get("ic")
    for count, first in ((5, True), (6, False)):
        await detectors.agent_repeat(
            world.app_state,
            _fid(world),
            ic,
            role="ic",
            project="api-version",
            tool="Bash",
            arg="pnpm test",
            count=count,
            first=first,
        )

    row = _one(world, "agent.repeat")
    assert row.count == 2 and row.status == "open" and "6×" in row.summary
    assert len(world.deps.factory_store.find_events(_fid(world), "agent.repeat", 0)) == 1

    await detectors.clear(world.app_state, _fid(world), "agent.repeat", row.target)
    assert _one(world, "agent.repeat").status == "resolved"


async def _repeat(world: FactoryWorld, key: str = "ic") -> FactoryIssue:
    await detectors.agent_repeat(
        world.app_state,
        _fid(world),
        world.get(key),
        role="ic",
        project="api-version",
        tool="Bash",
        arg="pnpm test",
        count=5,
        first=True,
    )
    return _one(world, "agent.repeat")


async def test_agent_repeat_resolves_on_the_tick_once_its_worker_is_closed(
    world: FactoryWorld,
) -> None:
    await _repeat(world)
    await _tick(world)
    assert _one(world, "agent.repeat").status == "open"  # negative: open and fresh

    # The PL closed the worker; the poller (or a restart) lost its track.
    world.store.set_labels(world.id("ic"), {"omnigent.closed": "true"})
    members = [world.get(k) for k in ("lead_a", "lead_b", "pl")]
    children = [world.get(k) for k in ("reviewer", "helper")]
    await detectors.tick_factory(world.app_state, _fid(world), members, children, time.time())

    row = _one(world, "agent.repeat")
    assert (row.status, row.resolved_by) == ("resolved", "server")
    [resolved] = world.deps.factory_store.find_events(_fid(world), "issue.resolved", 0)
    assert resolved.data["reason"] == "agent closed"


async def test_agent_repeat_resolves_once_nothing_re_raised_it_for_a_window(
    world: FactoryWorld,
) -> None:
    row = await _repeat(world)
    await _tick(world, now=row.last_seen + 29 * 60)
    assert _one(world, "agent.repeat").status == "open"

    await _tick(world, now=row.last_seen + 30 * 60)

    assert _one(world, "agent.repeat").status == "resolved"


async def test_agent_quiet_from_a_stuck_alert_clears_when_it_stops_running(
    world: FactoryWorld,
) -> None:
    ic = world.id("ic")
    await _emit(world, "alert.stuck", from_id=ic, summary="ic:task-1 running with no progress")
    await _emit(world, "alert.stuck", from_id=ic, summary="ic:task-1 running with no progress")

    row = _one(world, "agent.quiet")
    assert row.count == 2 and row.target == _member(world, "ic")

    await _tick(world)  # not running any more
    assert _one(world, "agent.quiet").status == "resolved"


# ── loops ──────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["loop.exhausted", "loop.thrashing", "loop.stalled"])
async def test_loop_states_become_incidents_and_clear(world: FactoryWorld, kind: str) -> None:
    store = world.deps.factory_store
    state = kind.removeprefix("loop.")
    loop, _ = store.upsert_loop(_fid(world), "implement", "task:t1", state=state)
    await _emit(world, "loop.started", data={"loop_id": loop.id})
    assert _issues(world) == []  # negative

    await _emit(world, kind, project="api-version", data={"loop_id": loop.id, "kind": "implement"})
    await _emit(world, kind, project="api-version", data={"loop_id": loop.id, "kind": "implement"})
    row = _one(world, kind)
    assert (row.target, row.count, row.status) == (f"loop:{loop.id}", 2, "open")
    assert detectors.runbook(kind) == ["raise_budget", "switch_vendor", "fresh_restart", "abandon"]

    store.update_loop(loop.id, state="escalated")
    await _tick(world)
    assert _one(world, kind).status == "open"  # escalated is still the same trouble

    store.update_loop(loop.id, state="running")
    await _tick(world)
    assert _one(world, kind).status == "resolved"


async def test_a_loop_event_of_progress_resolves_it(world: FactoryWorld) -> None:
    await _emit(world, "loop.exhausted", data={"loop_id": "l" * 32})
    await _emit(world, "loop.budget_raised", data={"loop_id": "l" * 32})

    assert _one(world, "loop.exhausted").status == "resolved"


# ── spend.rate ─────────────────────────────────────────────────


async def test_spend_rate_above_the_alert(
    world: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    costs = {"total": 0.0}
    pl = world.id("pl")

    def _cost(conv: Any, _store: Any) -> float | None:
        return costs["total"] if conv.id == pl else None

    monkeypatch.setattr(snapshot, "subtree_cost", _cost)
    t0 = time.time()
    await _tick(world, t0)
    costs["total"] = 1.0
    await _tick(world, t0 + 20 * 60)
    assert _issues(world, "spend.rate") == []  # negative: 3 $/h
    assert detectors.spend_rate(_fid(world)) == 3.0

    costs["total"] = 11.0
    await _tick(world, t0 + 25 * 60)
    targets = sorted(r.target for r in _issues(world, "spend.rate"))
    assert targets == ["factory", "project:api-version"]
    assert {r.assignee for r in _issues(world, "spend.rate")} == {"owners"}

    await _tick(world, t0 + 70 * 60)  # nothing spent for the whole window
    assert {r.status for r in _issues(world, "spend.rate")} == {"resolved"}


# ── PRs, decisions, previews ───────────────────────────────────


def _pr(world: FactoryWorld, **fields: Any) -> Any:
    row, _ = world.deps.factory_store.upsert_pr(
        _fid(world), "caffeinelabs/app", 9630, project_slug="api-version", head_sha=HEAD, **fields
    )
    return row


async def test_ready_pr_nobody_reviews(world: FactoryWorld) -> None:
    now = time.time()
    pr = _pr(world, ready=True, ready_at=int(now - 3600))
    await _tick(world, now)
    assert _issues(world) == []  # negative: ready for 1 h

    await _tick(world, now + 4 * 3600)
    row = _one(world, "pr.ready_unreviewed")
    assert (row.target, row.severity) == (f"pr:{pr.id}", "notice")
    await _tick(world, now + 4 * 3600 + 1)
    assert _one(world, "pr.ready_unreviewed").count == 1  # dedupe

    world.deps.factory_store.update_pr(
        pr.id, human_review={"verdict": "approved", "at": int(now + 4 * 3600)}
    )
    await _tick(world, now + 4 * 3600 + 2)
    assert _one(world, "pr.ready_unreviewed").status == "resolved"


async def test_blocking_decision_waiting(world: FactoryWorld) -> None:
    store = world.deps.factory_store
    now = time.time()
    blocking, _ = store.create_decision(_fid(world), dedupe_key="d1", question="Which db?")
    store.create_decision(_fid(world), dedupe_key="d2", question="FYI?", blocking=False)
    await _tick(world, now + 60)
    assert _issues(world) == []

    await _tick(world, now + 31 * 60)
    row = _one(world, "decision.waiting")
    assert row.target == f"decision:{blocking.id}" and "D-1 waiting 31 min" in row.summary

    store.answer_decision(blocking.id, option_id=None, text="pg", by="alice")
    await _tick(world, now + 32 * 60)
    assert _one(world, "decision.waiting").status == "resolved"


async def test_preview_gone_on_a_ready_pr(world: FactoryWorld) -> None:
    pr = _pr(world, ready=True, preview={"name": "p9630", "state": "ready"})
    await _tick(world)
    assert _issues(world) == []

    world.deps.factory_store.update_pr(pr.id, preview={"name": "p9630", "state": "gone"})
    await _tick(world)
    assert _one(world, "preview.gone_ready").summary == "preview p9630 of ready PR #9630 is gone"

    world.deps.factory_store.update_pr(pr.id, preview={"name": "p9630", "state": "ready"})
    await _tick(world)
    assert _one(world, "preview.gone_ready").status == "resolved"


# ── mail and the bus ───────────────────────────────────────────


async def test_mail_stuck_in_the_queue(world: FactoryWorld) -> None:
    pl = world.id("pl")
    now = time.time()
    get_mailer(world.app_state)._queued["mail_1"] = now - 11 * 60
    get_mailer(world.app_state)._queued["mail_2"] = now - 60
    for mail_id, age in (("mail_1", 11 * 60), ("mail_2", 60)):
        await _emit(
            world,
            "message.queued",
            to_id=pl,
            subject=f"mail:{mail_id}",
            data={"mail_id": mail_id, "kind": "request"},
            ts=int((now - age) * 1000),
        )
    await _tick(world, now)

    row = _one(world, "mail.stuck")
    assert row.target == _member(world, "pl") and row.evidence == {"mail_ids": ["mail_1"]}

    await _emit(
        world, "message.delivered", to_id=pl, subject="mail:mail_1", data={"mail_id": "mail_1"}
    )
    await _tick(world, now)
    assert _one(world, "mail.stuck").status == "resolved"


async def test_bus_ping_pong(world: FactoryWorld) -> None:
    a, b = world.id("lead_b"), world.id("pl")
    now = time.time()
    for n in range(7):
        src, dst = (a, b) if n % 2 else (b, a)
        await _emit(
            world,
            "message.queued",
            from_id=src,
            to_id=dst,
            subject=f"mail:pp{n}",
            data={"kind": "info"},
            ts=int((now - 600 + n) * 1000),
        )
    await _tick(world, now)
    row = _one(world, "bus.pingpong")
    assert row.class_ == "finding" and row.ref == "F-1" and "7 messages" in row.summary

    await _tick(world, now + 31 * 60)
    assert _one(world, "bus.pingpong").status == "resolved"


async def test_six_messages_or_progress_is_no_ping_pong(world: FactoryWorld) -> None:
    a, b = world.id("lead_b"), world.id("pl")
    now = time.time()
    for n in range(8):
        await _emit(
            world,
            "message.queued",
            from_id=a,
            to_id=b,
            subject=f"mail:x{n}",
            project="api-version",
            data={"kind": "info"},
            ts=int((now - 300) * 1000),
        )
    await _emit(world, "stage.changed", project="api-version", ts=int((now - 200) * 1000))
    await _tick(world, now)
    assert _issues(world, "bus.pingpong") == []


async def test_bus_orphan_request(world: FactoryWorld) -> None:
    a, b = world.id("pl"), world.id("lead_b")
    now = time.time()
    await _emit(
        world,
        "message.queued",
        from_id=a,
        to_id=b,
        subject="mail:o1",
        data={"kind": "request"},
        ts=int((now - 3 * 3600) * 1000),
    )
    await _emit(
        world,
        "message.queued",
        from_id=a,
        to_id=b,
        subject="mail:o2",
        data={"kind": "request"},
        ts=int((now - 600) * 1000),
    )
    await _tick(world, now)
    row = _one(world, "bus.orphan")  # only the 3 h old one
    assert row.target == f"bus:{a}>{b}" and "unanswered for 3.0 h" in row.summary

    await _emit(
        world,
        "message.queued",
        from_id=b,
        to_id=a,
        subject="mail:o3",
        data={"kind": "reply"},
        ts=int((now - 60) * 1000),
    )
    await _tick(world, now)
    assert _one(world, "bus.orphan").status == "resolved"


# ── PR findings ────────────────────────────────────────────────


async def test_gate_flapping(world: FactoryWorld) -> None:
    pr = _pr(world)
    now = time.time()
    for n, kind in enumerate(("pr.ready", "pr.not_ready")):
        await _emit(world, kind, data={"pr_id": pr.id}, ts=int((now - 600 + n) * 1000))
    await _tick(world, now)
    assert _issues(world) == []  # two flips

    await _emit(world, "pr.ready", data={"pr_id": pr.id}, ts=int((now - 500) * 1000))
    await _tick(world, now)
    assert _one(world, "gate.flapping").target == f"pr:{pr.id}"

    await _tick(world, now + 3 * 3600)
    assert _one(world, "gate.flapping").status == "resolved"


async def test_claim_mismatch(world: FactoryWorld) -> None:
    pr = _pr(world)
    data = {"pr_id": pr.id, "claim": "ready_for_human_merge"}
    await _emit(world, "pr.claim", data={**data, "claim_mismatch": False})
    assert _issues(world) == []

    await _emit(world, "pr.claim", data={**data, "claim_mismatch": True})
    await _emit(world, "pr.claim", data={**data, "claim_mismatch": True})
    row = _one(world, "claim.mismatch")
    assert row.count == 2 and row.class_ == "finding"

    await _emit(world, "pr.claim", data={**data, "claim_mismatch": False})
    assert _one(world, "claim.mismatch").status == "resolved"


async def test_an_early_claim_is_no_mismatch(world: FactoryWorld) -> None:
    """Only pending gates disagree (CI running): no finding, and an open one stays open."""
    pr = _pr(world)
    data = {"pr_id": pr.id, "claim": "ready_for_human_merge", "claim_mismatch": True}
    await _emit(world, "pr.claim", data={**data, "claim_status": "early"})
    assert _issues(world) == []
    await _emit(world, "pr.claim", data={**data, "claim_status": "refuted"})
    await _emit(world, "pr.claim", data={**data, "claim_status": "early"})
    row = _one(world, "claim.mismatch")
    assert (row.count, row.status) == (1, "open")


async def test_ci_flaky_is_a_fact(world: FactoryWorld) -> None:
    pr = _pr(world)
    other = "b" * 40
    subject = "pr:caffeinelabs/app#9630"
    gate = {"pr_id": pr.id, "gate": "checks"}
    await _emit(
        world, "gate.changed", subject=subject, data={**gate, "to": "fail", "head_sha": other}
    )
    await _emit(
        world, "gate.changed", subject=subject, data={**gate, "to": "pass", "head_sha": HEAD}
    )
    assert _issues(world) == []  # a new head passing is no flake

    await _emit(
        world, "gate.changed", subject=subject, data={**gate, "to": "fail", "head_sha": HEAD}
    )
    await _emit(
        world, "gate.changed", subject=subject, data={**gate, "to": "pass", "head_sha": HEAD}
    )
    row = _one(world, "ci.flaky")
    assert row.target == f"pr:{pr.id}@{HEAD[:7]}"

    await _tick(world)
    assert _one(world, "ci.flaky").status == "open"  # facts stay for a person


async def test_ask_unneeded(world: FactoryWorld) -> None:
    store = world.deps.factory_store
    pl = world.id("pl")
    options = [{"id": "a", "label": "A", "recommended": True}, {"id": "b", "label": "B"}]
    for n in range(3):
        decision, _ = store.create_decision(
            _fid(world),
            dedupe_key=f"q{n}",
            question=f"Q{n}?",
            options=options,
            asked_by_session_id=pl,
        )
        store.answer_decision(decision.id, option_id="a", text=None, by="alice")
        # Answers name their decision by ref, as the answer route emits them.
        await _emit(world, "decision.answered", subject=f"decision:D-{decision.number}")
        if n < 2:
            assert _issues(world) == []

    row = _one(world, "ask.unneeded")
    assert row.target == _member(world, "pl") and len(row.evidence["decisions"]) == 3


async def test_task_unplanned_is_a_fact(world: FactoryWorld) -> None:
    await _emit(
        world, "task.unplanned", project="api-version", data={"task": "task:api-version/hotfix"}
    )

    row = _one(world, "task.unplanned")
    assert (row.class_, row.target, row.severity) == ("finding", "task:api-version/hotfix", "info")
    await _tick(world)
    assert _one(world, "task.unplanned").status == "open"


async def test_heartbeat_waste(world: FactoryWorld) -> None:
    pl = world.id("pl")
    world.store.set_session_usage(pl, {"total_cost_usd": 1.0})
    now = time.time()
    await _emit(world, "heartbeat.delivered", to_id=pl)
    world.store.set_session_usage(pl, {"total_cost_usd": 2.0})
    await _tick(world, now)
    assert _issues(world) == []  # $1 wasted is under the line

    for spent in (4.0, 7.5):
        await _emit(world, "heartbeat.delivered", to_id=pl)
        world.store.set_session_usage(pl, {"total_cost_usd": spent})
        await _tick(world, now)
    row = _one(world, "heartbeat.waste")
    assert row.target == _member(world, "pl") and row.evidence["usd"] == 6.5

    await _tick(world, now + 86400)  # a new day starts the count again
    assert _one(world, "heartbeat.waste").status == "resolved"


async def test_a_beat_that_changed_something_is_no_waste(world: FactoryWorld) -> None:
    pl = world.id("pl")
    world.store.set_session_usage(pl, {"total_cost_usd": 0.0})
    await _emit(world, "heartbeat.delivered", to_id=pl)
    await _emit(world, "dispatch", from_id=pl, project="api-version")
    world.store.set_session_usage(pl, {"total_cost_usd": 9.0})
    await _tick(world)
    assert _issues(world) == []


def test_every_rule_of_the_spec_table_is_known() -> None:
    expected = {
        "member.dead",
        "agent.repeat",
        "agent.quiet",
        "loop.exhausted",
        "loop.thrashing",
        "loop.stalled",
        "spend.rate",
        "pr.ready_unreviewed",
        "decision.waiting",
        "preview.gone_ready",
        "mail.stuck",
        "bus.pingpong",
        "bus.orphan",
        "gate.flapping",
        "claim.mismatch",
        "ci.flaky",
        "ask.unneeded",
        "task.unplanned",
        "heartbeat.waste",
    }
    assert expected <= set(detectors.RULES)
    assert all(len(k) <= 32 for k in ("agent.repeat", "issue.opened", "issue.assigned"))


def test_thresholds_come_from_the_resolver_seam() -> None:
    assert detectors.threshold("f", "quiet_m") == 10
    restore = detectors.set_threshold_resolver(lambda fid, key: 3 if key == "quiet_m" else None)
    try:
        assert detectors.threshold("f", "quiet_m") == 3
        assert detectors.threshold("f", "repeat_tool_n") == 5
    finally:
        restore()
    assert detectors.threshold("f", "quiet_m") == 10


# ── Routes: lifecycle, report, page, badge, Slack ──────────────


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> AsyncIterator[Cockpit]:
    async for value in build_cockpit(world, db_uri, monkeypatch):
        value.deps.artifact_store = LocalArtifactStore(str(tmp_path / "secrets"))
        value.permissions.ensure_user("vic")
        for key in ("lead_a", "pl"):
            value.permissions.grant("vic", world.id(key), LEVEL_READ)
        yield value


async def _incident(cockpit: Cockpit, key: str = "pl", severity: str = "warning") -> FactoryIssue:
    row = await issues.raise_issue(
        cockpit.app.state,
        cockpit.fid,
        rule="agent.quiet",
        target=f"member:{cockpit.world.id(key)}",
        summary="quiet",
        severity=severity,
        project="api-version",
    )
    assert row is not None
    return row


def _url(cockpit: Cockpit, path: str = "") -> str:
    return f"/v1/factory/factories/{cockpit.fid}/issues{path}"


async def test_lifecycle_needs_an_operator(cockpit: Cockpit) -> None:
    row = await _incident(cockpit)

    viewer = await cockpit.post(_url(cockpit, f"/{row.ref}/ack"), user="vic")
    stranger = await cockpit.post(_url(cockpit, f"/{row.ref}/ack"), user="bob")
    agent = await cockpit.client.post(
        _url(cockpit, f"/{row.ref}/ack"),
        headers={"X-Test-User": "carol", RUNNER_TUNNEL_TOKEN_HEADER: "rt_x.y"},
    )
    assert viewer.status_code == 403
    assert stranger.status_code == 404
    assert agent.status_code == 403 and error_code(agent) == "human_only"

    acked = await cockpit.post(_url(cockpit, f"/{row.ref}/ack"), user="carol")
    assert acked.status_code == 200, acked.text
    assert acked.json()["issue"]["status"] == "acked"
    assigned = await cockpit.post(
        _url(cockpit, f"/{row.ref}/assign"), {"to": "owners"}, user="carol"
    )
    assert assigned.json()["issue"]["assignee"] == "owners"
    unknown = await cockpit.post(_url(cockpit, f"/{row.ref}/assign"), {"to": "zed"}, user="carol")
    assert error_code(unknown) == "unknown_assignee"
    short = await cockpit.post(_url(cockpit, f"/{row.ref}/snooze"), {"minutes": 1}, user="carol")
    assert error_code(short) == "invalid_snooze"
    snoozed = await cockpit.post(
        _url(cockpit, f"/{row.ref}/snooze"), {"minutes": 30}, user="carol"
    )
    assert snoozed.json()["issue"]["status"] == "snoozed"
    bad = await cockpit.post(
        _url(cockpit, f"/{row.ref}/resolve"), {"root_cause": "x"}, user="carol"
    )
    assert error_code(bad) == "invalid_root_cause"
    done = await cockpit.post(
        _url(cockpit, f"/{row.ref}/resolve"),
        {"note": "env flake", "root_cause": "env_flaky"},
        user="carol",
    )
    assert done.json()["issue"]["status"] == "resolved"
    assert done.json()["issue"]["resolved_by"] == "carol"
    again = await cockpit.post(_url(cockpit, f"/{row.ref}/ack"), user="carol")
    assert again.status_code == 409 and error_code(again) == "issue_closed"

    kinds = [e.kind for e in cockpit.events() if e.kind.startswith("issue.")]
    assert kinds == [
        "issue.opened",
        "issue.acked",
        "issue.assigned",
        "issue.snoozed",
        "issue.resolved",
    ]
    resolved = cockpit.events("issue.resolved")[0]
    assert (resolved.data["by"], resolved.data["by_role"]) == ("carol", "operator")
    [feedback] = cockpit.store.list_feedback(cockpit.fid)
    assert (feedback.kind, feedback.tags, feedback.target_kind) == (
        "root_cause",
        ["env_flaky"],
        "issue",
    )


async def test_snoozed_issues_wake_up(cockpit: Cockpit) -> None:
    row = await _incident(cockpit)
    await issues.snooze(cockpit.app.state, cockpit.fid, row.ref or "", user_id="carol", minutes=10)

    assert await issues.unsnooze_due(cockpit.app.state, cockpit.fid, time.time()) == 0
    assert await issues.unsnooze_due(cockpit.app.state, cockpit.fid, time.time() + 601) == 1
    assert cockpit.store.get_issue(row.id).status == "open"


async def test_anyone_who_can_read_reports_a_problem(cockpit: Cockpit) -> None:
    body = {"summary": "The demo video is black", "class": "incident", "project": "api-version"}

    resp = await cockpit.post(_url(cockpit), body, user="vic")
    stranger = await cockpit.post(_url(cockpit), body, user="bob")
    agent = await cockpit.client.post(
        _url(cockpit), json=body, headers={"X-Test-User": "vic", RUNNER_TUNNEL_TOKEN_HEADER: "x"}
    )

    assert resp.status_code == 201, resp.text
    issue = resp.json()["issue"]
    assert (issue["ref"], issue["source"], issue["rule"], issue["target"]) == (
        "INC-1",
        "human",
        "human.report",
        "project:api-version",
    )
    assert stranger.status_code == 404
    assert agent.status_code == 403
    assert (
        error_code(await cockpit.post(_url(cockpit), {"summary": " "}, user="vic"))
        == "invalid_summary"
    )


async def test_list_filters_mine_and_hidden_projects(cockpit: Cockpit) -> None:
    await _incident(cockpit)
    await _incident(cockpit, key="ic", severity="critical")

    listing = (await cockpit.client.get(_url(cockpit), headers={"X-Test-User": "carol"})).json()
    assert [i["ref"] for i in listing["data"]] == ["INC-2", "INC-1"]
    assert listing["data"][0]["runbook"] == ["interrupt", "message"]
    assert listing["data"][0]["target_title"] == "ic:task-1"
    mine = await cockpit.client.get(
        _url(cockpit, "?assigned=me&count_only=1"), headers={"X-Test-User": "vic"}
    )
    assert mine.json() == {"count": 0, "mine": 0}
    # bob reads only the root: the project's issues are hidden from him.
    cockpit.permissions.grant("bob", cockpit.fid, LEVEL_READ)
    hidden = (await cockpit.client.get(_url(cockpit), headers={"X-Test-User": "bob"})).json()
    assert hidden["data"] == []


async def test_incident_page_shows_the_steps_before_it(cockpit: Cockpit) -> None:
    pl = cockpit.world.id("pl")
    await events.emit(cockpit.app.state, cockpit.fid, "dispatch", from_id=pl, summary="ic:task-1")
    await events.emit(cockpit.app.state, cockpit.fid, "pr.opened", summary="elsewhere")
    row = await _incident(cockpit)

    page = await cockpit.client.get(_url(cockpit, f"/{row.ref}"), headers={"X-Test-User": "vic"})

    assert page.status_code == 200, page.text
    body = page.json()
    assert body["issue"]["ref"] == "INC-1"
    kinds = [e["kind"] for e in body["events"]]
    assert "dispatch" in kinds and "pr.opened" not in kinds
    assert [s["session_id"] for s in body["sessions"]] == [pl]
    missing = await cockpit.client.get(_url(cockpit, "/INC-9"), headers={"X-Test-User": "vic"})
    assert missing.status_code == 404


async def test_the_snapshot_badge_counts_open_incidents(cockpit: Cockpit) -> None:
    await _incident(cockpit)
    finding = await issues.raise_issue(
        cockpit.app.state,
        cockpit.fid,
        rule="ci.flaky",
        target="pr:x",
        summary="f",
        class_="finding",
    )
    assert finding is not None
    snapshot.reset_cache()

    body = (
        await cockpit.client.get(
            f"/v1/factory/factories/{cockpit.fid}", headers={"X-Test-User": "vic"}
        )
    ).json()

    assert body["counts"]["issues_open"] == 1


async def test_the_slack_webhook_is_owner_set_and_never_read_back(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    posted: list[tuple[str, str]] = []

    async def _post(url: str, text: str) -> None:
        posted.append((url, text))

    monkeypatch.setattr(issues, "slack_poster", _post)
    route = f"/v1/factory/factories/{cockpit.fid}/alerts/slack"

    operator = await cockpit.client.put(
        route, json={"url": WEBHOOK}, headers={"X-Test-User": "carol"}
    )
    assert operator.status_code == 403
    bad = await cockpit.client.put(
        route, json={"url": "https://evil.example/hook"}, headers={"X-Test-User": "alice"}
    )
    assert error_code(bad) == "invalid_webhook"
    ok = await cockpit.client.put(route, json={"url": WEBHOOK}, headers={"X-Test-User": "alice"})
    assert ok.status_code == 200 and ok.json() == {"slack": True}

    row = await _incident(cockpit, severity="critical")
    notice = await _incident(cockpit, key="ic", severity="notice")
    for _ in range(3):
        await asyncio.sleep(0)
    assert [url for url, _ in posted] == [WEBHOOK]  # warning or worse only
    assert "INC-1" in posted[0][1] and notice.ref == "INC-2"

    fid = cockpit.fid
    paths = [
        f"/v1/factory/factories/{fid}",
        f"/v1/factory/factories/{fid}/issues",
        f"/v1/factory/factories/{fid}/issues/{row.ref}",
        f"/v1/factory/factories/{fid}/agents",
        f"/v1/factory/factories/{fid}/agents/{cockpit.world.id('pl')}",
        f"/v1/factory/factories/{fid}/alerts/routing",
        f"/v1/factory/factories/{fid}/events",
        f"/v1/factory/factories/{fid}/people",
        "/v1/factory/factories",
        "/v1/factory/decisions",
    ]
    for user in ("alice", "carol", "vic"):
        for path in paths:
            resp = await cockpit.client.get(path, headers={"X-Test-User": user})
            assert resp.status_code in (200, 404), (path, resp.status_code)
            assert "s3cretWebhookPath" not in resp.text, path
            assert "hooks.slack.com" not in resp.text, path
    routing = await cockpit.client.get(paths[5], headers={"X-Test-User": "vic"})
    assert routing.json()["slack"] is True
    assert all("s3cret" not in str(e.data) for e in cockpit.events())

    cleared = await cockpit.client.put(route, json={"url": None}, headers={"X-Test-User": "alice"})
    assert cleared.json() == {"slack": False}


async def test_slack_gets_escaped_text_and_a_flapping_incident_once_an_hour(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    posted: list[str] = []

    async def _post(_url: str, text: str) -> None:
        posted.append(text)

    monkeypatch.setattr(issues, "slack_poster", _post)
    await issues.set_slack_webhook(cockpit.app.state, cockpit.fid, user_id="alice", url=WEBHOOK)

    attack = "<!channel> <https://evil.example|re-login here> & more"
    resp = await cockpit.post(
        _url(cockpit), {"summary": attack, "severity": "warning"}, user="vic"
    )
    assert resp.status_code == 201, resp.text
    for _ in range(3):
        await asyncio.sleep(0)

    [text] = posted
    assert "<" not in text and ">" not in text
    assert text.endswith(
        "human.report · &lt;!channel&gt; &lt;https://evil.example|re-login here&gt; &amp; more"
    )

    # A condition that flaps reopens its incident but reaches Slack once.
    row = await _incident(cockpit)
    await issues.clear_issue(cockpit.app.state, cockpit.fid, row.rule, row.target)
    reopened = await _incident(cockpit)
    for _ in range(3):
        await asyncio.sleep(0)
    assert reopened.status == "open" and reopened.id == row.id
    assert len(cockpit.events("issue.opened")) == 3
    assert len(posted) == 2


def test_slack_text_escapes_every_part() -> None:
    issue = FactoryIssue(
        id="i1",
        factory_id="f1",
        class_="incident",
        number=7,
        rule="agent.repeat",
        target="member:x",
        summary="ic failing `echo <@U123>`",
        severity="warning",
    )

    assert issues.slack_text(issue, "a<b>&c") == (
        "[a&lt;b&gt;&amp;c] INC-7 · warning · agent.repeat · ic failing `echo &lt;@U123&gt;`"
    )


async def test_reports_are_rate_limited_per_person(cockpit: Cockpit) -> None:
    body = {"summary": "something is off"}
    for _ in range(issues.REPORTS_PER_HOUR):
        assert (await cockpit.post(_url(cockpit), body, user="vic")).status_code == 201

    refused = await cockpit.post(_url(cockpit), body, user="vic")
    other = await cockpit.post(_url(cockpit), body, user="carol")

    assert refused.status_code == 429 and error_code(refused) == "rate_limited"
    assert other.status_code == 201


async def test_the_badge_hides_incidents_of_agents_the_viewer_cannot_read(
    cockpit: Cockpit,
) -> None:
    for key in ("pl", "lead_b"):
        row = await issues.raise_issue(
            cockpit.app.state,
            cockpit.fid,
            rule="agent.quiet",
            target=f"member:{cockpit.world.id(key)}",
            summary="quiet",
        )
        assert row is not None and row.project_slug is None
    snapshot.reset_cache()

    async def _badge(user: str) -> int:
        resp = await cockpit.client.get(
            f"/v1/factory/factories/{cockpit.fid}", headers={"X-Test-User": user}
        )
        return resp.json()["counts"]["issues_open"]

    async def _listed(user: str) -> int:
        resp = await cockpit.client.get(_url(cockpit), headers={"X-Test-User": user})
        return len(resp.json()["data"])

    # vic reads Lead A and the PL, not Lead B.
    assert await _badge("vic") == await _listed("vic") == 1
    assert await _badge("alice") == await _listed("alice") == 2


def test_every_issue_write_route_requires_human_origin() -> None:
    writes = [
        route
        for route in issue_routes.router.routes
        if isinstance(route, APIRoute) and route.methods & {"POST", "PUT", "PATCH", "DELETE"}
    ]
    assert len(writes) == 6
    for route in writes:
        calls = [dep.call for dep in route.dependant.dependencies]
        assert require_human_origin in calls, route.path


async def test_new_issues_carry_the_factory_definition_version(cockpit: Cockpit) -> None:
    from omnigent.server.factory.telemetry import spans

    spans.reset()
    row = await _incident(cockpit)
    # The first row resolved (and seeded) the definition, so it is stamped from the start.
    assert row.def_version == 1
    resp = await cockpit.post(
        _url(cockpit), {"summary": "the preview is stale", "project": "api-version"}, user="vic"
    )
    assert resp.status_code in (200, 201), resp.text
    reported = cockpit.store.get_issue(resp.json()["issue"]["id"])
    assert reported is not None and reported.def_version == 1
