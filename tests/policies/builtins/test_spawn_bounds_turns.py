"""``spawn_bounds`` counts per server turn id, whatever started the turn.

The deployed server rebuilds its engine for every tool call, so these tests
drive real engines over a real store: one fresh engine per evaluation, exactly
like the MCP ``tools/call`` gate. A turn starts by stamping a new turn id
(:func:`omnigent.runtime.policies.turns.begin_turn`), the same call every
server turn-start point makes; no request-phase event is evaluated, which is
what factory mail, heartbeats and decision answers looked like in production.
"""

from __future__ import annotations

from typing import Any

import pytest

from omnigent.policies.builtins.orchestration import spawn_bounds
from omnigent.policies.turns import TURN_LABEL
from omnigent.policies.types import EvaluationContext
from omnigent.runtime.policies import PolicyEngine, build_policy_engine
from omnigent.runtime.policies.turns import begin_turn
from omnigent.spec import AgentSpec
from omnigent.spec.types import (
    FunctionPolicySpec,
    FunctionRef,
    GuardrailsSpec,
    Phase,
    PolicyAction,
)
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore

_PATH = "omnigent.policies.builtins.orchestration.spawn_bounds"
_STATE_KEY = "_policy_spawn_bounds_dispatches"


def _spec(cap: int, tools: list[str]) -> AgentSpec:
    return AgentSpec(
        spec_version=1,
        name="spawn-bounds-turns",
        guardrails=GuardrailsSpec(
            policies=[
                FunctionPolicySpec(
                    name="spawn_bounds",
                    on=None,
                    function=FunctionRef(
                        path=_PATH,
                        arguments={"max_dispatches_per_turn": cap, "dispatch_tools": tools},
                    ),
                )
            ]
        ),
    )


def _call(tool: str) -> EvaluationContext:
    return EvaluationContext(
        phase=Phase.TOOL_CALL,
        content={"name": tool, "arguments": {}},
        tool_name=tool,
    )


class _Session:
    """One conversation evaluated the way the deployed server does it."""

    def __init__(self, store: SqlAlchemyConversationStore, spec: AgentSpec) -> None:
        self.store = store
        self.spec = spec
        self.id = store.create_conversation().id

    def engine(self) -> PolicyEngine:
        return build_policy_engine(
            spec=self.spec, conversation_id=self.id, conversation_store=self.store
        )

    async def call(self, tool: str) -> PolicyAction:
        return (await self.engine().evaluate(_call(tool))).action

    async def calls(self, tool: str, n: int) -> list[PolicyAction]:
        return [await self.call(tool) for _ in range(n)]

    def new_turn(self) -> str:
        turn = begin_turn(self.store, self.id)
        assert turn is not None
        return turn

    def state(self) -> dict[str, Any]:
        conv = self.store.get_conversation(self.id)
        assert conv is not None
        return dict(conv.session_state)


@pytest.fixture()
def store(db_uri: str) -> SqlAlchemyConversationStore:
    return SqlAlchemyConversationStore(db_uri)


ALLOW, DENY = PolicyAction.ALLOW, PolicyAction.DENY


@pytest.mark.asyncio
async def test_lead_cap_resets_on_a_turn_started_without_a_request_event(
    store: SqlAlchemyConversationStore,
) -> None:
    """The live failure: four spawns, a denied fifth, then a mail-started turn."""
    atlas = _Session(store, _spec(4, ["sys_factory_spawn", "sys_session_create"]))
    atlas.new_turn()
    assert await atlas.calls("sys_factory_spawn", 5) == [ALLOW] * 4 + [DENY]
    # Still the same turn: every further spawn stays denied.
    assert await atlas.call("sys_session_create") == DENY

    atlas.new_turn()  # factory mail / heartbeat / decision answer
    assert await atlas.call("sys_factory_spawn") == ALLOW


@pytest.mark.asyncio
async def test_pl_sends_reset_every_turn(store: SqlAlchemyConversationStore) -> None:
    """A PL that used its 8 sends can dispatch and answer ICs on its next turn."""
    pl = _Session(store, _spec(8, ["sys_session_send", "sys_session_create"]))
    for _ in range(3):
        pl.new_turn()
        assert await pl.calls("sys_session_send", 9) == [ALLOW] * 8 + [DENY]


@pytest.mark.asyncio
async def test_a_single_turn_stays_capped_and_other_tools_do_not_count(
    store: SqlAlchemyConversationStore,
) -> None:
    session = _Session(store, _spec(2, ["sys_session_send"]))
    session.new_turn()
    assert await session.call("sys_os_shell") == ALLOW
    assert await session.calls("sys_session_send", 2) == [ALLOW, ALLOW]
    for _ in range(3):
        assert await session.call("sys_os_shell") == ALLOW
        assert await session.call("sys_session_send") == DENY


@pytest.mark.asyncio
async def test_stale_turn_counts_are_dropped(store: SqlAlchemyConversationStore) -> None:
    """Only the current turn's count (and nothing per-session) stays in session_state."""
    session = _Session(store, _spec(3, ["sys_session_send"]))
    # A count persisted before turn ids existed (the stuck live sessions).
    store.set_session_state(session.id, {_STATE_KEY: 7})
    first = session.new_turn()
    assert await session.call("sys_session_send") == ALLOW
    second = session.new_turn()
    assert await session.call("sys_session_send") == ALLOW
    state = session.state()
    assert state == {f"{_STATE_KEY}@{second}": 1}, (first, state)


@pytest.mark.asyncio
async def test_policies_cannot_write_the_turn_id(store: SqlAlchemyConversationStore) -> None:
    """A policy's set_labels must not mint a turn (it would reset every cap)."""
    session = _Session(store, _spec(1, ["sys_session_send"]))
    turn = session.new_turn()
    session.engine().apply_label_writes({TURN_LABEL: "turn_forged", " Omnigent.Turn": "x"})
    conv = store.get_conversation(session.id)
    assert conv is not None
    assert conv.labels.get(TURN_LABEL) == turn
    assert " Omnigent.Turn" not in conv.labels


@pytest.mark.asyncio
async def test_request_event_still_resets_when_no_turn_id_is_stamped(
    store: SqlAlchemyConversationStore,
) -> None:
    """Sessions never stamped (older callers) keep the request-phase reset."""
    session = _Session(store, _spec(1, ["sys_session_send"]))
    request = EvaluationContext(phase=Phase.REQUEST, content="go")
    await session.engine().evaluate(request)
    assert await session.calls("sys_session_send", 2) == [ALLOW, DENY]
    await session.engine().evaluate(request)
    assert await session.call("sys_session_send") == ALLOW


@pytest.mark.asyncio
async def test_request_event_resets_a_stamped_turn_too(
    store: SqlAlchemyConversationStore,
) -> None:
    """A turn start seen only as a request-phase event (no new stamp) still resets."""
    session = _Session(store, _spec(1, ["sys_session_send"]))
    turn = session.new_turn()
    assert await session.calls("sys_session_send", 2) == [ALLOW, DENY]
    request = EvaluationContext(phase=Phase.REQUEST, content="go")
    await session.engine().evaluate(request)
    assert session.state() == {_STATE_KEY: 0}
    assert await session.calls("sys_session_send", 2) == [ALLOW, DENY]
    assert session.state() == {f"{_STATE_KEY}@{turn}": 2}


def _event(tool: str, turn: str | None) -> dict[str, Any]:
    return {
        "type": "tool_call",
        "data": {"name": tool, "arguments": {}},
        "context": {"turn": turn},
    }


def test_runner_local_closure_counts_per_turn_and_resets() -> None:
    """Without session_state the closure counts, per turn id when one is given."""
    evaluate = spawn_bounds(max_dispatches_per_turn=1)
    assert evaluate(_event("sys_session_send", None))["result"] == "ALLOW"
    assert evaluate(_event("sys_session_send", None))["result"] == "DENY"
    evaluate.reset_turn()  # type: ignore[attr-defined]
    assert evaluate(_event("sys_session_send", None))["result"] == "ALLOW"
    assert evaluate(_event("sys_session_send", "turn_a"))["result"] == "ALLOW"
    assert evaluate(_event("sys_session_send", "turn_a"))["result"] == "DENY"
    assert evaluate(_event("sys_session_send", "turn_b"))["result"] == "ALLOW"
