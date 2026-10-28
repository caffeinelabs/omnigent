"""Server side of the software factory.

A factory is a labelled tree of ordinary sessions (see :mod:`omnigent.factory`).
This package adds the ``/v1/factory`` API the runner tools call, the mailer
that delivers ``sys_session_post`` messages across pods, the delivery ladder
that wakes or relaunches a target session, and the supervisor that keeps
members alive and escalates trouble. Submodules import the
session-route helpers, so this module imports them lazily to stay cheap and
cycle-free for importers that only need one piece.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

    from omnigent.runtime.agent_cache import AgentCache
    from omnigent.server.auth import AuthProvider
    from omnigent.stores import AgentStore, ConversationStore
    from omnigent.stores.artifact_store import ArtifactStore
    from omnigent.stores.factory_store import FactoryStore
    from omnigent.stores.file_store import FileStore
    from omnigent.stores.permission_store import PermissionStore
    from omnigent.stores.project_store import ProjectStore

_logger = logging.getLogger(__name__)


def register_factory_routes(
    app: FastAPI,
    *,
    conversation_store: ConversationStore,
    agent_store: AgentStore,
    agent_cache: AgentCache | None = None,
    permission_store: PermissionStore | None = None,
    auth_provider: AuthProvider | None = None,
    file_store: FileStore | None = None,
    artifact_store: ArtifactStore | None = None,
    runner_tunnel_tokens: frozenset[str] | None = None,
    factory_store: FactoryStore | None = None,
    project_store: ProjectStore | None = None,
    factory_artifact_store: ArtifactStore | None = None,
) -> None:
    """
    Mount ``/v1/factory`` on *app* and record the stores it needs.

    :param app: The FastAPI app.
    :param conversation_store: Session rows and labels.
    :param agent_store: Agent rows (factory role lookup).
    :param agent_cache: Parsed agent bundles.
    :param permission_store: Session grants, or ``None`` when auth is off.
    :param auth_provider: Request identity, or ``None`` when auth is off.
    :param file_store: Forwarded with dispatched messages.
    :param artifact_store: Forwarded with dispatched messages.
    :param runner_tunnel_tokens: The server's runner tunnel-token allow-list;
        a listed token proves a runner the way the tunnel route accepts it.
    :param factory_store: The cockpit read model; in-memory when ``None``.
    :param project_store: Sidebar projects, for filing factory sessions.
    :param factory_artifact_store: Where demo files go; when ``None``, the
        store ``OMNIGENT_FACTORY_ARTIFACT_LOCATION`` names, if any.
    :raises ImportError: When that location's backend package is missing.
    :raises ValueError: When that location is malformed.
    """
    from omnigent.server.factory.api import include_routers
    from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
    from omnigent.server.factory.demo_storage import factory_artifact_store_from_env
    from omnigent.server.factory.routes import (
        FactoryRouteError,
        create_factory_router,
        factory_error_handler,
    )
    from omnigent.server.factory.spawn import ASGI_APP_ATTR
    from omnigent.stores.factory_store import InMemoryFactoryStore

    if factory_artifact_store is None:
        factory_artifact_store = factory_artifact_store_from_env()
    if factory_artifact_store is not None:
        _logger.info("factory demo files go to %s", factory_artifact_store.storage_location)
    setattr(
        app.state,
        FACTORY_STATE_ATTR,
        FactoryDeps(
            conversation_store=conversation_store,
            agent_store=agent_store,
            agent_cache=agent_cache,
            permission_store=permission_store,
            auth_provider=auth_provider,
            file_store=file_store,
            artifact_store=artifact_store,
            runner_tunnel_tokens=runner_tunnel_tokens,
            factory_store=factory_store if factory_store is not None else InMemoryFactoryStore(),
            project_store=project_store,
            factory_artifact_store=factory_artifact_store,
        ),
    )
    # The server spawns PLs through its own app, in process (see factory.spawn).
    setattr(app.state, ASGI_APP_ATTR, app)
    app.add_exception_handler(FactoryRouteError, factory_error_handler)
    app.include_router(create_factory_router())
    include_routers(app)


async def start_factory_services(app_state: Any) -> Callable[[], Awaitable[None]]:
    """
    Start the factory's background services for one server lifespan.

    The mailer always (replaying mail a previous process left undelivered);
    the telemetry recorders (spans and the cost ledger), the work-graph
    binder, the supervisor (and the status-edge notifier it reads), the "now"
    poller, the incident detectors and the auto-discovered ``background``
    services only when ``OMNIGENT_FACTORY_ENABLED=1``.

    :param app_state: ``app.state`` of an app passed to
        :func:`register_factory_routes`.
    :returns: An async callable that stops what was started.
    """
    from omnigent.factory import factory_enabled
    from omnigent.server.factory.context import FACTORY_STATE_ATTR
    from omnigent.server.factory.mail_events import replay_enabled
    from omnigent.server.factory.mailer import MAILER_STATE_ATTR, get_mailer

    if getattr(app_state, FACTORY_STATE_ATTR, None) is None:

        async def _noop() -> None:
            return None

        return _noop

    mailer = get_mailer(app_state)
    if replay_enabled():
        await mailer.replay_undelivered()
    stop_supervision: Callable[[], Awaitable[None]] | None = None
    stop_background: Callable[[], Awaitable[None]] | None = None
    stop_telemetry: Callable[[], None] | None = None
    stop_ops: Callable[[], Awaitable[None]] | None = None
    stop_binder: Callable[[], None] | None = None
    unseed_invariants = _seed_invariants()
    if factory_enabled():
        from omnigent.server.factory import activity_now, binder, detectors, telemetry
        from omnigent.server.factory.background import start_all
        from omnigent.server.factory.supervisor import start_supervision

        stop_telemetry = telemetry.start(app_state)
        stop_binder = binder.install()
        stop_supervision = start_supervision(app_state)
        # Live "now" and the incident detectors (event rules; the tick rules ride the supervisor).
        remove_detectors = detectors.install()
        stop_poller = activity_now.start(app_state)

        async def _stop_ops() -> None:
            await stop_poller()
            remove_detectors()

        stop_ops = _stop_ops
        started = ["supervisor"]
        stop_background = await start_all(app_state, started=started)
        # One line the rollout checks for: which services this process runs.
        _logger.info("factory services started: %s", ", ".join(started))

    async def _stop() -> None:
        unseed_invariants()
        if stop_ops is not None:
            await stop_ops()
        if stop_background is not None:
            await stop_background()
        if stop_supervision is not None:
            await stop_supervision()
        if stop_telemetry is not None:
            stop_telemetry()
        if stop_binder is not None:
            stop_binder()
        await mailer.close()
        if getattr(app_state, MAILER_STATE_ATTR, None) is mailer:
            setattr(app_state, MAILER_STATE_ATTR, None)

    return _stop


def _seed_invariants() -> Callable[[], None]:
    """Add the Tier-0 invariant policy to the server-wide defaults when the factory is on."""
    from omnigent.policies.builtins.factory_invariants import (
        invariants_enabled,
        seed_invariant_policy,
    )
    from omnigent.runtime import get_caps

    if not invariants_enabled():
        return lambda: None
    unseed = seed_invariant_policy(get_caps().default_policies)
    _logger.info("factory invariant policy seeded as a server-wide default policy")
    return unseed


__all__ = ["register_factory_routes", "start_factory_services"]
