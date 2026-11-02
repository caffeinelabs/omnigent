"""Why a queued project has no working PL yet (``ProjectView.start``) and the stage note.

New projects sat "Queued" for 2-5 minutes with no reason while their PL pods
started; a project made to validate an already-merged PR read "Merged" while
its PL was still deploying the preview.
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.entities.factory import EventDraft, FactoryEvent, FactoryProject
from omnigent.server.factory import snapshot, spawn
from omnigent.server.factory.snapshot import delivered_brief_targets, pl_ran_before, start_view
from omnigent.server.factory.stage import StageResult
from omnigent.server.routes._sessions.common import (
    _session_launch_progress_cache,
    _session_sandbox_status_cache,
)
from omnigent.server.routes._sessions.helpers import (
    _mark_launch_wake,
    _publish_launch_step,
    _publish_sandbox_status_impl,
)
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


def _row(
    spawner: str | None = "server", status: str = "active", stage_changed_at: int = 0
) -> FactoryProject:
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
        stage_changed_at=stage_changed_at,
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
            "restart": False,
        },
    ),
    (
        "cluster full: waiting for a node",
        _row(),
        QUEUED,
        _Pl(),
        {
            "sandbox": "starting",
            "progress": snapshot.SandboxProgress("starting", "node", NOW - 90),
        },
        {
            "state": "starting",
            "phase": "node",
            "label": "Waiting for a node",
            "since": NOW - 120,
            "phase_since": NOW - 90,
            "restart": False,
        },
    ),
    (
        "a cold node pulls the image, on the retry's new sandbox",
        _row(),
        QUEUED,
        _Pl(),
        {
            "sandbox": "starting",
            "progress": snapshot.SandboxProgress("starting", "image", NOW - 236, tries=2),
        },
        {
            "state": "starting",
            "phase": "image",
            "label": "Pulling the image",
            "since": NOW - 120,
            "phase_since": NOW - 236,
            "attempt": 2,
            "restart": False,
        },
    ),
    (
        "a step of an earlier stage is not shown",
        _row(),
        QUEUED,
        _Pl(),
        {
            "sandbox": "connecting",
            "progress": snapshot.SandboxProgress("starting", "clone", NOW - 60),
        },
        {
            "state": "starting",
            "phase": "connecting",
            "label": "Starting agent",
            "since": NOW - 120,
            "restart": False,
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
            "restart": False,
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
            "restart": False,
        },
    ),
    (
        "runner attached, brief on its way",
        _row(),
        QUEUED,
        _Pl(),
        {"runner_online": True, "briefing": True},
        {
            "state": "starting",
            "phase": "briefing",
            "label": "Briefing the PL",
            "since": NOW - 120,
            "restart": False,
        },
    ),
    # A PL made days ago, woken: timed from when the project went back to queued.
    (
        "a PL created days ago comes back",
        _row(stage_changed_at=NOW - 30),
        QUEUED,
        _Pl(created_at=NOW - 3 * 86400),
        {"sandbox": "provisioning", "ran_before": True},
        {
            "state": "starting",
            "phase": "provisioning",
            "label": "Provisioning sandbox",
            "since": NOW - 30,
            "restart": True,
        },
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
            "label": "All 12 pods in use",
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
            "label": "Starts within a minute",
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


def _brief(factory_id: str, kind: str, to_id: str) -> EventDraft:
    return EventDraft(
        factory_id=factory_id,
        kind=kind,
        summary="brief",
        to_id=to_id,
        subject="mail:mail_brief",
        data={"kind": "brief", "mail_id": "mail_brief"},
    )


def test_a_pl_that_already_worked_is_a_restart() -> None:
    # A fresh PL: its project has been queued since before the PL was made.
    fresh_pl = _Pl(created_at=NOW - 120)
    queued = _row(stage_changed_at=NOW - 300)
    assert pl_ran_before(queued, fresh_pl, set()) is False  # type: ignore[arg-type]
    assert pl_ran_before(queued, fresh_pl, {fresh_pl.id}) is True  # type: ignore[arg-type]

    old_pl = _Pl(created_at=NOW - 3 * 86400)
    # It moved the stage on since it was made (before reconcile stores the wake's queued).
    building = dataclasses.replace(_row(stage_changed_at=NOW - 86400), stage="building")
    assert pl_ran_before(building, old_pl, set()) is True  # type: ignore[arg-type]
    # A queued stage stored since is no proof: a replacement PL's project is queued too.
    assert pl_ran_before(_row(stage_changed_at=NOW - 30), old_pl, set()) is False  # type: ignore[arg-type]
    # It claimed a stage since it was made (every PL does at kickoff).
    claimed = _row(stage_changed_at=NOW - 30)
    assert pl_ran_before(claimed, old_pl, set()) is False  # type: ignore[arg-type]
    claimed = dataclasses.replace(claimed, agent_stage="building", agent_stage_at=NOW - 2 * 86400)
    assert pl_ran_before(claimed, old_pl, set()) is True  # type: ignore[arg-type]


def test_only_a_delivered_brief_counts_as_briefed() -> None:
    def event(kind: str, to_id: str, mail_kind: str = "brief") -> FactoryEvent:
        return FactoryEvent(
            id="e" * 32,
            factory_id=FID,
            ts=NOW * 1000,
            kind=kind,
            summary="brief",
            to_id=to_id,
            data={"kind": mail_kind},
        )

    mail = [
        event("message.queued", "queued-only"),
        event("message.delivered", "briefed"),
        event("message.delivered", "mailed", mail_kind="message"),
    ]
    assert delivered_brief_targets(mail) == {"briefed"}


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
    assert first["start"]["label"] == "All 3 pods in use"
    assert (first["start"]["ahead"], second["start"]["ahead"]) == (0, 1)
    assert views["api-version"]["start"] is None


async def test_an_archived_member_holds_no_pod(
    spawning: tuple[Cockpit, FakeCreates], monkeypatch: pytest.MonkeyPatch
) -> None:
    cockpit, _creates = spawning

    async def later(*_args: Any, **_kw: Any) -> spawn.SpawnOutcome:
        return spawn.SpawnOutcome("busy")

    monkeypatch.setattr(spawn, "launch", later)
    await _create(cockpit, "ux-status-page")
    monkeypatch.setattr(spawn, "pod_cap", lambda _deps, _lead: 3)
    assert (await _projects(cockpit))["ux-status-page"]["start"]["phase"] == "pod_cap"

    # The spawner does not count an archived member; neither does the cockpit.
    cockpit.world.store.update_conversation(cockpit.world.id("lead_b"), archived=True)
    start = (await _projects(cockpit))["ux-status-page"]["start"]
    assert (start["phase"], start["label"]) == ("server", "Starts within a minute")


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
            "phase_since": None,
            "attempt": None,
            "restart": False,
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


async def test_a_starting_pl_names_what_its_sandbox_waits_on(
    spawning: tuple[Cockpit, FakeCreates],
) -> None:
    """The 2026-11-02 launch: a full cluster, then a cold image pull, then a retry."""
    cockpit, _creates = spawning
    await _create(cockpit, "build-tier-drafts")
    pl = next(
        m
        for m in cockpit.world.store.list_conversations(limit=50).data
        if m.title == "PL · build-tier-drafts"
    )
    try:
        pulling_since = int(time.time()) - 200
        _publish_sandbox_status_impl(pl.id, "starting")
        _publish_launch_step(pl.id, "image", pulling_since)
        view = (await _projects(cockpit))["build-tier-drafts"]
        assert view["start"]["phase"] == "image"
        assert view["start"]["label"] == "Pulling the image"
        assert view["start"]["phase_since"] == pulling_since
        assert view["provisioning"]["step"] == "image"
        assert view["provisioning"]["label"] == "Pulling the image"

        # It failed: the server retries it on a new sandbox.
        _publish_sandbox_status_impl(pl.id, "failed", "did not start within 300s")
        view = (await _projects(cockpit))["build-tier-drafts"]
        assert view["overlay"] == "failed"
        assert view["overlay_reason"] == "PL sandbox failed · retrying on a new sandbox"
        assert view["start"] is None

        cockpit.world.store.set_labels(
            pl.id,
            {F.LABEL_LAUNCH_TRIES: "2", F.LABEL_LAUNCH_AUTO: "1", F.LABEL_LAUNCH_NEW: "1"},
        )
        _publish_sandbox_status_impl(pl.id, "provisioning")
        view = (await _projects(cockpit))["build-tier-drafts"]
        assert view["overlay"] is None
        assert (view["start"]["phase"], view["start"]["attempt"]) == ("provisioning", 2)
        assert view["provisioning"]["attempt"] == 2

        # The retry failed too: owners decide.
        _publish_sandbox_status_impl(pl.id, "failed", "did not start within 300s")
        view = (await _projects(cockpit))["build-tier-drafts"]
        assert view["overlay_reason"] == "PL sandbox failed twice"
    finally:
        _session_sandbox_status_cache.pop(pl.id, None)
        _session_launch_progress_cache.pop(pl.id, None)


async def test_a_pl_whose_own_sandbox_did_not_wake_is_told_apart(
    spawning: tuple[Cockpit, FakeCreates],
) -> None:
    """Its sandbox keeps its files: woken again, never "on a new sandbox"."""
    cockpit, _creates = spawning
    await _create(cockpit, "build-tier-drafts")
    pl = next(
        m
        for m in cockpit.world.store.list_conversations(limit=50).data
        if m.title == "PL · build-tier-drafts"
    )

    def wake() -> None:
        _publish_sandbox_status_impl(pl.id, "provisioning")
        _mark_launch_wake(pl.id)

    try:
        wake()
        _publish_sandbox_status_impl(pl.id, "starting")
        _publish_sandbox_status_impl(pl.id, "failed", "did not start within 900s")
        view = (await _projects(cockpit))["build-tier-drafts"]
        assert view["overlay_reason"] == "PL sandbox did not wake · waking it again"

        # The server's one wake again: a retry, not a new sandbox.
        cockpit.world.store.set_labels(
            pl.id, {F.LABEL_LAUNCH_TRIES: "2", F.LABEL_LAUNCH_AUTO: "1"}
        )
        wake()
        _publish_launch_step(pl.id, "node", int(time.time()) - 30)
        view = (await _projects(cockpit))["build-tier-drafts"]
        assert view["start"]["phase"] == "node"
        assert "attempt" not in view["start"] or view["start"]["attempt"] is None
        assert view["provisioning"].get("attempt") is None

        _publish_sandbox_status_impl(pl.id, "failed", "did not start within 900s")
        view = (await _projects(cockpit))["build-tier-drafts"]
        assert view["overlay_reason"] == "PL sandbox did not wake twice"
    finally:
        _session_sandbox_status_cache.pop(pl.id, None)
        _session_launch_progress_cache.pop(pl.id, None)


async def test_a_woken_pl_restarts_timed_from_the_wake(
    spawning: tuple[Cockpit, FakeCreates],
) -> None:
    cockpit, _creates = spawning
    pl_id = cockpit.world.id("pl")
    pl = cockpit.world.store.get_conversation(pl_id)
    assert pl is not None
    row = cockpit.store.get_project(cockpit.fid, "api-version")
    assert row is not None
    # Briefed when it first started; days later it is woken and back to queued.
    cockpit.store.append_event(_brief(cockpit.fid, "message.queued", pl_id))
    cockpit.store.append_event(_brief(cockpit.fid, "message.delivered", pl_id))
    woke = int(pl.created_at) + 3 * 86400
    cockpit.store.update_project(row.id, stage="queued", stage_changed_at=woke)
    try:
        _session_sandbox_status_cache[pl_id] = SandboxStatus(stage="provisioning")
        view = (await _projects(cockpit))["api-version"]
    finally:
        _session_sandbox_status_cache.pop(pl_id, None)

    assert view["stage"] == "queued"
    start = view["start"]
    assert (start["state"], start["phase"], start["restart"]) == ("starting", "provisioning", True)
    assert start["since"] == woke


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
        "verify",
        "!9774 was merged before this project started",
    )

    # The PL deploys the preview to validate it.
    cockpit.store.upsert_project(
        cockpit.fid, "api-version", agent_stage="verify", agent_stage_at=int(time.time())
    )
    view = (await _projects(cockpit))["api-version"]
    assert (view["stage"], view["stage_note"]) == (
        "verify",
        "PR merged · the PL says it is verifying",
    )

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
