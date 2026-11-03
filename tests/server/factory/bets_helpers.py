"""Shared setup for the bet tests: a sample bet, a strategy, and a fake discovery ledger.

The seeded factory has Atlas (``lead_a``, the root) and Sentinel (``lead_b``);
Atlas files bets with ``sys_factory_project`` and Sentinel co-signs them with
``sys_factory_propose(op="cosign", kind="projects")``. Bets need discovery on
(the server env and ``discovery.enabled``): :func:`bet_cockpit` turns both on.
Opportunities live in the discovery ledger, which the tests stand in for with
:class:`FakeLedger`. Every text is sample data.
"""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from omnigent.server.factory import bets, definition
from omnigent.server.factory.definition_blocks import DISCOVERY_ENV
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.definition_helpers import definition_cockpit
from tests.server.factory.projects_helpers import project

GOAL = {
    "id": "G-1",
    "statement": "More new users finish their first task in week one (sample)",
    "metric": "first_week_success",
}
GUARDRAIL = "support load per 1k users stays flat (sample)"
FLAGS = {"provider": "env-flags (sample)", "convention": "kebab-case keys, off by default"}

_BET: dict[str, Any] = {
    "kind": "enabler",
    "goal": "G-1",
    "hypothesis": "Measuring the first-week funnel shows where new users stop (S-3, S-9).",
    "outcome": "The team sees where new users stop in week one (sample)",
    "metric": "first_week_success",
    "expected": {"direction": "up", "low": 1, "high": 4, "unit": "pp"},
    "guardrails": ["no new prompts for users (sample)"],
    "flag": {"key": "funnel-events"},
    "measurement": {"method": "human_verdict", "readout_days": [28, 7]},
    "kill": {"state": "no readable funnel after two weeks (sample)", "date": "28 days"},
    "cost": {"agent_usd": 40, "human_review_min": 20, "risk": "low"},
}


def bet(**over: Any) -> dict[str, Any]:
    """:returns: A complete sample bet with *over* replacing top-level keys."""
    out = copy.deepcopy(_BET)
    for key, value in over.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = value
    return out


def set_strategy(
    cockpit: Cockpit, *, goals: Sequence[dict[str, Any]] = (GOAL,), discovery: bool = True
) -> None:
    """Give the factory a goal, a strategy guardrail, the product's flag convention and
    ``discovery.enabled`` (the server env is :func:`bet_cockpit`'s)."""
    definition.set_values(
        cockpit.deps,
        cockpit.fid,
        {
            "strategy.goals": [dict(g) for g in goals],
            "strategy.guardrails": [GUARDRAIL],
            "product.delivery.flags": dict(FLAGS),
            "discovery.enabled": discovery,
        },
        created_by="alice",
    )
    definition.invalidate(cockpit.fid)


async def bet_cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    """The definition cockpit with discovery on and :func:`set_strategy` applied."""
    monkeypatch.setenv(DISCOVERY_ENV, "1")
    async for value in definition_cockpit(world, db_uri, monkeypatch):
        set_strategy(value)
        yield value


async def file_bet(
    cockpit: Cockpit,
    *changes: dict[str, Any],
    key: str = "lead_a",
    **extra: Any,
) -> httpx.Response:
    """``sys_factory_project`` with a bet (a create of ``draft-share`` unless *changes*)."""
    body = {
        "from_session_id": cockpit.world.id(key),
        "kind": "projects",
        "changes": list(changes) or [{"op": "create", "project": project("draft-share")}],
        "rationale": "the funnel is unmeasured (sample)",
        **({"bet": bet()} if "bet" not in extra else {}),
        **{k: v for k, v in extra.items() if v is not None},
    }
    return await cockpit.client.post(
        "/v1/factory/agent/proposals", json=body, headers={"X-Test-User": "alice"}
    )


async def cosign(
    cockpit: Cockpit, ref: str = "C-1", *, key: str = "lead_b", **extra: Any
) -> httpx.Response:
    """``sys_factory_propose(op="cosign", kind="projects")`` as *key* (Sentinel)."""
    body = {
        "from_session_id": cockpit.world.id(key),
        "kind": "projects",
        "op": "cosign",
        "id": ref,
        "case_against": [
            "Most evidence comes from one thread (sample).",
            "Returning users are not in it (sample).",
        ],
        **extra,
    }
    return await cockpit.client.post(
        "/v1/factory/agent/proposals", json=body, headers={"X-Test-User": "alice"}
    )


def human_chat(cockpit: Cockpit, body: str = "please plan this (sample)") -> str:
    """:returns: ``chat_<id>`` of an owner's Room message (a person asked)."""
    message = cockpit.store.append_chat(
        cockpit.fid,
        thread="factory",
        author_kind="human",
        author="alice",
        author_role="owner",
        body=body,
    )
    return f"chat_{message.id}"


@dataclass
class FakeLedger:
    """The discovery ledger's hooks: known opportunities, recorded declines, verified quotes."""

    opportunities: dict[str, bets.OpportunityInfo] = field(default_factory=dict)
    declined: list[tuple[str, str, str]] = field(default_factory=list)
    excerpts: dict[str, str] = field(default_factory=dict)

    def add(self, ref: str, status: str = "validated", goal: str | None = "G-1") -> None:
        self.opportunities[ref] = bets.OpportunityInfo(ref, status, goal, f"{ref} (sample)")

    def lookup(self, _deps: Any, _factory_id: str, ref: str) -> bets.OpportunityInfo | None:
        return self.opportunities.get(ref)

    def on_decline(self, _deps: Any, _factory_id: str, ref: str, reason: str, by: str) -> None:
        self.declined.append((ref, reason, by))

    def check_quotes(
        self, _deps: Any, _factory_id: str, quotes: Sequence[str], cited: Sequence[str]
    ) -> list[str]:
        stored = " ".join(self.excerpts.get(ref, "") for ref in cited)
        return [q for q in quotes if q not in stored]

    def install(self) -> None:
        bets.register_opportunities(self.lookup, on_decline=self.on_decline)
        bets.register_quote_check(self.check_quotes)

    @staticmethod
    def uninstall() -> None:
        bets.register_opportunities(None)
        bets.register_quote_check(None)


__all__ = [
    "FLAGS",
    "GOAL",
    "GUARDRAIL",
    "FakeLedger",
    "bet",
    "bet_cockpit",
    "cosign",
    "file_bet",
    "human_chat",
    "set_strategy",
]
