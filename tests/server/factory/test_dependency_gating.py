"""Dependency gating: a project waits until what it ``depends_on`` is met.

``depends_mode=merged`` (the default) releases once a dependency's PR merged
into the base; ``ready`` once it is ready for human merge. Release runs on
``pr.merged`` / ``pr.ready`` / ``stage.changed`` events and on the base
watcher's ``base.moved`` hook, and then the server spawns the PL.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio

from omnigent.entities.factory import FactoryPr, FactoryProject
from omnigent.server.factory import base, events, gating, snapshot
from omnigent.server.factory.gating import (
    dependency_met,
    find_cycle,
    globs_overlap,
    resolve_alias,
    scope_overlaps,
    unmet_dependencies,
)
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import (
    REPO,
    FakeCreates,
    project,
    propose,
    settle,
    spawning_cockpit,
)


@pytest_asyncio.fixture()
async def spawning(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[Cockpit, FakeCreates]]:
    async for value in spawning_cockpit(world, db_uri, monkeypatch):
        yield value


FID = "f" * 32


def _row(slug: str, **extra: Any) -> FactoryProject:
    return FactoryProject(
        id=slug.ljust(32, "0")[:32], factory_id=FID, slug=slug, title=slug, **extra
    )


def _pr(slug: str, state: str = "open", *, ready: bool = False, number: int = 1) -> FactoryPr:
    return FactoryPr(
        id=f"{number:032x}",
        factory_id=FID,
        repo=REPO,
        number=number,
        url=f"https://github.com/{REPO}/pull/{number}",
        project_slug=slug,
        state=state,
        ready=ready,
    )


# ── Pure checks ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("prs", "stage", "mode", "met"),
    [
        ([], "planning", "merged", False),
        ([_pr("a", "merged")], "planning", "merged", True),
        ([_pr("a", "merged", number=1), _pr("a", "open", number=2)], "building", "merged", False),
        ([_pr("a", "open", ready=True)], "ready", "merged", False),
        ([_pr("a", "open", ready=True)], "ready", "ready", True),
        ([_pr("a", "open")], "verify", "ready", False),
        ([], "done", "merged", True),
    ],
)
def test_dependency_met(prs: list[FactoryPr], stage: str, mode: str, met: bool) -> None:
    assert dependency_met(_row("a", stage=stage), prs, mode) is met


def test_a_cancelled_dependency_is_never_met_and_a_merged_one_follows_its_survivor() -> None:
    rows = {
        "a": _row("a", status="cancelled"),
        "b": _row("b", status="merged", parent_slug="c"),
        "c": _row("c"),
        "x": _row("x", charter={"depends_on": ["a", "b"]}),
    }
    assert resolve_alias("b", rows) is rows["c"]
    assert unmet_dependencies(rows["x"], rows, [_pr("c", "merged")]) == ["a"]
    assert unmet_dependencies(rows["x"], rows, []) == ["a", "b"]


def test_find_cycle_returns_the_path() -> None:
    assert find_cycle({"a": ["b"], "b": ["c"], "c": []}) is None
    assert find_cycle({"a": ["b"], "b": ["a"]}) == ["a", "b", "a"]
    assert find_cycle({"a": ["a"]}) == ["a", "a"]
    assert find_cycle({"a": ["ghost"]}) is None


@pytest.mark.parametrize(
    ("a", "b", "overlap"),
    [
        ("services/api/**", "services/**", True),
        ("services/api/**", "services/web/**", False),
        ("web/src/*.tsx", "web/src/pages/**", True),
        ("docs/a.md", "docs/b.md", False),
        ("**", "anything/**", True),
    ],
)
def test_glob_prefix_overlap(a: str, b: str, overlap: bool) -> None:
    assert globs_overlap(a, b) is overlap


def test_scope_overlap_names_both_sides() -> None:
    warnings = scope_overlaps(
        "a", ["services/api/**"], [("b", ["services/**"]), ("c", ["web/**"])]
    )
    assert warnings == ["scope_overlap: a services/api/** ~ b services/**"]


def test_release_listens_to_events_and_the_base_watcher() -> None:
    gating.register()
    gating.register()
    assert events._listeners.count(gating._on_event) == 1
    assert base._hooks.count(gating._on_base_moved) == 1


# ── Hold and release ───────────────────────────────────────────


async def _plan(cockpit: Cockpit, mode: str = "merged") -> None:
    chat = cockpit.store.append_chat(
        cockpit.fid, thread="factory", author_kind="human", author="alice", body="go"
    )
    resp = await propose(
        cockpit,
        {"op": "create", "project": project("db-version")},
        {
            "op": "create",
            "project": project("settings", depends_on=["db-version"], depends_mode=mode),
        },
        reply_to=f"chat_{chat.id}",
    )
    assert resp.json()["status"] == "applied", resp.text
    await settle()


def _created(creates: FakeCreates) -> list[str]:
    from omnigent import factory as F

    return [c["body"]["labels"][F.LABEL_PROJECT] for c in creates.calls]


async def _view(cockpit: Cockpit, slug: str) -> dict[str, Any]:
    snapshot.reset_cache()
    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}", headers={"X-Test-User": "alice"}
    )
    return next(p for p in resp.json()["projects"] if p["slug"] == slug)


def _merged_pr(cockpit: Cockpit, *, state: str = "merged", ready: bool = False) -> FactoryPr:
    pr, _ = cockpit.store.upsert_pr(
        cockpit.fid,
        REPO,
        9630,
        url=f"https://github.com/{REPO}/pull/9630",
        project_slug="db-version",
        state=state,
        ready=ready,
    )
    return pr


async def test_a_dependent_waits_then_spawns_once_its_dependency_merged(
    spawning: tuple[Cockpit, FakeCreates],
) -> None:
    cockpit, creates = spawning
    await _plan(cockpit)

    assert _created(creates) == ["db-version"]
    [held] = cockpit.events("project.held")
    assert held.project_slug == "settings" and held.data["waiting_on"] == ["db-version"]
    view = await _view(cockpit, "settings")
    assert (view["overlay"], view["overlay_reason"]) == ("waiting", "waiting on db-version")
    assert view["depends_on"] == ["db-version"] and view["depends_mode"] == "merged"

    # A ready PR does not release a merged-mode dependent.
    _merged_pr(cockpit, state="open", ready=True)
    await events.emit(cockpit.app.state, cockpit.fid, "pr.ready", summary="ready")
    await settle()
    assert _created(creates) == ["db-version"]

    _merged_pr(cockpit)
    await events.emit(cockpit.app.state, cockpit.fid, "pr.merged", summary="merged")
    await settle()

    assert _created(creates) == ["db-version", "settings"]
    [released] = cockpit.events("project.released")
    assert released.project_slug == "settings" and released.data["trigger"] == "pr.merged"
    view = await _view(cockpit, "settings")
    assert view["overlay"] is None


async def test_ready_mode_releases_on_a_ready_pr(spawning: tuple[Cockpit, FakeCreates]) -> None:
    cockpit, creates = spawning
    await _plan(cockpit, mode="ready")

    _merged_pr(cockpit, state="open", ready=True)
    await events.emit(cockpit.app.state, cockpit.fid, "pr.ready", summary="ready")
    await settle()

    assert _created(creates) == ["db-version", "settings"]


async def test_a_base_move_releases_held_projects(spawning: tuple[Cockpit, FakeCreates]) -> None:
    cockpit, creates = spawning
    await _plan(cockpit)
    _merged_pr(cockpit)

    await gating._on_base_moved(cockpit.app.state, SimpleNamespace(factory_id=cockpit.fid))
    await settle()

    assert _created(creates) == ["db-version", "settings"]
    [released] = cockpit.events("project.released")
    assert released.data["trigger"] == "base.moved"


async def test_held_is_logged_once(spawning: tuple[Cockpit, FakeCreates]) -> None:
    cockpit, _creates = spawning
    await _plan(cockpit)

    await gating.release_ready(cockpit.app.state, cockpit.fid, trigger="sweep")
    await gating.release_ready(cockpit.app.state, cockpit.fid, trigger="sweep")

    assert len(cockpit.events("project.held")) == 1
