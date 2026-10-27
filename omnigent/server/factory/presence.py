"""Factory-wide presence: who has the cockpit open, and where (in memory).

The cockpit posts a heartbeat (``POST /v1/factory/factories/{fid}/presence``)
every :data:`HEARTBEAT_S` with the page the person is on; an entry lives
:data:`TTL_S` past its last heartbeat. A join, a leave, a move to another page
or an idle flip publishes a ``factory_changed`` frame with the ``presence``
scope. :func:`merged_entries` adds who is inside which agent chat, read from
the session presence registry (:mod:`omnigent.server.presence`), so the list
covers the cockpit and every member transcript.

Like the session registry this is process-local state on one replica; it
dies with the process and every client heartbeats it back within 20 s.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from omnigent.server import presence as session_presence
from omnigent.stores.factory_store import norm_id

HEARTBEAT_S: Final = 20
"""How often the cockpit posts a heartbeat."""
TTL_S: Final = 60
"""An entry without a heartbeat for this long has left."""
LAST_SEEN_WRITE_S: Final = 60
"""``factory_people.last_seen_at`` is written at most once per this window."""
LOCATION_MAX: Final = 120
LOCATION_RE: Final = re.compile(r"^[a-z][a-z_]{0,31}(?::[A-Za-z0-9_.:#!@/+-]{1,96})?$")
"""A page key: ``overview``, ``room:<thread>``, ``project:<slug>``, ``agent:<sid>`` …"""


@dataclass
class _Entry:
    """
    One person with the cockpit open.

    :param since: Epoch seconds of the join.
    :param seen: Epoch seconds of the last heartbeat.
    """

    user_id: str
    location: str | None
    idle: bool
    since: float
    seen: float


@dataclass
class PresenceRegistry:
    """
    Cockpit heartbeats per factory.

    :param clock: Wall clock in epoch seconds (injectable for tests).
    """

    clock: Callable[[], float] = time.time
    _entries: dict[str, dict[str, _Entry]] = field(default_factory=dict)
    _last_seen_writes: dict[tuple[str, str], float] = field(default_factory=dict)
    _left: dict[tuple[str, str], float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def heartbeat(self, factory_id: str, user_id: str, location: str | None, idle: bool) -> bool:
        """
        Record a heartbeat.

        :returns: Whether the visible state changed (join, move or idle flip).
        """
        fid = norm_id(factory_id)
        now = self.clock()
        with self._lock:
            users = self._entries.setdefault(fid, {})
            entry = users.get(user_id)
            if entry is None:
                users[user_id] = _Entry(user_id, location, idle, now, now)
                return True
            changed = entry.location != location or entry.idle != idle
            entry.location, entry.idle, entry.seen = location, idle, now
            return changed

    def leave(self, factory_id: str, user_id: str) -> bool:
        """Drop *user_id* at once (the page closed). :returns: Whether they were present."""
        fid = norm_id(factory_id)
        with self._lock:
            entry = self._entries.get(fid, {}).pop(user_id, None)
            if entry is not None:
                self._left[(fid, user_id)] = entry.seen
            return entry is not None

    def sweep(self, factory_id: str) -> list[str]:
        """Expire entries past :data:`TTL_S`. :returns: The users who left."""
        fid = norm_id(factory_id)
        now = self.clock()
        with self._lock:
            users = self._entries.get(fid, {})
            gone = [u for u, e in users.items() if now - e.seen >= TTL_S]
            for user in gone:
                self._left[(fid, user)] = users.pop(user).seen
            return gone

    def entries(self, factory_id: str) -> list[_Entry]:
        """:returns: Live entries (after a sweep), oldest join first."""
        self.sweep(factory_id)
        with self._lock:
            rows = list(self._entries.get(norm_id(factory_id), {}).values())
        return sorted(rows, key=lambda e: (e.since, e.user_id))

    def count(self, factory_id: str) -> int:
        """:returns: How many people have the cockpit open."""
        return len(self.entries(factory_id))

    def is_present(self, factory_id: str, user_id: str) -> bool:
        """:returns: Whether *user_id* has the cockpit open now."""
        return any(e.user_id == user_id for e in self.entries(factory_id))

    def last_seen(self, factory_id: str, user_id: str) -> float | None:
        """:returns: Epoch seconds of *user_id*'s last heartbeat in this process, if any."""
        fid = norm_id(factory_id)
        with self._lock:
            entry = self._entries.get(fid, {}).get(user_id)
            if entry is not None:
                return entry.seen
            return self._left.get((fid, user_id))

    def claim_last_seen_write(self, factory_id: str, user_id: str) -> bool:
        """:returns: ``True`` (and starts the window) when ``last_seen_at`` is due a write."""
        key = (norm_id(factory_id), user_id)
        now = self.clock()
        with self._lock:
            last = self._last_seen_writes.get(key)
            if last is not None and now - last < LAST_SEEN_WRITE_S:
                return False
            self._last_seen_writes[key] = now
            return True

    def reset(self) -> None:
        """Forget everything (tests)."""
        with self._lock:
            self._entries.clear()
            self._last_seen_writes.clear()
            self._left.clear()


registry = PresenceRegistry()
"""The process's registry."""


def presence_count(factory_id: str) -> int:
    """
    How many people have *factory_id*'s cockpit open (the "now" poll gates on it).

    :param factory_id: Root session id.
    :returns: Live cockpit viewers.
    """
    return registry.count(factory_id)


def valid_location(location: str | None) -> bool:
    """:returns: Whether *location* is ``None`` or a well-formed page key."""
    return location is None or (
        len(location) <= LOCATION_MAX and LOCATION_RE.fullmatch(location) is not None
    )


def _iso_epoch(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def merged_entries(factory_id: str, member_ids: Iterable[str]) -> list[dict[str, Any]]:
    """
    Cockpit viewers merged with who has a member transcript open.

    :param factory_id: Root session id.
    :param member_ids: Top-level members the viewer may read (their session
        trees are scanned; sub-agent pages count toward their member).
    :returns: ``PresenceEntry`` dicts (``name`` is filled by the caller), cockpit
        viewers first, then people only in agent chats.
    """
    out: dict[str, dict[str, Any]] = {}
    sessions: dict[str, list[str]] = {}
    for entry in registry.entries(factory_id):
        out[entry.user_id] = {
            "user_id": entry.user_id,
            "location": entry.location,
            "idle": entry.idle,
            "since": int(entry.since),
        }
    for member_id in member_ids:
        viewers = session_presence.snapshot(member_id, member_id).get("viewers") or []
        for viewer in viewers:
            user = viewer.get("user_id")
            if not isinstance(user, str) or not user:
                continue
            if user not in out:
                out[user] = {
                    "user_id": user,
                    "location": f"agent:{member_id}",
                    "idle": bool(viewer.get("idle")),
                    "since": _iso_epoch(viewer.get("joined_at")),
                }
            opened = sessions.setdefault(user, [])
            if member_id not in opened:
                opened.append(member_id)
    return [{**row, "sessions": sessions.get(user, [])} for user, row in out.items()]


__all__ = [
    "HEARTBEAT_S",
    "LAST_SEEN_WRITE_S",
    "TTL_S",
    "PresenceRegistry",
    "merged_entries",
    "presence_count",
    "registry",
    "valid_location",
]
