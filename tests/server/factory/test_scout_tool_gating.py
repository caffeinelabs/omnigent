"""Who gets the discovery tools, and who the server lets use them.

On the runner, ``executor.config.factory_role: scout`` registers Scout's
surface (the four ``sys_discovery_*`` tools, the roster, its beacon, the
rules, asks, the Room and proposals) and nothing that writes code, PRs,
projects, tasks or messages; leads also read discovery and file ideas;
nobody else sees a discovery tool. On the server, the seat comes from the
caller's own session labels: Scout reads, codes and shapes; a lead reads and
files ideas; a project lead or a sub-agent gets nothing. A sub-agent that
runs Scout's spec is an ``ic``. Every product word is sample data.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.runner.factory_dispatch import execute_factory_tool
from omnigent.server.factory.discovery import ledger
from omnigent.server.factory.membership import sub_agent_role
from omnigent.tools.builtins.factory import (
    DISCOVERY_OPPORTUNITY_OPS,
    DISCOVERY_READ_KINDS,
    DISCOVERY_TOOL_NAMES,
    FACTORY_TOOL_NAMES,
    SIGNAL_FORCES,
    SIGNAL_INTENSITIES,
    SIGNAL_LABEL_KINDS,
)
from omnigent.tools.manager import ToolManager
from tests.runner.factory_fakes import FakeServer, factory_spec
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.discovery_helpers import discovery_cockpit, ingest
from tests.server.factory.source_helpers import add_scout

SCOUT_SURFACE = {
    "sys_discovery_read",
    "sys_discovery_query",
    "sys_discovery_code",
    "sys_discovery_opportunity",
    "sys_factory_roster",
    "sys_factory_set",
    "sys_factory_rules",
    "sys_factory_ask",
    "sys_factory_chat",
    "sys_factory_propose",
}


def _factory_tools(role: str | None) -> set[str]:
    names = set(ToolManager(factory_spec(role)).get_tool_names())
    return names & (FACTORY_TOOL_NAMES | {"sys_session_send", "sys_session_create"})


# ── The runner's surface ───────────────────────────────────────


def test_scout_gets_its_surface_and_nothing_that_writes() -> None:
    assert _factory_tools("scout") == SCOUT_SURFACE


def test_leads_read_discovery_and_file_ideas() -> None:
    lead = _factory_tools("lead")
    assert {"sys_discovery_read", "sys_discovery_opportunity"} <= lead
    assert not {"sys_discovery_query", "sys_discovery_code"} & lead


@pytest.mark.parametrize(
    "role", ["project_lead", "ic", "reviewer", "env_keeper", "demo", "planner"]
)
def test_nobody_else_sees_a_discovery_tool(role: str) -> None:
    assert not _factory_tools(role) & DISCOVERY_TOOL_NAMES


def test_discovery_tools_ride_the_factory_dispatch() -> None:
    assert DISCOVERY_TOOL_NAMES <= FACTORY_TOOL_NAMES


def test_tool_vocabulary_matches_the_ledger() -> None:
    from omnigent.server.factory.discovery import agent

    assert set(SIGNAL_LABEL_KINDS) == ledger.LABEL_KINDS
    assert SIGNAL_INTENSITIES == ledger.INTENSITIES
    assert SIGNAL_FORCES == ledger.FORCES
    assert DISCOVERY_READ_KINDS == agent.READ_KINDS
    assert set(DISCOVERY_OPPORTUNITY_OPS) == set(
        agent.OpportunityIn.model_fields["op"].annotation.__args__  # type: ignore[union-attr]
    )


def test_a_sub_agent_running_scouts_spec_is_an_ic() -> None:
    assert sub_agent_role(factory_spec("scout")) == F.ROLE_IC
    assert F.ROLE_SCOUT in F.SEAT_ROLES and F.ROLE_SCOUT not in F.SPAWN_ROLES


@pytest.mark.parametrize(
    ("tool", "path"),
    [
        ("sys_discovery_read", "/v1/factory/agent/discovery/read"),
        ("sys_discovery_query", "/v1/factory/agent/discovery/query"),
        ("sys_discovery_code", "/v1/factory/agent/discovery/code"),
        ("sys_discovery_opportunity", "/v1/factory/agent/discovery/opportunity"),
    ],
)
async def test_each_tool_posts_to_its_agent_route(tool: str, path: str) -> None:
    server = FakeServer().on("POST", path, body={"ok": True})
    async with server.client() as client:
        out = await execute_factory_tool(
            tool,
            # A session id in the arguments never replaces the caller's.
            {"kind": "profile", "from_session_id": "someone-else"},
            server_client=client,
            conversation_id="conv_scout",
        )
    assert json.loads(out) == {"ok": True}
    [call] = server.requests
    assert call.path == path and call.body["from_session_id"] == "conv_scout"


async def test_a_refusal_reaches_the_agent_with_its_code() -> None:
    server = FakeServer().on(
        "POST",
        "/v1/factory/agent/discovery/read",
        status=404,
        body={"error": {"code": "discovery_off", "message": "discovery is off"}},
    )
    async with server.client() as client:
        out = json.loads(
            await execute_factory_tool(
                "sys_discovery_read",
                {"kind": "summary"},
                server_client=client,
                conversation_id="c",
            )
        )
    assert out["error"] == "discovery_off" and out["status"] == 404


# ── The server's role checks ───────────────────────────────────


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in discovery_cockpit(world, db_uri, monkeypatch):
        yield value


async def _call(cockpit: Cockpit, route: str, session_id: str, **body: object) -> object:
    return await cockpit.client.post(
        f"/v1/factory/agent/discovery/{route}",
        json={"from_session_id": session_id, **body},
        headers={"X-Test-User": "alice"},
    )


async def test_scout_reads_codes_and_shapes(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    scout = add_scout(cockpit, monkeypatch)
    [signal] = ingest(cockpit, "repo-issues", ["Exports time out on big projects (sample)"])

    read = await _call(cockpit, "read", scout, kind="unsorted")
    assert read.status_code == 200, read.text  # type: ignore[attr-defined]
    coded = await _call(
        cockpit, "code", scout, label=[{"signal": signal.ref, "intensity": "blocker"}]
    )
    assert coded.status_code == 200, coded.text  # type: ignore[attr-defined]
    assert coded.json()["labelled"] == [signal.ref]  # type: ignore[attr-defined]


@pytest.mark.parametrize("key", ["lead_a", "lead_b"])
async def test_a_lead_reads_and_files_ideas_only(cockpit: Cockpit, key: str) -> None:
    lead = cockpit.world.id(key)
    [signal] = ingest(cockpit, "repo-issues", ["Exports time out on big projects (sample)"])

    read = await _call(cockpit, "read", lead, kind="signals", ids=[signal.ref])
    assert read.status_code == 200, read.text  # type: ignore[attr-defined]
    code = await _call(cockpit, "code", lead, label=[{"signal": signal.ref, "kind": "pain"}])
    assert code.status_code == 403 and error_code(code) == "role_not_allowed"  # type: ignore[arg-type,attr-defined]
    shape = await _call(cockpit, "opportunity", lead, op="rephrase", id="O-1", title="x")
    assert shape.status_code == 403 and error_code(shape) == "role_not_allowed"  # type: ignore[arg-type,attr-defined]
    idea = await _call(
        cockpit, "opportunity", lead, op="idea", text="A guided first run might help (sample)"
    )
    assert idea.status_code == 200, idea.text  # type: ignore[attr-defined]


@pytest.mark.parametrize("key", ["pl", "ic", "reviewer", "helper", "plain", "other_lead"])
async def test_project_teams_and_strangers_get_nothing(cockpit: Cockpit, key: str) -> None:
    resp = await _call(cockpit, "read", cockpit.world.id(key), kind="summary")
    assert resp.status_code in (403, 404), (key, resp.text)  # type: ignore[attr-defined]
    if key in ("pl", "ic", "reviewer", "helper"):
        assert error_code(resp) == "role_not_allowed"  # type: ignore[arg-type]


async def test_every_route_is_dark_while_discovery_is_off(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    scout = add_scout(cockpit, monkeypatch)
    monkeypatch.delenv("OMNIGENT_FACTORY_DISCOVERY")
    for route, body in (
        ("read", {"kind": "profile"}),
        ("code", {"label": [{"signal": "S-1"}]}),
        ("opportunity", {"op": "idea", "text": "x"}),
    ):
        resp = await _call(cockpit, route, scout, **body)
        assert resp.status_code == 404 and error_code(resp) == "discovery_off", route  # type: ignore[arg-type,attr-defined]
