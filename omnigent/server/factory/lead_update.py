"""A project lead's structured update, and the project's health computed from facts.

A project lead posts its update on itself with
``sys_factory_set(update={health, done, next, blocker})``; the server keeps the
latest one on the project row (``factory_projects.lead_update``). The cockpit
shows it next to the health the server computes from facts it holds (the
project stopped, failing checks, spend against the budget, stuck members, an
open question blocking it). Facts never make a project look better than its
lead says, but they override a lead's "on track" (or "at risk") when they say
worse. An update older than :data:`STALE_AFTER_S` is stale: the facts show
instead and the cockpit greys the chip.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final, Literal

HEALTH_ON_TRACK: Final = "on_track"
HEALTH_AT_RISK: Final = "at_risk"
HEALTH_OFF_TRACK: Final = "off_track"
HEALTH_LEVELS: Final = (HEALTH_ON_TRACK, HEALTH_AT_RISK, HEALTH_OFF_TRACK)
"""Best first; a level's index is its severity."""

Health = Literal["on_track", "at_risk", "off_track"]

TEXT_MAX: Final = 280
"""Each of ``done``, ``next`` and ``blocker`` is one short line."""
STALE_AFTER_S: Final = 3 * 3600
"""An update older than this is stale; the facts speak instead."""
BUDGET_RISK: Final = 0.8
"""Spend at or above this share of the budget puts a project at risk."""

_FIELDS: Final = ("done", "next", "blocker")


class UpdateError(ValueError):
    """An update the server refuses, with the field that is wrong."""

    def __init__(self, message: str, field: str) -> None:
        super().__init__(message)
        self.field = field


def _line(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise UpdateError(f"update.{field} must be a string", field)
    text = " ".join(value.split())
    if len(text) > TEXT_MAX:
        raise UpdateError(f"update.{field} is at most {TEXT_MAX} characters", field)
    return text or None


def parse_update(raw: Any) -> dict[str, Any]:
    """
    Validate an update as a lead sends it.

    :param raw: ``{health, done?, next?, blocker?}``, e.g. ``{"health":
        "on_track", "done": "T-3 merged", "next": "T-4 review"}``.
    :returns: The update with each text on one line and empty fields dropped.
    :raises UpdateError: A missing or unknown health, an unknown key, a field
        that is not a string or is too long, or an update that says nothing.
    """
    if not isinstance(raw, Mapping):
        raise UpdateError("update must be an object {health, done, next, blocker}", "update")
    unknown = sorted(set(raw) - {"health", *_FIELDS})
    if unknown:
        raise UpdateError(f"update has unknown fields: {', '.join(unknown)}", unknown[0])
    health = raw.get("health")
    if health not in HEALTH_LEVELS:
        raise UpdateError(f"update.health must be one of {list(HEALTH_LEVELS)}", "health")
    out: dict[str, Any] = {"health": health}
    for field in _FIELDS:
        text = _line(raw.get(field), field)
        if text:
            out[field] = text
    if len(out) == 1:
        raise UpdateError("an update says what got done, what is next or what blocks it", "done")
    return out


def stored(update: Mapping[str, Any], *, by: str, at: int) -> dict[str, Any]:
    """:returns: The row's ``lead_update``: the parsed update, when, and the lead's session id."""
    return {**update, "at": at, "by": by}


def _worse(a: str, b: str) -> str:
    return a if HEALTH_LEVELS.index(a) >= HEALTH_LEVELS.index(b) else b


def _pct(share: float) -> str:
    return f"{round(share * 100)} %"


def computed_health(
    project: Mapping[str, Any],
    prs: Sequence[Mapping[str, Any]],
    members: Sequence[Mapping[str, Any]],
) -> tuple[str, list[str], list[str]]:
    """
    The project's health from facts the server holds.

    :param project: The snapshot's project view (stage, overlay, cost, team spec, charter).
    :param prs: The snapshot's PR views of this project.
    :param members: The snapshot's member views of this project.
    :returns: ``(health, facts, reasons)``: every fact in plain words
        (``"checks green"``, ``"budget at 42 %"``), and the facts that make it
        worse than on track.
    """
    health = HEALTH_ON_TRACK
    facts: list[str] = []
    reasons: list[str] = []

    def flag(level: str, text: str) -> None:
        nonlocal health
        health = _worse(level, health)
        reasons.append(text)

    overlay = project.get("overlay")
    if overlay == "failed":
        flag(HEALTH_OFF_TRACK, "the project stopped")
    elif overlay == "blocked":
        flag(HEALTH_AT_RISK, "an open question blocks it")

    open_prs = [p for p in prs if p.get("state") == "open"]
    failing = [p for p in open_prs if (p.get("gates") or {}).get("checks") == "fail"]
    if failing:
        refs = ", ".join(f"#{p.get('number')}" for p in failing[:3])
        text = f"checks failing on {refs}"
        facts.append(text)
        flag(HEALTH_AT_RISK, text)
    elif open_prs and all((p.get("gates") or {}).get("checks") == "pass" for p in open_prs):
        facts.append("checks green")
    elif open_prs:
        facts.append("checks running")

    budget = _budget(project)
    spent = project.get("cost_usd")
    if budget and isinstance(spent, int | float):
        share = spent / budget
        text = f"budget at {_pct(share)}"
        facts.append(text)
        if share >= 1:
            flag(HEALTH_OFF_TRACK, f"over budget ({_pct(share)})")
        elif share >= BUDGET_RISK:
            flag(HEALTH_AT_RISK, text)

    stuck = [m for m in members if m.get("stuck")]
    if stuck:
        text = f"{len(stuck)} {'member' if len(stuck) == 1 else 'members'} stuck"
        facts.append(text)
        flag(HEALTH_AT_RISK, text)
    dead = [m for m in members if m.get("health") == "dead" and m.get("is_top_level")]
    if dead and overlay != "failed":
        facts.append("the project lead is down")
        flag(HEALTH_OFF_TRACK, "the project lead is down")
    return health, facts, reasons


def _budget(project: Mapping[str, Any]) -> float | None:
    spec = project.get("team_spec") or {}
    value = spec.get("budget_usd") if isinstance(spec, Mapping) else None
    if isinstance(value, int | float) and value > 0:
        return float(value)
    return None


def views(
    project: Mapping[str, Any],
    lead_update: Mapping[str, Any] | None,
    prs: Sequence[Mapping[str, Any]],
    members: Sequence[Mapping[str, Any]],
    *,
    budget_usd: float | None = None,
    now: float,
    handle: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """
    The snapshot's ``update`` (the lead's) and ``health`` of one project.

    :param budget_usd: The charter's project budget, when the team spec sets none.
    :param handle: The lead's handle (``by`` names its session otherwise).
    :returns: ``(update view or None, health view or None)``; a closed project has neither.
    """
    if project.get("status") not in (None, "active") or project.get("kind") == "integration":
        return None, None
    scoped = dict(project)
    if _budget(scoped) is None and budget_usd:
        scoped["team_spec"] = {**(scoped.get("team_spec") or {}), "budget_usd": budget_usd}
    computed, facts, reasons = computed_health(scoped, prs, members)
    update: dict[str, Any] | None = None
    lead = None
    if isinstance(lead_update, Mapping) and lead_update.get("health") in HEALTH_LEVELS:
        at = lead_update.get("at")
        update = {
            "health": lead_update["health"],
            "done": lead_update.get("done"),
            "next": lead_update.get("next"),
            "blocker": lead_update.get("blocker"),
            "at": int(at) if isinstance(at, int | float) else 0,
            "by": handle or lead_update.get("by"),
        }
        lead = update["health"]
    stale = update is not None and now - update["at"] > STALE_AFTER_S
    if lead is None or stale:
        shown, source = computed, "facts"
    else:
        shown = _worse(computed, lead)
        source = "lead" if shown == lead else "facts"
    health = {
        "health": shown,
        "source": source,
        "computed": computed,
        "facts": facts,
        "reasons": reasons,
        "agrees": None if lead is None else lead == computed,
        "stale": stale,
        "budget_usd": _budget(scoped),
    }
    return update, health


__all__ = [
    "HEALTH_AT_RISK",
    "HEALTH_LEVELS",
    "HEALTH_OFF_TRACK",
    "HEALTH_ON_TRACK",
    "STALE_AFTER_S",
    "TEXT_MAX",
    "UpdateError",
    "computed_health",
    "parse_update",
    "stored",
    "views",
]
