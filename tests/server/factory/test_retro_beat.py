"""The learning tick: Sentinel's retro beat (S12) and eval mode.

The retro beat goes to Sentinel once a day after ``OMNIGENT_FACTORY_RETRO_HOUR``
(never on the factory's first day, and not while the last 24 h gave it nothing
to read) and once per project done; a ``retro.requested`` event makes it idempotent across a
server restart. In eval
mode the server answers decisions after 2 minutes as ``eval-auto`` and pauses a
project's PL once it reaches READY.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.entities.factory import EventDraft
from omnigent.server.factory import definition, eval_mode, issues, learning, retro
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld, Supervision
from tests.server.factory.projects_helpers import REPO, header_cockpit

D = 86400


@pytest.fixture(autouse=True)
def _fresh_memo() -> Iterator[None]:
    retro.reset()
    learning.reset()
    yield
    retro.reset()
    learning.reset()


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in header_cockpit(world, db_uri, monkeypatch):
        restore = definition.install(value.app.state)
        try:
            yield value
        finally:
            restore()


def _hour_of(ts: float) -> int:
    return datetime.fromtimestamp(ts, tz=UTC).hour


def _merged_pr(cockpit: Cockpit, at: float, number: int = 9700) -> None:
    """Some work the daily retro can look back on."""
    cockpit.store.upsert_pr(
        cockpit.fid, REPO, number, state="merged", project_slug="api-version", merged_at=int(at)
    )


def _retro_texts(cockpit: Cockpit) -> list[str]:
    return [
        t
        for t in cockpit.mailer.texts_to(cockpit.world.id("lead_b"))
        if t.startswith("[Factory heartbeat · retro")
    ]


async def test_the_daily_retro_goes_to_sentinel_once_even_across_a_restart(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    later = time.time() + 2 * D
    monkeypatch.setenv(retro.RETRO_HOUR_ENV, str(_hour_of(later)))
    _merged_pr(cockpit, later - 3600)

    sent = await retro.tick(cockpit.app.state, cockpit.fid, later)
    again = await retro.tick(cockpit.app.state, cockpit.fid, later + 60)
    retro.reset()  # a new server process: only the event log remembers
    after_restart = await retro.tick(cockpit.app.state, cockpit.fid, later + 120)

    day = datetime.fromtimestamp(later, tz=UTC).strftime("%Y-%m-%d")
    assert sent == [f"retro:{day}"] and again == [] and after_restart == []
    assert _retro_texts(cockpit) == [
        "[Factory heartbeat · retro · window 24h · budget $3] "
        'Run load_skill("factory-retro") and follow it: read sys_factory_insights, code the '
        "failures, file at most 2 proposals with evidence, post the digest in #improvements."
    ]
    [event] = cockpit.events(retro.REQUESTED_KIND)
    assert event.to_id == cockpit.world.id("lead_b") and event.data["budget_usd"] == 3.0


async def test_an_idle_day_holds_the_daily_retro_until_work_shows_up(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    later = time.time() + 2 * D
    if _hour_of(later) == 23:
        later -= 3 * 3600
    monkeypatch.setenv(retro.RETRO_HOUR_ENV, str(_hour_of(later)))
    _merged_pr(cockpit, later - 2 * D)  # two days ago: not in the last 24 h

    idle = await retro.tick(cockpit.app.state, cockpit.fid, later)
    still_idle = await retro.tick(cockpit.app.state, cockpit.fid, later + 60)
    _merged_pr(cockpit, later + 120, number=9701)
    within_the_hour = await retro.tick(cockpit.app.state, cockpit.fid, later + 600)
    rechecked = await retro.tick(cockpit.app.state, cockpit.fid, later + retro.IDLE_RECHECK_S)

    day = datetime.fromtimestamp(later, tz=UTC).strftime("%Y-%m-%d")
    assert idle == still_idle == within_the_hour == []
    assert rechecked == [f"retro:{day}"]
    assert len(_retro_texts(cockpit)) == 1
    [skipped] = cockpit.events(retro.SKIPPED_KIND)
    assert skipped.subject == f"retro:{day}" and skipped.severity == "info"
    assert skipped.summary == "No retro yet today: nothing in the last 24 h to learn from"
    assert skipped.data["signals"] == {
        "issues": 0,
        "prs_closed": 0,
        "decisions": 0,
        "loops_exhausted": 0,
        "feedback": 0,
        "human_posts": 0,
    }


async def test_a_finished_sub_agent_run_is_enough_for_a_retro(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    later = time.time() + 2 * D
    monkeypatch.setenv(retro.RETRO_HOUR_ENV, str(_hour_of(later)))
    cockpit.store.append_event(
        EventDraft(
            factory_id=cockpit.fid,
            kind="task.failed",
            summary="ic:task-1 failed",
            ts=int((later - 3600) * 1000),
        )
    )

    sent = await retro.tick(cockpit.app.state, cockpit.fid, later)

    assert len(sent) == 1 and cockpit.events(retro.SKIPPED_KIND) == []


# ── what a retro reads besides finished work ─────────────────


async def _quiet_today(cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch) -> float:
    """
    A factory old enough for a retro, with no work in the last 24 h.

    :returns: "Now": the daily retro is due and, as nothing happened yet, held.
    """
    monkeypatch.setattr(retro, "FIRST_RETRO_AFTER_S", 0)
    monkeypatch.setenv(retro.RETRO_HOUR_ENV, "0")
    now = time.time() + 60
    assert await retro.tick(cockpit.app.state, cockpit.fid, now) == []
    retro.reset()  # look again at once instead of within the hour
    return now


async def test_an_incident_alone_is_enough_for_a_retro(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = await _quiet_today(cockpit, monkeypatch)
    await issues.raise_issue(
        cockpit.app.state, cockpit.fid, rule="member.dead", target="member:x", summary="PL died"
    )

    assert len(await retro.tick(cockpit.app.state, cockpit.fid, now)) == 1


async def test_human_feedback_alone_is_enough_for_a_retro(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = await _quiet_today(cockpit, monkeypatch)
    cockpit.store.add_feedback(
        cockpit.fid,
        target_kind="issue",
        target_id="F-1",
        author_kind="human",
        author="luka@caffeine.ai",
        kind="calibration",
        tags=["wrong_label"],
    )

    assert len(await retro.tick(cockpit.app.state, cockpit.fid, now)) == 1


@pytest.mark.parametrize(
    ("kind", "by_kind"),
    [
        ("pr.closed", None),
        ("decision.answered", "human"),
        ("decision.escalated", None),
        ("loop.exhausted", None),
        ("chat.posted", "human"),
    ],
)
async def test_a_failure_a_decision_or_a_human_post_is_enough_for_a_retro(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch, kind: str, by_kind: str | None
) -> None:
    now = await _quiet_today(cockpit, monkeypatch)
    cockpit.store.append_event(
        EventDraft(
            factory_id=cockpit.fid,
            kind=kind,
            summary=kind,
            ts=int((now - 3600) * 1000),
            data={"by_kind": by_kind} if by_kind else None,
        )
    )

    assert len(await retro.tick(cockpit.app.state, cockpit.fid, now)) == 1


async def test_sentinels_own_findings_and_agent_chatter_do_not_call_for_a_retro(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = await _quiet_today(cockpit, monkeypatch)
    cockpit.store.upsert_issue(
        cockpit.fid,
        class_="finding",
        rule="finding.coordination",
        target="factory",
        summary="from yesterday's retro",
        source="sentinel",
    )
    cockpit.store.append_event(
        EventDraft(
            factory_id=cockpit.fid,
            kind="chat.posted",
            summary="Atlas: nothing new",
            ts=int((now - 3600) * 1000),
            data={"by_kind": "agent"},
        )
    )

    assert await retro.tick(cockpit.app.state, cockpit.fid, now) == []


async def test_no_retro_before_the_hour_or_on_the_first_day(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    later = time.time() + 2 * D
    hour = _hour_of(later)
    if hour == 23:
        later -= 3600
        hour = 22
    monkeypatch.setenv(retro.RETRO_HOUR_ENV, str(hour + 1))
    assert await retro.tick(cockpit.app.state, cockpit.fid, later) == []
    monkeypatch.setenv(retro.RETRO_HOUR_ENV, "0")
    assert await retro.tick(cockpit.app.state, cockpit.fid, time.time()) == []
    assert _retro_texts(cockpit) == []


async def test_a_project_done_gets_its_own_retro(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(retro.RETRO_HOUR_ENV, "23")
    now = time.time()
    if _hour_of(now) == 23:
        now -= 3600
    cockpit.store.upsert_project(cockpit.fid, "api-version", stage=F.STAGE_DONE)

    first = await retro.tick(cockpit.app.state, cockpit.fid, now)
    second = await retro.tick(cockpit.app.state, cockpit.fid, now + 60)

    assert first == ["retro:project:api-version"] and second == []
    [text] = _retro_texts(cockpit)
    assert text.startswith("[Factory heartbeat · retro · project api-version done · window 24h")


async def test_owners_can_run_the_retro_now(cockpit: Cockpit) -> None:
    resp = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/retro", {})
    carol = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/retro", {}, user="carol")

    assert resp.status_code == 200 and resp.json()["sent"] is True
    assert carol.status_code == 403
    assert len(_retro_texts(cockpit)) == 1


async def test_the_supervisor_tick_runs_the_retro_beat(
    supervision: Supervision, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = supervision.clock
    clock.advance(2 * D)
    monkeypatch.setenv(retro.RETRO_HOUR_ENV, str(_hour_of(clock.now)))
    supervision.world.deps.factory_store.append_event(
        EventDraft(
            factory_id=supervision.world.id("lead_a"),
            kind="task.done",
            summary="ic:task-1 finished",
            ts=int((clock.now - 3600) * 1000),
        )
    )

    await supervision.tick()
    await supervision.tick()

    lead_b = supervision.world.id("lead_b")
    retros = [t for t in supervision.deliver.texts_to(lead_b) if "heartbeat · retro" in t]
    assert len(retros) == 1


# ── Eval mode ──────────────────────────────────────────────────


async def test_eval_mode_answers_decisions_after_two_minutes(cockpit: Cockpit) -> None:
    store = cockpit.store
    decision, _ = store.create_decision(
        cockpit.fid,
        dedupe_key="ask-1",
        question="Which table name?",
        source="agent",
        asked_by_session_id=cockpit.world.id("pl"),
        options=[
            {"id": "a", "label": "orders"},
            {"id": "b", "label": "purchases", "recommended": True},
        ],
    )
    now = time.time()

    normal = await eval_mode.tick(cockpit.app.state, cockpit.fid, now + 300)
    cockpit.world.store.set_labels(cockpit.fid, {F.LABEL_MODE: F.MODE_EVAL})
    early = await eval_mode.tick(cockpit.app.state, cockpit.fid, now + 60)
    due = await eval_mode.tick(cockpit.app.state, cockpit.fid, now + eval_mode.ANSWER_AFTER_S + 1)

    assert normal == {"answered": [], "stopped": []}
    assert early["answered"] == []
    assert due["answered"] == [decision.ref]
    answered = store.get_decision(decision.id)
    assert answered is not None
    assert (answered.status, answered.answer_option_id, answered.answered_by) == (
        "answered",
        "b",
        "eval-auto",
    )
    pl_texts = cockpit.mailer.texts_to(cockpit.world.id("pl"))
    assert any("purchases" in t and "eval-auto" in t for t in pl_texts)


async def test_eval_mode_leaves_owner_decisions_to_people(cockpit: Cockpit) -> None:
    store, fid = cockpit.store, cockpit.fid
    filed = await cockpit.client.post(
        "/v1/factory/agent/proposals",
        json={
            "from_session_id": cockpit.world.id("lead_b"),
            "kind": "tuning",
            "changes": [{"op": "replace", "path": "budgets.project_usd", "value": 900}],
            "rationale": "projects run out of budget",
            "evidence": ["F-1"],
            "expected_effect": {"metric": "cost_usd", "direction": "up"},
        },
        headers={"X-Test-User": "alice"},
    )
    assert filed.status_code == 200 and filed.json()["class"] == "B", filed.text
    access, _ = store.create_decision(
        fid,
        dedupe_key="access:bob",
        question="bob asks for the owner role",
        source="access_request",
        options=[
            {
                "id": "grant",
                "label": "Grant owner",
                "action": "grant_role",
                "target": "person:bob",
                "role": "owner",
                "recommended": True,
            },
            {"id": "decline", "label": "Decline", "action": F.ACTION_DISMISS},
        ],
        assignee="owners",
    )
    budget, _ = store.create_decision(
        fid,
        dedupe_key="loop:raise",
        question="The review loop is out of rounds",
        source="loop",
        options=[
            {
                "id": "raise",
                "label": "Raise to 6 rounds",
                "action": F.ACTION_RAISE_BUDGET,
                "target": "loop:x",
                "owner_only": True,
                "recommended": True,
            },
            {"id": "abandon", "label": "Abandon", "action": F.ACTION_DISMISS},
        ],
        assignee="owners",
    )
    asked, _ = store.create_decision(
        fid,
        dedupe_key="ask-2",
        question="Which table name?",
        source="agent",
        asked_by_session_id=cockpit.world.id("pl"),
        options=[{"id": "a", "label": "orders", "recommended": True}],
    )
    cockpit.world.store.set_labels(fid, {F.LABEL_MODE: F.MODE_EVAL})
    usd = definition.value(cockpit.deps, fid, "budgets.project_usd")

    due = await eval_mode.tick(cockpit.app.state, fid, time.time() + eval_mode.ANSWER_AFTER_S + 1)

    assert due["answered"] == [asked.ref]
    for decision in (access, budget):
        assert store.get_decision(decision.id).status == "open"  # type: ignore[union-attr]
    proposal = store.get_proposal_by_number(fid, 1)
    assert proposal is not None and proposal.status == "pending"
    assert store.get_decision(proposal.decision_id).status == "open"  # type: ignore[arg-type,union-attr]
    assert definition.value(cockpit.deps, fid, "budgets.project_usd") == usd
    assert store.get_person(fid, "bob") is None


async def test_eval_mode_stops_a_project_at_ready(cockpit: Cockpit) -> None:
    cockpit.world.store.set_labels(cockpit.fid, {F.LABEL_MODE: F.MODE_EVAL})
    cockpit.store.upsert_project(cockpit.fid, "api-version", stage=F.STAGE_READY)

    first = await eval_mode.tick(cockpit.app.state, cockpit.fid, time.time())
    second = await eval_mode.tick(cockpit.app.state, cockpit.fid, time.time())

    assert first["stopped"] == ["api-version"] and second["stopped"] == []
    assert cockpit.world.get("pl").labels[F.LABEL_STATE] == F.STATE_PAUSED
    [event] = cockpit.events(eval_mode.STOPPED_KIND)
    assert event.project_slug == "api-version"
