"""A native harness's prompt hook starts a policy turn, even when its gate dedups.

A server-dispatched prompt (factory mail, a heartbeat, a web message) is
already in ``pending_inputs`` when the native ``UserPromptSubmit`` hook posts
``PHASE_REQUEST``, so the route skips re-gating it. Per-turn policy state must
still restart there: before turn ids, that skip also skipped ``spawn_bounds``'
reset, and a lead that used its dispatches stayed capped for good.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from omnigent.policies.turns import TURN_LABEL
from omnigent.runtime import pending_inputs
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from tests.server.helpers import create_test_agent

pytestmark = pytest.mark.asyncio

_SPAWN_BOUNDS = {
    "policies": {
        "spawn_bounds": {
            "type": "function",
            "function": {
                "path": "omnigent.policies.builtins.orchestration.spawn_bounds",
                "arguments": {
                    "max_dispatches_per_turn": 2,
                    "dispatch_tools": ["sys_session_send"],
                },
            },
        },
    },
}


def _event(phase: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"event": {"type": phase, "target": "", "data": data, "context": {}}}


async def _evaluate(client: httpx.AsyncClient, session_id: str, body: dict[str, Any]) -> str:
    resp = await client.post(f"/v1/sessions/{session_id}/policies/evaluate", json=body)
    assert resp.status_code == 200, resp.text
    return str(resp.json()["result"])


async def test_deduped_prompt_hook_restarts_spawn_bounds(
    client: httpx.AsyncClient, db_uri: str
) -> None:
    agent = await create_test_agent(client, guardrails=_SPAWN_BOUNDS)
    resp = await client.post("/v1/sessions", json={"agent_id": agent["id"]})
    assert resp.status_code == 201, resp.text
    session_id = resp.json()["id"]
    store = SqlAlchemyConversationStore(db_uri)
    send = _event("PHASE_TOOL_CALL", {"name": "sys_session_send", "arguments": {}})
    turns: list[str] = []
    try:
        for text in ("[Factory brief] Start.", "[Factory heartbeat · #1]"):
            pending_inputs.record(session_id, [{"type": "input_text", "text": text}])
            prompt = await _evaluate(client, session_id, _event("PHASE_REQUEST", {"text": text}))
            assert prompt == "POLICY_ACTION_ALLOW"
            verdicts = [await _evaluate(client, session_id, send) for _ in range(3)]
            assert verdicts == ["POLICY_ACTION_ALLOW"] * 2 + ["POLICY_ACTION_DENY"], text
            conv = store.get_conversation(session_id)
            assert conv is not None
            turns.append(conv.labels[TURN_LABEL])
    finally:
        pending_inputs.reset_for_tests()
    assert turns[0] != turns[1]


async def test_clients_cannot_set_the_turn_id(client: httpx.AsyncClient, db_uri: str) -> None:
    """A forged turn id would reset per-turn caps; the server drops it."""
    agent = await create_test_agent(client, guardrails=_SPAWN_BOUNDS)
    resp = await client.post(
        "/v1/sessions",
        json={"agent_id": agent["id"], "labels": {TURN_LABEL: "turn_forged", "team": "a"}},
    )
    assert resp.status_code == 201, resp.text
    session_id = resp.json()["id"]
    patched = await client.patch(
        f"/v1/sessions/{session_id}", json={"labels": {TURN_LABEL: "turn_forged_2"}}
    )
    assert patched.status_code == 200, patched.text
    conv = SqlAlchemyConversationStore(db_uri).get_conversation(session_id)
    assert conv is not None
    assert conv.labels.get("team") == "a"
    assert TURN_LABEL not in conv.labels
