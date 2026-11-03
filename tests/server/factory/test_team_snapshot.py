"""The snapshot shows each project's team spec next to its live team, with drift.

``projects[].team_spec`` is the project's own team (or today's bundle), with
the charter's builders and budget filled in, why a member cannot be removed,
and where the live sub-agents differ from it (or a gate the charter needs now
has no member). ``members[]`` carry their vendor and model.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.server.factory import snapshot
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.definition_helpers import definition_cockpit, put

SLUG = "api-version"
MEMBERS = f"team.projects.{SLUG}.members"


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in definition_cockpit(world, db_uri, monkeypatch):
        yield value


async def _snapshot(cockpit: Cockpit) -> dict[str, Any]:
    snapshot.reset_cache()
    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}", headers={"X-Test-User": "alice"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _project(body: dict[str, Any]) -> dict[str, Any]:
    return next(p for p in body["projects"] if p["slug"] == SLUG)


async def test_a_project_without_its_own_team_follows_todays_bundle(cockpit: Cockpit) -> None:
    body = await _snapshot(cockpit)

    spec = _project(body)["team_spec"]
    assert (spec["own"], spec["follows"], spec["template"], spec["mode"]) == (
        False,
        "bundle",
        None,
        "warn",
    )
    assert spec["summary"].startswith("Follows today's bundle: every agent of the PL bundle")
    assert (spec["builders_at_once"], spec["builders_from"]) == (3, "charter")
    assert (spec["budget_usd"], spec["budget_from"]) == (150.0, "definition")
    builder = spec["members"][1]
    assert (builder["id"], builder["agent"], builder["model"], builder["model_label"]) == (
        "M-2",
        "ic",
        None,
        "sonnet",
    )
    assert spec["members"][3]["remove_blocked"] == (
        "Can't remove: the review gate needs a reviewer (Tier 0)"
    )
    # Today's team has no drift by definition: it is the whole bundle.
    assert spec["drift"] == [] and spec["drifted"] is False
    members = {m["session_id"]: m for m in body["members"]}
    assert members[cockpit.world.id("reviewer")]["vendor"] == "codex"
    assert members[cockpit.world.id("ic")]["vendor"] == "claude"


async def test_its_own_team_flags_a_live_sub_agent_outside_it(cockpit: Cockpit) -> None:
    resp = await put(
        cockpit,
        {"op": "remove", "path": f"{MEMBERS}/M-3"},
        {"op": "replace", "path": f"team.projects.{SLUG}/budget_usd", "value": 90},
    )
    assert resp.status_code == 200, resp.text
    codex = cockpit.world.make_session(
        "codex_ic", agent="pl", title="route", parent="pl", sub_agent_name="ic_codex"
    )

    spec = _project(await _snapshot(cockpit))["team_spec"]

    assert spec["own"] is True and spec["def_version"] == 2
    assert (spec["budget_usd"], spec["budget_from"]) == (90.0, "team")
    assert [m["id"] for m in spec["members"]] == ["M-1", "M-2", "M-4", "M-5", "M-6"]
    assert spec["drifted"] is True
    outside = {d["session_id"]: d["text"] for d in spec["drift"] if d["kind"] == "outside_team"}
    assert outside[codex.id].endswith("ic_codex is not on the team")
    # The seeded helper sub-agent is no team member either; the IC and reviewer are.
    assert cockpit.world.id("helper") in outside
    assert cockpit.world.id("ic") not in outside and cockpit.world.id("reviewer") not in outside


async def test_a_gate_the_charter_needs_now_is_flagged_and_stays_dispatchable(
    cockpit: Cockpit,
) -> None:
    row = cockpit.store.get_project(cockpit.fid, SLUG)
    assert row is not None
    charter = dict(row.charter or {})
    cockpit.store.update_project(
        row.id, charter={**charter, "env_needed": False, "preview": {"enabled": False}}
    )
    resp = await put(
        cockpit,
        {"op": "remove", "path": f"{MEMBERS}/M-5"},
        {"op": "remove", "path": f"{MEMBERS}/M-6"},
    )
    assert resp.status_code == 200, resp.text
    spec = _project(await _snapshot(cockpit))["team_spec"]
    assert [d for d in spec["drift"] if d["kind"] == "missing_gate_role"] == []

    # The charter turns the env back on: the team was saved without an env keeper.
    cockpit.store.update_project(row.id, charter={**charter, "env_needed": True})
    spec = _project(await _snapshot(cockpit))["team_spec"]

    gaps = [d["text"] for d in spec["drift"] if d["kind"] == "missing_gate_role"]
    assert gaps[0] == (
        "No env keeper on the team: the verify gate needs an env keeper; env_keeper stays "
        "dispatchable until one is added"
    )
    assert spec["drifted"] is True
