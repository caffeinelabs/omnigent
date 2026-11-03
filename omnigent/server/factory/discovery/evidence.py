"""The evidence meter: how much an opportunity's signals say, computed by the server.

An agent never types a confidence. The meter reads the signals attached to
an opportunity and the rubric's numbers (``discovery.rubric``, Tier 1); its
rules are code (Tier 0):

========  ==========================================================  ==========
level     needs (the defaults are rubric fields)                      confidence
========  ==========================================================  ==========
hunch     only ``hunch`` or ``idea`` signals                          0.1
weak      user evidence short of moderate                             0.25
moderate  ≥ 5 accounts' worth from ≥ 2 source kinds, within 90 days   0.5
strong    moderate "say" evidence plus "do" evidence                  0.8
proven    a won test or readout                                       1.0
========  ==========================================================  ==========

- **Loud users count once**: an account counts at most ``per_account_cap``
  signals per opportunity per window; a signal with no account (a public
  page, an aggregate) counts as its own.
- **Evidence ages**: each account's weight decays with the rubric's
  half-life, so "moderate" needs ``moderate_min_accounts`` accounts' worth of
  evidence after ageing (to the nearest whole account).
- **Counter-evidence lowers the level** by one when its weight is at least
  ``counter_share_down`` of the supporting weight.
- **Some inputs never count**: ``simulation`` signals, signals flagged as a
  possible injection, and people's votes and pins.
- **A lost readout** drops the level to weak; a won one makes it proven.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Final

from omnigent.server.factory.discovery.ledger import (
    CONVICTION_KINDS,
    DO_KINDS,
    INJECTION_SUSPECT,
)

if TYPE_CHECKING:
    from omnigent.entities.factory_discovery import FactorySignal

LEVELS: Final = ("none", "hunch", "weak", "moderate", "strong", "proven")
"""The meter's levels, weakest first (``none``: no evidence at all)."""
_DAY_S: Final = 86400
_DEFAULTS: Final[dict[str, Any]] = {
    "ladder": {"hunch": 0.1, "weak": 0.25, "moderate": 0.5, "strong": 0.8, "proven": 1.0},
    "moderate_min_accounts": 5,
    "moderate_min_source_types": 2,
    "window_days": 90,
    "per_account_cap": 1,
    "decay_half_life_days": 60,
    "counter_share_down": 0.33,
}


@dataclass(frozen=True)
class Linked:
    """
    One signal as the meter reads it.

    :param counter: Attached as counter-evidence.
    """

    signal: FactorySignal
    counter: bool = False


@dataclass(frozen=True)
class Meter:
    """
    The evidence behind one opportunity.

    :param accounts: Distinct accounts of supporting user evidence in the window.
    :param weight: Their decayed weight (what the thresholds read).
    :param source_kinds: Kinds of source the supporting evidence came from.
    :param by_source: Distinct accounts per source id.
    :param stale: The newest signal is older than two half-lives.
    """

    level: str
    confidence: float
    accounts: int = 0
    weight: float = 0.0
    source_kinds: tuple[str, ...] = ()
    by_source: dict[str, int] = field(default_factory=dict)
    say: int = 0
    do: int = 0
    counter: int = 0
    counter_weight: float = 0.0
    hunches: int = 0
    excluded: int = 0
    first_seen: int | None = None
    last_seen: int | None = None
    stale: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """:returns: The meter as JSON (weights rounded)."""
        return {
            "level": self.level,
            "confidence": self.confidence,
            "accounts": self.accounts,
            "weight": round(self.weight, 2),
            "source_kinds": list(self.source_kinds),
            "by_source": dict(self.by_source),
            "say": self.say,
            "do": self.do,
            "counter": self.counter,
            "counter_weight": round(self.counter_weight, 2),
            "hunches": self.hunches,
            "excluded": self.excluded,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "stale": self.stale,
            "reason": self.reason,
        }


def rubric_values(rubric: Mapping[str, Any] | None) -> dict[str, Any]:
    """:returns: *rubric* over the meter's defaults (missing numbers filled in)."""
    out = {**_DEFAULTS, **dict(rubric or {})}
    out["ladder"] = {**_DEFAULTS["ladder"], **dict((rubric or {}).get("ladder") or {})}
    return out


def level_index(level: str | None) -> int:
    """:returns: *level*'s position in :data:`LEVELS` (``none`` is 0)."""
    return LEVELS.index(level) if level in LEVELS else 0


def decay(age_days: float, half_life_days: float) -> float:
    """:returns: The weight of evidence *age_days* old (``1`` when fresh)."""
    if half_life_days <= 0:
        return 1.0
    return math.pow(0.5, max(0.0, age_days) / half_life_days)


def _key(signal: FactorySignal) -> str:
    return signal.account or f"signal:{signal.number}"


def _weights(
    signals: Sequence[FactorySignal], *, now: int, cap: int, half_life_days: float
) -> dict[str, float]:
    """``{account: decayed weight}``: an account's freshest *cap* signals, each decayed."""
    by_account: dict[str, list[FactorySignal]] = {}
    for signal in signals:
        by_account.setdefault(_key(signal), []).append(signal)
    out: dict[str, float] = {}
    for account, rows in by_account.items():
        rows.sort(key=lambda s: s.seen_at, reverse=True)
        total = 0.0
        for row in rows[: max(cap, 1)]:
            total += decay((now - row.seen_at) / _DAY_S, half_life_days)
        out[account] = total
    return out


def _ago(seconds: int) -> str:
    days = seconds // _DAY_S
    if days <= 0:
        return "today"
    return "1 day ago" if days == 1 else f"{days} days ago"


def _reason(meter: Meter, now: int, gap: str | None) -> str:
    if meter.level == "none":
        return "no signals yet"
    if meter.level == "hunch":
        noun = "hunch" if meter.hunches == 1 else "hunches"
        return f"{meter.hunches} team {noun}, no user evidence yet"
    parts = [
        f"{meter.accounts} account{'s' if meter.accounts != 1 else ''}",
        f"{len(meter.source_kinds)} source type{'s' if len(meter.source_kinds) != 1 else ''}",
    ]
    if meter.do == 0:
        parts.append("no behaviour data yet")
    if meter.last_seen is not None:
        parts.append(f"newest {_ago(now - meter.last_seen)}")
    if gap:
        parts.append(gap)
    return " · ".join(parts)


def measure(linked: Sequence[Linked], rubric: Mapping[str, Any] | None, *, now: int) -> Meter:
    """
    The evidence meter of one opportunity.

    :param linked: Its attached signals (detached links already left out).
    :param rubric: ``discovery.rubric`` (defaults fill what is missing).
    """
    r = rubric_values(rubric)
    window_s = int(r["window_days"]) * _DAY_S
    half_life = float(r["decay_half_life_days"])
    cap = int(r["per_account_cap"])
    excluded = 0
    hunches = 0
    support: list[FactorySignal] = []
    against: list[FactorySignal] = []
    verdicts: list[tuple[int, str]] = []
    seen: list[int] = []
    for item in linked:
        signal = item.signal
        if signal.kind == "simulation" or INJECTION_SUSPECT in signal.flags:
            excluded += 1
            continue
        seen.append(signal.seen_at)
        if signal.kind == "test_result":
            verdict = str((signal.meta or {}).get("verdict") or "")
            if verdict in ("won", "lost") and not item.counter:
                verdicts.append((signal.seen_at, verdict))
        if signal.kind in CONVICTION_KINDS:
            hunches += 0 if item.counter else 1
            continue
        if now - signal.seen_at > window_s:
            continue
        (against if item.counter else support).append(signal)
    say = [s for s in support if s.kind not in DO_KINDS and s.say_do == "say"]
    do = [s for s in support if s.kind in DO_KINDS or s.say_do == "do"]
    say_weights = _weights(say, now=now, cap=cap, half_life_days=half_life)
    all_weights = _weights(support, now=now, cap=cap, half_life_days=half_life)
    counter_weights = _weights(against, now=now, cap=cap, half_life_days=half_life)
    say_kinds = sorted({s.source_kind for s in say})
    by_source: dict[str, set[str]] = {}
    for signal in support:
        by_source.setdefault(signal.source, set()).add(_key(signal))
    weight = sum(all_weights.values())
    counter_weight = sum(counter_weights.values())

    gap: str | None = None
    if not support:
        level = "hunch" if hunches else "none"
    else:
        worth = math.floor(sum(say_weights.values()) + 0.5)
        moderate_weight = worth >= int(r["moderate_min_accounts"])
        moderate_kinds = len(say_kinds) >= int(r["moderate_min_source_types"])
        level = "weak"
        if moderate_weight and moderate_kinds:
            level = "strong" if do else "moderate"
        elif not moderate_kinds:
            gap = f"moderate needs {int(r['moderate_min_source_types'])} source types"
        else:
            accounts = int(r["moderate_min_accounts"])
            gap = f"moderate needs {accounts} accounts' worth of recent evidence ({worth} now)"
        if counter_weight > 0 and counter_weight >= float(r["counter_share_down"]) * weight:
            level = LEVELS[max(level_index("hunch"), level_index(level) - 1)]
            gap = "counter-evidence lowers it one level"
    if verdicts:
        latest = max(verdicts)[1]
        if latest == "won":
            level = "proven"
            gap = None
        elif level_index(level) > level_index("weak"):
            level = "weak"
            gap = "a lost readout"
    ladder = r["ladder"]
    confidence = float(ladder.get(level, 0.0)) if level != "none" else 0.0
    last_seen = max(seen) if seen else None
    meter = Meter(
        level=level,
        confidence=confidence,
        accounts=len(all_weights),
        weight=weight,
        source_kinds=tuple(sorted({s.source_kind for s in support})),
        by_source={k: len(v) for k, v in sorted(by_source.items())},
        say=len(say_weights),
        do=len(do),
        counter=len(counter_weights),
        counter_weight=counter_weight,
        hunches=hunches,
        excluded=excluded,
        first_seen=min(seen) if seen else None,
        last_seen=last_seen,
        stale=last_seen is not None and now - last_seen > 2 * half_life * _DAY_S,
    )
    return replace(meter, reason=_reason(meter, now, gap))


def at_least(level: str, threshold: str) -> bool:
    """:returns: Whether *level* is *threshold* or above."""
    return level_index(level) >= level_index(threshold)


__all__ = [
    "LEVELS",
    "Linked",
    "Meter",
    "at_least",
    "decay",
    "level_index",
    "measure",
    "rubric_values",
]
