"""Stores and services the factory modules share, hung off ``app.state``."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from omnigent.stores.factory_store import FactoryStore, InMemoryFactoryStore

if TYPE_CHECKING:
    from omnigent.runtime.agent_cache import AgentCache
    from omnigent.server.auth import AuthProvider
    from omnigent.stores import AgentStore, ConversationStore
    from omnigent.stores.artifact_store import ArtifactStore
    from omnigent.stores.file_store import FileStore
    from omnigent.stores.permission_store import PermissionStore
    from omnigent.stores.project_store import ProjectStore

FACTORY_STATE_ATTR = "factory"
"""``app.state`` attribute holding the :class:`FactoryDeps`."""


@dataclass
class FactoryDeps:
    """
    The stores the factory routes, delivery and supervisor read.

    ``app.state`` already carries the runner registries, host store,
    managed-launch tracker and GitHub connection store/client
    (``app_state.github_store`` / ``app_state.github_client``); the
    conversation/agent stores are only wired into router closures, so the
    factory keeps its own handle on them here.

    :param conversation_store: Session rows and labels.
    :param agent_store: Agent rows (spec lookup for factory roles).
    :param agent_cache: Parsed agent bundles, or ``None`` when unavailable.
    :param permission_store: Session grants, or ``None`` when auth is off.
    :param auth_provider: Request identity, or ``None`` when auth is off.
    :param file_store: File metadata forwarded with dispatched messages.
    :param artifact_store: Binary store forwarded with dispatched messages.
    :param runner_tunnel_tokens: The server's runner tunnel-token allow-list,
        or ``None`` when runners prove themselves by token-bound ids only.
    :param factory_store: The cockpit read model; an in-memory store when not given.
    :param project_store: Sidebar projects (factory folders), or ``None``.
    """

    conversation_store: ConversationStore
    agent_store: AgentStore
    agent_cache: AgentCache | None = None
    permission_store: PermissionStore | None = None
    auth_provider: AuthProvider | None = None
    file_store: FileStore | None = None
    artifact_store: ArtifactStore | None = None
    runner_tunnel_tokens: frozenset[str] | None = None
    factory_store: FactoryStore = field(default_factory=InMemoryFactoryStore)
    project_store: ProjectStore | None = None


def factory_deps(app_state: Any) -> FactoryDeps:
    """
    Return the :class:`FactoryDeps` registered on *app_state*.

    :param app_state: ``app.state`` (or a test stand-in).
    :returns: The registered deps.
    :raises RuntimeError: When the factory routes were never registered.
    """
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps):
        raise RuntimeError("factory routes are not registered on this app")
    return deps
