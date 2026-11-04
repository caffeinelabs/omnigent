"""Quiet heartbeats: skip a due beat when nothing relevant happened, else say what did.

A due beat is skipped when no event touched the member since its last
delivered beat, none of its sub-agents has been running without progress for
half the stuck threshold, and that beat is younger than the backstop. The
backstop is three intervals, doubled for every backstop beat in a row that
found nothing (an idle factory), up to a day; any relevant event resets it.
A delivered beat carries a short delta built from those events: the newest
few, plus every PR's latest ready or merged news, which a lead that is not the
PR's PL learns only there. Pure functions; the supervisor reads the events and
the clock.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from omnigent.factory import LABEL_PROJECT, LABEL_ROLE, ROLE_LEAD, ROLE_PROJECT_LEAD
from omnigent.server.factory.ready_notices import is_reminder

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
IDLE_BACKSTOP_MAX_ENV: Final = "OMNIGENT_FACTORY_IDLE_BACKSTOP_MAX_S"
DEFAULT_IDLE_BACKSTOP_MAX_S: Final = 86400.0
"""The longest an idle member goes without a beat (never shorter than three intervals)."""

_LEAD_SEVERITIES: Final = frozenset({"notice", "warning", "critical"})
PR_STATE_KINDS: Final = frozenset(
    {"pr.ready", "pr.not_ready", "pr.merged", "pr.auto_merged", "pr.closed"}
)
"""Events that change what a PR is waiting on; the newest per PR is its state."""
PINNED_PR_KINDS: Final = frozenset({"pr.ready", "pr.merged"})
"""A PR whose newest state event is one of these keeps its bullet in every delta."""


def quiet_beats_enabled() -> bool:
    """Quiet beats are on unless ``OMNIGENT_FACTORY_QUIET_BEATS=0``."""
    return os.environ.get(QUIET_BEATS_ENV, "").strip() != "0"


def idle_backstop_max_s() -> float:
    """:returns: ``OMNIGENT_FACTORY_IDLE_BACKSTOP_MAX_S`` (seconds), else one day."""
    raw = os.environ.get(IDLE_BACKSTOP_MAX_ENV, "").strip()
    try:
        value = float(raw) if raw else DEFAULT_IDLE_BACKSTOP_MAX_S
    except ValueError:
        return DEFAULT_IDLE_BACKSTOP_MAX_S
    return value if value > 0 else DEFAULT_IDLE_BACKSTOP_MAX_S


def backstop_s(
    interval: float, idle_streak: int, cap: float = DEFAULT_IDLE_BACKSTOP_MAX_S
) -> float:
    """
    How long after its last beat a member that nothing touched gets one anyway.

    :param interval: The member's heartbeat interval in seconds.
    :param idle_streak: Backstop beats in a row that found nothing.
    :param cap: The most the wait grows to.
    :returns: Three intervals, doubled per idle backstop beat, at most *cap*
        (but never below three intervals).
    """
    base = BACKSTOP_INTERVALS * interval
    if idle_streak <= 0:
        return base
    return max(base, min(base * 2 ** min(idle_streak, 32), cap))


def relevant_events(conv: Conversation, events: Iterable[FactoryEvent]) -> list[FactoryEvent]:
    """
    The events a member's heartbeat should react to.

    An event touches a member when it is to it, concerns a PL's project, or
    (for a lead) is at least a notice. Heartbeat events, still-queued mail,
    anything the member itself did (its posts, label changes, questions) and a
    ``pr.ready`` reminder (for people, see :mod:`.ready_notices`) never count,
    so rewriting its own beacon cannot reset its backoff. A ``pr.ready``
    repeat does count for a PL: it follows the ``pr.not_ready`` that already
    woke it, and its last word on the PR must be "ready".

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
        if event.from_id == conv.id or is_reminder(event.kind, event.data):
            continue
        if (
            event.to_id == conv.id
            or (role == ROLE_PROJECT_LEAD and project and event.project_slug == project)
            or (role == ROLE_LEAD and event.severity in _LEAD_SEVERITIES)
        ):
            out.append(event)
    return out


def _bullet(summary: str) -> str:
    flat = " ".join(summary.replace("•", "·").split())
    return flat if len(flat) <= BULLET_MAX_CHARS else flat[: BULLET_MAX_CHARS - 1] + "…"


def _pr_key(event: FactoryEvent) -> str | None:
    pr_id = (event.data or {}).get("pr_id")
    return event.subject or (str(pr_id) if pr_id else None)


def _kept(events: Sequence[FactoryEvent]) -> list[FactoryEvent]:
    """
    The newest :data:`DELTA_MAX_BULLETS` events, plus each PR's newest state
    event when it says ready or merged, oldest first.
    """
    keep = set(range(max(0, len(events) - DELTA_MAX_BULLETS), len(events)))
    seen: set[str] = set()
    for i in range(len(events) - 1, -1, -1):
        event = events[i]
        key = _pr_key(event) if event.kind in PR_STATE_KINDS else None
        if key is None or key in seen:
            continue
        seen.add(key)
        if event.kind in PINNED_PR_KINDS:
            keep.add(i)
    return [events[i] for i in sorted(keep)]


def delta_text(events: Sequence[FactoryEvent], since_beat: int) -> str | None:
    """
    Summarise what changed since beat *since_beat*.

    :param events: Relevant events, oldest first.
    :param since_beat: Number of the last delivered beat (``0`` = none).
    :returns: e.g. ``"2 changes since #11: • PR #9630 ready • D-3 answered"``
        (the newest :data:`DELTA_MAX_BULLETS`, plus every PR whose newest state
        event is ready or merged, so that news is never dropped), or ``None``
        without events or a prior beat.
    """
    if not events or since_beat < 1:
        return None
    count = len(events)
    noun = "change" if count == 1 else "changes"
    bullets = " ".join(f"• {_bullet(e.summary)}" for e in _kept(events))
    return f"{count} {noun} since #{since_beat}: {bullets}"


@dataclass(frozen=True)
class BeatPlan:
    """
    What to do with a due beat.

    :param skip: Nothing needs the member; write nothing and wait an interval.
    :param delta: The delta line for a delivered beat, if any.
    :param idle_streak: Backstop beats in a row that found nothing, counting
        this one when it is delivered as a backstop (``0`` resets it).
    """

    skip: bool
    delta: str | None = None
    idle_streak: int = 0


def plan_beat(
    *,
    now: float,
    last_beat: float | None,
    interval: float,
    beat_n: int,
    relevant: Sequence[FactoryEvent],
    stalled_child: bool,
    idle_streak: int = 0,
    backstop_max_s: float = DEFAULT_IDLE_BACKSTOP_MAX_S,
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
    :param idle_streak: Backstop beats in a row that found nothing (see :func:`backstop_s`).
    :param backstop_max_s: The longest the backstop grows to.
    :returns: The :class:`BeatPlan`.
    """
    if last_beat is None:
        return BeatPlan(skip=False)
    if relevant or stalled_child:
        return BeatPlan(skip=False, delta=delta_text(relevant, beat_n))
    if now - last_beat < backstop_s(interval, idle_streak, backstop_max_s):
        return BeatPlan(skip=True, idle_streak=idle_streak)
    return BeatPlan(skip=False, idle_streak=idle_streak + 1)


__all__ = [
    "BACKSTOP_INTERVALS",
    "DEFAULT_IDLE_BACKSTOP_MAX_S",
    "DELTA_MAX_BULLETS",
    "EVENT_SCAN_LIMIT",
    "IDLE_BACKSTOP_MAX_ENV",
    "PINNED_PR_KINDS",
    "PR_STATE_KINDS",
    "QUIET_BEATS_ENV",
    "SKIP_EVENT_INTERVAL_S",
    "BeatPlan",
    "backstop_s",
    "delta_text",
    "idle_backstop_max_s",
    "plan_beat",
    "quiet_beats_enabled",
    "relevant_events",
]
