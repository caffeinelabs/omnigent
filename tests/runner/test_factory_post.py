"""``sys_session_post``: the factory SendMessage.

The runner proxies ``POST /v1/factory/messages``. It always stamps the calling
session as ``from_session_id`` (the server additionally binds it to the
runner's tunnel token), validates locally what it can, and passes the server's
answer — ``202 queued`` or a typed error — straight back to the agent.
"""

from __future__ import annotations

import json

import httpx
import pytest

from omnigent.factory import MESSAGE_MAX_CHARS
from omnigent.runner.factory_dispatch import execute_factory_tool
from tests.runner.factory_fakes import LEAD, PEER, FakeServer

_PATH = "/v1/factory/messages"
_QUEUED = {"mail_id": "mail_1", "to_session_id": PEER, "status": "queued"}


async def _post(server: FakeServer, args: dict[str, object], caller: str = LEAD) -> dict:
    async with server.client() as client:
        out = await execute_factory_tool(
            "sys_session_post", args, server_client=client, conversation_id=caller
        )
    return json.loads(out)


async def test_request_body_is_exactly_the_contract() -> None:
    server = FakeServer().on("POST", _PATH, status=202, body=_QUEUED)

    result = await _post(server, {"to": "peer_lead", "message": "status?", "kind": "request"})

    assert result == _QUEUED
    [call] = server.requests
    assert (call.method, call.path) == ("POST", _PATH)
    assert call.body == {
        "from_session_id": LEAD,
        "to": "peer_lead",
        "message": "status?",
        "kind": "request",
        "restart": False,
    }


async def test_kind_defaults_to_info_and_restart_passes_through() -> None:
    server = FakeServer().on("POST", _PATH, status=202, body=_QUEUED)

    await _post(server, {"to": " project:api-version ", "message": "wake up", "restart": True})

    body = server.requests[0].body
    assert body["kind"] == "info"
    assert body["restart"] is True
    assert body["to"] == "project:api-version"


async def test_sender_is_stamped_from_the_caller_not_the_arguments() -> None:
    server = FakeServer().on("POST", _PATH, status=202, body=_QUEUED)

    await _post(
        server,
        {"to": PEER, "message": "hi", "from_session_id": "conv_spoofed"},
        caller="conv_ic_7",
    )

    assert server.requests[0].body["from_session_id"] == "conv_ic_7"


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (403, {"error": "session_out_of_factory"}, "session_out_of_factory"),
        (403, {"detail": {"error": "restart_not_allowed"}}, "restart_not_allowed"),
        (
            403,
            {"error": {"code": "permission_denied", "message": "sender_not_bound"}},
            "sender_not_bound",
        ),
        (400, {"detail": "unknown_target"}, "unknown_target"),
        (429, {"error": "rate_limited", "retry_after_s": 60}, "rate_limited"),
        (429, {"detail": "too many"}, "rate_limited"),
        (413, {"detail": "body too large"}, "message_too_large"),
    ],
)
async def test_server_errors_pass_through_as_typed_codes(
    status: int, body: dict[str, object], code: str
) -> None:
    server = FakeServer().on("POST", _PATH, status=status, body=body)

    result = await _post(server, {"to": "conv_other", "message": "hello"})

    assert result["error"] == code
    assert result["status"] == status


async def test_rate_limit_extra_fields_survive() -> None:
    server = FakeServer().on(
        "POST", _PATH, status=429, body={"error": "rate_limited", "retry_after_s": 60}
    )

    result = await _post(server, {"to": PEER, "message": "again"})

    assert result == {"error": "rate_limited", "status": 429, "retry_after_s": 60}


async def test_non_json_server_error_keeps_a_readable_message() -> None:
    def _html(_req: object) -> httpx.Response:
        return httpx.Response(502, text="<html>bad gateway</html>")

    server = FakeServer().on("POST", _PATH, handler=_html)

    result = await _post(server, {"to": PEER, "message": "hello"})

    assert result["error"] == "sys_session_post returned 502"
    assert "bad gateway" in result["message"]


async def test_transport_failure_becomes_a_tool_error() -> None:
    def _boom(_req: object) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    server = FakeServer().on("POST", _PATH, handler=_boom)

    result = await _post(server, {"to": PEER, "message": "hello"})

    assert result["error"].startswith("sys_session_post failed")


@pytest.mark.parametrize(
    ("args", "fragment"),
    [
        ({"message": "no target"}, "'to'"),
        ({"to": "  ", "message": "blank target"}, "'to'"),
        ({"to": PEER}, "'message'"),
        ({"to": PEER, "message": "x", "kind": "shout"}, "'kind'"),
        ({"to": PEER, "message": "x", "restart": "yes"}, "'restart'"),
    ],
)
async def test_invalid_arguments_never_reach_the_server(
    args: dict[str, object], fragment: str
) -> None:
    server = FakeServer()

    result = await _post(server, args)

    assert fragment in result["error"]
    assert server.requests == []


async def test_oversized_message_is_refused_locally() -> None:
    server = FakeServer()

    result = await _post(server, {"to": PEER, "message": "x" * (MESSAGE_MAX_CHARS + 1)})

    assert result["error"] == "message_too_large"
    assert result["max_chars"] == MESSAGE_MAX_CHARS
    assert server.requests == []


async def test_requires_server_client_and_caller() -> None:
    no_client = await execute_factory_tool(
        "sys_session_post", {"to": PEER, "message": "x"}, server_client=None, conversation_id=LEAD
    )
    assert "requires server access" in json.loads(no_client)["error"]
    async with FakeServer().client() as client:
        no_caller = await execute_factory_tool(
            "sys_session_post",
            {"to": PEER, "message": "x"},
            server_client=client,
            conversation_id=None,
        )
    assert "requires a session id" in json.loads(no_caller)["error"]
