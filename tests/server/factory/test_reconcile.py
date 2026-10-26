"""The reconcile loop (folder, filing, project rows, stages) and event retention."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from omnigent import factory as F
from omnigent.entities.factory import EventDraft
from omnigent.runtime import user_session_stream
from omnigent.server.factory.background import prune, reconcile
from omnigent.stores.project_store.sqlalchemy_store import SqlAlchemyProjectStore
from tests.server.factory.conftest import FactoryWorld

REPO_LABEL = "omnigent.sandbox.repo.0"


@pytest.fixture()
def projects(world: FactoryWorld, db_uri: str) -> SqlAlchemyProjectStore:
    store = SqlAlchemyProjectStore(db_uri)
    world.deps.project_store = store
    return store


@pytest.fixture()
def announced(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    monkeypatch.setattr(user_session_stream, "publish", lambda _key, frame: frames.append(frame))
    return frames


def _fstore(world: FactoryWorld) -> Any:
    return world.deps.factory_store


async def test_the_folder_is_created_once_and_members_are_filed(
    world: FactoryWorld, projects: SqlAlchemyProjectStore, announced: list[dict[str, Any]]
) -> None:
    report = await reconcile.reconcile_once(world.app_state)

    folder_id = world.get("lead_a").labels[F.LABEL_FOLDER_PROJECT]
    other_id = world.get("other_lead").labels[F.LABEL_FOLDER_PROJECT]
    assert set(report.folders_created) == {folder_id, other_id}
    folder = projects.get(folder_id, user_id=None)
    assert folder is not None and folder.name == "🏭 Atlas"
    assert folder.config == {"icon": "🏭"}
    for key in ("lead_a", "lead_b", "pl"):
        assert world.get(key).project_id == folder_id
    assert world.get("other_lead").project_id == other_id
    # Sub-agents stay where they are (the sidebar hides them anyway); so do non-members.
    assert world.get("ic").project_id is None
    assert world.get("plain").project_id is None
    assert {"type": "projects_changed"} in announced

    announced.clear()
    again = await reconcile.reconcile_once(world.app_state)
    assert again.folders_created == [] and again.filed == []
    assert [p.name for p in projects.list(user_id=None)].count("🏭 Atlas") == 1
    assert {"type": "projects_changed"} not in announced


async def test_a_user_chosen_project_is_never_overridden(
    world: FactoryWorld, projects: SqlAlchemyProjectStore
) -> None:
    mine = projects.create(project_id=uuid.uuid4().hex, name="Mine", user_id=None)
    world.store.set_conversation_project(world.id("pl"), mine.id)

    await reconcile.reconcile_once(world.app_state)

    assert world.get("pl").project_id == mine.id
    assert world.get("lead_b").project_id == world.get("lead_a").labels[F.LABEL_FOLDER_PROJECT]


async def test_a_lost_folder_is_recreated_and_a_name_clash_gets_a_suffix(
    world: FactoryWorld, projects: SqlAlchemyProjectStore
) -> None:
    clash = projects.create(project_id=uuid.uuid4().hex, name="🏭 Atlas", user_id=None)
    world.store.set_labels(world.id("other_lead"), {F.LABEL_FOLDER_PROJECT: clash.id})

    await reconcile.reconcile_once(world.app_state)

    folder_id = world.get("lead_a").labels[F.LABEL_FOLDER_PROJECT]
    assert folder_id != clash.id
    folder = projects.get(folder_id, user_id=None)
    assert folder is not None and folder.name == f"🏭 Atlas · {world.id('lead_a')[:6]}"


async def test_project_rows_are_backfilled_from_pl_labels(
    world: FactoryWorld, projects: SqlAlchemyProjectStore
) -> None:
    world.store.set_labels(
        world.id("pl"), {REPO_LABEL: "https://github.com/caffeinelabs/app#main"}
    )

    report = await reconcile.reconcile_once(world.app_state)

    assert report.projects_created == ["api-version"]
    [row] = _fstore(world).list_projects(world.id("lead_a"))
    assert (row.slug, row.title, row.repo, row.base_branch) == (
        "api-version",
        "api-version",
        "caffeinelabs/app",
        "main",
    )
    assert row.pl_session_id == world.id("pl")
    assert row.charter is None


async def test_the_repo_falls_back_to_the_roots_and_a_missing_pl_is_linked(
    world: FactoryWorld, projects: SqlAlchemyProjectStore
) -> None:
    lead_a = world.id("lead_a")
    world.store.set_labels(lead_a, {REPO_LABEL: "git@github.com:caffeinelabs/app.git"})
    world.make_session(
        "pl2",
        agent="pl",
        title="PL · db-version",
        labels={
            F.LABEL_ID: lead_a,
            F.LABEL_ROLE: "project_lead",
            F.LABEL_PROJECT: "db-version",
            F.LABEL_STATE: "active",
        },
    )
    # A row from the start form, before Atlas spawned the PL.
    _fstore(world).upsert_project(lead_a, "api-version", title="API version endpoint")

    report = await reconcile.reconcile_once(world.app_state)

    rows = {p.slug: p for p in _fstore(world).list_projects(lead_a)}
    assert rows["db-version"].repo == "caffeinelabs/app"
    assert rows["db-version"].base_branch is None
    assert rows["api-version"].title == "API version endpoint"
    assert rows["api-version"].pl_session_id == world.id("pl")
    assert report.pls_linked == ["api-version"]


async def test_stage_is_persisted_and_its_event_fires_once(
    world: FactoryWorld, projects: SqlAlchemyProjectStore
) -> None:
    lead_a = world.id("lead_a")

    first = await reconcile.reconcile_once(world.app_state)
    await reconcile.reconcile_once(world.app_state)

    [row] = _fstore(world).list_projects(lead_a)
    assert first.stages_changed == [("api-version", "queued", "planning")]
    assert row.stage == "planning"
    [event] = _fstore(world).find_events(lead_a, "stage.changed", 0)
    assert event.project_slug == "api-version"
    assert event.data == {"from": "queued", "to": "planning", "overlay": None}

    _fstore(world).upsert_pr(
        lead_a, "caffeinelabs/app", 9630, project_slug="api-version", ready=True
    )
    await reconcile.reconcile_once(world.app_state)
    assert _fstore(world).list_projects(lead_a)[0].stage == "ready"
    events = _fstore(world).find_events(lead_a, "stage.changed", 0)
    assert [e.data["to"] for e in events if e.data] == ["planning", "ready"]
    assert events[-1].severity == "notice"


async def test_reconcile_works_without_a_project_store(world: FactoryWorld) -> None:
    report = await reconcile.reconcile_once(world.app_state)

    assert report.folders_created == [] and report.filed == []
    assert report.projects_created == ["api-version"]
    assert F.LABEL_FOLDER_PROJECT not in world.get("lead_a").labels


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        (
            {REPO_LABEL: "https://github.com/caffeinelabs/app#release-1"},
            ("caffeinelabs/app", "release-1"),
        ),
        ({REPO_LABEL: "git@github.com:org/repo.git"}, ("org/repo", None)),
        ({"omnigent.sandbox.repo": "https://github.com/a/b"}, ("a/b", None)),
        ({}, (None, None)),
    ],
)
def test_repo_from_labels(labels: dict[str, str], expected: tuple[Any, Any]) -> None:
    assert reconcile.repo_from_labels(labels) == expected


# ── retention ──────────────────────────────────────────────────


async def test_prune_drops_old_events_and_older_heartbeats(world: FactoryWorld) -> None:
    store = _fstore(world)
    fid = world.id("lead_a")
    now = 1_800_000_000_000
    day = 86_400_000
    for kind, age_days in (
        ("pr.ready", 15),
        ("pr.ready", 13),
        ("heartbeat.delivered", 3),
        ("heartbeat.skipped", 1),
    ):
        store.append_event(
            EventDraft(factory_id=fid, kind=kind, summary=kind, ts=now - age_days * day)
        )

    assert await prune.prune_once(world.app_state, now=now) == 2

    left = store.find_events(fid, "*", 0)
    assert sorted((e.kind, (now - e.ts) // day) for e in left) == [
        ("heartbeat.skipped", 1),
        ("pr.ready", 13),
    ]


async def test_background_services_start_and_stop(world: FactoryWorld) -> None:
    for module in (reconcile, prune):
        stop = await module.start(world.app_state)
        await stop()
