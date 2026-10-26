"""The factory event log: one call to record what happened and tell the cockpit.

Every factory service records state changes with :func:`emit`. It never raises
(a lost event must not break delivery or supervision) and then announces a
``factory_changed`` frame so open cockpits refetch.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from omnigent.db.db_models import InvalidUuidError, uuid_to_bytes
from omnigent.db.enum_codecs import FACTORY_EVENT_SEVERITY, decode_factory_event_severity
from omnigent.entities.factory import EventDraft, FactoryEvent
from omnigent.server.factory import live

_logger = logging.getLogger(__name__)

EventListener = Callable[[Any, FactoryEvent], Awaitable[None] | None]
"""Called with ``(app_state, event)`` after each stored event."""

_listeners: list[EventListener] = []

_KIND_MAX = 32
_SUMMARY_MAX = 300
_SUBJECT_MAX = 128
_SLUG_MAX = 64


def add_listener(listener: EventListener) -> Callable[[], None]:
    """
    Run *listener* after every stored event (e.g. to fill a decision's receipt).

    Listener errors are logged and swallowed.

    :returns: A callable that removes the listener.
    """
    _listeners.append(listener)

    def _remove() -> None:
        if listener in _listeners:
            _listeners.remove(listener)

    return _remove


def _severity(value: int | str) -> str:
    if isinstance(value, int):
        return decode_factory_event_severity(value)
    if value not in FACTORY_EVENT_SEVERITY:
        raise ValueError(f"unknown severity {value!r}")
    return value


def _clip(value: str | None, limit: int) -> str | None:
    if value is None or len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _session_or_data(value: str | None, key: str, data: dict[str, Any]) -> str | None:
    """Keep a session id; move any other actor (``"server"``, ``"user:x"``) into data."""
    if value is None:
        return None
    try:
        uuid_to_bytes(value)
    except InvalidUuidError:
        data.setdefault(key, value)
        return None
    return value


def scopes_for(kind: str) -> list[str]:
    """:returns: The cockpit query scopes an event of *kind* invalidates."""
    scopes = ["snapshot", "events"]
    if kind.startswith("decision.") or kind == "message.delivered":
        scopes.append("decisions")
    return scopes


async def _notify(app_state: Any, event: FactoryEvent) -> None:
    for listener in list(_listeners):
        try:
            result = listener(app_state, event)
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - a listener bug must not break the emitter
            _logger.warning("factory: event listener failed for %s", event.kind, exc_info=True)


async def emit(
    app_state: Any,
    factory_id: str,
    kind: str,
    *,
    severity: int | str = 0,
    project: str | None = None,
    from_id: str | None = None,
    to_id: str | None = None,
    subject: str | None = None,
    summary: str,
    data: dict[str, Any] | None = None,
    ts: int | None = None,
) -> FactoryEvent | None:
    """
    Append an event to the factory log and announce the change. Never raises.

    :param app_state: ``app.state`` carrying the factory deps.
    :param factory_id: Root lead session id.
    :param kind: Stable kind, e.g. ``"pr.ready"`` (≤ 32 chars).
    :param severity: ``0``-``3`` or ``info``/``notice``/``warning``/``critical``.
    :param project: Project slug.
    :param from_id: Acting session id; a non-session actor is kept in ``data["from"]``.
    :param to_id: Target session id; likewise ``data["to"]``.
    :param subject: e.g. ``"pr:caffeinelabs/app#9630"``.
    :param summary: One line (clipped to 300 chars).
    :param data: JSON payload.
    :param ts: Epoch milliseconds, for callers on their own clock; defaults to now.
    :returns: The stored event, or ``None`` when it could not be stored.
    """
    event: FactoryEvent | None = None
    try:
        from omnigent.server.factory.context import factory_deps

        store = factory_deps(app_state).factory_store
        payload = dict(data) if data else {}
        draft = EventDraft(
            factory_id=factory_id,
            kind=kind[:_KIND_MAX],
            summary=_clip(summary, _SUMMARY_MAX) or "",
            severity=_severity(severity),
            project_slug=_clip(project, _SLUG_MAX),
            from_id=_session_or_data(from_id, "from", payload),
            to_id=_session_or_data(to_id, "to", payload),
            subject=_clip(subject, _SUBJECT_MAX),
            data=payload or None,
            ts=ts,
        )
        event = await asyncio.to_thread(store.append_event, draft)
    except Exception:  # noqa: BLE001 - emit never raises; a lost event is only logged
        _logger.warning(
            "factory: could not record %s event for factory %s", kind, factory_id, exc_info=True
        )
    if event is not None:
        await _notify(app_state, event)
    try:
        live.announce_factory_changed(app_state, factory_id, scopes_for(kind))
    except Exception:  # noqa: BLE001 - the cockpit falls back to polling
        _logger.warning("factory: could not announce %s for %s", kind, factory_id, exc_info=True)
    return event


__all__ = ["EventListener", "add_listener", "emit", "scopes_for"]
