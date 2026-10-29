"""Definition changes a person proposes for the owners (``C-n``, ``author_kind=human``).

Owners edit the definition directly (``PUT …/definition``). Operators cannot,
but they may "Propose change" from a finding, an Insights tile or an
incident: the change is validated like an agent's, stored pending with its
evidence, and an owners ``proposal`` decision opens in ``#improvements``.
Approving it applies the change as a trial; nothing applies on its own.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Final, Literal

from pydantic import Field

from omnigent.factory import THREAD_IMPROVEMENTS
from omnigent.server.auth import LEVEL_EDIT
from omnigent.server.factory import control, definition, projects, tuning
from omnigent.server.factory.actor import proposal_ref
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.proposals import proposal_view
from omnigent.server.factory.start import parse_body

RATIONALE_MAX: Final = 2000


class HumanChangeIn(tuning._Body):
    """``POST /v1/factory/factories/{fid}/definition/proposals``."""

    kind: Literal["tuning", "rules"] = "tuning"
    changes: list[dict[str, Any]] = Field(min_length=1, max_length=tuning.MAX_CHANGES)
    rationale: str = Field(min_length=1, max_length=RATIONALE_MAX)
    evidence: list[definition.EvidenceId] = Field(
        default_factory=list, max_length=tuning.MAX_EVIDENCE
    )
    expected_effect: tuning.EffectIn | None = None
    trial_h: int | None = Field(None, ge=definition.TRIAL_H_MIN, le=definition.TRIAL_H_MAX)


async def propose_change(
    app_state: Any, factory_id: str, user_id: str | None, payload: Mapping[str, Any] | None
) -> dict[str, Any]:
    """
    File a tuning or rules proposal as a person (EDIT on the root); owners decide.

    :returns: ``{proposal: ProposalView, decision: "D-n"}``.
    :raises CockpitError: 400 ``invalid_body`` / ``no_change``, 409 ``path_busy``,
        and the 422 ``tier0`` / ``out_of_bounds`` refusals of the definition.
    """
    body = parse_body(HumanChangeIn, payload if payload is not None else {})
    deps = factory_deps(app_state)
    root = await control.load_factory_root(deps, user_id, factory_id)
    await control.require(deps, user_id, root.id, LEVEL_EDIT)
    changes = tuning._changes(body.changes)
    is_rules = all(c.path == "rules" for c in changes)
    if body.kind == "rules" and not is_rules:
        raise CockpitError(400, "invalid_body", "a rules proposal changes only rules")
    if body.kind == "tuning" and any(c.path == "rules" for c in changes):
        raise CockpitError(400, "invalid_body", "rules go in a rules proposal")
    effect = tuning._effect(body.expected_effect)
    resolved = await asyncio.to_thread(definition.definition_for, deps, root.id, fresh=True)
    assert resolved is not None
    after = definition.apply_changes(resolved.stored, changes, rule_state="trial")
    diff = tuning.preview(definition.diff(resolved.stored, after))
    if not diff:
        raise CockpitError(400, "no_change", "the definition already has these values")
    await asyncio.to_thread(tuning._check_overlap, deps, root.id, diff, resolved.stored)
    actor = await asyncio.to_thread(projects.human_actor, deps, user_id, root.id)
    trial_h = body.trial_h or definition.TRIAL_H_DEFAULT
    row, _ = await asyncio.to_thread(
        lambda: deps.factory_store.create_proposal(
            root.id,
            kind=body.kind,
            title=tuning._title(diff, body.kind),
            author_kind="human",
            author=actor.id,
            approval="owner",
            class_="B",
            ops=tuning._stored_ops(changes),
            **tuning._pending_fields(diff, resolved.version),
            rationale=body.rationale,
            evidence=list(body.evidence),
            expected_effect=effect,
            author_role=actor.role,
            thread=THREAD_IMPROVEMENTS,
            result={"trial_h": trial_h},
        )
    )
    by = tuning.projects_name(actor)
    ref = proposal_ref(row.number)
    await tuning._emit(
        app_state,
        row,
        "proposal.opened",
        f"{by} proposed {ref} ({row.kind}): {row.title}",
        actor,
        approval=row.approval,
        evidence=row.evidence,
    )
    decision = await tuning._open_decision(app_state, root, row, by, definition.diff_lines(diff))
    row = (
        await asyncio.to_thread(
            deps.factory_store.update_proposal, row.id, decision_id=decision.id
        )
        or row
    )
    return {
        "proposal": await asyncio.to_thread(proposal_view, deps, row),
        "decision": decision.ref,
    }


__all__ = ["HumanChangeIn", "propose_change"]
