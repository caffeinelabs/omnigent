"""The discovery beats: Scout's sweep and synthesis, Atlas's ready-to-plan, Sentinel's retro.

An idle sweep is skipped; a seat's first sweep and synthesis come early; the
day's budget posts one line at 80 % and stops the beats (and queues asks) at
100 %; ready-to-plan goes to Atlas after a synthesis or when capacity frees
up, with the validated opportunities, their bet notes, the goals without a
metric, the mix and how many bets fit; the discovery retro goes to Sentinel
monthly when something happened. Every product word is sample data.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.entities.factory import EventDraft
from omnigent.server.factory import definition
from omnigent.server.factory.background import discovery_beats as beats
from omnigent.server.factory.definition_blocks import DISCOVERY_ENV
from omnigent.server.factory.discovery import ledger
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.discovery_helpers import (
    base,
    discovery_cockpit,
    ingest,
    make_opportunity,
)
from tests.server.factory.source_helpers import add_scout

HOUR = 3600
DAY = 86400
T0 = int(time.time())
"""The tests' clock: beats run at ``T0 + offset`` and stamp their events then."""


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in discovery_cockpit(world, db_uri, monkeypatch):
        yield value


@pytest.fixture()
def scout(cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch) -> str:
    return add_scout(cockpit, monkeypatch)


async def _beat(cockpit: Cockpit, after_s: int = 0) -> list[str]:
    root = cockpit.world.get("lead_a")
    return await beats.beat_factory(cockpit.app.state, root, now=T0 + after_s)


def _ingest_at(cockpit: Cockpit, after_s: int, texts: list[str]) -> None:
    """Store sample signals as the ledger would at ``T0 + after_s``."""
    ref = ledger.source_ref(cockpit.deps, cockpit.fid, "repo-issues")
    assert ref is not None
    items = [
        ledger.Item(text=t, pointer=f"https://example.org/late/{after_s}/{i}", account=f"late-{i}")
        for i, t in enumerate(texts)
    ]
    ledger.ingest(cockpit.deps, cockpit.fid, ref, items, now=T0 + after_s)


def _texts(cockpit: Cockpit, session_id: str, head: str) -> list[str]:
    return [t for t in cockpit.mailer.texts_to(session_id) if t.startswith(head)]


def _spend(cockpit: Cockpit, usd: float, now: float | None = None) -> None:
    cockpit.store.add_cost_daily(
        cockpit.fid,
        project_slug="",
        role="scout",
        agent_name="factory-scout",
        model="sonnet",
        day_utc=time.strftime("%Y-%m-%d", time.gmtime(now or time.time())),
        cost_usd=usd,
    )


def _set(cockpit: Cockpit, values: dict[str, Any]) -> None:
    definition.set_values(cockpit.deps, cockpit.fid, values, created_by="alice")
    definition.invalidate(cockpit.fid)


# ── Scout's beats ──────────────────────────────────────────────


async def test_an_idle_sweep_is_skipped(cockpit: Cockpit, scout: str) -> None:
    assert await _beat(cockpit, 3 * HOUR) == []
    assert cockpit.mailer.texts_to(scout) == []
    assert cockpit.events(beats.KIND_SWEEP) == []


async def test_new_signals_bring_the_sweep_and_the_first_synthesis(
    cockpit: Cockpit, scout: str
) -> None:
    ingest(cockpit, "repo-issues", ["Exports time out (sample)", "Drafts get lost (sample)"])

    # Not before the seat's first sweep is due (2 h after it was seated).
    assert await _beat(cockpit, HOUR) == []
    assert await _beat(cockpit, 3 * HOUR) == ["sweep"]
    [sweep] = _texts(cockpit, scout, "[Factory discovery · sweep")
    assert sweep.startswith("[Factory discovery · sweep · 2 new signals since the start")
    assert 'load_skill("discovery-sweep")' in sweep
    assert "Exports time out" not in sweep  # beats carry counts, never signal text
    # The sweep just went: nothing more for now, and the synthesis waits a day.
    assert await _beat(cockpit, 3 * HOUR) == []
    sent = await _beat(cockpit, 25 * HOUR)
    assert sent == ["synthesis"]
    [synthesis] = _texts(cockpit, scout, "[Factory discovery · synthesis")
    assert "2 new signals · 2 unsorted" in synthesis
    assert 'load_skill("discovery-synthesis")' in synthesis


async def test_the_daily_sweep_needs_new_signals(cockpit: Cockpit, scout: str) -> None:
    ingest(cockpit, "repo-issues", ["Exports time out (sample)"])
    assert await _beat(cockpit, 3 * HOUR) == ["sweep"]
    # A day later with nothing new: skipped (but the synthesis comes).
    assert await _beat(cockpit, 27 * HOUR) == ["synthesis"]
    _ingest_at(cockpit, 28 * HOUR, ["Exports fail on mobile (sample)"])
    assert await _beat(cockpit, 28 * HOUR) == ["sweep"]
    [_first, second] = _texts(cockpit, scout, "[Factory discovery · sweep")
    assert second.startswith("[Factory discovery · sweep · 1 new signals since ")


async def test_a_paused_scout_gets_no_beats(cockpit: Cockpit, scout: str) -> None:
    ingest(cockpit, "repo-issues", ["Exports time out (sample)"])
    cockpit.world.store.set_labels(scout, {F.LABEL_STATE: F.STATE_PAUSED})

    assert await _beat(cockpit, 30 * HOUR) == []


async def test_beats_stop_when_discovery_is_off(
    cockpit: Cockpit, scout: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ingest(cockpit, "repo-issues", ["Exports time out (sample)"])
    _set(cockpit, {"discovery.enabled": False})
    assert await _beat(cockpit, 30 * HOUR) == []
    monkeypatch.delenv(DISCOVERY_ENV)
    assert await beats.run_once(cockpit.app.state) == {}
    assert cockpit.mailer.texts_to(scout) == []


# ── Budget ─────────────────────────────────────────────────────


async def test_the_budget_line_then_the_stop_and_the_queued_ask(
    cockpit: Cockpit, scout: str
) -> None:
    signals = ingest(cockpit, "repo-issues", ["Exports time out (sample)"])
    row = make_opportunity(cockpit, signals)
    _spend(cockpit, 8.5)

    assert await _beat(cockpit) == []
    [line] = cockpit.store.list_chat(cockpit.fid, "discovery", limit=10)
    assert line.author_kind == "system" and "$8.50 of its $10" in line.body
    assert await _beat(cockpit) == []  # once a day
    assert len(cockpit.events(beats.KIND_BUDGET_LINE)) == 1

    _spend(cockpit, 2.0)
    assert await _beat(cockpit, 3 * HOUR) == ["budget_stop"]
    # The Room does not wake Scout either: Atlas answers in #discovery until tomorrow.
    posted = await cockpit.post(
        base(cockpit, "/chat"), {"thread": "discovery", "body": "Who exports? (sample)"}
    )
    assert posted.status_code == 201, posted.text
    assert posted.json()["routed"] == ["Atlas"]
    assert "Scout reached today's budget; Atlas will answer" in [
        m.body for m in cockpit.store.list_chat(cockpit.fid, "discovery", limit=20)
    ]
    assert _texts(cockpit, scout, "[Factory discovery · sweep") == []  # stopped
    [stop] = cockpit.events(beats.KIND_BUDGET_STOP)
    assert stop.data["cap_usd"] == 10.0
    # An ask made now waits for Scout instead of waking it.
    asked = await cockpit.post(
        base(cockpit, f"/opportunities/{row.ref}/research"), {"question": "Who exports? (sample)"}
    )
    assert asked.status_code == 200, asked.text
    assert asked.json()["research"]["state"] == "queued"
    assert _texts(cockpit, scout, "[Factory discovery · research") == []

    # An owner raises the budget: the ask goes out with the next pass.
    _set(cockpit, {"discovery.budgets.usd_per_day": 50.0})
    sent = await _beat(cockpit, 3 * HOUR)
    assert sent[0] == "asks"
    [ask] = _texts(cockpit, scout, "[Factory discovery · research")
    assert ask.startswith(f"[Factory discovery · research · {row.ref}]")
    found = cockpit.store.get_opportunity(cockpit.fid, row.number)
    assert found is not None and found.research["state"] == "asked"


async def test_a_zero_budget_means_scout_spends_nothing(cockpit: Cockpit, scout: str) -> None:
    signals = ingest(cockpit, "repo-issues", ["Exports time out (sample)"])
    row = make_opportunity(cockpit, signals)
    _set(cockpit, {"discovery.budgets.usd_per_day": 0})

    assert await _beat(cockpit, 30 * HOUR) == ["budget_stop"]
    assert cockpit.mailer.texts_to(scout) == []  # no sweep, no synthesis
    asked = await cockpit.post(
        base(cockpit, f"/opportunities/{row.ref}/research"), {"question": "Who exports? (sample)"}
    )
    assert asked.status_code == 200, asked.text
    assert asked.json()["research"]["state"] == "queued"
    assert "asks" not in await _beat(cockpit, 31 * HOUR)
    assert _texts(cockpit, scout, "[Factory discovery · research") == []


def test_a_readout_wakes_scout_only_within_its_budget(cockpit: Cockpit, scout: str) -> None:
    from omnigent.server.factory.discovery import readouts

    assert readouts._scout(cockpit.deps, cockpit.fid) == scout
    _spend(cockpit, 10.0)
    assert readouts._scout(cockpit.deps, cockpit.fid) is None


def test_the_budget_cap_reads_the_definition() -> None:
    assert beats.budget_cap({}) == 10.0  # the starter's default
    assert beats.budget_cap({"discovery": {"budgets": {"usd_per_day": 0}}}) == 0.0
    assert beats.budget_cap({"discovery": {"budgets": {"usd_per_day": 25}}}) == 25.0


# ── Ready to plan (Atlas) ──────────────────────────────────────


def _validated(cockpit: Cockpit) -> Any:
    signals = ingest(
        cockpit, "repo-issues", [f"I need to show a draft first {i} (sample)" for i in range(3)]
    ) + ingest(cockpit, "web", [f"Wish I could share drafts {i} (sample)" for i in range(3)])
    return make_opportunity(
        cockpit,
        signals,
        bet_note={
            "solutions": [
                "A private link (sample)",
                "Invite by email (sample)",
                "A password (sample)",
            ],
            "cheapest_test": "A fake door on publish (sample)",
        },
    )


async def test_ready_to_plan_follows_a_synthesis_with_what_atlas_needs(
    cockpit: Cockpit, scout: str
) -> None:
    row = _validated(cockpit)
    atlas = cockpit.fid

    sent = await _beat(cockpit, 25 * HOUR)
    assert "synthesis" in sent and "ready_to_plan" not in sent  # Scout's pass lands first
    assert await _beat(cockpit, 28 * HOUR) == ["ready_to_plan"]

    [notice] = _texts(cockpit, atlas, "[Factory discovery · ready to plan")
    head = notice.splitlines()[0]
    assert head == "[Factory discovery · ready to plan · 1 validated · capacity 9 of 10]"
    assert "Plan at most 3 bet(s) now." in notice
    assert f"Validated, pinned first then by rank: {row.ref}." in notice
    # Scout's title and bet note reach Atlas as untrusted text, under a server line.
    assert "[Factory source data · opportunities · titles and bet notes written by Scout" in notice
    lines = notice.splitlines()
    server_line = next(line for line in lines if line.startswith(f"{row.ref} · #1 · moderate"))
    at = lines.index(server_line)
    assert lines[at + 1] == "> Show a draft before it goes live (sample)"
    assert lines[at + 2].startswith("> bet note: solutions: A private link (sample)")
    assert "cheapest test: A fake door on publish (sample)" in lines[at + 2]
    assert "Goals with no metric" in notice and "- G-1 " in notice
    assert "Mix (target → now): outcome 60% → 0%" in notice
    assert 'sys_factory_project(op="decline"' in notice
    assert "I need to show a draft" not in notice  # no signal text
    [event] = cockpit.events(beats.KIND_READY)
    assert event.data["validated"] == [row.ref] and event.data["why"] == "synthesis"
    # Nothing new: no second notice.
    assert await _beat(cockpit, 29 * HOUR) == []


def _finish(cockpit: Cockpit, slug: str, after_s: int) -> None:
    """*slug*'s work reaches done (it keeps its row but frees its slot), as the server records."""
    project = cockpit.store.get_project(cockpit.fid, slug)
    assert project is not None
    cockpit.store.update_project(project.id, stage=F.STAGE_DONE)
    cockpit.store.append_event(
        EventDraft(
            factory_id=cockpit.fid,
            kind="stage.changed",
            summary=f"{slug}: ready → done (sample)",
            subject=f"project:{slug}",
            data={"from": "ready", "to": "done"},
            ts=(T0 + after_s) * 1000,
        )
    )


async def test_freed_capacity_brings_ready_to_plan_again(cockpit: Cockpit, scout: str) -> None:
    _validated(cockpit)
    await _beat(cockpit, 25 * HOUR)
    assert await _beat(cockpit, 28 * HOUR) == ["ready_to_plan"]
    # Two projects consolidated is no freed capacity.
    cockpit.store.append_event(
        EventDraft(
            factory_id=cockpit.fid,
            kind="project.merged",
            summary="two projects consolidated (sample)",
            subject="project:api-version",
            ts=(T0 + 28 * HOUR + 10) * 1000,
        )
    )
    assert await _beat(cockpit, 28 * HOUR + 20) == []
    _finish(cockpit, "api-version", 28 * HOUR + 30)

    assert await _beat(cockpit, 28 * HOUR + 60) == ["ready_to_plan"]
    assert cockpit.events(beats.KIND_READY)[-1].data["why"] == "capacity"


async def test_finished_work_holds_no_slot(cockpit: Cockpit, scout: str) -> None:
    """A project whose work is done keeps status active, but it frees its cap slot."""
    _validated(cockpit)
    _set(cockpit, {"caps.max_projects": 1})
    for project in cockpit.store.list_projects(cockpit.fid):
        _finish(cockpit, project.slug, 0)
    assert {p.status for p in cockpit.store.list_projects(cockpit.fid)} == {"active"}

    await _beat(cockpit, 25 * HOUR)
    assert await _beat(cockpit, 28 * HOUR) == ["ready_to_plan"]
    [notice] = _texts(cockpit, cockpit.fid, "[Factory discovery · ready to plan")
    assert notice.splitlines()[0].endswith("· capacity 1 of 1]")
    assert "Plan at most 1 bet(s) now." in notice


async def test_no_room_for_a_bet_means_no_notice(cockpit: Cockpit, scout: str) -> None:
    _validated(cockpit)
    _set(cockpit, {"discovery.thresholds.bets_per_week": 0})

    await _beat(cockpit, 25 * HOUR)
    assert await _beat(cockpit, 28 * HOUR) == []
    assert _texts(cockpit, cockpit.fid, "[Factory discovery · ready to plan") == []
    # Recorded once: later passes wait for the next trigger.
    [skipped] = cockpit.events(beats.KIND_READY)
    assert skipped.data["skipped"] is True
    assert await _beat(cockpit, 29 * HOUR) == []
    assert len(cockpit.events(beats.KIND_READY)) == 1


def test_ready_items_put_pinned_first_and_skip_recent_declines() -> None:
    from types import SimpleNamespace

    def item(number: int, rank: float, *, status: str = "validated", **row: Any) -> Any:
        fields = {"number": number, "pinned_by": None, "declines": [], **row}
        return SimpleNamespace(
            status=status,
            row=SimpleNamespace(**fields),
            score=SimpleNamespace(rank=rank, quadrant="test_first"),
        )

    now = 1_800_000_000
    items = [
        item(1, 0.5),
        item(2, 0.1, pinned_by="alice"),
        item(3, 0.9, declines=[{"at": now - 3600, "reason": "too costly (sample)"}]),
        item(4, 0.3, status="idea"),
    ]

    validated, tests = beats.ready_items(items, now)

    assert [e.row.number for e in validated] == [2, 1]
    assert [e.row.number for e in tests] == [4]


# ── The discovery retro (Sentinel) ─────────────────────────────


async def test_the_monthly_discovery_retro_goes_to_sentinel(cockpit: Cockpit, scout: str) -> None:
    sentinel = cockpit.world.id("lead_b")
    month = 31 * DAY
    # Nothing happened: skipped.
    assert "retro" not in await _beat(cockpit, month)
    cockpit.store.append_event(
        EventDraft(
            factory_id=cockpit.fid,
            kind="opportunity.killed",
            summary="alice killed O-1 (sample)",
            subject="opportunity:O-1",
            ts=(T0 + month - DAY) * 1000,
        )
    )

    assert "retro" in await _beat(cockpit, month)

    [retro] = _texts(cockpit, sentinel, "[Factory discovery · retro")
    assert retro.startswith("[Factory discovery · retro · window 30d · budget $3]")
    assert 'load_skill("discovery-retro")' in retro
    assert "retro" not in await _beat(cockpit, month + DAY)
