"""Open proposals that no longer apply close themselves, visibly and reversibly.

A pending proposal whose project closed, whose charter changed since, or
whose new slug is taken would only be refused when someone clicks Apply.
The sweep closes it with its reason (``withdrawn`` with ``result.closed``),
withdraws its decision so Needs you holds no moot ask, posts a Room line and
tells the proposer. A person can reopen it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.server.factory import proposals, snapshot
from omnigent.server.factory.background import proposals_sweep
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import REPO, header_cockpit, project, propose


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in header_cockpit(world, db_uri, monkeypatch):
        yield value


def _goal(slug: str = "api-version") -> dict[str, Any]:
    return {"op": "update", "slug": slug, "patch": {"goal": "A different goal (sample)."}}


SCOPE = ("services/api/**", "env/schema/api.ts", "env/schema/web.ts")
"""The project's files and two more (sample)."""


def _scope(slug: str = "api-version") -> dict[str, Any]:
    return {"op": "update", "slug": slug, "patch": {"scope_paths": list(SCOPE)}}


async def _pending(cockpit: Cockpit, *changes: dict[str, Any], **extra: Any) -> str:
    resp = await propose(cockpit, *changes, **extra)
    assert resp.json()["status"] == "pending", resp.text
    return str(resp.json()["proposal"])


def _close_project(cockpit: Cockpit, slug: str, status: str = "cancelled") -> None:
    row = cockpit.store.get_project(cockpit.fid, slug)
    assert row is not None
    cockpit.store.update_project(row.id, status=status)


def _finish_project(cockpit: Cockpit, slug: str, stage: str = "merged") -> None:
    """Merged and done are stages: the server keeps such a project ``active``."""
    row = cockpit.store.get_project(cockpit.fid, slug)
    assert row is not None
    cockpit.store.update_project(row.id, stage=stage)


async def _asks(cockpit: Cockpit) -> dict[str, Any]:
    snapshot.reset_cache()
    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/asks", headers={"X-Test-User": "alice"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.parametrize(
    ("word", "code", "reopenable"),
    [
        ("cancelled", "project_closed", False),
        ("merged", "project_done", True),
        ("done", "project_done", True),
    ],
)
async def test_a_proposal_for_a_closed_project_closes_itself(
    cockpit: Cockpit, word: str, code: str, reopenable: bool
) -> None:
    ref = await _pending(cockpit, _goal())
    assert any(a["id"] == ref for a in (await _asks(cockpit))["asks"])
    if word == "cancelled":
        _close_project(cockpit, "api-version", word)
    else:
        _finish_project(cockpit, "api-version", word)

    closed = await proposals.close_stale(cockpit.app.state, cockpit.fid)

    assert closed == [ref]
    row = proposals.find_proposal(cockpit.deps, cockpit.fid, ref)
    assert row is not None and row.status == "withdrawn" and row.decided_by == "server"
    info = (row.result or {})["closed"]
    assert (info["by"], info["code"], info["from"]) == ("server", code, "pending")
    assert info["reason"] == f"project 'api-version' is {word}"
    assert info["reopenable"] is reopenable
    decision = cockpit.store.get_decision(row.decision_id)
    assert decision is not None and decision.status == "withdrawn"
    assert not any(a["id"] == ref for a in (await _asks(cockpit))["asks"])
    [event] = cockpit.events("proposal.withdrawn")
    assert event.summary.startswith(f"{ref} closed itself: project 'api-version' is {word}")
    assert event.data["by_kind"] == "server"
    [mail] = cockpit.mailer.texts_to(cockpit.world.id("lead_a"))[-1:]
    assert mail.startswith(f"[Factory proposal · {ref} closed: project 'api-version' is")


async def test_a_project_finished_before_filing_does_not_close_it(cockpit: Cockpit) -> None:
    # A follow-up for a project that had merged already is what the lead meant.
    _finish_project(cockpit, "api-version", "merged")
    ref = await _pending(cockpit, _goal())

    assert await proposals.close_stale(cockpit.app.state, cockpit.fid) == []
    row = proposals.find_proposal(cockpit.deps, cockpit.fid, ref)
    assert row is not None and row.based_on["api-version"]["stage"] == "merged"


def _without_stages(cockpit: Cockpit, ref: str) -> None:
    """The proposal as a row filed before ``based_on`` recorded each project's stage."""
    row = proposals.find_proposal(cockpit.deps, cockpit.fid, ref)
    assert row is not None
    old = {
        slug: {k: v for k, v in fp.items() if k != "stage"} for slug, fp in row.based_on.items()
    }
    cockpit.store.update_proposal(row.id, based_on=old)


async def test_the_live_shape_closes_itself_once_its_project_merged(cockpit: Cockpit) -> None:
    """
    The live C-15: more files for a project, filed before stages were recorded;
    the project merged since and stays ``active``. It no longer matters.
    """
    ref = await _pending(cockpit, _scope())
    _without_stages(cockpit, ref)
    _finish_project(cockpit, "api-version", "merged")
    project = cockpit.store.get_project(cockpit.fid, "api-version")
    assert project is not None and project.status == "active"

    closed = await proposals.close_stale(cockpit.app.state, cockpit.fid)

    assert closed == [ref]
    row = proposals.find_proposal(cockpit.deps, cockpit.fid, ref)
    assert row is not None and row.status == "withdrawn"
    info = (row.result or {})["closed"]
    assert (info["code"], info["reason"]) == ("project_done", "project 'api-version' is merged")
    assert info["reopenable"] is True
    decision = cockpit.store.get_decision(row.decision_id)
    assert decision is not None and decision.status == "withdrawn"
    assert not any(a["id"] == ref for a in (await _asks(cockpit))["asks"])


async def test_a_row_from_before_stages_whose_project_had_finished_stays(
    cockpit: Cockpit,
) -> None:
    # Filed for a project that had merged already: the follow-up the lead meant.
    ref = await _pending(cockpit, _goal())
    _without_stages(cockpit, ref)
    row = proposals.find_proposal(cockpit.deps, cockpit.fid, ref)
    project = cockpit.store.get_project(cockpit.fid, "api-version")
    assert row is not None and project is not None
    cockpit.store.update_project(
        project.id, stage="merged", stage_changed_at=row.created_at - 3600
    )

    assert await proposals.close_stale(cockpit.app.state, cockpit.fid) == []


async def test_changes_the_charter_already_holds_close_it(cockpit: Cockpit) -> None:
    ref = await _pending(cockpit, _scope())
    # Another change gave the project the same files meanwhile.
    row = cockpit.store.get_project(cockpit.fid, "api-version")
    assert row is not None
    cockpit.store.update_project(
        row.id, charter={**(row.charter or {}), "scope_paths": list(SCOPE)}
    )

    closed = await proposals.close_stale(cockpit.app.state, cockpit.fid)

    assert closed == [ref]
    found = proposals.find_proposal(cockpit.deps, cockpit.fid, ref)
    assert found is not None
    info = (found.result or {})["closed"]
    assert (info["code"], info["reopenable"]) == ("already_applied", False)
    assert info["reason"] == "'api-version' already has these changes"


async def test_a_changed_charter_or_a_taken_slug_closes_it(cockpit: Cockpit) -> None:
    changed = await _pending(cockpit, _goal())
    taken = await _pending(cockpit, {"op": "create", "project": project("db-version")})
    row = cockpit.store.get_project(cockpit.fid, "api-version")
    assert row is not None
    cockpit.store.update_project(row.id, charter={**(row.charter or {}), "constraints": "new"})
    cockpit.store.upsert_project(cockpit.fid, "db-version", title="db", repo=REPO)

    closed = await proposals.close_stale(cockpit.app.state, cockpit.fid)

    assert sorted(closed) == sorted([changed, taken])
    codes = {
        ref: (proposals.find_proposal(cockpit.deps, cockpit.fid, ref).result or {})["closed"][
            "code"
        ]
        for ref in closed
    }
    assert codes == {changed: "stale_proposal", taken: "slug_exists"}


async def test_a_proposal_that_still_applies_stays(cockpit: Cockpit) -> None:
    ref = await _pending(cockpit, _goal())

    assert await proposals.close_stale(cockpit.app.state, cockpit.fid) == []
    row = proposals.find_proposal(cockpit.deps, cockpit.fid, ref)
    assert row is not None and row.status == "pending"


async def test_the_sweep_closes_them(cockpit: Cockpit) -> None:
    ref = await _pending(cockpit, _goal())
    _close_project(cockpit, "api-version")

    report = await proposals_sweep.sweep_once(cockpit.app.state)

    assert report.closed == [ref]


async def test_a_person_can_reopen_it(cockpit: Cockpit) -> None:
    ref = await _pending(cockpit, _goal())
    first = proposals.find_proposal(cockpit.deps, cockpit.fid, ref)
    assert first is not None
    _finish_project(cockpit, "api-version", "merged")
    await proposals.close_stale(cockpit.app.state, cockpit.fid)

    resp = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/{ref}/reopen")

    assert resp.status_code == 200, resp.text
    view = resp.json()["proposal"]
    assert view["status"] == "pending" and view["decided_by"] is None
    assert view["result"]["reopened"]["by"] == "alice"
    assert view["decision_ref"] is not None and view["decision_id"] != first.decision_id
    decision = cockpit.store.get_decision(view["decision_id"])
    assert decision is not None and decision.status == "open"
    # It no longer closes itself for the reason a person kept it open for.
    assert await proposals.close_stale(cockpit.app.state, cockpit.fid) == []
    [reopened] = cockpit.events("proposal.reopened")
    assert reopened.summary == f"{ref} reopened by alice"


async def test_only_a_proposal_that_closed_itself_reopens(cockpit: Cockpit) -> None:
    ref = await _pending(cockpit, _goal())
    by_agent = await propose(cockpit, withdraw=ref)
    assert by_agent.status_code == 200, by_agent.text

    resp = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/{ref}/reopen")
    nobody = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/proposals/{ref}/reopen", user="bob"
    )

    assert (resp.status_code, error_code(resp)) == (409, "not_closed"), resp.text
    assert nobody.status_code in (403, 404)


async def test_a_reason_apply_would_refuse_again_does_not_reopen(cockpit: Cockpit) -> None:
    ref = await _pending(cockpit, _goal())
    _close_project(cockpit, "api-version")
    await proposals.close_stale(cockpit.app.state, cockpit.fid)

    resp = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/{ref}/reopen")

    assert (resp.status_code, error_code(resp)) == (409, "not_reopenable"), resp.text
    assert "propose the work again" in resp.json()["error"]["message"]
    [event] = cockpit.events("proposal.withdrawn")
    assert event.summary.endswith("propose again if still needed")


async def test_a_reopened_grace_proposal_waits_for_a_person(cockpit: Cockpit) -> None:
    ref = await _pending(cockpit, _goal())
    row = proposals.find_proposal(cockpit.deps, cockpit.fid, ref)
    assert row is not None
    # Filed under grace autonomy: its timer ran out while it was closed.
    cockpit.store.update_proposal(row.id, result={"grace_until": 1})
    _finish_project(cockpit, "api-version", "merged")
    await proposals.close_stale(cockpit.app.state, cockpit.fid)

    resp = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/{ref}/reopen")

    assert resp.status_code == 200, resp.text
    assert "grace_until" not in resp.json()["proposal"]["result"]
