"""``sys_factory_review``: the independent done reviewer's verdict on a feature.

The runner proxies ``POST /v1/factory/agent/reviews`` with the caller stamped;
a server from before done reviews has no such route, and its plain 404
becomes ``not_served`` so the reviewer files the verdict with
``sys_factory_retro(target="review:I-n")`` as its brief says. Argument errors
never reach the server.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from omnigent.runner.factory_dispatch import execute_factory_tool
from omnigent.tools.builtins.factory import SYS_FACTORY_REVIEW, SysFactoryReviewTool
from tests.runner.factory_fakes import FakeServer

SENTINEL = "conv_sentinel"
PATH = "/v1/factory/agent/reviews"
VERDICT = {
    "initiative": "I-1",
    "review": 1,
    "verdict": "not_done",
    "summary": "Half the ask is parked (sample).",
    "gaps": [
        {
            "line": "Sales is not built",
            "severity": "high",
            "evidence": ["E1"],
            "follow_up": {"kind": "next_wave"},
        }
    ],
    "accepted": [{"evidence": "E2", "why": "an env limit (sample)"}],
}


async def _call(server: FakeServer, args: dict[str, Any]) -> dict[str, Any]:
    async with server.client() as client:
        out = await execute_factory_tool(
            SYS_FACTORY_REVIEW, args, server_client=client, conversation_id=SENTINEL
        )
    return json.loads(out)


def test_the_schema_returns_a_verdict_with_gaps_and_follow_ups() -> None:
    schema = SysFactoryReviewTool().get_schema()["function"]
    params = schema["parameters"]

    assert schema["name"] == "sys_factory_review"
    assert params["required"] == ["initiative", "verdict", "summary"]
    assert params["properties"]["verdict"]["enum"] == ["done", "done_with_follow_ups", "not_done"]
    gap = params["properties"]["gaps"]["items"]
    assert gap["required"] == ["line", "severity", "follow_up"]
    assert gap["properties"]["follow_up"]["properties"]["kind"]["enum"] == [
        "task",
        "project",
        "choice",
        "input",
        "next_wave",
        "none",
    ]
    assert "sys_factory_retro" in schema["description"] and "only propose" in schema["description"]


async def test_the_verdict_goes_to_the_server_with_the_caller() -> None:
    reply = {"initiative": "I-1", "review": 1, "verdict": "not_done", "gaps": []}
    server = FakeServer().on("POST", PATH, body=reply)

    out = await _call(server, {**VERDICT, "extra": "dropped"})

    assert out == reply
    [call] = server.requests
    assert call.body == {**VERDICT, "from_session_id": SENTINEL}


async def test_an_older_server_answers_not_served() -> None:
    out = await _call(FakeServer(), VERDICT)

    assert out["error"] == "not_served"
    assert 'target="review:<I-n>"' in out["message"]


async def test_a_refusal_keeps_its_code() -> None:
    server = FakeServer().on(
        "POST",
        PATH,
        status=403,
        body={"error": {"code": "not_independent", "message": "the planner never reviews"}},
    )

    out = await _call(server, VERDICT)

    assert (out["error"], out["status"]) == ("not_independent", 403)


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        ({**VERDICT, "initiative": ""}, "'initiative'"),
        ({**VERDICT, "verdict": None}, "'verdict'"),
        ({**VERDICT, "summary": "  "}, "'summary'"),
        ({**VERDICT, "gaps": "none"}, "'gaps' must be a list"),
    ],
)
async def test_argument_errors_never_reach_the_server(args: dict[str, Any], needle: str) -> None:
    server = FakeServer()

    out = await _call(server, args)

    assert needle in json.dumps(out)
    assert server.requests == []
