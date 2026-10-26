"""Pure renderers for the texts a factory starts from.

``render_kickoff_brief`` is what the root lead receives when a human starts a
factory from the cockpit; ``render_pl_charter`` is the CHARTER block appended
to a PL's spawn brief, rendered from the stored project row so a lead cannot
mangle it. ``normalize_charter`` gives the stored charter one shape.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any, Final

from omnigent.entities.factory import FactoryProject
from omnigent.factory import CADENCE_DEMO, CADENCE_HEARTBEATS, ROLE_LEAD, ROLE_PROJECT_LEAD

PREVIEW_TOOLING_REF_ENV: Final = "OMNIGENT_FACTORY_PREVIEW_TOOLING_REF"
DEFAULT_PREVIEW_SLOTS: Final = 2
MAX_PREVIEW_SLOTS: Final = 3
DEFAULT_BUDGET_ICS: Final = 3
FIXED_DOD: Final = "PR ready for human merge"


def preview_tooling_ref() -> str | None:
    """:returns: The server's preview tooling ref knob, or ``None`` when unset."""
    value = os.environ.get(PREVIEW_TOOLING_REF_ENV, "").strip()
    return value or None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _texts(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    items = (_text(v) for v in value)
    return [v for v in items if v and v.casefold() != FIXED_DOD.casefold()]


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def normalize_charter(
    raw: Mapping[str, Any] | None, *, tooling_ref: str | None = None
) -> dict[str, Any]:
    """
    Return a charter in its one stored shape.

    ``preview`` and ``demo_focus`` are authoritative; the older ``env_needed``,
    ``preview_slots`` and ``demo`` keys are read as fallbacks and written back
    to match. The fixed DoD item is never stored (it is always rendered).

    :param raw: A charter as submitted (start form, PATCH, backfill), or ``None``.
    :param tooling_ref: Preview tooling ref to record when previews are on.
    :returns: ``{goal, dod, scope_paths, constraints, env_needed, preview{enabled,
        slots, tooling_ref?}, preview_slots, demo_focus, demo, budget_ics,
        version_path?, health_path?}``.
    """
    src = dict(raw or {})
    preview_in = src.get("preview") if isinstance(src.get("preview"), Mapping) else {}
    env_needed = bool(src.get("env_needed", preview_in.get("enabled", True)))
    enabled = bool(preview_in.get("enabled", env_needed)) and env_needed
    slots = _int(
        preview_in.get("slots", src.get("preview_slots")),
        DEFAULT_PREVIEW_SLOTS,
        1,
        MAX_PREVIEW_SLOTS,
    )
    preview: dict[str, Any] = {"enabled": enabled, "slots": slots}
    ref = _text(preview_in.get("tooling_ref")) or tooling_ref
    if enabled and ref:
        preview["tooling_ref"] = ref
    demo_focus = _text(src.get("demo_focus")) or _text(src.get("demo"))
    out: dict[str, Any] = {
        "goal": _text(src.get("goal")) or "",
        "dod": _texts(src.get("dod")),
        "scope_paths": _texts(src.get("scope_paths")),
        "constraints": _text(src.get("constraints")),
        "env_needed": env_needed,
        "preview": preview,
        "preview_slots": slots,
        "demo_focus": demo_focus,
        "demo": demo_focus,
        "budget_ics": _int(src.get("budget_ics"), DEFAULT_BUDGET_ICS, 1, 10),
    }
    for key in ("version_path", "health_path"):
        path = _text(src.get(key))
        if path:
            out[key] = path
    return out


def _repo_ref(project: FactoryProject) -> str:
    repo = project.repo or "(repo not set)"
    return f"{repo}#{project.base_branch or 'main'}"


def _env_line(charter: Mapping[str, Any]) -> str:
    if not charter["env_needed"]:
        return "Env needed: no · label PRs factory:no-env (no preview, no demo)"
    preview = charter["preview"]
    if not preview["enabled"]:
        return "Env needed: yes · factory previews: off"
    line = (
        "Env needed: yes · factory previews: on, one per runnable PR at its head, "
        f"up to {preview['slots']} at once"
    )
    if preview.get("tooling_ref"):
        line += f" · preview tooling ref {preview['tooling_ref']}"
    return line


def _fixed_dod(charter: Mapping[str, Any]) -> str:
    proof = "CI green, review and verify markers at the head"
    if charter["env_needed"] and charter["preview"]["enabled"]:
        proof += ", live preview and a recorded demo"
    return f"{FIXED_DOD}: {proof}"


def render_pl_charter(project: FactoryProject) -> str:
    """
    Render the CHARTER block for a project's PL.

    :param project: The stored project row.
    :returns: Plain text starting ``CHARTER · <slug>``.
    """
    charter = normalize_charter(project.charter)
    repo = project.repo or "the repo"
    lines = [
        f"CHARTER · {project.slug}",
        f"Title: {project.title} · priority P{project.priority}",
        f"Goal: {charter['goal'] or '(ask your lead)'}",
        "Definition of Done:",
        f"- {_fixed_dod(charter)}",
        *(f"- {item}" for item in charter["dod"]),
        f"Repo#branch: {_repo_ref(project)} · branches factory/{project.slug}/<task>",
        f"Scope paths: {', '.join(charter['scope_paths']) or '(whole repo)'}",
        f"Constraints: {charter['constraints'] or 'none beyond the repo AGENTS.md rules'}",
        _env_line(charter),
    ]
    probes = [
        f"{label}: {charter[key]}"
        for key, label in (("version_path", "Version path"), ("health_path", "Health path"))
        if charter.get(key)
    ]
    if probes:
        lines.append(" · ".join(probes))
    if charter["env_needed"] and charter["preview"]["enabled"]:
        lines.append(
            f"Demo focus: {charter['demo_focus'] or 'the user-visible change of each PR'}"
        )
    else:
        lines.append("Demo focus: none (no env)")
    lines += [
        f"IC budget: up to {charter['budget_ics']} ICs at once",
        (
            f"Merge policy: human. Humans merge {repo}; the factory never merges. Report "
            "READY FOR HUMAN MERGE <url> to your lead once every gate is green; never run "
            "`gh pr merge` or the merge gate."
        ),
    ]
    return "\n".join(lines)


def _project_line(index: int, project: FactoryProject) -> str:
    charter = normalize_charter(project.charter)
    if not charter["env_needed"]:
        env = "no env"
    elif charter["preview"]["enabled"]:
        env = f"env + previews ({charter['preview']['slots']})"
    else:
        env = "env, no previews"
    head = (
        f"{index}. {project.slug} · {project.title} · P{project.priority} · "
        f"{_repo_ref(project)} · {env}"
    )
    return f"{head}\n   Goal: {charter['goal'] or '(none given)'}"


def render_kickoff_brief(
    name: str,
    cadence: str,
    human: str,
    projects: Sequence[FactoryProject],
    *,
    factory_id: str | None = None,
) -> str:
    """
    Render the brief the root lead receives when a human starts a factory.

    :param name: Factory name, e.g. ``"Atlas factory"``.
    :param cadence: ``demo`` or ``normal``.
    :param human: Display name of the human who started it.
    :param projects: The stored project rows, in start order.
    :param factory_id: Root session id, for the cockpit link.
    :returns: Plain text (the server adds the envelope).
    """
    beats = CADENCE_HEARTBEATS.get(cadence, CADENCE_HEARTBEATS[CADENCE_DEMO])
    lead_s, pl_s = beats[ROLE_LEAD], beats[ROLE_PROJECT_LEAD]
    lines = [
        f"FACTORY BRIEF · {name}",
        (
            f"Started by {human} from the cockpit. Cadence: {cadence} "
            f"(heartbeats: leads {lead_s} s, PLs {pl_s} s)."
        ),
    ]
    if factory_id:
        lines.append(f"Cockpit: /factory/{factory_id}")
    lines += [
        "",
        (
            f"Projects ({len(projects)}). Their charters are stored on the server and "
            "attach automatically when you spawn each PL:"
        ),
        *(_project_line(i, p) for i, p in enumerate(projects, start=1)),
        "",
        "First turn:",
        (
            f"1. sys_factory_set(heartbeat_s={lead_s}). If the roster has no other lead, "
            f"spawn Sentinel (playbook charter, heartbeat_s={lead_s})."
        ),
        (
            "2. Spawn one PL per project, in the order above, never a duplicate: "
            'sys_factory_spawn(agent="factory-pl", role="project_lead", project="<slug>", '
            'title="PL · <slug>", workspaces=["https://github.com/<repo>#<branch>"], '
            f'heartbeat_s={pl_s}, message="Start. Your charter follows.").'
        ),
        (
            "3. Reply with at most 5 lines and the cockpit link. Questions for the human "
            "go through sys_factory_ask, not chat."
        ),
        "",
        (
            "Guardrails: humans merge; the factory never merges and never runs "
            "`gh pr merge` or the merge gate. PLs report READY FOR HUMAN MERGE <url>."
        ),
    ]
    return "\n".join(lines)


__all__ = [
    "FIXED_DOD",
    "PREVIEW_TOOLING_REF_ENV",
    "normalize_charter",
    "preview_tooling_ref",
    "render_kickoff_brief",
    "render_pl_charter",
]
