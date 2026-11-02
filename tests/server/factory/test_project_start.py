"""Why a queued project has no working PL yet (``ProjectView.start``) and the stage note.

New projects sat "Queued" for 2-5 minutes with no reason while their PL pods
started; a project made to validate an already-merged PR read "Merged" while
its PL was still deploying the preview.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
import pytest_asyncio

from omnigent.entities.factory import EventDraft, FactoryProject
from omnigent.server.factory import snapshot, spawn
from omnigent.server.factory.snapshot import start_view
from omnigent.server.factory.stage import StageResult
from omnigent.server.routes._sessions.common import _session_sandbox_status_cache
from omnigent.server.schemas import SandboxStatus
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import (
    REPO,
    FakeCreates,
    header_cockpit,
    project,
    propose,
    settle,
    spawning_cockpit,
)

FID = "f" * 32
NOW = 1_793_000_000


@dataclass
class _Pl:
    """What :func:`start_view` reads of a PL session."""

    id: str = "a" * 32
    created_at: int = NOW - 120
    labels: dict[str, str] = field(default_factory=dict)


def _row(spawner: str | None = "server", status: str = "active") -> FactoryProject:
    charter: dict[str, Any] = {"goal": "g"}
    if spawner:
        charter["spawn_owner"] = spawner
    return FactoryProject(
        id="c" * 32,
        factory_id=FID,
        slug="ux-status-page",
        title="Status page",
        charter=charter,
        status=status,
        created_at=NOW - 300,
    )


QUEUED = StageResult("queued")

# (id, project, result, pl, kwargs, expected or None)
CASES: list[tuple[str, FactoryProject, StageResult, Any, dict[str, Any], dict | None]] = [
    ("planning: nothing to explain", _row(), StageResult("planning"), _Pl(), {}, None),
    (
        "waiting on dependencies says so itself",
        _row(),
        StageResult("queued", "waiting", "waiting on db-version"),
        None,
        {},
        None,
    ),
    (
        "a failed sandbox says so itself",
        _row(),
        StageResult("queued", "failed", "PL sandbox failed"),
        _Pl(),
        {"sandbox": "failed"},
        None,
    ),
    ("a cancelled project", _row(status="cancelled"), QUEUED, None, {}, None),
    (
        "pod pending",
        _row(),
        QUEUED,
        _Pl(),
        {"sandbox": "provisioning", "runner_online": False, "briefing": True},
        {
            "state": "starting",
            "phase": "provisioning",
            "label": "Provisioning sandbox",
            "since": NOW - 120,
        },
    ),
    (
        "agent starting in the pod",
        _row(),
        QUEUED,
        _Pl(),
        {"sandbox": "connecting"},
        {
            "state": "starting",
            "phase": "connecting",
            "label": "Starting agent",
            "since": NOW - 120,
        },
    ),
    (
        "sandbox up, runner not attached",
        _row(),
        QUEUED,
        _Pl(),
        {"runner_online": False, "briefing": True},
        {
            "state": "starting",
            "phase": "connect",
            "label": "Waiting for the agent to connect",
            "since": NOW - 120,
        },
    ),
    (
        "runner attached, brief on its way",
        _row(),
        QUEUED,
        _Pl(),
        {"runner_online": True, "briefing": True},
        {"state": "starting", "phase": "briefing", "label": "Briefing the PL", "since": NOW - 120},
    ),
    (
        "the server creates the session",
        _row(),
        QUEUED,
        None,
        {"spawning": True},
        {
            "state": "starting",
            "phase": "creating",
            "label": "Creating the PL session",
            "since": NOW - 300,
        },
    ),
    (
        "Atlas spawns it (charter)",
        _row(spawner="lead"),
        QUEUED,
        None,
        {"pods": (3, 12)},
        {
            "state": "queued",
            "phase": "lead",
            "label": "Waiting for Atlas to start its PL",
            "since": NOW - 300,
        },
    ),
    (
        "Atlas spawns every PL (engine.spawn=lead)",
        _row(),
        QUEUED,
        None,
        {"lead_spawns": True},
        {
            "state": "queued",
            "phase": "lead",
            "label": "Waiting for Atlas to start its PL",
            "since": NOW - 300,
        },
    ),
    (
        "no free pod, two projects ahead",
        _row(),
        QUEUED,
        None,
        {"pods": (12, 12), "ahead": 2},
        {
            "state": "queued",
            "phase": "pod_cap",
            "label": "No free pod: 12 of 12 in use",
            "since": NOW - 300,
            "ahead": 2,
            "pods_in_use": 12,
            "pod_cap": 12,
        },
    ),
    (
        "a free pod: the server's next pass",
        _row(),
        QUEUED,
        None,
        {"pods": (4, 12)},
        {
            "state": "queued",
            "phase": "server",
            "label": "Waiting for the server to start its PL",
            "since": NOW - 300,
        },
    ),
]


@pytest.mark.parametrize(
    ("row", "result", "pl", "kwargs", "expected"),
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_start_view(
    row: FactoryProject,
    result: StageResult,
    pl: Any,
    kwargs: dict[str, Any],
    expected: dict | None,
) -> None:
    assert start_view(row, result, pl, **kwargs) == expected


# ── Through the snapshot ────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    snapshot.reset_cache()
    yield
    snapshot.reset_cache()


@pytest_asyncio.fixture()
async def spawning(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[Cockpit, FakeCreates]]:
    async for value in spawning_cockpit(world, db_uri, monkeypatch):
        yield value


@pytest_asyncio.fixture()
async def lead_spawned(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in header_cockpit(world, db_uri, monkeypatch):
        yield value


async def _projects(cockpit: Cockpit) -> dict[str, dict[str, Any]]:
    snapshot.reset_cache()
    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}", headers={"X-Test-User": "alice"}
    )
    assert resp.status_code == 200, resp.text
    return {p["slug"]: p for p in resp.json()["projects"]}


async def _create(cockpit: Cockpit, *slugs: str) -> None:
    chat = cockpit.store.append_chat(
        cockpit.fid, thread="factory", author_kind="human", author="alice", body="go"
    )
    resp = await propose(
        cockpit,
        *({"op": "create", "project": project(slug)} for slug in slugs),
        reply_to=f"chat_{chat.id}",
    )
    assert resp.status_code == 200 and resp.json()["status"] == "applied", resp.text
    await settle()


async def test_projects_waiting_for_a_pod_say_so_and_how_many_are_ahead(
    spawning: tuple[Cockpit, FakeCreates], monkeypatch: pytest.MonkeyPatch
) -> None:
    cockpit, creates = spawning

    async def later(*_args: Any, **_kw: Any) -> spawn.SpawnOutcome:
        return spawn.SpawnOutcome("busy")

    # Created while pods were free; before the spawner's pass, the factory shrinks to
    # three pods that Atlas, Sentinel and api-version's PL fill.
    monkeypatch.setattr(spawn, "launch", later)
    await _create(cockpit, "ux-status-page", "ux-env-badge-copy")
    monkeypatch.setattr(spawn, "pod_cap", lambda _deps, _lead: 3)

    assert creates.calls == []
    views = await _projects(cockpit)
    # The spawner takes them oldest first, as the store lists them.
    order = [p.slug for p in cockpit.store.list_projects(cockpit.fid) if p.slug.startswith("ux-")]
    first, second = (views[slug] for slug in order)
    assert first["stage"] == second["stage"] == "queued"
    assert first["start"]["phase"] == second["start"]["phase"] == "pod_cap"
    assert first["start"]["label"] == "No free pod: 3 of 3 in use"
    assert (first["start"]["ahead"], second["start"]["ahead"]) == (0, 1)
    assert views["api-version"]["start"] is None


async def test_a_starting_pl_shows_its_sandbox_then_its_runner(
    spawning: tuple[Cockpit, FakeCreates],
) -> None:
    cockpit, creates = spawning
    await _create(cockpit, "ux-slash-focus")
    [call] = creates.calls
    pl = next(
        m
        for m in cockpit.world.store.list_conversations(limit=50).data
        if m.title == call["body"]["title"]
    )
    try:
        _session_sandbox_status_cache[pl.id] = SandboxStatus(stage="provisioning")
        start = (await _projects(cockpit))["ux-slash-focus"]["start"]
        assert start == {
            "state": "starting",
            "phase": "provisioning",
            "label": "Provisioning sandbox",
            "since": pl.created_at,
            "ahead": None,
            "pods_in_use": None,
            "pod_cap": None,
        }

        # The pod is up; the brief waits for a runner that has not attached yet.
        _session_sandbox_status_cache.pop(pl.id)
        cockpit.store.append_event(
            EventDraft(
                factory_id=cockpit.fid,
                kind="message.queued",
                summary="brief",
                to_id=pl.id,
                subject="mail:mail_brief",
                data={"kind": "brief", "mail_id": "mail_brief"},
            )
        )
        view = (await _projects(cockpit))["ux-slash-focus"]
        assert view["stage"] == "queued"
        assert view["start"]["phase"] == "connect"
        assert view["start"]["label"] == "Waiting for the agent to connect"
    finally:
        _session_sandbox_status_cache.pop(pl.id, None)


async def test_a_lead_spawned_project_waits_for_atlas(lead_spawned: Cockpit) -> None:
    await _create(lead_spawned, "api-request-id")

    start = (await _projects(lead_spawned))["api-request-id"]["start"]

    assert (start["state"], start["phase"]) == ("queued", "lead")
    assert start["label"] == "Waiting for Atlas to start its PL"


async def test_a_project_made_to_validate_a_merged_pr_is_not_merged(
    spawning: tuple[Cockpit, FakeCreates],
) -> None:
    cockpit, _creates = spawning
    row = cockpit.store.get_project(cockpit.fid, "api-version")
    assert row is not None and row.pl_session_id is not None
    merged_at = row.created_at - 3600
    cockpit.store.upsert_pr(
        cockpit.fid,
        REPO,
        9774,
        url=f"https://github.com/{REPO}/pull/9774",
        project_slug="api-version",
        state="merged",
        merged_at=merged_at,
        closed_at=merged_at,
    )

    view = (await _projects(cockpit))["api-version"]
    assert (view["stage"], view["stage_note"]) == (
        "planning",
        "!9774 was merged before this project started",
    )

    # The PL deploys the preview to validate it.
    cockpit.store.upsert_project(
        cockpit.fid, "api-version", agent_stage="verify", agent_stage_at=int(time.time())
    )
    view = (await _projects(cockpit))["api-version"]
    assert (view["stage"], view["stage_note"]) == ("verify", "PR merged · PL still at verify")

    # Done validating: only now does it read Merged.
    cockpit.store.upsert_project(
        cockpit.fid, "api-version", agent_stage="merged", agent_stage_at=int(time.time()) + 1
    )
    view = (await _projects(cockpit))["api-version"]
    assert (view["stage"], view["stage_note"]) == ("merged", None)


async def test_open_tasks_keep_a_merged_project_working(
    spawning: tuple[Cockpit, FakeCreates],
) -> None:
    cockpit, _creates = spawning
    cockpit.store.upsert_pr(
        cockpit.fid,
        REPO,
        9630,
        url=f"https://github.com/{REPO}/pull/9630",
        project_slug="api-version",
        state="merged",
        merged_at=int(time.time()),
    )
    assert (await _projects(cockpit))["api-version"]["stage"] == "merged"

    cockpit.store.upsert_task(cockpit.fid, "api-version", "measure", kind="test", state="running")
    view = (await _projects(cockpit))["api-version"]
    assert (view["stage"], view["stage_note"]) == ("verify", "PR merged · 1 task still open")
