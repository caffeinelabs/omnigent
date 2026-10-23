"""Server side of the software factory.

A factory is a labelled tree of ordinary sessions (see :mod:`omnigent.factory`).
This package adds the ``/v1/factory`` API the runner tools call, the mailer
that delivers ``sys_session_post`` messages across pods, and the delivery
ladder that wakes or relaunches a target session. Submodules import the
session-route helpers, so this module imports them lazily to stay cheap and
cycle-free for importers that only need one piece.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

    from omnigent.runtime.agent_cache import AgentCache
    from omnigent.server.auth import AuthProvider
    from omnigent.stores import AgentStore, ConversationStore
    from omnigent.stores.artifact_store import ArtifactStore
    from omnigent.stores.file_store import FileStore
    from omnigent.stores.permission_store import PermissionStore


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
    """
    from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
    from omnigent.server.factory.routes import (
        FactoryRouteError,
        create_factory_router,
        factory_error_handler,
    )

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
        ),
    )
    app.add_exception_handler(FactoryRouteError, factory_error_handler)
    app.include_router(create_factory_router())


async def start_factory_services(app_state: Any) -> Callable[[], Awaitable[None]]:
    """
    Start the factory's background services for one server lifespan.

    Today that is the mailer; the supervisor joins it when
    ``OMNIGENT_FACTORY_ENABLED=1``.

    :param app_state: ``app.state`` of an app passed to
        :func:`register_factory_routes`.
    :returns: An async callable that stops what was started.
    """
    from omnigent.server.factory.context import FACTORY_STATE_ATTR
    from omnigent.server.factory.mailer import MAILER_STATE_ATTR, get_mailer

    if getattr(app_state, FACTORY_STATE_ATTR, None) is None:

        async def _noop() -> None:
            return None

        return _noop

    mailer = get_mailer(app_state)

    async def _stop() -> None:
        await mailer.close()
        if getattr(app_state, MAILER_STATE_ATTR, None) is mailer:
            setattr(app_state, MAILER_STATE_ATTR, None)

    return _stop


__all__ = ["register_factory_routes", "start_factory_services"]
