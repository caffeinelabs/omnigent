"""Did the factory do any work in a window? The signal idle-aware trials and retros share.

A window is idle when no PR merged, no task (``T-n``) was finished and no
sub-agent run ended (``task.done`` / ``task.failed``) in it. An idle window
says nothing about a change on trial, and gives a retro nothing to learn from.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from omnigent.server.factory.context import FactoryDeps

RUN_END_KINDS: Final = ("task.done", "task.failed")
"""Events the supervisor logs when a sub-agent's run ends."""
_RUN_SCAN_LIMIT: Final = 200


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
    tasks_done = sum(
        1 for t in store.list_tasks(factory_id, states=["done"]) if inside(t.closed_at)
    )
    runs = store.find_events(factory_id, list(RUN_END_KINDS), since * 1000, limit=_RUN_SCAN_LIMIT)
    return Activity(
        merged_prs=merged_prs,
        tasks_done=tasks_done,
        runs_ended=sum(1 for e in runs if e.ts < until * 1000),
    )


__all__ = ["RUN_END_KINDS", "Activity", "window_activity"]
