"""Cockpit foundation helpers: launch drift, charter renderers, auto-discovery,
nudges, the status registry and the store wiring."""

from __future__ import annotations

import asyncio
import importlib
import pkgutil
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent.entities.factory import FactoryProject
from omnigent.server.factory import api as api_pkg
from omnigent.server.factory import background as background_pkg
from omnigent.server.factory import nudge, status
from omnigent.server.factory.api import discover_routers, include_routers
from omnigent.server.factory.background import start_all
from omnigent.server.factory.charter import (
    normalize_charter,
    render_kickoff_brief,
    render_pl_charter,
)
from omnigent.server.factory.context import FactoryDeps
from omnigent.server.factory.launch import launch_drift, spec_launch_args
from omnigent.stores.factory_store import InMemoryFactoryStore
from tests.server.factory.conftest import FactoryWorld, _register_agent
from tests.server.helpers import build_agent_bundle

# ── Launch drift ───────────────────────────────────────────────


def _bypass_lead(world: FactoryWorld, tmp_path: Path) -> str:
    from omnigent.stores.artifact_store.local import LocalArtifactStore

    artifacts = LocalArtifactStore(str(tmp_path / "artifacts"))
    executor = {
        "type": "omnigent",
        "config": {
            "harness": "claude-native",
            "permission_mode": "bypassPermissions",
            "factory_role": "lead",
        },
    }
    bundle = build_agent_bundle("factory-lead-bypass", executor=executor, include_llm=False)
    return _register_agent(world.agent_store, artifacts, "factory-lead-bypass", bundle)


def test_launch_drift_auto_vs_bypass_spec(world: FactoryWorld, tmp_path: Path) -> None:
    world.agents["bypass"] = _bypass_lead(world, tmp_path)
    atlas = world.make_session("atlas2", agent="bypass", title="Atlas")
    world.store.update_conversation(atlas.id, terminal_launch_args=["--permission-mode", "auto"])
    stale = world.get("atlas2")

    assert spec_launch_args(stale, world.deps) == ["--permission-mode", "bypassPermissions"]
    assert launch_drift(stale, world.deps) == {
        "fields": ["permission_mode"],
        "stored": {"permission_mode": "auto"},
        "spec": {"permission_mode": "bypassPermissions"},
    }

    world.store.update_conversation(
        atlas.id, terminal_launch_args=["--permission-mode", "bypassPermissions"]
    )
    assert launch_drift(world.get("atlas2"), world.deps) is None


def test_launch_drift_ignores_sub_agents_and_undeclared_specs(
    world: FactoryWorld, tmp_path: Path
) -> None:
    # The seeded lead's spec declares no permission mode: nothing to drift from.
    world.store.update_conversation(world.id("lead_a"), terminal_launch_args=["--yolo"])
    assert spec_launch_args(world.get("lead_a"), world.deps) is None
    assert launch_drift(world.get("lead_a"), world.deps) is None
    # Sub-agents are relaunched by their PL, never from the cockpit.
    assert launch_drift(world.get("ic"), world.deps) is None


# ── Charter renderers (golden) ─────────────────────────────────

_API_PROJECT = FactoryProject(
    id="c" * 32,
    factory_id="f" * 32,
    slug="api-version",
    title="API version endpoint",
    repo="caffeinelabs/app",
    base_branch="main",
    priority=1,
    charter={
        "goal": "GET /version on caffeine-api returns the deployed commit.",
        "dod": [
            "PR ready for human merge",
            "curl -fsS {api}/version → 200",
            "pnpm -F caffeine-api test",
        ],
        "scope_paths": ["services/caffeine-api/**"],
        "env_needed": True,
        "preview_slots": 3,
        "demo": "Call GET /version and show gitSha.",
        "version_path": "/version",
        "budget_ics": 2,
    },
)
_DOCS_PROJECT = FactoryProject(
    id="d" * 32,
    factory_id="f" * 32,
    slug="docs-factory-envs",
    title="Factory envs docs",
    repo="caffeinelabs/app",
    priority=2,
    charter={
        "goal": "Add docs/factory-envs.md.",
        "env_needed": False,
        "constraints": "Docs only.",
    },
)


GOLDEN = Path(__file__).parent / "golden"


def _golden(name: str) -> str:
    return (GOLDEN / name).read_text(encoding="utf-8").removesuffix("\n")


def _with_ref(project: FactoryProject) -> FactoryProject:
    from dataclasses import replace

    charter = normalize_charter(project.charter, tooling_ref="main")
    return replace(project, charter=charter)


def test_render_pl_charter_golden() -> None:
    assert render_pl_charter(_with_ref(_API_PROJECT)) == _golden("pl_charter_api.txt")
    assert render_pl_charter(_with_ref(_DOCS_PROJECT)) == _golden("pl_charter_docs.txt")


def test_render_pl_charter_names_the_projects_base_branch() -> None:
    """The base branch is an explicit charter line; a missing one renders as main."""
    from dataclasses import replace

    temp = render_pl_charter(replace(_API_PROJECT, base_branch="temp-main")).splitlines()
    assert "Repo#branch: caffeinelabs/app#temp-main · branches factory/api-version/<task>" in temp
    assert (
        "Base branch: temp-main (open every PR against it; the dev env tracks origin/temp-main)"
        in temp
    )
    assert not any("origin/main" in line for line in temp)
    for unset in (None, "", "  "):
        lines = render_pl_charter(replace(_API_PROJECT, base_branch=unset)).splitlines()
        assert "Base branch: main (open every PR against it; the dev env tracks origin/main)" in (
            lines
        )


def test_render_kickoff_brief_golden() -> None:
    brief = render_kickoff_brief(
        "Atlas factory", "demo", "Luka", [_API_PROJECT, _DOCS_PROJECT], factory_id="f" * 32
    )
    assert brief == _golden("kickoff_brief.txt")
    normal = render_kickoff_brief("Atlas factory", "normal", "Luka", [])
    assert "heartbeats: leads 3600 s, PLs 7200 s" in normal and "Cockpit:" not in normal


def test_normalize_charter_shapes() -> None:
    charter = normalize_charter(
        {"goal": " g ", "preview": {"enabled": True, "slots": 9}, "demo_focus": "show it"},
        tooling_ref="ref",
    )
    assert charter["preview"] == {"enabled": True, "slots": 3, "tooling_ref": "ref"}
    assert charter["preview_slots"] == 3
    assert charter["demo"] == charter["demo_focus"] == "show it"
    assert charter["goal"] == "g" and charter["budget_ics"] == 3
    off = normalize_charter({"env_needed": False, "preview": {"enabled": True}}, tooling_ref="ref")
    assert off["preview"] == {"enabled": False, "slots": 2}
    assert normalize_charter(None)["env_needed"] is True


@pytest.mark.parametrize("retired", ["factory/preview-tooling", "origin/factory/preview-tooling"])
def test_normalize_charter_drops_the_retired_tooling_ref(retired: str) -> None:
    """A charter stored while the tooling lived on its own branch falls back to main."""
    from dataclasses import replace

    stored = {"preview": {"enabled": True, "tooling_ref": retired}}
    assert "tooling_ref" not in normalize_charter(stored)["preview"]
    assert normalize_charter(stored, tooling_ref="main")["preview"]["tooling_ref"] == "main"
    assert "tooling_ref" not in normalize_charter({}, tooling_ref=retired)["preview"]
    project = replace(_API_PROJECT, charter=stored)
    assert "preview tooling ref" not in render_pl_charter(project)


# ── Auto-discovery ─────────────────────────────────────────────


@pytest.fixture
def dummy_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A throwaway package with a router module, a service module and a broken module."""
    pkg = tmp_path / "cockpit_dummy_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "hello.py").write_text(
        textwrap.dedent(
            """
            from fastapi import APIRouter

            router = APIRouter()
            STARTED = []

            @router.get("/v1/factory/dummy")
            def dummy() -> dict:
                return {"ok": True}

            async def start(app_state):
                STARTED.append(app_state)

                async def stop():
                    STARTED.remove(app_state)

                return stop
            """
        )
    )
    (pkg / "broken.py").write_text("raise RuntimeError('import fails')\n")
    (pkg / "failing.py").write_text(
        "async def start(app_state):\n    raise RuntimeError('start fails')\n"
    )
    (pkg / "_private.py").write_text("router = None\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield "cockpit_dummy_pkg"
    for name in [m for m in sys.modules if m.startswith("cockpit_dummy_pkg")]:
        del sys.modules[name]


def test_api_discovery_mounts_a_module_router(dummy_package: str) -> None:
    app = FastAPI()
    assert include_routers(app, dummy_package) == [f"{dummy_package}.hello"]
    assert TestClient(app).get("/v1/factory/dummy").json() == {"ok": True}
    assert "/v1/factory/dummy" not in app.openapi()["paths"]


async def test_background_discovery_starts_and_stops_services(dummy_package: str) -> None:
    state = SimpleNamespace()
    stop = await start_all(state, dummy_package)
    started = sys.modules[f"{dummy_package}.hello"].STARTED
    assert started == [state]
    await stop()
    assert started == []


async def test_background_start_that_hangs_times_out_and_others_run(
    dummy_package: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / dummy_package / "aaa_slow.py").write_text(
        "import asyncio\n\nasync def start(app_state):\n    await asyncio.sleep(3600)\n"
    )
    monkeypatch.setattr("omnigent.server.factory.background.START_TIMEOUT_S", 0.05)
    state = SimpleNamespace()
    stop = await asyncio.wait_for(start_all(state, dummy_package), 5)
    started = sys.modules[f"{dummy_package}.hello"].STARTED
    assert started == [state]
    await stop()


def test_shipped_routers_stay_under_the_factory_prefix() -> None:
    # Add-tolerant: later packages drop modules in here without editing this test.
    discovered = discover_routers()
    for name, router in discovered:
        paths = [getattr(route, "path", "") for route in router.routes]
        assert all(p.startswith("/v1/factory/") for p in paths), (name, paths)
    assert include_routers(FastAPI()) == [name for name, _ in discovered]


@pytest.mark.parametrize("package", [api_pkg, background_pkg])
def test_shipped_discovery_modules_import(package: ModuleType) -> None:
    # Discovery skips a module that fails to import; fail loudly here instead.
    for info in pkgutil.iter_modules(package.__path__):
        importlib.import_module(f"{package.__name__}.{info.name}")


async def test_factory_services_start_background_when_enabled(
    world: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.server import factory as factory_pkg

    calls: list[str] = []

    async def fake_start_all(app_state: object, *, started: list[str] | None = None) -> object:
        calls.append("start")

        async def stop() -> None:
            calls.append("stop")

        return stop

    monkeypatch.setenv("OMNIGENT_FACTORY_ENABLED", "1")
    monkeypatch.setattr("omnigent.server.factory.background.start_all", fake_start_all)
    monkeypatch.setattr(
        "omnigent.server.factory.supervisor.start_supervision",
        lambda app_state: _noop_stop,
    )
    stop = await factory_pkg.start_factory_services(world.app_state)
    await stop()
    assert calls == ["start", "stop"]


async def _noop_stop() -> None:
    return None


# ── Nudge and status ───────────────────────────────────────────


async def test_nudge_wakes_a_waiter_and_keeps_early_nudges() -> None:
    nudge.reset()
    assert await nudge.wait("prs", 0.01) is None

    nudge.nudge("prs", "pr-1")
    nudge.nudge("prs", "pr-2")
    assert await nudge.wait("prs", 1) == {"pr-1", "pr-2"}

    waiter = asyncio.create_task(nudge.wait("prs", 5))
    await asyncio.sleep(0.01)
    await asyncio.to_thread(nudge.nudge, "prs")
    assert await asyncio.wait_for(waiter, 1) == set()
    assert await nudge.wait("envs", 0.01) is None


def test_status_registry_merges_and_copies() -> None:
    status.reset()
    status.set_status("github", state="ok", last_poll_at=10)
    status.set_status("github", error=None, last_poll_at=20)
    status.set_status("supervisor", ok=True)
    everything = status.get_all()
    assert everything["github"]["state"] == "ok" and everything["github"]["last_poll_at"] == 20
    assert "updated_at" in everything["supervisor"]
    everything["github"]["state"] = "mutated"
    assert status.get_status("github")["state"] == "ok"  # type: ignore[index]
    assert status.get_status("probes") is None
    status.reset()
    assert status.get_all() == {}


# ── Wiring ─────────────────────────────────────────────────────


def test_factory_deps_fall_back_to_an_in_memory_store(world: FactoryWorld) -> None:
    from omnigent.server.factory import register_factory_routes
    from omnigent.server.factory.context import factory_deps

    assert isinstance(world.deps.factory_store, InMemoryFactoryStore)
    app = FastAPI()
    register_factory_routes(app, conversation_store=world.store, agent_store=world.agent_store)
    assert isinstance(factory_deps(app.state).factory_store, InMemoryFactoryStore)

    store = InMemoryFactoryStore()
    explicit = FactoryDeps(
        conversation_store=world.store, agent_store=world.agent_store, factory_store=store
    )
    assert explicit.factory_store is store
