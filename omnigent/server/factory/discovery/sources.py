"""Sources: the factory's ``discovery.sources[]``, resolved for a caller, and the gateway's checks.

A source is data an owner approved (class B): its kind, parameters, the
seats that may query it, its privacy and its daily call cap. Nothing in code
enables one. Its running state lives in ``factory_source_state``: today's
calls, the sync cursor, the last success and error, and a *hold* the server
puts on it after :data:`FAIL_LIMIT` failures in a row (an incident tells the
owners; ``POST …/sources/{id}/resume`` lifts it).

The caller's seat comes from the labels the server stamped on its session
(:class:`~omnigent.server.factory.membership.Membership`), never from a tool's
input: the factory's leads are ``lead``, the discovery seat is ``scout``.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

from omnigent.server.factory import definition
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.discovery.kinds import kind as kind_of
from omnigent.server.factory.discovery.kinds.base import Backends, Kind, Source
from omnigent.server.factory.discovery.ledger import SALT_ROW, ref_of
from omnigent.stores.factory_store import now_s

if TYPE_CHECKING:
    from omnigent.entities.factory_discovery import FactorySourceState
    from omnigent.server.factory.context import FactoryDeps

SEATS: Final[dict[str, str]] = {"lead": "lead", "scout": "scout"}
"""Membership role → the ``discovery.sources[].roles`` seat it fills."""
WRITE_LOOKING: Final = re.compile(
    r"(^|[_\-.])(create|update|delete|remove|post|send|merge|write|edit|patch|put|publish"
    r"|close|comment|push|upload|invite|approve)([_\-.]|$)",
    re.IGNORECASE,
)
"""Tool names refused even when listed: a source is read-only (mechanism §4.3)."""
FAIL_LIMIT: Final = 3
"""Failures in a row that put a source on hold and open an incident."""
INCIDENT_RULE: Final = "source.failed"
SUBJECT_PREFIX: Final = "source:"
HOLD_KEY: Final = "hold"
LAST_SYNC_KEY: Final = "last_sync_at"
PAUSED_FROM_KEY: Final = "paused_from"
STOPPED: Final = frozenset({"paused", "pending"})
"""Source states nothing reads: paused by a person, or pending an owner's approval."""


def today(now: int | None = None) -> str:
    """:returns: The UTC day of *now* (``YYYY-MM-DD``), the day calls count against."""
    return time.strftime("%Y-%m-%d", time.gmtime(now if now is not None else now_s()))


def subject(source_id: str) -> str:
    """:returns: The event subject of a source, e.g. ``"source:web"``."""
    return f"{SUBJECT_PREFIX}{source_id}"


def target(source_id: str) -> str:
    """:returns: The incident target of a source (same as its event subject)."""
    return subject(source_id)


# ── Resolving ──────────────────────────────────────────────────


def _source(raw: Mapping[str, Any]) -> Source:
    budget = raw.get("budget") or {}
    return Source(
        id=str(raw["id"]),
        label=str(raw.get("label") or raw["id"]),
        kind=str(raw.get("kind") or ""),
        params=dict(raw.get("params") or {}),
        roles=tuple(str(r) for r in raw.get("roles") or ()),
        state=str(raw.get("state") or "trial"),
        calls_per_day=int(budget.get("calls_per_day", 0) or 0),
        interval_min=int(raw.get("interval_min") or 0),
        ref=ref_of(raw),
    )


def listed(deps: FactoryDeps, factory_id: str) -> list[Source]:
    """:returns: The factory's ``discovery.sources`` in order (the definition in force)."""
    found = definition.value(deps, factory_id, "discovery.sources", []) or []
    return [_source(s) for s in found if isinstance(s, Mapping) and s.get("id")]


def resolve(deps: FactoryDeps, factory_id: str, source_id: str) -> Source:
    """
    :returns: Source *source_id* of the factory.
    :raises CockpitError: 404 ``unknown_source``.
    """
    for source in listed(deps, factory_id):
        if source.id == source_id:
            return source
    raise CockpitError(
        404,
        "unknown_source",
        f"no source {source_id!r} in this factory's discovery.sources",
        source=source_id,
    )


def seat_of(role: str | None) -> str | None:
    """:returns: The source seat of a membership *role* (``None``: it queries no source)."""
    return SEATS.get(role or "")


# ── Checks ─────────────────────────────────────────────────────


def require_kind(source: Source) -> Kind:
    """:raises CockpitError: 422 ``kind_not_supported`` for a kind that needs a secret."""
    found = kind_of(source.kind)
    if found is None:
        raise CockpitError(
            422,
            "kind_not_supported",
            f"source {source.id} is a {source.kind} source: it needs a secret the server keeps, "
            "which this server does not read yet (repo, web and team need none)",
            source=source.id,
        )
    return found


def require_role(source: Source, role: str | None) -> str:
    """
    :returns: The caller's seat.
    :raises CockpitError: 403 ``role_not_allowed``.
    """
    seat = seat_of(role)
    if seat is None or seat not in source.roles:
        who = seat or role or "this member"
        raise CockpitError(
            403,
            "role_not_allowed",
            f"{who} may not read source {source.id} "
            f"(it is for {', '.join(source.roles) or 'no one'})",
            source=source.id,
        )
    return seat


def held(state: FactorySourceState | None) -> dict[str, Any] | None:
    """:returns: The server's hold on a source after repeated failures, if any."""
    found = (state.data or {}).get(HOLD_KEY) if state is not None else None
    return dict(found) if isinstance(found, Mapping) else None


def require_running(source: Source, state: FactorySourceState | None) -> None:
    """:raises CockpitError: 409 ``source_paused``, ``source_pending`` or ``source_failing``."""
    if source.state == "paused":
        raise CockpitError(409, "source_paused", f"source {source.id} is paused", source=source.id)
    if source.state == "pending":
        raise CockpitError(
            409,
            "source_pending",
            f"source {source.id} waits for an owner's approval; nothing reads it until then",
            source=source.id,
        )
    hold = held(state)
    if hold is not None:
        # The hold's reason may quote what the source sent: it stays in the log, for people.
        raise CockpitError(
            409,
            "source_failing",
            f"source {source.id} failed {hold.get('failures', FAIL_LIMIT)} times in a row and is "
            "on hold; a person resumes it",
            source=source.id,
        )


def require_tool(kind: Kind, source: Source, backends: Backends, tool: str) -> None:
    """
    The tool is one this source offers, and it is not write-looking.

    :raises CockpitError: 403 ``tool_not_allowed``.
    """
    allowed = kind.allowed(source, backends)
    if WRITE_LOOKING.search(tool):
        raise CockpitError(
            403,
            "tool_not_allowed",
            f"{tool} looks like a write; sources are read-only",
            source=source.id,
            tools=allowed,
        )
    if tool not in allowed:
        raise CockpitError(
            403,
            "tool_not_allowed",
            f"source {source.id} offers {', '.join(allowed) or 'no tool'}, not {tool}",
            source=source.id,
            tools=allowed,
        )


def charge(deps: FactoryDeps, factory_id: str, source: Source, *, now: int | None = None) -> int:
    """
    Count one call against the source's daily cap.

    :returns: The calls counted today, this one included.
    :raises CockpitError: 429 ``over_budget``.
    """
    state, ok = deps.factory_store.charge_source_call(
        factory_id, source.id, day=today(now), limit=source.calls_per_day
    )
    if not ok:
        raise CockpitError(
            429,
            "over_budget",
            f"source {source.id} used its {source.calls_per_day} calls today; it reads again "
            "tomorrow (UTC), or an owner raises its budget",
            source=source.id,
            calls_per_day=source.calls_per_day,
        )
    return state.calls_today


def usage_today(state: FactorySourceState | None, now: int | None = None) -> int:
    """:returns: The calls counted today (0 when the last ones were on another day)."""
    if state is None or state.usage_day != today(now):
        return 0
    return state.calls_today


# ── Views ──────────────────────────────────────────────────────


def health(state: FactorySourceState | None) -> str:
    """:returns: ``failing`` (on hold), ``ok``, ``erroring`` (failing, not yet held) or ``new``."""
    if held(state) is not None:
        return "failing"
    if state is None or (state.last_ok_at is None and state.last_error_at is None):
        return "new"
    if state.failures:
        return "erroring"
    return "ok"


def status(
    source: Source,
    state: FactorySourceState | None,
    backends: Backends,
    *,
    now: int | None = None,
) -> dict[str, Any]:
    """:returns: A source as the cockpit and agents see it (never a secret: it has none)."""
    found = kind_of(source.kind)
    data = (state.data or {}) if state is not None else {}
    last_sync = data.get(LAST_SYNC_KEY)
    syncs = bool(found is not None and found.syncs and source.interval_min)
    try:
        tools = found.allowed(source, backends) if found is not None else []
        invalid = None
    except CockpitError as exc:
        tools, invalid = [], exc.message
    return {
        "id": source.id,
        "label": source.label,
        "kind": source.kind,
        "state": source.state,
        "health": health(state),
        "held": held(state),
        "supported": found is not None,
        "invalid": invalid,
        "roles": list(source.roles),
        "privacy": source.ref.privacy,
        "keep_days": source.ref.keep_days,
        "quote_max": source.ref.quote_max,
        "consent": source.ref.consent,
        "tools": tools,
        "calls_today": usage_today(state, now),
        "calls_per_day": source.calls_per_day,
        "interval_min": source.interval_min,
        "last_sync_at": last_sync,
        "next_sync_at": (
            int(last_sync) + source.interval_min * 60
            if syncs and isinstance(last_sync, int)
            else None
        ),
        "last_ok_at": state.last_ok_at if state is not None else None,
        "last_error_at": state.last_error_at if state is not None else None,
        "last_error": state.last_error if state is not None else None,
        "failures": state.failures if state is not None else 0,
        "signals_total": state.signals_total if state is not None else 0,
    }


def states(deps: FactoryDeps, factory_id: str) -> dict[str, FactorySourceState]:
    """:returns: Every source's running state by source id (the account salt row left out)."""
    return {
        s.source: s
        for s in deps.factory_store.list_source_states(factory_id)
        if s.source != SALT_ROW
    }


__all__ = [
    "FAIL_LIMIT",
    "HOLD_KEY",
    "INCIDENT_RULE",
    "LAST_SYNC_KEY",
    "PAUSED_FROM_KEY",
    "SEATS",
    "STOPPED",
    "WRITE_LOOKING",
    "Source",
    "charge",
    "health",
    "held",
    "listed",
    "require_kind",
    "require_role",
    "require_running",
    "require_tool",
    "resolve",
    "seat_of",
    "states",
    "status",
    "subject",
    "target",
    "today",
    "usage_today",
]
