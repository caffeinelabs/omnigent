"""
The node chat as a conversation (``GET …/chat?thread=&feed=``, ``chat_feed``).

``feed=conversation`` holds what people and agents say plus the live cards the
reader acts on; ``feed=activity`` holds everything the system mirrors (base
moves, spawns, incidents, closed or answered items, other people's asks), with
how much of it came since the reader's read mark. The live case is the product
thread from the person's screenshot: base moves, a spawn, three resolved
incidents, a closed proposal with its diff, one open question. Every name is
sample data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio

from omnigent.server.auth import LEVEL_OWNER
from omnigent.server.factory import chat_feed, initiatives, nodes, schemas, snapshot
from omnigent.server.factory.node_views import validate_page
from omnigent.server.factory.your_move import Viewer
from omnigent.stores.factory_store import InMemoryFactoryStore
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import REPO, header_cockpit

THREAD = "factory"
FOLLOW_UP = [
    {"id": "follow-up", "label": "Follow-up project now", "recommended": True},
    {"id": "park", "label": "Park it"},
]


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    initiatives.register()
    nodes.reset_cache()
    snapshot.reset_cache()
    async for value in header_cockpit(world, db_uri, monkeypatch):
        yield value
    nodes.reset_cache()
    snapshot.reset_cache()


def _system(cockpit: Cockpit, body: str, card: dict[str, Any], **extra: Any) -> str:
    row = cockpit.store.append_chat(
        cockpit.fid,
        thread=extra.pop("thread", THREAD),
        author_kind="system",
        author="server",
        body=body,
        card=card,
        **extra,
    )
    return row.id


def _said(cockpit: Cockpit, who: str, body: str, *, agent: bool = False) -> str:
    row = cockpit.store.append_chat(
        cockpit.fid,
        thread=THREAD,
        author_kind="agent" if agent else "human",
        author=cockpit.world.id("lead_a") if agent else who,
        body=body,
    )
    return row.id


def _incident(cockpit: Cockpit, n: int, summary: str) -> str:
    issue, _ = cockpit.store.upsert_issue(
        cockpit.fid,
        class_="incident",
        rule=f"sample.rule.{n}",
        target="factory",
        summary=summary,
        severity="warning",
    )
    cockpit.store.update_issue(issue.id, status="resolved", resolved_at=issue.first_seen + 60)
    return _system(cockpit, summary, {"type": "issue", "ref": issue.ref, "id": issue.id})


async def _feed(cockpit: Cockpit, feed: str, user: str = "alice", **params: Any) -> dict[str, Any]:
    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/chat",
        params={"thread": THREAD, "feed": feed, **params},
        headers={"X-Test-User": user},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for message in body["data"]:
        schemas.ChatMessageView.model_validate(message)
    return body


def _live_thread(cockpit: Cockpit) -> dict[str, str]:
    """The product thread of the screenshot, oldest first; returns row ids by name."""
    store = cockpit.store
    ids: dict[str, str] = {}
    moves = {"kind": "base.moved", "key": f"{REPO.lower()}#main", "repo": REPO, "base": "main"}
    ids["base"] = _system(
        cockpit,
        "base main moved 21 times since 13:35 UTC → 386cd05",
        {"type": "system", "meta": {**moves, "moves": [{"at": 1, "to_sha": "386cd05"}]}},
    )
    ids["ask"] = _said(cockpit, "alice", "@Atlas how is the assistant going? (sample)")
    ids["reply"] = _said(
        cockpit, "", "Core is held for C-16; the rest builds. (sample)", agent=True
    )
    question, _ = store.create_decision(
        cockpit.fid,
        dedupe_key="sample-live-search",
        question="Discovery is on the base env but live search does not work. Start a "
        "follow-up project to fix it?",
        source="agent",
        blocking=True,
        options=FOLLOW_UP,
        assignee="owners",
    )
    ids["question"] = _system(
        cockpit,
        f"{question.ref} opened",
        {"type": "decision", "ref": question.ref, "id": question.id},
    )
    ids["spawned"] = _system(
        cockpit, "the server spawned PL · sample-panel", {"type": "system", "ref": None}
    )
    for n, summary in (
        (1, "the factory spends $21.00/h"),
        (5, "Sentinel idle"),
        (4, "Atlas idle"),
    ):
        ids[f"inc{n}"] = _incident(cockpit, n, summary)
    closed, _ = store.create_proposal(
        cockpit.fid,
        kind="projects",
        title="update skill-discovery (scope_paths)",
        author_kind="agent",
        author=cockpit.world.id("lead_a"),
    )
    store.update_proposal(closed.id, status="refused")
    ids["closed"] = _system(
        cockpit,
        f"{closed.ref} closed itself: the project changed since",
        {"type": "proposal", "ref": closed.ref, "id": closed.id},
    )
    ids["question_ref"] = question.ref
    return ids


async def test_the_live_product_thread_is_a_conversation(cockpit: Cockpit) -> None:
    ids = _live_thread(cockpit)

    said = await _feed(cockpit, "conversation")
    moved = await _feed(cockpit, "activity")

    assert [m["id"] for m in said["data"]] == [ids["ask"], ids["reply"], ids["question"]]
    card = said["data"][-1]
    # The open question is the one live card this reader answers.
    assert card["lane"] == "conversation" and card["needs_you"] is True
    assert card["ask"] == ids["question_ref"]
    activity = {m["id"]: m["activity"] for m in moved["data"]}
    assert list(activity) == [
        ids["base"],
        ids["spawned"],
        ids["inc1"],
        ids["inc5"],
        ids["inc4"],
        ids["closed"],
    ]
    assert activity[ids["base"]] == {"kind": "base.moved", "closed": False}
    # Resolved incidents and the closed proposal never come back as conversation.
    assert all(activity[ids[k]] == {"kind": "issue", "closed": True} for k in ("inc1", "inc4"))
    assert activity[ids["closed"]] == {"kind": "proposal", "closed": True}
    assert said["activity_since"] == moved["activity_since"] == 6
    assert said["unread"] == 2  # the agent's reply and the question card; not the reader's own
    assert said["has_more"] is False and said["before_seq"] is not None
    # One read of every row, each marked with its lane, for a client that folds them itself.
    every = await _feed(cockpit, "all")
    lanes = {m["id"]: m["lane"] for m in every["data"]}
    assert len(lanes) == 9 and lanes[ids["question"]] == "conversation"
    assert lanes[ids["inc5"]] == lanes[ids["closed"]] == "activity"


async def test_activity_since_counts_from_the_read_mark(cockpit: Cockpit) -> None:
    _live_thread(cockpit)
    last = max(m.seq for m in cockpit.store.list_chat(cockpit.fid, THREAD, limit=100))
    resp = await cockpit.client.put(
        f"/v1/factory/factories/{cockpit.fid}/chat/reads",
        json={"thread": THREAD, "last_seq": last},
        headers={"X-Test-User": "alice"},
    )
    assert resp.status_code == 200, resp.text
    _system(cockpit, "base main moved 1 time", {"type": "system"})

    said = await _feed(cockpit, "conversation")

    assert (said["last_read_seq"], said["unread"], said["activity_since"]) == (last, 0, 1)


async def test_someone_elses_ask_is_activity_for_this_reader(cockpit: Cockpit) -> None:
    ids = _live_thread(cockpit)

    # carol edits but the question is the owners': nothing in it needs her.
    said = await _feed(cockpit, "conversation", user="carol")
    moved = await _feed(cockpit, "activity", user="carol")

    assert ids["question"] not in [m["id"] for m in said["data"]]
    question = next(m for m in moved["data"] if m["id"] == ids["question"])
    assert question["activity"] == {"kind": "decision", "closed": False}
    assert question["ask"] == ids["question_ref"] and "needs_you" not in question


async def test_live_cards_stay_while_they_need_the_reader(cockpit: Cockpit) -> None:
    store = cockpit.store
    open_pr, _ = store.upsert_pr(
        cockpit.fid,
        REPO,
        9101,
        project_slug="api-version",
        state="open",
        ready=True,
        base_ref="main",
        title="Sample change",
    )
    merged_pr, _ = store.upsert_pr(
        cockpit.fid, REPO, 9102, project_slug="api-version", state="merged", base_ref="main"
    )
    pending, _ = store.create_proposal(
        cockpit.fid,
        kind="projects",
        title="Sample update",
        author_kind="agent",
        author=cockpit.world.id("lead_a"),
    )
    applying, _ = store.create_decision(
        cockpit.fid,
        dedupe_key="apply-sample",
        question=f"Apply {pending.ref}?",
        source="proposal",
        subject=f"proposal:{pending.ref}",
        blocking=False,
    )
    answered, _ = store.create_decision(
        cockpit.fid, dedupe_key="answered", question="Old question?", source="agent"
    )
    store.answer_decision(answered.id, option_id=None, text="done", by="alice")
    asked = _said(cockpit, "alice", "why is the panel not moving?")
    rows = {
        "proposal": _system(
            cockpit, "C", {"type": "proposal", "ref": pending.ref, "id": pending.id}
        ),
        "pr": _system(cockpit, "PR", {"type": "pr", "ref": "!9101", "id": open_pr.id}),
        "merged": _system(cockpit, "PR", {"type": "pr", "ref": "!9102", "id": merged_pr.id}),
        "answered": _system(
            cockpit, "D", {"type": "decision", "ref": answered.ref, "id": answered.id}
        ),
        "why": _system(cockpit, "Why", {"type": "why", "ref": "panel"}),
        "offer": _system(
            cockpit,
            "Offer",
            {"type": "offer", "ref": "panel", "meta": {"kind": "offer", "verb": "start"}},
        ),
        "note": _system(cockpit, "PL is paused; Atlas will answer", {}, reply_to=asked),
        "undo": _system(
            cockpit,
            "Applied",
            {
                "type": "preview_applied",
                "ref": "panel",
                "meta": {
                    "kind": "preview_applied",
                    "verb": "start",
                    "undo_until": 4_000_000_000,
                    "by_user": "alice",
                },
            },
        ),
        "old_undo": _system(
            cockpit,
            "Applied",
            {
                "type": "preview_applied",
                "ref": "panel",
                "meta": {"kind": "preview_applied", "verb": "start", "undo_until": 1},
            },
        ),
        "closed_alarm": _system(cockpit, "Closed", {"type": "closed_alarm", "ref": "D-9"}),
    }

    said = {m["id"]: m for m in (await _feed(cockpit, "conversation"))["data"]}
    moved = {m["id"]: m for m in (await _feed(cockpit, "activity"))["data"]}

    assert said[rows["proposal"]]["ask"] == applying.ref and said[rows["proposal"]]["needs_you"]
    assert said[rows["pr"]]["ask"] == "merge:main"
    assert said[rows["offer"]]["needs_you"] is True
    assert {rows[k] for k in ("why", "note", "undo")} <= said.keys()
    assert moved[rows["merged"]]["activity"] == {"kind": "pr", "closed": True}
    assert moved[rows["answered"]]["activity"] == {"kind": "decision", "closed": True}
    assert moved[rows["closed_alarm"]]["activity"]["closed"] is True
    assert rows["old_undo"] in moved and not said.keys() & moved.keys()


async def test_a_feed_pages_back_past_the_other_feed(cockpit: Cockpit) -> None:
    first = _said(cockpit, "alice", "first (sample)")
    second = _said(cockpit, "alice", "second (sample)")
    for n in range(240):
        _system(cockpit, f"base main moved {n}", {"type": "system"})
    third = _said(cockpit, "alice", "third (sample)")

    newest = await _feed(cockpit, "conversation", limit=1)
    older = await _feed(cockpit, "conversation", limit=2, before_seq=newest["before_seq"])
    moved = await _feed(cockpit, "activity", limit=50)

    assert [m["id"] for m in newest["data"]] == [third] and newest["has_more"] is True
    assert [m["id"] for m in older["data"]] == [first, second] and older["has_more"] is False
    assert len(moved["data"]) == 50 and moved["has_more"] is True
    assert moved["activity_since"] == 99  # capped: the cockpit says "99+"


async def test_a_feed_reads_one_thread_and_the_room_is_unchanged(cockpit: Cockpit) -> None:
    _live_thread(cockpit)

    bad = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/chat",
        params={"feed": "conversation"},
        headers={"X-Test-User": "alice"},
    )
    room = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/chat",
        params={"thread": THREAD},
        headers={"X-Test-User": "alice"},
    )

    assert (bad.status_code, error_code(bad)) == (400, "invalid_query")
    # Without a feed the Room reads every row, as before.
    assert len(room.json()["data"]) == 9 and "feed" not in room.json()["data"][0]


async def test_the_node_page_counts_unread_conversation_and_activity_apart(
    cockpit: Cockpit,
) -> None:
    _live_thread(cockpit)
    nodes.reset_cache()

    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/n/product", headers={"X-Test-User": "alice"}
    )
    page = validate_page(resp.json())

    assert page["thread"]["key"] == THREAD
    assert (page["thread"]["unread"], page["thread"]["activity"]) == (2, 6)


def test_a_mark_far_behind_still_counts_what_is_recent() -> None:
    store = InMemoryFactoryStore()
    deps = SimpleNamespace(factory_store=store)
    fid = "e" * 32
    first = store.append_chat(fid, thread=THREAD, author_kind="human", author="alice", body="hi")
    store.upsert_chat_read(fid, "alice", THREAD, first.seq)
    for n in range(chat_feed.SCAN_MAX + 100):
        store.append_chat(
            fid,
            thread=THREAD,
            author_kind="system",
            author="server",
            body=f"moved {n}",
            card={"type": "system"},
        )
    for body in ("one (sample)", "two (sample)"):
        store.append_chat(fid, thread=THREAD, author_kind="human", author="ben", body=body)

    found = chat_feed.since(deps, fid, THREAD, "alice", Viewer("alice", LEVEL_OWNER))  # type: ignore[arg-type]

    assert found == (first.seq, 2, chat_feed.SINCE_CAP)
