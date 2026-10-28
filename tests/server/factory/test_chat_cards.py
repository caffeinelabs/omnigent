"""Factory events mirrored into the Room as cards (system rows, never routed)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.server.factory import chat_cards, events
from tests.server.factory.chat_helpers import RoomEnv, room_env
from tests.server.factory.conftest import FactoryWorld

REPO = "caffeinelabs/app"


@pytest_asyncio.fixture()
async def env(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[RoomEnv]:
    async for value in room_env(world, db_uri, monkeypatch):
        yield value


async def _emit(env: RoomEnv, kind: str, **kwargs: Any) -> Any:
    kwargs.setdefault("summary", f"{kind} happened")
    return await events.emit(env.cockpit.app.state, env.cockpit.fid, kind, **kwargs)


def _cards(env: RoomEnv) -> list[tuple[str, str | None, str | None, str]]:
    return [
        (m.thread, (m.card or {}).get("type"), (m.card or {}).get("ref"), m.body)
        for m in env.messages()
        if m.author_kind == "system"
    ]


def _pr(env: RoomEnv, number: int = 9630, project: str | None = "api-version") -> Any:
    pr, _ = env.cockpit.store.upsert_pr(
        env.cockpit.fid,
        REPO,
        number,
        url=f"https://github.com/{REPO}/pull/{number}",
        project_slug=project,
    )
    return pr


def test_every_listed_event_family_maps_to_a_card_type() -> None:
    assert {
        "decision.opened",
        "decision.answered",
        "decision.escalated",
    } == chat_cards.DECISION_KINDS
    assert {"pr.ready", "pr.merged", "pr.closed", "pr.discovered", "pr.human_review"} == set(
        chat_cards.PR_KINDS
    )
    assert chat_cards.mirror_event in events._listeners


async def test_decisions_land_in_their_thread_else_their_project(env: RoomEnv) -> None:
    store = env.cockpit.store
    in_thread, _ = store.create_decision(
        env.cockpit.fid, dedupe_key="d1", question="Which table?", thread="project:api-version"
    )
    in_project, _ = store.create_decision(
        env.cockpit.fid, dedupe_key="d2", question="Ship?", project_slug="api-version"
    )
    loose, _ = store.create_decision(env.cockpit.fid, dedupe_key="d3", question="Budget?")
    for decision, kind in ((in_thread, "decision.opened"), (in_project, "decision.answered")):
        await _emit(env, kind, data={"decision_id": decision.id})
    await _emit(env, "decision.escalated", data={"decision_id": loose.id})

    assert _cards(env) == [
        ("project:api-version", "decision", f"D-{in_thread.number}", "decision.opened happened"),
        (
            "project:api-version",
            "decision",
            f"D-{in_project.number}",
            "decision.answered happened",
        ),
        ("factory", "decision", f"D-{loose.number}", "decision.escalated happened"),
    ]


async def test_a_proposal_shows_as_its_own_card_not_its_decision(env: RoomEnv) -> None:
    store = env.cockpit.store
    projects, _ = store.create_proposal(
        env.cockpit.fid, kind="projects", title="3 projects", author_kind="agent", author="x"
    )
    tuning, _ = store.create_proposal(
        env.cockpit.fid, kind="tuning", title="slower beats", author_kind="agent", author="x"
    )
    threaded, _ = store.create_proposal(
        env.cockpit.fid,
        kind="projects",
        title="split",
        author_kind="agent",
        author="x",
        thread="project:api-version",
    )
    mirror, _ = store.create_decision(
        env.cockpit.fid,
        dedupe_key="m",
        question="Apply C-1?",
        source="proposal",
        subject="proposal:C-1",
    )
    await _emit(env, "proposal.opened", data={"proposal_id": projects.id})
    await _emit(env, "proposal.applied", subject="proposal:C-2")
    await _emit(env, "proposal.opened", data={"proposal_id": threaded.id})
    await _emit(env, "decision.opened", data={"decision_id": mirror.id})

    assert [(t, kind, ref) for t, kind, ref, _ in _cards(env)] == [
        ("factory", "proposal", f"C-{projects.number}"),
        ("improvements", "proposal", f"C-{tuning.number}"),
        ("project:api-version", "proposal", f"C-{threaded.number}"),
    ]


async def test_controls_are_receipts_in_their_project_or_the_factory(env: RoomEnv) -> None:
    await _emit(
        env, "control.pause", project="api-version", summary="Pause api-version · by alice"
    )
    await _emit(env, "control.ack", summary="pause acknowledged")

    assert [(t, kind) for t, kind, _, _ in _cards(env)] == [
        ("project:api-version", "control"),
        ("factory", "control"),
    ]


async def test_pr_events_land_in_the_pr_thread_with_a_project_line(env: RoomEnv) -> None:
    pr = _pr(env)
    loose = _pr(env, 9700, project=None)
    await _emit(env, "pr.ready", data={"pr_id": pr.id}, summary="PR #9630 ready for human merge")
    await _emit(env, "pr.human_review", subject=f"pr:{REPO}#9630", summary="changes requested")
    await _emit(env, "pr.discovered", data={"pr_id": loose.id})
    await _emit(env, "pr.not_ready", data={"pr_id": pr.id})

    pr_thread = f"pr:{pr.id}"
    assert _cards(env) == [
        (pr_thread, "pr", "!9630", "PR #9630 ready for human merge"),
        ("project:api-version", "system", "!9630", "PR #9630 ready for human merge"),
        (pr_thread, "pr", "!9630", "changes requested"),
        ("project:api-version", "system", "!9630", "changes requested"),
        (f"pr:{loose.id}", "pr", "!9700", "pr.discovered happened"),
    ]


async def test_a_demo_card_lands_in_the_pr_thread(env: RoomEnv) -> None:
    pr = _pr(env)
    await _emit(env, "demo.published", data={"pr_id": pr.id, "demo_id": "d" * 32})

    [message] = [m for m in env.messages() if m.author_kind == "system"]
    assert message.thread == f"pr:{pr.id}"
    assert message.card == {"type": "demo", "ref": "!9630", "id": "d" * 32}


async def test_loops_and_issues(env: RoomEnv) -> None:
    await _emit(
        env,
        "loop.exhausted",
        project="api-version",
        data={"loop_id": "e" * 32, "ref": "T-4"},
        summary="implement T-4 exhausted 3/3",
    )
    await _emit(env, "loop.converged", project="api-version")
    await _emit(
        env,
        "issue.opened",
        project="api-version",
        data={"issue_id": "f" * 32, "ref": "INC-2", "class": "incident", "severity": "warning"},
    )
    await _emit(
        env, "issue.opened", data={"ref": "INC-3", "class": "incident", "severity": "notice"}
    )
    await _emit(env, "issue.opened", data={"ref": "F-1", "class": "finding", "severity": "info"})

    assert [(t, kind, ref) for t, kind, ref, _ in _cards(env)] == [
        ("project:api-version", "loop", "T-4"),
        ("project:api-version", "issue", "INC-2"),
        ("improvements", "issue", "F-1"),
    ]


async def test_factory_lines_and_project_lifecycle(env: RoomEnv) -> None:
    await _emit(env, "people.invited", summary="mia joined as operator")
    await _emit(env, "people.drift_repaired")
    await _emit(env, "base.moved", summary="temp-main moved to abc1234")
    await _emit(env, "member.spawned", project="docs", summary="PL · docs spawned")
    await _emit(env, "project.held", project="docs", summary="docs waits on api-version")
    await _emit(env, "chat.posted")

    assert [(t, kind, body) for t, kind, _, body in _cards(env)] == [
        ("factory", "system", "mia joined as operator"),
        ("factory", "system", "temp-main moved to abc1234"),
        ("factory", "system", "PL · docs spawned"),
        ("project:docs", "system", "PL · docs spawned"),
        ("project:docs", "system", "docs waits on api-version"),
    ]


async def test_a_repeated_event_writes_one_card_and_cards_never_route(env: RoomEnv) -> None:
    event = await _emit(env, "base.moved", summary="moved")
    assert event is not None
    await chat_cards.mirror_event(env.cockpit.app.state, event)
    await env.settle()

    [card] = [m for m in env.messages() if m.author_kind == "system"]
    assert card.routed == []
    assert env.deliveries.calls == []
    assert any("chat" in scopes for _, scopes in env.cockpit.announces)
