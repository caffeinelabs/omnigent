"""
Alarms the server closed by itself, each with its cause (the factory tree's Health).

A stall, claim or done-check alarm (a ``loop`` or ``done_check`` decision) that
the server answered or expired is noise the factory handled: its node closed
(the task finished, the PR merged, the project shipped), it ran again, or its
task was held on purpose all along. :mod:`.server_decisions` records the cause
on the closing event (``cause``, ``real_ask``, ``about``, ``was``);
:func:`read` turns those events into :class:`ClosedAlarm` rows, newest first,
one per decision. Older events without those keys still read: the cause comes
from the event's reason or summary, what it asked and its project from the row.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from omnigent.server.factory import node_refs
from omnigent.stores.factory_store import norm_id

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryDecision, FactoryEvent
    from omnigent.stores.factory_store import FactoryStore

ALARM_SOURCES: Final = frozenset({"loop", "done_check"})
"""Decision sources that are the factory's own alarms (stall, claim, done check)."""
WINDOW_S: Final = 24 * 3600
"""How far back closed alarms are listed (Health shows today's)."""
KINDS: Final = ("decision.answered", "decision.expired")
_EVENTS_MAX: Final = 500
_WAS_MAX: Final = 200


@dataclass(frozen=True)
class ClosedAlarm:
    """
    One alarm the server closed.

    :param ref: The decision, e.g. ``D-48``.
    :param was: What it asked, e.g. ``T-16: implement 1/3 stalled - what next?``.
    :param cause: Why it closed, e.g. ``T-16 is held on purpose for C-16; …``.
    :param at: When it closed (epoch seconds).
    :param real_ask: The ask a person owes instead, e.g. ``D-45``.
    :param about: The node it was about (a ref: ``T-16``, a project slug).
    :param project: The project it was about, for viewer filtering.
    """

    ref: str
    was: str
    cause: str
    at: int
    real_ask: str | None = None
    about: str | None = None
    project: str | None = None

    def to_json(self) -> dict[str, Any]:
        """:returns: The ``closed_alarms`` item (``{id, was, cause, real_ask?, ref?}``)."""
        out: dict[str, Any] = {"id": self.ref, "was": self.was, "cause": self.cause}
        if self.real_ask:
            out["real_ask"] = self.real_ask
        if self.about:
            out["ref"] = self.about
        return out


def _from_server(event: FactoryEvent) -> bool:
    data = event.data or {}
    if event.kind == "decision.expired":
        return True
    return data.get("by_kind") == "server"


def _ref_of(event: FactoryEvent) -> str | None:
    parsed = node_refs.parse(node_refs.normalize(event.subject) or "")
    if parsed is not None and parsed.kind == "decision":
        return parsed.text
    head = (event.summary or "").split(" ", 1)[0]
    parsed = node_refs.parse(head)
    return parsed.text if parsed is not None and parsed.kind == "decision" else None


def _cause_of(event: FactoryEvent) -> str:
    data = event.data or {}
    for key in ("cause", "reason"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    _ref, _sep, tail = (event.summary or "").partition(": ")
    return tail.strip() or "it no longer applied"


def read(
    events: Iterable[FactoryEvent],
    rows: Mapping[str, FactoryDecision] | None = None,
) -> list[ClosedAlarm]:
    """
    The alarms the server closed, newest first, one per decision.

    :param events: ``decision.answered`` and ``decision.expired`` events (any order).
    :param rows: Decision id (bare hex) -> its row, for events that predate the
        recorded cause (what it asked, its project).
    """
    found: dict[str, ClosedAlarm] = {}
    for event in sorted(events, key=lambda e: e.ts, reverse=True):
        data = event.data or {}
        if event.kind not in KINDS or data.get("source") not in ALARM_SOURCES:
            continue
        if not _from_server(event):
            continue
        ref = _ref_of(event)
        if ref is None or ref in found:
            continue
        row = None
        if rows is not None and isinstance(data.get("decision_id"), str):
            try:
                row = rows.get(norm_id(data["decision_id"]))
            except ValueError:
                row = None
        was = str(data.get("was") or (row.question if row is not None else "") or ref)
        about = data.get("about")
        about_ref = node_refs.normalize(about) if isinstance(about, str) and about else None
        project = event.project_slug or (row.project_slug if row is not None else None)
        found[ref] = ClosedAlarm(
            ref=ref,
            was=" ".join(was.split())[:_WAS_MAX],
            cause=_cause_of(event),
            at=int(event.ts // 1000),
            real_ask=str(data["real_ask"]) if data.get("real_ask") else None,
            about=about_ref or project or None,
            project=project or None,
        )
    return list(found.values())


def load(store: FactoryStore, factory_id: str, now: float) -> list[ClosedAlarm]:
    """The alarms the server closed in the last :data:`WINDOW_S` (synchronous)."""
    events = store.find_events(
        factory_id,
        list(KINDS),
        int((now - WINDOW_S) * 1000),
        newest_first=True,
        limit=_EVENTS_MAX,
    )
    events = [e for e in events if (e.data or {}).get("source") in ALARM_SOURCES]
    if not events:
        return []
    rows: dict[str, FactoryDecision] | None = None
    if any(not (e.data or {}).get("was") for e in events):
        listed = store.list_decisions(
            factory_id=factory_id, status=["answered", "expired"], limit=_EVENTS_MAX
        )
        rows = {norm_id(d.id): d for d in listed}
    return read(events, rows)


def visible(alarms: Sequence[ClosedAlarm], hidden: Iterable[str]) -> list[ClosedAlarm]:
    """:returns: The alarms about projects a viewer may read."""
    gone = set(hidden)
    return [a for a in alarms if not (a.project and a.project in gone)]


__all__ = ["ALARM_SOURCES", "KINDS", "WINDOW_S", "ClosedAlarm", "load", "read", "visible"]
