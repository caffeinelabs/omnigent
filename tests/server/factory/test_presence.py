"""Factory presence: heartbeats with a TTL, frames on join/leave/move, last_seen
throttling, and the merge with member-session presence."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import pytest_asyncio

from omnigent.server import presence as session_presence
from omnigent.server.factory import people, snapshot
from omnigent.server.factory import presence as factory_presence
from omnigent.server.factory.presence import PresenceRegistry, presence_count
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit, error_code
from tests.server.factory.conftest import FactoryWorld, FakeClock

MIA = "mia@caffeine.ai"
FID = "a" * 32


@pytest.fixture(autouse=True)
def _fresh_state() -> Iterator[None]:
    snapshot.reset_cache()
    factory_presence.registry.reset()
    session_presence.reset_for_tests()
    yield
    snapshot.reset_cache()
    factory_presence.registry.clock = time.time
    factory_presence.registry.reset()
    session_presence.reset_for_tests()


# ── The registry ───────────────────────────────────────────────


def test_an_entry_lives_for_the_ttl_after_its_last_heartbeat() -> None:
    clock = FakeClock(1000.0)
    registry = PresenceRegistry(clock=clock)

    assert registry.heartbeat(FID, MIA, "overview", False) is True
    clock.advance(factory_presence.HEARTBEAT_S)
    assert registry.heartbeat(FID, MIA, "overview", False) is False
    clock.advance(factory_presence.TTL_S - 1)
    assert registry.is_present(FID, MIA)
    assert registry.count(FID) == 1
    clock.advance(1)
    assert registry.sweep(FID) == [MIA]
    assert registry.count(FID) == 0
    assert registry.last_seen(FID, MIA) == 1020.0


def test_a_move_or_an_idle_flip_is_a_change() -> None:
    registry = PresenceRegistry(clock=FakeClock(1.0))
    registry.heartbeat(FID, MIA, "overview", False)

    assert registry.heartbeat(FID, MIA, "project:db-version", False) is True
    assert registry.heartbeat(FID, MIA, "project:db-version", True) is True
    assert registry.heartbeat(FID, MIA, "project:db-version", True) is False
    assert registry.leave(FID, MIA) is True
    assert registry.leave(FID, MIA) is False


def test_last_seen_is_written_at_most_once_a_minute() -> None:
    clock = FakeClock(0.0)
    registry = PresenceRegistry(clock=clock)

    assert registry.claim_last_seen_write(FID, MIA) is True
    clock.advance(59)
    assert registry.claim_last_seen_write(FID, MIA) is False
    clock.advance(1)
    assert registry.claim_last_seen_write(FID, MIA) is True


@pytest.mark.parametrize(
    ("location", "valid"),
    [
        (None, True),
        ("overview", True),
        ("room:project:db-version", True),
        ("pr:9f2c", True),
        ("agent:" + "b" * 32, True),
        ("settings", True),
        ("Overview", False),
        ("room: x", False),
        ("x" * 200, False),
    ],
)
def test_locations_are_page_keys(location: str | None, valid: bool) -> None:
    assert factory_presence.valid_location(location) is valid


def test_cockpit_viewers_merge_with_member_session_viewers() -> None:
    pl, ic = "c" * 32, "d" * 32
    factory_presence.registry.heartbeat(FID, MIA, "overview", False)
    session_presence.connect(pl, ic, MIA, False)
    session_presence.connect(pl, pl, "ops@caffeine.ai", True)

    rows = {r["user_id"]: r for r in factory_presence.merged_entries(FID, [pl])}

    assert rows[MIA]["location"] == "overview"
    assert rows[MIA]["sessions"] == [pl]
    assert rows["ops@caffeine.ai"]["location"] == f"agent:{pl}"
    assert rows["ops@caffeine.ai"]["idle"] is True
    assert rows["ops@caffeine.ai"]["sessions"] == [pl]
    assert presence_count(FID) == 1


# ── The routes ─────────────────────────────────────────────────


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in build_cockpit(world, db_uri, monkeypatch):
        await people.set_person(value.app.state, value.fid, MIA, "viewer", by_user="alice")
        value.announces.clear()
        yield value


async def _beat(cockpit: Cockpit, user: str = MIA, **body: object) -> httpx.Response:
    return await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/presence", body, user=user)


async def test_a_heartbeat_announces_joins_and_moves_only(cockpit: Cockpit) -> None:
    clock = FakeClock(10_000.0)
    factory_presence.registry.clock = clock

    first = await _beat(cockpit, location="overview")
    same = await _beat(cockpit, location="overview")
    moved = await _beat(cockpit, location="project:api-version")

    assert first.status_code == 200, first.text
    assert first.json() == {"present": 1, "heartbeat_s": 20, "ttl_s": 60}
    assert same.status_code == moved.status_code == 200
    assert cockpit.announces == [(cockpit.fid, ["presence"]), (cockpit.fid, ["presence"])]
    stored = cockpit.store.get_person(cockpit.fid, MIA)
    assert stored is not None and stored.last_seen_at is not None


async def test_a_lapsed_viewer_leaves_on_the_next_sweep(cockpit: Cockpit) -> None:
    clock = FakeClock(10_000.0)
    factory_presence.registry.clock = clock
    await _beat(cockpit, location="overview")
    await _beat(cockpit, user="alice", location="overview")
    cockpit.announces.clear()

    clock.advance(factory_presence.TTL_S)
    resp = await _beat(cockpit, user="alice", location="overview")

    assert resp.json()["present"] == 1
    assert cockpit.announces == [(cockpit.fid, ["presence"])]
    listed = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/presence", headers={"X-Test-User": MIA}
    )
    assert [r["user_id"] for r in listed.json()["data"]] == ["alice"]


async def test_leave_drops_the_caller_at_once(cockpit: Cockpit) -> None:
    await _beat(cockpit, location="overview")
    cockpit.announces.clear()

    resp = await _beat(cockpit, leave=True)

    assert resp.json()["present"] == 0
    assert cockpit.announces == [(cockpit.fid, ["presence"])]


async def test_presence_shows_in_the_snapshot_and_the_list(cockpit: Cockpit) -> None:
    pl = cockpit.world.id("pl")
    await _beat(cockpit, location="room:factory")
    session_presence.connect(pl, pl, "alice", False)

    snap = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}", headers={"X-Test-User": MIA}
    )
    listed = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/presence", headers={"X-Test-User": MIA}
    )

    rows = {r["user_id"]: r for r in snap.json()["presence"]}
    assert rows[MIA]["location"] == "room:factory"
    assert rows["alice"]["sessions"] == [pl]
    assert listed.json()["data"] == snap.json()["presence"]


async def test_presence_needs_read_and_a_page_key(cockpit: Cockpit) -> None:
    outsider = await _beat(cockpit, user="bob", location="overview")
    bad = await _beat(cockpit, location="no spaces")

    assert outsider.status_code == 404
    assert (bad.status_code, error_code(bad)) == (400, "invalid_location")
    assert factory_presence.registry.count(cockpit.fid) == 0
