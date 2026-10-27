"""Decision routing: default assignees, ``sys_factory_ask(for=…)``, answer rights,
claims and assignment, the ``assigned=me`` list and escalation on a fake clock."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from omnigent.entities.factory import FactoryDecision
from omnigent.server.factory import decision_routing, people, snapshot
from omnigent.server.factory import presence as factory_presence
from omnigent.server.factory.decision_routing import default_assignee, is_mine, may_answer
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit, error_code
from tests.server.factory.conftest import FactoryWorld, Supervision

MIA = "mia@caffeine.ai"
OPAL = "opal@caffeine.ai"
OWEN = "owen@caffeine.ai"
VIC = "vic@caffeine.ai"


@pytest.fixture(autouse=True)
def _fresh_state() -> Iterator[None]:
    snapshot.reset_cache()
    factory_presence.registry.reset()
    yield
    snapshot.reset_cache()
    factory_presence.registry.reset()


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in build_cockpit(world, db_uri, monkeypatch):
        state = value.app.state
        for user, role in (
            (MIA, "operator"),
            (OPAL, "operator"),
            (OWEN, "owner"),
            (VIC, "viewer"),
        ):
            await people.set_person(state, value.fid, user, role, by_user="alice")
        value.store.upsert_project(
            value.fid,
            "api-version",
            title="API version",
            repo="caffeinelabs/app",
            pl_session_id=value.world.id("pl"),
            charter={"goal": "GET /version", "human_owner": MIA},
        )
        yield value


async def _ask(cockpit: Cockpit, question: str = "Which table?", **extra: Any) -> httpx.Response:
    return await cockpit.client.post(
        "/v1/factory/agent/decisions",
        json={"from_session_id": cockpit.world.id("pl"), "question": question, **extra},
        headers={"X-Test-User": "alice"},
    )


def _decision(cockpit: Cockpit, resp: httpx.Response) -> FactoryDecision:
    row = cockpit.store.get_decision(resp.json()["id"])
    assert row is not None
    return row


async def _answer(cockpit: Cockpit, decision_id: str, user: str) -> httpx.Response:
    return await cockpit.post(
        f"/v1/factory/decisions/{decision_id}/answer", {"text": "knex_migrations"}, user=user
    )


# ── Defaults ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("source", "kwargs", "expected"),
    [
        ("agent", {}, "operators"),
        ("agent", {"human_owner": MIA}, MIA),
        ("done_check", {"human_owner": MIA}, MIA),
        ("done_check", {}, "operators"),
        ("relaunch_failed", {}, "owners"),
        ("storm", {}, "owners"),
        ("access_request", {}, "owners"),
        ("proposal", {"proposal_kind": "tuning"}, "owners"),
        ("proposal", {"proposal_kind": "rules"}, "owners"),
        ("proposal", {"proposal_kind": "code"}, "owners"),
        ("proposal", {"proposal_kind": "projects"}, "operators"),
        ("blocked_approval", {}, "operators"),
        ("pr_closed", {}, "operators"),
        ("loop", {}, "operators"),
        (
            "loop",
            {"options": [{"id": "r", "action": "raise_budget", "owner_only": True}]},
            "owners",
        ),
    ],
)
def test_default_assignee_by_source(source: str, kwargs: dict[str, Any], expected: str) -> None:
    assert default_assignee(source, **kwargs) == expected


async def test_an_agent_question_goes_to_the_projects_human_owner(cockpit: Cockpit) -> None:
    resp = await _ask(cockpit, subject="task:T-4")

    assert resp.status_code == 200, resp.text
    assert resp.json()["assignee"] == MIA
    assert "warning" not in resp.json()
    row = _decision(cockpit, resp)
    assert (row.assignee, row.subject) == (MIA, "task:T-4")


@pytest.mark.parametrize(
    ("wanted", "assignee", "warned"),
    [
        ("owners", "owners", False),
        ("operators", "operators", False),
        (OPAL, OPAL, False),
        ("OPAL@caffeine.ai", OPAL, False),
        ("owen", OWEN, False),
        ("nobody@caffeine.ai", "operators", True),
        (VIC, "operators", True),
    ],
)
async def test_ask_for_routes_the_decision(
    cockpit: Cockpit, wanted: str, assignee: str, warned: bool
) -> None:
    resp = await _ask(cockpit, **{"for": wanted})

    assert resp.status_code == 200, resp.text
    assert resp.json()["assignee"] == assignee
    assert ("warning" in resp.json()) is warned
    assert _decision(cockpit, resp).assignee == assignee


async def test_server_decisions_carry_their_default(cockpit: Cockpit) -> None:
    from omnigent.server.factory import server_decisions

    opened = await server_decisions.open_relaunch_failed_decision(
        cockpit.app.state, cockpit.world.get("pl"), cockpit.fid, "runner_unavailable", 3
    )

    assert opened is not None
    assert opened[0].assignee == "owners"
    [event] = cockpit.events("decision.opened")
    assert event.data["assignee"] == "owners"


# ── Answer rights ──────────────────────────────────────────────


def _row(assignee: str | None, claimed_by: str | None = None) -> FactoryDecision:
    return FactoryDecision(
        id="1" * 32,
        factory_id="2" * 32,
        number=1,
        question="q",
        assignee=assignee,
        claimed_by=claimed_by,
    )


@pytest.mark.parametrize(
    ("assignee", "user", "level", "answer", "mine"),
    [
        (None, OPAL, 2, True, True),
        ("operators", OPAL, 2, True, True),
        ("operators", VIC, 1, False, False),
        ("operators", OWEN, 3, True, True),
        ("owners", OPAL, 2, False, False),
        ("owners", OWEN, 3, True, True),
        (MIA, MIA, 2, True, True),
        (MIA, OPAL, 2, False, False),
        (MIA, OWEN, 3, True, False),
    ],
)
def test_who_may_answer_and_whose_it_is(
    assignee: str | None, user: str, level: int, answer: bool, mine: bool
) -> None:
    row = _row(assignee)

    assert may_answer(row, user, level) is answer
    assert is_mine(row, user, level) is mine


def test_a_claim_makes_it_the_claimers() -> None:
    row = _row("operators", claimed_by=MIA)

    assert is_mine(row, MIA, 2) is True
    assert is_mine(row, OPAL, 2) is False
    assert may_answer(row, OPAL, 2) is True


async def test_operators_answer_only_what_is_theirs(cockpit: Cockpit) -> None:
    for_mia = _decision(cockpit, await _ask(cockpit, "For Mia?"))
    for_owners = _decision(cockpit, await _ask(cockpit, "For owners?", **{"for": "owners"}))

    other = await _answer(cockpit, for_mia.id, OPAL)
    owners = await _answer(cockpit, for_owners.id, MIA)
    viewer = await _answer(cockpit, for_mia.id, VIC)
    mine = await _answer(cockpit, for_mia.id, MIA)
    owner = await _answer(cockpit, for_owners.id, OWEN)

    assert (other.status_code, error_code(other)) == (403, "not_assigned")
    assert other.json()["assignee"] == MIA
    assert (owners.status_code, error_code(owners)) == (403, "not_assigned")
    assert viewer.status_code == 403
    assert mine.status_code == 200, mine.text
    assert owner.status_code == 200, owner.text
    [text] = cockpit.mailer.texts_to(cockpit.world.id("pl"))[:1]
    assert text.startswith(f"[Factory decision · {for_mia.ref} answered by {MIA} (operator)")


# ── Claim and assign ───────────────────────────────────────────


async def test_claim_release_and_conflict(cockpit: Cockpit) -> None:
    row = _decision(cockpit, await _ask(cockpit, **{"for": "operators"}))
    url = f"/v1/factory/decisions/{row.id}/claim"

    first = await cockpit.post(url, {}, user=MIA)
    second = await cockpit.post(url, {}, user=OPAL)
    viewer = await cockpit.post(url, {}, user=VIC)
    released = await cockpit.post(url, {"release": True}, user=MIA)
    again = await cockpit.post(url, {}, user=OPAL)

    assert first.status_code == 200, first.text
    assert first.json()["decision"]["claimed_by"] == MIA
    assert (second.status_code, error_code(second)) == (409, "already_claimed")
    assert second.json()["claimed_by"] == MIA
    assert viewer.status_code == 403
    assert released.json()["decision"]["claimed_by"] is None
    assert again.json()["decision"]["claimed_by"] == OPAL
    assert [e.data["user"] for e in cockpit.events("decision.claimed")] == [MIA, OPAL]


async def test_assign_routes_and_owners_keep_theirs(cockpit: Cockpit) -> None:
    row = _decision(cockpit, await _ask(cockpit, **{"for": "operators"}))
    url = f"/v1/factory/decisions/{row.id}/assign"

    up = await cockpit.post(url, {"to": "owners"}, user=MIA)
    down = await cockpit.post(url, {"to": "operators"}, user=MIA)
    unknown = await cockpit.post(url, {"to": "nobody@x"}, user=OWEN)
    to_opal = await cockpit.post(url, {"to": OPAL}, user=OWEN)

    assert up.json()["decision"]["assignee"] == "owners"
    assert (down.status_code, error_code(down)) == (403, "owners_only")
    assert (unknown.status_code, error_code(unknown)) == (400, "unknown_assignee")
    assert to_opal.json()["decision"]["assignee"] == OPAL
    moves = [(e.data["from"], e.data["to"]) for e in cockpit.events("decision.assigned")]
    assert moves == [("operators", "owners"), ("owners", OPAL)]


async def test_a_personal_decision_moves_only_by_its_person_or_an_owner(
    cockpit: Cockpit,
) -> None:
    row = _decision(cockpit, await _ask(cockpit, "For Mia?"))
    url = f"/v1/factory/decisions/{row.id}/assign"

    grabbed = await cockpit.post(url, {"to": OPAL}, user=OPAL)
    handed = await cockpit.post(url, {"to": "operators"}, user=MIA)
    back = await cockpit.post(url, {"to": MIA}, user=OPAL)
    moved = await cockpit.post(url, {"to": OPAL}, user=OWEN)

    assert (grabbed.status_code, error_code(grabbed)) == (403, "assigned_to_person")
    assert grabbed.json()["assignee"] == MIA
    assert handed.json()["decision"]["assignee"] == "operators"
    assert back.json()["decision"]["assignee"] == MIA
    assert moved.json()["decision"]["assignee"] == OPAL


async def test_claim_and_assign_lose_to_a_concurrent_answer(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.server.factory import decisions

    first = _decision(cockpit, await _ask(cockpit, **{"for": "operators"}))
    other = _decision(cockpit, await _ask(cockpit, "Second?", **{"for": "operators"}))
    check = decisions.require_assigned
    resolve = decision_routing.resolve_for

    async def answered_meanwhile(deps: Any, user_id: Any, decision: Any) -> None:
        await check(deps, user_id, decision)
        cockpit.store.answer_decision(decision.id, option_id=None, text="done", by=OWEN)

    def resolve_after_answer(deps: Any, factory_id: str, wanted: str) -> Any:
        cockpit.store.answer_decision(other.id, option_id=None, text="done", by=OWEN)
        return resolve(deps, factory_id, wanted)

    monkeypatch.setattr(decisions, "require_assigned", answered_meanwhile)
    monkeypatch.setattr(decision_routing, "resolve_for", resolve_after_answer)
    claim = await cockpit.post(f"/v1/factory/decisions/{first.id}/claim", {}, user=MIA)
    assign = await cockpit.post(f"/v1/factory/decisions/{other.id}/assign", {"to": OPAL}, user=MIA)

    assert (claim.status_code, error_code(claim)) == (409, "decision_not_open")
    assert (assign.status_code, error_code(assign)) == (409, "decision_not_open")
    assert cockpit.events("decision.claimed") == []
    assert cockpit.events("decision.assigned") == []
    assert cockpit.store.get_decision(other.id).assignee == "operators"  # type: ignore[union-attr]


async def test_the_second_of_two_racing_claims_gets_409(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.server.factory import decisions

    row = _decision(cockpit, await _ask(cockpit, **{"for": "operators"}))
    check = decisions.require_assigned

    async def claimed_meanwhile(deps: Any, user_id: Any, decision: Any) -> None:
        await check(deps, user_id, decision)
        cockpit.store.route_decision(decision.id, claimed_by=MIA, claimed_at=1)

    monkeypatch.setattr(decisions, "require_assigned", claimed_meanwhile)

    resp = await cockpit.post(f"/v1/factory/decisions/{row.id}/claim", {}, user=OPAL)

    assert (resp.status_code, error_code(resp)) == (409, "already_claimed")
    assert resp.json()["claimed_by"] == MIA
    assert cockpit.store.get_decision(row.id).claimed_by == MIA  # type: ignore[union-attr]
    assert cockpit.events("decision.claimed") == []


async def test_assigned_me_counts_mine_and_my_roles(cockpit: Cockpit) -> None:
    await _ask(cockpit, "Mia's")
    await _ask(cockpit, "Ops'", **{"for": "operators"})
    await _ask(cockpit, "Owners'", **{"for": "owners"})

    async def count(user: str) -> int:
        resp = await cockpit.client.get(
            "/v1/factory/decisions?assigned=me&count_only=1", headers={"X-Test-User": user}
        )
        assert resp.status_code == 200, resp.text
        return int(resp.json()["count"])

    assert await count(MIA) == 2
    assert await count(OPAL) == 1
    assert await count(OWEN) == 2
    assert await count(VIC) == 0
    listed = await cockpit.client.get("/v1/factory/decisions", headers={"X-Test-User": OPAL})
    mine = {i["decision"]["question"]: i["mine"] for i in listed.json()["data"]}
    assert mine == {"Mia's": False, "Ops'": True, "Owners'": False}
    bad = await cockpit.client.get(
        "/v1/factory/decisions?assigned=them", headers={"X-Test-User": OPAL}
    )
    assert bad.status_code == 400


# ── Escalation (S10) ───────────────────────────────────────────


async def test_an_absent_assignee_widens_to_operators_then_owners(cockpit: Cockpit) -> None:
    row = _decision(cockpit, await _ask(cockpit))
    state = cockpit.app.state
    t0 = float(row.created_at)

    assert await decision_routing.escalate_decisions(state, t0 + 29 * 60) == 0
    assert await decision_routing.escalate_decisions(state, t0 + 30 * 60) == 1
    widened = cockpit.store.get_decision(row.id)
    assert widened is not None and widened.assignee == "operators"
    assert await decision_routing.escalate_decisions(state, t0 + 119 * 60) == 0
    assert await decision_routing.escalate_decisions(state, t0 + 120 * 60) == 1
    owners = cockpit.store.get_decision(row.id)
    assert owners is not None and owners.assignee == "owners"
    assert await decision_routing.escalate_decisions(state, t0 + 500 * 60) == 0
    steps = [(e.data["from"], e.data["to"]) for e in cockpit.events("decision.escalated")]
    assert steps == [(MIA, "operators"), ("operators", "owners")]


async def test_escalation_skips_a_decision_answered_or_moved_since_the_plan(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    answered = _decision(cockpit, await _ask(cockpit, "Answered?"))
    moved = _decision(cockpit, await _ask(cockpit, "Moved?"))
    now = float(answered.created_at) + 31 * 60
    plan = decision_routing.plan_escalations

    def stale_plan(deps: Any, at: float) -> Any:
        steps = plan(deps, at)
        cockpit.store.answer_decision(answered.id, option_id=None, text="done", by=MIA)
        cockpit.store.route_decision(moved.id, assignee=OWEN)
        return steps

    monkeypatch.setattr(decision_routing, "plan_escalations", stale_plan)

    assert await decision_routing.escalate_decisions(cockpit.app.state, now) == 0
    assert cockpit.store.get_decision(answered.id).assignee == MIA  # type: ignore[union-attr]
    assert cockpit.store.get_decision(moved.id).assignee == OWEN  # type: ignore[union-attr]
    assert cockpit.events("decision.escalated") == []


async def test_a_present_assignee_keeps_the_decision(cockpit: Cockpit) -> None:
    row = _decision(cockpit, await _ask(cockpit))
    factory_presence.registry.heartbeat(cockpit.fid, MIA, "overview", False)

    assert await decision_routing.escalate_decisions(cockpit.app.state, row.created_at + 3600) == 0
    kept = cockpit.store.get_decision(row.id)
    assert kept is not None and kept.assignee == MIA


async def test_non_blocking_decisions_and_custom_windows(cockpit: Cockpit) -> None:
    quiet = _decision(cockpit, await _ask(cockpit, "FYI?", blocking=False))
    loud = _decision(cockpit, await _ask(cockpit, "Now?"))
    cockpit.store.put_definition(
        cockpit.fid,
        doc={"decisions": {"escalate_after_m": 5, "owners_after_m": 10}},
        created_by="alice",
        created_via="human",
    )

    done = await decision_routing.escalate_decisions(cockpit.app.state, loud.created_at + 5 * 60)

    assert done == 1
    assert cockpit.store.get_decision(quiet.id).assignee == MIA  # type: ignore[union-attr]
    assert cockpit.store.get_decision(loud.id).assignee == "operators"  # type: ignore[union-attr]


async def test_the_supervisor_tick_runs_the_escalation(
    supervision: Supervision, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[float] = []

    async def record(_state: Any, now: float) -> int:
        seen.append(now)
        return 0

    monkeypatch.setattr(decision_routing, "escalate_decisions", record)

    await supervision.tick()

    assert seen == [supervision.clock.now]
