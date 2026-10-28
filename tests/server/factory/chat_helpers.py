"""Shared setup for the Room tests (routing, delivery, cards and routes).

The cockpit over the seeded factory (Atlas, Sentinel, ``PL · api-version`` with
a project row), with a real mailer whose delivery is recorded, and a Room whose
"is this session running" answer and coalescing timer the test controls.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from omnigent.server.factory import chat
from omnigent.server.factory.delivery import DeliveryOutcome
from omnigent.server.factory.mailer import MAILER_STATE_ATTR, Mailer
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit
from tests.server.factory.conftest import FactoryWorld, FakeClock
from tests.server.factory.projects_helpers import seed


@dataclass
class Deliveries:
    """Records what the mailer delivers (text and the human it is attributed to)."""

    calls: list[tuple[str, str, str | None]] = field(default_factory=list)
    gate: asyncio.Event | None = None

    async def __call__(
        self,
        app_state: Any,
        session_id: str,
        text: str,
        *,
        restart: bool = False,
        created_by: str | None = None,
    ) -> DeliveryOutcome:
        if self.gate is not None:
            await self.gate.wait()
        self.calls.append((session_id, text, created_by))
        return DeliveryOutcome(True)

    def to(self, session_id: str) -> list[str]:
        """:returns: Texts delivered to *session_id*, oldest first."""
        return [text for sid, text, _ in self.calls if sid == session_id]


@dataclass
class Busy:
    """The sessions the Room should treat as running."""

    running: set[str] = field(default_factory=set)

    async def __call__(self, session_id: str) -> bool:
        return session_id in self.running


@dataclass
class Timer:
    """A coalescing timer that fires only when released."""

    delays: list[float] = field(default_factory=list)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)
        await self.release.wait()


@dataclass
class RoomEnv:
    """The cockpit plus the Room's moving parts."""

    cockpit: Cockpit
    deliveries: Deliveries
    busy: Busy
    timer: Timer
    clock: FakeClock
    mailer: Mailer
    room: chat.Room

    @property
    def world(self) -> FactoryWorld:
        """:returns: The seeded world."""
        return self.cockpit.world

    def id(self, key: str) -> str:
        """:returns: The id of a seeded session, as the Room keys it."""
        return chat._key(self.world.id(key))

    async def settle(self) -> None:
        """Let queued mail and Room writes land."""
        for _ in range(3):
            await self.mailer.drain()
            await self.room.drain()

    def messages(self, thread: str | None = None) -> list[Any]:
        """:returns: The Room's messages (all threads, or one), oldest first."""
        return self.cockpit.store.list_chat(self.cockpit.fid, thread, limit=500)

    def new_room(self) -> chat.Room:
        """Replace the Room (and the mailer), as a server restart does."""
        state = self.cockpit.app.state
        self.room = chat.Room(state, busy=self.busy, sleep=self.timer, clock=self.clock)
        self.mailer = Mailer(state, deliver=self.deliveries, sleep=_instant, clock=self.clock)
        setattr(state, chat.ROOM_STATE_ATTR, self.room)
        setattr(state, MAILER_STATE_ATTR, self.mailer)
        return self.room


async def _instant(_seconds: float) -> None:
    return None


async def room_env(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[RoomEnv]:
    """Yield a :class:`RoomEnv` (use from an async fixture)."""
    async for cockpit in build_cockpit(world, db_uri, monkeypatch):
        seed(cockpit)
        env = RoomEnv(
            cockpit=cockpit,
            deliveries=Deliveries(),
            busy=Busy(),
            timer=Timer(),
            clock=FakeClock(),
            mailer=cockpit.mailer,
            room=chat.get_room(cockpit.app.state),
        )
        env.new_room()
        try:
            yield env
        finally:
            env.timer.release.set()
            await env.room.close()
            await env.mailer.close()


__all__ = ["Busy", "Deliveries", "RoomEnv", "Timer", "room_env"]
