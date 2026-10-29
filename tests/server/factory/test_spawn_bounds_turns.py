"""Factory turns started by mail restart ``spawn_bounds`` (live round-3 regression).

Atlas spawned Sentinel and three PLs in its first turn (the cap is 4), and the
fifth spawn stayed denied in every later turn: those turns were started by
factory mail, heartbeats and decision answers, which the server delivers
without a request-phase policy event, so the persisted count never reset.

Here each turn is started the way production starts it: ``deliver_message``
running the real dispatch chokepoint into a native terminal (only the runner
transport is stubbed). Each spawn is gated the way the server's MCP
``tools/call`` gates it: a fresh engine over the persisted state, built from
the shipped ``factory-lead`` / ``factory-pl`` bundles.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from omnigent.policies.turns import TURN_LABEL
from omnigent.policies.types import EvaluationContext
from omnigent.runtime import pending_inputs
from omnigent.server.factory import delivery
from omnigent.server.factory.delivery import deliver_message
from omnigent.server.routes._sessions import orchestration
from omnigent.server.routes._sessions.orchestration import (
    _evaluate_policy_with_fresh_engine,
    _NativeTerminalEnsureOutcome,
)
from omnigent.spec import AgentSpec, load
from omnigent.spec.types import Phase, PolicyAction
from tests.server.factory.conftest import FactoryWorld

_EXAMPLES = Path(__file__).resolve().parents[3] / "examples" / "factory"
ALLOW, DENY = PolicyAction.ALLOW, PolicyAction.DENY


@pytest.fixture(scope="module")
def lead_spec() -> AgentSpec:
    return load(_EXAMPLES / "factory-lead", expand_env=False)


@pytest.fixture(scope="module")
def pl_spec() -> AgentSpec:
    return load(_EXAMPLES / "factory-pl", expand_env=False)


@pytest.fixture()
def native_runner(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stub only the runner transport; returns the texts typed into the TUI."""
    typed: list[str] = []

    async def _client(*_args: Any, **_kwargs: Any) -> object:
        return object()

    async def _noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _not_stale(**_kwargs: Any) -> bool:
        return False

    async def _ready(*_args: Any, **_kwargs: Any) -> _NativeTerminalEnsureOutcome:
        return _NativeTerminalEnsureOutcome(error=None)

    async def _type(_client: Any, _sid: str, _conv: Any, body: Any, **_kw: Any) -> None:
        typed.append(body.data["content"][0]["text"])

    monkeypatch.setattr(delivery, "_get_runner_client", _client)
    monkeypatch.setattr(delivery, "_ensure_runner_relay_ready", _noop)
    monkeypatch.setattr(delivery, "_maybe_wake_stale_resumable_managed_sandbox", _not_stale)
    monkeypatch.setattr(delivery, "_is_native_terminal_session", lambda _conv: True)
    monkeypatch.setattr(orchestration, "_is_native_terminal_session", lambda _conv: True)
    monkeypatch.setattr(
        orchestration, "_build_native_terminal_message_event", lambda *_a, **_k: {}
    )
    monkeypatch.setattr(orchestration, "_ensure_native_terminal_ready", _ready)
    monkeypatch.setattr(orchestration, "_forward_native_terminal_message", _type)
    yield typed
    pending_inputs.reset_for_tests()


async def _deliver(world: FactoryWorld, key: str, text: str) -> None:
    outcome = await deliver_message(world.app_state, world.id(key), text)
    assert outcome.delivered, outcome


async def _dispatch(world: FactoryWorld, key: str, spec: AgentSpec, tool: str) -> PolicyAction:
    """Gate one dispatch exactly like the server's MCP ``tools/call``."""
    conv = world.get(key)
    ctx = EvaluationContext(
        phase=Phase.TOOL_CALL,
        content={"name": tool, "arguments": {"agent": "factory-pl", "title": "PL"}},
        tool_name=tool,
    )
    _engine, result = await _evaluate_policy_with_fresh_engine(
        conv.id, spec, world.store, conv, ctx
    )
    return result.action


async def test_atlas_can_spawn_again_on_a_turn_started_by_factory_mail(
    world: FactoryWorld, lead_spec: AgentSpec, native_runner: list[str]
) -> None:
    await _deliver(world, "lead_a", "[Factory brief · from luka via cockpit] Start.")
    first = [await _dispatch(world, "lead_a", lead_spec, "sys_factory_spawn") for _ in range(5)]
    assert first == [ALLOW] * 4 + [DENY]

    for text in (
        "[Factory heartbeat · #1]",
        "[Factory decision · D-1 answered by luka] Spawn it.",
        "[Factory message · from PL · api-version kind=info] done",
    ):
        await _deliver(world, "lead_a", text)
        assert await _dispatch(world, "lead_a", lead_spec, "sys_factory_spawn") == ALLOW
    assert len(native_runner) == 4  # every turn really reached the terminal


async def test_atlas_stays_capped_within_one_turn(
    world: FactoryWorld, lead_spec: AgentSpec, native_runner: list[str]
) -> None:
    await _deliver(world, "lead_a", "[Factory heartbeat · #2]")
    turn = world.get("lead_a").labels[TURN_LABEL]
    actions = [
        await _dispatch(world, "lead_a", lead_spec, tool)
        for tool in ("sys_factory_spawn", "sys_session_create") * 3
    ]
    assert actions == [ALLOW] * 4 + [DENY] * 2
    assert world.get("lead_a").labels[TURN_LABEL] == turn


async def test_pl_answers_ics_again_after_a_full_turn(
    world: FactoryWorld, pl_spec: AgentSpec, native_runner: list[str]
) -> None:
    await _deliver(world, "pl", "[Factory message · from Atlas kind=brief] CHARTER · api-version")
    sends = [await _dispatch(world, "pl", pl_spec, "sys_session_send") for _ in range(9)]
    assert sends == [ALLOW] * 8 + [DENY]

    await _deliver(world, "pl", "[Factory heartbeat · #1]")
    assert await _dispatch(world, "pl", pl_spec, "sys_session_send") == ALLOW
