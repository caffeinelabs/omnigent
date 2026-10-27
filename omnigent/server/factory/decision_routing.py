"""Who a decision is for: assignees, claims, escalation and answer rights.

A decision's ``assignee`` is ``owners``, ``operators`` (also when ``NULL``) or
one person's user id. Operators answer decisions assigned to operators or to
themselves; owners (and the creator) answer any. A claim marks who is on it.

Escalation (supervisor rule S10): a blocking decision whose person has not
been seen for ``decisions.escalate_after_m`` minutes (30) widens to
``operators``; any still open after ``decisions.owners_after_m`` (120) goes to
``owners``. Each step emits ``decision.escalated``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from omnigent.server.auth import LEVEL_EDIT, LEVEL_MANAGE
from omnigent.server.factory import events
from omnigent.server.factory import presence as factory_presence
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryDecision, FactoryPerson

_logger = logging.getLogger(__name__)

ASSIGNEE_OWNERS: Final = "owners"
ASSIGNEE_OPERATORS: Final = "operators"
GROUP_ASSIGNEES: Final = frozenset({ASSIGNEE_OWNERS, ASSIGNEE_OPERATORS})
ASSIGNEE_MAX: Final = 128
SUBJECT_MAX: Final = 64

ESCALATE_AFTER_M: Final = 30
"""Minutes a person may be away before their decision widens to operators."""
OWNERS_AFTER_M: Final = 120
"""Minutes after which any open blocking decision goes to owners."""

_OWNER_SOURCES: Final = frozenset({"relaunch_failed", "storm", "access_request"})
_HUMAN_OWNER_SOURCES: Final = frozenset({"agent", "done_check"})
_OWNER_PROPOSAL_KINDS: Final = frozenset({"tuning", "rules", "code"})


def default_assignee(
    source: str,
    *,
    human_owner: str | None = None,
    proposal_kind: str | None = None,
    options: list[dict[str, Any]] | None = None,
) -> str:
    """
    Who a new decision goes to when its asker names nobody.

    :param source: The decision source, e.g. ``"agent"``.
    :param human_owner: The project charter's ``human_owner`` (agent and
        done-check decisions go to them).
    :param proposal_kind: For ``proposal`` decisions: ``projects`` goes to
        operators, ``tuning`` / ``rules`` / ``code`` to owners.
    :param options: A ``loop`` decision whose option raises a budget past the
        operator cap (``"owner_only": true``) goes to owners.
    :returns: ``owners``, ``operators`` or a user id.
    """
    if source in _HUMAN_OWNER_SOURCES:
        return human_owner or ASSIGNEE_OPERATORS
    if source in _OWNER_SOURCES:
        return ASSIGNEE_OWNERS
    if source == "proposal":
        return ASSIGNEE_OWNERS if proposal_kind in _OWNER_PROPOSAL_KINDS else ASSIGNEE_OPERATORS
    if source == "loop" and any(
        o.get("action") == "raise_budget" and o.get("owner_only") for o in options or []
    ):
        return ASSIGNEE_OWNERS
    return ASSIGNEE_OPERATORS


def human_owner_of(deps: FactoryDeps, factory_id: str, project: str | None) -> str | None:
    """:returns: The project's ``charter.human_owner`` when they are on the roster."""
    if not project:
        return None
    row = deps.factory_store.get_project(factory_id, project)
    owner = (row.charter or {}).get("human_owner") if row is not None else None
    if not isinstance(owner, str) or not owner.strip():
        return None
    person = _roster_match(deps.factory_store.list_people(factory_id), owner)
    return person.user_id if person is not None and person.role != "viewer" else None


def _roster_match(people: list[FactoryPerson], value: str) -> FactoryPerson | None:
    """A roster row by exact user id (case-insensitive), else by a unique local part."""
    wanted = value.strip().casefold()
    for person in people:
        if person.user_id.casefold() == wanted:
            return person
    if "@" in wanted:
        return None
    matches = [p for p in people if p.user_id.casefold().split("@", 1)[0] == wanted]
    return matches[0] if len(matches) == 1 else None


def resolve_for(
    deps: FactoryDeps, factory_id: str, value: str | None
) -> tuple[str | None, str | None]:
    """
    Read ``sys_factory_ask(for=…)``.

    :param value: ``owners``, ``operators``, a roster email (or its unique
        local part), or ``None``.
    :returns: ``(assignee, warning)``: ``(None, None)`` when *value* is empty
        (the default applies); an unknown name or a viewer becomes
        ``operators`` with a warning for the tool result.
    """
    text = (value or "").strip()
    if not text:
        return None, None
    if text.casefold() in GROUP_ASSIGNEES:
        return text.casefold(), None
    person = _roster_match(deps.factory_store.list_people(factory_id), text)
    if person is None:
        return ASSIGNEE_OPERATORS, (
            f"{text[:80]!r} is not on this factory's roster; the decision went to operators"
        )
    if person.role == "viewer":
        return ASSIGNEE_OPERATORS, (
            f"{person.user_id} is a viewer and cannot answer; the decision went to operators"
        )
    return person.user_id, None


def assignee_of(decision: FactoryDecision) -> str:
    """:returns: The decision's assignee (``operators`` when unset)."""
    return decision.assignee or ASSIGNEE_OPERATORS


def may_answer(decision: FactoryDecision, user_id: str | None, level: int) -> bool:
    """
    Whether a human at *level* on the root may answer (or claim) *decision*.

    :param level: Their level on the factory root.
    """
    if user_id is None or level >= LEVEL_MANAGE:
        return True
    if level < LEVEL_EDIT:
        return False
    assignee = assignee_of(decision)
    return assignee in (ASSIGNEE_OPERATORS, user_id) or decision.claimed_by == user_id


def is_mine(decision: FactoryDecision, user_id: str | None, level: int) -> bool:
    """
    Whether *decision* is assigned to *user_id* or to a role they hold.

    A claimed decision belongs to its claimer only.
    """
    if user_id is None:
        return True
    if decision.claimed_by:
        return decision.claimed_by == user_id
    assignee = assignee_of(decision)
    if assignee == ASSIGNEE_OPERATORS:
        return level >= LEVEL_EDIT
    if assignee == ASSIGNEE_OWNERS:
        return level >= LEVEL_MANAGE
    return assignee == user_id


# ── Escalation (S10) ───────────────────────────────────────────


@dataclass(frozen=True)
class Escalation:
    """One widening of a decision's assignee."""

    decision: FactoryDecision
    from_assignee: str
    to_assignee: str
    reason: str


def _settings(deps: FactoryDeps, factory_id: str) -> tuple[int, int]:
    """``(escalate_after_m, owners_after_m)`` from the factory definition, else the defaults."""
    escalate, owners = ESCALATE_AFTER_M, OWNERS_AFTER_M
    try:
        definition = deps.factory_store.latest_definition(factory_id)
    except Exception:  # noqa: BLE001 - the defaults apply
        definition = None
    block = (definition.doc or {}).get("decisions") if definition is not None else None
    if isinstance(block, dict):
        value = block.get("escalate_after_m")
        if isinstance(value, int) and value > 0:
            escalate = value
        value = block.get("owners_after_m")
        if isinstance(value, int) and value > 0:
            owners = value
    return escalate, owners


def _away_since(deps: FactoryDeps, decision: FactoryDecision, user_id: str) -> float | None:
    """When *user_id* was last seen, or ``None`` while they are present."""
    from omnigent.server.factory.people import last_seen_at

    if factory_presence.registry.is_present(decision.factory_id, user_id):
        return None
    seen = last_seen_at(deps, decision.factory_id, user_id) or 0.0
    started = decision.claimed_at if decision.claimed_by == user_id else decision.created_at
    return max(float(seen), float(started or decision.created_at))


def plan_escalations(deps: FactoryDeps, now: float) -> list[Escalation]:
    """
    The escalations due now across every factory (synchronous).

    :param now: Epoch seconds (the supervisor's clock).
    """
    rows = deps.factory_store.list_decisions(status="open", limit=1000)
    settings: dict[str, tuple[int, int]] = {}
    out: list[Escalation] = []
    for decision in rows:
        if not decision.blocking:
            continue
        if decision.factory_id not in settings:
            settings[decision.factory_id] = _settings(deps, decision.factory_id)
        escalate_m, owners_m = settings[decision.factory_id]
        assignee = assignee_of(decision)
        if assignee == ASSIGNEE_OWNERS:
            continue
        if now - decision.created_at >= owners_m * 60:
            out.append(Escalation(decision, assignee, ASSIGNEE_OWNERS, f"open {owners_m} min"))
            continue
        person = decision.claimed_by or (None if assignee in GROUP_ASSIGNEES else assignee)
        if person is None:
            continue
        away = _away_since(deps, decision, person)
        if away is not None and now - away >= escalate_m * 60:
            out.append(
                Escalation(
                    decision, assignee, ASSIGNEE_OPERATORS, f"{person} away {escalate_m} min"
                )
            )
    return out


async def escalate_decisions(app_state: Any, now: float) -> int:
    """
    Widen the assignees of blocking decisions nobody is answering (S10). Never raises.

    :param now: Epoch seconds (the supervisor's clock).
    :returns: Decisions escalated.
    """
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not isinstance(deps, FactoryDeps):
        return 0
    try:
        due = await asyncio.to_thread(plan_escalations, deps, now)
    except Exception:  # noqa: BLE001 - the next tick retries
        _logger.warning("factory: could not plan decision escalations", exc_info=True)
        return 0
    done = 0
    for step in due:
        decision = step.decision
        try:
            updated = await asyncio.to_thread(
                deps.factory_store.update_decision,
                decision.id,
                assignee=step.to_assignee,
                claimed_by=None,
                claimed_at=None,
            )
        except Exception:  # noqa: BLE001 - one bad row must not stop the others
            _logger.warning("factory: could not escalate %s", decision.ref, exc_info=True)
            continue
        if updated is None:
            continue
        done += 1
        await events.emit(
            app_state,
            decision.factory_id,
            "decision.escalated",
            severity="warning",
            project=decision.project_slug,
            subject=f"decision:{decision.ref}",
            summary=f"{decision.ref} widened to {step.to_assignee} ({step.reason})",
            data={
                "decision_id": decision.id,
                "from": step.from_assignee,
                "to": step.to_assignee,
                "reason": step.reason,
                "thread": decision.thread,
                "by": "server",
                "by_kind": "server",
            },
            ts=int(now * 1000),
        )
    return done


__all__ = [
    "ASSIGNEE_OPERATORS",
    "ASSIGNEE_OWNERS",
    "ESCALATE_AFTER_M",
    "GROUP_ASSIGNEES",
    "OWNERS_AFTER_M",
    "Escalation",
    "assignee_of",
    "default_assignee",
    "escalate_decisions",
    "human_owner_of",
    "is_mine",
    "may_answer",
    "plan_escalations",
    "resolve_for",
]
