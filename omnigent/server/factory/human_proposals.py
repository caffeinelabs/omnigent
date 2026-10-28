"""Projects proposals a person files from the cockpit (``C-n``, ``author_kind=human``).

A structural edit made on the Graph page (a project's ``depends_on``) goes
through the same proposal path as an agent's: it is validated like one,
mirrored as a ``proposal`` decision in its thread, and applied, edited or
rejected from its card, so it stays audited and reversible. Nothing
auto-applies: the author (or anyone allowed) presses Apply.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from omnigent.factory import THREAD_FACTORY, THREAD_MAX_LEN
from omnigent.server.auth import LEVEL_EDIT
from omnigent.server.factory import control, events, projects, proposals
from omnigent.server.factory.actor import proposal_ref
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.start import parse_body

RATIONALE_MAX: Final = 2000


class HumanProposalIn(BaseModel):
    """``POST /v1/factory/factories/{fid}/proposals``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    changes: list[dict[str, Any]] = Field(min_length=1, max_length=proposals.MAX_CHANGES)
    rationale: str = Field(min_length=1, max_length=RATIONALE_MAX)
    thread: str | None = Field(None, max_length=THREAD_MAX_LEN)


async def propose_human(
    app_state: Any, factory_id: str, user_id: str | None, payload: Mapping[str, Any]
) -> dict[str, Any]:
    """
    File a projects proposal as a person (EDIT on the root).

    :returns: ``{proposal: ProposalView, decision: "D-n"}``.
    :raises CockpitError: The validation errors of :func:`proposals.validate`.
    """
    body = parse_body(HumanProposalIn, payload)
    deps = factory_deps(app_state)
    root = await control.load_factory_root(deps, user_id, factory_id)
    await control.require(deps, user_id, root.id, LEVEL_EDIT)
    plan = await asyncio.to_thread(
        proposals.validate, deps, root, body.changes, reason=body.rationale
    )
    actor = await asyncio.to_thread(projects.human_actor, deps, user_id, root.id)
    approval = "owner" if plan.owner_reasons() else "operator"
    row, _ = await asyncio.to_thread(
        lambda: deps.factory_store.create_proposal(
            root.id,
            kind=proposals.KIND_PROJECTS,
            title=plan.title(),
            author_kind="human",
            author=actor.id,
            approval=approval,
            ops=plan.ops,
            rationale=body.rationale,
            warnings=plan.warnings,
            author_role=actor.role,
            thread=body.thread or THREAD_FACTORY,
            based_on=plan.based_on,
        )
    )
    ref = proposal_ref(row.number)
    by = projects.actor_name(actor)
    await events.emit(
        app_state,
        root.id,
        "proposal.opened",
        severity="notice",
        subject=f"proposal:{ref}",
        summary=f"{by} proposed {ref}: {row.title}",
        data={**actor.event_data(), "proposal_id": row.id, "approval": approval},
    )
    decision = await proposals._mirror(app_state, root, row, plan, by)
    row = (
        await asyncio.to_thread(
            deps.factory_store.update_proposal, row.id, decision_id=decision.id
        )
        or row
    )
    view = await asyncio.to_thread(proposals.proposal_view, deps, row)
    return {"proposal": view, "decision": decision.ref}


__all__ = ["HumanProposalIn", "propose_human"]
