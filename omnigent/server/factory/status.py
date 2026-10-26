"""In-process registry of the factory services' own health.

The supervisor, GitHub poller and preview prober post what they last did here
(``set_status("github", state="ok", last_poll_at=...)``); the snapshot reads
it for the cockpit's health strip. Process-local by design: it describes this
server process, and resets with it.
"""

from __future__ import annotations

import copy
import threading
import time
from typing import Any

_lock = threading.Lock()
_registry: dict[str, dict[str, Any]] = {}


def set_status(name: str, **fields: Any) -> dict[str, Any]:
    """
    Merge *fields* into service *name*'s status and stamp ``updated_at``.

    :param name: Service name, e.g. ``"supervisor"``, ``"github"``, ``"probes"``.
    :param fields: e.g. ``ok=True, last_tick_at=1767000000``. ``None`` values
        are stored (they clear a field).
    :returns: A copy of the service's merged status.
    """
    with _lock:
        entry = _registry.setdefault(name, {})
        entry.update(fields)
        entry["updated_at"] = int(time.time())
        return copy.deepcopy(entry)


def get_status(name: str) -> dict[str, Any] | None:
    """:returns: A copy of one service's status, or ``None`` if it never reported."""
    with _lock:
        entry = _registry.get(name)
        return copy.deepcopy(entry) if entry is not None else None


def get_all() -> dict[str, dict[str, Any]]:
    """:returns: A deep copy of every service's status, keyed by name."""
    with _lock:
        return copy.deepcopy(_registry)


def reset() -> None:
    """Forget every status (tests)."""
    with _lock:
        _registry.clear()


__all__ = ["get_all", "get_status", "reset", "set_status"]
