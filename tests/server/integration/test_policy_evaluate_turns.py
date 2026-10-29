"""Every server turn start restarts per-turn policy state (``spawn_bounds``).

A server-dispatched prompt (factory mail, a heartbeat, a web message) is
already in ``pending_inputs`` when the native ``UserPromptSubmit`` hook posts
``PHASE_REQUEST``, so the route skips re-gating it. Per-turn policy state must
still restart there: before turn ids, that skip also skipped ``spawn_bounds``'
reset, and a lead that used its dispatches stayed capped for good.

A skill slash command (``/fanout …``) is forwarded to the runner on its own
path, not through the message dispatch; it must start a turn as well.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from omnigent.policies.turns import TURN_LABEL
from omnigent.runtime import pending_inputs
from omnigent.spec.types import SkillSpec
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.tools.builtins.load_skill import format_skill_meta_text
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


async def test_skill_slash_command_turn_gets_a_fresh_spawn_budget(
    client: httpx.AsyncClient, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``/fanout`` turn after a capped turn may dispatch again (polly's brain).

    claude-sdk posts no ``PHASE_REQUEST`` of its own, so the turn id stamped
    when the slash command is forwarded is what restarts the budget.
    """
    from omnigent.server.routes import sessions as sessions_module

    def _runner(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/skills/resolve"):
            payload = json.loads(request.content)
            skill = SkillSpec(name="fanout", description="Fan out.", content="Fan out.")
            meta_text = format_skill_meta_text(skill, payload.get("arguments", ""))
            return httpx.Response(200, json={"meta_text": meta_text})
        return httpx.Response(202, json={"queued": True})

    fake_runner = httpx.AsyncClient(transport=httpx.MockTransport(_runner), base_url="http://r")

    async def _get_runner_client(_session_id: str, _router: Any) -> httpx.AsyncClient:
        return fake_runner

    monkeypatch.setattr(sessions_module, "_get_runner_client", _get_runner_client)
    monkeypatch.setattr(
        "omnigent.server.routes.sessions.session_stream.publish", lambda *_a, **_k: None
    )
    store = SqlAlchemyConversationStore(db_uri)
    send = _event("PHASE_TOOL_CALL", {"name": "sys_session_send", "arguments": {}})
    try:
        agent = await create_test_agent(
            client,
            name="polly-like",
            guardrails=_SPAWN_BOUNDS,
            skills=[{"name": "fanout", "description": "Fan out.", "content": "Fan out."}],
        )
        resp = await client.post("/v1/sessions", json={"agent_id": agent["id"]})
        assert resp.status_code == 201, resp.text
        session_id = resp.json()["id"]

        # Turn 1: a plain message; the orchestrator uses its whole budget.
        message = {"role": "user", "content": [{"type": "input_text", "text": "go"}]}
        resp = await client.post(
            f"/v1/sessions/{session_id}/events", json={"type": "message", "data": message}
        )
        assert resp.status_code == 202, resp.text
        verdicts = [await _evaluate(client, session_id, send) for _ in range(3)]
        assert verdicts == ["POLICY_ACTION_ALLOW"] * 2 + ["POLICY_ACTION_DENY"]
        conv = store.get_conversation(session_id)
        assert conv is not None
        first_turn = conv.labels[TURN_LABEL]

        # Turn 2: the human runs a skill. A new turn, so a fresh budget.
        command = {"kind": "skill", "name": "fanout", "arguments": "three tasks"}
        resp = await client.post(
            f"/v1/sessions/{session_id}/events", json={"type": "slash_command", "data": command}
        )
        assert resp.status_code == 202, resp.text
        verdicts = [await _evaluate(client, session_id, send) for _ in range(3)]
        assert verdicts == ["POLICY_ACTION_ALLOW"] * 2 + ["POLICY_ACTION_DENY"]
        conv = store.get_conversation(session_id)
        assert conv is not None
        assert conv.labels[TURN_LABEL] != first_turn
    finally:
        await fake_runner.aclose()
