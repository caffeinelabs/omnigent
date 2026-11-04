"""READY announcements once per PR head and gate states; repeats stay quiet.

A tracked PR turns ready (``pr.ready``) whenever every gate passes after one
did not. A gate can dip and come back without anything changing for a person
or an agent: a check is re-run, GitHub answers ``mergeable=UNKNOWN`` twice
while it recomputes after a base move, a listing fails once. Announcing every
such turn would mail the PR's lead a READY notice and post the PR to the Room
each time.

So a ready turn, and a ready PR whose head or gate states change, is
classified against the PR's newest ``pr.ready`` event by its
:func:`ready_key` (head sha, the state of every gate, the newest human review
verdict, and whether the server merges it):

- ``new``: no earlier announcement, or the key changed. The ``pr.ready``
  event is a notice (Room card and project line, Slack, the lead's heartbeat
  delta) and the PR's lead gets the READY notice.
- ``repeat``: the same key, announced less than :func:`reminder_s` ago. The
  event is ``info`` and flagged ``repeat``: the Room, Slack DMs, the digest
  counts and agents' heartbeats ignore it, and nobody is mailed.
- ``reminder``: the same key, announced at least :func:`reminder_s` ago
  (``OMNIGENT_FACTORY_READY_REMINDER_S``, 24 h by default). The Room shows it
  again ("still ready"), nobody is mailed and heartbeats ignore it: people are
  reminded by the Slack digest's Waiting on you list, not the agents.

The decision is pure; :mod:`.prs` reads the newest event and emits the next one.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from omnigent.factory_gates import DEMO_GATE, READY_GATES

_logger = logging.getLogger(__name__)

READY_REMINDER_ENV: Final = "OMNIGENT_FACTORY_READY_REMINDER_S"
DEFAULT_READY_REMINDER_S: Final = 24 * 3600.0
"""An unchanged ready PR is announced again (to people, in the Room) after this long."""
MIN_READY_REMINDER_S: Final = 3600.0

NEW: Final = "new"
REPEAT: Final = "repeat"
REMINDER: Final = "reminder"

KEY_FIELD: Final = "ready_key"
ANNOUNCED_AT_FIELD: Final = "announced_at"
REPEAT_FLAG: Final = "repeat"
REMINDER_FLAG: Final = "reminder"


def reminder_s() -> float:
    """
    :returns: ``OMNIGENT_FACTORY_READY_REMINDER_S`` in seconds (at least one
        hour), else :data:`DEFAULT_READY_REMINDER_S`.
    """
    raw = os.environ.get(READY_REMINDER_ENV, "").strip()
    if not raw:
        return DEFAULT_READY_REMINDER_S
    try:
        value = float(raw)
    except ValueError:
        _logger.warning("factory: ignoring invalid %s=%r", READY_REMINDER_ENV, raw)
        return DEFAULT_READY_REMINDER_S
    return max(value, MIN_READY_REMINDER_S)


def gate_states(gates: Mapping[str, Any] | None) -> str:
    """
    :param gates: A PR's gates JSON.
    :returns: Its gate states only, e.g. ``"draft=pass,…,protected_paths=pass"``
        (evidence and notes change on every poll).
    """
    states = gates if isinstance(gates, Mapping) else {}
    return ",".join(
        f"{name}={states.get(name)}"
        for name in (*READY_GATES, DEMO_GATE)
        if states.get(name) is not None
    )


def ready_key(
    head_sha: str | None,
    gates: Mapping[str, Any] | None,
    *,
    auto: bool,
    human_review: Mapping[str, Any] | None = None,
) -> str:
    """
    What a READY announcement says, as one comparable string.

    :param head_sha: The PR's head.
    :param gates: Its gates JSON; only the gate states count (evidence and
        notes change on every poll).
    :param auto: The server merges it (an auto-merge target), not a person.
    :param human_review: Its newest human verdict; a person lifting their own
        change request makes the next ready turn news again.
    :returns: e.g. ``"<sha>|draft=pass,base=pass,…,demo=skip|human|approved@1730…"``.
    """
    merger = "auto" if auto else "human"
    review = ""
    if isinstance(human_review, Mapping) and human_review.get("verdict"):
        review = f"{human_review.get('verdict')}@{human_review.get('at')}"
    return f"{(head_sha or '').lower()}|{gate_states(gates)}|{merger}|{review}"


@dataclass(frozen=True)
class ReadyPlan:
    """
    How one ready turn is announced.

    :param kind: :data:`NEW`, :data:`REPEAT` or :data:`REMINDER`.
    :param announced_at: Epoch seconds of the announcement it belongs to (now
        for a new one or a reminder; the earlier one for a repeat).
    """

    kind: str
    announced_at: int

    @property
    def mails_agents(self) -> bool:
        """:returns: Whether the PR's lead gets the READY notice."""
        return self.kind == NEW

    @property
    def shows_in_room(self) -> bool:
        """:returns: Whether the Room posts it."""
        return self.kind != REPEAT

    def event_data(self, key: str) -> dict[str, Any]:
        """:returns: The fields this plan adds to the ``pr.ready`` event's data."""
        data: dict[str, Any] = {KEY_FIELD: key, ANNOUNCED_AT_FIELD: self.announced_at}
        if self.kind == REPEAT:
            data[REPEAT_FLAG] = True
        elif self.kind == REMINDER:
            data[REMINDER_FLAG] = True
        return data


def plan_ready(
    key: str,
    previous: Mapping[str, Any] | None,
    previous_ts: float | None,
    *,
    now: float,
    reminder: float,
) -> ReadyPlan:
    """
    Classify one ready turn of a PR (pure).

    :param key: Its :func:`ready_key` now.
    :param previous: The data of the PR's newest earlier ``pr.ready`` event,
        or ``None``. An event written before this module (no key) counts as a
        different announcement, so the first turn after an upgrade is announced.
    :param previous_ts: That event's time in epoch seconds.
    :param now: Epoch seconds.
    :param reminder: :func:`reminder_s`.
    :returns: The :class:`ReadyPlan`.
    """
    at = int(now)
    if not isinstance(previous, Mapping) or previous.get(KEY_FIELD) != key:
        return ReadyPlan(NEW, at)
    announced = previous.get(ANNOUNCED_AT_FIELD)
    if not isinstance(announced, int | float) or isinstance(announced, bool):
        announced = previous_ts if previous_ts is not None else now
    if now - float(announced) >= reminder:
        return ReadyPlan(REMINDER, at)
    return ReadyPlan(REPEAT, int(announced))


def is_repeat(data: Mapping[str, Any] | None) -> bool:
    """:returns: Whether an event repeats news its readers already got (nothing to show)."""
    return isinstance(data, Mapping) and data.get(REPEAT_FLAG) is True


def is_reminder(data: Mapping[str, Any] | None) -> bool:
    """:returns: Whether an event is a reminder for people only."""
    return isinstance(data, Mapping) and data.get(REMINDER_FLAG) is True


def quiet_for_agents(data: Mapping[str, Any] | None) -> bool:
    """
    :returns: Whether an event must not wake or brief an agent: a repeat (it
        already had the news) or a reminder (for people, via the Slack digest).
    """
    return is_repeat(data) or is_reminder(data)


__all__ = [
    "ANNOUNCED_AT_FIELD",
    "DEFAULT_READY_REMINDER_S",
    "KEY_FIELD",
    "MIN_READY_REMINDER_S",
    "NEW",
    "READY_REMINDER_ENV",
    "REMINDER",
    "REMINDER_FLAG",
    "REPEAT",
    "REPEAT_FLAG",
    "ReadyPlan",
    "gate_states",
    "is_reminder",
    "is_repeat",
    "plan_ready",
    "quiet_for_agents",
    "ready_key",
    "reminder_s",
]
