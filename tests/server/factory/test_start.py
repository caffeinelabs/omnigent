"""``POST /v1/factory/factories``, ``POST …/projects`` and ``PATCH …/projects/{slug}``:
labels, project rows, the sidebar folder, the kickoff brief, idempotency,
access, validation and the charter-update diff."""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.server.auth import LEVEL_OWNER
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit, error_code
from tests.server.factory.conftest import FactoryWorld

GOLDEN = Path(__file__).parent / "golden"

PROJECTS: list[dict[str, Any]] = [
    {
        "slug": "api-status",
        "title": "API status endpoint",
        "repo": "caffeinelabs/app",
        "base_branch": "main",
        "goal": 'Add GET /status to caffeine-api returning {service, uptimeS, db:"ok"|"down"}.',
        "dod": ['curl -fsS {api}/status returns 200 with "db":"ok"', "unit test passes"],
        "scope_paths": ["services/caffeine-api/**"],
        "env_needed": True,
        "preview": {"enabled": True, "slots": 2},
        "demo_focus": "API capture of GET /status",
        "priority": 1,
        "budget_ics": 3,
    },
    {
        "slug": "docs-factory-envs",
        "title": "Factory envs docs",
        "repo": "caffeinelabs/app",
        "goal": "Add docs/factory-envs.md.",
        "env_needed": False,
        "priority": 2,
    },
]


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    monkeypatch.delenv("OMNIGENT_FACTORY_PREVIEW_TOOLING_REF", raising=False)
    async for value in build_cockpit(world, db_uri, monkeypatch):
        yield value


def _new_lead(cockpit: Cockpit, key: str = "atlas", agent: str = "lead") -> str:
    conv = cockpit.world.make_session(key, agent=agent, title="Atlas")
    cockpit.permissions.grant("alice", conv.id, LEVEL_OWNER)
    return conv.id


def _body(root_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "root_session_id": root_id,
        "name": "Atlas factory",
        "cadence": "demo",
        "projects": copy.deepcopy(PROJECTS),
    }
    body.update(overrides)
    return body


async def _start(cockpit: Cockpit, body: dict[str, Any], *, user: str = "alice") -> Any:
    return await cockpit.post("/v1/factory/factories", body, user=user)


# ── Start ──────────────────────────────────────────────────────


async def test_start_writes_labels_rows_and_the_folder(cockpit: Cockpit) -> None:
    root = _new_lead(cockpit)

    resp = await _start(cockpit, _body(root))

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["factory_id"] == root
    assert body["url"] == f"/factory/{root}"
    assert body["created"] is True
    conv = cockpit.world.get("atlas")
    labels = conv.labels
    assert labels[F.LABEL_ID] == root
    assert labels[F.LABEL_ROLE] == "lead"
    assert labels[F.LABEL_STATE] == "active"
    assert labels[F.LABEL_NAME] == "Atlas factory"
    assert labels[F.LABEL_CADENCE] == "demo"
    assert labels[F.LABEL_HEARTBEAT_S] == "300"
    assert int(labels[F.LABEL_STARTED_AT]) > 0
    folder_id = labels[F.LABEL_FOLDER_PROJECT]
    folder = cockpit.project_store.get(folder_id, user_id="alice")
    assert folder is not None
    assert folder.name == "🏭 Atlas factory"
    assert folder.config == {"icon": "🏭"}
    assert conv.project_id == folder_id

    rows = {p.slug: p for p in cockpit.store.list_projects(root)}
    assert set(rows) == {"api-status", "docs-factory-envs"}
    api = rows["api-status"]
    assert api.stage == "queued"
    assert api.priority == 1
    assert api.repo == "caffeinelabs/app"
    assert api.base_branch == "main"
    assert api.created_by == "alice"
    assert api.charter is not None
    assert api.charter["preview"] == {"enabled": True, "slots": 2}
    assert api.charter["demo_focus"] == "API capture of GET /status"
    assert api.charter["scope_paths"] == ["services/caffeine-api/**"]
    docs = rows["docs-factory-envs"]
    assert docs.charter is not None
    assert docs.charter["env_needed"] is False
    assert docs.charter["preview"]["enabled"] is False
    assert docs.title == "Factory envs docs"
    [started] = [e for e in cockpit.store.list_events(root) if e.kind == "factory.started"]
    assert started.data is not None
    assert started.data["projects"] == ["api-status", "docs-factory-envs"]


async def test_the_brief_is_queued_once_with_the_golden_text(cockpit: Cockpit) -> None:
    root = _new_lead(cockpit)

    resp = await _start(cockpit, _body(root))

    [item] = cockpit.mailer.items
    assert item.target_id == root
    assert item.kind == "brief"
    assert item.sender_id is None
    assert item.mail_id == resp.json()["mail_id"]
    expected = (
        (GOLDEN / "start_kickoff_brief.txt")
        .read_text()
        .rstrip("\n")
        .replace("<FID>", root)
        .replace("<MAIL>", item.mail_id)
    )
    assert item.text == expected


async def test_start_is_idempotent_for_the_same_slugs(cockpit: Cockpit) -> None:
    root = _new_lead(cockpit)
    assert (await _start(cockpit, _body(root))).status_code == 201

    again = await _start(cockpit, _body(root, name="Renamed"))
    other = await _start(cockpit, _body(root, projects=[PROJECTS[0] | {"slug": "something-else"}]))

    assert again.status_code == 200
    assert again.json() == {"factory_id": root, "url": f"/factory/{root}", "created": False}
    assert (other.status_code, error_code(other)) == (409, "factory_exists")
    assert other.json()["projects"] == ["api-status", "docs-factory-envs"]
    assert len(cockpit.mailer.items) == 1
    assert cockpit.world.get("atlas").labels[F.LABEL_NAME] == "Atlas factory"
    assert len(cockpit.project_store.list(user_id="alice")) == 1


async def test_start_reuses_a_folder_and_keeps_a_user_chosen_project(cockpit: Cockpit) -> None:
    root = _new_lead(cockpit)
    existing = cockpit.project_store.create("a" * 32, "🏭 Atlas factory", "alice")
    mine = cockpit.project_store.create("b" * 32, "My stuff", "alice")
    cockpit.world.store.set_conversation_project(root, mine.id)

    assert (await _start(cockpit, _body(root))).status_code == 201

    conv = cockpit.world.get("atlas")
    assert conv.labels[F.LABEL_FOLDER_PROJECT] == existing.id
    assert conv.project_id == mine.id


async def test_start_needs_owner_on_the_root(cockpit: Cockpit) -> None:
    root = cockpit.fid  # carol has EDIT on it, bob nothing

    editor = await _start(cockpit, _body(root), user="carol")
    outsider = await _start(cockpit, _body(root), user="bob")

    assert editor.status_code == 403
    assert outsider.status_code == 404
    assert cockpit.mailer.items == []
    assert cockpit.store.list_projects(root) == []


@pytest.mark.parametrize("key", ["plain", "pl", "ic"])
async def test_start_refuses_a_non_lead_session(cockpit: Cockpit, key: str) -> None:
    resp = await _start(cockpit, _body(cockpit.world.id(key)))

    assert (resp.status_code, error_code(resp)) == (400, "not_a_factory_lead")
    assert cockpit.mailer.items == []


async def test_start_refuses_when_the_factory_is_off(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _new_lead(cockpit)
    monkeypatch.delenv(F.FACTORY_ENABLED_ENV, raising=False)

    resp = await _start(cockpit, _body(root))

    assert (resp.status_code, error_code(resp)) == (409, "factory_disabled")
    assert cockpit.mailer.items == []
    assert cockpit.store.list_projects(root) == []
    assert F.LABEL_ID not in cockpit.world.get("atlas").labels


async def test_a_preview_without_enabled_follows_env_needed(cockpit: Cockpit) -> None:
    root = _new_lead(cockpit)
    projects = copy.deepcopy(PROJECTS)
    projects[0]["preview"] = {"slots": 3}
    projects[1]["preview"] = {"slots": 3}

    assert (await _start(cockpit, _body(root, projects=projects))).status_code == 201

    rows = {p.slug: p for p in cockpit.store.list_projects(root)}
    assert rows["api-status"].charter["preview"] == {"enabled": True, "slots": 3}
    assert rows["docs-factory-envs"].charter["preview"]["enabled"] is False


async def test_start_refuses_a_lead_of_another_factory(cockpit: Cockpit) -> None:
    resp = await _start(cockpit, _body(cockpit.world.id("lead_b")))

    assert (resp.status_code, error_code(resp)) == (409, "not_a_factory_root")


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (lambda b: b["projects"][1].update(slug="Bad Slug"), "projects[1].slug"),
        (lambda b: b["projects"][1].update(slug="api-status"), "projects[1].slug"),
        (lambda b: b["projects"][0].update(repo="not a repo"), "projects[0].repo"),
        (lambda b: b["projects"][0].update(repo="o/" + "r" * 300), "projects[0].repo"),
        (lambda b: b["projects"][0].update(goal=""), "projects[0].goal"),
        (lambda b: b["projects"][0].update(priority=7), "projects[0].priority"),
        (
            lambda b: b["projects"][0].update(preview={"enabled": True, "slots": 9}),
            "projects[0].preview.slots",
        ),
        (lambda b: b["projects"][0].update(surprise=True), "projects[0].surprise"),
        (lambda b: b.update(projects=[]), "projects"),
        (lambda b: b.update(cadence="turbo"), "cadence"),
        (lambda b: b.update(name="two\nlines"), "name"),
        (lambda b: b.update(root_session_id="nope"), "root_session_id"),
    ],
)
async def test_validation_errors_name_the_field(cockpit: Cockpit, mutate: Any, field: str) -> None:
    body = _body(_new_lead(cockpit))
    mutate(body)

    resp = await _start(cockpit, body)

    assert resp.status_code == 400, resp.text
    assert resp.json()["field"] == field
    assert resp.json()["error"]["field"] == field
    assert resp.json()["message"]
    assert cockpit.mailer.items == []


# ── Add project ────────────────────────────────────────────────


async def test_add_project_asks_the_root_to_spawn_its_pl(cockpit: Cockpit) -> None:
    root = _new_lead(cockpit)
    await _start(cockpit, _body(root, cadence="normal"))
    cockpit.mailer.items.clear()
    project = {
        "slug": "ux-env-badge",
        "title": "ENV badge",
        "repo": "caffeinelabs/app",
        "base_branch": "main",
        "goal": "Show an ENV badge in the UX header on factory envs.",
        "demo_focus": "video of the header badge",
    }

    resp = await cockpit.post(f"/v1/factory/factories/{root}/projects", project, user="alice")
    dup = await cockpit.post(f"/v1/factory/factories/{root}/projects", project, user="alice")

    assert resp.status_code == 201, resp.text
    assert resp.json()["project"]["slug"] == "ux-env-badge"
    assert resp.json()["project"]["stage"] == "queued"
    assert "workspace_id" not in resp.json()["project"]
    assert (dup.status_code, error_code(dup)) == (409, "slug_exists")
    [item] = cockpit.mailer.items
    assert item.target_id == root
    assert item.text == (
        f"[Factory brief · from alice via cockpit · id={item.mail_id}] Human added project "
        "ux-env-badge. Spawn its PL now: sys_factory_spawn(agent='factory-pl', "
        "role='project_lead', title='PL · ux-env-badge', project='ux-env-badge', "
        "workspaces=['https://github.com/caffeinelabs/app#main'], heartbeat_s=7200, "
        "message='Start. Your charter follows.')"
    )
    row = cockpit.store.get_project(root, "ux-env-badge")
    assert row is not None and row.charter is not None
    assert row.charter["demo_focus"] == "video of the header badge"


async def test_add_project_access_and_factory_checks(cockpit: Cockpit) -> None:
    project = {"slug": "x", "repo": "caffeinelabs/app", "goal": "g"}

    outsider = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/projects", project, user="bob"
    )
    not_root = await cockpit.post(
        f"/v1/factory/factories/{cockpit.world.id('pl')}/projects", project, user="alice"
    )
    editor = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/projects", project, user="carol"
    )

    assert outsider.status_code == 404
    assert (not_root.status_code, error_code(not_root)) == (404, "not_a_factory")
    assert editor.status_code == 201


# ── Charter PATCH ──────────────────────────────────────────────


def _seed_project(cockpit: Cockpit) -> None:
    cockpit.store.upsert_project(
        cockpit.fid,
        "api-version",
        title="API version",
        repo="caffeinelabs/app",
        base_branch="main",
        charter={"goal": "Add GET /version.", "dod": ["curl /version returns 200"]},
        priority=1,
        pl_session_id=cockpit.world.id("pl"),
    )


async def test_charter_patch_sends_the_pl_a_unified_diff(cockpit: Cockpit) -> None:
    _seed_project(cockpit)
    path = f"/v1/factory/factories/{cockpit.fid}/projects/api-version"

    resp = await cockpit.patch(
        path, {"goal": "Add GET /version with the git sha.", "preview_slots": 3}, user="carol"
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["changed"] is True
    assert "workspace_id" not in resp.json()["project"]
    row = cockpit.store.get_project(cockpit.fid, "api-version")
    assert row is not None and row.charter is not None
    assert row.charter["goal"] == "Add GET /version with the git sha."
    assert row.charter["preview"]["slots"] == 3
    assert row.charter["dod"] == ["curl /version returns 200"]
    [item] = cockpit.mailer.to(cockpit.world.id("pl"))
    assert resp.json()["mail_id"] == item.mail_id
    head, diff = item.text.split("\n", 1)
    assert head == (
        "[Factory control · CHARTER UPDATE · by carol] Your charter changed: follow the new one "
        'from your next step, update plan.md, and reply once: sys_session_post(to="parent", '
        'kind="reply").'
    )
    assert diff.startswith("```diff\n--- charter (before)\n+++ charter (after)\n")
    assert diff.endswith("\n```")
    assert "-Goal: Add GET /version.\n+Goal: Add GET /version with the git sha." in diff
    assert "up to 3 at once" in diff
    [event] = cockpit.events("control.charter_update")
    assert event.data["fields"] == ["goal", "preview_slots"]


async def test_a_slots_only_preview_patch_keeps_previews_off(cockpit: Cockpit) -> None:
    _seed_project(cockpit)
    path = f"/v1/factory/factories/{cockpit.fid}/projects/api-version"
    off = await cockpit.patch(path, {"preview": {"enabled": False, "slots": 2}})
    assert off.status_code == 200, off.text
    cockpit.mailer.items.clear()

    resp = await cockpit.patch(path, {"preview": {"slots": 3}})

    assert resp.status_code == 200, resp.text
    row = cockpit.store.get_project(cockpit.fid, "api-version")
    assert row is not None and row.charter is not None
    assert row.charter["preview"] == {"enabled": False, "slots": 3}
    assert all(
        "factory previews: on" not in t for t in cockpit.mailer.texts_to(cockpit.world.id("pl"))
    )
    on = await cockpit.patch(path, {"preview": {"enabled": True}})
    assert on.status_code == 200
    row = cockpit.store.get_project(cockpit.fid, "api-version")
    assert row is not None and row.charter is not None
    assert row.charter["preview"]["enabled"] is True
    assert row.charter["preview"]["slots"] == 3


def test_merge_charter_copies_enabled_only_when_sent() -> None:
    from omnigent.server.factory.start import CharterPatchIn, merge_charter

    old = {"goal": "g", "preview": {"enabled": False, "slots": 1}}

    slots = merge_charter(old, CharterPatchIn.model_validate({"preview": {"slots": 3}}))
    legacy = merge_charter(old, CharterPatchIn.model_validate({"preview_slots": 2}))
    enabled = merge_charter(old, CharterPatchIn.model_validate({"preview": {"enabled": True}}))

    assert slots["preview"] == {"enabled": False, "slots": 3}
    assert legacy["preview"] == {"enabled": False, "slots": 2}
    assert enabled["preview"] == {"enabled": True, "slots": 1}


async def test_charter_patch_without_a_change_sends_nothing(cockpit: Cockpit) -> None:
    _seed_project(cockpit)
    path = f"/v1/factory/factories/{cockpit.fid}/projects/api-version"

    resp = await cockpit.patch(path, {"goal": None, "budget_ics": None})

    assert resp.status_code == 200
    assert resp.json()["changed"] is False
    assert cockpit.mailer.items == []


async def test_charter_patch_priority_and_demo_alias(cockpit: Cockpit) -> None:
    _seed_project(cockpit)
    path = f"/v1/factory/factories/{cockpit.fid}/projects/api-version"

    resp = await cockpit.patch(path, {"priority": 0, "demo": "Settings shows the version"})

    assert resp.status_code == 200, resp.text
    row = cockpit.store.get_project(cockpit.fid, "api-version")
    assert row is not None and row.charter is not None
    assert row.priority == 0
    assert row.charter["demo_focus"] == "Settings shows the version"
    [text] = cockpit.mailer.texts_to(cockpit.world.id("pl"))
    assert "+Title: API version · priority P0" in text


async def test_charter_patch_refusals(cockpit: Cockpit) -> None:
    _seed_project(cockpit)
    base = f"/v1/factory/factories/{cockpit.fid}/projects"

    unknown = await cockpit.patch(f"{base}/nope", {"goal": "x"})
    bad = await cockpit.patch(f"{base}/api-version", {"budget_ics": 99})
    extra = await cockpit.patch(f"{base}/api-version", {"repo": "other/repo"})
    outsider = await cockpit.patch(f"{base}/api-version", {"goal": "x"}, user="bob")

    assert (unknown.status_code, error_code(unknown)) == (404, "unknown_project")
    assert (bad.status_code, bad.json()["field"]) == (400, "budget_ics")
    assert (extra.status_code, extra.json()["field"]) == (400, "repo")
    assert outsider.status_code == 404
    assert cockpit.mailer.items == []
