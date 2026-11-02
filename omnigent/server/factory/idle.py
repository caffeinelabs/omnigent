"""Did the factory do anything in a window? The signals idle-aware trials and retros read.

A window is idle when no PR merged, no task (``T-n``) was finished and no
sub-agent run ended (``task.done`` / ``task.failed``) in it. An idle window
says nothing about a change on trial.

A retro learns from more than finished work: incidents and findings, PRs
closed without a merge, answered or escalated decisions, exhausted loops, the
humans' feedback and their posts in the Room. :func:`retro_signals` counts
those; a day is only too quiet for a retro when both are empty.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from omnigent.server.factory.context import FactoryDeps

RUN_END_KINDS: Final = ("task.done", "task.failed")
"""Events the supervisor logs when a sub-agent's run ends."""
_RUN_SCAN_LIMIT: Final = 200
DECISION_KINDS: Final = ("decision.answered", "decision.escalated")
PR_CLOSED_KIND: Final = "pr.closed"
LOOP_EXHAUSTED_KIND: Final = "loop.exhausted"
HUMAN_POST_KINDS: Final = ("chat.posted", "chat.replied")
_SIGNAL_SCAN_LIMIT: Final = 500
_ISSUE_SCAN_LIMIT: Final = 50
RETRO_ISSUE_SOURCES_SKIPPED: Final = frozenset({"sentinel"})
"""Sentinel's own findings come from the last retro; they do not call for the next one."""


@dataclass(frozen=True)
class Activity:
    """What got done in ``[since, until)``."""

    merged_prs: int = 0
    tasks_done: int = 0
    runs_ended: int = 0

    @property
    def idle(self) -> bool:
        """:returns: Nothing merged, finished or ran."""
        return not (self.merged_prs or self.tasks_done or self.runs_ended)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RetroSignals:
    """What a retro can code in ``[since, until)`` besides finished work."""

    issues: int = 0
    """Incidents and findings first seen (Sentinel's own findings excluded)."""
    prs_closed: int = 0
    decisions: int = 0
    """Decisions answered or escalated."""
    loops_exhausted: int = 0
    feedback: int = 0
    """Ratings, tags and label calibrations from humans."""
    human_posts: int = 0

    @property
    def any(self) -> bool:
        """:returns: Something happened that a retro should look at."""
        return any(asdict(self).values())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def window_activity(
    deps: FactoryDeps,
    factory_id: str,
    since: int,
    until: int,
    *,
    merged_prs: int | None = None,
) -> Activity:
    """
    Count the work of *factory_id* in ``[since, until)`` (epoch seconds). Synchronous.

    :param merged_prs: The merged-PR count when the caller already has it
        (e.g. the ``throughput`` metric of the same window).
    """
    store = deps.factory_store

    def inside(ts: float | None) -> bool:
        return ts is not None and since <= ts < until

    if merged_prs is None:
        merged_prs = sum(1 for p in store.list_prs(factory_id, ["merged"]) if inside(p.merged_at))
    tasks_done = store.count_tasks_closed(factory_id, since, until)
    runs = store.find_events(factory_id, list(RUN_END_KINDS), since * 1000, limit=_RUN_SCAN_LIMIT)
    return Activity(
        merged_prs=merged_prs,
        tasks_done=tasks_done,
        runs_ended=sum(1 for e in runs if e.ts < until * 1000),
    )


def retro_signals(deps: FactoryDeps, factory_id: str, since: int, until: int) -> RetroSignals:
    """Count what a retro reads besides work in ``[since, until)`` (epoch seconds). Synchronous."""
    store = deps.factory_store
    issues = [
        i
        for i in store.list_issues(
            factory_id, class_=["incident", "finding"], limit=_ISSUE_SCAN_LIMIT
        )
        if since <= i.first_seen < until and i.source not in RETRO_ISSUE_SOURCES_SKIPPED
    ]
    kinds = [*DECISION_KINDS, PR_CLOSED_KIND, LOOP_EXHAUSTED_KIND, *HUMAN_POST_KINDS]
    found = [
        e
        for e in store.find_events(factory_id, kinds, since * 1000, limit=_SIGNAL_SCAN_LIMIT)
        if e.ts < until * 1000
    ]

    def count(*names: str) -> int:
        return sum(1 for e in found if e.kind in names)

    feedback = store.list_feedback(factory_id, since=since, limit=_SIGNAL_SCAN_LIMIT)
    return RetroSignals(
        issues=len(issues),
        prs_closed=count(PR_CLOSED_KIND),
        decisions=count(*DECISION_KINDS),
        loops_exhausted=count(LOOP_EXHAUSTED_KIND),
        feedback=sum(1 for f in feedback if f.author_kind == "human" and f.created_at < until),
        human_posts=sum(
            1
            for e in found
            if e.kind in HUMAN_POST_KINDS and (e.data or {}).get("by_kind") == "human"
        ),
    )


__all__ = ["RUN_END_KINDS", "Activity", "RetroSignals", "retro_signals", "window_activity"]
