"""The opportunity rank: its formula is code (Tier 0), every number in it is a rubric field.

::

    rank       = reach_s × intensity × fit × confidence
    reach_s    = min(1, sqrt(reach / reach_ref))
                 reach = accounts a month: a behaviour aggregate's reach (from an
                         analytics or observability source) when one exists,
                         otherwise distinct accounts × the middle of reach_ratio
    intensity  = the median label of the supporting signals, each account's
                 freshest per_account_cap labels only (one loud account is one voice):
                 blocker 1.0 · workaround 0.6 · annoyance 0.3 · nice-to-have 0.1
    fit        = goal weight ÷ heaviest goal weight × (1 in a focus segment, else
                 off_focus_factor); 0 under a non-goal
    confidence = the evidence meter (:mod:`.evidence`)
    band       = Now ≥ bands.now · Next ≥ bands.next · Later below

RICE with effort left to the bet. Value (reach_s × intensity × fit) against
confidence places an opportunity on the map: bet, test first, quick win or
wait. :func:`rerank` fills the re-rank of ``POST …/definition/preview``.
"""

from __future__ import annotations

import logging
import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from omnigent.server.factory.discovery import evidence
from omnigent.server.factory.discovery.evidence import Linked, Meter
from omnigent.server.factory.discovery.ledger import (
    BEHAVIOR_SOURCE_KINDS,
    CONVICTION_KINDS,
    INJECTION_SUSPECT,
)

if TYPE_CHECKING:
    from omnigent.entities.factory_discovery import FactoryOpportunity
    from omnigent.server.factory.context import FactoryDeps

_logger = logging.getLogger(__name__)

_DAY_S: Final = 86400
INTENSITY_ORDER: Final = ("nice", "annoyance", "workaround", "blocker")
_INTENSITY_WORDS: Final = {
    "blocker": "blocker",
    "workaround": "workaround",
    "annoyance": "annoyance",
    "nice": "nice-to-have",
}
_DEFAULTS: Final[dict[str, Any]] = {
    "window_days": 90,
    "reach_ratio": {"low": 10, "high": 40},
    "reach_ref": 1000,
    "intensity": {"blocker": 1.0, "workaround": 0.6, "annoyance": 0.3, "nice": 0.1},
    "off_focus_factor": 0.5,
    "bands": {"now": 0.20, "next": 0.10},
    "map": {"value_high": 0.4, "confidence_high": 0.5},
}
RERANK_PATHS: Final = ("strategy", "discovery.rubric")
"""Definition paths that move a rank (a change elsewhere re-ranks nothing)."""
RERANK_MAX: Final = 20


@dataclass(frozen=True)
class Part:
    """One factor of the rank, with what it read and why."""

    name: str
    value: float
    input: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """:returns: The part as JSON (value to 2 places)."""
        return {
            "name": self.name,
            "value": round(self.value, 2),
            "input": self.input,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Score:
    """
    One opportunity's score.

    :param band: ``now``, ``next`` or ``later`` (what the rank says, whatever the status).
    :param quadrant: ``bet``, ``test_first``, ``quick_win`` or ``wait``.
    """

    rank: float
    value: float
    band: str
    quadrant: str
    parts: tuple[Part, ...] = ()
    inputs: dict[str, Any] = field(default_factory=dict)

    def part(self, name: str) -> float:
        """:returns: The value of part *name*."""
        return next((p.value for p in self.parts if p.name == name), 0.0)

    def to_dict(self) -> dict[str, Any]:
        """:returns: The score as JSON: rank, band, quadrant, the parts and the formula line."""
        return {
            "rank": round(self.rank, 2),
            "value": round(self.value, 2),
            "band": self.band,
            "quadrant": self.quadrant,
            "parts": [p.to_dict() for p in self.parts],
            "line": " × ".join(f"{p.name} {p.value:.2f}" for p in self.parts)
            + f" = {self.rank:.2f}",
        }


def rubric_values(rubric: Mapping[str, Any] | None) -> dict[str, Any]:
    """:returns: *rubric* over the score's defaults."""
    raw = dict(rubric or {})
    out = {**_DEFAULTS, **raw}
    for key in ("reach_ratio", "intensity", "bands", "map"):
        out[key] = {**_DEFAULTS[key], **dict(raw.get(key) or {})}
    return out


def band_of(rank: float, rubric: Mapping[str, Any] | None) -> str:
    """:returns: ``now``, ``next`` or ``later`` for *rank*."""
    bands = rubric_values(rubric)["bands"]
    rounded = round(rank, 2)
    if rounded >= float(bands["now"]):
        return "now"
    if rounded >= float(bands["next"]):
        return "next"
    return "later"


def quadrant_of(value: float, confidence: float, rubric: Mapping[str, Any] | None) -> str:
    """:returns: Where *value* × *confidence* sits on the map."""
    cut = rubric_values(rubric)["map"]
    high_value = value >= float(cut["value_high"])
    high_confidence = confidence >= float(cut["confidence_high"])
    if high_value:
        return "bet" if high_confidence else "test_first"
    return "quick_win" if high_confidence else "wait"


def _support(linked: Sequence[Linked], now: int, window_days: int) -> list[Any]:
    window = window_days * _DAY_S
    return [
        item.signal
        for item in linked
        if not item.counter
        and item.signal.kind not in CONVICTION_KINDS
        and item.signal.kind != "simulation"
        and INJECTION_SUSPECT not in item.signal.flags
        and now - item.signal.seen_at <= window
    ]


def _reach(meter: Meter, support: Sequence[Any], r: Mapping[str, Any]) -> Part:
    ref = max(float(r["reach_ref"]), 1.0)
    measured = [
        (s.seen_at, float(s.meta["reach"]))
        for s in support
        if s.kind == "behavior"
        and s.source_kind in BEHAVIOR_SOURCE_KINDS
        and isinstance((s.meta or {}).get("reach"), int | float)
    ]
    if measured:
        reach = max(measured)[1]
        what = f"{reach:g}/mo measured"
        why = "from a behaviour aggregate"
    else:
        ratio = r["reach_ratio"]
        middle = (float(ratio["low"]) + float(ratio["high"])) / 2
        reach = meter.accounts * middle
        what = f"{meter.accounts} accts × {middle:g} = {reach:g}/mo of {ref:g}"
        why = "accounts heard from, scaled by the reach ratio"
    value = min(1.0, math.sqrt(max(reach, 0.0) / ref))
    return Part("reach", value, what, why)


def capped_labels(support: Sequence[Any], cap: int) -> dict[str, list[str]]:
    """
    ``{account: labels}``: each account's freshest *cap* intensity labels.

    An account-less signal (a public page, an aggregate) counts as its own
    account, as in the evidence meter.
    """
    by_account: dict[str, list[Any]] = {}
    for signal in support:
        if signal.intensity in INTENSITY_ORDER:
            by_account.setdefault(evidence.account_key(signal), []).append(signal)
    out: dict[str, list[str]] = {}
    for account, rows in by_account.items():
        rows.sort(key=lambda s: (s.seen_at, s.number), reverse=True)
        out[account] = [s.intensity for s in rows[: max(cap, 1)]]
    return out


def _intensity(support: Sequence[Any], r: Mapping[str, Any], cap: int) -> Part:
    weights = r["intensity"]
    per_account = capped_labels(support, cap)
    labels = [lbl for found in per_account.values() for lbl in found]
    if not labels:
        return Part(
            "intensity",
            float(weights["nice"]),
            "no signal labelled yet",
            "counts as nice-to-have until signals are labelled",
        )
    middle = statistics.median_low(sorted(INTENSITY_ORDER.index(lbl) for lbl in labels))
    label = INTENSITY_ORDER[middle]
    share = sum(1 for found in per_account.values() if label in found)
    accounts = len(per_account)
    return Part(
        "intensity",
        float(weights[label]),
        f"most say {_INTENSITY_WORDS[label]}",
        f"the median label of {accounts} account{'s' if accounts != 1 else ''} "
        f"({share} say {_INTENSITY_WORDS[label]}; at most {max(cap, 1)} per account)",
    )


def _fit(
    opportunity: FactoryOpportunity, strategy: Mapping[str, Any], r: Mapping[str, Any]
) -> Part:
    off = float(r["off_focus_factor"])
    non_goals = [str(n) for n in strategy.get("non_goals") or []]
    if opportunity.non_goal and opportunity.non_goal in non_goals:
        return Part("fit", 0.0, f'under the non-goal "{opportunity.non_goal}"', "a non-goal")
    goals = {
        str(g.get("id")): float(g.get("weight", 1.0) or 0.0)
        for g in strategy.get("goals") or []
        if isinstance(g, Mapping) and g.get("id")
    }
    heaviest = max(goals.values(), default=0.0)
    if not goals:
        goal_factor, goal_why = 1.0, "no goals yet"
    elif opportunity.goal in goals:
        goal_factor = goals[opportunity.goal] / heaviest if heaviest > 0 else 0.0
        goal_why = (
            f"under {opportunity.goal}"
            if goal_factor == 1.0
            else f"under {opportunity.goal} (weight {goals[opportunity.goal]:g} of {heaviest:g})"
        )
    else:
        goal_factor, goal_why = off, "not under a goal yet"
    focus = [str(s) for s in strategy.get("focus_segments") or []]
    if not focus:
        seg_factor, seg_why = 1.0, "no focus segments"
    elif set(opportunity.segments) & set(focus):
        seg_factor, seg_why = 1.0, "in a focus segment"
    else:
        seg_factor, seg_why = off, "outside the focus segments"
    return Part(
        "fit", goal_factor * seg_factor, f"{goal_why}, {seg_why}", f"{goal_why}; {seg_why}"
    )


def score(
    opportunity: FactoryOpportunity,
    meter: Meter,
    linked: Sequence[Linked],
    *,
    strategy: Mapping[str, Any] | None,
    rubric: Mapping[str, Any] | None,
    now: int,
) -> Score:
    """
    Score one opportunity (pure: the caller loads the facts).

    :param meter: Its evidence (:func:`.evidence.measure`).
    :param linked: Its attached signals.
    :param strategy: The effective ``strategy`` block.
    :param rubric: The effective ``discovery.rubric``.
    """
    r = rubric_values(rubric)
    cap = int(evidence.rubric_values(rubric)["per_account_cap"])
    support = _support(linked, now, int(r["window_days"]))
    reach = _reach(meter, support, r)
    intensity = _intensity(support, r, cap)
    fit = _fit(opportunity, strategy or {}, r)
    confidence = Part(
        "confidence", meter.confidence, f"{meter.level} evidence", meter.reason or meter.level
    )
    value = reach.value * intensity.value * fit.value
    rank = value * confidence.value
    return Score(
        rank=rank,
        value=value,
        band=band_of(rank, r),
        quadrant=quadrant_of(value, confidence.value, r),
        parts=(reach, intensity, fit, confidence),
        inputs={
            "accounts": meter.accounts,
            "source_kinds": list(meter.source_kinds),
            "level": meter.level,
            "labels": sorted(
                lbl for found in capped_labels(support, cap).values() for lbl in found
            ),
            "goal": opportunity.goal,
            "segments": list(opportunity.segments),
            "non_goal": opportunity.non_goal,
        },
    )


# ── Re-rank (``POST …/definition/preview``) ────────────────────


def _moves_rank(paths: Iterable[str]) -> bool:
    return any(p == prefix or p.startswith(f"{prefix}.") for p in paths for prefix in RERANK_PATHS)


def _ranked(rows: Sequence[tuple[str, str, float, str]]) -> dict[str, tuple[int, float, str, str]]:
    ordered = sorted(rows, key=lambda r: (-round(r[2], 4), r[0]))
    return {ref: (i + 1, rank, band, title) for i, (ref, title, rank, band) in enumerate(ordered)}


def _line(
    ref: str, before: tuple[int, float, str, str], after: tuple[int, float, str, str]
) -> str:
    title = after[3]
    if before[2] != after[2]:
        return f"{ref} {title} moves from {before[2].capitalize()} to {after[2].capitalize()}"
    direction = "rises" if after[0] < before[0] else "drops"
    return f"{ref} {title} {direction} from #{before[0]} to #{after[0]}"


def rerank(
    deps: FactoryDeps,
    factory_id: str,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    paths: Iterable[str],
) -> list[dict[str, Any]]:
    """
    How a definition change would move the open opportunities (synchronous; never raises).

    :param before: The stored doc in force. :param after: The stored doc the change makes.
    :param paths: The changed paths (only strategy and rubric paths move a rank).
    :returns: ``[{opportunity, title, before{position, rank, band}, after{…}, line}]``,
        the opportunities whose band, position or rank changes, by their new position.
    """
    from omnigent.server.factory import definition
    from omnigent.server.factory.discovery import opportunities

    try:
        if not _moves_rank(list(paths)) or not definition.discovery_enabled(deps, factory_id):
            return []
        facts = opportunities.load_facts(deps, factory_id)
        rows_before: list[tuple[str, str, float, str]] = []
        rows_after: list[tuple[str, str, float, str]] = []
        for doc, rows in ((before, rows_before), (after, rows_after)):
            effective = definition.effective(doc)
            for item in opportunities.evaluate_all(facts, effective):
                if item.open:
                    rows.append((item.row.ref, item.row.title, item.score.rank, item.score.band))
        old, new = _ranked(rows_before), _ranked(rows_after)
        out: list[dict[str, Any]] = []
        for ref, now_at in sorted(new.items(), key=lambda kv: kv[1][0]):
            was = old.get(ref)
            if was is None:
                continue
            changed = (
                was[0] != now_at[0]
                or was[2] != now_at[2]
                or round(was[1], 2) != round(now_at[1], 2)
            )
            if not changed:
                continue
            out.append(
                {
                    "opportunity": ref,
                    "title": now_at[3],
                    "before": {"position": was[0], "rank": round(was[1], 2), "band": was[2]},
                    "after": {
                        "position": now_at[0],
                        "rank": round(now_at[1], 2),
                        "band": now_at[2],
                    },
                    "line": _line(ref, was, now_at),
                }
            )
        return out[:RERANK_MAX]
    except Exception:  # noqa: BLE001 - a preview never fails on the re-rank
        _logger.warning("factory %s: could not re-rank for a preview", factory_id, exc_info=True)
        return []


__all__ = [
    "INTENSITY_ORDER",
    "RERANK_MAX",
    "RERANK_PATHS",
    "Part",
    "Score",
    "band_of",
    "capped_labels",
    "quadrant_of",
    "rerank",
    "rubric_values",
    "score",
]
