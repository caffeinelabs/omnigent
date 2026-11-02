"""``GET …/catalog``: what a project's team can be made of.

The roles come from the PL bundle the server ships (``tools.agents`` and each
agent's harness and model), each with the vendor · models it runs, the gate
that needs it, a $/h estimate, and the roles no bundle covers yet. With a
project it also says which templates are locked there and which members it
cannot remove.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from omnigent.server.factory import team, team_spec
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.definition_helpers import definition_cockpit

SLUG = "api-version"


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in definition_cockpit(world, db_uri, monkeypatch):
        yield value


def test_the_code_map_matches_the_shipped_pl_bundle() -> None:
    shipped = team.bundle_agents()
    assert set(shipped) == {"factory-pl", "env_keeper", "demo", "ic", "ic_codex", "reviewer"}
    for (role, vendor), agent in team_spec.AGENTS.items():
        found = shipped[agent]
        assert found["vendor"] == vendor or (role, agent) == ("reviewer", "ic"), agent
        # A Claude review runs as an ic session; every other agent's role is its own.
        assert found["role"] == role or (role, agent) == ("reviewer", "ic"), agent


async def test_the_catalog_lists_roles_vendors_gates_and_what_is_missing(
    cockpit: Cockpit,
) -> None:
    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/catalog", headers={"X-Test-User": "carol"}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    roles = {r["role"]: r for r in body["roles"]}
    assert list(roles) == ["project_lead", "ic", "reviewer", "env_keeper", "demo"]
    lead = {v["vendor"]: v for v in roles["project_lead"]["vendors"]}
    assert lead["claude"]["available"] is True and lead["claude"]["agent"] == "factory-pl"
    assert lead["codex"] == {
        "vendor": "codex",
        "available": False,
        "reason": "Codex is not available for project leads yet: they run on Claude Code today",
    }
    assert roles["project_lead"]["addable"] is False
    builder = {v["vendor"]: v for v in roles["ic"]["vendors"]}
    assert builder["claude"]["default_model"] == "sonnet"
    assert builder["claude"]["models"][:3] == ["opus", "sonnet", "haiku"]
    assert builder["codex"]["agent"] == "ic_codex" and builder["codex"]["default_model"] is None
    assert builder["claude"]["rate_source"] == "default" and builder["claude"]["usd_per_h"] > 0
    assert roles["reviewer"]["needed_by"]["gate"] == "review"
    assert roles["demo"]["needed_by"] == {
        "gate": "demo",
        "text": "the demo gate needs one while previews are on",
        "when": "previews",
    }
    assert [m["role"] for m in body["missing"]] == ["designer", "qa", "docs"]
    assert body["missing"][0]["reason"].startswith("Not in the catalog.")
    assert [t["id"] for t in body["templates"]] == ["standard", "pair", "lean", "investigation"]
    assert all(t["locked"] is None for t in body["templates"])
    assert body["mode"] == "warn" and "project" not in body


async def test_with_a_project_it_says_what_is_locked_there(cockpit: Cockpit) -> None:
    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/catalog",
        params={"project": SLUG},
        headers={"X-Test-User": "carol"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    locks = {t["id"]: t["locked"] for t in body["templates"]}
    assert locks["standard"] is None and locks["pair"] is None
    assert locks["lean"] == "Locked: the demo gate needs one while previews are on."
    assert locks["investigation"] == "Locked: the verify gate needs an env keeper."
    project = body["project"]
    assert (project["own"], project["env_needed"], project["previews_on"]) == (False, True, True)
    assert project["remove_blocked"] == {
        "M-1": "The project lead stays: every project has one.",
        "M-4": "Can't remove: the review gate needs a reviewer (Tier 0)",
        "M-5": "Can't remove: the verify gate needs an env keeper (Tier 0)",
        "M-6": "Can't remove: the demo gate needs one while previews are on (Tier 0)",
    }


async def test_the_catalog_needs_read_access_and_a_real_project(cockpit: Cockpit) -> None:
    denied = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/catalog", headers={"X-Test-User": "bob"}
    )
    assert denied.status_code in (403, 404)
    missing = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/catalog",
        params={"project": "nope"},
        headers={"X-Test-User": "alice"},
    )
    assert missing.status_code == 404 and error_code(missing) == "unknown_project"
