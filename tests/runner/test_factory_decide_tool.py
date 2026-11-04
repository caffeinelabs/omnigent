"""``sys_factory_decide`` and the decide-first ``class`` of ``sys_factory_ask``.

The runner proxies ``POST /v1/factory/agent/checkpoints`` with the caller
stamped; a server from before checkpoints has no such route, and its plain
404 becomes ``not_served`` (the agent asks with ``sys_factory_ask`` and its
choice recommended instead). Argument errors never reach the server.
"""

from __future__ import annotations

import json
from typing import Any

from omnigent.runner.factory_dispatch import execute_factory_tool
from omnigent.tools.builtins.factory import (
    SYS_FACTORY_ASK,
    SYS_FACTORY_DECIDE,
    SysFactoryAskTool,
    SysFactoryDecideTool,
)
from tests.runner.factory_fakes import FakeServer

PL = "conv_pl"
PATH = "/v1/factory/agent/checkpoints"


async def _call(server: FakeServer, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    async with server.client() as client:
        out = await execute_factory_tool(tool, args, server_client=client, conversation_id=PL)
    return json.loads(out)


def test_the_schema_records_a_choice_and_why() -> None:
    schema = SysFactoryDecideTool().get_schema()["function"]
    params = schema["parameters"]

    assert schema["name"] == "sys_factory_decide"
    assert params["required"] == ["question", "choice", "why"]
    assert {"class", "confidence", "node", "effects", "reversible"} <= set(params["properties"])
    assert "sys_factory_ask" in schema["description"]


def test_the_ask_tool_takes_a_class() -> None:
    params = SysFactoryAskTool().get_schema()["function"]["parameters"]

    assert params["properties"]["class"]["type"] == "string"
    assert "Tier 0" in params["properties"]["class"]["description"]


async def test_decide_posts_the_decision_with_the_caller() -> None:
    reply = {"status": "decided", "checkpoint": "K-1"}
    server = FakeServer().on("POST", PATH, body=reply)

    out = await _call(
        server,
        SYS_FACTORY_DECIDE,
        {
            "question": " Where does the tab go? ",
            "options": [{"label": "Next to Chat", "recommended": True}, {"label": "Under More"}],
            "choice": "Next to Chat",
            "why": "people look there first",
            "class": "ux",
            "confidence": 0.8,
            "effects": ["C-19", " "],
        },
    )

    assert out == reply
    [call] = server.requests
    assert call.body == {
        "from_session_id": PL,
        "question": "Where does the tab go?",
        "choice": "Next to Chat",
        "why": "people look there first",
        "options": [{"label": "Next to Chat"}, {"label": "Under More"}],
        "class": "ux",
        "confidence": 0.8,
        "reversible": True,
        "effects": ["C-19"],
    }


async def test_bad_arguments_never_reach_the_server() -> None:
    server = FakeServer()

    missing = await _call(server, SYS_FACTORY_DECIDE, {"question": "q", "choice": "a"})
    sure = await _call(
        server, SYS_FACTORY_DECIDE, {"question": "q", "choice": "a", "why": "w", "confidence": 2}
    )

    assert "why" in missing["error"]
    assert "confidence" in sure["error"]
    assert server.requests == []


async def test_an_older_server_answers_not_served() -> None:
    out = await _call(
        FakeServer(), SYS_FACTORY_DECIDE, {"question": "q", "choice": "a", "why": "w"}
    )

    assert out["error"] == "not_served"
    assert "sys_factory_ask" in out["message"]


async def test_the_ask_passes_its_class() -> None:
    server = FakeServer().on("POST", "/v1/factory/agent/decisions", body={"decision": "D-1"})

    await _call(server, SYS_FACTORY_ASK, {"question": "Where?", "class": " ux "})

    [call] = server.requests
    assert call.body["class"] == "ux"
