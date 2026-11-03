"""Discovery beats: the server wakes Scout, offers Atlas what is ready, asks Sentinel for a retro.

Every :data:`TICK_S` seconds, while the server runs discovery
(``OMNIGENT_FACTORY_DISCOVERY``), each open factory with ``discovery.enabled``:

- **Budget.** Scout's spend today (the cost ledger, role ``scout``) against
  ``discovery.budgets.usd_per_day``: at :data:`BUDGET_LINE_SHARE` one line in
  ``#discovery``; at 100 % one ``discovery.budget_stop`` and no more beats or
  asks for Scout until the next UTC day (asks wait, queued). A budget of $0
  means Scout spends nothing: no beats and no asks at all.
- **Sweep** (``discovery.cadence.sweep_h``, default daily): ``[Factory discovery ·
  sweep · …]`` to Scout, only when signals arrived since the last one (an idle
  sweep is skipped, like the retro's idle skip).
- **Synthesis** (``discovery.cadence.synthesis_h``, default weekly): ``[Factory
  discovery · synthesis · …]`` to Scout, skipped while the factory has no signal.
  A seat's first sweep and synthesis come early (:data:`FIRST_SWEEP_S`,
  :data:`FIRST_SYNTHESIS_S` after it is seated).
- **Asks**: research asks people made while Scout was off or over budget
  (``research.state = queued``) go to Scout once it can take them.
- **Ready to plan**: ``[Factory discovery · ready to plan · …]`` to Atlas when
  capacity frees up (a project's work reached merged or done, or it was
  cancelled, so it no longer takes a slot under ``caps.max_projects``) or a
  while after a synthesis, with the top validated opportunities (pinned first)
  and their bet notes, the goals without a metric, the mix gap and the free
  capacity, at most ``thresholds.bets_per_week`` minus the bets filed in the
  last 7 days. Scout's titles and notes reach Atlas inside the untrusted
  envelope: they paraphrase outside text.
- **Discovery retro**: ``[Factory discovery · retro · window 30d · …]`` to
  Sentinel every :data:`RETRO_EVERY_S`, skipped when nothing happened.

Each beat is a ``discovery.*`` event, so the cadence survives a restart.
Signal text never goes into a beat: it carries ids, counts and Scout's titles.
One pass reads the seat and the day's spend once per factory.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import (
    LABEL_STATE,
    STAGE_DONE,
    STAGE_MERGED,
    STATE_ACTIVE,
    THREAD_DISCOVERY,
)
from omnigent.server.factory import control, definition, events
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps, factory_deps
from omnigent.server.factory.definition_blocks import discovery_server_on
from omnigent.server.factory.envelope import UntrustedItem, format_untrusted
from omnigent.server.factory.projects import cap_slots, holds_cap_slot
from omnigent.stores.factory_store import now_s

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.server.factory.discovery.opportunities import Evaluated

_logger = logging.getLogger(__name__)

TICK_S: Final = 60.0
SUBJECT: Final = "discovery:beats"
KIND_SWEEP: Final = "discovery.sweep"
KIND_SYNTHESIS: Final = "discovery.synthesis"
KIND_READY: Final = "discovery.ready_to_plan"
KIND_RETRO: Final = "discovery.retro"
KIND_BUDGET_LINE: Final = "discovery.budget_line"
KIND_BUDGET_STOP: Final = "discovery.budget_stop"
KIND_ASK: Final = "discovery.ask_sent"
KIND_RESEARCH: Final = "opportunity.research"
"""What a person's "Research this" (or its cancel) records; a queued ask is one of these."""
FREED_EVENTS: Final = ("stage.changed", "project.cancelled")
BUDGET_LINE_SHARE: Final = 0.8
FIRST_SWEEP_S: Final = 2 * 3600
FIRST_SYNTHESIS_S: Final = 24 * 3600
SYNTHESIS_SETTLE_S: Final = 2 * 3600
"""A synthesis's ready-to-plan notice waits this long, so Scout's pass lands first."""
READY_MIN_GAP_S: Final = 6 * 3600
READY_ITEMS_MAX: Final = 5
TESTS_MAX: Final = 2
DECLINE_QUIET_S: Final = 14 * 86400
"""An opportunity Atlas declined is not offered again for this long."""
RETRO_EVERY_S: Final = 30 * 86400
RETRO_BUDGET_USD: Final = 3.0
WEEK_S: Final = 7 * 86400
MIX_KEYS: Final = {
    "outcome": "outcome",
    "test": "tests",
    "enabler": "enablers",
    "hunch": "hunches",
}
"""A bet's kind → the strategy mix share it fills."""


# ── Reading the facts ──────────────────────────────────────────


def _day(now: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(now))


def _last(deps: FactoryDeps, fid: str, kind: str, subject: str | None = SUBJECT) -> Any:
    found = deps.factory_store.find_events(fid, kind, 0, subject, limit=1, newest_first=True)
    return found[0] if found else None


def _last_s(deps: FactoryDeps, fid: str, kind: str, subject: str = SUBJECT) -> int | None:
    event = _last(deps, fid, kind, subject)
    return event.ts // 1000 if event is not None else None


def _seated_at(deps: FactoryDeps, fid: str, scout: Conversation) -> int:
    """:returns: When the seat (re)started: its last spawn or wake, else its creation."""
    from omnigent.server.factory.discovery.seat import EVENT_SEAT
    from omnigent.server.factory.discovery.seat import SUBJECT as SEAT_SUBJECT

    rows = deps.factory_store.find_events(
        fid, EVENT_SEAT, 0, SEAT_SUBJECT, limit=20, newest_first=True
    )
    for event in rows:
        if (event.data or {}).get("state") in ("spawned", "revived"):
            return event.ts // 1000
    return int(scout.created_at)


def _due(last: int | None, every_h: int, seated: int, first_s: int, now: int) -> bool:
    if every_h <= 0:
        return False
    if last is None or last < seated:
        return now - seated >= min(first_s, every_h * 3600)
    return now - last >= every_h * 3600


def _cadence(doc: Mapping[str, Any]) -> tuple[int, int]:
    cadence = (doc.get("discovery") or {}).get("cadence") or {}
    return int(cadence.get("sweep_h", 24) or 0), int(cadence.get("synthesis_h", 168) or 0)


# ── Budget ─────────────────────────────────────────────────────


def budget_cap(doc: Mapping[str, Any]) -> float:
    """:returns: ``discovery.budgets.usd_per_day`` (default when unset; ``0``: spend nothing)."""
    from omnigent.server.factory.discovery.seat import daily_cap

    return daily_cap(((doc.get("discovery") or {}).get("budgets") or {}).get("usd_per_day"))


async def _budget(
    app_state: Any, root: Conversation, doc: Mapping[str, Any], now: int, spent: float
) -> bool:
    """Post the 80 % line and the stop once a day. :returns: Whether Scout is stopped today."""
    from omnigent.server.factory import chat

    deps = factory_deps(app_state)
    fid = root.id
    cap = budget_cap(doc)
    if cap <= 0:
        # The owners gave Scout nothing to spend: it is never woken.
        return True
    day = _day(now)
    subject = f"{SUBJECT}:{day}"
    if (
        spent >= cap * BUDGET_LINE_SHARE
        and await asyncio.to_thread(_last, deps, fid, KIND_BUDGET_LINE, subject) is None
    ):
        text = (
            f"Scout has spent ${spent:.2f} of its ${cap:g} for today "
            f"({int(spent / cap * 100)} %); it stops at the cap until tomorrow (UTC)."
        )
        await chat.post_system(app_state, fid, THREAD_DISCOVERY, text)
        await events.emit(
            app_state,
            fid,
            KIND_BUDGET_LINE,
            subject=subject,
            summary=text,
            data={"spent_usd": spent, "cap_usd": cap, "day": day},
            ts=now * 1000,
        )
    if spent < cap:
        return False
    if await asyncio.to_thread(_last, deps, fid, KIND_BUDGET_STOP, subject) is None:
        text = (
            f"Scout reached its ${cap:g} for today: the server wakes it again tomorrow "
            "(UTC). Asks made meanwhile wait in the queue; an owner can raise the budget."
        )
        await chat.post_system(app_state, fid, THREAD_DISCOVERY, text)
        await events.emit(
            app_state,
            fid,
            KIND_BUDGET_STOP,
            severity="notice",
            subject=subject,
            summary=text,
            data={"spent_usd": spent, "cap_usd": cap, "day": day},
            ts=now * 1000,
        )
    return True


# ── Scout's beats ──────────────────────────────────────────────


def sweep_text(new: int, since: int | None, spent: float, cap: float) -> str:
    """:returns: ``[Factory discovery · sweep · <n> new signals · …]`` and what to do."""
    when = time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(since)) if since else "the start"
    return (
        f"[Factory discovery · sweep · {new} new signals since {when} · "
        f"budget ${max(0.0, cap - spent):.2f} of ${cap:g} left today] "
        'Run load_skill("discovery-sweep") and follow it: label the new signals, attach '
        "each to an opportunity or leave it unsorted, flag odd batches. No digest."
    )


def synthesis_text(signals: int, unsorted: int, spent: float, cap: float) -> str:
    """:returns: ``[Factory discovery · synthesis · …]`` and what to do."""
    return (
        f"[Factory discovery · synthesis · window 7d · {signals} new signals · "
        f"{unsorted} unsorted · budget ${max(0.0, cap - spent):.2f} of ${cap:g} left today] "
        'Run load_skill("discovery-synthesis") and follow it: merge, split and create '
        "opportunities in one batch, draft bet notes for the validated ones, then post the "
        "digest in #discovery."
    )


def _research_due(deps: FactoryDeps, fid: str) -> list[Any]:
    """
    :returns: The opportunities with a queued research ask. Read only when a person
        asked since the last pass that passed asks on (a ``discovery.ask_sent``).
    """
    store = deps.factory_store
    last = _last(deps, fid, KIND_ASK, None)
    since = last.ts if last is not None else 0
    newest: dict[str | None, Any] = {}
    for event in store.find_events(fid, KIND_RESEARCH, since, None, limit=200, newest_first=True):
        newest.setdefault(event.subject, event)
    if not any((e.data or {}).get("state") in (None, "queued") for e in newest.values()):
        return []
    return [
        row
        for row in store.list_opportunities(fid)
        if isinstance(row.research, Mapping) and row.research.get("state") == "queued"
    ]


async def _asks(app_state: Any, root: Conversation, scout: Conversation, now: int) -> int:
    from omnigent.server.factory.discovery import levers

    deps = factory_deps(app_state)
    fid = root.id
    rows = await asyncio.to_thread(_research_due, deps, fid)
    for row in rows:
        research = dict(row.research or {})
        control.send_text(
            app_state,
            scout.id,
            levers._research_text(
                row.ref,
                str(research.get("by_name") or "someone"),
                str(research.get("question") or ""),
                float(research.get("budget_usd") or 0),
                research.get("due"),
            ),
            kind="request",
        )
        research["state"] = "asked"
        research["asked_at"] = now
        await asyncio.to_thread(
            deps.factory_store.update_opportunity, fid, row.number, research=research
        )
        await events.emit(
            app_state,
            fid,
            KIND_ASK,
            to_id=scout.id,
            subject=f"opportunity:{row.ref}",
            summary=f"the server passed the waiting research ask on {row.ref} to Scout",
            data={"opportunity": row.ref},
            ts=now * 1000,
        )
    return len(rows)


async def _scout_beats(
    app_state: Any,
    root: Conversation,
    scout: Conversation,
    doc: Mapping[str, Any],
    now: int,
    spent: float,
) -> list[str]:
    from omnigent.server.factory.discovery.ledger import SUSPECT_AT
    from omnigent.stores.factory_store.discovery import SignalFilter, is_unsorted

    deps = factory_deps(app_state)
    fid = root.id
    sent: list[str] = []
    if await _asks(app_state, root, scout, now):
        sent.append("asks")
    sweep_h, synthesis_h = _cadence(doc)
    seated = await asyncio.to_thread(_seated_at, deps, fid, scout)
    cap = budget_cap(doc)
    last_sweep = await asyncio.to_thread(_last_s, deps, fid, KIND_SWEEP)
    if _due(last_sweep, sweep_h, seated, FIRST_SWEEP_S, now):
        since = last_sweep if last_sweep is not None and last_sweep >= seated else None
        new = await asyncio.to_thread(deps.factory_store.count_signals, fid, since=since)
        if new:
            control.send_text(
                app_state, scout.id, sweep_text(new, since, spent, cap), kind="request"
            )
            await events.emit(
                app_state,
                fid,
                KIND_SWEEP,
                to_id=scout.id,
                subject=SUBJECT,
                summary=f"the server asked Scout for its sweep ({new} new signals)",
                data={"new": new, "since": since},
                ts=now * 1000,
            )
            sent.append("sweep")
    last_synthesis = await asyncio.to_thread(_last_s, deps, fid, KIND_SYNTHESIS)
    if _due(last_synthesis, synthesis_h, seated, FIRST_SYNTHESIS_S, now):
        total = await asyncio.to_thread(deps.factory_store.count_signals, fid)
        if total:
            since = now - WEEK_S

            def counts() -> tuple[int, int]:
                store = deps.factory_store
                recent = store.list_signals(fid, SignalFilter(since=since), limit=2000)
                linked = {
                    link.signal
                    for link in store.list_links(fid, signals=[r.number for r in recent])
                }
                waiting = [
                    r for r in recent if r.number not in linked and is_unsorted(r, SUSPECT_AT)
                ]
                return len(recent), len(waiting)

            recent, unsorted = await asyncio.to_thread(counts)
            control.send_text(
                app_state, scout.id, synthesis_text(recent, unsorted, spent, cap), kind="request"
            )
            await events.emit(
                app_state,
                fid,
                KIND_SYNTHESIS,
                to_id=scout.id,
                subject=SUBJECT,
                summary=f"the server asked Scout for its weekly synthesis ({recent} signals)",
                data={"signals": recent, "unsorted": unsorted},
                ts=now * 1000,
            )
            sent.append("synthesis")
    return sent


# ── Ready to plan (Atlas) ──────────────────────────────────────


def _mix(deps: FactoryDeps, fid: str, doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    """:returns: Each mix share's target and its share of active projects with a bet now."""
    target = (doc.get("strategy") or {}).get("mix") or {}
    counts = dict.fromkeys(MIX_KEYS.values(), 0)
    active = 0
    for project in deps.factory_store.list_projects(fid):
        if not holds_cap_slot(project):
            continue
        why = (project.charter or {}).get("why")
        key = MIX_KEYS.get(str(why.get("kind"))) if isinstance(why, Mapping) else None
        if key is None:
            continue
        counts[key] += 1
        active += 1
    return [
        {
            "share": key,
            "target": int(target.get(key, 0) or 0),
            "now": round(counts[key] / active * 100) if active else 0,
            "gap": int(target.get(key, 0) or 0)
            - (round(counts[key] / active * 100) if active else 0),
        }
        for key in MIX_KEYS.values()
    ]


def _bets_filed(deps: FactoryDeps, fid: str, now: int) -> tuple[int, int]:
    """:returns: ``(bets filed in the last 7 days, bets open now)``."""
    rows = deps.factory_store.list_proposals(fid, kind="projects", limit=500, author_kind="agent")
    with_bet = [r for r in rows if r.bet is not None]
    week = sum(1 for r in with_bet if r.created_at >= now - WEEK_S)
    open_now = sum(1 for r in with_bet if r.status in ("pending", "awaiting_cosign"))
    return week, open_now


def _declined_lately(e: Evaluated, now: int) -> bool:
    return any(
        isinstance(d, Mapping) and int(d.get("at") or 0) >= now - DECLINE_QUIET_S
        for d in e.row.declines
    )


def ready_items(items: Sequence[Evaluated], now: int) -> tuple[list[Evaluated], list[Evaluated]]:
    """:returns: ``(validated, test first)``: what Atlas may plan, pinned first, then by rank."""
    order = sorted(
        items, key=lambda e: (e.row.pinned_by is None, -round(e.score.rank, 4), e.row.number)
    )
    validated = [e for e in order if e.status == "validated" and not _declined_lately(e, now)]
    tests = [
        e
        for e in order
        if e.status == "idea" and e.score.quadrant == "test_first" and not _declined_lately(e, now)
    ]
    return validated, tests


def _note_line(note: Any) -> str | None:
    if not isinstance(note, Mapping):
        return None
    parts = []
    solutions = [str(s) for s in note.get("solutions") or []][:4]
    if solutions:
        parts.append("solutions: " + "; ".join(control.clip(s, 100) for s in solutions))
    if note.get("riskiest_assumption"):
        parts.append(f"riskiest: {control.clip(str(note['riskiest_assumption']), 140)}")
    if note.get("cheapest_test"):
        parts.append(f"cheapest test: {control.clip(str(note['cheapest_test']), 140)}")
    return " · ".join(parts) or None


def ready_text(
    *,
    validated: Sequence[Evaluated],
    tests: Sequence[Evaluated],
    unmeasured: Sequence[Mapping[str, Any]],
    mix: Sequence[Mapping[str, Any]],
    free: int,
    slots: int,
    offer: int,
    week: int,
    per_week: int,
    open_now: int,
    max_open: int,
) -> str:
    """:returns: Atlas's ``[Factory discovery · ready to plan · …]`` notice."""
    head = (
        f"[Factory discovery · ready to plan · {len(validated)} validated · "
        f"capacity {free} of {slots}]"
    )
    lines = [
        f"Plan at most {offer} bet(s) now. Bets filed this week: {week}/{per_week}; "
        f"waiting for a person: {open_now}/{max_open}.",
    ]
    gaps = sorted(mix, key=lambda m: -int(m["gap"]))
    if mix:
        lines.append(
            "Mix (target → now): "
            + " · ".join(f"{m['share']} {m['target']}% → {m['now']}%" for m in mix)
            + (f". Largest gap: {gaps[0]['share']}." if gaps and gaps[0]["gap"] > 0 else ".")
        )
    if unmeasured:
        lines.append("Goals with no metric (an enabler bet measures one):")
        lines += [
            f"- {g.get('id')} {control.clip(str(g.get('statement') or ''), 140)}"
            for g in unmeasured
        ]
    items: list[UntrustedItem] = []
    if validated:
        lines.append(
            "Validated, pinned first then by rank: "
            + ", ".join(e.row.ref for e in validated[:READY_ITEMS_MAX])
            + "."
        )
        items += [
            _ready_item(e, f"#{i}", e.score.rank, pinned=bool(e.row.pinned_by))
            for i, e in enumerate(validated[:READY_ITEMS_MAX], 1)
        ]
    if tests:
        lines.append(
            "Test first (high value, low confidence: only a test bet fits): "
            + ", ".join(e.row.ref for e in tests[:TESTS_MAX])
            + "."
        )
        items += [_ready_item(e, "test first") for e in tests[:TESTS_MAX]]
    body = (
        [
            format_untrusted(
                origin="opportunities · titles and bet notes written by Scout", items=items
            )
        ]
        if items
        else []
    )
    tail = (
        'Run load_skill("factory-planning") and follow "From an opportunity": file each bet '
        'with sys_factory_project(opportunity="O-<n>", bet={…}), or decline one with '
        'sys_factory_project(op="decline", opportunity="O-<n>", reason="…"). Read an '
        'opportunity with sys_discovery_read(kind="opportunity", ids=["O-<n>"]).'
    )
    return "\n".join([head, *lines, *body, tail])


def _ready_item(
    e: Evaluated, place: str, rank: float | None = None, *, pinned: bool = False
) -> UntrustedItem:
    """One opportunity in the notice: server-written tags; Scout's title and note as text."""
    tags = [place, e.meter.level]
    if rank is not None:
        tags.append(f"rank {rank:.2f}")
    tags.append(f"goal {e.row.goal or 'unassigned'}")
    if pinned:
        tags.append("pinned")
    note = _note_line(e.row.bet_note)
    text = control.clip(e.row.title, 120) + (f"\nbet note: {note}" if note else "")
    return UntrustedItem(ref=e.row.ref, text=text, tags=tuple(tags))


def _ready_facts(deps: FactoryDeps, fid: str, doc: Mapping[str, Any], now: int) -> dict[str, Any]:
    from omnigent.server.factory.discovery import opportunities

    facts = opportunities.load_facts(deps, fid, now=now)
    items = opportunities.evaluate_all(facts, doc)
    validated, tests = ready_items(items, now)
    strategy = doc.get("strategy") or {}
    unmeasured = [
        g for g in strategy.get("goals") or [] if isinstance(g, Mapping) and not g.get("metric")
    ]
    thresholds = (doc.get("discovery") or {}).get("thresholds") or {}
    slots = int((doc.get("caps") or {}).get("max_projects") or 0)
    # Finished work (merged or done) keeps its row but no longer takes a slot.
    active = cap_slots(deps.factory_store.list_projects(fid))
    week, open_now = _bets_filed(deps, fid, now)
    per_week = int(thresholds.get("bets_per_week", 3) or 0)
    max_open = int(thresholds.get("max_open_bets", 3) or 0)
    free = max(0, slots - active) if slots else 0
    offer = max(0, min(free, per_week - week, max_open - open_now))
    return {
        "validated": validated,
        "tests": tests,
        "unmeasured": unmeasured,
        "mix": _mix(deps, fid, doc),
        "free": free,
        "slots": slots,
        "active": active,
        "offer": offer,
        "week": week,
        "per_week": per_week,
        "open_now": open_now,
        "max_open": max_open,
    }


def _capacity_freed(deps: FactoryDeps, fid: str, since: int, active: int, last: Any) -> bool:
    """Whether a slot freed since *since*: work reached merged or done, or a project ended."""
    rows = deps.factory_store.find_events(fid, list(FREED_EVENTS), since * 1000)
    ended = any(
        e.kind == "project.cancelled" or (e.data or {}).get("to") in (STAGE_MERGED, STAGE_DONE)
        for e in rows
    )
    was = (last.data or {}).get("active") if last is not None else None
    return ended or (isinstance(was, int) and active < was)


async def _ready_to_plan(
    app_state: Any, root: Conversation, doc: Mapping[str, Any], now: int
) -> bool:
    deps = factory_deps(app_state)
    fid = root.id
    last = await asyncio.to_thread(_last, deps, fid, KIND_READY)
    last_at = last.ts // 1000 if last is not None else 0
    synthesis = await asyncio.to_thread(_last_s, deps, fid, KIND_SYNTHESIS)
    after_synthesis = (
        synthesis is not None and synthesis > last_at and now - synthesis >= SYNTHESIS_SETTLE_S
    )

    def active() -> int:
        return cap_slots(deps.factory_store.list_projects(fid))

    freed = last is not None and await asyncio.to_thread(
        lambda: _capacity_freed(deps, fid, last_at, active(), last)
    )
    if not (after_synthesis or freed):
        return False
    if last is not None and now - last_at < READY_MIN_GAP_S and not freed:
        return False
    # Only a due notice reads the opportunities (every minute would re-score them all).
    facts = await asyncio.to_thread(_ready_facts, deps, fid, doc, now)
    if facts["offer"] <= 0 or not (facts["validated"] or facts["tests"] or facts["unmeasured"]):
        # Recorded once, so the next pass waits for the next trigger instead of re-reading.
        await events.emit(
            app_state,
            fid,
            KIND_READY,
            subject=SUBJECT,
            summary="nothing to offer Atlas yet (no room for a bet, or nothing ready)",
            data={"skipped": True, "offer": facts["offer"], "active": facts["active"]},
            ts=now * 1000,
        )
        return False
    text = ready_text(
        validated=facts["validated"],
        tests=facts["tests"],
        unmeasured=facts["unmeasured"],
        mix=facts["mix"],
        free=facts["free"],
        slots=facts["slots"],
        offer=facts["offer"],
        week=facts["week"],
        per_week=facts["per_week"],
        open_now=facts["open_now"],
        max_open=facts["max_open"],
    )
    control.send_text(app_state, root.id, text, kind="request")
    await events.emit(
        app_state,
        fid,
        KIND_READY,
        to_id=root.id,
        subject=SUBJECT,
        summary=(
            f"the server offered Atlas {len(facts['validated'])} validated opportunities "
            f"(capacity {facts['free']})"
        ),
        data={
            "validated": [e.row.ref for e in facts["validated"][:READY_ITEMS_MAX]],
            "tests": [e.row.ref for e in facts["tests"][:TESTS_MAX]],
            "unmeasured": [str(g.get("id")) for g in facts["unmeasured"]],
            "offer": facts["offer"],
            "free": facts["free"],
            "active": facts["active"],
            "why": "capacity" if freed else "synthesis",
        },
        ts=now * 1000,
    )
    return True


# ── The discovery retro (Sentinel) ─────────────────────────────


def retro_text(window_days: int, budget_usd: float) -> str:
    """:returns: Sentinel's ``[Factory discovery · retro · window 30d · …]`` beat."""
    return (
        f"[Factory discovery · retro · window {window_days}d · budget ${budget_usd:g}] "
        'Run load_skill("discovery-retro") and follow it: read kills and their reasons, '
        "declined and rejected bets, readouts, co-sign blocks, quote refusals and coverage "
        "gaps, then propose at most 2 method changes with evidence."
    )


def _discovery_activity(deps: FactoryDeps, fid: str, since: int) -> int:
    return len(
        deps.factory_store.find_events(
            fid, ["opportunity.*", "bet.*", "readout.*"], since * 1000, limit=50
        )
    )


async def _retro(
    app_state: Any, root: Conversation, now: int, rows: Sequence[Conversation]
) -> bool:
    from omnigent.server.factory.retro import sentinel_of

    deps = factory_deps(app_state)
    fid = root.id
    if not rows:
        return False
    last = await asyncio.to_thread(_last_s, deps, fid, KIND_RETRO)
    baseline = last if last is not None else int(min(c.created_at for c in rows))
    if now - baseline < RETRO_EVERY_S:
        return False
    if not await asyncio.to_thread(_discovery_activity, deps, fid, now - RETRO_EVERY_S):
        return False
    sentinel = await asyncio.to_thread(sentinel_of, deps, fid)
    if sentinel is None:
        return False
    days = RETRO_EVERY_S // 86400
    control.send_text(app_state, sentinel.id, retro_text(days, RETRO_BUDGET_USD), kind="request")
    await events.emit(
        app_state,
        fid,
        KIND_RETRO,
        to_id=sentinel.id,
        subject=SUBJECT,
        summary=f"the server asked Sentinel for the discovery retro (window {days}d)",
        data={"window_days": days},
        ts=now * 1000,
    )
    return True


# ── The pass ───────────────────────────────────────────────────


async def beat_factory(app_state: Any, root: Conversation, *, now: int | None = None) -> list[str]:
    """
    Run one factory's discovery beats.

    :returns: What went out, e.g. ``["sweep", "ready_to_plan"]``.
    """
    from omnigent.server.factory.discovery import seat

    deps = factory_deps(app_state)
    fid = root.id
    when = now if now is not None else now_s()
    if not await asyncio.to_thread(definition.discovery_enabled, deps, fid):
        return []
    from omnigent.server.factory.discovery.opportunities import effective_doc

    doc, _version = await asyncio.to_thread(effective_doc, deps, fid)
    sent: list[str] = []
    # Read once per pass: the seat (any state) and Scout's spend today.
    rows = await asyncio.to_thread(seat.scouts, deps, fid)
    scout = next((c for c in rows if seat.spawn.holds_pod(c)), None)
    spent = await asyncio.to_thread(seat.spent_today, deps, fid, when)
    stopped = await _budget(app_state, root, doc, when, spent)
    if stopped:
        sent.append("budget_stop")
    if scout is not None and scout.labels.get(LABEL_STATE) == STATE_ACTIVE and not stopped:
        sent += await _scout_beats(app_state, root, scout, doc, when, spent)
    if await _ready_to_plan(app_state, root, doc, when):
        sent.append("ready_to_plan")
    if await _retro(app_state, root, when, rows):
        sent.append("retro")
    return sent


async def run_once(app_state: Any, *, now: int | None = None) -> dict[str, list[str]]:
    """One pass over every open factory. :returns: Factory id → the beats sent."""
    from omnigent.server.factory.discovery import seat

    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps) or not discovery_server_on():
        return {}
    out: dict[str, list[str]] = {}
    for root in await asyncio.to_thread(seat.factory_roots, deps):
        if not seat._open(root):
            continue
        try:
            sent = await beat_factory(app_state, root, now=now)
        except Exception:
            _logger.exception("factory %s: discovery beats failed", root.id)
            continue
        if sent:
            out[root.id] = sent
    return out


async def start(app_state: Any) -> Callable[[], Awaitable[None]]:
    """Run :func:`run_once` every :data:`TICK_S` while the server runs discovery."""
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)

    async def _noop() -> None:
        return None

    if not isinstance(deps, FactoryDeps) or not discovery_server_on():
        return _noop

    async def _loop() -> None:
        while True:
            try:
                done = await run_once(app_state)
                # A stopped budget repeats every pass; log only what went out.
                news = {k: v for k, v in done.items() if v != ["budget_stop"]}
                if news:
                    _logger.info("factory discovery: beats %s", news)
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("factory discovery: beats pass failed")
            await asyncio.sleep(TICK_S)

    task = asyncio.get_running_loop().create_task(_loop(), name="factory-discovery-beats")

    async def stop() -> None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    return stop


__all__ = [
    "KIND_BUDGET_LINE",
    "KIND_BUDGET_STOP",
    "KIND_READY",
    "KIND_RETRO",
    "KIND_SWEEP",
    "KIND_SYNTHESIS",
    "TICK_S",
    "beat_factory",
    "budget_cap",
    "ready_items",
    "ready_text",
    "retro_text",
    "run_once",
    "start",
    "sweep_text",
    "synthesis_text",
]
