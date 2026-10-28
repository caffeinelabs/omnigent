"""Project and factory dollar budgets, staged by the engine mode.

Spend comes from the cost ledger (``factory_cost_daily``): a project's total
over every day, and the factory's total for the current UTC day. The caps are
``budgets.project_usd`` (150; a charter's ``budgets.project_usd`` overrides
it) and ``budgets.factory_usd_per_day`` (400), read through
:func:`omnigent.server.factory.loops.budget`.

============ ============================== ================================
mode         80 %                           100 %
============ ============================== ================================
observe      ``budget.near``                ``budget.exceeded``
assist       ``budget.near``                ``budget.exceeded`` + a decision
                                            (pause, raise, dismiss)
enforce      ``budget.near``                ``budget.exceeded`` + pause with
                                            the note ``budget`` (a human
                                            resumes or raises)
============ ============================== ================================

Each threshold fires once per budget value (a project) or per day (the
factory); a raise lets it fire again at the new cap.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from omnigent.factory import ACTION_DISMISS, ACTION_PAUSE, ACTION_RAISE_BUDGET, LABEL_STATE
from omnigent.server.factory import control, events, server_decisions
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.loops import NEAR_FRACTION, budget, engine_mode
from omnigent.stores.factory_store import norm_id

_logger = logging.getLogger(__name__)

PAUSE_NOTE: Final = "budget"
SCOPE_FACTORY: Final = "factory"


@dataclass(frozen=True)
class Spend:
    """
    One budget scope's spend.

    :param scope: ``project:<slug>`` or ``factory``.
    :param period: ``all`` (projects) or the UTC day (the factory).
    """

    scope: str
    project: str | None
    usd: float
    limit: float
    period: str

    @property
    def fraction(self) -> float:
        """:returns: Spend over the cap (0 when the cap is 0)."""
        return self.usd / self.limit if self.limit > 0 else 0.0


def utc_day(now: float) -> str:
    """:returns: ``YYYY-MM-DD`` of *now* in UTC."""
    return datetime.fromtimestamp(now, tz=UTC).strftime("%Y-%m-%d")


def project_limit(deps: FactoryDeps, factory_id: str, slug: str) -> float:
    """:returns: The project $ cap: the charter's ``budgets.project_usd``, else the definition."""
    project = deps.factory_store.get_project(factory_id, slug)
    charter = (project.charter or {}) if project is not None else {}
    found = charter.get("budgets") if isinstance(charter.get("budgets"), Mapping) else {}
    value = found.get("project_usd")
    if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
        return float(value)
    return float(budget(factory_id, "budgets", "project_usd"))


def spends(deps: FactoryDeps, factory_id: str, now: float) -> list[Spend]:
    """Every active project's total spend and the factory's spend today (synchronous)."""
    store = deps.factory_store
    day = utc_day(now)
    rows = store.list_cost_daily(factory_id)
    by_project: dict[str, float] = {}
    today = 0.0
    for row in rows:
        if row.project_slug:
            by_project[row.project_slug] = by_project.get(row.project_slug, 0.0) + row.cost_usd
        if row.day_utc == day:
            today += row.cost_usd
    out: list[Spend] = []
    for project in store.list_projects(factory_id):
        if project.status not in (None, "active"):
            continue
        usd = round(by_project.get(project.slug, 0.0), 2)
        out.append(
            Spend(
                f"project:{project.slug}",
                project.slug,
                usd,
                project_limit(deps, factory_id, project.slug),
                "all",
            )
        )
    limit = float(budget(factory_id, "budgets", "factory_usd_per_day"))
    out.append(Spend(SCOPE_FACTORY, None, round(today, 2), limit, day))
    return out


# ── Once per threshold ─────────────────────────────────────────

_fired: dict[tuple[str, str, str, str, float], None] = {}
_FIRED_MAX: Final = 10000


def _key(factory_id: str, spend: Spend, kind: str) -> tuple[str, str, str, str, float]:
    return (norm_id(factory_id), spend.scope, spend.period, kind, spend.limit)


def _already(deps: FactoryDeps, factory_id: str, spend: Spend, kind: str) -> bool:
    """Whether *kind* fired for this scope, period and cap (in memory, else the event log)."""
    key = _key(factory_id, spend, kind)
    if key in _fired:
        return True
    since = (
        0
        if spend.period == "all"
        else int(
            datetime.strptime(spend.period, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000
        )
    )
    found = deps.factory_store.find_events(
        factory_id, kind, since, f"budget:{spend.scope}", limit=50
    )
    if any((e.data or {}).get("limit") == spend.limit for e in found):
        _remember(key)
        return True
    return False


def _remember(key: tuple[str, str, str, str, float]) -> None:
    if len(_fired) > _FIRED_MAX:
        _fired.clear()
    _fired[key] = None


# ── The check ──────────────────────────────────────────────────


async def check(app_state: Any, factory_id: str, *, now: float | None = None) -> list[str]:
    """
    Fire the 80 % and 100 % budget rules of one factory by its engine mode. Never raises.

    :returns: The event kinds emitted (tests).
    """
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps):
        return []
    at = time.time() if now is None else now
    emitted: list[str] = []
    try:
        mode = await asyncio.to_thread(engine_mode, deps, factory_id)
        rows = await asyncio.to_thread(spends, deps, factory_id, at)
        for spend in rows:
            if spend.limit <= 0:
                continue
            for kind, threshold in (("budget.exceeded", 1.0), ("budget.near", NEAR_FRACTION)):
                if spend.fraction < threshold:
                    continue
                if await asyncio.to_thread(_already, deps, factory_id, spend, kind):
                    break
                _remember(_key(factory_id, spend, kind))
                await _fire(app_state, deps, factory_id, spend, kind, mode, at)
                emitted.append(kind)
                break
    except Exception:  # noqa: BLE001 - the next tick retries
        _logger.warning("factory budgets: check failed for %s", factory_id, exc_info=True)
    return emitted


def _what(spend: Spend) -> str:
    if spend.project is not None:
        return f"project {spend.project}"
    return f"the factory today ({spend.period})"


async def _fire(
    app_state: Any,
    deps: FactoryDeps,
    factory_id: str,
    spend: Spend,
    kind: str,
    mode: str,
    now: float,
) -> None:
    exceeded = kind == "budget.exceeded"
    pct = round(spend.fraction * 100)
    summary = f"{_what(spend)} spent ${spend.usd:.2f} of ${spend.limit:.0f} ({pct} %)"
    action = None
    if exceeded and mode == "enforce":
        action = "pause"
    elif exceeded and mode == "assist":
        action = "decision"
    await events.emit(
        app_state,
        factory_id,
        kind,
        severity="warning" if exceeded else "notice",
        project=spend.project,
        subject=f"budget:{spend.scope}",
        summary=summary,
        data={
            "scope": spend.scope,
            "usd": spend.usd,
            "limit": spend.limit,
            "period": spend.period,
            "mode": mode,
            "action": action,
            "by": "server",
            "by_kind": "server",
        },
        ts=int(now * 1000),
    )
    if action == "pause":
        await _auto_pause(app_state, deps, factory_id, spend, summary)
    elif action == "decision":
        await _decision(app_state, factory_id, spend, summary)


def _target(spend: Spend) -> str:
    return f"project:{spend.project}" if spend.project is not None else control.TARGET_FACTORY


async def _auto_pause(
    app_state: Any, deps: FactoryDeps, factory_id: str, spend: Spend, summary: str
) -> None:
    """Pause the project (or the factory) as the server, with the note ``budget``."""
    if spend.project is None:
        root = await asyncio.to_thread(deps.conversation_store.get_conversation, factory_id)
        if root is not None and root.labels.get(LABEL_STATE) == "paused":
            return
    else:
        pl = await asyncio.to_thread(control.find_project_pl, deps, factory_id, spend.project)
        if pl is None or pl.labels.get(LABEL_STATE) == "paused":
            return
    root = await asyncio.to_thread(deps.conversation_store.get_conversation, factory_id)
    if root is None:
        return
    try:
        # The server acts here, not a person: no grant to check (planned like plan_control).
        resolved = await control.resolve_target(deps, root, _target(spend))
        plan = control.ControlPlan(
            root=root,
            action=control.ACTION_PAUSE,
            target=resolved,
            by="server (budget)",
            note=PAUSE_NOTE,
            options={"keep_previews": True},
        )
        plan.recipients = control._recipients(control.ACTION_PAUSE, resolved)
        if any(control.same_id(m.id, root.id) for m in plan.recipients):
            plan.root_reply_to = await asyncio.to_thread(
                control._root_reply_address,
                deps,
                root,
            )
    except Exception:  # noqa: BLE001 - e.g. nothing left to pause; the event stands
        _logger.warning(
            "factory budgets: could not plan the pause of %s", spend.scope, exc_info=True
        )
        return
    if not plan.recipients:
        return
    await control.execute_control(app_state, plan)
    _logger.info("factory budgets: paused %s (%s)", spend.scope, summary)


async def _decision(app_state: Any, factory_id: str, spend: Spend, summary: str) -> None:
    """Ask owners to pause or raise (assist)."""
    target = _target(spend)
    options: list[dict[str, Any]] = [
        {
            "id": "pause",
            "label": "Pause",
            "action": ACTION_PAUSE,
            "target": target,
            "recommended": True,
        },
    ]
    if spend.project is not None:
        options.append(
            {
                "id": "raise",
                "label": f"Raise to ${spend.limit * 2:.0f}",
                "action": ACTION_RAISE_BUDGET,
                "target": f"budget:{target}",
                "usd": spend.limit * 2,
                "owner_only": True,
            }
        )
    options.append({"id": "dismiss", "label": "Keep going", "action": ACTION_DISMISS})
    await server_decisions.open_decision(
        app_state,
        factory_id,
        source="loop",
        dedupe_key=f"budget:{spend.scope}:{spend.period}"[:64],
        question=f"{summary}. Pause or raise?",
        context="The engine mode is assist: nothing is paused until someone decides.",
        options=options,
        blocking=True,
        project=spend.project,
        asked_by=None,
        assignee="owners",
    )


async def raise_project(app_state: Any, factory_id: str, slug: str, usd: float) -> float:
    """Set a project's $ cap in its charter (an owner's raise). :returns: The new cap."""
    deps = getattr(app_state, FACTORY_STATE_ATTR)
    store = deps.factory_store
    project = await asyncio.to_thread(store.get_project, factory_id, slug)
    if project is None:
        raise ValueError(f"no project {slug!r}")
    charter = dict(project.charter or {})
    found = charter.get("budgets") if isinstance(charter.get("budgets"), Mapping) else {}
    charter["budgets"] = {**found, "project_usd": round(float(usd), 2)}
    await asyncio.to_thread(store.update_project, project.id, charter=charter)
    return float(usd)


def reset() -> None:
    """Forget fired thresholds (tests)."""
    _fired.clear()


__all__ = [
    "PAUSE_NOTE",
    "Spend",
    "check",
    "project_limit",
    "raise_project",
    "reset",
    "spends",
    "utc_day",
]
