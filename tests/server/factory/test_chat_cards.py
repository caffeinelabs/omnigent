"""Factory events mirrored into the Room as cards (system rows, never routed)."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.entities.factory import FactoryEvent
from omnigent.server.factory import chat, chat_cards, events, issues, schemas
from omnigent.stores.factory_store import new_event_id
from tests.server.factory.chat_helpers import RoomEnv, room_env
from tests.server.factory.conftest import FactoryWorld

REPO = "sample-org/sample-app"


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
    assert {
        "pr.ready",
        "pr.auto_merged",
        "pr.merged",
        "pr.closed",
        "pr.discovered",
        "pr.human_review",
    } == set(chat_cards.PR_KINDS)
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
    await _emit(env, "base.moved", summary="develop moved to abc1234")
    await _emit(env, "member.spawned", project="docs", summary="PL · docs spawned")
    await _emit(env, "project.held", project="docs", summary="docs waits on api-version")
    await _emit(env, "chat.posted")

    assert [(t, kind, body) for t, kind, _, body in _cards(env)] == [
        ("factory", "system", "mia joined as operator"),
        ("factory", "system", "develop moved to abc1234"),
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


def test_only_card_kinds_reach_the_store() -> None:
    from omnigent.server.factory.chat_cards import shows

    for kind in (
        "decision.opened",
        "proposal.applied",
        "pr.ready",
        "issue.opened",
        "people.joined",
    ):
        assert shows(kind), kind
    for kind in ("chat.posted", "message.delivered", "heartbeat.sent", "people.drift_repaired"):
        assert not shows(kind), kind


# ── Live incident cards ────────────────────────────────────────

SPEND = "the factory spends ${:.2f}/h (alert above $15/h)"


async def _spend(env: RoomEnv, rate: float, severity: str = "warning") -> Any:
    issue = await issues.raise_issue(
        env.cockpit.app.state,
        env.cockpit.fid,
        rule="spend.rate",
        target="factory",
        summary=SPEND.format(rate),
        severity=severity,
        assignee=issues.ASSIGN_OWNERS,
    )
    assert issue is not None
    return issue


def _issue_cards(env: RoomEnv, thread: str = "factory") -> list[Any]:
    return [m for m in env.messages(thread) if (m.card or {}).get("type") == "issue"]


async def test_a_flapping_incident_keeps_one_live_card(env: RoomEnv) -> None:
    state, fid = env.cockpit.app.state, env.cockpit.fid
    first = await _spend(env, 18.33)
    for rate in (16.10, 19.75, 21.40):
        await issues.clear_issue(state, fid, "spend.rate", "factory", reason="back under")
        await _spend(env, rate)
    await _spend(env, 22.05)  # a plain re-raise: no event, the card reads it live

    [card] = _issue_cards(env)
    assert card.card["ref"] == first.ref and card.card["id"] == first.id
    assert card.body == f"{first.ref} {SPEND.format(21.40)}"
    meta = chat_cards.card_meta(card)
    assert meta["reopened"] == 3 and meta["severity"] == "warning"
    assert isinstance(meta["reopened_at"], int)
    # Every comeback is still an event (Slack, the digest and Activity read those).
    assert len(env.cockpit.events("issue.opened")) == 4
    assert env.cockpit.store.get_issue(first.id).count == 5
    [view] = chat.message_views(env.cockpit.deps, [card])
    assert view["card"]["meta"] == {
        "kind": "issue",
        "severity": "warning",
        "reopened": 3,
        "reopened_at": meta["reopened_at"],
    }
    schemas.ChatMessageView.model_validate(view)


async def test_an_incident_posts_again_when_its_severity_rises(env: RoomEnv) -> None:
    await _spend(env, 18.0)
    await _spend(env, 40.0, severity="critical")
    await _spend(env, 45.0, severity="critical")  # already shown at critical
    await _spend(env, 19.0, severity="warning")  # a drop changes no card

    first, again = _issue_cards(env)
    assert chat_cards.card_meta(first)["severity"] == "warning"
    assert chat_cards.card_meta(first)["posts"] == 1
    assert chat_cards.card_meta(again)["severity"] == "critical"
    assert again.body.endswith("is now critical: " + SPEND.format(40.0))
    [escalated] = env.cockpit.events(issues.ESCALATED)
    assert escalated.data["was_severity"] == "warning"
    assert escalated.data["severity"] == "critical"


async def test_an_incident_posts_again_after_a_person_resolved_it(env: RoomEnv) -> None:
    issue = await _spend(env, 18.0)
    env.cockpit.store.update_issue(
        issue.id, status="resolved", resolved_at=int(time.time()), resolved_by="alice"
    )
    await _spend(env, 19.0)

    _, again = _issue_cards(env)
    assert chat_cards.card_meta(again)["reopened"] == 0
    assert again.id == chat_cards.issue_card_id(issue.id, "factory", 1)
    [_, reopened] = env.cockpit.events("issue.opened")
    assert reopened.data["was_resolved_by"] == "alice"


async def test_an_incident_posts_again_after_staying_clear_an_hour(env: RoomEnv) -> None:
    state, fid = env.cockpit.app.state, env.cockpit.fid
    issue = await _spend(env, 18.0)
    await issues.clear_issue(state, fid, "spend.rate", "factory")
    env.cockpit.store.update_issue(
        issue.id, resolved_at=int(time.time()) - chat_cards.REPOST_AFTER_S - 5
    )
    await _spend(env, 19.0)
    await issues.clear_issue(state, fid, "spend.rate", "factory")
    await _spend(env, 20.0)  # flaps right back: the new card counts it

    first, again = _issue_cards(env)
    assert chat_cards.card_meta(first)["posts"] == 1
    assert chat_cards.card_meta(again)["reopened"] == 1


async def test_every_report_of_a_person_shows(env: RoomEnv) -> None:
    data = {"issue_id": "f" * 32, "ref": "INC-5", "class": "incident", "severity": "warning"}
    await _emit(env, "issue.opened", data={**data, "by_kind": "human"}, summary="mia reported")
    await _emit(env, "issue.opened", data={**data, "by_kind": "human"}, summary="leo reported")

    assert [m.body for m in _issue_cards(env)] == ["mia reported", "leo reported"]


async def test_a_repeated_incident_event_changes_nothing(env: RoomEnv) -> None:
    state, fid = env.cockpit.app.state, env.cockpit.fid
    await _spend(env, 18.0)
    await issues.clear_issue(state, fid, "spend.rate", "factory")
    await _spend(env, 19.0)
    for event in env.cockpit.events("issue.opened"):
        await chat_cards.mirror_event(state, event)

    [card] = _issue_cards(env)
    assert chat_cards.card_meta(card)["reopened"] == 1
    assert len(chat_cards.card_meta(card)["events"]) == 2


async def test_an_updated_card_reads_on_one_line_like_a_posted_one(env: RoomEnv) -> None:
    data = {"issue_id": "e" * 32, "ref": "INC-6", "class": "incident", "severity": "warning"}
    await _emit(env, "issue.opened", data=data, summary="INC-6 line one\nline two")
    [card] = _issue_cards(env)
    assert card.body == "INC-6 line one line two"
    comeback = {**data, "reopened": True, "was_resolved_by": "server"}
    comeback["was_resolved_at"] = int(time.time())
    await _emit(env, "issue.opened", data=comeback, summary="INC-6 back\n  again\tnow")

    [card] = _issue_cards(env)
    assert card.body == "INC-6 back again now"
    assert chat_cards.card_meta(card)["reopened"] == 1


async def test_a_late_copy_of_an_older_event_leaves_the_live_card_alone(env: RoomEnv) -> None:
    state = env.cockpit.app.state
    await _spend(env, 18.0)
    await _spend(env, 40.0, severity="critical")
    first, live = _issue_cards(env)
    [opened] = env.cockpit.events("issue.opened")
    [escalated] = env.cockpit.events(issues.ESCALATED)

    # The first card's event, delivered again after the repost.
    await chat_cards.mirror_event(state, opened)
    # An event older than the live card's newest that neither card recorded.
    stale = FactoryEvent(
        id=new_event_id(escalated.ts - 1),
        factory_id=env.cockpit.fid,
        ts=escalated.ts - 1,
        kind="issue.opened",
        summary="INC-1 an old copy",
        severity="warning",
        data={**opened.data, "reopened": True, "was_resolved_by": "server"},
    )
    await chat_cards.mirror_event(state, stale)

    assert [(m.id, m.body) for m in _issue_cards(env)] == [
        (first.id, first.body),
        (live.id, live.body),
    ]
    assert chat_cards.card_meta(_issue_cards(env)[1]) == chat_cards.card_meta(live)


# ── Batched base lines ─────────────────────────────────────────

HEAD = [f"{i:x}" * 40 for i in range(16)]


def _move(
    env: RoomEnv,
    i: int,
    at_s: int,
    *,
    base: str = "main",
    pr: int | None = None,
    repo: str = REPO,
    repos: int | None = None,
) -> Any:
    data = {"repo": repo, "base": base, "from": HEAD[i], "to": HEAD[i + 1], "pr": pr}
    if repos is not None:
        data["repos_watched"] = repos
    via = f" (#{pr})" if pr else ""
    return FactoryEvent(
        id=new_event_id(at_s * 1000),
        factory_id=env.cockpit.fid,
        ts=at_s * 1000,
        kind="base.moved",
        summary=f"base {base} moved {HEAD[i][:7]} → {HEAD[i + 1][:7]}{via}",
        severity="notice",
        subject=f"base:{repo}#{base}",
        data=data,
    )


def _base_lines(env: RoomEnv) -> list[Any]:
    return [
        m
        for m in env.messages("factory")
        if chat_cards.card_meta(m).get("kind") == chat_cards.BASE_MOVED
    ]


async def test_base_moves_join_one_line_while_nothing_else_is_said(env: RoomEnv) -> None:
    state = env.cockpit.app.state
    t0 = 1_793_000_000  # 2026-10-26 07:33 UTC
    await chat_cards.mirror_event(state, _move(env, 0, t0, pr=9630))
    [line] = _base_lines(env)
    assert line.body == f"base main moved {HEAD[0][:7]} → {HEAD[1][:7]} (#9630)"

    for i in range(1, 7):
        await chat_cards.mirror_event(state, _move(env, i, t0 + i * 3600))  # hours apart

    [line] = _base_lines(env)
    assert line.body == f"base main moved 7 times since 07:33 UTC → {HEAD[7][:7]}"
    moves = chat_cards.card_meta(line)["moves"]
    assert [m["to_sha"] for m in moves] == HEAD[1:8]
    assert moves[0]["pr"] == 9630 and moves[0]["from_sha"] == HEAD[0]
    [view] = chat.message_views(env.cockpit.deps, [line])
    schemas.ChatMessageView.model_validate(view)
    assert view["card"]["meta"]["moves"][0] == {
        "at": t0,
        "from_sha": HEAD[0],
        "to_sha": HEAD[1],
        "pr": 9630,
    }
    assert view["card"]["meta"]["base"] == "main"


async def test_a_message_after_the_line_starts_a_new_one_ten_minutes_on(env: RoomEnv) -> None:
    state, store, fid = env.cockpit.app.state, env.cockpit.store, env.cockpit.fid
    t0 = 1_793_000_000
    await chat_cards.mirror_event(state, _move(env, 0, t0))
    store.append_chat(fid, thread="factory", author_kind="human", author="alice", body="hi")
    await chat_cards.mirror_event(state, _move(env, 1, t0 + 300))  # inside the window
    # Another base's line is no conversation: main's line still takes main's moves.
    await chat_cards.mirror_event(state, _move(env, 9, t0 + 400, base="develop"))
    await chat_cards.mirror_event(state, _move(env, 2, t0 + 500))
    await chat_cards.mirror_event(state, _move(env, 3, t0 + chat_cards.BASE_BATCH_S + 1))

    lines = _base_lines(env)
    assert [len(chat_cards.card_meta(m)["moves"]) for m in lines] == [3, 1, 1]
    assert [chat_cards.card_meta(m)["base"] for m in lines] == ["main", "develop", "main"]
    assert [m.body for m in env.messages("factory")][1] == "hi"


async def test_a_full_line_starts_another_and_a_replay_adds_nothing(
    env: RoomEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chat_cards, "BASE_BATCH_MAX", 3)
    state = env.cockpit.app.state
    t0 = 1_793_000_000
    moves = [_move(env, i, t0 + i) for i in range(4)]
    for event in moves:
        await chat_cards.mirror_event(state, event)
    for event in moves:
        await chat_cards.mirror_event(state, event)

    assert [len(chat_cards.card_meta(m)["moves"]) for m in _base_lines(env)] == [3, 1]


async def test_cards_between_moves_keep_one_line_people_and_agents_do_not(
    env: RoomEnv,
) -> None:
    state, store, fid = env.cockpit.app.state, env.cockpit.store, env.cockpit.fid
    t0 = 1_793_000_000
    await chat_cards.mirror_event(state, _move(env, 0, t0))
    await chat.post_system(state, fid, "factory", "INC-1 the factory spends $18/h")
    await chat_cards.mirror_event(state, _move(env, 1, t0 + 3600))
    store.append_chat(fid, thread="factory", author_kind="agent", author="atlas", body="ok")
    await chat_cards.mirror_event(state, _move(env, 2, t0 + 7200))

    assert [len(chat_cards.card_meta(m)["moves"]) for m in _base_lines(env)] == [2, 1]


async def test_a_line_spans_one_utc_day_and_at_most_twelve_hours(env: RoomEnv) -> None:
    state = env.cockpit.app.state
    t0 = 1_792_972_800 + 60  # 2026-10-26 00:01 UTC
    await chat_cards.mirror_event(state, _move(env, 0, t0))
    await chat_cards.mirror_event(state, _move(env, 1, t0 + chat_cards.BASE_LINE_SPAN_S - 60))
    await chat_cards.mirror_event(state, _move(env, 2, t0 + chat_cards.BASE_LINE_SPAN_S))
    await chat_cards.mirror_event(state, _move(env, 3, t0 + 86_400 - 120))  # 23:59, same day
    await chat_cards.mirror_event(state, _move(env, 4, t0 + 86_400))  # 00:01 the next day

    lines = _base_lines(env)
    assert [len(chat_cards.card_meta(m)["moves"]) for m in lines] == [2, 2, 1]
    assert lines[0].body == f"base main moved 2 times since 00:01 UTC → {HEAD[2][:7]}"


async def test_each_repo_has_its_own_line_named_when_there_are_several(env: RoomEnv) -> None:
    state = env.cockpit.app.state
    t0 = 1_793_000_000
    infra = "sample-org/sample-infra"
    await chat_cards.mirror_event(state, _move(env, 0, t0, repos=2))
    await chat_cards.mirror_event(state, _move(env, 1, t0 + 60, repos=2))
    await chat_cards.mirror_event(state, _move(env, 8, t0 + 120, repo=infra, repos=2, pr=12))
    await chat_cards.mirror_event(state, _move(env, 2, t0 + 180, repos=2))

    app, other = _base_lines(env)
    assert app.body == f"base main (sample-app) moved 3 times since 07:33 UTC → {HEAD[3][:7]}"
    assert other.body == f"base main (sample-infra) moved {HEAD[8][:7]} → {HEAD[9][:7]} (#12)"
    assert chat_cards.card_meta(other)["repo"] == infra
    assert chat_cards.base_line_text("main", chat_cards.card_meta(app)["moves"]).startswith(
        "base main moved 3 times"
    )


def test_a_base_move_without_its_data_stays_a_plain_line() -> None:
    event = FactoryEvent(id="a" * 32, factory_id="b" * 32, ts=0, kind="base.moved", summary="x")
    assert chat_cards.base_move(event) is None
    assert chat_cards.base_line_text(
        "main", [{"at": 0, "from_sha": None, "to_sha": HEAD[1], "pr": None}]
    ) == (f"base main moved ? → {HEAD[1][:7]}")
