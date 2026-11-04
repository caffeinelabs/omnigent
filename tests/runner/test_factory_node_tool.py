"""``sys_factory_node``: one node of the cockpit's work tree, read by an agent.

The runner proxies ``GET /v1/factory/agent/node`` with the caller stamped; a
server from before node pages has no such route, and its plain 404 becomes
``not_served`` so the agent carries on with the header its Room messages
carry. Argument errors never reach the server.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from omnigent.runner.factory_dispatch import execute_factory_tool
from omnigent.tools.builtins.factory import SYS_FACTORY_NODE, SysFactoryNodeTool
from tests.runner.factory_fakes import FakeServer

PL = "conv_pl"
PATH = "/v1/factory/agent/node"


async def _call(server: FakeServer, args: dict[str, Any]) -> dict[str, Any]:
    async with server.client() as client:
        out = await execute_factory_tool(
            SYS_FACTORY_NODE, args, server_client=client, conversation_id=PL
        )
    return json.loads(out)


def test_the_schema_reads_one_node() -> None:
    schema = SysFactoryNodeTool().get_schema()["function"]
    params = schema["parameters"]

    assert schema["name"] == "sys_factory_node"
    assert params["required"] == ["ref"]
    assert params["properties"]["op"]["enum"] == ["read"]
    assert "sys_factory_project" in schema["description"]


async def test_read_proxies_the_node_page_with_the_caller() -> None:
    page = {"node": {"ref": "T-16"}, "status": {"kind": "needs"}}
    server = FakeServer().on("GET", PATH, body=page)

    out = await _call(server, {"op": "read", "ref": " T-16 ", "children_page": "60"})

    assert out == page
    [call] = server.requests
    assert call.params == {"from_session_id": PL, "ref": "T-16", "children_page": "60"}


async def test_an_older_server_answers_not_served() -> None:
    out = await _call(FakeServer(), {"ref": "product"})

    assert out["error"] == "not_served"
    assert "header" in out["message"]


async def test_an_unknown_node_keeps_its_code() -> None:
    server = FakeServer().on(
        "GET",
        PATH,
        status=404,
        body={"error": {"code": "unknown_node", "message": "no node 'T-404'"}},
    )

    out = await _call(server, {"ref": "T-404"})

    assert (out["error"], out["status"]) == ("unknown_node", 404)


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        ({"ref": ""}, "requires a 'ref'"),
        ({"ref": "x" * 201}, "requires a 'ref'"),
        ({"op": "write", "ref": "T-1"}, "'op' must be 'read'"),
        ({"ref": "T-1", "children_page": 60}, "children_page"),
    ],
)
async def test_bad_arguments_never_reach_the_server(args: dict[str, Any], needle: str) -> None:
    server = FakeServer()

    out = await _call(server, args)

    assert needle in out["error"]
    assert server.requests == []
