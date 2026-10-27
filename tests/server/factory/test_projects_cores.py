"""The project cores: create, update, cancel, split and merge, and the lifecycle they leave.

Every caller (cockpit forms, the human routes, applied proposals) goes through
these cores with an actor. Cancel stops the PL and leaves its PRs open; split
and merge move PRs only through an explicit ``pr_map``; closed projects carry
an overlay and fold below the live ones.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.entities.factory import FactoryProject
from omnigent.server.auth import LEVEL_OWNER
from omnigent.server.factory import snapshot
from omnigent.server.factory.charter import (
    normalize_charter,
    render_kickoff_brief,
    render_pl_charter,
)
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import (
    BASE,
    REPO,
    header_cockpit,
    project,
)


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in header_cockpit(world, db_uri, monkeypatch):
        yield value


GOLDEN = Path(__file__).parent / "golden"


def _url(cockpit: Cockpit, slug: str, action: str) -> str:
    return f"/v1/factory/factories/{cockpit.fid}/projects/{slug}/{action}"


def _pr(cockpit: Cockpit, number: int, slug: str, state: str = "open") -> None:
    cockpit.store.upsert_pr(
        cockpit.fid,
        REPO,
        number,
        url=f"https://github.com/{REPO}/pull/{number}",
        project_slug=slug,
        state=state,
    )


def _second_pl(cockpit: Cockpit, slug: str = "db-version") -> str:
    cockpit.store.upsert_project(
        cockpit.fid, slug, title=slug, repo=REPO, base_branch=BASE, charter={"goal": "g"}
    )
    conv = cockpit.world.make_session(
        f"pl_{slug}",
        agent="pl",
        title=f"PL · {slug}",
        labels={
            F.LABEL_ID: cockpit.fid,
            F.LABEL_ROLE: "project_lead",
            F.LABEL_PROJECT: slug,
            F.LABEL_STATE: "active",
        },
    )
    cockpit.permissions.grant("alice", conv.id, LEVEL_OWNER)
    return conv.id


async def _snapshot(cockpit: Cockpit) -> dict[str, Any]:
    snapshot.reset_cache()
    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}", headers={"X-Test-User": "alice"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── Cancel ─────────────────────────────────────────────────────


async def test_cancel_stops_the_pl_and_leaves_its_prs_open(cockpit: Cockpit) -> None:
    _pr(cockpit, 9630, "api-version")
    _second_pl(cockpit)

    resp = await cockpit.post(_url(cockpit, "api-version", "cancel"), {"reason": "not needed"})

    assert resp.status_code == 200, resp.text
    row = cockpit.store.get_project(cockpit.fid, "api-version")
    assert row is not None and row.status == "cancelled" and row.closed_at
    [stop] = cockpit.mailer.texts_to(cockpit.world.id("pl"))
    assert stop.startswith("[Factory control · STOP · by alice]")
    assert "not needed" in stop
    assert cockpit.world.get("pl").labels[F.LABEL_STATE] == "done"
    pr = cockpit.store.get_pr_by_number(cockpit.fid, REPO, 9630)
    assert pr is not None and pr.state == "open"
    [cancelled] = cockpit.events("project.cancelled")
    assert cancelled.data["prs_left_open"] == [9630]
    assert cancelled.data["by_kind"] == "human" and cancelled.data["by_role"] == "owner"

    body = await _snapshot(cockpit)
    assert [p["slug"] for p in body["projects"]] == ["db-version", "api-version"]
    closed = body["projects"][-1]
    assert (closed["status"], closed["overlay"], closed["overlay_reason"]) == (
        "cancelled",
        "cancelled",
        "Cancelled",
    )
    listing = await cockpit.client.get("/v1/factory/factories", headers={"X-Test-User": "alice"})
    [summary] = [f for f in listing.json()["data"] if f["id"] == body["factory"]["id"]]
    assert summary["projects_total"] == 1

    again = await cockpit.post(_url(cockpit, "api-version", "cancel"), {})
    assert (again.status_code, error_code(again)) == (409, "project_closed")


async def test_cancel_can_keep_previews_for_review(cockpit: Cockpit) -> None:
    resp = await cockpit.post(
        _url(cockpit, "api-version", "cancel"), {"reason": "r", "teardown_previews": False}
    )

    assert resp.status_code == 200, resp.text
    [pause] = cockpit.mailer.texts_to(cockpit.world.id("pl"))
    assert pause.startswith("[Factory control · PAUSE · by alice]")
    assert "keep your factory previews up" in pause


# ── Split ──────────────────────────────────────────────────────


async def test_split_refuses_open_prs_left_behind(cockpit: Cockpit) -> None:
    _pr(cockpit, 9630, "api-version")
    _pr(cockpit, 9631, "api-version", state="merged")

    resp = await cockpit.post(
        _url(cockpit, "api-version", "split"),
        {"into": [project("api-route"), project("api-docs")], "reason": "two outcomes"},
    )

    assert (resp.status_code, error_code(resp)) == (409, "unmapped_prs")
    assert resp.json()["prs"] == [9630]
    assert resp.json()["field"] == "changes[0].pr_map"
    assert cockpit.store.get_project(cockpit.fid, "api-route") is None


async def test_split_moves_prs_by_pr_map_and_cancels_the_original(cockpit: Cockpit) -> None:
    _pr(cockpit, 9630, "api-version")

    resp = await cockpit.post(
        _url(cockpit, "api-version", "split"),
        {
            "into": [project("api-route"), project("api-docs", depends_on=["api-route"])],
            "pr_map": {"9630": "api-route"},
            "reason": "two outcomes",
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["started"] == {"api-route": "lead", "api-docs": "held"}
    route = cockpit.store.get_project(cockpit.fid, "api-route")
    assert route is not None and route.parent_slug == "api-version"
    pr = cockpit.store.get_pr_by_number(cockpit.fid, REPO, 9630)
    assert pr is not None and pr.project_slug == "api-route"
    original = cockpit.store.get_project(cockpit.fid, "api-version")
    assert original is not None and original.status == "cancelled"
    [split] = cockpit.events("project.split")
    assert split.data["pr_map"] == {"9630": "api-route"} and split.data["keep"] == "cancel"


async def test_an_umbrella_split_keeps_the_original(cockpit: Cockpit) -> None:
    resp = await cockpit.post(
        _url(cockpit, "api-version", "split"),
        {"into": [project("api-route")], "keep": "umbrella", "reason": "r"},
    )

    assert resp.status_code == 200, resp.text
    original = cockpit.store.get_project(cockpit.fid, "api-version")
    assert original is not None and original.status == "active"


async def test_a_split_that_fails_half_way_takes_its_children_back(cockpit: Cockpit) -> None:
    from omnigent.server.factory import projects
    from omnigent.server.factory.actor import Actor
    from omnigent.server.factory.control import CockpitError
    from omnigent.server.factory.start import ProjectIn

    # "api-docs" turned up between validation and the split.
    cockpit.store.upsert_project(cockpit.fid, "api-docs", title="d", repo=REPO, status="cancelled")
    root = cockpit.world.store.get_conversation(cockpit.fid)
    into = [
        ProjectIn.model_validate({**project(s), "repo": REPO}) for s in ("api-route", "api-docs")
    ]

    with pytest.raises(CockpitError) as refused:
        await projects.split_project_core(
            cockpit.app.state,
            root,
            "api-version",
            into,
            "cancel",
            {},
            Actor.human("alice", "owner"),
        )

    assert refused.value.code == "slug_exists"
    assert cockpit.store.get_project(cockpit.fid, "api-route") is None
    original = cockpit.store.get_project(cockpit.fid, "api-version")
    assert original is not None and original.status == "active"


# ── Dependencies through the human routes ──────────────────────


async def test_the_cores_refuse_unknown_dependencies_and_cycles(cockpit: Cockpit) -> None:
    cockpit.store.upsert_project(
        cockpit.fid,
        "b",
        title="b",
        repo=REPO,
        charter={"goal": "g", "depends_on": ["api-version"]},
    )
    url = f"/v1/factory/factories/{cockpit.fid}/projects"

    cycle = await cockpit.patch(f"{url}/api-version", {"depends_on": ["b"]})
    unknown = await cockpit.patch(f"{url}/api-version", {"depends_on": ["nope"]})
    itself = await cockpit.patch(f"{url}/api-version", {"depends_on": ["api-version"]})
    ghost = await cockpit.post(url, project("c", depends_on=["ghost"]))
    fine = await cockpit.post(url, project("c", depends_on=["b"]))

    assert (cycle.status_code, error_code(cycle)) == (409, "cycle")
    assert cycle.json()["path"] in (["api-version", "b", "api-version"], ["b", "api-version", "b"])
    assert (unknown.status_code, error_code(unknown)) == (400, "unknown_dependency")
    assert unknown.json()["field"] == "depends_on"
    assert (itself.status_code, error_code(itself)) == (400, "invalid_body")
    assert (ghost.status_code, error_code(ghost)) == (400, "unknown_dependency")
    assert fine.status_code == 201, fine.text
    row = cockpit.store.get_project(cockpit.fid, "api-version")
    assert row is not None and "depends_on" not in (row.charter or {})


# ── Merge ──────────────────────────────────────────────────────


async def test_merge_moves_prs_and_tells_the_survivor_what_it_inherits(cockpit: Cockpit) -> None:
    other_pl = _second_pl(cockpit)
    _pr(cockpit, 9640, "db-version")

    refused = await cockpit.post(
        _url(cockpit, "api-version", "merge"), {"from": ["db-version"], "reason": "overlap"}
    )
    resp = await cockpit.post(
        _url(cockpit, "api-version", "merge"),
        {
            "from": ["db-version"],
            "pr_map": {"9640": "api-version"},
            "patch": {"dod": ["GET /version shows the DB version"]},
            "reason": "overlap",
        },
    )

    assert (refused.status_code, error_code(refused)) == (409, "unmapped_prs")
    assert resp.status_code == 200, resp.text
    absorbed = cockpit.store.get_project(cockpit.fid, "db-version")
    assert absorbed is not None
    assert (absorbed.status, absorbed.parent_slug) == ("merged", "api-version")
    pr = cockpit.store.get_pr_by_number(cockpit.fid, REPO, 9640)
    assert pr is not None and pr.project_slug == "api-version"
    [stop] = cockpit.mailer.texts_to(other_pl)
    assert stop.startswith("[Factory control · STOP · by alice]")
    [update] = cockpit.mailer.texts_to(cockpit.world.id("pl"))
    assert update.startswith("[Factory control · CHARTER UPDATE · by alice] Inherited PRs #9640:")
    assert "run PREVIEW for them" in update and "+- GET /version shows the DB version" in update

    body = await _snapshot(cockpit)
    merged = next(p for p in body["projects"] if p["slug"] == "db-version")
    assert (merged["overlay"], merged["overlay_reason"]) == ("merged", "merged → api-version")


# ── Charter additions and the start ────────────────────────────


async def test_charter_additions_round_trip_and_render(cockpit: Cockpit) -> None:
    cockpit.store.upsert_project(cockpit.fid, "db-version", title="db", repo=REPO)
    resp = await cockpit.patch(
        f"/v1/factory/factories/{cockpit.fid}/projects/api-version",
        {
            "depends_on": ["db-version"],
            "depends_mode": "ready",
            "human_owner": "mia@caffeine.ai",
            "budgets": {"task_usd": 5, "project_usd": 40},
        },
    )

    assert resp.status_code == 200, resp.text
    row = cockpit.store.get_project(cockpit.fid, "api-version")
    assert row is not None and row.charter is not None
    assert row.charter["depends_on"] == ["db-version"]
    assert row.charter["depends_mode"] == "ready"
    assert row.charter["budgets"] == {"task_usd": 5.0, "project_usd": 40.0}
    text = render_pl_charter(row)
    assert (
        "Depends on: db-version (the server starts you once each is ready for human merge)" in text
    )
    assert "Human owner: mia@caffeine.ai" in text
    assert "Budgets: per task $5 · project $40" in text

    cleared = await cockpit.patch(
        f"/v1/factory/factories/{cockpit.fid}/projects/api-version",
        {"depends_on": [], "human_owner": None, "budgets": None},
    )
    assert cleared.status_code == 200
    row = cockpit.store.get_project(cockpit.fid, "api-version")
    assert row is not None and "depends_on" not in (row.charter or {})
    assert "human_owner" not in (row.charter or {}) and "budgets" not in (row.charter or {})


def test_charters_without_additions_render_as_before() -> None:
    plain = normalize_charter({"goal": "g"})
    assert set(plain) == {
        "kind",
        "goal",
        "dod",
        "scope_paths",
        "constraints",
        "env_needed",
        "preview",
        "preview_slots",
        "demo_focus",
        "demo",
        "budget_ics",
    }


def _new_root(cockpit: Cockpit) -> str:
    conv = cockpit.world.make_session("atlas", agent="lead", title="Atlas")
    cockpit.permissions.grant("alice", conv.id, LEVEL_OWNER)
    return conv.id


async def test_an_empty_start_with_a_brief(cockpit: Cockpit) -> None:
    root = _new_root(cockpit)
    body = {
        "root_session_id": root,
        "name": "Atlas factory",
        "projects": [],
        "brief": "Show the deployed versions in Settings.",
    }

    resp = await cockpit.post("/v1/factory/factories", body)
    again = await cockpit.post("/v1/factory/factories", body)

    assert resp.status_code == 201, resp.text
    assert again.status_code == 200 and again.json()["created"] is False
    assert cockpit.store.list_projects(root) == []
    [kickoff] = cockpit.mailer.to(root)
    assert "Brief from alice:\nShow the deployed versions in Settings." in kickoff.text
    assert f'reply_to="{kickoff.mail_id}"' in kickoff.text
    assert "never sys_factory_spawn them" in kickoff.text


async def test_a_start_holds_projects_on_their_dependencies(cockpit: Cockpit) -> None:
    root = _new_root(cockpit)
    body = {
        "root_session_id": root,
        "name": "Atlas factory",
        "projects": [
            {**project("db-version"), "repo": REPO},
            {**project("settings", depends_on=["db-version"]), "repo": REPO},
        ],
    }

    resp = await cockpit.post("/v1/factory/factories", body)

    assert resp.status_code == 201, resp.text
    [kickoff] = cockpit.mailer.to(root)
    assert "Do not spawn settings: each waits for its depends_on" in kickoff.text
    settings = cockpit.store.get_project(root, "settings")
    assert settings is not None and (settings.charter or {})["spawn_owner"] == "server"
    db = cockpit.store.get_project(root, "db-version")
    assert db is not None and "spawn_owner" not in (db.charter or {})
    assert [
        e.project_slug for e in cockpit.store.list_events(root) if e.kind == "project.held"
    ] == ["settings"]


def test_the_empty_kickoff_golden() -> None:
    text = render_kickoff_brief(
        "Atlas factory",
        "demo",
        "luka",
        [],
        factory_id="f" * 32,
        brief="Show the deployed versions in Settings: an API version endpoint and a DB one.",
        reply_to="mail_0123456789abcdef",
    )
    golden = (GOLDEN / "empty_kickoff_brief.txt").read_text(encoding="utf-8")
    assert text == golden.removesuffix("\n")


def test_an_integration_only_start_still_plans_from_the_brief() -> None:
    env = FactoryProject(
        id="c" * 32,
        factory_id="f" * 32,
        slug="base-env",
        title="Base env · temp-main",
        repo=REPO,
        base_branch=BASE,
        charter=normalize_charter({"kind": "integration"}),
    )
    text = render_kickoff_brief("A", "demo", "luka", [env], brief="b", reply_to="mail_00000000")
    assert "No projects yet: plan them from the brief" in text
    assert "Spawn base-env yourself:" in text and "heartbeat_s=0" in text
