"""The gates a pull request passes before a person merges it (people; read only).

- ``GET /v1/factory/factories/{fid}/gates``: every ready gate of
  :data:`omnigent.factory_gates.READY_GATES` and the demo gate, in order, with
  when it is required, the role a team needs for it
  (:data:`.team_spec.GATE_NEEDS`), what it checks, the labels that skip or
  allow it, and the protected paths. Steer › Gates shows it; it is why some
  team edits are locked.

Gates are Tier 0: nothing in the definition changes them, and no route here
writes. Any person who can read the factory reads them.
"""

from __future__ import annotations

from typing import Any, Final

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from omnigent.factory_gates import (
    DEMO_GATE,
    LABEL_ALLOW_PROTECTED,
    LABEL_NO_ENV,
    LABEL_REVERT,
    PROTECTED_PATHS,
    READY_GATES,
)
from omnigent.server.factory import control, team_spec
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.routes._auth_helpers import get_user_id

router = APIRouter(prefix="/v1/factory", tags=["factory"])

_PROTECTED: Final = ", ".join(PROTECTED_PATHS)

GATE_TEXT: Final[dict[str, tuple[str, str]]] = {
    "draft": ("Ready for review", "The pull request is marked ready for review, not a draft."),
    "base": ("Right base branch", "It targets its project's base branch."),
    "checks": (
        "Checks pass",
        "The checks the base branch requires (or every check, when it requires none) "
        "have reported and none failed.",
    ),
    "review": (
        "Review",
        "A reviewer approved the newest commit: its review marker was posted after that commit.",
    ),
    "verify": (
        "Verify",
        "The env keeper ran the change in an environment and it passed.",
    ),
    "mergeable": ("No conflicts", "The code host reports the branch mergeable."),
    "protected_paths": (
        "Protected paths",
        f"It touches no protected path ({_PROTECTED}) unless a person allows it.",
    ),
    DEMO_GATE: ("Demo", "The newest demo of the head passed (PASS or PARTIAL)."),
}
"""Each gate's name and what it checks, in words."""

_REQUIRED: Final[dict[str, str]] = {DEMO_GATE: "previews"}
_REQUIRED_TEXT: Final = {
    "always": "every pull request",
    "previews": "while the project has previews on",
}
_SKIPS: Final[dict[str, list[str]]] = {
    "verify": [LABEL_NO_ENV, LABEL_REVERT],
    DEMO_GATE: [LABEL_NO_ENV],
}
_ALLOWS: Final[dict[str, str]] = {"protected_paths": LABEL_ALLOW_PROTECTED}
_NEED_WHEN: Final = {
    "always": "on every team",
    "env": "when the project needs an environment",
    "previews": "while the project has previews on",
}
NOTE: Final = (
    "Gates are Tier 0: no definition change, trial or agent proposal changes them. A project "
    "cannot lose the only member a gate needs, which is why some team edits are locked. Making "
    "gate policy a per-project owner setting would be a deliberate Tier-0 change."
)


def _need(gate: str) -> team_spec.GateNeed | None:
    return next((n for n in team_spec.GATE_NEEDS if n.gate == gate), None)


def gates_view() -> dict[str, Any]:
    """
    :returns: ``{gates[], team_needs[], protected_paths[], labels, tier, note}``;
        each gate ``{gate, label, checks, required, required_text, role, role_label,
        role_when, role_text, skipped_by[], allowed_by}``.
    """
    out: list[dict[str, Any]] = []
    for gate in (*READY_GATES, DEMO_GATE):
        label, checks = GATE_TEXT.get(gate, (gate.replace("_", " ").capitalize(), ""))
        required = _REQUIRED.get(gate, "always")
        need = _need(gate)
        out.append(
            {
                "gate": gate,
                "label": label,
                "checks": checks,
                "required": required,
                "required_text": _REQUIRED_TEXT[required],
                "role": need.role if need else None,
                "role_label": team_spec.ROLE_LABELS.get(need.role) if need else None,
                "role_when": need.when if need else None,
                "role_text": need.text if need else None,
                "skipped_by": list(_SKIPS.get(gate, [])),
                "allowed_by": _ALLOWS.get(gate),
            }
        )
    gates = {g["gate"] for g in out}
    team_needs = [
        {
            "gate": need.gate,
            "role": need.role,
            "role_label": team_spec.ROLE_LABELS.get(need.role, need.role),
            "when": need.when,
            "when_text": _NEED_WHEN[need.when],
            "text": need.text,
            "pr_gate": need.gate in gates,
        }
        for need in team_spec.GATE_NEEDS
    ]
    return {
        "gates": out,
        "team_needs": team_needs,
        "protected_paths": list(PROTECTED_PATHS),
        "labels": {
            "no_env": LABEL_NO_ENV,
            "revert": LABEL_REVERT,
            "allow_protected": LABEL_ALLOW_PROTECTED,
        },
        "tier": 0,
        "note": NOTE,
    }


@router.get("/factories/{factory_id}/gates", response_model=None)
async def get_gates(request: Request, factory_id: str) -> dict[str, Any] | JSONResponse:
    """Every gate a pull request passes before a merge, with the role each needs (read only)."""
    try:
        deps = factory_deps(request.app.state)
        user_id = get_user_id(request, deps.auth_provider)
        await control.load_factory_root(deps, user_id, factory_id)
        return gates_view()
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())


__all__ = ["GATE_TEXT", "NOTE", "gates_view", "router"]
