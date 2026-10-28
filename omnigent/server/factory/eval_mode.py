"""Eval mode: a benchmark factory that runs without waiting for people.

A factory whose root carries ``omnigent.factory.mode=eval`` (the temp-main
benchmark, ``examples/factory/benchmarks/temp-main/*.json``):

- every open decision older than 2 minutes is answered with its recommended
  option (else its first actionable one), recorded as ``answered_by="eval-auto"``,
  except the ones only an owner may approve (access requests, Tier-1 changes,
  owner-approval projects proposals, budget raises past the operator cap);
- every project stops at READY: its PL is paused once its stage reaches
  ``ready`` (humans close the PRs afterwards with the reason ``test_run``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import (
    ACTION_DISMISS,
    ACTION_RAISE_BUDGET,
    LABEL_MODE,
    MODE_EVAL,
    STAGE_READY,
)
from omnigent.server.factory import control, events
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import CockpitError

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryDecision

_logger = logging.getLogger(__name__)

ANSWER_AFTER_S: Final = 120
EVAL_BY: Final = "eval-auto"
STOPPED_KIND: Final = "eval.stopped"
AUTO_TEXT: Final = "eval-auto: proceed with your own recommendation."
SOURCE_ACCESS_REQUEST: Final = "access_request"
SOURCE_PROPOSAL: Final = "proposal"


def is_eval(labels: Mapping[str, str]) -> bool:
    """:returns: Whether a root's labels put its factory in eval mode."""
    return (labels.get(LABEL_MODE) or "").strip() == MODE_EVAL


def pick_option(options: list[dict[str, Any]]) -> dict[str, Any] | None:
    """:returns: The recommended option, else the first that is not a dismissal."""
    recommended = [o for o in options if o.get("recommended")]
    if recommended:
        return recommended[0]
    actionable = [o for o in options if o.get("action") != ACTION_DISMISS and not o.get("href")]
    return actionable[0] if actionable else None


def owner_only(
    deps: FactoryDeps, decision: FactoryDecision, option: Mapping[str, Any] | None
) -> bool:
    """Whether answering *decision* with *option* needs an owner (eval mode leaves it open)."""
    from omnigent.server.factory import tuning
    from omnigent.server.factory.proposals import find_proposal

    if decision.source == SOURCE_ACCESS_REQUEST:
        return True
    option = option or {}
    if option.get("action") == ACTION_RAISE_BUDGET and option.get("owner_only"):
        return True
    if decision.source != SOURCE_PROPOSAL:
        return False
    ref = (decision.subject or "").removeprefix(tuning.SUBJECT_PREFIX)
    row = find_proposal(deps, decision.factory_id, ref) if ref else None
    return row is None or tuning.is_tier1(row) or row.approval == "owner"


async def auto_answer(app_state: Any, factory_id: str, now: float) -> list[str]:
    """Answer the factory's decisions open for 2 minutes. :returns: Their refs."""
    from omnigent.server.factory import decisions
    from omnigent.server.factory.people import creator_of

    deps = factory_deps(app_state)
    rows = await asyncio.to_thread(
        lambda: deps.factory_store.list_decisions(status="open", factory_id=factory_id, limit=200)
    )
    creator = await asyncio.to_thread(creator_of, deps, factory_id)
    answered: list[str] = []
    for decision in rows:
        if now - decision.created_at < ANSWER_AFTER_S:
            continue
        option = pick_option(list(decision.options or []))
        if await asyncio.to_thread(owner_only, deps, decision, option):
            continue
        reasons = (option or {}).get("reasons")
        try:
            await decisions.answer_decision(
                app_state,
                decision.id,
                user_id=creator,
                option_id=str(option["id"]) if option is not None else None,
                text=None if option is not None else AUTO_TEXT,
                reason="test_run" if isinstance(reasons, list) and "test_run" in reasons else None,
                answered_by=EVAL_BY,
            )
        except CockpitError as exc:
            _logger.info("factory eval: %s not answered: %s", decision.ref, exc.code)
            continue
        except Exception:  # noqa: BLE001 - one decision must not stop the rest
            _logger.warning("factory eval: answering %s failed", decision.ref, exc_info=True)
            continue
        answered.append(decision.ref)
    return answered


async def stop_ready(app_state: Any, factory_id: str) -> list[str]:
    """Pause the PL of every project that reached READY (once each). :returns: The slugs."""
    from omnigent.server.factory.people import creator_of

    deps = factory_deps(app_state)
    projects = await asyncio.to_thread(deps.factory_store.list_projects, factory_id)
    creator = await asyncio.to_thread(creator_of, deps, factory_id)
    stopped: list[str] = []
    for project in projects:
        if project.status != "active" or project.stage != STAGE_READY:
            continue
        subject = f"project:{project.slug}"
        seen = await asyncio.to_thread(
            deps.factory_store.find_events, factory_id, STOPPED_KIND, 0, subject, limit=1
        )
        if seen:
            continue
        try:
            await control.apply_control(
                app_state,
                factory_id,
                user_id=creator,
                action="pause",
                target=subject,
                note="eval mode: the project reached READY; the benchmark stops here",
            )
        except Exception:  # noqa: BLE001 - recorded below; a person can pause it instead
            _logger.warning("factory eval: pausing %s failed", project.slug, exc_info=True)
        await events.emit(
            app_state,
            factory_id,
            STOPPED_KIND,
            severity="notice",
            project=project.slug,
            subject=subject,
            summary=f"{project.slug} reached READY · eval mode paused its PL",
            data={"by": EVAL_BY, "by_kind": "server"},
        )
        stopped.append(project.slug)
    return stopped


async def tick(app_state: Any, factory_id: str, now: float) -> dict[str, list[str]]:
    """Run eval mode for one factory (nothing unless its root says ``mode=eval``)."""
    deps = factory_deps(app_state)
    root = await control.get_conv(deps, factory_id)
    if root is None or not is_eval(root.labels):
        return {"answered": [], "stopped": []}
    return {
        "answered": await auto_answer(app_state, factory_id, now),
        "stopped": await stop_ready(app_state, factory_id),
    }


__all__ = [
    "ANSWER_AFTER_S",
    "EVAL_BY",
    "STOPPED_KIND",
    "auto_answer",
    "is_eval",
    "owner_only",
    "pick_option",
    "stop_ready",
    "tick",
]
