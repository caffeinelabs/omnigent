"""Helpers for the definition tests: the cockpit with the definition installed, edits, proposals.

Sentinel is ``lead_b`` and Atlas ``lead_a`` (the root); ``alice`` owns the
factory, ``carol`` is an operator (EDIT) and ``bob`` has no access.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from omnigent.entities.factory import FactoryProposal
from omnigent.server.factory import definition
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import header_cockpit

EVIDENCE = ["F-9", "D-2"]
EFFECT = {"metric": "heartbeat_share", "direction": "down"}


async def definition_cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    """The header-auth cockpit with the definition wired in."""
    async for value in header_cockpit(world, db_uri, monkeypatch):
        restore = definition.install(value.app.state)
        try:
            yield value
        finally:
            restore()


def url(cockpit: Cockpit, suffix: str = "") -> str:
    """:returns: ``/v1/factory/factories/{fid}/definition`` plus *suffix*."""
    return f"/v1/factory/factories/{cockpit.fid}/definition{suffix}"


async def put(
    cockpit: Cockpit, *changes: dict[str, Any], user: str = "alice", **extra: Any
) -> httpx.Response:
    """``PUT …/definition`` as *user*."""
    return await cockpit.client.put(
        url(cockpit),
        json={"changes": list(changes), **extra},
        headers={"X-Test-User": user},
    )


async def propose(cockpit: Cockpit, key: str = "lead_b", **body: Any) -> httpx.Response:
    """``sys_factory_propose`` from member *key* (Sentinel by default)."""
    payload = {
        "from_session_id": cockpit.world.id(key),
        "kind": "tuning",
        "rationale": "the sample evidence says so (sample)",
        "evidence": EVIDENCE,
        "expected_effect": EFFECT,
        **body,
    }
    return await cockpit.client.post(
        "/v1/factory/agent/proposals", json=payload, headers={"X-Test-User": "alice"}
    )


def row(cockpit: Cockpit, number: int = 1) -> FactoryProposal:
    """:returns: Proposal ``C-<number>``."""
    found = cockpit.store.get_proposal_by_number(cockpit.fid, number)
    assert found is not None
    return found


async def answer(
    cockpit: Cockpit, number: int, option: str = "apply", user: str = "alice"
) -> httpx.Response:
    """Answer ``C-<number>``'s owners decision with *option*."""
    found = row(cockpit, number)
    return await cockpit.post(
        f"/v1/factory/decisions/{found.decision_id}/answer", {"option_id": option}, user=user
    )


def texts(cockpit: Cockpit, key: str) -> list[str]:
    """:returns: The mail member *key* got."""
    return cockpit.mailer.texts_to(cockpit.world.id(key))


def version(cockpit: Cockpit) -> int:
    """:returns: The version in force."""
    resolved = definition.definition_for(cockpit.deps, cockpit.fid, fresh=True)
    assert resolved is not None
    return resolved.version


__all__ = [
    "EFFECT",
    "EVIDENCE",
    "answer",
    "definition_cockpit",
    "propose",
    "put",
    "row",
    "texts",
    "url",
    "version",
]
