"""Shared setup for the project-core, proposal, spawn and gating tests.

The seeded factory (Atlas + Sentinel + ``PL · api-version``) gets a default
``caffeinelabs/app#temp-main`` and a project row for ``api-version``. Agent
calls go through ``/v1/factory/agent/*`` as the session owner (alice);
:class:`FakeCreates` stands in for the in-process ``POST /v1/sessions`` of a
server spawn and creates the PL session row with the labels it was sent.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from omnigent import factory as F
from omnigent.server.auth import LEVEL_OWNER
from omnigent.server.factory import spawn
from omnigent.server.factory.charter import DEFAULT_BASE_ENV
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit
from tests.server.factory.conftest import FactoryWorld, HeaderAuth

REPO = "caffeinelabs/app"
BASE = "temp-main"


class MintingAuth(HeaderAuth):
    """Header auth that can also mint runner tokens (as OIDC and accounts auth do)."""

    def mint_runner_token(self, user_id: str, ttl_seconds: int) -> str | None:
        return f"runner-token-for-{user_id}-{ttl_seconds}"


@dataclass
class FakeCreates:
    """The server's own ``POST /v1/sessions``, recorded; a 201 creates the session row."""

    world: FactoryWorld
    calls: list[dict[str, Any]] = field(default_factory=list)
    fail_next: int = 0
    permissions: Any = None

    def client(self, _app_state: Any, token: str) -> httpx.AsyncClient:
        def handle(request: httpx.Request) -> httpx.Response:
            import json

            body = json.loads(request.content)
            self.calls.append(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "authorization": request.headers.get("authorization"),
                    "body": body,
                    "token": token,
                }
            )
            if self.fail_next:
                self.fail_next -= 1
                return httpx.Response(500, json={"error": {"code": "boom", "message": "boom"}})
            conv = self.world.store.create_conversation(
                kind="default", title=body["title"], agent_id=body["agent_id"]
            )
            self.world.store.set_labels(conv.id, body["labels"])
            if self.permissions is not None:
                # The create route grants the caller (the factory's creator) ownership.
                self.permissions.grant("alice", conv.id, LEVEL_OWNER)
            return httpx.Response(201, json={"id": conv.id, "status": "idle"})

        return httpx.AsyncClient(
            transport=httpx.MockTransport(handle),
            base_url="http://omnigent.internal",
            headers={"Authorization": f"Bearer {token}"},
        )


def seed(cockpit: Cockpit, *, pl: bool = True) -> None:
    """Default repo/base on the root and the ``api-version`` project row."""
    cockpit.world.store.set_labels(
        cockpit.fid,
        {F.LABEL_DEFAULT_REPO: REPO, F.LABEL_DEFAULT_BASE: BASE, F.LABEL_CADENCE: "demo"},
    )
    cockpit.store.upsert_project(
        cockpit.fid,
        "api-version",
        title="API version",
        repo=REPO,
        base_branch=BASE,
        charter={"goal": "Add GET /version.", "scope_paths": ["services/api/**"]},
        pl_session_id=cockpit.world.id("pl") if pl else None,
    )


def project(slug: str, **extra: Any) -> dict[str, Any]:
    """A create op's project body."""
    return {"slug": slug, "title": slug, "goal": f"Build {slug}.", **extra}


async def propose(
    cockpit: Cockpit, *changes: dict[str, Any], key: str = "lead_a", **extra: Any
) -> httpx.Response:
    """``sys_factory_project`` as member *key*."""
    body = {
        "from_session_id": cockpit.world.id(key),
        "kind": "projects",
        "changes": list(changes),
        "rationale": "split the brief",
        **extra,
    }
    return await cockpit.client.post("/v1/factory/agent/proposals", json=body, headers=_alice())


def _alice() -> dict[str, str]:
    return {"X-Test-User": "alice"}


async def settle() -> None:
    """Let background spawns finish."""
    await spawn.drain()


async def header_cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    """The cockpit with header auth (no runner token: spawns fall back to the lead)."""
    monkeypatch.delenv("OMNIGENT_FACTORY_PREVIEW_TOOLING_REF", raising=False)
    monkeypatch.delenv(DEFAULT_BASE_ENV, raising=False)
    async for value in build_cockpit(world, db_uri, monkeypatch):
        seed(value)
        yield value
        await settle()


async def spawning_cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[Cockpit, FakeCreates]]:
    """The cockpit with a minting provider and recorded in-process creates."""
    monkeypatch.delenv("OMNIGENT_FACTORY_PREVIEW_TOOLING_REF", raising=False)
    monkeypatch.delenv(DEFAULT_BASE_ENV, raising=False)
    creates = FakeCreates(world)
    monkeypatch.setattr(spawn, "_client_factory", creates.client)
    async for value in build_cockpit(world, db_uri, monkeypatch, auth_provider=MintingAuth()):
        seed(value)
        creates.permissions = value.permissions
        yield value, creates
        await settle()


__all__ = [
    "BASE",
    "REPO",
    "FakeCreates",
    "MintingAuth",
    "header_cockpit",
    "project",
    "propose",
    "seed",
    "settle",
    "spawning_cockpit",
]
