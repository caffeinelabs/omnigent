"""The factory event log (``events.emit``) and the live ``factory_changed`` frame."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from omnigent.runtime import user_session_stream
from omnigent.server.auth import LEVEL_OWNER, LEVEL_READ, RESERVED_USER_PUBLIC
from omnigent.server.factory import events, live
from omnigent.server.factory.context import FactoryDeps
from omnigent.server.routes._sessions.helpers import _discovery_key
from omnigent.stores.factory_store import InMemoryFactoryStore
from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore

FID = uuid.uuid4().hex
PL = uuid.uuid4().hex


@pytest.fixture(autouse=True)
def fast_debounce(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live, "DEBOUNCE_S", 0.05)


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """Every discovery-channel publish, as ``(user key, frame)``."""
    frames: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        user_session_stream, "publish", lambda key, frame: frames.append((key, frame))
    )
    return frames


def _state(store: Any = None, permission_store: Any = None) -> SimpleNamespace:
    deps = FactoryDeps(
        conversation_store=None,  # type: ignore[arg-type]
        agent_store=None,  # type: ignore[arg-type]
        permission_store=permission_store,
        factory_store=store if store is not None else InMemoryFactoryStore(),
    )
    return SimpleNamespace(factory=deps)


async def _settle() -> None:
    await asyncio.sleep(live.DEBOUNCE_S * 3)


class _BrokenStore(InMemoryFactoryStore):
    def append_event(self, draft: Any) -> Any:
        raise RuntimeError("database is down")


async def test_emit_stores_the_event_and_announces(
    published: list[tuple[str, dict[str, Any]]],
) -> None:
    state = _state()
    event = await events.emit(
        state,
        FID,
        "pr.ready",
        severity="notice",
        project="api-version",
        from_id=PL,
        to_id="server",
        subject="pr:caffeinelabs/app#9630",
        summary="PR #9630 ready " + "x" * 400,
        data={"gates": 6},
    )
    assert event is not None
    assert event.severity == "notice" and event.from_id == PL and event.to_id is None
    assert event.data == {"gates": 6, "to": "server"}
    assert len(event.summary) == 300
    assert state.factory.factory_store.list_events(FID) == [event]
    await _settle()
    assert published == [
        (
            _discovery_key(None),
            {"type": "factory_changed", "factory_id": FID, "scopes": ["events", "snapshot"]},
        )
    ]


async def test_emit_never_raises_when_the_store_fails(
    published: list[tuple[str, dict[str, Any]]], caplog: pytest.LogCaptureFixture
) -> None:
    state = _state(_BrokenStore())
    assert await events.emit(state, FID, "pr.ready", summary="x") is None
    assert "could not record pr.ready" in caplog.text
    assert await events.emit(SimpleNamespace(), FID, "pr.ready", summary="x") is None
    assert await events.emit(state, FID, "pr.ready", severity="loud", summary="x") is None
    await _settle()
    # The cockpit still refetches: something may have changed even if the log write failed.
    assert [frame["factory_id"] for _, frame in published] == [FID]


async def test_listeners_run_after_each_event_and_errors_are_contained() -> None:
    seen: list[str] = []

    async def good(app_state: Any, event: Any) -> None:
        seen.append(event.kind)

    def bad(app_state: Any, event: Any) -> None:
        raise RuntimeError("boom")

    remove_good = events.add_listener(good)
    remove_bad = events.add_listener(bad)
    try:
        await events.emit(_state(), FID, "message.delivered", summary="delivered")
    finally:
        remove_good()
        remove_bad()
    await events.emit(_state(), FID, "message.delivered", summary="again")
    assert seen == ["message.delivered"]


def test_scopes_for_decision_events() -> None:
    assert events.scopes_for("decision.answered") == ["snapshot", "events", "decisions"]
    assert events.scopes_for("gate.changed") == ["snapshot", "events"]


async def test_debounce_coalesces_five_announces_into_one(
    published: list[tuple[str, dict[str, Any]]],
) -> None:
    state = _state()
    for scope in ("snapshot", "events", "snapshot", "decisions", "events"):
        live.announce_factory_changed(state, FID, [scope])
    other = uuid.uuid4().hex
    live.announce_factory_changed(state, other)
    await _settle()
    frames = sorted((frame for _, frame in published), key=lambda f: f["factory_id"] != FID)
    assert frames == [
        {
            "type": "factory_changed",
            "factory_id": FID,
            "scopes": ["decisions", "events", "snapshot"],
        },
        {"type": "factory_changed", "factory_id": other, "scopes": ["snapshot"]},
    ]
    live.announce_factory_changed(state, FID, ["events"])
    await _settle()
    assert len(published) == 3


@pytest.fixture
def permissions(db_uri: str) -> Iterator[SqlAlchemyPermissionStore]:
    store = SqlAlchemyPermissionStore(db_uri)
    for user in ("alice", "bob", "carol"):
        store.ensure_user(user)
    store.grant("alice", FID, LEVEL_OWNER)
    store.grant("bob", FID, LEVEL_READ)
    store.grant(RESERVED_USER_PUBLIC, FID, LEVEL_READ)
    yield store


async def test_fan_out_reaches_the_owner_and_every_grantee(
    permissions: SqlAlchemyPermissionStore,
) -> None:
    state = _state(permission_store=permissions)
    received: dict[str, list[dict[str, Any]]] = {"alice": [], "bob": [], "carol": []}

    async def listen(user: str) -> None:
        async for frame in user_session_stream.subscribe(_discovery_key(user)):
            received[user].append(frame)

    tasks = [asyncio.create_task(listen(u)) for u in received]
    await asyncio.sleep(0)
    try:
        assert live.recipients(state, FID) == ["alice", "bob"]
        live.announce_factory_changed(state, FID, ["decisions"])
        await _settle()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    frame = {"type": "factory_changed", "factory_id": FID, "scopes": ["decisions"]}
    assert received == {"alice": [frame], "bob": [frame], "carol": []}


def test_announce_outside_a_loop_publishes_at_once(
    published: list[tuple[str, dict[str, Any]]],
) -> None:
    live.announce_factory_changed(_state(), FID, ["snapshot"])
    assert [frame["factory_id"] for _, frame in published] == [FID]


async def test_frames_carry_the_bare_factory_id(
    published: list[tuple[str, dict[str, Any]]],
) -> None:
    state = _state()
    live.announce_factory_changed(state, str(uuid.UUID(FID)), ["snapshot"])
    live.announce_factory_changed(state, f"conv_{FID}", ["events"])
    live.announce_factory_changed(state, "not-a-uuid", ["snapshot"])
    await _settle()
    frames = sorted((frame for _, frame in published), key=lambda f: f["factory_id"] != FID)
    assert frames == [
        {"type": "factory_changed", "factory_id": FID, "scopes": ["events", "snapshot"]},
        {"type": "factory_changed", "factory_id": "not-a-uuid", "scopes": ["snapshot"]},
    ]


def test_recipients_read_the_grants_once(permissions: SqlAlchemyPermissionStore) -> None:
    calls: list[str] = []
    real = permissions.list_for_session

    def counting(session_id: str, **kwargs: Any) -> Any:
        calls.append(session_id)
        return real(session_id, **kwargs)

    permissions.list_for_session = counting  # type: ignore[method-assign]
    assert live.recipients(_state(permission_store=permissions), FID) == ["alice", "bob"]
    assert calls == [FID]
