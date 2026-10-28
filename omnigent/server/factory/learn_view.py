"""Read models for the cockpit's Insights and Improvements pages.

- version markers: the factory's definition versions and the platform
  versions (image, server sha) its members ran, each with when it started;
- bundle drift: members whose agent bundle differs from the one the server
  seeds now (they run an older prompt; a relaunch picks up the new one);
- a trial's scorecard: baseline vs trial, guards and target, as
  :func:`.evaluate.evaluate_trial` computes them (the stored verdict once decided).
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import LABEL_BUNDLE, LABEL_PROJECT, LABEL_ROLE, LABEL_STATE, STATE_DONE
from omnigent.server.factory import control
from omnigent.server.factory.actor import proposal_ref
from omnigent.server.factory.envelope import display_title
from omnigent.server.factory.telemetry import versions

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryProposal
    from omnigent.server.factory.context import FactoryDeps

DEF_VERSIONS_MAX: Final = 50
PLATFORM_VERSIONS_MAX: Final = 20
_SCORED: Final = frozenset({"trial", "kept", "reverted"})
SCORECARD_TTL_S: Final = 60
"""A live scorecard is reused this long: every open card refetches on each full frame."""
_SCORECARD_MAX: Final = 256
_scorecards: dict[tuple[str, int], tuple[int, dict[str, Any]]] = {}
_scorecards_lock = threading.Lock()


def _proposal_refs(deps: FactoryDeps, ids: Iterable[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for proposal_id in ids:
        row = deps.factory_store.get_proposal(proposal_id)
        if row is not None:
            out[proposal_id] = proposal_ref(row.number)
    return out


def version_markers(deps: FactoryDeps, factory_id: str) -> dict[str, Any]:
    """
    :returns: ``{definition: [{version, created_at, created_via, created_by,
        proposal_ref, pinned}], platform: [{id, created_at, image_tag,
        server_sha, current}]}``, oldest first.
    """
    from omnigent.server.factory import definition

    store = deps.factory_store
    pinned = definition.pinned_version(deps, factory_id)
    rows = store.list_definitions(factory_id, limit=DEF_VERSIONS_MAX)
    refs = _proposal_refs(deps, {r.proposal_id for r in rows if r.proposal_id})
    defs = [
        {
            "version": r.version,
            "created_at": r.created_at,
            "created_via": r.created_via,
            "created_by": r.created_by,
            "proposal_ref": refs.get(r.proposal_id) if r.proposal_id else None,
            "pinned": r.version == pinned,
        }
        for r in sorted(rows, key=lambda r: r.version)
    ]
    current = versions.current_version_id()
    platform = [
        {
            "id": v.id,
            "created_at": v.created_at,
            "image_tag": v.image_tag,
            "server_sha": v.server_sha,
            "current": v.id == current,
        }
        for v in sorted(
            store.list_versions(limit=PLATFORM_VERSIONS_MAX), key=lambda v: v.created_at
        )
    ]
    return {"definition": defs, "platform": platform}


def bundle_drift(
    deps: FactoryDeps, factory_id: str, hidden_projects: frozenset[str] = frozenset()
) -> dict[str, Any]:
    """
    :returns: ``{members: [{session_id, title, bundle (the agent's name), role,
        project, running, current, drift}], drifted}`` for the live members of the
        factory (hidden projects left out).
    """
    from omnigent.server.factory.membership import factory_members

    out: list[dict[str, Any]] = []
    for member in factory_members(factory_id, deps.conversation_store):
        labels = member.labels
        project = labels.get(LABEL_PROJECT) or None
        if project and project in hidden_projects:
            continue
        if labels.get(LABEL_STATE) == STATE_DONE or control.is_closed(member):
            continue
        running = labels.get(LABEL_BUNDLE) or None
        current = versions.labels_for_agent(deps.agent_store, member.agent_id).get(LABEL_BUNDLE)
        agent = deps.agent_store.get(member.agent_id) if member.agent_id else None
        out.append(
            {
                "session_id": member.id,
                "title": display_title(member.title, member.id),
                "bundle": agent.name if agent is not None else None,
                "role": labels.get(LABEL_ROLE) or None,
                "project": project,
                "running": running,
                "current": current,
                "drift": bool(running and current and running != current),
            }
        )
    out.sort(key=lambda m: (not m["drift"], m["title"].casefold()))
    return {"members": out, "drifted": sum(1 for m in out if m["drift"])}


def scorecard(deps: FactoryDeps, row: FactoryProposal, now: int) -> dict[str, Any] | None:
    """
    :returns: A trial's evaluation (live while on trial, the stored one once
        decided), or ``None`` for a proposal that never ran as a trial.
    """
    from omnigent.server.factory import evaluate

    if row.kind not in ("tuning", "rules") or row.status not in _SCORED:
        return None
    stored = (row.result or {}).get("evaluation")
    if row.status != "trial" and isinstance(stored, dict):
        return {**stored, "live": False}
    if row.status != "trial" and row.trial_until is None:
        return None
    # Keyed on the row's last update, so a keep, revert or edit is never served stale.
    key = (row.id, int(row.updated_at or 0))
    with _scorecards_lock:
        hit = _scorecards.get(key)
    if hit is not None and now - hit[0] < SCORECARD_TTL_S:
        return hit[1]
    card = {**evaluate.evaluate_trial(deps, row, now).to_dict(), "live": row.status == "trial"}
    with _scorecards_lock:
        if len(_scorecards) >= _SCORECARD_MAX:
            _scorecards.clear()
        _scorecards[key] = (now, card)
    return card


__all__ = ["bundle_drift", "scorecard", "version_markers"]
