"""A server started with ``OMNIGENT_FACTORY_ENABLED=1`` lists the shipped factory agents.

Runs the real app lifespan (which seeds every built-in) over the packaged
``examples/factory`` bundles and reads them back through ``GET /v1/agents``,
the endpoint the web picker and ``sys_factory_spawn`` resolve agents with.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from omnigent.factory import FACTORY_ENABLED_ENV, ROLE_LEAD, ROLE_PROJECT_LEAD

_FACTORY_AGENTS = {"factory-lead": ROLE_LEAD, "factory-pl": ROLE_PROJECT_LEAD}


async def _listed_agents(app: FastAPI, client: httpx.AsyncClient) -> dict[str, dict[str, object]]:
    async with app.router.lifespan_context(app):
        resp = await client.get("/v1/agents", params={"limit": 1000})
    assert resp.status_code == 200, resp.text
    return {str(agent["name"]): agent for agent in resp.json()["data"]}


async def test_enabled_server_lists_factory_lead_and_pl(
    app: FastAPI,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnigent.factory import factory_role_from_spec
    from omnigent.runtime import get_agent_cache, get_agent_store

    monkeypatch.setenv(FACTORY_ENABLED_ENV, "1")
    # Keep the supervisor from ticking while the lifespan is up.
    monkeypatch.setenv("OMNIGENT_FACTORY_TICK_S", "3600")

    agents = await _listed_agents(app, client)

    for name, role in _FACTORY_AGENTS.items():
        assert name in agents, f"{name} not seeded; listed: {sorted(agents)}"
        assert agents[name]["builtin"] is True
        # The stored bundle is the packaged one, prompts and skills included.
        row = get_agent_store().get(str(agents[name]["id"]))
        assert row is not None and row.bundle_location
        spec = get_agent_cache().load(row.id, row.bundle_location, expand_env=True).spec
        assert factory_role_from_spec(spec) == role
        assert "factory-protocol" in {skill.name for skill in spec.skills}


async def test_factory_agents_are_not_listed_without_the_flag(
    app: FastAPI,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(FACTORY_ENABLED_ENV, raising=False)

    agents = await _listed_agents(app, client)

    assert not set(_FACTORY_AGENTS) & set(agents)
    assert "polly" in agents
