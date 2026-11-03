"""The gateway's scope: only the server reads sources, for the caller's own factory and seat.

The factory and the seat come from the labels the server stamped on the
caller's session, never from what it sends. A wrong factory, a wrong role, a
paused or held source, a kind that needs a secret, a tool the source does not
offer (or one that looks like a write), bad arguments and an exhausted daily
cap are all refused before any source is reached, and each refusal of a
known source is logged. Every product word is sample data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi.routing import APIRoute

from omnigent.server.factory import definition
from omnigent.server.factory.api import discovery_sources as routes
from omnigent.server.factory.definition_blocks import DISCOVERY_ENV
from omnigent.server.factory.discovery import gateway, sources
from omnigent.server.factory.origin import require_human_origin
from tests.examples.factory_sample_tokens import FORBIDDEN_SAMPLE_PATTERN
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.discovery_helpers import base, discovery_cockpit
from tests.server.factory.source_helpers import (
    REPO_SOURCE,
    SAMPLE_REPO,
    TEAM_SOURCE,
    FakeGitHub,
    add_scout,
    install_backends,
    issue,
    query,
    set_sources,
    source_events,
)


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in discovery_cockpit(world, db_uri, monkeypatch):
        set_sources(value, REPO_SOURCE, TEAM_SOURCE)
        gateway.reset()
        yield value


def github(cockpit: Cockpit) -> FakeGitHub:
    fake = FakeGitHub(repos={SAMPLE_REPO: {"issues": [issue(1, "Sharing is hard (sample)")]}})
    install_backends(cockpit, github=fake)
    return fake


async def test_a_lead_reads_an_allowed_source_and_the_read_is_logged(cockpit: Cockpit) -> None:
    fake = github(cockpit)

    resp = await query(cockpit, "repo-issues", "issues", {"limit": 5})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [i["signal"] for i in body["items"]] == ["S-1"]
    assert body["count"] == {"returned": 1, "new": 1, "flagged": 0}
    assert len(fake.calls) == 1
    (event,) = source_events(cockpit)
    assert event.subject == "source:repo-issues"
    assert event.id == body["query_event"]
    assert event.data["outcome"] == "ok" and event.data["seat"] == "lead"
    assert event.data["by"] == cockpit.world.id("lead_a") and event.data["by_kind"] == "agent"
    assert event.data["signals"] == ["S-1"] and event.data["calls"] == 1
    assert event.data["args_digest"] and "limit" not in event.data
    assert event.data["cost_usd"] == 0.0 and isinstance(event.data["duration_ms"], int)
    # The stored signal names the query that fetched it.
    signal = cockpit.store.get_signal(cockpit.fid, 1)
    assert signal is not None and signal.query_event == event.id


async def test_the_factory_comes_from_the_callers_labels_never_the_body(cockpit: Cockpit) -> None:
    fake = github(cockpit)

    # A lead of another factory names this factory's source: its own factory has none.
    other = await query(cockpit, "repo-issues", "issues", key="other_lead")
    assert other.status_code == 404 and error_code(other) == "discovery_off"
    # A body naming a factory is refused outright.
    named = await query(cockpit, "repo-issues", "issues", factory_id=cockpit.fid)
    assert named.status_code == 400 and error_code(named) == "invalid_body"
    assert fake.calls == [] and source_events(cockpit) == []


async def test_a_session_outside_any_factory_is_refused(cockpit: Cockpit) -> None:
    fake = github(cockpit)

    resp = await query(cockpit, "repo-issues", "issues", key="plain")

    assert resp.status_code == 403
    assert fake.calls == [] and source_events(cockpit) == []


@pytest.mark.parametrize("key", ["pl", "ic", "reviewer", "helper"])
async def test_members_without_a_seat_are_refused_and_logged(cockpit: Cockpit, key: str) -> None:
    fake = github(cockpit)

    resp = await query(cockpit, "repo-issues", "issues", key=key)

    assert resp.status_code == 403 and error_code(resp) == "role_not_allowed"
    assert fake.calls == []
    (event,) = source_events(cockpit)
    assert event.data["outcome"] == "refused" and event.data["error_code"] == "role_not_allowed"


async def test_a_seat_the_source_does_not_list_is_refused(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = github(cockpit)
    set_sources(cockpit, {**REPO_SOURCE, "roles": ["scout"]})
    scout = add_scout(cockpit, monkeypatch)

    lead = await query(cockpit, "repo-issues", "issues")
    seat = await query(cockpit, "repo-issues", "issues", session_id=scout)

    assert lead.status_code == 403 and error_code(lead) == "role_not_allowed"
    assert seat.status_code == 200, seat.text
    assert len(fake.calls) == 1
    events = source_events(cockpit)
    assert [(e.data["seat"], e.data["outcome"]) for e in events] == [
        ("lead", "refused"),
        ("scout", "ok"),
    ]


async def test_a_paused_source_is_refused(cockpit: Cockpit) -> None:
    fake = github(cockpit)
    set_sources(cockpit, {**REPO_SOURCE, "state": "paused"})

    resp = await query(cockpit, "repo-issues", "issues")

    assert resp.status_code == 409 and error_code(resp) == "source_paused"
    assert fake.calls == []


async def test_a_source_on_hold_is_refused(cockpit: Cockpit) -> None:
    fake = github(cockpit)
    cockpit.store.put_source_state(
        cockpit.fid, "repo-issues", data={sources.HOLD_KEY: {"at": 1, "reason": "x"}}
    )

    resp = await query(cockpit, "repo-issues", "issues")

    assert resp.status_code == 409 and error_code(resp) == "source_failing"
    assert fake.calls == []


async def test_over_budget_is_refused_and_the_cap_is_per_day(cockpit: Cockpit) -> None:
    fake = github(cockpit)
    set_sources(cockpit, {**REPO_SOURCE, "budget": {"calls_per_day": 2}})

    first = await query(cockpit, "repo-issues", "issues")
    second = await query(cockpit, "repo-issues", "releases")
    third = await query(cockpit, "repo-issues", "issues")

    assert first.status_code == second.status_code == 200
    assert third.status_code == 429 and error_code(third) == "over_budget"
    assert len(fake.calls) == 2
    assert [e.data["outcome"] for e in source_events(cockpit)] == ["ok", "ok", "refused"]
    # Yesterday's calls do not count today.
    cockpit.store.put_source_state(cockpit.fid, "repo-issues", usage_day="2020-01-01")
    assert (await query(cockpit, "repo-issues", "issues")).status_code == 200


async def test_a_kind_that_needs_a_secret_is_refused(cockpit: Cockpit) -> None:
    install_backends(cockpit)
    set_sources(
        cockpit,
        {
            "id": "metrics",
            "label": "Product analytics (sample)",
            "kind": "analytics",
            "roles": ["scout", "lead"],
            "state": "active",
        },
    )

    resp = await query(cockpit, "metrics", "query")

    assert resp.status_code == 422 and error_code(resp) == "kind_not_supported"


@pytest.mark.parametrize(
    ("tool", "code"),
    [
        ("create_issue", "tool_not_allowed"),
        ("comment", "tool_not_allowed"),
        ("fetch", "tool_not_allowed"),
        ("nope", "tool_not_allowed"),
    ],
)
async def test_tools_outside_the_allowlist_are_refused(
    cockpit: Cockpit, tool: str, code: str
) -> None:
    fake = github(cockpit)

    resp = await query(cockpit, "repo-issues", tool)

    assert resp.status_code == 403 and error_code(resp) == code
    assert set(resp.json()["tools"]) == {"issues", "discussions", "releases", "search"}
    assert fake.calls == []


async def test_params_narrow_the_tools_and_a_listed_write_is_still_refused(
    cockpit: Cockpit,
) -> None:
    github(cockpit)
    params = {**REPO_SOURCE["params"], "include": ["issues"], "tools": ["issues", "post_reply"]}
    set_sources(cockpit, {**REPO_SOURCE, "params": params})

    search = await query(cockpit, "repo-issues", "search", {"q": "sharing"})
    write = await query(cockpit, "repo-issues", "post_reply")
    ok = await query(cockpit, "repo-issues", "issues")

    assert error_code(search) == "tool_not_allowed"
    assert error_code(write) == "tool_not_allowed" and "write" in write.json()["error"]["message"]
    assert ok.status_code == 200


async def test_bad_arguments_are_refused_before_any_call_counts(cockpit: Cockpit) -> None:
    fake = github(cockpit)

    extra = await query(cockpit, "repo-issues", "issues", {"limit": 5, "owner": "x"})
    big = await query(cockpit, "repo-issues", "issues", {"limit": 500})
    outside = await query(cockpit, "repo-issues", "issues", {"repo": "sample-org/private-repo"})

    assert error_code(extra) == error_code(big) == "invalid_args"
    assert outside.status_code == 403 and error_code(outside) == "repo_not_allowed"
    assert fake.calls == []
    state = cockpit.store.get_source_state(cockpit.fid, "repo-issues")
    assert state is None or state.calls_today == 0


async def test_an_unknown_source_is_404(cockpit: Cockpit) -> None:
    github(cockpit)

    resp = await query(cockpit, "nope", "issues")

    assert resp.status_code == 404 and error_code(resp) == "unknown_source"


async def test_everything_is_dark_unless_discovery_is_on(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = github(cockpit)
    definition.set_values(
        cockpit.deps, cockpit.fid, {"discovery.enabled": False}, created_by="alice"
    )
    definition.invalidate(cockpit.fid)

    off = await query(cockpit, "repo-issues", "issues")
    listing = await cockpit.client.get(
        "/v1/factory/agent/discovery/sources",
        params={"from_session_id": cockpit.world.id("lead_a")},
        headers={"X-Test-User": "alice"},
    )
    log = await cockpit.client.get(
        base(cockpit, "/sources/repo-issues/log"), headers={"X-Test-User": "alice"}
    )

    assert error_code(off) == error_code(listing) == error_code(log) == "discovery_off"
    definition.set_values(cockpit.deps, cockpit.fid, {"discovery.enabled": True}, created_by="a")
    definition.invalidate(cockpit.fid)
    monkeypatch.delenv(DISCOVERY_ENV)
    assert error_code(await query(cockpit, "repo-issues", "issues")) == "discovery_off"
    assert fake.calls == []


async def test_the_source_list_shows_only_the_callers_sources_and_their_tools(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_backends(cockpit)
    set_sources(cockpit, {**REPO_SOURCE, "roles": ["scout"]}, TEAM_SOURCE)
    scout = add_scout(cockpit, monkeypatch)

    async def listing(session_id: str) -> dict:
        resp = await cockpit.client.get(
            "/v1/factory/agent/discovery/sources",
            params={"from_session_id": session_id},
            headers={"X-Test-User": "alice"},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    lead = await listing(cockpit.world.id("lead_a"))
    seat = await listing(scout)
    pl = await cockpit.client.get(
        "/v1/factory/agent/discovery/sources",
        params={"from_session_id": cockpit.world.id("pl")},
        headers={"X-Test-User": "alice"},
    )

    assert lead["seat"] == "lead" and [s["id"] for s in lead["sources"]] == ["team"]
    assert seat["seat"] == "scout" and [s["id"] for s in seat["sources"]] == [
        "repo-issues",
        "team",
    ]
    repo = seat["sources"][0]
    assert repo["usable"] is True and repo["calls_per_day"] == 50
    assert [t["name"] for t in repo["tools"]] == ["issues", "discussions", "releases", "search"]
    search = repo["tools"][3]
    assert search["args"]["q"]["required"] is True and search["args"]["q"]["maxLength"] == 200
    assert pl.status_code == 403 and error_code(pl) == "role_not_allowed"


def test_every_person_write_route_requires_human_origin() -> None:
    people = [
        route
        for route in routes.router.routes
        if isinstance(route, APIRoute)
        and route.methods & {"POST", "PUT", "PATCH", "DELETE"}
        and "/agent/" not in route.path
    ]
    assert {r.path.rsplit("/", 1)[-1] for r in people} == {"test", "pause", "resume"}
    for route in people:
        calls = [dep.call for dep in route.dependant.dependencies]
        assert require_human_origin in calls, route.path


async def test_people_cannot_reach_the_agents_gateway_without_a_member_session(
    cockpit: Cockpit,
) -> None:
    fake = github(cockpit)

    resp = await cockpit.client.post(
        "/v1/factory/agent/discovery/query",
        json={"source": "repo-issues", "tool": "issues"},
        headers={"X-Test-User": "alice"},
    )

    assert resp.status_code == 400 and error_code(resp) == "invalid_body"
    assert fake.calls == []


def test_the_gateway_code_names_no_product() -> None:
    from pathlib import Path

    import omnigent.server.factory as factory_pkg

    root = Path(factory_pkg.__file__).parent
    files = [
        root / "discovery" / "sources.py",
        root / "discovery" / "gateway.py",
        root / "discovery" / "web_io.py",
        *sorted((root / "discovery" / "kinds").glob("*.py")),
        root / "api" / "discovery_sources.py",
        root / "background" / "discovery_sync.py",
    ]
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert not FORBIDDEN_SAMPLE_PATTERN.search(text), path
