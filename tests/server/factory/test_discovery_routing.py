"""The Room's ``#discovery`` thread and ``@scout``: Scout answers while discovery runs.

``#discovery`` routes to the seated Scout (to Atlas while it has none); it is
listed, and resolves, only while the server env and the factory's
``discovery.enabled`` are on. ``@scout`` routes only then too. Scout posts in
``#discovery`` and nowhere else. Every product word is sample data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from omnigent.server.factory import chat, definition
from omnigent.server.factory.chat_routing import (
    Agent,
    Members,
    is_agent_mention,
    parse_mentions,
    routes,
    team_signal,
)
from omnigent.server.factory.control import CockpitError
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.discovery_helpers import base, discovery_cockpit
from tests.server.factory.source_helpers import add_scout

ATLAS = Agent("a" * 32, "Atlas")
SENTINEL = Agent("b" * 32, "Sentinel")
SCOUT = Agent("f" * 32, "Scout")


def _names(route: object) -> list[str]:
    return [t.name for t in route.targets]  # type: ignore[attr-defined]


# ── The routing table (pure) ───────────────────────────────────


def test_discovery_thread_goes_to_scout() -> None:
    route = routes("discovery", [], Members(atlas=ATLAS, scout=SCOUT, discovery=True))
    assert _names(route) == ["Scout"] and route.notes == ()


def test_discovery_thread_falls_back_to_atlas_without_a_seat() -> None:
    route = routes("discovery", [], Members(atlas=ATLAS, discovery=True))
    assert _names(route) == ["Atlas"]
    assert route.notes == ("Scout is not seated; Atlas will answer",)


def test_a_stopped_scout_hands_the_thread_to_atlas() -> None:
    stopped = Agent(SCOUT.session_id, "Scout", "done")
    route = routes("discovery", [], Members(atlas=ATLAS, scout=stopped, discovery=True))
    assert _names(route) == ["Atlas"]
    assert route.unroutable == ("Scout",)


def test_at_scout_adds_scout_anywhere_while_discovery_runs() -> None:
    members = Members(atlas=ATLAS, sentinel=SENTINEL, scout=SCOUT, discovery=True)
    route = routes("factory", ["@scout", "@atlas"], members)
    assert _names(route) == ["Scout", "Atlas"]
    # An agent reaches Scout only by mentioning it.
    agent = routes("improvements", ["@scout"], members, author_kind="agent")
    assert _names(agent) == ["Scout"]


def test_at_scout_is_plain_text_without_discovery() -> None:
    members = Members(atlas=ATLAS, sentinel=SENTINEL)
    route = routes("factory", ["@scout"], members)
    # The thread's default answers, as before discovery existed; nobody is notified.
    assert _names(route) == ["Atlas"] and route.notes == () and route.people == ()
    assert routes("factory", ["@scout"], members, author_kind="agent").targets == ()


def test_scout_mentions_parse() -> None:
    assert parse_mentions("@Scout what do new users say?") == ["@scout"]
    assert parse_mentions("ask", ["scout"]) == ["@scout"]
    assert is_agent_mention("@scout")
    # An address that only starts with the word is not the mention.
    assert parse_mentions("@scouts and @scout-team") == []


@pytest.mark.parametrize(
    ("thread", "body", "expected"),
    [
        ("discovery", "I think people leave because setup is slow (sample)", "hunch"),
        ("discovery", "we need an export (sample)", "request"),
        ("discovery", "@scout hunch: drafts matter (sample)", "hunch"),
        ("project:api-version", "I think it is slow because of x (sample)", None),
    ],
)
def test_discovery_keeps_peoples_hunches_and_requests(
    thread: str, body: str, expected: str | None
) -> None:
    found = team_signal(thread, body)
    assert (found[0] if found else None) == expected


# ── The Room over a factory ────────────────────────────────────


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in discovery_cockpit(world, db_uri, monkeypatch):
        yield value


async def _threads(cockpit: Cockpit) -> dict[str, dict]:
    resp = await cockpit.client.get(
        base(cockpit, "/chat/threads"), headers={"X-Test-User": "alice"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    rows = body["data"] if isinstance(body, dict) and "data" in body else body["threads"]
    return {row["key"]: row for row in rows}


async def test_the_thread_list_shows_discovery_only_while_it_runs(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    scout = add_scout(cockpit, monkeypatch)

    on = await _threads(cockpit)
    assert on["discovery"]["kind"] == "discovery"
    assert on["discovery"]["title"] == "#discovery"
    assert on["discovery"]["responder_session_id"] == chat._key(scout)
    assert list(on)[:3] == ["factory", "improvements", "discovery"]

    definition.set_values(
        cockpit.deps, cockpit.fid, {"discovery.enabled": False}, created_by="alice"
    )
    definition.invalidate(cockpit.fid)
    assert "discovery" not in await _threads(cockpit)


async def test_a_person_posts_in_discovery_and_scout_answers(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_scout(cockpit, monkeypatch)

    resp = await cockpit.post(
        base(cockpit, "/chat"), {"thread": "#discovery", "body": "What do teams ask for? (sample)"}
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["routed"] == ["Scout (sample)"]


async def test_discovery_is_refused_while_off(cockpit: Cockpit) -> None:
    definition.set_values(
        cockpit.deps, cockpit.fid, {"discovery.enabled": False}, created_by="alice"
    )
    definition.invalidate(cockpit.fid)

    resp = await cockpit.post(base(cockpit, "/chat"), {"thread": "discovery", "body": "hi"})

    assert resp.status_code == 404 and error_code(resp) == "discovery_off"
    with pytest.raises(CockpitError) as raised:
        chat.resolve_thread(cockpit.deps, cockpit.fid, "#discovery")
    assert raised.value.code == "discovery_off"


async def _agent_post(cockpit: Cockpit, session_id: str, thread: str) -> object:
    return await cockpit.client.post(
        "/v1/factory/agent/chat",
        json={"from_session_id": session_id, "thread": thread, "message": "Found 3 (sample)"},
        headers={"X-Test-User": "alice"},
    )


async def test_scout_posts_in_discovery_and_nowhere_else(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    scout = add_scout(cockpit, monkeypatch)

    ok = await _agent_post(cockpit, scout, "discovery")
    assert ok.status_code == 200, ok.text  # type: ignore[attr-defined]
    for thread in ("factory", "improvements", "project:api-version"):
        refused = await _agent_post(cockpit, scout, thread)
        assert refused.status_code == 403, thread  # type: ignore[attr-defined]
        assert error_code(refused) == "thread_not_allowed"  # type: ignore[arg-type]
    # Leads post in #discovery too; a project lead does not.
    lead = await _agent_post(cockpit, cockpit.world.id("lead_b"), "discovery")
    assert lead.status_code == 200  # type: ignore[attr-defined]
    pl = await _agent_post(cockpit, cockpit.world.id("pl"), "discovery")
    assert pl.status_code == 403  # type: ignore[attr-defined]
