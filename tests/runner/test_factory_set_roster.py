"""``sys_factory_roster`` and ``sys_factory_set`` proxy the factory routes."""

from __future__ import annotations

import json

import pytest

from omnigent.factory import SUMMARY_MAX_LEN
from omnigent.runner.factory_dispatch import execute_factory_tool
from tests.runner.factory_fakes import LEAD, PEER, FakeServer, member, membership, roster


async def _call(server: FakeServer, tool: str, args: dict[str, object]) -> dict:
    async with server.client() as client:
        out = await execute_factory_tool(tool, args, server_client=client, conversation_id=LEAD)
    return json.loads(out)


async def test_roster_is_a_passthrough_get_with_the_sender() -> None:
    body = roster(member(LEAD, role="lead"), member(PEER, role="lead"))
    server = FakeServer().on("GET", "/v1/factory/roster", body=body)

    assert await _call(server, "sys_factory_roster", {}) == body
    assert server.requests[0].params == {"from_session_id": LEAD}


async def test_roster_error_passes_through() -> None:
    server = FakeServer().on(
        "GET", "/v1/factory/roster", status=403, body={"error": "sender_not_bound"}
    )

    result = await _call(server, "sys_factory_roster", {})

    assert result == {"error": "sender_not_bound", "status": 403}


async def test_set_patches_self_by_default() -> None:
    server = FakeServer().on(
        "PATCH", f"/v1/factory/sessions/{LEAD}", body=membership(LEAD, role="lead")
    )

    result = await _call(
        server, "sys_factory_set", {"summary": "2 projects", "health": "ok", "heartbeat_s": 300}
    )

    assert result["session_id"] == LEAD
    [call] = server.requests
    assert call.method == "PATCH"
    assert call.body == {
        "from_session_id": LEAD,
        "summary": "2 projects",
        "health": "ok",
        "heartbeat_s": 300,
    }


async def test_set_targets_another_member_and_trims_the_beacon() -> None:
    server = FakeServer().on("PATCH", "/v1/factory/sessions/conv_pl", body={})

    await _call(
        server,
        "sys_factory_set",
        {
            "session_id": "conv_pl",
            "state": "paused",
            "summary": "line one\nline two " + "x" * 200,
            "supervisor_session_id": PEER,
            "title": " PL · api ",
        },
    )

    body = server.requests[0].body
    assert body["state"] == "paused"
    assert body["supervisor_session_id"] == PEER
    assert body["title"] == "PL · api"
    assert "\n" not in body["summary"]
    assert len(body["summary"]) == SUMMARY_MAX_LEN


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"state": "sleeping"},
        {"health": "great"},
        {"heartbeat_s": 30},
        {"heartbeat_s": True},
        {"heartbeat_s": 999999},
        {"title": ""},
        {"summary": 5},
    ],
)
async def test_set_rejects_invalid_fields_locally(args: dict[str, object]) -> None:
    server = FakeServer()

    result = await _call(server, "sys_factory_set", args)

    assert "error" in result
    assert server.requests == []


async def test_set_heartbeat_zero_turns_heartbeats_off() -> None:
    server = FakeServer().on("PATCH", f"/v1/factory/sessions/{LEAD}", body={})

    await _call(server, "sys_factory_set", {"heartbeat_s": 0})

    assert server.requests[0].body == {"from_session_id": LEAD, "heartbeat_s": 0}


async def test_set_permission_error_passes_through() -> None:
    server = FakeServer().on(
        "PATCH",
        "/v1/factory/sessions/conv_other",
        status=403,
        body={"error": {"code": "forbidden", "message": "session_out_of_factory"}},
    )

    result = await _call(server, "sys_factory_set", {"session_id": "conv_other", "health": "ok"})

    assert result["error"] == "session_out_of_factory"


@pytest.mark.parametrize(
    "session_id",
    ["../../sessions/conv_private", "..", ".", "conv_a/../conv_b", "conv a", "%2e%2e"],
)
async def test_set_refuses_ids_that_could_leave_the_factory_route(session_id: str) -> None:
    # A dot segment or separator would let httpx normalize the PATCH onto
    # another route (e.g. PATCH /v1/sessions/<id>, a plain rename).
    server = FakeServer()

    result = await _call(server, "sys_factory_set", {"session_id": session_id, "title": "x"})

    assert result["error"] == "invalid_session_id"
    assert server.requests == []


async def test_set_empty_supervisor_clears_it() -> None:
    server = FakeServer().on("PATCH", "/v1/factory/sessions/conv_pl", body={})

    await _call(server, "sys_factory_set", {"session_id": "conv_pl", "supervisor_session_id": " "})

    assert server.requests[0].body == {"from_session_id": LEAD, "supervisor_session_id": ""}


async def test_set_rejects_a_non_string_supervisor() -> None:
    server = FakeServer()

    result = await _call(server, "sys_factory_set", {"supervisor_session_id": 7})

    assert "error" in result
    assert server.requests == []


def test_session_path_encodes_the_id_as_one_segment() -> None:
    from omnigent.runner.factory_dispatch import _session_path

    assert _session_path("/v1/factory/sessions", "a/../b") == "/v1/factory/sessions/a%2F..%2Fb"
    assert _session_path("/v1/sessions", "conv_1", "/child_sessions") == (
        "/v1/sessions/conv_1/child_sessions"
    )
