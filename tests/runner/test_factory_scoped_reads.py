"""Factory-scoped session reads.

A factory member acts as the human on every endpoint, so without scoping a
prompt-injected IC could read any of the human's other chats. For specs with a
``factory_role`` the runner therefore checks ``GET /v1/factory/membership``
before ``sys_session_get_history`` / ``sys_session_get_info`` on another
session, and ``sys_session_list`` lists roster members instead of every
session. Non-factory agents keep the unchanged global behaviour.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from omnigent.runner import factory_dispatch
from omnigent.runner.tool_dispatch import _send_to_existing_session, execute_tool
from tests.runner.factory_fakes import (
    LEAD,
    PEER,
    FakeServer,
    factory_spec,
    member,
    membership,
    roster,
)

_IC = "conv_ic_1"
_STRANGER = "conv_private_chat"


@pytest.fixture(autouse=True)
def _fresh_membership_cache() -> Iterator[None]:
    factory_dispatch._membership_cache.clear()
    yield
    factory_dispatch._membership_cache.clear()


def _with_session(server: FakeServer, session_id: str) -> FakeServer:
    server.on("GET", f"/v1/sessions/{session_id}", body={"id": session_id, "title": "t"})
    server.on(
        "GET",
        f"/v1/sessions/{session_id}/items",
        body={"data": [{"type": "message", "role": "assistant", "content": "secret"}]},
    )
    return server


def _with_memberships(server: FakeServer, **targets: dict[str, object] | int) -> FakeServer:
    server.on("GET", f"/v1/factory/membership/{LEAD}", body=membership(LEAD, role="lead"))
    for target, answer in targets.items():
        path = f"/v1/factory/membership/{target}"
        if isinstance(answer, int):
            server.on("GET", path, status=answer, body={"error": "session_out_of_factory"})
        else:
            server.on("GET", path, body=answer)
    return server


async def _run(
    server: FakeServer,
    tool: str,
    args: dict[str, Any],
    *,
    role: str | None = "lead",
    caller: str = LEAD,
) -> dict[str, Any]:
    async with server.client() as client:
        out = await execute_tool(
            tool_name=tool,
            arguments=json.dumps(args),
            server_client=client,
            agent_spec=factory_spec(role),
            conversation_id=caller,
        )
    return json.loads(out)


async def test_history_outside_the_factory_is_refused_without_reading_it() -> None:
    server = _with_session(_with_memberships(FakeServer(), **{_STRANGER: 403}), _STRANGER)

    result = await _run(server, "sys_session_get_history", {"conversation_id": _STRANGER})

    assert result == {
        "error": "session_out_of_factory",
        "status": 403,
        "conversation_id": _STRANGER,
    }
    assert server.calls("GET", f"/v1/sessions/{_STRANGER}/items") == []
    [check] = server.calls("GET", f"/v1/factory/membership/{_STRANGER}")
    assert check.params == {"from_session_id": LEAD}


async def test_history_inside_the_factory_reads_normally() -> None:
    server = _with_session(
        _with_memberships(FakeServer(), **{PEER: membership(PEER, role="lead")}), PEER
    )

    result = await _run(server, "sys_session_get_history", {"conversation_id": PEER})

    assert result["conversation_id"] == PEER
    assert len(server.calls("GET", f"/v1/sessions/{PEER}/items")) == 1


async def test_a_member_of_another_factory_is_refused_even_on_a_200() -> None:
    other = membership(_STRANGER, role="lead", factory_id="conv_other_root")
    server = _with_session(_with_memberships(FakeServer(), **{_STRANGER: other}), _STRANGER)

    result = await _run(server, "sys_session_get_history", {"conversation_id": _STRANGER})

    assert result == {"error": "session_out_of_factory", "conversation_id": _STRANGER}
    assert server.calls("GET", f"/v1/sessions/{_STRANGER}/items") == []


async def test_get_info_on_self_needs_no_membership_lookup() -> None:
    server = FakeServer().on("GET", f"/v1/sessions/{LEAD}", body={"id": LEAD, "status": "idle"})

    result = await _run(server, "sys_session_get_info", {})

    assert result["session_id"] == LEAD
    assert not [r for r in server.requests if r.path.startswith("/v1/factory/")]


async def test_get_info_outside_the_factory_is_refused() -> None:
    server = _with_session(_with_memberships(FakeServer(), **{_STRANGER: 403}), _STRANGER)

    result = await _run(server, "sys_session_get_info", {"session_id": _STRANGER})

    assert result["error"] == "session_out_of_factory"
    assert result["session_id"] == _STRANGER
    assert server.calls("GET", f"/v1/sessions/{_STRANGER}") == []


async def test_unknown_membership_fails_closed() -> None:
    server = _with_session(FakeServer(), _STRANGER)  # no /v1/factory routes at all

    result = await _run(server, "sys_session_get_history", {"conversation_id": _STRANGER})

    assert result["error"] == "factory_membership_unavailable"
    assert server.calls("GET", f"/v1/sessions/{_STRANGER}/items") == []


async def test_membership_is_cached_across_reads_in_a_turn() -> None:
    server = _with_session(
        _with_memberships(FakeServer(), **{PEER: membership(PEER, role="lead")}), PEER
    )

    await _run(server, "sys_session_get_history", {"conversation_id": PEER})
    await _run(server, "sys_session_get_info", {"session_id": PEER})

    assert len(server.calls("GET", f"/v1/factory/membership/{PEER}")) == 1
    assert len(server.calls("GET", f"/v1/factory/membership/{LEAD}")) == 1


async def test_session_list_returns_roster_members_only() -> None:
    ic_row = {
        "session_id": _IC,
        "title": "ic:auth",
        "agent": "ic",
        "role": "ic",
        "status": "running",
        "summary": "writing tests",
    }
    server = FakeServer()
    server.on(
        "GET",
        "/v1/factory/roster",
        body=roster(
            member(LEAD, role="lead"),
            member("conv_pl", role="project_lead", project="api", children=[ic_row]),
        ),
    )
    server.on("GET", f"/v1/sessions/{LEAD}/child_sessions", body={"data": []})
    server.on("GET", f"/v1/sessions/{LEAD}", body={"id": LEAD, "parent_session_id": None})

    result = await _run(server, "sys_session_list", {})

    assert result["scope"] == "factory"
    assert [row["session_id"] for row in result["sessions"]] == [LEAD, "conv_pl", _IC]
    pl_row = result["sessions"][1]
    assert pl_row["role"] == "project_lead"
    assert pl_row["project"] == "api"
    assert pl_row["spawned_by"] == LEAD
    assert pl_row["parent_session_id"] is None
    assert result["sessions"][2]["parent_session_id"] == "conv_pl"
    assert result["sessions"][2]["agent_name"] == "ic"
    # The human's global session list is never fetched.
    assert server.calls("GET", "/v1/sessions") == []


async def test_session_list_filters_by_agent_name() -> None:
    server = FakeServer()
    server.on(
        "GET",
        "/v1/factory/roster",
        body=roster(
            member("conv_pl", role="project_lead", children=[{"session_id": _IC, "agent": "ic"}])
        ),
    )

    result = await _run(server, "sys_session_list", {"agent_name": "ic"})

    assert [row["session_id"] for row in result["sessions"]] == [_IC]


async def test_session_list_roster_failure_lists_nothing_global() -> None:
    server = FakeServer().on(
        "GET", "/v1/factory/roster", status=403, body={"error": "sender_not_bound"}
    )

    result = await _run(server, "sys_session_list", {})

    assert result["sessions"] == []
    assert result["roster_error"]["error"] == "sender_not_bound"
    assert server.calls("GET", "/v1/sessions") == []


async def test_non_factory_agents_keep_global_reads() -> None:
    server = _with_session(FakeServer(), _STRANGER)

    result = await _run(
        server, "sys_session_get_history", {"conversation_id": _STRANGER}, role=None
    )

    assert result["conversation_id"] == _STRANGER
    assert not [r for r in server.requests if r.path.startswith("/v1/factory/")]


async def test_ic_role_is_scoped_too() -> None:
    server = FakeServer()
    server.on("GET", f"/v1/factory/membership/{_IC}", body=membership(_IC, role="ic"))
    server.on("GET", f"/v1/factory/membership/{_STRANGER}", status=403, body={})

    result = await _run(
        server,
        "sys_session_get_history",
        {"conversation_id": _STRANGER},
        role="ic",
        caller=_IC,
    )

    assert result["error"] == "session_out_of_factory"


async def test_send_by_session_id_out_of_tree_points_at_sys_session_post() -> None:
    server = FakeServer().on(
        "GET", f"/v1/sessions/{PEER}", body={"id": PEER, "parent_session_id": None}
    )

    async with server.client() as client:
        out = await _send_to_existing_session(
            PEER, "hello", server_client=client, conversation_id=LEAD
        )

    result = json.loads(out)
    assert result["error"] == "session_out_of_tree"
    assert "sys_session_post" in result["message"]
