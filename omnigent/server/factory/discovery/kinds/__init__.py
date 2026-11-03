"""The source kinds the gateway reads without a secret: ``repo``, ``web`` and ``team``.

Every other kind of ``discovery.sources`` (analytics, chat, trackers, MCP …)
needs a secret the server keeps and waits for the secret-backed sources; the
gateway refuses it with ``kind_not_supported``.
"""

from __future__ import annotations

from typing import Any, Final

from omnigent.server.factory.discovery.kinds.base import Backends, Kind
from omnigent.server.factory.discovery.kinds.repo import AppGitHubReader, RepoKind
from omnigent.server.factory.discovery.kinds.team import TeamKind
from omnigent.server.factory.discovery.kinds.web import WebKind

KINDS: Final[dict[str, Kind]] = {k.name: k for k in (RepoKind(), WebKind(), TeamKind())}
BACKENDS_ATTR: Final = "factory_source_backends"
"""``app.state`` attribute that replaces the backends (tests)."""


def kind(name: str) -> Kind | None:
    """:returns: The kind the gateway reads for *name*, or ``None`` (not without a secret)."""
    return KINDS.get(name)


def backends(app_state: Any) -> Backends:
    """:returns: The backends on ``app.state`` (tests), else the server's own."""
    from omnigent.server.factory.discovery.web_io import WebFetcher, search_from_env

    found = getattr(app_state, BACKENDS_ATTR, None)
    if isinstance(found, Backends):
        return found
    return Backends(
        github=AppGitHubReader(app_state), search=search_from_env(), fetcher=WebFetcher()
    )


__all__ = ["BACKENDS_ATTR", "KINDS", "backends", "kind"]
