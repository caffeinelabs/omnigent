"""Fixtures for the software-factory server tests.

Builds a real SQLite-backed factory: Lead A (the root), Lead B, one project
lead with co-located sub-agents, plus an unrelated second factory and a plain
non-factory session.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.db.utils import builtin_agent_id
from omnigent.entities import Conversation
from omnigent.runtime.agent_cache import AgentCache
from omnigent.server.factory.context import FactoryDeps
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from tests.server.helpers import build_agent_bundle


def _native(role: str | None, harness: str = "claude-native") -> dict[str, Any]:
    config: dict[str, Any] = {"harness": harness}
    if role is not None:
        config["factory_role"] = role
    return {"type": "omnigent", "config": config}


@dataclass
class FactoryWorld:
    """A seeded factory and the stores behind it."""

    store: SqlAlchemyConversationStore
    agent_store: SqlAlchemyAgentStore
    deps: FactoryDeps
    app_state: SimpleNamespace
    agents: dict[str, str]
    sessions: dict[str, Conversation] = field(default_factory=dict)

    def make_session(
        self,
        key: str,
        *,
        agent: str,
        title: str,
        labels: dict[str, str] | None = None,
        parent: str | None = None,
        sub_agent_name: str | None = None,
        runner_id: str | None = None,
        host_id: str | None = None,
    ) -> Conversation:
        """Create a session row (plus labels) and remember it under *key*."""
        conv = self.store.create_conversation(
            kind="sub_agent" if parent else "default",
            title=title,
            parent_conversation_id=self.sessions[parent].id if parent else None,
            agent_id=self.agents[agent],
            sub_agent_name=sub_agent_name,
            runner_id=runner_id,
            host_id=host_id,
            workspace="/workspace" if host_id else None,
        )
        if labels:
            self.store.set_labels(conv.id, labels)
        refreshed = self.store.get_conversation(conv.id)
        assert refreshed is not None
        self.sessions[key] = refreshed
        return refreshed

    def get(self, key: str) -> Conversation:
        """Re-read a remembered session."""
        conv = self.store.get_conversation(self.sessions[key].id)
        assert conv is not None
        return conv

    def id(self, key: str) -> str:
        """:returns: The id of a remembered session."""
        return self.sessions[key].id


def _register_agent(
    agent_store: SqlAlchemyAgentStore,
    artifact_store: LocalArtifactStore,
    name: str,
    bundle: bytes,
) -> str:
    agent_id = builtin_agent_id(name)
    location = f"{agent_id}/v1"
    artifact_store.put(location, bundle)
    agent_store.create(agent_id, name, location)
    return agent_id


@pytest.fixture()
def world(db_uri: str, tmp_path: Path) -> FactoryWorld:
    """A factory rooted at Lead A with Lead B, one PL and its sub-agents."""
    store = SqlAlchemyConversationStore(db_uri)
    agent_store = SqlAlchemyAgentStore(db_uri)
    artifacts = LocalArtifactStore(str(tmp_path / "artifacts"))
    cache = AgentCache(artifacts, tmp_path / "agent-cache")
    agents = {
        "lead": _register_agent(
            agent_store,
            artifacts,
            "factory-lead",
            build_agent_bundle("factory-lead", executor=_native("lead"), include_llm=False),
        ),
        "pl": _register_agent(
            agent_store,
            artifacts,
            "factory-pl",
            build_agent_bundle(
                "factory-pl",
                executor=_native("project_lead"),
                include_llm=False,
                sub_agents=[
                    {"name": "ic", "description": "implementer", "executor": _native("ic")},
                    {
                        "name": "reviewer",
                        "description": "reviewer",
                        "executor": _native("reviewer", "codex-native"),
                    },
                    {"name": "helper", "description": "no factory role"},
                ],
            ),
        ),
        "plain": _register_agent(
            agent_store,
            artifacts,
            "plain",
            build_agent_bundle("plain", executor=_native(None), include_llm=False),
        ),
    }
    deps = FactoryDeps(conversation_store=store, agent_store=agent_store, agent_cache=cache)
    app_state = SimpleNamespace(
        factory=deps,
        runner_router=None,
        tunnel_registry=None,
        host_registry=None,
        host_store=None,
        managed_launches=None,
        runner_session_initializer=object(),
    )
    w = FactoryWorld(
        store=store,
        agent_store=agent_store,
        deps=deps,
        app_state=app_state,
        agents=agents,
    )
    lead_a = w.make_session("lead_a", agent="lead", title="Atlas")
    a = lead_a.id
    w.store.set_labels(
        a,
        {
            F.LABEL_ID: a,
            F.LABEL_ROLE: "lead",
            F.LABEL_STATE: "active",
            F.LABEL_HEARTBEAT_S: "3600",
        },
    )
    w.sessions["lead_a"] = w.get("lead_a")
    lead_b = w.make_session(
        "lead_b",
        agent="lead",
        title="Lead B · Sentinel",
        labels={
            F.LABEL_ID: a,
            F.LABEL_ROLE: "lead",
            F.LABEL_PARENT: a,
            F.LABEL_SUPERVISOR: a,
            F.LABEL_STATE: "active",
        },
    )
    w.store.set_labels(a, {F.LABEL_SUPERVISOR: lead_b.id})
    w.sessions["lead_a"] = w.get("lead_a")
    w.make_session(
        "pl",
        agent="pl",
        title="PL · api-version",
        labels={
            F.LABEL_ID: a,
            F.LABEL_ROLE: "project_lead",
            F.LABEL_PROJECT: "api-version",
            F.LABEL_PARENT: a,
            F.LABEL_SUPERVISOR: lead_b.id,
            F.LABEL_STATE: "active",
            F.LABEL_HEARTBEAT_S: "600",
        },
        runner_id="runner_pl",
    )
    w.make_session("ic", agent="pl", title="ic:task-1", parent="pl", sub_agent_name="ic")
    w.make_session(
        "reviewer", agent="pl", title="reviewer:pr-1", parent="pl", sub_agent_name="reviewer"
    )
    w.make_session("helper", agent="pl", title="helper:x", parent="pl", sub_agent_name="helper")
    other = w.make_session("other_lead", agent="lead", title="Other factory lead")
    w.store.set_labels(other.id, {F.LABEL_ID: other.id, F.LABEL_ROLE: "lead"})
    w.sessions["other_lead"] = w.get("other_lead")
    w.make_session("plain", agent="plain", title="Unrelated chat")
    return w


@dataclass
class FakeDeliver:
    """Records deliveries; returns scripted outcomes per target."""

    outcomes: dict[str, list[Any]] = field(default_factory=dict)
    calls: list[tuple[str, str, bool]] = field(default_factory=list)
    gate: Any = None

    async def __call__(
        self, app_state: Any, session_id: str, text: str, *, restart: bool = False
    ) -> Any:
        from omnigent.server.factory.delivery import DeliveryOutcome

        if self.gate is not None:
            await self.gate.wait()
        self.calls.append((session_id, text, restart))
        scripted = self.outcomes.get(session_id)
        if scripted:
            return scripted.pop(0)
        return DeliveryOutcome(True)

    def texts_to(self, session_id: str) -> list[str]:
        """:returns: Texts delivered (or attempted) to *session_id*."""
        return [text for sid, text, _ in self.calls if sid == session_id]


@pytest.fixture()
def fake_deliver() -> FakeDeliver:
    """A recording stand-in for ``deliver_message``."""
    return FakeDeliver()


@pytest.fixture()
def no_sleep() -> Callable[[float], Any]:
    """An instant ``asyncio.sleep`` that records requested delays."""
    delays: list[float] = []

    async def _sleep(seconds: float) -> None:
        delays.append(seconds)

    _sleep.delays = delays  # type: ignore[attr-defined]
    return _sleep


class HeaderAuth:
    """Auth provider stand-in: the caller is whoever ``X-Test-User`` names."""

    def get_user_id(self, request: Any) -> str | None:
        return request.headers.get("X-Test-User")


@dataclass
class FactoryApi:
    """A FastAPI app with only the factory routes, plus a client and mailer."""

    app: Any
    client: Any
    mailer: Any
    deliver: FakeDeliver


@pytest_asyncio.fixture()
async def api(
    world: FactoryWorld,
    fake_deliver: FakeDeliver,
    no_sleep: Callable[[float], Any],
) -> AsyncIterator[FactoryApi]:
    """The factory routes over the seeded world, delivering into a recorder."""
    async for value in _make_api(world, fake_deliver, no_sleep):
        yield value


async def _make_api(
    world: FactoryWorld,
    fake_deliver: FakeDeliver,
    no_sleep: Callable[[float], Any],
    *,
    permission_store: Any = None,
    auth_provider: Any = None,
) -> AsyncIterator[FactoryApi]:
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    from omnigent.errors import OmnigentError
    from omnigent.server.factory import register_factory_routes
    from omnigent.server.factory.mailer import MAILER_STATE_ATTR, Mailer

    app = FastAPI()
    register_factory_routes(
        app,
        conversation_store=world.store,
        agent_store=world.agent_store,
        agent_cache=world.deps.agent_cache,
        permission_store=permission_store,
        auth_provider=auth_provider,
    )

    async def _omnigent_error(_request: Any, exc: Exception) -> JSONResponse:
        assert isinstance(exc, OmnigentError)
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    app.add_exception_handler(OmnigentError, _omnigent_error)
    world.app_state = app.state  # type: ignore[assignment]
    mailer = Mailer(app.state, deliver=fake_deliver, sleep=no_sleep)
    setattr(app.state, MAILER_STATE_ATTR, mailer)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield FactoryApi(app=app, client=client, mailer=mailer, deliver=fake_deliver)
    await mailer.close()


@pytest_asyncio.fixture()
async def authed_api(
    world: FactoryWorld,
    fake_deliver: FakeDeliver,
    no_sleep: Callable[[float], Any],
    db_uri: str,
) -> AsyncIterator[FactoryApi]:
    """Factory routes with real grants: alice owns the seeded world, bob owns nothing."""
    from omnigent.server.auth import LEVEL_OWNER
    from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore

    permissions = SqlAlchemyPermissionStore(db_uri)
    for user in ("alice", "bob"):
        permissions.ensure_user(user)
    for conv in world.sessions.values():
        if conv.parent_conversation_id is None:
            permissions.grant("alice", conv.id, LEVEL_OWNER)
    async for value in _make_api(
        world,
        fake_deliver,
        no_sleep,
        permission_store=permissions,
        auth_provider=HeaderAuth(),
    ):
        yield value


class FakeClock:
    """A wall clock (epoch seconds) that only moves when told to."""

    def __init__(self, now: float | None = None) -> None:
        self.now = time.time() if now is None else now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        """Move the clock forward and return the new time."""
        self.now += seconds
        return self.now


@dataclass
class FakeRunnerClient:
    """Records ``GET`` calls the supervisor makes through a runner tunnel."""

    calls: list[tuple[str, Any]] = field(default_factory=list)
    error: BaseException | None = None

    async def get(self, path: str, **kwargs: Any) -> Any:
        self.calls.append((path, kwargs.get("timeout")))
        if self.error is not None:
            raise self.error
        return httpx.Response(200, json={"status": "ok"})


@dataclass
class FakeRunners:
    """Stand-in for ``_get_runner_client``: a live client only for listed sessions."""

    clients: dict[str, FakeRunnerClient] = field(default_factory=dict)

    def connect(self, session_id: str) -> FakeRunnerClient:
        """Give *session_id* a live runner and return its client."""
        return self.clients.setdefault(session_id, FakeRunnerClient())

    async def __call__(self, session_id: str, runner_router: Any, **_: Any) -> Any:
        return self.clients.get(session_id)


@dataclass
class Supervision:
    """A supervisor, notifier and mailer over the seeded world, on one fake clock."""

    world: FactoryWorld
    clock: FakeClock
    deliver: FakeDeliver
    runners: FakeRunners
    mailer: Any
    notifier: Any
    supervisor: Any

    async def settle(self) -> None:
        """Finish in-flight beats, status-edge tasks and queued mail."""
        await self.supervisor.drain()
        await self.notifier.drain()
        await self.mailer.drain()

    async def tick(self) -> None:
        """Run one supervisor pass and let everything it queued land."""
        await self.supervisor.tick()
        await self.settle()


@pytest_asyncio.fixture()
async def supervision(
    world: FactoryWorld,
    fake_deliver: FakeDeliver,
    no_sleep: Callable[[float], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Supervision]:
    """The factory switched on, with recorded delivery and runner tunnels."""
    from omnigent.server.factory import supervisor as supervisor_module
    from omnigent.server.factory.mailer import MAILER_STATE_ATTR, Mailer
    from omnigent.server.factory.notify import (
        NOTIFIER_STATE_ATTR,
        FactoryNotifier,
        register_relay_notifier,
        unregister_relay_notifier,
    )
    from omnigent.server.factory.supervisor import FactorySupervisor, SupervisorConfig

    monkeypatch.setenv(F.FACTORY_ENABLED_ENV, "1")
    runners = FakeRunners()
    monkeypatch.setattr(supervisor_module, "_get_runner_client", runners)
    clock = FakeClock()
    state = world.app_state
    mailer = Mailer(state, deliver=fake_deliver, sleep=no_sleep, clock=clock)
    notifier = FactoryNotifier(state, clock=clock)
    setattr(state, MAILER_STATE_ATTR, mailer)
    setattr(state, NOTIFIER_STATE_ATTR, notifier)
    register_relay_notifier(notifier)
    supervisor = FactorySupervisor(
        state, config=SupervisorConfig(), deliver=fake_deliver, clock=clock, sleep=no_sleep
    )
    yield Supervision(
        world=world,
        clock=clock,
        deliver=fake_deliver,
        runners=runners,
        mailer=mailer,
        notifier=notifier,
        supervisor=supervisor,
    )
    unregister_relay_notifier(notifier)
    await supervisor.stop()
    await notifier.close()
    await mailer.close()
