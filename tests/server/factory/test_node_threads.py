"""
Threads per node: the Room's ``need:``, ``feature:``, ``study:`` and ``task:`` keys.

Who answers each level (the definition's responders; a task's thread goes to
its project's lead with the task named), no routing loops, the node's header
in every agent turn within the prompt size guard, cards in the thread of the
node they are about, ``node_ref`` on the thread list, and
``sys_factory_node(op="read", ref)``. Every name is sample data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.server.factory import chat, chat_cards, events, nodes, schemas, snapshot
from omnigent.server.factory.chat import Line, render_envelope
from omnigent.server.factory.chat_routing import (
    STATE_PAUSED,
    Agent,
    Members,
    routes,
)
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.node_views import validate_page
from tests.server.factory.chat_helpers import RoomEnv, room_env
from tests.server.factory.conftest import FactoryWorld

ATLAS = Agent("a" * 32, "Atlas")
SENTINEL = Agent("b" * 32, "Sentinel")
SCOUT = Agent("f" * 32, "Scout")
API = Agent("d" * 32, "PL · api-version")
TASK = "task:T-7"


def _members(**overrides: object) -> Members:
    base: dict[str, object] = {
        "atlas": ATLAS,
        "sentinel": SENTINEL,
        "pls": {"api-version": API},
        "node_projects": {TASK: "api-version"},
        "node_labels": {TASK: "T-7"},
    }
    base.update(overrides)
    return Members(**base)  # type: ignore[arg-type]


def _names(route: Any) -> list[str]:
    return [t.name for t in route.targets]


# ── Routing per level (pure) ───────────────────────────────────


@pytest.mark.parametrize(
    ("thread", "members", "names", "notes"),
    [
        pytest.param("feature:I-1", _members(), ["Atlas"], [], id="feature-atlas"),
        pytest.param(
            "need:O-3", _members(scout=SCOUT, discovery=True), ["Scout"], [], id="need-scout"
        ),
        pytest.param("need:O-3", _members(), ["Atlas"], [], id="need-no-discovery-atlas"),
        pytest.param(
            "study:R-1", _members(scout=SCOUT, discovery=True), ["Scout"], [], id="study-scout"
        ),
        pytest.param(
            "need:O-3",
            _members(discovery=True, scout_away="Scout reached today's budget; Atlas will answer"),
            ["Atlas"],
            ["Scout reached today's budget; Atlas will answer"],
            id="need-scout-away",
        ),
        pytest.param(TASK, _members(), ["PL · api-version"], [], id="task-its-lead"),
        pytest.param(
            TASK,
            _members(pls={}),
            ["Atlas"],
            ["PL · api-version not up yet; Atlas has it"],
            id="task-lead-not-up",
        ),
        pytest.param(
            TASK,
            _members(pls={"api-version": Agent(API.session_id, API.name, STATE_PAUSED)}),
            ["Atlas"],
            ["PL · api-version is paused; Atlas will answer"],
            id="task-lead-paused",
        ),
        pytest.param(
            "task:T-9", _members(node_projects={}), ["Atlas"], [], id="task-without-project"
        ),
        pytest.param(
            TASK,
            _members(responders={"task": "sentinel"}),
            ["Sentinel"],
            [],
            id="task-renamed-responder",
        ),
        pytest.param(
            TASK,
            _members(responders={"task": None}),
            [],
            ["Nobody answers T-7's thread; @mention an agent to ask one"],
            id="task-nobody",
        ),
    ],
)
def test_node_threads_route_to_their_levels_responder(
    thread: str, members: Members, names: list[str], notes: list[str]
) -> None:
    route = routes(thread, [], members)

    assert _names(route) == names
    assert list(route.notes) == notes


def test_an_agents_message_in_a_node_thread_never_loops() -> None:
    plain = routes(TASK, [], _members(), author_kind="agent", author_session=API.session_id)
    mentioned = routes(
        TASK, ["@atlas"], _members(), author_kind="agent", author_session=API.session_id
    )
    own = routes(
        TASK, ["@pl:api-version"], _members(), author_kind="agent", author_session=API.session_id
    )

    assert _names(plain) == []
    assert _names(mentioned) == ["Atlas"]
    assert _names(own) == []  # never routed its own message


def test_node_thread_kinds() -> None:
    assert [F.thread_kind(k) for k in ("need:O-3", "feature:I-1", "study:R-1", TASK)] == [
        "need",
        "feature",
        "study",
        "task",
    ]
    assert F.thread_kind("task:") is None


# ── The Room over a store ──────────────────────────────────────


@pytest_asyncio.fixture()
async def env(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[RoomEnv]:
    nodes.reset_cache()
    snapshot.reset_cache()
    async for value in room_env(world, db_uri, monkeypatch):
        task, _ = value.cockpit.store.upsert_task(
            value.cockpit.fid, "api-version", "route", title="Version route"
        )
        assert task.ref == "T-1"
        yield value
    nodes.reset_cache()
    snapshot.reset_cache()


async def _post(env: RoomEnv, thread: str, body: str, user: str = "alice") -> chat.Posted:
    return await chat.post_human(
        env.cockpit.app.state, env.world.get("lead_a"), user, {"thread": thread, "body": body}
    )


def _resolve(env: RoomEnv, raw: str) -> chat.Thread:
    return chat.resolve_thread(env.cockpit.deps, env.cockpit.fid, raw)


def _code(call: Any) -> str:
    with pytest.raises(CockpitError) as caught:
        call()
    return caught.value.code


async def test_node_thread_keys_resolve_to_this_factorys_nodes(env: RoomEnv) -> None:
    store = env.cockpit.store
    store.create_initiative(env.cockpit.fid, title="Helper (sample)", ask="a helper")

    task = _resolve(env, "task:T-1")
    spelled = _resolve(env, "T-1")
    feature = _resolve(env, "I-1")

    assert (task.key, task.kind, task.project, task.node, task.title) == (
        "task:T-1",
        "task",
        "api-version",
        "T-1",
        "T-1",
    )
    assert spelled.key == "task:T-1"
    assert (feature.key, feature.kind, feature.node) == ("feature:I-1", "feature", "I-1")
    assert _code(lambda: _resolve(env, "task:T-99")) == "unknown_node"
    assert _code(lambda: _resolve(env, "study:R-1")) == "unknown_node"
    assert _code(lambda: _resolve(env, "need:from-C-9")) == "unknown_node"
    assert _code(lambda: _resolve(env, "task:route")) == "invalid_thread"


async def test_a_task_message_reaches_its_lead_with_the_task_and_its_header(
    env: RoomEnv,
) -> None:
    posted = await _post(env, "task:T-1", "Is the route versioned by header? (sample)")
    await env.settle()

    texts = env.deliveries.to(env.id("pl"))
    assert [t.name for t in posted.route.targets] == ["PL · api-version"]
    assert len(texts) == 1
    text = texts[0]
    assert text.startswith("[Factory chat · T-1 · from ")
    assert "This thread is about Task T-1 · Version route (T-1)" in text
    assert "\nStatus: " in text and "\nPath: " in text
    assert 'sys_factory_node(op="read", ref="T-1")' in text
    assert text.endswith(
        f'sys_factory_chat(thread="task:T-1", reply_to="chat_{posted.message.id}", message=…)'
    )
    assert len(text) <= chat.ENVELOPE_MAX_CHARS


async def _node_header(env: RoomEnv, mode: str) -> None:
    resp = await env.cockpit.client.put(
        f"/v1/factory/factories/{env.cockpit.fid}/definition",
        json={"changes": [{"op": "replace", "path": "structure.node_header", "value": mode}]},
        headers={"X-Test-User": "alice"},
    )
    assert resp.status_code == 200, resp.text


async def test_the_header_is_off_where_the_factory_switched_it_off(env: RoomEnv) -> None:
    await _node_header(env, "off")

    await _post(env, "task:T-1", "Plain envelope, please (sample)")
    await env.settle()

    text = env.deliveries.to(env.id("pl"))[0]
    assert "This thread is about" not in text and "Status:" not in text


async def test_the_threads_v2_shares_keep_their_envelope_by_default(env: RoomEnv) -> None:
    await _post(env, "project:api-version", "Plain, as v2 sends it (sample)")
    await env.settle()

    [text] = env.deliveries.to(env.id("pl"))
    assert "This thread is about" not in text and "\nStatus: " not in text


async def test_a_factory_may_ask_for_the_header_in_every_thread(env: RoomEnv) -> None:
    await _node_header(env, "all")

    await _post(env, "project:api-version", "With the header (sample)")
    await env.settle()

    [text] = env.deliveries.to(env.id("pl"))
    assert "This thread is about Project " in text and "\nStatus: " in text


def test_the_prompt_stays_under_the_guard_with_the_header() -> None:
    header = "\n".join(
        [
            "--- This thread is about Task T-1 · Route (T-1), as the cockpit shows it ---",
            "Path: " + " › ".join(["Product", "Need " * 20, "Feature " * 20, "api-version"]),
            "Why: " + "w" * 300,
            "Status: Not moving: T-1 → held for C-16 → needs a person: Apply C-16 (D-45)",
            "Roll-up: tasks 3/5 · $25.13 of $150.00 · $18.88/h",
            "Open asks: Apply C-16 (D-45) · Answer D-49",
            'More: sys_factory_node(op="read", ref="T-1") returns this node\'s page as data.',
        ]
    )
    big = "x" * F.CHAT_MESSAGE_MAX_CHARS
    text = render_envelope(
        thread="task:T-1",
        title="T-1",
        messages=[Line("a" * 32, 0, "alice (owner)", big), Line("b" * 32, 60, "bob (owner)", big)],
        context=[Line(f"{i:032x}", 0, "carol (operator)", "y" * 190) for i in range(8)],
        refs=["!9001 (open)"],
        node=header,
    )
    plain = render_envelope(
        thread="task:T-1",
        title="T-1",
        messages=[Line("a" * 32, 0, "alice (owner)", "short")],
        node=header,
    )

    # What the server adds gives way; the two messages arrive whole.
    assert text.count("x" * F.CHAT_MESSAGE_MAX_CHARS) == 2
    assert "carol (operator)" not in text and "Why: " not in text
    assert text.startswith("[Factory chat · T-1 · 2 new from alice, bob · ids=")
    assert text.endswith('reply_to="chat_' + "b" * 32 + '", message=…)')
    # With room to spare, nothing is trimmed: the whole header rides along.
    assert header in plain
    assert len(plain) <= chat.ENVELOPE_MAX_CHARS


def test_the_guard_gives_up_the_header_and_the_context_before_any_message() -> None:
    header = "\n".join(
        [
            "--- This thread is about Task T-1 · Route (T-1), as the cockpit shows it ---",
            "Why: " + "w" * 300,
            "Status: Not moving: T-1 → needs a person: Apply C-16 (D-45)",
        ]
    )
    body = "y" * 3000
    text = render_envelope(
        thread="task:T-1",
        title="T-1",
        messages=[Line("a" * 32, 0, "alice (owner)", body + "END-A")],
        context=[Line(f"{i:032x}", 0, "carol (operator)", "c" * 190) for i in range(30)],
        node=header,
    )
    whole = render_envelope(
        thread="project:core",
        title="#core",
        messages=[
            Line("a" * 32, 0, "alice (owner)", "a" * 3998 + "END-A"),
            Line("b" * 32, 60, "bob (owner)", "b" * 3998 + "END-B"),
        ],
    )

    assert len(text) <= chat.ENVELOPE_MAX_CHARS
    assert "END-A" in text and "\nStatus: Not moving" in text and "Why: " not in text
    assert "… " in text  # the oldest context gave way, counted as earlier
    assert "END-A" in whole and "END-B" in whole


async def test_a_lead_posts_in_its_own_tasks_threads_only(env: RoomEnv) -> None:
    store = env.cockpit.store
    store.upsert_project(env.cockpit.fid, "docs", title="Docs", repo="sample-org/sample-app")
    store.upsert_task(env.cockpit.fid, "docs", "readme", title="Readme")
    pl = env.world.id("pl")

    mine = await env.cockpit.client.post(
        "/v1/factory/agent/chat",
        json={"from_session_id": pl, "thread": "task:T-1", "message": "Versioned by path."},
        headers={"X-Test-User": "alice"},
    )
    other = await env.cockpit.client.post(
        "/v1/factory/agent/chat",
        json={"from_session_id": pl, "thread": "task:T-2", "message": "Not mine."},
        headers={"X-Test-User": "alice"},
    )

    assert mine.status_code == 201 or mine.status_code == 200, mine.text
    assert other.status_code == 403 and "thread_not_allowed" in other.text


async def test_a_decision_card_lands_only_in_its_nodes_thread(env: RoomEnv) -> None:
    store, fid = env.cockpit.store, env.cockpit.fid
    store.upsert_task(fid, "api-version", "docs", title="Docs")
    decision, _ = store.create_decision(
        fid,
        dedupe_key="t1",
        question="Header or path? (sample)",
        subject="task:T-1",
        project_slug="api-version",
    )
    await events.emit(
        env.cockpit.app.state,
        fid,
        "decision.opened",
        summary="D asked",
        data={"decision_id": decision.id},
    )

    carded = [
        (m.thread, m.card["ref"])
        for m in env.messages()
        if (m.card or {}).get("type") == "decision"
    ]
    node_threads = {t for t, _ref in carded if F.thread_kind(t) in ("task", "need", "feature")}
    # Its own node's thread (the cockpit's), and the project's (the Room's) as before.
    assert node_threads == {"task:T-1"}
    assert {t for t, _ref in carded} == {"task:T-1", "project:api-version"}


async def test_an_alarm_the_server_closed_is_carded_in_its_tasks_thread(env: RoomEnv) -> None:
    store, fid = env.cockpit.store, env.cockpit.fid
    decision, _ = store.create_decision(
        fid,
        dedupe_key="stall",
        question="T-1 stalled: what next?",
        project_slug="api-version",
        subject="task:api-version/route",
    )
    await events.emit(
        env.cockpit.app.state,
        fid,
        "decision.answered",
        subject=f"decision:{decision.ref}",
        summary=f"{decision.ref} answered by the server",
        data={
            "decision_id": decision.id,
            "source": "loop",
            "by_kind": "server",
            "cause": "T-1 is held on purpose for C-16",
            "about": "T-1",
            "was": "T-1 stalled: what next?",
            "real_ask": "D-45",
            "real_ask_verb": "Apply C-16",
        },
    )

    in_task = [(m.card or {}).get("type") for m in env.messages("task:T-1")]
    cards = [m for m in env.messages("task:T-1") if (m.card or {}).get("type") == "closed_alarm"]
    # In the task's thread the alarm reads as closed, not as an answered decision.
    assert in_task == ["closed_alarm"]
    assert [(m.card or {}).get("type") for m in env.messages("project:api-version")] == [
        "decision"
    ]
    assert len(cards) == 1
    view = chat.message_views(env.cockpit.deps, cards)[0]["card"]
    assert view == {
        "type": "closed_alarm",
        "ref": decision.ref,
        "id": decision.id,
        "meta": {
            "kind": "closed_alarm",
            "was": "T-1 stalled: what next?",
            "cause": "T-1 is held on purpose for C-16",
            "real_ask": "D-45 (Apply C-16)",
        },
    }


async def test_a_bare_why_in_a_node_thread_posts_its_why_card(env: RoomEnv) -> None:
    posted = await _post(env, "task:T-1", "why?")
    await env.settle()

    assert posted.route.targets == ()
    assert env.deliveries.to(env.id("pl")) == []
    cards = [m for m in env.messages("task:T-1") if (m.card or {}).get("type") == "why"]
    assert [(c.card["ref"], c.body.startswith("Why Version route:")) for c in cards] == [
        ("T-1", True)
    ]


async def test_node_cards_post_only_in_their_nodes_thread(env: RoomEnv) -> None:
    state, fid = env.cockpit.app.state, env.cockpit.fid
    row = await chat_cards.post_node_card(
        state, fid, "T-1", "offer", "It can start now.", meta={"verb": "start", "title": "Start"}
    )
    product = await chat_cards.post_node_card(state, fid, "product", "why", "Why the product")

    assert row is not None and row.thread == "task:T-1"
    assert product is not None and product.thread == "factory"
    view = chat.message_views(env.cockpit.deps, [row])[0]["card"]
    assert view["meta"] == {"kind": "offer", "verb": "start", "title": "Start"}
    with pytest.raises(ValueError):
        await chat_cards.post_node_card(state, fid, "T-1", "decision", "not a node card")


async def test_each_node_card_is_a_valid_chat_message(env: RoomEnv) -> None:
    state, fid = env.cockpit.app.state, env.cockpit.fid
    cards = [
        ("why", "Why T-1: waiting", None),
        ("offer", "It can start now.", {"verb": "start", "title": "Start"}),
        (
            "closed_alarm",
            "T-1 is held on purpose.",
            {"kind": "closed_alarm", "was": "T-1 stalled", "cause": "held for C-1"},
        ),
        (
            "preview_applied",
            "alice added T-2.",
            {
                "verb": "add",
                "label": "Add a task",
                "by": "alice",
                "by_user": "alice",
                "undo_token": "0" * 24,
            },
        ),
    ]
    for kind, line, meta in cards:
        await chat_cards.post_node_card(state, fid, "T-1", kind, line, meta=meta)

    resp = await env.cockpit.client.get(
        f"/v1/factory/factories/{fid}/chat",
        params={"thread": "task:T-1"},
        headers={"X-Test-User": "alice"},
    )

    assert resp.status_code == 200, resp.text
    views = [schemas.ChatMessageView.model_validate(m) for m in resp.json()["data"]]
    shown = {v.card.type: v.card.meta for v in views if v.card is not None}
    assert set(shown) == {"why", "offer", "closed_alarm", "preview_applied"}
    assert shown["closed_alarm"] is not None and shown["closed_alarm"].cause == "held for C-1"
    assert shown["preview_applied"] is not None and shown["preview_applied"].verb == "add"
    assert shown["preview_applied"].by_user == "alice"


async def test_the_thread_list_names_each_threads_node(env: RoomEnv) -> None:
    await _post(env, "task:T-1", "A question about the route (sample)")
    base = f"/v1/factory/factories/{env.cockpit.fid}/chat/threads"

    plain = (await env.cockpit.client.get(base, headers={"X-Test-User": "alice"})).json()["data"]
    with_nodes = (
        await env.cockpit.client.get(base, params={"nodes": "1"}, headers={"X-Test-User": "alice"})
    ).json()["data"]

    refs = {t["key"]: t["node_ref"] for t in plain}
    assert refs["factory"] == "product" and refs["improvements"] == "fx"
    assert refs["project:api-version"] == "api-version"
    assert "task:T-1" not in refs  # the Room's own list stays as it was
    task = next(t for t in with_nodes if t["key"] == "task:T-1")
    assert (task["kind"], task["node_ref"], task["responder_title"]) == (
        "task",
        "T-1",
        "PL · api-version",
    )


async def test_the_thread_list_caps_the_node_threads_at_the_newest(
    env: RoomEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.server.factory.api import chat as chat_api

    await _post(env, "task:T-1", "A question about the route (sample)")
    base = f"/v1/factory/factories/{env.cockpit.fid}/chat/threads"
    monkeypatch.setattr(chat_api, "NODE_THREADS_MAX", 0)

    capped = (
        await env.cockpit.client.get(base, params={"nodes": "1"}, headers={"X-Test-User": "alice"})
    ).json()["data"]

    keys = {t["key"] for t in capped}
    assert "task:T-1" not in keys and "factory" in keys


async def test_preflight_says_the_server_routes_node_threads(env: RoomEnv) -> None:
    resp = await env.cockpit.client.get("/v1/factory/preflight", headers={"X-Test-User": "alice"})

    assert resp.status_code == 200 and resp.json()["node_threads"] is True


async def test_agents_read_a_node_page(env: RoomEnv) -> None:
    pl = env.world.id("pl")

    async def read(ref: str, **params: Any) -> Any:
        return await env.cockpit.client.get(
            "/v1/factory/agent/node",
            params={"from_session_id": pl, "ref": ref, **params},
            headers={"X-Test-User": "alice"},
        )

    product = await read("product")
    task = await read("T-1")
    missing = await read("T-404")
    cursor = await read("product", children_page="x")

    assert product.status_code == 200, product.text
    page = validate_page(product.json())
    assert page["node"]["ref"] == "product"
    assert page["verbs"] and not any(v["enabled"] for v in page["verbs"])
    assert validate_page(task.json())["thread"]["key"] == "task:T-1"
    assert missing.status_code == 404 and "unknown_node" in missing.text
    assert cursor.status_code == 400 and "bad_cursor" in cursor.text
