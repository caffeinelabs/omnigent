"""Quiet heartbeats: skip a due beat when nothing relevant happened, else say what did.

A due beat is skipped when no event touched the member since its last
delivered beat, none of its sub-agents has been running without progress for
half the stuck threshold, and that beat is less than three intervals old (the
backstop). A delivered beat carries a short delta built from those events.
Pure functions; the supervisor reads the events and the clock.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from omnigent.factory import LABEL_PROJECT, LABEL_ROLE, ROLE_LEAD, ROLE_PROJECT_LEAD

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactoryEvent

QUIET_BEATS_ENV: Final = "OMNIGENT_FACTORY_QUIET_BEATS"
BACKSTOP_INTERVALS: Final = 3
DELTA_MAX_BULLETS: Final = 5
BULLET_MAX_CHARS: Final = 120
EVENT_SCAN_LIMIT: Final = 500
"""Newest factory events a due beat looks through."""
SKIP_EVENT_INTERVAL_S: Final = 3600.0
"""Skipped beats are reported as one ``heartbeat.skipped`` event per factory per hour."""

_LEAD_SEVERITIES: Final = frozenset({"notice", "warning", "critical"})


def quiet_beats_enabled() -> bool:
    """Quiet beats are on unless ``OMNIGENT_FACTORY_QUIET_BEATS=0``."""
    return os.environ.get(QUIET_BEATS_ENV, "").strip() != "0"


def relevant_events(conv: Conversation, events: Iterable[FactoryEvent]) -> list[FactoryEvent]:
    """
    The events a member's heartbeat should react to.

    An event touches a member when it is from or to it, concerns a PL's
    project, or (for a lead) is at least a notice. Heartbeat events, a
    member's own posts and still-queued mail never count.

    :param conv: A top-level member.
    :param events: Factory events since its last beat, oldest first.
    :returns: The relevant ones, oldest first.
    """
    role = conv.labels.get(LABEL_ROLE)
    project = conv.labels.get(LABEL_PROJECT)
    out: list[FactoryEvent] = []
    for event in events:
        if event.kind.startswith("heartbeat.") or event.kind == "message.queued":
            continue
        if event.from_id == conv.id and event.kind.startswith("message."):
            continue
        if (
            conv.id in (event.from_id, event.to_id)
            or (role == ROLE_PROJECT_LEAD and project and event.project_slug == project)
            or (role == ROLE_LEAD and event.severity in _LEAD_SEVERITIES)
        ):
            out.append(event)
    return out


def _bullet(summary: str) -> str:
    flat = " ".join(summary.replace("•", "·").split())
    return flat if len(flat) <= BULLET_MAX_CHARS else flat[: BULLET_MAX_CHARS - 1] + "…"


def delta_text(events: Sequence[FactoryEvent], since_beat: int) -> str | None:
    """
    Summarise what changed since beat *since_beat*.

    :param events: Relevant events, oldest first.
    :param since_beat: Number of the last delivered beat (``0`` = none).
    :returns: e.g. ``"2 changes since #11: • PR #9630 ready • D-3 answered"``
        (the newest :data:`DELTA_MAX_BULLETS`), or ``None`` without events or a prior beat.
    """
    if not events or since_beat < 1:
        return None
    count = len(events)
    noun = "change" if count == 1 else "changes"
    bullets = " ".join(f"• {_bullet(e.summary)}" for e in events[-DELTA_MAX_BULLETS:])
    return f"{count} {noun} since #{since_beat}: {bullets}"


@dataclass(frozen=True)
class BeatPlan:
    """
    What to do with a due beat.

    :param skip: Nothing needs the member; write nothing and wait an interval.
    :param delta: The delta line for a delivered beat, if any.
    """

    skip: bool
    delta: str | None = None


def plan_beat(
    *,
    now: float,
    last_beat: float | None,
    interval: float,
    beat_n: int,
    relevant: Sequence[FactoryEvent],
    stalled_child: bool,
) -> BeatPlan:
    """
    Decide whether a due beat is delivered.

    :param now: Epoch seconds.
    :param last_beat: When the last beat was delivered, or ``None`` (never: deliver).
    :param interval: The member's heartbeat interval in seconds.
    :param beat_n: Number of the last delivered beat.
    :param relevant: Events since *last_beat* that touch the member.
    :param stalled_child: A sub-agent is running with no progress for half the
        stuck threshold.
    :returns: The :class:`BeatPlan`.
    """
    if last_beat is None:
        return BeatPlan(skip=False)
    backstop = now - last_beat >= BACKSTOP_INTERVALS * interval
    if not relevant and not stalled_child and not backstop:
        return BeatPlan(skip=True)
    return BeatPlan(skip=False, delta=delta_text(relevant, beat_n))


__all__ = [
    "BACKSTOP_INTERVALS",
    "DELTA_MAX_BULLETS",
    "EVENT_SCAN_LIMIT",
    "QUIET_BEATS_ENV",
    "SKIP_EVENT_INTERVAL_S",
    "BeatPlan",
    "delta_text",
    "plan_beat",
    "quiet_beats_enabled",
    "relevant_events",
]
