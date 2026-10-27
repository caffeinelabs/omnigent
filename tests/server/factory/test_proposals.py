"""Projects proposals (``C-n``): validation, the decision mirror, apply-once, undo, expiry.

Agents file proposals with ``sys_factory_project`` (``POST
/v1/factory/agent/proposals``); people apply, edit, reject and undo them from
the cockpit. The seeded factory runs header auth, so applied projects start
through the lead fallback (Atlas is mailed the spawn call).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from fastapi.routing import APIRoute

from omnigent import factory as F
from omnigent.runner.identity import RUNNER_TUNNEL_TOKEN_HEADER
from omnigent.server.factory import projects, proposals, spawn, start
from omnigent.server.factory.api import proposals as proposals_api
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.origin import require_human_origin
from omnigent.stores.factory_store import now_s
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import (
    REPO,
    header_cockpit,
    project,
    propose,
    settle,
)


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in header_cockpit(world, db_uri, monkeypatch):
        yield value


def _apply(cockpit: Cockpit, ref: str, user: str = "alice", **body: Any) -> Any:
    return cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/proposals/{ref}/apply", body, user=user
    )


def _human_chat(cockpit: Cockpit, body: str = "please add a db version project") -> str:
    message = cockpit.store.append_chat(
        cockpit.fid,
        thread="factory",
        author_kind="human",
        author="alice",
        author_role="owner",
        body=body,
    )
    return f"chat_{message.id}"


def _proposal(cockpit: Cockpit, number: int = 1) -> Any:
    row = cockpit.store.get_proposal_by_number(cockpit.fid, number)
    assert row is not None
    return row


# ── Validation ─────────────────────────────────────────────────


async def test_field_errors_name_their_path(cockpit: Cockpit) -> None:
    bad_slug = await propose(cockpit, {"op": "create", "project": project("Bad Slug")})
    no_goal = await propose(cockpit, {"op": "create", "project": {"slug": "x"}})
    bad_patch = await propose(
        cockpit, {"op": "update", "slug": "api-version", "patch": {"priority": 9}}
    )
    unknown = await propose(cockpit, {"op": "update", "slug": "nope", "patch": {"priority": 1}})
    bad_op = await propose(cockpit, {"op": "rename", "slug": "api-version"})
    no_reason = await propose(cockpit, {"op": "cancel", "slug": "api-version"})
    second = await propose(
        cockpit,
        {"op": "create", "project": project("a")},
        {"op": "create", "project": project("b", priority=-1)},
    )

    assert (bad_slug.status_code, bad_slug.json()["field"]) == (400, "changes[0].project.slug")
    assert no_goal.json()["field"] == "changes[0].project.goal"
    assert bad_patch.json()["field"] == "changes[0].patch.priority"
    assert (unknown.status_code, unknown.json()["field"]) == (404, "changes[0].slug")
    assert bad_op.json()["field"] == "changes[0].op"
    assert (no_reason.status_code, no_reason.json()["field"]) == (400, "reason")
    assert second.json()["field"] == "changes[1].project.priority"
    assert cockpit.store.list_proposals(cockpit.fid) == []


async def test_slugs_are_unique_across_projects_and_pending_proposals(cockpit: Cockpit) -> None:
    first = await propose(cockpit, {"op": "create", "project": project("db-version")})
    again = await propose(cockpit, {"op": "create", "project": project("db-version")})
    taken = await propose(cockpit, {"op": "create", "project": project("api-version")})

    assert first.status_code == 200, first.text
    assert (again.status_code, error_code(again)) == (409, "slug_pending")
    assert again.json()["proposal"] == "C-1"
    assert (taken.status_code, error_code(taken)) == (409, "slug_exists")


async def test_a_dependency_cycle_is_refused_with_its_path(cockpit: Cockpit) -> None:
    resp = await propose(
        cockpit,
        {"op": "create", "project": project("a", depends_on=["b"])},
        {"op": "create", "project": project("b", depends_on=["c"])},
        {"op": "create", "project": project("c", depends_on=["a"])},
    )
    unknown = await propose(
        cockpit, {"op": "create", "project": project("d", depends_on=["ghost"])}
    )

    assert (resp.status_code, error_code(resp)) == (409, "cycle")
    assert resp.json()["path"] == ["a", "b", "c", "a"]
    assert (unknown.status_code, error_code(unknown)) == (400, "unknown_dependency")
    assert unknown.json()["field"] == "changes[0].project.depends_on"


async def test_scope_overlap_is_a_warning(cockpit: Cockpit) -> None:
    resp = await propose(
        cockpit,
        {"op": "create", "project": project("api-cache", scope_paths=["services/api/cache/**"])},
        {"op": "create", "project": project("web", scope_paths=["web/**"])},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["warnings"] == [
        "scope_overlap: api-cache services/api/cache/** ~ api-version services/api/**"
    ]


async def test_over_the_cap_is_refused_with_candidates(cockpit: Cockpit) -> None:
    cockpit.store.upsert_project(cockpit.fid, "old", title="old", repo=REPO, stage="merged")
    changes = [{"op": "create", "project": project(f"p{i}")} for i in range(9)]

    resp = await propose(cockpit, *changes)

    assert (resp.status_code, error_code(resp)) == (409, "over_caps")
    assert resp.json()["done_candidates"] == ["old"]


async def test_a_project_lead_proposes_only_for_its_own_project(cockpit: Cockpit) -> None:
    split = await propose(
        cockpit,
        {
            "op": "split",
            "slug": "api-version",
            "into": [project("api-route"), project("api-docs")],
            "keep": "umbrella",
        },
        key="pl",
        reason="two independent milestones",
    )
    other = await propose(cockpit, {"op": "create", "project": project("unrelated")}, key="pl")
    ic = await propose(cockpit, {"op": "create", "project": project("x")}, key="ic")

    assert split.status_code == 200, split.text
    assert split.json()["status"] == "pending"
    assert (other.status_code, error_code(other)) == (403, "not_your_project")
    assert (ic.status_code, error_code(ic)) == (403, "not_allowed")


# ── The decision mirror and applying ───────────────────────────


async def test_an_agent_own_create_waits_on_a_proposal_decision(cockpit: Cockpit) -> None:
    resp = await propose(cockpit, {"op": "create", "project": project("db-version")})

    body = resp.json()
    assert body["proposal"] == "C-1" and body["status"] == "pending"
    assert body["approval"] == "operator"
    row = _proposal(cockpit)
    decision = cockpit.store.get_decision(row.decision_id)
    assert decision is not None and body["decision"] == decision.ref
    assert decision.source == "proposal"
    assert decision.subject == "proposal:C-1"
    assert decision.assignee == "operators"
    assert [o["id"] for o in decision.options] == ["apply", "edit", "reject"]
    assert decision.options[0]["action"] == "apply_proposal"
    assert row.based_on == {}
    assert cockpit.store.get_project(cockpit.fid, "db-version") is None
    assert "proposal.opened" in [e.kind for e in cockpit.events()]


async def test_a_requested_create_applies_at_once_with_undo(cockpit: Cockpit) -> None:
    resp = await propose(
        cockpit,
        {"op": "create", "project": project("db-version")},
        reply_to=_human_chat(cockpit),
    )

    body = resp.json()
    assert body["status"] == "applied", body
    assert body["decision"] is None
    assert body["undo_until"] >= now_s() + 590
    row = cockpit.store.get_project(cockpit.fid, "db-version")
    assert row is not None
    assert row.repo == REPO and row.base_branch == "temp-main"
    assert _proposal(cockpit).on_behalf_of == "alice"
    # Header auth mints no runner token: Atlas is asked to spawn it.
    [mail] = cockpit.mailer.to(cockpit.fid)
    assert "Spawn its PL now" in mail.text and "project='db-version'" in mail.text


async def test_two_concurrent_applies_apply_once(cockpit: Cockpit) -> None:
    await propose(cockpit, {"op": "create", "project": project("db-version")})

    first, second = await asyncio.gather(
        _apply(cockpit, "C-1"), _apply(cockpit, "C-1", user="carol")
    )

    statuses = sorted([first.status_code, second.status_code])
    assert statuses == [200, 409], (first.text, second.text)
    loser = first if first.status_code == 409 else second
    winner = "alice" if loser is second else "carol"
    assert error_code(loser) in ("decision_not_open", "proposal_not_pending")
    body = loser.json()
    assert body.get("answered_by") == winner or body.get("decided_by") == winner, body
    [applied] = [e for e in cockpit.events() if e.kind == "proposal.applied"]
    assert applied.data["by"] == winner
    assert _proposal(cockpit).decided_by == winner
    assert len(cockpit.store.list_projects(cockpit.fid)) == 2


async def test_apply_tells_the_proposer_what_spawns(cockpit: Cockpit) -> None:
    await propose(cockpit, {"op": "create", "project": project("db-version")})

    resp = await _apply(cockpit, "C-1")

    assert resp.status_code == 200, resp.text
    assert resp.json()["proposal"]["status"] == "applied"
    assert resp.json()["decision"]["status"] == "answered"
    [outcome] = [t for t in cockpit.mailer.texts_to(cockpit.fid) if "Factory proposal" in t]
    assert outcome.startswith(
        "[Factory proposal · C-1 applied by alice (owner) · spawning: db-version]"
    )


async def test_a_stale_proposal_is_refused(cockpit: Cockpit) -> None:
    await propose(
        cockpit, {"op": "update", "slug": "api-version", "patch": {"goal": "Add GET /version v2."}}
    )
    await cockpit.patch(
        f"/v1/factory/factories/{cockpit.fid}/projects/api-version", {"dod": ["curl works"]}
    )

    resp = await _apply(cockpit, "C-1")

    assert (resp.status_code, error_code(resp)) == (409, "stale_proposal")
    assert resp.json()["project"] == "api-version"
    assert _proposal(cockpit).status == "pending"


async def test_edit_and_apply_uses_the_edited_changes(cockpit: Cockpit) -> None:
    await propose(cockpit, {"op": "create", "project": project("db-version")})

    resp = await _apply(
        cockpit,
        "C-1",
        changes=[{"op": "create", "project": project("db-schema", priority=0)}],
    )

    assert resp.status_code == 200, resp.text
    assert cockpit.store.get_project(cockpit.fid, "db-version") is None
    row = cockpit.store.get_project(cockpit.fid, "db-schema")
    assert row is not None and row.priority == 0
    assert _proposal(cockpit).result["edited"] is True


async def test_reject_mails_the_note_to_the_proposer(cockpit: Cockpit) -> None:
    await propose(cockpit, {"op": "create", "project": project("db-version")})

    resp = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/reject", {"note": "too small"}
    )

    assert resp.status_code == 200, resp.text
    assert _proposal(cockpit).status == "rejected"
    [text] = cockpit.mailer.texts_to(cockpit.fid)
    assert text.startswith('[Factory proposal · C-1 rejected by alice: "too small"]')


async def test_an_owner_class_proposal_needs_an_owner(cockpit: Cockpit) -> None:
    resp = await propose(
        cockpit,
        {"op": "create", "project": project("infra", repo="caffeinelabs/infra")},
        reply_to=_human_chat(cockpit),
    )
    assert resp.json()["approval"] == "owner"
    decision = cockpit.store.get_decision(_proposal(cockpit).decision_id)
    assert decision is not None and decision.assignee == "owners"

    operator = await _apply(cockpit, "C-1", user="carol")
    owner = await _apply(cockpit, "C-1")

    assert operator.status_code == 403, operator.text
    assert owner.status_code == 200, owner.text


async def test_a_split_up_to_the_cap_applies(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One project of a two-project cap: the split closes it and adds two.
    monkeypatch.setattr(start, "max_projects", lambda: 2)
    resp = await propose(
        cockpit,
        {
            "op": "split",
            "slug": "api-version",
            "into": [project("api-route"), project("api-docs")],
            "keep": "cancel",
        },
        reason="two milestones",
    )
    assert resp.json()["status"] == "pending", resp.text

    applied = await _apply(cockpit, "C-1")

    assert applied.status_code == 200, applied.text
    assert _proposal(cockpit).status == "applied"
    rows = {p.slug: p.status for p in cockpit.store.list_projects(cockpit.fid)}
    assert rows == {"api-version": "cancelled", "api-route": "active", "api-docs": "active"}


async def test_a_create_next_to_a_later_cancel_fits_the_cap(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(start, "max_projects", lambda: 1)
    await propose(
        cockpit,
        {"op": "create", "project": project("db-version")},
        {"op": "cancel", "slug": "api-version"},
        reason="replace it",
    )

    resp = await _apply(cockpit, "C-1")

    assert resp.status_code == 200, resp.text
    rows = {p.slug: p.status for p in cockpit.store.list_projects(cockpit.fid)}
    assert rows == {"api-version": "cancelled", "db-version": "active"}


async def test_a_failed_apply_takes_back_the_projects_it_created(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    await propose(
        cockpit,
        {"op": "create", "project": project("db-version")},
        {
            "op": "split",
            "slug": "api-version",
            "into": [project("api-route")],
            "keep": "umbrella",
        },
        {"op": "create", "project": project("settings")},
        reason="split the work",
    )
    real_create = projects.create_project_core

    async def create_then_fail(app_state: Any, root: Any, project_in: Any, *a: Any, **k: Any):
        if project_in.slug == "settings":
            raise CockpitError(409, "boom", "the store said no")
        return await real_create(app_state, root, project_in, *a, **k)

    monkeypatch.setattr(projects, "create_project_core", create_then_fail)

    resp = await _apply(cockpit, "C-1")
    started = await spawn.finish_pending(cockpit.app.state, cockpit.fid)
    await settle()

    assert error_code(resp) == "boom", resp.text
    row = _proposal(cockpit)
    assert row.status == "refused"
    assert row.result["rolled_back"] == ["db-version", "api-route"]
    assert {p.slug for p in cockpit.store.list_projects(cockpit.fid)} == {"api-version"}
    assert started == []
    assert not any("Spawn its PL now" in t for t in cockpit.mailer.texts_to(cockpit.fid))


async def test_a_self_project_is_refused_while_the_smith_lane_is_off(cockpit: Cockpit) -> None:
    resp = await propose(cockpit, {"op": "create", "project": project("me", kind="self")})

    assert (resp.status_code, resp.json()["field"]) == (400, "changes[0].project.kind")


async def test_an_oversized_analysis_is_refused(cockpit: Cockpit) -> None:
    resp = await propose(
        cockpit,
        {"op": "create", "project": project("db-version")},
        analysis={"notes": "x" * (proposals.ANALYSIS_MAX_BYTES + 1)},
    )

    assert (resp.status_code, resp.json()["field"]) == (400, "analysis")


# ── Undo, withdraw, expiry ─────────────────────────────────────


async def test_undo_before_spawn_drops_the_project(cockpit: Cockpit) -> None:
    await propose(
        cockpit,
        {"op": "create", "project": project("settings", depends_on=["api-version"])},
        reply_to=_human_chat(cockpit),
    )
    assert cockpit.store.get_project(cockpit.fid, "settings") is not None

    resp = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/undo")

    assert resp.status_code == 200, resp.text
    assert resp.json()["proposal"]["status"] == "undone"
    assert cockpit.store.get_project(cockpit.fid, "settings") is None
    assert "proposal.undone" in [e.kind for e in cockpit.events()]


async def test_undo_after_the_pl_spawned_is_refused(cockpit: Cockpit) -> None:
    await propose(
        cockpit,
        {"op": "update", "slug": "api-version", "patch": {"priority": 0}},
        {"op": "create", "project": project("db-version")},
        reply_to=_human_chat(cockpit),
    )
    row = cockpit.store.get_project(cockpit.fid, "db-version")
    assert row is not None
    cockpit.store.update_project(row.id, pl_session_id=cockpit.world.id("lead_b"))

    resp = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/undo")

    assert (resp.status_code, error_code(resp)) == (409, "already_spawned")
    assert _proposal(cockpit).status == "applied"


async def test_undo_after_atlas_was_asked_to_spawn_is_refused(cockpit: Cockpit) -> None:
    # Header auth: the create went to Atlas as a spawn request.
    await propose(
        cockpit,
        {"op": "create", "project": project("db-version")},
        reply_to=_human_chat(cockpit),
    )
    assert any("Spawn its PL now" in t for t in cockpit.mailer.texts_to(cockpit.fid))

    resp = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/undo")

    assert (resp.status_code, error_code(resp)) == (409, "already_spawned")
    assert cockpit.store.get_project(cockpit.fid, "db-version") is not None


async def test_undo_refuses_when_an_updated_project_closed(cockpit: Cockpit) -> None:
    await propose(
        cockpit,
        {"op": "update", "slug": "api-version", "patch": {"priority": 0}},
        reply_to=_human_chat(cockpit),
    )
    row = cockpit.store.get_project(cockpit.fid, "api-version")
    assert row is not None
    cockpit.store.update_project(row.id, status="cancelled")

    resp = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/undo")

    assert (resp.status_code, error_code(resp)) == (409, "undo_closed")
    assert _proposal(cockpit).status == "applied"


async def test_owner_class_reject_and_undo_need_an_owner(cockpit: Cockpit) -> None:
    await propose(
        cockpit,
        {"op": "create", "project": project("infra", repo="caffeinelabs/infra")},
    )
    reject_url = f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/reject"

    by_operator = await cockpit.post(reject_url, {"note": "no"}, user="carol")
    by_owner = await cockpit.post(reject_url, {"note": "no"})

    assert by_operator.status_code == 403, by_operator.text
    assert by_owner.status_code == 200, by_owner.text

    await propose(
        cockpit,
        {"op": "update", "slug": "api-version", "patch": {"budgets": {"task_usd": 5}}},
    )
    applied = await _apply(cockpit, "C-2")
    assert applied.status_code == 200, applied.text
    undo_url = f"/v1/factory/factories/{cockpit.fid}/proposals/C-2/undo"

    undo_by_operator = await cockpit.post(undo_url, user="carol")
    undo_by_owner = await cockpit.post(undo_url)

    assert undo_by_operator.status_code == 403, undo_by_operator.text
    assert undo_by_owner.status_code == 200, undo_by_owner.text


async def test_undo_restores_an_update(cockpit: Cockpit) -> None:
    await propose(
        cockpit,
        {"op": "update", "slug": "api-version", "patch": {"priority": 0}},
        reply_to=_human_chat(cockpit),
    )
    row = cockpit.store.get_project(cockpit.fid, "api-version")
    assert row is not None and row.priority == 0

    resp = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/undo")

    assert resp.status_code == 200, resp.text
    row = cockpit.store.get_project(cockpit.fid, "api-version")
    assert row is not None and row.priority == 2
    updates = cockpit.mailer.texts_to(cockpit.world.id("pl"))
    assert any("(undo C-1)" in t and "priority P2" in t for t in updates), updates


async def test_the_proposer_withdraws_its_pending_proposal(cockpit: Cockpit) -> None:
    await propose(cockpit, {"op": "create", "project": project("db-version")})

    other = await propose(cockpit, key="pl", withdraw="C-1")
    mine = await propose(cockpit, withdraw="C-1")

    assert (other.status_code, error_code(other)) == (403, "not_allowed")
    assert mine.json()["status"] == "withdrawn"
    decision = cockpit.store.get_decision(_proposal(cockpit).decision_id)
    assert decision is not None and decision.status == "withdrawn"


async def test_pending_proposals_expire_after_72_hours(cockpit: Cockpit) -> None:
    await propose(cockpit, {"op": "create", "project": project("db-version")})

    early = await proposals.expire_due(cockpit.app.state, cockpit.fid)
    late = await proposals.expire_due(
        cockpit.app.state, cockpit.fid, now=now_s() + proposals.PROPOSAL_EXPIRY_S + 1
    )

    assert early == [] and late == ["C-1"]
    row = _proposal(cockpit)
    assert row.status == "expired"
    decision = cockpit.store.get_decision(row.decision_id)
    assert decision is not None and decision.status == "expired"
    assert "[Factory proposal · C-1 expired]" in cockpit.mailer.texts_to(cockpit.fid)[0]


async def test_grace_applies_after_15_minutes_without_objection(cockpit: Cockpit) -> None:
    cockpit.world.store.set_labels(cockpit.fid, {"omnigent.factory.autonomy_projects": "grace"})
    await propose(cockpit, {"op": "create", "project": project("db-version")})

    early = await proposals.apply_graceful(cockpit.app.state, cockpit.fid)
    late = await proposals.apply_graceful(
        cockpit.app.state, cockpit.fid, now=now_s() + proposals.GRACE_S + 1
    )

    assert early == [] and late == ["C-1"]
    assert _proposal(cockpit).status == "applied"
    assert cockpit.store.get_project(cockpit.fid, "db-version") is not None


# ── Routes ─────────────────────────────────────────────────────


async def test_people_read_proposals_and_agents_read_projects(cockpit: Cockpit) -> None:
    await propose(cockpit, {"op": "create", "project": project("db-version")})

    listing = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/proposals?status=pending",
        headers={"X-Test-User": "carol"},
    )
    one = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/proposals/C-1", headers={"X-Test-User": "carol"}
    )
    outsider = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/proposals", headers={"X-Test-User": "bob"}
    )
    lead = await cockpit.client.get(
        "/v1/factory/agent/projects",
        params={"from_session_id": cockpit.world.id("lead_a")},
        headers={"X-Test-User": "alice"},
    )

    bad_kind = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/proposals?kind=nope",
        headers={"X-Test-User": "carol"},
    )
    assert (bad_kind.status_code, error_code(bad_kind)) == (400, "invalid_kind")
    assert [p["ref"] for p in listing.json()["data"]] == ["C-1"]
    assert one.json()["proposal"]["ops"][0]["project"]["slug"] == "db-version"
    assert one.json()["proposal"]["decision_ref"].startswith("D-")
    assert one.json()["proposal"]["author_title"] == "Atlas"
    assert outsider.status_code == 404
    assert [p["slug"] for p in lead.json()["projects"]] == ["api-version"]
    assert lead.json()["pending_proposals"][0]["proposal"] == "C-1"


async def test_human_write_routes_refuse_runners(cockpit: Cockpit) -> None:
    await propose(cockpit, {"op": "create", "project": project("db-version")})

    resp = await cockpit.client.post(
        f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/apply",
        json={},
        headers={"X-Test-User": "alice", RUNNER_TUNNEL_TOKEN_HEADER: "rt_abc.def"},
    )

    assert (resp.status_code, error_code(resp)) == (403, "human_only")
    writes = [
        route
        for route in proposals_api.router.routes
        if isinstance(route, APIRoute)
        and route.methods & {"POST", "PUT", "PATCH", "DELETE"}
        and "/agent/" not in route.path
    ]
    assert len(writes) == 6
    for route in writes:
        assert require_human_origin in [d.call for d in route.dependant.dependencies], route.path


async def test_events_carry_the_actor(cockpit: Cockpit) -> None:
    await propose(cockpit, {"op": "create", "project": project("db-version")})
    await _apply(cockpit, "C-1")
    await settle()

    [opened] = cockpit.events("proposal.opened")
    [applied] = cockpit.events("proposal.applied")
    assert opened.data["by_kind"] == "agent" and opened.data["by_role"] == "lead"
    assert applied.data["by"] == "alice" and applied.data["by_kind"] == "human"
    assert all(len(e.kind) <= 32 for e in cockpit.events())
    assert {"pending"} <= F.PROPOSAL_OPEN_STATUSES
