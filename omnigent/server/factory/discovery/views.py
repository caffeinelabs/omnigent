"""JSON views of the discovery ledger for the cockpit (``api/discovery.py``).

Every view is computed from stored facts: counts, levels and scores come
from the server, quotes are picked from the ledger (never from agent text),
and signal text is marked as untrusted data. A suspected injection is shown
as such and is never actionable.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import LABEL_ROLE, LABEL_STATE, STATE_ACTIVE
from omnigent.server.factory import definition
from omnigent.server.factory.control import human_name
from omnigent.server.factory.discovery import evidence
from omnigent.server.factory.discovery.ledger import (
    CONVICTION_KINDS,
    INJECTION_SUSPECT,
    SALT_ROW,
    SUSPECT_AT,
    sources_of,
)
from omnigent.server.factory.discovery.opportunities import (
    CLOSED,
    LIFECYCLE,
    OPEN_PROPOSALS,
    Evaluated,
    Facts,
)
from omnigent.server.factory.envelope import display_title
from omnigent.server.factory.membership import factory_members, is_session_id
from omnigent.stores.factory_store import now_s

if TYPE_CHECKING:
    from omnigent.entities.factory_discovery import FactorySignal, FactorySignalLink
    from omnigent.server.factory.context import FactoryDeps

SCOUT_ROLE: Final = "scout"
"""The discovery seat's role label (the seat itself comes with the Scout bundle)."""
QUOTES_MAX: Final = 3
AGAINST_MAX: Final = 2
HUNCH_QUOTES_MAX: Final = 2
HISTORY_MAX: Final = 20
_DAY_S: Final = 86400
_WEEK_S: Final = 7 * _DAY_S
_EXCLUDED: Final = {
    INJECTION_SUSPECT: "Possible injection · excluded from scoring",
    "simulation": "Simulated · never counts as evidence",
}


def author_name(deps: FactoryDeps, author: str | None, kind: str | None) -> str | None:
    """:returns: How the cockpit names a hunch's author (a person or an agent)."""
    if not author:
        return None
    if kind == "agent" and is_session_id(author):
        conv = deps.conversation_store.get_conversation(author)
        return display_title(conv.title, conv.id) if conv is not None else "an agent"
    return human_name(author)


def _weight(signal: FactorySignal, ladder: Mapping[str, Any]) -> float:
    if signal.kind == "simulation" or INJECTION_SUSPECT in signal.flags:
        return 0.0
    if signal.kind in CONVICTION_KINDS:
        return float(ladder.get("hunch", 0.1))
    return 1.0


def _excluded(signal: FactorySignal) -> str | None:
    if INJECTION_SUSPECT in signal.flags:
        return _EXCLUDED[INJECTION_SUSPECT]
    if signal.kind == "simulation":
        return _EXCLUDED["simulation"]
    return None


def signal_view(
    deps: FactoryDeps,
    signal: FactorySignal,
    *,
    links: Sequence[FactorySignalLink] = (),
    titles: Mapping[int, str] | None = None,
    ladder: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """
    One signal as the cockpit shows it.

    ``text`` is the redacted excerpt and is untrusted user text; ``gone``
    says why there is none (``expired``, ``withdrawn``).
    """
    excluded = _excluded(signal)
    gone = "withdrawn" if signal.withdrawn_at else "expired" if signal.expired_at else None
    return {
        "id": signal.ref,
        "source": signal.source,
        "source_kind": signal.source_kind,
        "kind": signal.kind,
        "say_do": signal.say_do,
        "text": signal.excerpt,
        "untrusted": True,
        "gone": gone,
        "pointer": signal.pointer,
        "observed_at": signal.observed_at,
        "ingested_at": signal.ingested_at,
        "segments": list(signal.segments),
        "account": signal.account,
        "paraphrase": (
            {"text": signal.paraphrase, "by": signal.labelled_by, "agent_text": True}
            if signal.paraphrase
            else None
        ),
        "intensity": signal.intensity,
        "force": signal.force,
        "flags": list(signal.flags),
        "injection_suspect": INJECTION_SUSPECT in signal.flags,
        "excluded": excluded,
        "actionable": excluded is None and gone is None,
        "weight": _weight(signal, ladder or {}),
        "hunch": signal.kind in CONVICTION_KINDS,
        "author": author_name(deps, signal.author, signal.author_kind),
        "privacy": signal.privacy,
        "expires_at": signal.expires_at,
        "redactions": dict(signal.redactions),
        "query_event": signal.query_event,
        "links": [
            {
                "opportunity": f"O-{link.opportunity}",
                "title": (titles or {}).get(link.opportunity),
                "counter": link.counter,
                "confidence": link.confidence,
                "attached_by": author_name(deps, link.attached_by, link.attached_kind),
                "attached_at": link.attached_at,
            }
            for link in links
        ],
    }


def _votes(votes: Mapping[str, int], user_id: str | None) -> dict[str, Any]:
    # Keyed as the levers key them: the person's id, or their name in single-user mode.
    mine = user_id or human_name(user_id)
    return {
        "up": sum(1 for v in votes.values() if v > 0),
        "down": sum(1 for v in votes.values() if v < 0),
        "mine": votes.get(mine, 0) if votes else 0,
        "note": "votes never change the score",
    }


def _read_out_and_dropped(e: Evaluated) -> bool:
    return (e.row.result or {}).get("decision") == "drop"


def _bet(e: Evaluated) -> Any:
    with_bet = [p for p in e.proposals if p.bet is not None]
    return with_bet[0] if with_bet else (e.proposals[0] if e.proposals else None)


def next_step(e: Evaluated) -> dict[str, Any] | None:
    """:returns: The main button of the opportunity: what happens to it next."""
    status = e.status
    if status == "merged":
        return {"action": "open", "label": f"Open O-{e.row.merged_into}"}
    if status == "building":
        live = [p for p in e.projects if p.status == "active"]
        slug = live[0].slug if live else (e.projects[0].slug if e.projects else None)
        return {"action": "open_project", "label": "Open project", "project": slug}
    if status == "proposed":
        waiting = [p for p in e.proposals if p.status in OPEN_PROPOSALS]
        ref = waiting[0].ref if waiting else None
        return {"action": "review_bet", "label": f"Review bet {ref}", "proposal": ref}
    if status == "shipped":
        return {"action": "await_readout", "label": "Readout after release"}
    if status == "measured":
        return {"action": "decide", "label": "Decide: keep or drop"}
    if status == "validated":
        return {"action": "plan", "label": "Plan this"}
    if status == "idea":
        if e.score.quadrant == "test_first":
            return {"action": "plan", "label": "Plan a test"}
        return {"action": "research", "label": "Research this"}
    if status == "parked":
        return {"action": "unpark", "label": "Unpark"}
    if status == "dropped" and e.row.kill is not None and not _read_out_and_dropped(e):
        return {"action": "revive", "label": "Revive"}
    return None


def opportunity_row(e: Evaluated, *, user_id: str | None) -> dict[str, Any]:
    """One opportunity as a row of the table."""
    row = e.row
    bet = _bet(e)
    return {
        "id": row.ref,
        "title": row.title,
        "statement": row.statement,
        "status": e.status,
        "status_reason": e.reason,
        "band": e.band,
        "rank": round(e.score.rank, 2),
        "score": e.score.to_dict(),
        "evidence": e.meter.to_dict(),
        "goal": row.goal,
        "non_goal": row.non_goal,
        "segments": list(row.segments),
        "area": row.area,
        "votes": _votes(row.votes, user_id),
        "pinned": row.pinned_by is not None,
        "parked": row.park,
        "killed": row.kill,
        "research": row.research,
        "next": next_step(e),
        "bet": (
            {"proposal": bet.ref, "status": bet.status, "kind": (bet.bet or {}).get("kind")}
            if bet is not None
            else None
        ),
        "projects": [{"slug": p.slug, "status": p.status, "stage": p.stage} for p in e.projects],
        "stale": e.meter.stale,
        "updated_at": row.updated_at,
    }


def _quote(deps: FactoryDeps, signal: FactorySignal) -> dict[str, Any]:
    return {
        "signal": signal.ref,
        "text": signal.excerpt,
        "source": signal.source,
        "source_kind": signal.source_kind,
        "segments": list(signal.segments),
        "observed_at": signal.seen_at,
        "verified": True,
        "hunch": signal.kind in CONVICTION_KINDS,
        "author": author_name(deps, signal.author, signal.author_kind)
        if signal.kind in CONVICTION_KINDS
        else None,
    }


def _pick(signals: Iterable[FactorySignal], limit: int) -> list[FactorySignal]:
    seen: set[str] = set()
    out: list[FactorySignal] = []
    for signal in sorted(signals, key=lambda s: s.seen_at, reverse=True):
        key = signal.account or signal.ref
        if key in seen or not signal.excerpt or _excluded(signal) is not None:
            continue
        seen.add(key)
        out.append(signal)
        if len(out) >= limit:
            break
    return out


def quotes(deps: FactoryDeps, e: Evaluated) -> dict[str, Any]:
    """:returns: ``{said[], against[], hunches[]}``: verified excerpts picked from the ledger."""
    support = [
        i.signal for i in e.linked if not i.counter and i.signal.kind not in CONVICTION_KINDS
    ]
    against = [i.signal for i in e.linked if i.counter]
    hunches = [i.signal for i in e.linked if not i.counter and i.signal.kind in CONVICTION_KINDS]
    return {
        "said": [_quote(deps, s) for s in _pick(support, QUOTES_MAX)],
        "against": [_quote(deps, s) for s in _pick(against, AGAINST_MAX)],
        "hunches": [_quote(deps, s) for s in _pick(hunches, HUNCH_QUOTES_MAX)],
        "total": len(support),
        "note": "agents can cite quotes but never write them",
    }


def why_chain(e: Evaluated) -> list[dict[str, Any]]:
    """:returns: Evidence → Opportunity → Bet → Project → Readout; a missing link says so."""
    bet = _bet(e)
    live = [p for p in e.projects if p.status != "cancelled"]
    meter = e.meter
    evidence_label = (
        f"{meter.accounts} accounts · {meter.level}"
        if meter.accounts
        else (
            f"{meter.hunches} hunch{'es' if meter.hunches != 1 else ''}"
            if meter.hunches
            else "none yet"
        )
    )
    measured = (e.row.result or {}).get("measured_at")
    return [
        {"kind": "evidence", "label": evidence_label, "missing": meter.level == "none"},
        {"kind": "opportunity", "ref": e.row.ref, "label": e.row.title, "missing": False},
        {
            "kind": "bet",
            "ref": bet.ref if bet is not None else None,
            "label": ((bet.bet or {}).get("outcome") or bet.title)
            if bet is not None
            else "none yet",
            "missing": bet is None,
        },
        {
            "kind": "project",
            "ref": live[0].slug if live else None,
            "label": ", ".join(f"#{p.slug}" for p in live) if live else "none yet",
            "missing": not live,
        },
        {
            "kind": "readout",
            "ref": None,
            "label": "in" if measured else "not yet · day 7 and 28 after release",
            "missing": not measured,
        },
    ]


def lifecycle(status: str) -> list[dict[str, Any]]:
    """:returns: The steps idea › … › measured, with the one reached marked."""
    at = LIFECYCLE.index(status) if status in LIFECYCLE else None
    return [
        {"step": step, "reached": at is not None and i <= at, "current": i == at}
        for i, step in enumerate(LIFECYCLE)
    ]


def opportunity_detail(
    deps: FactoryDeps,
    e: Evaluated,
    *,
    user_id: str | None,
    def_version: int | None,
    history: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """One opportunity as its panel shows it."""
    row = e.row
    return {
        **opportunity_row(e, user_id=user_id),
        "problem": row.problem,
        "alternatives": list(row.alternatives),
        "bet_note": row.bet_note,
        "quotes": quotes(deps, e),
        "why": why_chain(e),
        "lifecycle": lifecycle(e.status),
        "declines": list(row.declines),
        "origin": row.origin,
        "merged_into": f"O-{row.merged_into}" if row.merged_into else None,
        "split_from": f"O-{row.split_from}" if row.split_from else None,
        "plan_request": row.plan_request,
        "created_by": author_name(deps, row.created_by, row.created_kind),
        "created_kind": row.created_kind,
        "created_at": row.created_at,
        "pinned_by": human_name(row.pinned_by) if row.pinned_by else None,
        "proposals": [
            {"proposal": p.ref, "status": p.status, "title": p.title, "bet": p.bet is not None}
            for p in e.proposals
        ],
        "rubric_version": def_version,
        "history": list(history),
    }


# ── The summary (``GET …/discovery``) ──────────────────────────


def _scout(deps: FactoryDeps, factory_id: str) -> dict[str, Any]:
    for conv in factory_members(factory_id, deps.conversation_store):
        if conv.labels.get(LABEL_ROLE) == SCOUT_ROLE:
            state = conv.labels.get(LABEL_STATE) or STATE_ACTIVE
            return {
                "state": state,
                "session_id": conv.id,
                "title": display_title(conv.title, conv.id),
            }
    return {"state": "off", "session_id": None, "title": None}


def _spent_today(deps: FactoryDeps, factory_id: str, now: int) -> float:
    import time

    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    cells = deps.factory_store.list_cost_daily(factory_id, since_day=day, until_day=day)
    return round(sum(c.cost_usd for c in cells if c.role == SCOUT_ROLE), 2)


def _sources(
    deps: FactoryDeps, factory_id: str, per_source: Mapping[str, int]
) -> list[dict[str, Any]]:
    states = {
        s.source: s
        for s in deps.factory_store.list_source_states(factory_id)
        if s.source != SALT_ROW
    }
    configured = {
        str(s.get("id")): s
        for s in definition.value(deps, factory_id, "discovery.sources", []) or []
        if isinstance(s, Mapping)
    }
    out: list[dict[str, Any]] = []
    for ref in sources_of(deps, factory_id):
        raw = configured.get(ref.id, {})
        state = states.get(ref.id)
        out.append(
            {
                "id": ref.id,
                "label": raw.get("label") or ref.id,
                "kind": ref.kind,
                "state": ref.state,
                "privacy": ref.privacy,
                "keep_days": ref.keep_days,
                "calls_today": state.calls_today if state is not None else 0,
                "calls_per_day": (raw.get("budget") or {}).get("calls_per_day"),
                "last_ok_at": state.last_ok_at if state is not None else None,
                "last_error": state.last_error if state is not None else None,
                "signals_total": state.signals_total if state is not None else 0,
                "signals_week": per_source.get(ref.id, 0),
            }
        )
    return out


def summary(
    deps: FactoryDeps,
    factory_id: str,
    facts: Facts,
    items: Sequence[Evaluated],
    *,
    doc: Mapping[str, Any],
    def_version: int | None,
) -> dict[str, Any]:
    """
    ``GET …/discovery``: the chain counts, coverage, Scout, budget, sources and goals.

    Signal counts are counted by the store (no signal row is read), so the
    header costs the same with ten signals or a hundred thousand.
    """
    now = facts.now
    discovery = doc.get("discovery") or {}
    strategy = doc.get("strategy") or {}
    product = doc.get("product") or {}
    statuses = [e.status for e in items]
    counts = deps.factory_store.signal_counts(
        factory_id, since=now - _WEEK_S, segments_since=now - 30 * _DAY_S, suspect_at=SUSPECT_AT
    )
    focus = [str(s) for s in strategy.get("focus_segments") or []]
    coverage = []
    for segment in product.get("segments") or []:
        if not isinstance(segment, Mapping):
            continue
        sid = str(segment.get("id"))
        coverage.append(
            {
                "segment": sid,
                "label": segment.get("label") or sid,
                "focus": sid in focus,
                "signals_30d": counts.segments.get(sid, 0),
            }
        )
    coverage.sort(key=lambda c: (not c["focus"], c["signals_30d"]))
    goals = []
    for goal in strategy.get("goals") or []:
        if not isinstance(goal, Mapping):
            continue
        gid = str(goal.get("id"))
        under = [e for e in items if e.row.goal == gid and e.status not in CLOSED]
        goals.append(
            {
                "id": gid,
                "statement": goal.get("statement"),
                "metric": goal.get("metric"),
                "measured": bool(goal.get("metric")),
                "opportunities": len(under),
            }
        )
    budgets = discovery.get("budgets") or {}
    bands = (discovery.get("rubric") or {}).get("bands") or {}
    return {
        "enabled": True,
        "def_version": def_version,
        "rubric_version": def_version,
        "scout": _scout(deps, factory_id),
        "budget": {
            "usd_per_day": budgets.get("usd_per_day"),
            "per_ask": budgets.get("per_ask"),
            "spent_today": _spent_today(deps, factory_id, now),
        },
        "sources": _sources(deps, factory_id, counts.by_source),
        "chain": {
            "signals_week": counts.recent,
            "opportunities": sum(1 for s in statuses if s not in CLOSED),
            "bets": sum(1 for s in statuses if s == "proposed"),
            "building": sum(1 for s in statuses if s == "building"),
            "shipped": sum(1 for s in statuses if s == "shipped"),
            "measured": sum(
                1 for e in items if e.status in ("measured", "kept") or _read_out_and_dropped(e)
            ),
        },
        "bands": {
            "now": sum(1 for e in items if e.band == "now"),
            "next": sum(1 for e in items if e.band == "next"),
            "later": sum(1 for e in items if e.band == "later"),
            "parked": sum(1 for e in items if e.band == "parked"),
            "cutoffs": {"now": bands.get("now"), "next": bands.get("next")},
        },
        "goals": goals,
        "unassigned": sum(1 for e in items if e.row.goal is None and e.status not in CLOSED),
        "coverage": coverage,
        "signals": {
            "total": counts.total,
            "unsorted": counts.unsorted,
            "flagged": counts.flagged,
            "hunches": counts.hunches,
        },
        "levels": list(evidence.LEVELS),
        "generated_at": now_s(),
    }


__all__ = [
    "SCOUT_ROLE",
    "author_name",
    "lifecycle",
    "next_step",
    "opportunity_detail",
    "opportunity_row",
    "quotes",
    "signal_view",
    "summary",
    "why_chain",
]
