"""Shared setup for the discovery tests: a factory with discovery on, sample sources and signals.

The seeded factory has Atlas (``lead_a``, the root) and Sentinel (``lead_b``);
``alice`` owns it, ``carol`` is an operator and ``bob`` has no access.
:func:`discovery_cockpit` turns discovery on (the server env and
``discovery.enabled``), gives the factory a sample profile, goal and sources,
and wires the ledger in. Every product word is sample data.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest

from omnigent.entities.factory_discovery import FactoryOpportunity, FactorySignal
from omnigent.server.factory import definition, discovery
from omnigent.server.factory.actor import Actor
from omnigent.server.factory.definition_blocks import DISCOVERY_ENV
from omnigent.server.factory.discovery import ledger, opportunities
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.definition_helpers import definition_cockpit

DAY = 86400
SEGMENTS = [
    {"id": "new_user", "label": "Accounts in their first 14 days (sample)", "rule": "age ≤ 14d"},
    {"id": "teams", "label": "Teams (sample)", "rule": "a shared workspace"},
]
AREAS = [{"id": "share", "label": "Sharing (sample)", "paths": ["src/share/**"]}]
GOAL = {"id": "G-1", "statement": "More new users finish a first project in week one (sample)"}
SOURCES = [
    {
        "id": "repo-issues",
        "label": "Repo issues (sample)",
        "kind": "repo",
        "params": {"repos": ["example-org/sample-app"], "since_days": 90},
        "state": "active",
    },
    {"id": "web", "label": "Web and market (sample)", "kind": "web", "state": "active"},
]
ALTERNATIVES = [
    "A private preview link (sample)",
    "Invite a reviewer by email before publishing (sample)",
    "A password on the draft (sample)",
]


def turn_on(cockpit: Cockpit, **values: Any) -> None:
    """Discovery on for the factory, with the sample profile, goal and sources (plus *values*)."""
    definition.set_values(
        cockpit.deps,
        cockpit.fid,
        {
            "discovery.enabled": True,
            "discovery.sources": [dict(s) for s in SOURCES],
            "product.segments": [dict(s) for s in SEGMENTS],
            "product.areas": [dict(a) for a in AREAS],
            "strategy.goals": [dict(GOAL)],
            "strategy.focus_segments": ["new_user"],
            "strategy.non_goals": ["Enterprise single sign-on (sample)"],
            **values,
        },
        created_by="alice",
    )
    definition.invalidate(cockpit.fid)


async def discovery_cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch, *, on: bool = True
) -> AsyncIterator[Cockpit]:
    """The definition cockpit with the server env on, the ledger wired in, and :func:`turn_on`."""
    monkeypatch.setenv(DISCOVERY_ENV, "1")
    async for value in definition_cockpit(world, db_uri, monkeypatch):
        uninstall = discovery.install(value.app.state)
        try:
            if on:
                turn_on(value)
            yield value
        finally:
            uninstall()


def base(cockpit: Cockpit, suffix: str = "") -> str:
    """:returns: ``/v1/factory/factories/{fid}`` plus *suffix*."""
    return f"/v1/factory/factories/{cockpit.fid}{suffix}"


def ingest(
    cockpit: Cockpit,
    source: str,
    texts: Sequence[str],
    *,
    accounts: Sequence[str | None] | None = None,
    age_days: float = 1,
    segments: Sequence[str] = ("new_user",),
) -> list[FactorySignal]:
    """Store sample *texts* from *source* through the ledger, one account each by default."""
    ref = ledger.source_ref(cockpit.deps, cockpit.fid, source)
    assert ref is not None
    now = int(time.time())
    items = [
        ledger.Item(
            text=text,
            pointer=f"https://example.org/{source}/{i}-{abs(hash(text)) % 10**8}",
            observed_at=now - int(age_days * DAY),
            account=(accounts[i] if accounts is not None else f"user-{source}-{i}"),
            segments=segments,
        )
        for i, text in enumerate(texts)
    ]
    return ledger.ingest(cockpit.deps, cockpit.fid, ref, items).signals


def label(cockpit: Cockpit, signals: Sequence[FactorySignal], intensity: str) -> None:
    """Label *signals* with *intensity* (as a labeller would)."""
    for signal in signals:
        ledger.label(
            cockpit.deps, cockpit.fid, signal.number, by="scout (sample)", intensity=intensity
        )


def agent(cockpit: Cockpit) -> Actor:
    """:returns: An agent author (Atlas stands in for the discovery seat)."""
    return Actor.agent(cockpit.world.id("lead_a"), "lead", title="Atlas")


def make_opportunity(
    cockpit: Cockpit,
    signals: Sequence[FactorySignal] = (),
    *,
    counter: Sequence[FactorySignal] = (),
    **fields: Any,
) -> FactoryOpportunity:
    """An agent-written sample opportunity citing *signals* (and *counter* as against)."""
    body = {
        "title": "Show a draft before it goes live (sample)",
        "statement": (
            "When I finish a draft, I want to show it to someone before it goes live, "
            "so I can get feedback without risk (sample)"
        ),
        "goal": "G-1",
        "segments": ["new_user"],
        "alternatives": list(ALTERNATIVES),
        "signals": [{"signal": s.ref} for s in signals]
        + [{"signal": s.ref, "counter": True} for s in counter],
        **fields,
    }
    return opportunities.create(cockpit.deps, cockpit.fid, body, actor=agent(cockpit))


__all__ = [
    "ALTERNATIVES",
    "DAY",
    "GOAL",
    "SEGMENTS",
    "SOURCES",
    "agent",
    "base",
    "discovery_cockpit",
    "ingest",
    "label",
    "make_opportunity",
    "turn_on",
]
