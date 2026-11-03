"""Shared setup for the readout tests: an applied bet with its plan frozen into a project.

Builds on :mod:`tests.server.factory.discovery_helpers`: the factory has
discovery on, a sample profile and goal, and O-1 backed by five accounts from
two source kinds before the release. A signal trend counts the window after
the release only once its sources were read after it (:func:`read_sources`)
and its signals sorted. Every product word is sample data.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from omnigent.entities.factory import EventDraft, FactoryProject, FactoryProposal
from omnigent.entities.factory_discovery import FactoryReadout, FactorySignal
from omnigent.server.factory import bets
from omnigent.server.factory.discovery import readouts, sources
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.discovery_helpers import DAY, ingest, label, make_opportunity

REPO = "sample-org/sample-app"
PLAN: dict[str, Any] = {
    "kind": "outcome",
    "goal": "G-1",
    "hypothesis": "People show a draft before it goes live, so fewer ask for it (sample).",
    "outcome": "Share a private draft link (sample)",
    "metric": "signal_trend:O-1",
    "expected": {"direction": "down", "low": -60.0, "high": -20.0, "unit": "%"},
    "guardrails": ["support load (sample)"],
    "flag": {"key": "draft_share"},
    "measurement": {"method": "signal_trend", "readout_days": [7, 28]},
    "kill": {"state": "fewer than 20 % fewer asks by day 28 (sample)", "date": "day 28"},
}


def now() -> int:
    return int(time.time())


def seeded(cockpit: Cockpit, *, before_days: float = 10) -> list[FactorySignal]:
    """O-1 with five accounts from two source kinds, *before_days* old (sample)."""
    signals = ingest(
        cockpit,
        "repo-issues",
        [f"I want to show a draft first {i} (sample)" for i in range(3)],
        age_days=before_days,
    )
    signals += ingest(
        cockpit,
        "web",
        [f"I publish and unpublish to show a draft {i} (sample)" for i in range(2)],
        age_days=before_days,
    )
    label(cockpit, signals, "workaround")
    make_opportunity(cockpit, signals)
    return signals


def later_signals(
    cockpit: Cockpit, n: int, *, age_days: float, source: str = "repo-issues", tag: str = "after"
) -> list[FactorySignal]:
    """*n* more accounts' signals on O-1, *age_days* old (sample)."""
    signals = ingest(
        cockpit,
        source,
        [f"Still no private draft link {tag} {i} (sample)" for i in range(n)],
        accounts=[f"{tag}-{source}-{i}" for i in range(n)],
        age_days=age_days,
    )
    for signal in signals:
        cockpit.store.put_link(
            cockpit.fid, signal.number, 1, attached_by="scout (sample)", attached_kind="agent"
        )
    return signals


def file_bet(
    cockpit: Cockpit,
    *,
    plan: dict[str, Any] | None = None,
    opportunity: str | None = "O-1",
    slugs: Sequence[str] = ("draft-share",),
    stage: str = "merged",
    on_behalf_of: str | None = None,
    applied_at: int | None = None,
) -> tuple[FactoryProposal, list[FactoryProject]]:
    """An applied bet on *opportunity* whose plan is frozen into the projects *slugs*."""
    store = cockpit.store
    row, _ = store.create_proposal(
        cockpit.fid,
        kind="projects",
        title="create draft-share (sample)",
        author_kind="agent",
        author=cockpit.world.id("lead_a"),
        status="applied",
        opportunity=opportunity,
        bet={**PLAN, **(plan or {})},
        applied_at=applied_at if applied_at is not None else now() - 40 * DAY,
        on_behalf_of=on_behalf_of,
    )
    why = bets.frozen_why(row, by="alice (sample)", at=now() - 39 * DAY)
    projects = [
        store.upsert_project(
            cockpit.fid,
            slug,
            title=f"{slug} (sample)",
            repo=REPO,
            charter={"goal": "Share a draft privately (sample)", "why": why},
            stage=stage,
        )
        for slug in slugs
    ]
    return row, projects


def read_sources(
    cockpit: Cockpit,
    at: int,
    ids: Sequence[str] = ("repo-issues", "web"),
    *,
    outcome: str = "ok",
) -> None:
    """The server read sources *ids* at *at* (one ``source.sync`` row each, as the gateway)."""
    for source_id in ids:
        cockpit.store.append_event(
            EventDraft(
                factory_id=cockpit.fid,
                kind="source.sync",
                summary=f"the server read {source_id} (sample)",
                subject=sources.subject(source_id),
                data={"source": source_id, "mode": "sync", "outcome": outcome},
                ts=at * 1000,
            )
        )


def bet_of(cockpit: Cockpit, number: int = 1) -> readouts.Bet:
    found = [b for b in readouts.bets(cockpit.deps, cockpit.fid) if b.proposal.number == number]
    assert found, f"no bet C-{number}"
    return found[0]


def release(cockpit: Cockpit, released_at: int, number: int = 1) -> list[FactoryReadout]:
    """Schedule C-*number*'s readouts from *released_at* (as a detected release would)."""
    return readouts.schedule(
        cockpit.deps, cockpit.fid, bet_of(cockpit, number), released_at, how="merged"
    )


def readout(cockpit: Cockpit, day: int, number: int = 1) -> FactoryReadout:
    rows = cockpit.store.list_readouts(cockpit.fid, proposal=number)
    found = [r for r in rows if r.day == day]
    assert found, f"no readout C-{number} · day {day}"
    return found[0]


def base(cockpit: Cockpit, suffix: str = "") -> str:
    return f"/v1/factory/factories/{cockpit.fid}{suffix}"


__all__ = [
    "PLAN",
    "REPO",
    "base",
    "bet_of",
    "file_bet",
    "later_signals",
    "now",
    "read_sources",
    "readout",
    "release",
    "seeded",
]
