"""Charter versions: every person's charter edit is a version with Undo, history and Revert.

``PATCH …/projects/{slug}`` records the edit as a human projects proposal that
applied when it was filed (``C-n``, with the charter from before); the
proposal's own Undo puts it back for 10 minutes; ``GET …/charter/versions``
lists the project's charter history; ``POST …/charter/revert`` files the
inverse of one version as a new version.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.factory import COCKPIT_V2_ENV
from omnigent.runner.identity import RUNNER_TUNNEL_TOKEN_HEADER
from omnigent.server.factory import charter_versions, proposals, snapshot, tasks
from omnigent.server.factory.schemas import FactorySnapshot
from omnigent.server.factory.telemetry import metrics
from omnigent.stores.factory_store import now_s
from tests.server.factory.bets_helpers import human_chat
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import REPO, header_cockpit, project, propose

SLUG = "api-version"


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    monkeypatch.setenv(COCKPIT_V2_ENV, "1")
    async for value in header_cockpit(world, db_uri, monkeypatch):
        snapshot.reset_cache()
        yield value


def _path(cockpit: Cockpit, tail: str = "") -> str:
    return f"/v1/factory/factories/{cockpit.fid}/projects/{SLUG}{tail}"


async def _edit(cockpit: Cockpit, patch: dict[str, Any], user: str = "alice") -> Any:
    return await cockpit.patch(_path(cockpit), patch, user=user)


async def _versions(cockpit: Cockpit, user: str = "alice") -> list[dict[str, Any]]:
    resp = await cockpit.client.get(
        _path(cockpit, "/charter/versions"), headers={"X-Test-User": user}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["versions"]


def _revert(cockpit: Cockpit, ref: str, user: str = "alice", **extra: Any) -> Any:
    return cockpit.post(_path(cockpit, "/charter/revert"), {"proposal": ref, **extra}, user=user)


def _charter(cockpit: Cockpit) -> dict[str, Any]:
    row = cockpit.store.get_project(cockpit.fid, SLUG)
    assert row is not None and row.charter is not None
    return row.charter


async def test_an_edit_is_a_version_with_a_ten_minute_undo(cockpit: Cockpit) -> None:
    resp = await _edit(cockpit, {"goal": "Add GET /version with the sha.", "priority": 1})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["changed"] is True and body["proposal"] == "C-1"
    assert body["undo_until"] >= now_s() + proposals.UNDO_WINDOW_S - 5
    row = cockpit.store.get_proposal_by_number(cockpit.fid, 1)
    assert row is not None
    assert (row.status, row.author_kind, row.author) == ("applied", "human", "alice")
    assert row.before[SLUG]["charter"]["goal"] == "Add GET /version."
    assert row.after[SLUG]["charter"]["goal"] == "Add GET /version with the sha."
    assert row.result["charter_edit"] is True
    [event] = cockpit.events("control.charter_update")
    assert event.data["proposal"] == "C-1"

    undo = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/undo")

    assert undo.status_code == 200, undo.text
    assert _charter(cockpit)["goal"] == "Add GET /version."
    restored = cockpit.store.get_project(cockpit.fid, SLUG)
    assert restored is not None and restored.priority == 2
    [version] = await _versions(cockpit)
    assert (version["ref"], version["status"], version["undo_until"]) == ("C-1", "undone", None)
    assert version["revertible"] is False


async def test_without_the_new_cockpit_an_edit_applies_as_before(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(COCKPIT_V2_ENV)

    resp = await _edit(cockpit, {"goal": "Add GET /version with the sha."})
    versions = await cockpit.client.get(
        _path(cockpit, "/charter/versions"), headers={"X-Test-User": "alice"}
    )
    revert = await _revert(cockpit, "C-1")

    assert resp.status_code == 200, resp.text
    assert (resp.json()["changed"], resp.json()["proposal"]) == (True, None)
    assert _charter(cockpit)["goal"] == "Add GET /version with the sha."
    assert cockpit.store.list_proposals(cockpit.fid) == []
    assert "CHARTER UPDATE · by alice]" in cockpit.mailer.texts_to(cockpit.world.id("pl"))[-1]
    assert (versions.status_code, error_code(versions)) == (404, "charter_versions_off")
    assert (revert.status_code, error_code(revert)) == (404, "charter_versions_off")


async def test_an_edit_that_changes_nothing_records_no_version(cockpit: Cockpit) -> None:
    resp = await _edit(cockpit, {"goal": "Add GET /version."})

    assert resp.status_code == 200, resp.text
    assert resp.json()["changed"] is False and resp.json()["proposal"] is None
    assert cockpit.store.list_proposals(cockpit.fid) == []


async def test_a_refused_edit_records_no_version(cockpit: Cockpit) -> None:
    resp = await _edit(cockpit, {"depends_on": ["nope"]})

    assert resp.status_code == 400 and error_code(resp) == "unknown_dependency"
    assert [p.status for p in cockpit.store.list_proposals(cockpit.fid)] in ([], ["refused"])
    assert await _versions(cockpit) == []


async def test_the_history_lists_people_and_agents_newest_first(cockpit: Cockpit) -> None:
    await _edit(cockpit, {"goal": "Add GET /version with the sha."})
    await propose(cockpit, {"op": "update", "slug": SLUG, "patch": {"scope_paths": ["api/**"]}})
    await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-2/apply")
    await propose(cockpit, {"op": "create", "project": project("db-version")})

    versions = await _versions(cockpit)

    assert [v["ref"] for v in versions] == ["C-2", "C-1"]
    agent, person = versions
    assert agent["by_kind"] == "agent" and person["by"] == "alice"
    assert person["changes"] == [
        {"field": "goal", "before": "Add GET /version.", "after": "Add GET /version with the sha."}
    ]
    assert agent["changes"] == [
        {"field": "scope_paths", "before": ["services/api/**"], "after": ["api/**"]}
    ]
    # The agent's change sits on top of the person's: only it can be undone, the person's reverted.
    assert agent["undo_until"] is not None
    assert person["undo_until"] is None and person["revertible"] is True


async def test_revert_files_the_inverse_as_a_new_version(cockpit: Cockpit) -> None:
    await _edit(cockpit, {"goal": "Add GET /version with the sha.", "dod": ["curl it"]})
    await _edit(cockpit, {"constraints": "No new dependencies."})

    resp = await _revert(cockpit, "C-1", note="the sha leaks the build")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["proposal"]["ref"] == "C-3" and body["changed"] is True
    assert "workspace_id" not in body["project"]
    charter = _charter(cockpit)
    assert charter["goal"] == "Add GET /version." and charter["dod"] == []
    assert charter["constraints"] == "No new dependencies."
    [newest, *_] = await _versions(cockpit)
    assert (newest["ref"], newest["revert_of"]) == ("C-3", "C-1")
    assert newest["rationale"] == "Revert C-1: the sha leaks the build"
    updates = cockpit.mailer.texts_to(cockpit.world.id("pl"))
    assert "CHARTER UPDATE · by alice (C-3)" in updates[-1]

    again = await _revert(cockpit, "C-1")
    undone = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-3/undo")

    assert (again.status_code, error_code(again)) == (409, "nothing_to_revert")
    assert undone.status_code == 200
    assert _charter(cockpit)["goal"] == "Add GET /version with the sha."


async def test_undoing_an_older_version_never_wipes_a_newer_edit(cockpit: Cockpit) -> None:
    await _edit(cockpit, {"goal": "Add GET /version with the sha."})
    await _edit(cockpit, {"constraints": "No new dependencies."})
    mails = len(cockpit.mailer.texts_to(cockpit.world.id("pl")))

    stale = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/undo")

    assert (stale.status_code, error_code(stale)) == (409, "undo_stale")
    assert "changed since (C-2); revert it instead" in stale.json()["error"]["message"]
    charter = _charter(cockpit)
    assert charter["goal"] == "Add GET /version with the sha."
    assert charter["constraints"] == "No new dependencies."
    assert len(cockpit.mailer.texts_to(cockpit.world.id("pl"))) == mails
    newest, older = await _versions(cockpit)
    assert (newest["ref"], newest["status"]) == ("C-2", "applied")
    assert newest["undo_until"] is not None
    assert (older["ref"], older["status"], older["undo_until"]) == ("C-1", "applied", None)
    assert older["revertible"] is True

    # Undo the newest first, and the older one can be undone again.
    assert (
        await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-2/undo")
    ).status_code == 200
    [_, again] = await _versions(cockpit)
    assert again["undo_until"] is not None
    undone = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/undo")

    assert undone.status_code == 200, undone.text
    assert _charter(cockpit)["goal"] == "Add GET /version."


async def test_an_agents_change_cannot_be_undone_over_a_persons_edit(cockpit: Cockpit) -> None:
    await propose(cockpit, {"op": "update", "slug": SLUG, "patch": {"scope_paths": ["api/**"]}})
    await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/apply")
    row = cockpit.store.get_proposal_by_number(cockpit.fid, 1)
    assert row is not None and row.after[SLUG]["hash"] == proposals.charter_hash(
        _charter(cockpit), 2
    )
    await _edit(cockpit, {"goal": "Add GET /version with the sha."})

    stale = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/undo")

    assert (stale.status_code, error_code(stale)) == (409, "undo_stale")
    assert _charter(cockpit)["goal"] == "Add GET /version with the sha."
    assert _charter(cockpit)["scope_paths"] == ["api/**"]


def test_the_charter_hash_skips_what_the_server_writes() -> None:
    charter = {"goal": "g", "preview": {"enabled": True, "slots": 1}}
    spawned = {**charter, "spawn_owner": "lead", "preview": {**charter["preview"]}}
    spawned["preview"]["tooling_ref"] = "origin/main"

    assert proposals.charter_hash(charter, 2) == proposals.charter_hash(spawned, 2)
    assert proposals.charter_hash(charter, 2) != proposals.charter_hash(charter, 1)
    assert proposals.charter_hash(charter, 2) != proposals.charter_hash({"goal": "h"}, 2)


async def test_revert_refuses_what_is_not_an_applied_charter_change(cockpit: Cockpit) -> None:
    await _edit(cockpit, {"goal": "Add GET /version with the sha."})
    await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/undo")
    await propose(cockpit, {"op": "create", "project": project("db-version")})

    undone = await _revert(cockpit, "C-1")
    create = await _revert(cockpit, "C-2")
    missing = await _revert(cockpit, "C-99")

    assert (undone.status_code, error_code(undone)) == (409, "not_a_charter_version")
    assert (create.status_code, error_code(create)) == (409, "not_a_charter_version")
    assert (missing.status_code, error_code(missing)) == (404, "unknown_proposal")


async def test_revert_needs_edit_an_owner_for_budgets_and_a_person(cockpit: Cockpit) -> None:
    await _edit(cockpit, {"budgets": {"project_usd": 40}})
    await _edit(cockpit, {"budgets": {"project_usd": 10}})

    outsider = await _revert(cockpit, "C-1", user="bob")
    runner = await cockpit.client.post(
        _path(cockpit, "/charter/revert"),
        json={"proposal": "C-2"},
        headers={"X-Test-User": "alice", RUNNER_TUNNEL_TOKEN_HEADER: "rt_abc.def"},
    )
    raise_back = await _revert(cockpit, "C-2", user="carol")

    assert outsider.status_code in (403, 404)
    assert (runner.status_code, error_code(runner)) == (403, "human_only")
    assert raise_back.status_code == 403
    assert _charter(cockpit)["budgets"] == {"project_usd": 10.0}


async def test_charter_edits_do_not_count_as_decided_proposals(cockpit: Cockpit) -> None:
    await _edit(cockpit, {"goal": "Add GET /version with the sha."})
    await propose(cockpit, {"op": "update", "slug": SLUG, "patch": {"priority": 1}})

    now = now_s()
    facts = metrics.load_facts(cockpit.deps, cockpit.fid, metrics.Window(now - 3600, now + 60))

    assert [p.ref for p in facts.proposals] == ["C-2"]


async def test_many_charter_edits_never_crowd_out_agents_proposals(cockpit: Cockpit) -> None:
    planned = cockpit.store.create_proposal(
        cockpit.fid,
        kind="projects",
        title="Plan (sample)",
        author_kind="agent",
        author=cockpit.world.id("lead_a"),
        dedupe_key="plan-api-version",
        ops=[{"op": "update", "slug": SLUG, "patch": {"priority": 1}}],
        analysis={"hub_files": ["services/api/src/index.ts"]},
    )[0]
    for n in range(60):
        cockpit.store.create_proposal(
            cockpit.fid,
            kind="projects",
            title=f"update {SLUG} (goal)",
            author_kind="human",
            author="alice",
            status="applied",
            ops=[{"op": "update", "slug": SLUG, "patch": {"goal": f"Goal {n}."}}],
            result={charter_versions.RESULT_FLAG: True, "updated": [SLUG]},
        )

    assert tasks.hub_files(cockpit.deps, cockpit.fid) == {SLUG: ["services/api/src/index.ts"]}
    found = proposals._by_dedupe(cockpit.deps, cockpit.fid, "plan-api-version")
    assert found is not None and found.id == planned.id
    assert len(cockpit.store.list_proposals(cockpit.fid, author_kind="human", limit=500)) == 60


def test_inverse_patch_reads_each_field_back_from_the_charter() -> None:
    before = {
        "charter": {
            "goal": "Old goal",
            "dod": ["a"],
            "preview": {"enabled": False, "slots": 2},
            "demo_focus": "the header",
            "budgets": {"task_usd": 5},
        },
        "priority": 3,
    }
    patch = {
        "goal": "New goal",
        "dod": [],
        "preview": {"enabled": True},
        "demo_focus": None,
        "budgets": {"task_usd": 9},
        "priority": 0,
        "depends_mode": "ready",
    }

    assert charter_versions.inverse_patch(patch, before) == {
        "goal": "Old goal",
        "dod": ["a"],
        "preview": {"enabled": False, "slots": 2},
        "demo_focus": "the header",
        "budgets": {"task_usd": 5.0},
        "priority": 3,
    }


# ── Who asked for a project ────────────────────────────────────


async def test_requested_by_names_the_person_or_the_proposing_agent(cockpit: Cockpit) -> None:
    added = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/projects",
        {"slug": "csv-export", "repo": REPO, "goal": "Export a report as CSV."},
    )
    assert added.status_code == 201, added.text
    await propose(cockpit, {"op": "create", "project": project("db-version")})
    await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/apply")

    raw = await snapshot.build_snapshot(cockpit.app.state, cockpit.world.get("lead_a"))

    cards = {p.slug: p for p in FactorySnapshot.model_validate(raw.body).projects}
    person, agent = cards["csv-export"].requested_by, cards["db-version"].requested_by
    assert person is not None and (person.kind, person.name) == ("human", "alice")
    assert agent is not None and (agent.kind, agent.proposal) == ("agent", "C-1")
    assert agent.name == "Atlas"
    assert cards[SLUG].requested_by is None


async def test_split_children_keep_who_asked_and_an_answer_names_the_person(
    cockpit: Cockpit,
) -> None:
    added = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/projects",
        {"slug": "csv-export", "repo": REPO, "goal": "Export a report as CSV."},
        user="carol",
    )
    assert added.status_code == 201, added.text
    split = {
        "op": "split",
        "slug": "csv-export",
        "into": [project("csv-export-api"), project("csv-export-ui")],
        "keep": "umbrella",
    }
    resp = await propose(cockpit, split, reason="two milestones (sample)")
    assert resp.status_code == 200, resp.text
    if resp.json()["status"] != "applied":
        applied = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/apply")
        assert applied.status_code == 200, applied.text
    asked = await propose(
        cockpit, {"op": "create", "project": project("db-version")}, reply_to=human_chat(cockpit)
    )
    assert asked.status_code == 200, asked.text
    if asked.json()["status"] != "applied":
        await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-2/apply")

    for slug in ("csv-export-api", "csv-export-ui"):
        child = cockpit.store.get_project(cockpit.fid, slug)
        assert child is not None
        assert child.charter["requested_by"] == {"kind": "human", "name": "carol"}, slug
    answer = cockpit.store.get_project(cockpit.fid, "db-version")
    assert answer is not None
    assert answer.charter["requested_by"] == {"kind": "human", "name": "alice", "proposal": "C-2"}
