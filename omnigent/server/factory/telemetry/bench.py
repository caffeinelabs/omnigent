"""The bench: each role's track record by vendor and model, always with n.

One row per role × vendor · model seen in the window. Cost comes from the cost
ledger (every turn is booked with its model). Quality needs work attributed to
a vendor and model, which the task spans of workers carry once stamped
(:func:`~omnigent.server.factory.telemetry.spans.write_span`):

- builders (``ic``): first-pass review (the review gate passed on its first run)
  and review rounds per merged PR; cost per merged PR;
- reviewers: share of the PRs they reviewed that reached ready and got no later
  change request from a person; cost per reviewed PR;
- demo: demo passes first run; cost per demo;
- every other role: cost per active day only.

Rows with fewer than :data:`LOW_N` samples are flagged ``low_n``: the cockpit
shows them grey with "not enough data". Until any stamped work exists, the
bench says so and shows cost by model only.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Final

from omnigent.factory import (
    ROLE_DEMO,
    ROLE_ENV_KEEPER,
    ROLE_IC,
    ROLE_LEAD,
    ROLE_PLANNER,
    ROLE_PROJECT_LEAD,
    ROLE_REVIEWER,
)
from omnigent.server.factory.telemetry import metrics

DEFAULT_WINDOW: Final = "30d"
LOW_N: Final = 5
ROLE_ORDER: Final = (
    ROLE_PROJECT_LEAD,
    ROLE_IC,
    ROLE_REVIEWER,
    ROLE_DEMO,
    ROLE_ENV_KEEPER,
    ROLE_LEAD,
    ROLE_PLANNER,
)
QUALITY_LABELS: Final[dict[str, str]] = {
    ROLE_IC: "first-pass review",
    ROLE_REVIEWER: "no later change requests",
    ROLE_DEMO: "demo passes first run",
}
UNITS: Final[dict[str, str]] = {
    ROLE_IC: "merged PR",
    ROLE_REVIEWER: "reviewed PR",
    ROLE_DEMO: "demo",
}
DAY_UNIT: Final = "active day"
NOT_READY_NOTE: Final = (
    "Quality by model needs a few more days of data. "
    "Until then the bench shows cost by model only."
)

Key = tuple[str, str | None, str | None]


@dataclass
class _Row:
    cost: float = 0.0
    days: set[str] = field(default_factory=set)
    quality: list[float] = field(default_factory=list)
    rounds: list[float] = field(default_factory=list)
    units: int = 0


def _first_pass(facts: metrics.Facts, w: metrics.Window, gate: str, role: str) -> Iterable[Any]:
    for span in facts.spans:
        attrs = span.attrs or {}
        if span.kind != "gate" or span.name != gate:
            continue
        if not isinstance(attrs.get("first_pass"), bool) or not w.holds(span.started_at / 1000):
            continue
        stamp = facts.pr_agent(span.pr_id, role)
        if stamp.vendor:
            yield span, stamp, 1.0 if attrs["first_pass"] else 0.0


def compute_bench(
    facts: metrics.Facts, window: metrics.Window, filters: metrics.Filters | None = None
) -> dict[str, Any]:
    """
    The bench of one factory over *window* (pure).

    :returns: ``{rows: [{role, vendor, model, n, quality, rounds, cost, low_n}],
        quality_ready, stamped_since, note, low_n}``.
    """
    keep = filters or metrics.Filters()
    rows: dict[Key, _Row] = defaultdict(_Row)
    for sample in metrics.CATALOG["cost_usd"].samples(facts, window):
        if not keep.keep(sample) or not sample.role:
            continue
        row = rows[(sample.role, sample.vendor, sample.model)]
        row.cost += sample.value
        if sample.value > 0:
            row.days.add(sample.group("day"))
    for span, stamp, value in _first_pass(facts, window, "review", ROLE_IC):
        if keep.keeps_project(span.project_slug):
            rows[(ROLE_IC, stamp.vendor, stamp.model)].quality.append(value)
    for span, stamp, value in _first_pass(facts, window, "demo", ROLE_DEMO):
        if keep.keeps_project(span.project_slug):
            row = rows[(ROLE_DEMO, stamp.vendor, stamp.model)]
            row.quality.append(value)
            row.units += 1
    for sample in metrics.CATALOG["review_rounds"].samples(facts, window):
        if keep.keep(sample) and sample.vendor:
            row = rows[(ROLE_IC, sample.vendor, sample.model)]
            row.rounds.append(sample.value)
            row.units += 1
    for pr in facts.prs:
        if not window.holds(pr.first_ready_at) or not keep.keeps_project(pr.project_slug):
            continue
        stamp = facts.pr_agent(pr.id, ROLE_REVIEWER)
        if not stamp.vendor:
            continue
        later = metrics._changes_requested(facts, pr, after=pr.first_ready_at)
        row = rows[(ROLE_REVIEWER, stamp.vendor, stamp.model)]
        row.quality.append(0.0 if later else 1.0)
        row.units += 1
    out = [_view(key, row) for key, row in rows.items()]
    out.sort(key=_order)
    stamped = [
        s.started_at
        for s in facts.spans
        if s.kind == "task" and isinstance((s.attrs or {}).get("vendor"), str)
    ]
    ready = any(r["quality"] is not None for r in out)
    return {
        "rows": out,
        "quality_ready": ready,
        "stamped_since": min(stamped) // 1000 if stamped else None,
        "note": None if ready else NOT_READY_NOTE,
        "low_n": LOW_N,
    }


def _order(row: dict[str, Any]) -> tuple[int, str, str, str]:
    role = row["role"]
    rank = ROLE_ORDER.index(role) if role in ROLE_ORDER else len(ROLE_ORDER)
    return rank, role, row["vendor"] or "~", row["model"] or "~"


def _view(key: Key, row: _Row) -> dict[str, Any]:
    role, vendor, model = key
    quality: dict[str, Any] | None = None
    if row.quality and role in QUALITY_LABELS:
        quality = {
            "label": QUALITY_LABELS[role],
            "value": round(sum(row.quality) / len(row.quality), 4),
            "n": len(row.quality),
        }
    rounds = (
        {"value": round(sum(row.rounds) / len(row.rounds), 2), "n": len(row.rounds)}
        if row.rounds
        else None
    )
    unit = UNITS.get(role, DAY_UNIT)
    units = row.units if role in UNITS else len(row.days)
    cost = None
    if row.cost > 0 or units:
        cost = {
            "usd": round(row.cost, 2),
            "unit": unit,
            "units": units,
            "per_unit": round(row.cost / units, 2) if units else None,
        }
    n: int = len(row.quality) if quality is not None else units
    return {
        "role": role,
        "vendor": vendor,
        "model": model,
        "n": n,
        "quality": quality,
        "rounds": rounds,
        "cost": cost,
        "low_n": n < LOW_N,
    }


__all__ = ["DEFAULT_WINDOW", "LOW_N", "NOT_READY_NOTE", "compute_bench"]
