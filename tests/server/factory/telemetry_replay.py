"""Replay the live-run fixture event log through the real factory code paths.

Not a test module. ``fixtures/live_run_2026_10_26.json`` holds the
2026-10-26 run of caffeinelabs/app#9630 and #9631 as steps: factory events
(``emit``), GitHub changes (``pr``, applied to :class:`FakeGitHub`), PR
poller passes (``poll``) and a decision answer (``answer``). The poller runs
the real ``prs.apply_refresh`` (and its span hook), events go through the
real ``events.emit`` (and the span recorder), and every store timestamp
follows one fake clock.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from omnigent.entities.factory import FactorySpan
from omnigent.server.factory import events, live, nudge, status
from omnigent.server.factory.background.pr_poller import PrPoller
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.mailer import MAILER_STATE_ATTR, Mailer
from omnigent.server.factory.telemetry import cost, recorder, spans
from omnigent.stores import factory_store as factory_store_module
from omnigent.stores.factory_store import FactoryStore
from tests.server.factory.conftest import FactoryWorld, FakeClock, FakeDeliver
from tests.server.factory.github_fakes import (
    FakeCheck,
    FakeConnection,
    FakeGitHub,
    FakeGithubStore,
    FakePr,
    iso,
)

FIXTURE = Path(__file__).parent / "fixtures" / "live_run_2026_10_26.json"


def load_fixture() -> dict[str, Any]:
    """:returns: The parsed live-run fixture."""
    return json.loads(FIXTURE.read_text())


@dataclass
class Replay:
    """The seeded world, a fake GitHub and the poller, driven by the fixture."""

    world: FactoryWorld
    gh: FakeGitHub
    poller: PrPoller
    clock: FakeClock
    mailer: Mailer
    fixture: dict[str, Any]
    start: float
    """Fixture t=0 (epoch seconds), just after the factory root was created."""
    prs: dict[int, FakePr] = field(default_factory=dict)
    decisions: dict[str, str] = field(default_factory=dict)

    @property
    def deps(self) -> FactoryDeps:
        """The deps the app (and so the poller and routes) uses."""
        return factory_deps(self.world.app_state)

    @property
    def store(self) -> FactoryStore:
        return self.deps.factory_store

    @property
    def fid(self) -> str:
        return self.world.id("lead_a")

    def ms(self, seconds: float) -> int:
        """:returns: Epoch ms of fixture time *seconds*."""
        return int((self.start + seconds) * 1000)

    def s(self, seconds: float) -> int:
        """:returns: Epoch seconds of fixture time *seconds*."""
        return int(self.start + seconds)

    def head(self, key: str) -> str:
        """:returns: The full sha of fixture head *key*."""
        return self.fixture["heads"][key]

    def pr_id(self, number: int) -> str:
        row = self.store.get_pr_by_number(self.fid, self.fixture["repo"], number)
        assert row is not None, number
        return row.id

    def spans(self, kind: str | None = None, **filters: Any) -> list[FactorySpan]:
        """Stored spans of *kind* (all kinds when ``None``), oldest first."""
        return self.store.list_spans(
            self.fid, kinds=[kind] if kind else None, limit=10_000, **filters
        )

    async def run(self, until: float | None = None) -> None:
        """Play every step (up to fixture time *until*)."""
        for step in self.fixture["steps"]:
            if until is not None and step["at"] > until:
                break
            await self.step(step)

    async def step(self, step: dict[str, Any]) -> None:
        self.clock.now = self.start + step["at"]
        if "emit" in step:
            await self._emit(step["emit"], step["at"])
        elif "pr" in step:
            self._github(step["pr"], step["at"])
        elif "answer" in step:
            await self._answer(step["answer"], step["at"])
        elif step.get("poll"):
            await self.poller.run_once()
            await self.mailer.drain()

    def _sid(self, key: str | None) -> str | None:
        return self.world.id(key) if key else None

    async def _emit(self, spec: dict[str, Any], at: float) -> None:
        data = dict(spec.get("data") or {})
        subject = spec.get("subject")
        if "decision" in spec:
            row, _ = self.store.create_decision(
                self.fid,
                dedupe_key=f"replay:{at}",
                question=spec["decision"]["question"],
                project_slug=spec.get("project"),
                asked_by_session_id=self._sid(spec.get("from")),
            )
            self.decisions[row.ref] = row.id
            data.update(decision_id=row.id, number=row.number)
            subject = f"decision:{row.ref}"
        await events.emit(
            self.world.app_state,
            self.fid,
            spec["kind"],
            project=spec.get("project"),
            from_id=self._sid(spec.get("from")),
            to_id=self._sid(spec.get("to")),
            subject=subject or (f"session:{self._sid(spec['to'])}" if spec.get("to") else None),
            summary=spec["summary"],
            data=data,
            ts=self.ms(at),
        )

    async def _answer(self, spec: dict[str, Any], at: float) -> None:
        decision_id = self.decisions[spec["ref"]]
        self.store.answer_decision(decision_id, option_id=None, text=spec["label"], by=spec["by"])
        await events.emit(
            self.world.app_state,
            self.fid,
            "decision.answered",
            subject=f"decision:{spec['ref']}",
            summary=f"{spec['by']} answered {spec['ref']}",
            data={"by": spec["by"], "label": spec["label"], "text": "ship #9631 first please"},
            ts=self.ms(at),
        )

    def _github(self, spec: dict[str, Any], at: float) -> None:
        number = spec["number"]
        now = self.start + at
        checks = [_check(name, state, now) for name, state in spec.get("checks") or []]
        if spec["op"] == "open":
            head = self.head(spec["head"])
            pr = FakePr(self.fixture["repo"], number, spec["branch"], head, now)
            pr.commits.append((head, iso(self.start + spec["commit_at"])))
            pr.checks = checks
            self.prs[number] = self.gh.add(pr)
            return
        pr = self.prs[number]
        if spec["op"] == "checks":
            pr.checks = checks
        elif spec["op"] == "push":
            pr.push(self.head(spec["head"]), self.start + spec["commit_at"], checks=checks)
        elif spec["op"] == "review":
            sha = self.head(spec["head"])
            pr.comment(f"<!-- factory:review sha={sha} verdict={spec['verdict']} -->", now, "rv")
        elif spec["op"] == "verify":
            sha = self.head(spec["head"])
            pr.comment(f"<!-- factory:verify sha={sha} result={spec['result']} env=x -->", now)
        elif spec["op"] == "demo":
            sha = self.head(spec["head"])
            pr.comment(f"<!-- factory:demo sha={sha} status={spec['status']} -->", now)
        elif spec["op"] == "merge":
            pr.state = "MERGED"
            pr.merged_by = spec["by"]
            pr.merge_sha = "f" * 40
            pr.merged_at = pr.closed_at = now


def _check(name: str, state: str, at: float) -> FakeCheck:
    if state in ("IN_PROGRESS", "QUEUED"):
        return FakeCheck(name, status=state, at=iso(at))
    return FakeCheck(name, conclusion=state, at=iso(at))


def make_replay(
    world: FactoryWorld,
    deliver: FakeDeliver,
    no_sleep: Callable[[float], Any],
    monkeypatch: pytest.MonkeyPatch,
    *,
    record: bool = True,
) -> Replay:
    """
    Seed the fixture's projects on *world* and wire the poller, mailer and clock.

    :param record: Install the span recorder (heal backfill tests turn it and
        the refresh hook off to start from events only).
    """
    fixture = load_fixture()
    start = float(int(time.time()) + 10)
    clock = FakeClock(start)
    monkeypatch.setattr(factory_store_module, "now_ms", lambda: int(clock.now * 1000))
    monkeypatch.setattr(factory_store_module, "now_s", lambda: int(clock.now))
    monkeypatch.setattr(live, "announce_factory_changed", lambda *_a, **_k: None)
    status.reset()
    nudge.reset()
    spans.reset()
    recorder.reset()
    cost.reset()
    store = factory_deps(world.app_state).factory_store
    fid = world.id("lead_a")
    for project in fixture["projects"]:
        store.upsert_project(
            fid,
            project["slug"],
            title=project["title"],
            repo=fixture["repo"],
            base_branch="main",
            charter=project["charter"],
            pl_session_id=world.id(project["pl"]) if project["pl"] else None,
        )
    gh = FakeGitHub()
    state = world.app_state
    state.github_client = gh.client()
    # The factory owner's GitHub connection: ``local`` without auth, ``alice`` with it.
    state.github_store = FakeGithubStore({"local": FakeConnection(), "alice": FakeConnection()})
    mailer = Mailer(state, deliver=deliver, sleep=no_sleep)
    setattr(state, MAILER_STATE_ATTR, mailer)
    if record:
        monkeypatch.setattr(events, "_listeners", [*events._listeners])
        spans.install()
    return Replay(
        world=world,
        gh=gh,
        poller=PrPoller(state, clock=clock, poll_s=60.0),
        clock=clock,
        mailer=mailer,
        fixture=fixture,
        start=start,
    )


def span_table(spans_: list[FactorySpan]) -> list[tuple[Any, ...]]:
    """Every span as a comparable tuple (heal idempotency)."""
    return sorted(
        (
            s.dedupe_key,
            s.kind,
            s.name,
            s.started_at,
            s.ended_at,
            s.outcome,
            s.parent_id,
            s.head_sha,
            s.cost_usd,
            json.dumps(s.attrs, sort_keys=True),
            s.id,
        )
        for s in spans_
    )


__all__ = ["Replay", "load_fixture", "make_replay", "span_table"]
