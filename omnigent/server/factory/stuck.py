"""Whether a member is stuck, and why, from the open incidents the detectors raise.

A member is stuck while an open (or acknowledged) ``agent.quiet`` or
``agent.repeat`` incident targets it: the supervisor's quiet detection and the
activity poller raise those, and they resolve themselves once the agent moves
again. The reason is one plain clause built from the incident's evidence,
e.g. ``"same failing Bash call 3 times"``.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from typing import Any, Final

from omnigent.entities.factory import FactoryIssue
from omnigent.stores.factory_store import norm_id

STUCK_RULES: Final = ("agent.repeat", "agent.quiet")
"""Incident rules that mark their member stuck (repeat first: it says more)."""
_MEMBER_PREFIX: Final = "member:"
_OPEN: Final = frozenset({"open", "acked"})


def _member_id(target: str) -> str | None:
    if not target.startswith(_MEMBER_PREFIX):
        return None
    try:
        return norm_id(target[len(_MEMBER_PREFIX) :])
    except (ValueError, TypeError):
        return None


def stuck_issues(issues: Iterable[FactoryIssue]) -> dict[str, FactoryIssue]:
    """
    The incident that makes each member stuck.

    :param issues: Incidents of one factory (any status; only open and acked count).
    :returns: Bare session id → its stuck incident (``agent.repeat`` wins, then the oldest).
    """
    found: dict[str, FactoryIssue] = {}
    for issue in issues:
        if issue.rule not in STUCK_RULES or issue.status not in _OPEN:
            continue
        sid = _member_id(issue.target)
        if sid is None:
            continue
        held = found.get(sid)
        rank = (STUCK_RULES.index(issue.rule), issue.first_seen)
        if held is None or rank < (STUCK_RULES.index(held.rule), held.first_seen):
            found[sid] = issue
    return found


def _minutes(seconds: float) -> int:
    return max(1, round(seconds / 60))


def stuck_reason(issue: FactoryIssue, now: float | None = None) -> str:
    """
    One clause saying why a member is stuck, built from the incident's facts.

    :param issue: An ``agent.repeat`` or ``agent.quiet`` incident.
    :param now: Epoch seconds (defaults to the clock).
    :returns: e.g. ``"same failing Bash call 3 times"`` or
        ``"running with no new activity for 14 min"``.
    """
    evidence: Mapping[str, Any] = issue.evidence if isinstance(issue.evidence, dict) else {}
    if issue.rule == "agent.repeat":
        tool = evidence.get("tool")
        count = evidence.get("count")
        what = f"{tool} call" if isinstance(tool, str) and tool else "call"
        times = f" {count} times" if isinstance(count, int) and count > 1 else " again and again"
        return f"same failing {what}{times}"
    idle = evidence.get("idle_s")
    clock = time.time() if now is None else now
    seconds = float(idle) if isinstance(idle, int | float) else max(0.0, clock - issue.first_seen)
    return f"running with no new activity for {_minutes(seconds)} min"


def stuck_fields(issue: FactoryIssue | None, now: float | None = None) -> dict[str, Any]:
    """:returns: ``{stuck, stuck_reason, stuck_since, stuck_issue}`` of one member."""
    if issue is None:
        return {"stuck": False, "stuck_reason": None, "stuck_since": None, "stuck_issue": None}
    return {
        "stuck": True,
        "stuck_reason": stuck_reason(issue, now),
        "stuck_since": issue.first_seen or None,
        "stuck_issue": issue.ref,
    }


__all__ = ["STUCK_RULES", "stuck_fields", "stuck_issues", "stuck_reason"]
