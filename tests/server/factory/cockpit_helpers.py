"""Shared setup for the cockpit write-API tests.

The factory routes over the seeded :class:`FactoryWorld`, with SQL factory,
project and permission stores, header auth (``X-Test-User``) and a mailer that
records every submit instead of delivering. Grants: ``alice`` owns every
top-level session, ``carol`` has EDIT on the root lead and the PL, ``bob`` has
nothing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from omnigent import factory as F
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.mailer import MAILER_STATE_ATTR, Mailer, MailItem
from omnigent.stores.factory_store import FactoryStore
from tests.server.factory.conftest import FactoryWorld, HeaderAuth


class RecordingMailer(Mailer):
    """A mailer that records submitted mail and delivers nothing."""

    def __init__(self, app_state: Any) -> None:
        super().__init__(app_state)
        self.items: list[MailItem] = []

    def submit(self, item: MailItem) -> None:
        self.items.append(item)

    def to(self, session_id: str) -> list[MailItem]:
        """:returns: Mail queued to *session_id*, oldest first."""
        return [i for i in self.items if i.target_id == session_id]

    def texts_to(self, session_id: str) -> list[str]:
        """:returns: Texts queued to *session_id*, oldest first."""
        return [i.text for i in self.to(session_id)]


@dataclass
class Cockpit:
    """The app, its client, stores and the recorded mail and announces."""

    world: FactoryWorld
    app: Any
    client: httpx.AsyncClient
    mailer: RecordingMailer
    permissions: Any
    project_store: Any
    announces: list[tuple[str, list[str]]] = field(default_factory=list)

    @property
    def deps(self) -> FactoryDeps:
        """:returns: The app's factory deps."""
        return factory_deps(self.app.state)

    @property
    def store(self) -> FactoryStore:
        """:returns: The app's factory store."""
        return self.deps.factory_store

    @property
    def fid(self) -> str:
        """:returns: The seeded factory's root id (Lead A)."""
        return self.world.id("lead_a")

    async def post(self, path: str, json: Any = None, *, user: str = "alice") -> httpx.Response:
        """POST as *user*."""
        return await self.client.post(path, json=json, headers={"X-Test-User": user})

    async def patch(self, path: str, json: Any = None, *, user: str = "alice") -> httpx.Response:
        """PATCH as *user*."""
        return await self.client.patch(path, json=json, headers={"X-Test-User": user})

    def events(self, kind: str | None = None) -> list[Any]:
        """:returns: The factory's events (oldest first), optionally of one kind."""
        rows = list(reversed(self.store.list_events(self.fid, limit=500)))
        return [e for e in rows if kind is None or e.kind == kind]


async def build_cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    """Yield a :class:`Cockpit` over *world* (use from an async fixture)."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    from omnigent.errors import OmnigentError
    from omnigent.server.auth import LEVEL_EDIT, LEVEL_OWNER
    from omnigent.server.factory import control, live, register_factory_routes
    from omnigent.stores.factory_store.sqlalchemy_store import SqlAlchemyFactoryStore
    from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore
    from omnigent.stores.project_store.sqlalchemy_store import SqlAlchemyProjectStore

    monkeypatch.setenv(F.FACTORY_ENABLED_ENV, "1")
    permissions = SqlAlchemyPermissionStore(db_uri)
    for user in ("alice", "bob", "carol"):
        permissions.ensure_user(user)
    for conv in world.sessions.values():
        if conv.parent_conversation_id is None:
            permissions.grant("alice", conv.id, LEVEL_OWNER)
    for key in ("lead_a", "pl"):
        permissions.grant("carol", world.id(key), LEVEL_EDIT)
    projects = SqlAlchemyProjectStore(db_uri)
    auth: Any = HeaderAuth()

    app = FastAPI()
    register_factory_routes(
        app,
        conversation_store=world.store,
        agent_store=world.agent_store,
        agent_cache=world.deps.agent_cache,
        permission_store=permissions,
        auth_provider=auth,
        factory_store=SqlAlchemyFactoryStore(db_uri),
        project_store=projects,
    )

    async def _omnigent_error(_request: Any, exc: Exception) -> JSONResponse:
        assert isinstance(exc, OmnigentError)
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    app.add_exception_handler(OmnigentError, _omnigent_error)
    world.app_state = app.state  # type: ignore[assignment]
    mailer = RecordingMailer(app.state)
    setattr(app.state, MAILER_STATE_ATTR, mailer)
    announces: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        live,
        "announce_factory_changed",
        lambda _state, fid, scopes=("snapshot",): announces.append((fid, list(scopes))),
    )
    control.refresh_limiter.reset()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield Cockpit(
            world=world,
            app=app,
            client=client,
            mailer=mailer,
            permissions=permissions,
            project_store=projects,
            announces=announces,
        )
    control.refresh_limiter.reset()


def error_code(resp: httpx.Response) -> str:
    """:returns: The ``error.code`` of a refusal."""
    return resp.json()["error"]["code"]
