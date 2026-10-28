"""People file projects proposals from the cockpit (``POST …/proposals``).

A dependency edited on the Graph page opens a pending ``C-n`` authored by the
person, mirrored as a decision in ``#factory``; applying it from its card
changes the project like an agent's proposal would.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from omnigent.runner.identity import RUNNER_TUNNEL_TOKEN_HEADER
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import BASE, REPO, header_cockpit


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in header_cockpit(world, db_uri, monkeypatch):
        cockpit_ = value
        cockpit_.store.upsert_project(
            cockpit_.fid,
            "settings-row",
            title="Settings row",
            repo=REPO,
            base_branch=BASE,
            charter={"goal": "Show both versions.", "scope_paths": ["web/settings/**"]},
        )
        yield cockpit_


def _depends(slug: str, *on: str) -> dict:
    return {"op": "update", "slug": slug, "patch": {"depends_on": list(on)}}


async def _file(cockpit: Cockpit, *changes: dict, user: str = "alice", **extra: object):
    body = {"changes": list(changes), "rationale": "settings-row needs the API first", **extra}
    return await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals", body, user=user)


async def test_a_dependency_edit_opens_a_pending_human_proposal(cockpit: Cockpit) -> None:
    resp = await _file(cockpit, _depends("settings-row", "api-version"))

    assert resp.status_code == 201, resp.text
    body = resp.json()
    proposal = body["proposal"]
    assert proposal["ref"] == "C-1" and proposal["status"] == "pending"
    assert proposal["author_kind"] == "human" and proposal["author"] == "alice"
    assert proposal["approval"] == "operator"
    assert proposal["thread"] == "factory"
    decision = cockpit.store.get_decision(proposal["decision_id"])
    assert decision is not None and decision.ref == body["decision"]
    assert decision.subject == "proposal:C-1" and decision.source == "proposal"
    assert decision.asked_by_session_id is None
    assert [e.kind for e in cockpit.events("proposal.opened")] == ["proposal.opened"]
    # Nothing changes until someone applies it.
    row = cockpit.store.get_project(cockpit.fid, "settings-row")
    assert row is not None and "depends_on" not in (row.charter or {})


async def test_applying_it_sets_the_dependency(cockpit: Cockpit) -> None:
    await _file(cockpit, _depends("settings-row", "api-version"))

    resp = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/apply", {}, user="carol"
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["proposal"]["status"] == "applied"
    row = cockpit.store.get_project(cockpit.fid, "settings-row")
    assert row is not None and row.charter is not None
    assert row.charter["depends_on"] == ["api-version"]


async def test_a_cycle_is_refused_before_anything_is_stored(cockpit: Cockpit) -> None:
    await cockpit.patch(
        f"/v1/factory/factories/{cockpit.fid}/projects/api-version",
        {"depends_on": ["settings-row"]},
    )

    resp = await _file(cockpit, _depends("settings-row", "api-version"))

    assert (resp.status_code, error_code(resp)) == (409, "cycle")
    assert cockpit.store.list_proposals(cockpit.fid) == []


async def test_only_operators_file_and_only_people(cockpit: Cockpit) -> None:
    outsider = await _file(cockpit, _depends("settings-row", "api-version"), user="bob")
    runner = await cockpit.client.post(
        f"/v1/factory/factories/{cockpit.fid}/proposals",
        json={"changes": [_depends("settings-row", "api-version")], "rationale": "x"},
        headers={"X-Test-User": "alice", RUNNER_TUNNEL_TOKEN_HEADER: "rt_abc.def"},
    )
    empty = await _file(cockpit, rationale="")

    assert outsider.status_code in (403, 404)
    assert (runner.status_code, error_code(runner)) == (403, "human_only")
    assert empty.status_code == 400
    assert cockpit.store.list_proposals(cockpit.fid) == []
