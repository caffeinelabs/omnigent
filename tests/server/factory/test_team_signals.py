"""The ``team`` source: what the factory's own people say in the Room becomes hunch signals.

``hunch: …`` anywhere, "I think … because …", and requests such as "we need
…" in ``#factory`` are kept as hunches (weight 0.1, never user evidence);
other messages are not. Nothing is kept while discovery is off or the team
source is paused. Every text is sample data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from omnigent.server.factory import definition
from omnigent.server.factory.chat_routing import team_signal
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.discovery_helpers import SOURCES, discovery_cockpit


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in discovery_cockpit(world, db_uri, monkeypatch):
        yield value


async def say(
    cockpit: Cockpit, body: str, thread: str = "factory", user: str = "carol"
) -> httpx.Response:
    resp = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/chat", {"thread": thread, "body": body}, user=user
    )
    assert resp.status_code == 201, resp.text
    return resp


@pytest.mark.parametrize(
    ("thread", "body", "found"),
    [
        (
            "factory",
            "hunch: teams review drafts together (sample)",
            ("hunch", "teams review drafts together (sample)"),
        ),
        ("project:x", "I think people leave because builds fail (sample)", ("hunch", None)),
        ("factory", "We need a way to share drafts (sample)", ("request", None)),
        (
            "factory",
            "@atlas can we add an export? (sample)",
            ("request", "can we add an export? (sample)"),
        ),
        ("project:x", "we need this today (sample)", None),
        ("factory", "How is the build going? (sample)", None),
    ],
)
def test_which_messages_are_hunches(thread: str, body: str, found: tuple | None) -> None:
    out = team_signal(thread, body)

    if found is None:
        assert out is None
    else:
        assert out is not None and out[0] == found[0]
        assert out[1] == (found[1] or body)


async def test_a_room_hunch_and_a_request_become_team_signals(cockpit: Cockpit) -> None:
    await say(cockpit, "hunch: people want to show drafts before publishing (sample)")
    await say(cockpit, "We need a private preview for clients (sample)", user="alice")
    await say(cockpit, "Thanks, looks good (sample)")

    rows = cockpit.store.list_signals(cockpit.fid)
    assert [(r.kind, r.source, r.author) for r in rows] == [
        ("hunch", "team", "alice"),
        ("hunch", "team", "carol"),
    ]
    assert rows[1].excerpt == "people want to show drafts before publishing (sample)"
    added = cockpit.events("hunch.added")
    assert [e.data["request"] for e in added] == [False, True]
    assert all(e.data["from"] == "room" for e in added)


async def test_nothing_is_kept_while_discovery_or_the_team_source_is_off(cockpit: Cockpit) -> None:
    team = {"id": "people", "label": "Factory people (sample)", "kind": "team", "state": "paused"}
    definition.set_values(
        cockpit.deps, cockpit.fid, {"discovery.sources": [*SOURCES, team]}, created_by="alice"
    )
    definition.invalidate(cockpit.fid)
    await say(cockpit, "hunch: paused source (sample)")

    definition.set_values(
        cockpit.deps,
        cockpit.fid,
        {"discovery.sources": list(SOURCES), "discovery.enabled": False},
        created_by="alice",
    )
    definition.invalidate(cockpit.fid)
    await say(cockpit, "hunch: discovery off (sample)")

    assert cockpit.store.count_signals(cockpit.fid) == 0


async def test_a_listed_team_source_names_the_signals(cockpit: Cockpit) -> None:
    team = {"id": "people", "label": "Factory people (sample)", "kind": "team", "state": "active"}
    definition.set_values(
        cockpit.deps, cockpit.fid, {"discovery.sources": [*SOURCES, team]}, created_by="alice"
    )
    definition.invalidate(cockpit.fid)

    await say(cockpit, "hunch: a listed team source (sample)")

    [row] = cockpit.store.list_signals(cockpit.fid)
    assert row.source == "people" and row.source_kind == "team"
