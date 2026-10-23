"""Registration and relay surface of the software-factory tools.

The four factory tools are an opt-in surface: they appear only for specs that
declare ``executor.config.factory_role``, and ``sys_factory_spawn`` (a new
top-level sandbox) only for leads. Native harnesses see nothing but the relay,
so the tools must also ride ``build_native_relay_tool_schemas`` — that is what
puts ``mcp__omnigent__sys_session_post`` in front of Claude Code.
"""

from __future__ import annotations

import json

import pytest

from omnigent.runner.tool_dispatch import (
    _ALL_LOCAL_TOOLS,
    _FACTORY_TOOLS,
    _NATIVE_RELAY_BUILTIN_TOOLS,
    build_native_relay_tool_schemas,
    execute_tool,
    should_dispatch_locally,
)
from omnigent.tools.builtins.factory import (
    FACTORY_TOOL_NAMES,
    SysFactoryRosterTool,
    SysFactorySetTool,
    SysFactorySpawnTool,
    SysSessionPostTool,
)
from omnigent.tools.manager import ToolManager
from tests.runner.factory_fakes import LEAD, FakeServer, factory_spec, member, roster

_NON_SPAWN = {"sys_session_post", "sys_factory_roster", "sys_factory_set"}
_ALL = _NON_SPAWN | {"sys_factory_spawn"}


def _registered(role: str | None, **config: object) -> set[str]:
    return set(ToolManager(factory_spec(role, **config)).get_tool_names()) & _ALL


def test_no_factory_tools_without_factory_role() -> None:
    assert _registered(None) == set()


def test_unknown_factory_role_registers_nothing() -> None:
    assert _registered("ceo") == set()


@pytest.mark.parametrize("role", ["project_lead", "ic", "env_keeper", "reviewer"])
def test_non_lead_roles_get_post_roster_set_but_not_spawn(role: str) -> None:
    assert _registered(role) == _NON_SPAWN


def test_lead_also_gets_spawn() -> None:
    assert _registered("lead", factory_max_pods=4) == _ALL


def test_factory_tools_are_runner_dispatched_and_relayed() -> None:
    assert FACTORY_TOOL_NAMES == _ALL
    assert _FACTORY_TOOLS == FACTORY_TOOL_NAMES
    assert _FACTORY_TOOLS <= _NATIVE_RELAY_BUILTIN_TOOLS
    assert _FACTORY_TOOLS <= _ALL_LOCAL_TOOLS
    assert all(should_dispatch_locally(name) for name in _ALL)


def test_claude_native_relay_includes_factory_tools_for_a_lead() -> None:
    schemas = build_native_relay_tool_schemas(factory_spec("lead"))
    relayed = {s["name"]: s for s in schemas if s["name"] in _ALL}

    assert set(relayed) == _ALL
    for schema in relayed.values():
        # The bridges register the flat {name, description, parameters} shape.
        assert schema["description"]
        assert schema["parameters"]["type"] == "object"
    assert relayed["sys_session_post"]["parameters"]["required"] == ["to", "message"]


def test_claude_native_relay_for_a_project_lead_omits_spawn() -> None:
    names = {s["name"] for s in build_native_relay_tool_schemas(factory_spec("project_lead"))}
    assert names & _ALL == _NON_SPAWN


def test_claude_native_relay_without_factory_role_has_no_factory_tools() -> None:
    names = {s["name"] for s in build_native_relay_tool_schemas(factory_spec(None))}
    assert not names & _ALL
    # The pre-existing session surface is unchanged.
    assert {"sys_session_list", "sys_session_get_history"} <= names


def test_post_description_teaches_aliases_async_delivery_and_send() -> None:
    text = SysSessionPostTool.description()
    for alias in ("'parent'", "'lead'", "'peer_lead'", "'supervisor'", "'project:<slug>'"):
        assert alias in text
    assert "ASYNCHRONOUS" in text
    assert "queued" in text
    assert "reply arrives later as a new message" in text
    assert "sys_session_send" in text


def test_schemas_use_the_shared_vocabulary() -> None:
    post = SysSessionPostTool().get_schema()["function"]["parameters"]["properties"]
    assert post["kind"]["enum"] == ["alert", "brief", "info", "reply", "request"]
    spawn = SysFactorySpawnTool().get_schema()["function"]["parameters"]
    assert spawn["properties"]["role"]["enum"] == ["project_lead", "lead"]
    assert spawn["required"] == ["agent", "title", "message"]
    fields = SysFactorySetTool().get_schema()["function"]["parameters"]["properties"]
    assert fields["state"]["enum"] == ["active", "done", "paused"]
    assert fields["health"]["enum"] == ["blocked", "dead", "degraded", "ok"]
    assert SysFactoryRosterTool().get_schema()["function"]["parameters"]["properties"] == {}


async def test_execute_tool_routes_factory_tools_to_factory_dispatch() -> None:
    server = FakeServer().on("GET", "/v1/factory/roster", body=roster(member(LEAD, role="lead")))
    async with server.client() as client:
        out = await execute_tool(
            tool_name="sys_factory_roster",
            arguments="{}",
            server_client=client,
            agent_spec=factory_spec("lead"),
            conversation_id=LEAD,
        )
    assert json.loads(out)["members"][0]["session_id"] == LEAD
    [call] = server.requests
    assert (call.method, call.path, call.params) == (
        "GET",
        "/v1/factory/roster",
        {"from_session_id": LEAD},
    )
