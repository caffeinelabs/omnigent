"""The Room API: people post, read and page; agents answer with sys_factory_chat."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi.routing import APIRoute

from omnigent.runner.identity import RUNNER_TUNNEL_TOKEN_HEADER
from omnigent.server.auth import LEVEL_OWNER, LEVEL_READ
from omnigent.server.factory import chat, schemas
from omnigent.server.factory.api import chat as chat_api
from omnigent.server.factory.origin import require_human_origin
from tests.server.factory.chat_helpers import RoomEnv, room_env
from tests.server.factory.cockpit_helpers import error_code
from tests.server.factory.conftest import FactoryWorld


@pytest_asyncio.fixture()
async def env(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[RoomEnv]:
    async for value in room_env(world, db_uri, monkeypatch):
        value.cockpit.permissions.ensure_user("dave")
        value.cockpit.permissions.grant("dave", value.cockpit.fid, LEVEL_READ)
        yield value


def _chat(env: RoomEnv) -> str:
    return f"/v1/factory/factories/{env.cockpit.fid}/chat"


async def _say(
    env: RoomEnv, body: str, *, thread: str = "factory", user: str = "alice", **extra: Any
) -> httpx.Response:
    return await env.cockpit.client.post(
        _chat(env), json={"thread": thread, "body": body, **extra}, headers={"X-Test-User": user}
    )


async def _agent(env: RoomEnv, key: str, **body: Any) -> httpx.Response:
    return await env.cockpit.client.post(
        "/v1/factory/agent/chat",
        json={"from_session_id": env.world.id(key), **body},
        headers={"X-Test-User": "alice"},
    )


# ── Posting ────────────────────────────────────────────────────


async def test_an_operator_posts_and_atlas_gets_it(env: RoomEnv) -> None:
    resp = await _say(env, "@pl:api-version and #api-version: keep it small", user="carol")
    await env.settle()

    assert resp.status_code == 201, resp.text
    out = resp.json()
    assert out["routed"] == ["PL · api-version"]
    message = out["message"]
    assert message["author_kind"] == "human"
    assert message["author"] == "carol" and message["author_role"] == "operator"
    assert message["mentions"] == ["@pl:api-version"]
    assert message["refs"] == ["#api-version"]
    assert message["routed"][0]["title"] == "PL · api-version"
    [text] = env.deliveries.to(env.world.id("pl"))
    assert text.startswith("[Factory chat · #factory · from carol (operator) · id=chat_")
    assert "Refs: #api-version (queued)" in text
    [posted] = env.cockpit.events("chat.posted")
    assert posted.data["by"] == "carol" and posted.data["by_kind"] == "human"


async def test_a_project_thread_reaches_its_pl_and_a_pr_thread_its_owner(env: RoomEnv) -> None:
    pr, _ = env.cockpit.store.upsert_pr(
        env.cockpit.fid,
        "caffeinelabs/app",
        9630,
        url="https://github.com/caffeinelabs/app/pull/9630",
        project_slug="api-version",
        head_sha="7767f0d" + "a" * 33,
        gates={"checks": "pass", "review": "pending"},
    )
    project = await _say(env, "use knex_migrations", thread="project:api-version")
    by_number = await _say(env, "is this reversible?", thread="!9630")
    await env.settle()

    assert project.json()["routed"] == ["PL · api-version"]
    assert by_number.json()["message"]["thread"] == f"pr:{pr.id}"
    texts = env.deliveries.to(env.world.id("pl"))
    assert len(texts) == 1  # the PR message waits for the PL's turn to end
    await env.room.turn_ended(env.world.get("pl"), env.cockpit.fid, {})
    await env.settle()
    pr_text = env.deliveries.to(env.world.id("pl"))[-1]
    assert pr_text.split("\n")[:2] == [
        pr_text.split("\n")[0],
        "PR !9630 caffeinelabs/app · head 7767f0d · open · checks pass · review pending",
    ]
    assert pr_text.startswith("[Factory chat · !9630 · from alice (creator)")


async def test_a_paused_pl_hands_its_thread_to_atlas_with_a_line(env: RoomEnv) -> None:
    env.world.store.set_labels(env.world.id("pl"), {"omnigent.factory.state": "paused"})

    resp = await _say(env, "status?", thread="project:api-version")
    await env.settle()

    assert resp.json()["routed"] == ["Atlas"]
    [note] = resp.json()["notes"]
    assert note["author_kind"] == "system"
    assert note["body"] == "PL · api-version is paused; Atlas will answer"
    assert len(env.deliveries.to(env.world.id("lead_a"))) == 1
    assert env.cockpit.events("chat.unroutable")


async def test_a_viewer_cannot_post(env: RoomEnv) -> None:
    resp = await _say(env, "hello", user="dave")
    assert resp.status_code == 403, resp.text
    assert env.messages() == []


async def test_a_stranger_gets_nothing(env: RoomEnv) -> None:
    resp = await _say(env, "hello", user="bob")
    assert resp.status_code in (403, 404)
    threads = await env.cockpit.client.get(f"{_chat(env)}/threads", headers={"X-Test-User": "bob"})
    assert threads.status_code in (403, 404)


async def test_an_agent_cannot_use_the_human_route(env: RoomEnv) -> None:
    resp = await env.cockpit.client.post(
        _chat(env),
        json={"thread": "factory", "body": "I am a human"},
        headers={"X-Test-User": "alice", RUNNER_TUNNEL_TOKEN_HEADER: "rt_abc.def"},
    )
    assert (resp.status_code, error_code(resp)) == (403, "human_only")


def test_every_human_write_route_requires_human_origin() -> None:
    writes = [
        route
        for route in chat_api.router.routes
        if isinstance(route, APIRoute)
        and route.methods & {"POST", "PUT", "PATCH", "DELETE"}
        and "/agent/" not in route.path
    ]
    assert len(writes) == 2
    for route in writes:
        assert require_human_origin in [d.call for d in route.dependant.dependencies]


async def test_bad_bodies_are_refused_with_their_field(env: RoomEnv) -> None:
    cases = [
        ({"thread": "nowhere", "body": "x"}, 400, "invalid_thread"),
        ({"thread": "project:nope", "body": "x"}, 404, "unknown_project"),
        ({"thread": "!1", "body": "x"}, 404, "unknown_pr"),
        ({"thread": "factory", "body": "   "}, 400, "invalid_body"),
        ({"thread": "factory", "body": "x" * 4001}, 400, "invalid_body"),
        ({"thread": "factory", "body": "x", "reply_to": "chat_" + "0" * 32}, 404, None),
    ]
    for body, status, code in cases:
        resp = await env.cockpit.client.post(
            _chat(env), json=body, headers={"X-Test-User": "alice"}
        )
        assert resp.status_code == status, (body, resp.text)
        if code:
            assert error_code(resp) == code, body


async def test_people_are_rate_limited(env: RoomEnv) -> None:
    env.busy.running.add(env.id("lead_a"))
    for i in range(chat.HUMAN_POSTS_PER_WINDOW):
        assert (await _say(env, f"msg {i}")).status_code == 201
    refused = await _say(env, "one too many")
    other = await _say(env, "carol still can", user="carol")

    assert (refused.status_code, error_code(refused)) == (429, "rate_limited")
    assert int(refused.headers["Retry-After"]) > 0
    assert other.status_code == 201
    env.clock.advance(chat.HUMAN_WINDOW_S + 1)
    assert (await _say(env, "later")).status_code == 201


# ── Reading ────────────────────────────────────────────────────


async def test_threads_list_responders_and_unread_counts(env: RoomEnv) -> None:
    await _say(env, "one")
    await _say(env, "two", user="carol")
    await _agent(env, "pl", thread="project:api-version", message="env is up")
    headers = {"X-Test-User": "carol"}

    resp = await env.cockpit.client.get(f"{_chat(env)}/threads", headers=headers)

    assert resp.status_code == 200, resp.text
    for view in resp.json()["data"]:
        schemas.ThreadView.model_validate(view)
    threads = {t["key"]: t for t in resp.json()["data"]}
    assert list(threads) == ["factory", "improvements", "project:api-version"]
    assert threads["factory"]["responder_title"] == "Atlas"
    assert threads["improvements"]["responder_title"] == "Lead B · Sentinel"
    assert threads["project:api-version"]["responder_title"] == "PL · api-version"
    assert threads["factory"]["messages"] == 2
    assert threads["factory"]["unread"] == 1  # carol's own message is read
    assert threads["project:api-version"]["unread"] == 1

    put = await env.cockpit.client.put(
        f"{_chat(env)}/reads",
        json={"thread": "factory", "last_seq": threads["factory"]["last_seq"]},
        headers=headers,
    )
    back = await env.cockpit.client.put(
        f"{_chat(env)}/reads", json={"thread": "#factory", "last_seq": 0}, headers=headers
    )
    again = await env.cockpit.client.get(f"{_chat(env)}/threads", headers=headers)

    assert put.status_code == 200 and back.json()["last_seq"] == threads["factory"]["last_seq"]
    assert {t["key"]: t["unread"] for t in again.json()["data"]}["factory"] == 0


async def test_messages_page_by_thread_and_seq(env: RoomEnv) -> None:
    env.busy.running.add(env.id("lead_a"))
    for i in range(5):
        await _say(env, f"m{i}")
    await _say(env, "elsewhere", thread="project:api-version")
    headers = {"X-Test-User": "dave"}

    latest = await env.cockpit.client.get(
        _chat(env), params={"thread": "factory", "limit": 2}, headers=headers
    )
    older = await env.cockpit.client.get(
        _chat(env),
        params={"thread": "factory", "limit": 2, "before_seq": latest.json()["data"][0]["seq"]},
        headers=headers,
    )
    after = await env.cockpit.client.get(
        _chat(env), params={"thread": "factory", "after_seq": 0, "limit": 10}, headers=headers
    )
    everything = await env.cockpit.client.get(_chat(env), headers=headers)

    for view in everything.json()["data"]:
        schemas.ChatMessageView.model_validate(view)
    assert [m["body"] for m in latest.json()["data"]] == ["m3", "m4"]
    assert latest.json()["has_more"] is True
    assert [m["body"] for m in older.json()["data"]] == ["m1", "m2"]
    assert [m["body"] for m in after.json()["data"]] == [f"m{i}" for i in range(5)]
    assert after.json()["has_more"] is False
    # dave cannot read PL · api-version, so its thread is left out for him.
    assert [m["thread"] for m in everything.json()["data"]] == ["factory"] * 5
    owner = await env.cockpit.client.get(_chat(env), headers={"X-Test-User": "alice"})
    assert len(owner.json()["data"]) == 6


async def test_a_hidden_projects_threads_are_left_out(env: RoomEnv) -> None:
    await _say(env, "plan?", thread="project:api-version")
    dave = {"X-Test-User": "dave"}

    threads = await env.cockpit.client.get(f"{_chat(env)}/threads", headers=dave)
    page = await env.cockpit.client.get(
        _chat(env), params={"thread": "project:api-version"}, headers=dave
    )
    mine = await env.cockpit.client.get(f"{_chat(env)}/threads", headers={"X-Test-User": "alice"})

    assert "project:api-version" not in [t["key"] for t in threads.json()["data"]]
    assert (page.status_code, error_code(page)) == (404, "unknown_project")
    assert "project:api-version" in [t["key"] for t in mine.json()["data"]]


async def test_the_status_digest_renders_the_snapshot(env: RoomEnv) -> None:
    headers = {"X-Test-User": "alice"}
    whole = await env.cockpit.client.get(
        f"/v1/factory/factories/{env.cockpit.fid}/status-digest", headers=headers
    )
    one = await env.cockpit.client.get(
        f"/v1/factory/factories/{env.cockpit.fid}/status-digest",
        params={"slug": "api-version"},
        headers=headers,
    )
    missing = await env.cockpit.client.get(
        f"/v1/factory/factories/{env.cockpit.fid}/status-digest",
        params={"slug": "nope"},
        headers=headers,
    )

    assert whole.status_code == 200, whole.text
    text = whole.json()["markdown"]
    assert text.startswith("**")
    assert "- `api-version`" in text
    assert one.json()["markdown"].startswith("**#api-version** · API version")
    assert (missing.status_code, error_code(missing)) == (404, "unknown_project")
    # A viewer who cannot read the PL sees the project counted as hidden, not listed.
    viewer = await env.cockpit.client.get(
        f"/v1/factory/factories/{env.cockpit.fid}/status-digest", headers={"X-Test-User": "dave"}
    )
    assert "(1 hidden)" in viewer.json()["markdown"]
    assert "api-version" not in viewer.json()["markdown"]


def test_the_digest_names_prs_and_open_decisions() -> None:
    body = {
        "factory": {"name": "Atlas factory", "state": "active"},
        "totals": {"prs_ready": 1, "decisions_open": 1, "cost_usd": 12.4},
        "projects": [
            {
                "slug": "db-version",
                "title": "DB version",
                "priority": 1,
                "stage": "review",
                "status": "active",
                "pl": {"title": "PL · db-version", "status": "running"},
            },
            {
                "slug": "ui",
                "title": "UI",
                "stage": "queued",
                "overlay": "waiting",
                "overlay_reason": "waiting on db-version",
                "depends_on": ["db-version"],
            },
        ],
        "prs": [
            {
                "number": 9631,
                "state": "open",
                "ready": True,
                "project_slug": "db-version",
                "gates": {"checks": "pass", "review": "pass", "verify": "unknown"},
                "demo": {"status": "PASS"},
                "url": "https://github.com/caffeinelabs/app/pull/9631",
            }
        ],
        "decisions": [
            {
                "ref": "D-7",
                "status": "open",
                "blocking": True,
                "question": "Which table?",
                "project_slug": "db-version",
            }
        ],
    }

    text = chat_api.render_digest(body)
    detail = chat_api.render_digest(body, "ui")

    assert text.splitlines() == [
        "**Atlas factory** · active · 2 projects · 1 ready for human merge · "
        "1 decisions open · $12.40",
        "- `db-version` review · PL · db-version running · "
        "!9631 · ready for human merge · checks pass · review pass · demo PASS",
        "- `ui` queued (waiting: waiting on db-version) · no PL yet",
        "Needs you: D-7 (blocking) Which table?",
    ]
    assert "Waiting on: db-version" in detail


# ── Agents ─────────────────────────────────────────────────────


async def test_sys_factory_chat_scopes_a_project_lead_to_its_threads(env: RoomEnv) -> None:
    store = env.cockpit.store
    store.upsert_project(env.cockpit.fid, "docs", title="Docs", repo="caffeinelabs/app")
    other_pr, _ = store.upsert_pr(
        env.cockpit.fid,
        "caffeinelabs/app",
        9700,
        url="https://github.com/caffeinelabs/app/pull/9700",
        project_slug="docs",
    )
    own_pr, _ = store.upsert_pr(
        env.cockpit.fid,
        "caffeinelabs/app",
        9630,
        url="https://github.com/caffeinelabs/app/pull/9630",
        project_slug="api-version",
    )

    own = await _agent(env, "pl", thread="project:api-version", message="done", refs=["D-1"])
    factory = await _agent(env, "pl", thread="factory", message="api-version is ready")
    own_pr_post = await _agent(env, "pl", thread="pr:9630", message="rebased")
    other = await _agent(env, "pl", thread="project:docs", message="hi")
    other_pr_post = await _agent(env, "pl", thread=f"pr:{other_pr.id}", message="hi")
    improvements = await _agent(env, "pl", thread="improvements", message="hi")
    sentinel = await _agent(env, "lead_b", thread="improvements", message="retro posted")
    ic = await _agent(env, "ic", thread="factory", message="hi")

    assert own.status_code == 200, own.text
    assert own.json()["id"].startswith("chat_") and own.json()["thread"] == "project:api-version"
    assert factory.status_code == 200
    assert own_pr_post.json()["thread"] == f"pr:{own_pr.id}"
    for refused in (other, other_pr_post, improvements):
        assert (refused.status_code, error_code(refused)) == (403, "thread_not_allowed")
    assert sentinel.status_code == 200
    assert (ic.status_code, error_code(ic)) == (403, "not_allowed")
    assert env.messages("project:api-version")[0].refs == ["D-1"]


async def test_sys_factory_chat_is_rate_limited_through_the_mailer(env: RoomEnv) -> None:
    env.mailer.pair_rate_per_hour = 2
    for i in range(2):
        assert (await _agent(env, "lead_a", thread="factory", message=f"m{i}")).status_code == 200
    refused = await _agent(env, "lead_a", thread="factory", message="m3")
    elsewhere = await _agent(env, "lead_a", thread="improvements", message="ok")

    assert (refused.status_code, error_code(refused)) == (429, "rate_limited")
    assert elsewhere.status_code == 200
    assert ("chat:" + env.id("lead_a"), "factory") in env.mailer._pair_posts


async def test_sys_factory_chat_refuses_bad_bodies(env: RoomEnv) -> None:
    empty = await _agent(env, "lead_a", thread="factory", message=" ")
    long = await _agent(env, "lead_a", thread="factory", message="x" * 4001)
    unknown = await _agent(
        env, "lead_a", thread="factory", message="x", reply_to="chat_" + "1" * 32
    )
    no_thread = await _agent(env, "lead_a", message="x")

    assert (empty.status_code, error_code(empty)) == (400, "empty_message")
    assert (long.status_code, error_code(long)) == (413, "message_too_large")
    assert (unknown.status_code, error_code(unknown)) == (404, "unknown_message")
    assert (no_thread.status_code, error_code(no_thread)) == (400, "invalid_thread")


async def test_sys_factory_chat_caps_mentions_and_names_a_person_posting_as_an_agent(
    env: RoomEnv,
) -> None:
    resp = await _agent(
        env, "lead_a", thread="factory", message="fyi", mentions=[f"@u{i}@x.io" for i in range(50)]
    )

    assert resp.status_code == 200, resp.text
    [row] = env.messages("factory")
    assert len(row.mentions) == 20
    # No runner credential carried the post: the person behind it is on the event.
    [event] = env.cockpit.events("chat.posted")
    assert event.data["via_user"] == "alice"


# ── Kickoff ────────────────────────────────────────────────────


async def test_the_start_brief_is_factory_message_one_and_atlas_answers_it(
    env: RoomEnv,
) -> None:
    root = env.world.make_session("atlas", agent="lead", title="Atlas")
    env.cockpit.permissions.grant("alice", root.id, LEVEL_OWNER)
    resp = await env.cockpit.post(
        "/v1/factory/factories",
        {
            "root_session_id": root.id,
            "name": "Atlas factory",
            "projects": [],
            "brief": "Show the deployed versions in Settings.",
        },
    )
    await env.settle()

    assert resp.status_code == 201, resp.text
    [first] = env.cockpit.store.list_chat(root.id, "factory", limit=10)[:1]
    assert first.seq == 1
    assert (first.author_kind, first.author, first.body) == (
        "human",
        "alice",
        "Show the deployed versions in Settings.",
    )
    assert first.routed[0]["mail_id"] == resp.json()["mail_id"]
    [kickoff] = env.deliveries.to(root.id)
    ref = f"chat_{first.id}"
    assert f'reply_to="{ref}"' in kickoff
    assert f'sys_factory_chat(thread="factory", reply_to="{ref}"' in kickoff
    # The kickoff turn's output answers the brief in #factory when Atlas posts nothing there.
    atlas = env.world.get("atlas")
    await env.room.turn_ended(atlas, root.id, {"output": "Planning… → C-1"})
    await env.settle()
    reply = env.cockpit.store.list_chat(root.id, "factory", limit=10)[-1]
    assert reply.auto_reply and reply.reply_to == first.id
    brief = env.cockpit.store.get_chat_message(first.id)
    assert brief is not None
    assert brief.routed[0]["delivered_at"] is not None  # the kickoff's delivery
    assert brief.routed[0]["replied_at"] == reply.created_at
    lines = [m.body for m in env.cockpit.store.list_chat(root.id, "factory", limit=10)]
    assert any("started Atlas factory" in body for body in lines)
