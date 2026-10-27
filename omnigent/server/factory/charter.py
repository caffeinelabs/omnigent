"""Pure renderers for the texts a factory starts from.

``render_kickoff_brief`` is what the root lead receives when a human starts a
factory from the cockpit; ``render_pl_charter`` is the CHARTER block appended
to a PL's spawn brief, rendered from the stored project row so a lead cannot
mangle it. ``normalize_charter`` gives the stored charter one shape.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from omnigent.entities.factory import FactoryProject
from omnigent.factory import (
    CADENCE_DEMO,
    CADENCE_HEARTBEATS,
    PROJECT_KIND_INTEGRATION,
    PROJECT_KIND_WORK,
    PROJECT_KINDS,
    PROJECT_SLUG_RE,
    ROLE_LEAD,
    ROLE_PROJECT_LEAD,
)

_logger = logging.getLogger(__name__)

PREVIEW_TOOLING_REF_ENV: Final = "OMNIGENT_FACTORY_PREVIEW_TOOLING_REF"
# The branch that carried the preview tooling before it reached app main; it is deleted.
RETIRED_PREVIEW_TOOLING_REFS: Final = frozenset(
    {"factory/preview-tooling", "origin/factory/preview-tooling"}
)
DEFAULT_PREVIEW_SLOTS: Final = 2
MAX_PREVIEW_SLOTS: Final = 3
DEFAULT_BUDGET_ICS: Final = 3
DEFAULT_BASE_BRANCH: Final = "main"
FIXED_DOD: Final = "PR ready for human merge"
DEPENDS_MERGED: Final = "merged"
DEPENDS_READY: Final = "ready"
DEPENDS_MODES: Final = (DEPENDS_MERGED, DEPENDS_READY)
"""``depends_mode``: a dependency counts once its PR merged, or once it is ready for merge."""
SPAWN_SERVER: Final = "server"
SPAWN_LEAD: Final = "lead"
SPAWN_OWNERS: Final = (SPAWN_SERVER, SPAWN_LEAD)
"""``spawn_owner``: who starts the project's PL (the server, or the lead from a brief)."""
DEFAULT_BASE_ENV: Final = "OMNIGENT_FACTORY_DEFAULT_BASE"
"""``owner/repo#branch`` new factories default to, e.g. ``caffeinelabs/app#temp-main``."""

REPO_RE: Final = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# A branch git accepts: no leading "-", "." or "/", no "..", "//" or "/.", no ".lock"
# component and no trailing "/" or ".".
BRANCH_RE: Final = re.compile(
    r"^(?![-./])(?!.*\.\.)(?!.*//)(?!.*/\.)(?!.*\.lock(?:/|$))(?!.*[/.]$)[A-Za-z0-9._/-]{1,255}$"
)


def server_default_base() -> tuple[str | None, str | None]:
    """
    The server's default repo and base for new factories (``OMNIGENT_FACTORY_DEFAULT_BASE``).

    :returns: e.g. ``("caffeinelabs/app", "temp-main")``; ``(None, None)`` when
        unset or malformed; ``(repo, None)`` for a bare ``owner/repo``.
    """
    raw = os.environ.get(DEFAULT_BASE_ENV, "").strip()
    if not raw:
        return None, None
    repo, _, branch = (part.strip() for part in raw.partition("#"))
    if not REPO_RE.match(repo) or (branch and not BRANCH_RE.match(branch)):
        _logger.warning("factory: ignoring malformed %s=%r", DEFAULT_BASE_ENV, raw)
        return None, None
    return repo, branch or None


def resolve_base_branch(
    value: str | None, repo: str | None, default_repo: str | None, default_base: str | None
) -> str:
    """
    The base branch a project targets.

    The project's own value wins, then the factory's default base (only for
    the factory's default repo, when one is set), then ``main``.

    :returns: e.g. ``"temp-main"``.
    """
    own = _text(value)
    if own:
        return own
    same_repo = not default_repo or not repo or repo.lower() == default_repo.lower()
    if default_base and same_repo:
        return default_base
    return DEFAULT_BASE_BRANCH


def preview_tooling_ref() -> str | None:
    """:returns: The server's preview tooling ref knob, or ``None`` when unset."""
    value = os.environ.get(PREVIEW_TOOLING_REF_ENV, "").strip()
    return value or None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _tooling_ref(value: Any) -> str | None:
    text = _text(value)
    return None if text in RETIRED_PREVIEW_TOOLING_REFS else text


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


def _slugs(value: Any) -> list[str]:
    out: list[str] = []
    for item in _texts(value):
        if PROJECT_SLUG_RE.match(item) and item not in out:
            out.append(item)
    return out


def _budgets(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, float] = {}
    for key in ("task_usd", "project_usd"):
        raw = value.get(key)
        if isinstance(raw, int | float) and not isinstance(raw, bool) and raw >= 0:
            out[key] = float(raw)
    return out


def normalize_charter(
    raw: Mapping[str, Any] | None, *, tooling_ref: str | None = None
) -> dict[str, Any]:
    """
    Return a charter in its one stored shape.

    ``preview`` and ``demo_focus`` are authoritative; the older ``env_needed``,
    ``preview_slots`` and ``demo`` keys are read as fallbacks and written back
    to match. The fixed DoD item is never stored (it is always rendered). A
    retired preview tooling ref is dropped, so env_keeper falls back to ``main``.

    :param raw: A charter as submitted (start form, PATCH, backfill), or ``None``.
    :param tooling_ref: Preview tooling ref to record when previews are on.
    :returns: ``{kind, goal, dod, scope_paths, constraints, env_needed,
        preview{enabled, slots, tooling_ref?}, preview_slots, demo_focus, demo,
        budget_ics, version_path?, health_path?, depends_on?, depends_mode?,
        human_owner?, budgets?, spawn_owner?}``; ``kind`` is ``work`` unless it is
        one of :data:`~omnigent.factory.PROJECT_KINDS`. The optional keys are
        stored only when set.
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
    ref = _tooling_ref(preview_in.get("tooling_ref")) or _tooling_ref(tooling_ref)
    if enabled and ref:
        preview["tooling_ref"] = ref
    demo_focus = _text(src.get("demo_focus")) or _text(src.get("demo"))
    kind = src.get("kind")
    out: dict[str, Any] = {
        "kind": kind if kind in PROJECT_KINDS else PROJECT_KIND_WORK,
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
    depends_on = _slugs(src.get("depends_on"))
    if depends_on:
        out["depends_on"] = depends_on
        mode = src.get("depends_mode")
        out["depends_mode"] = mode if mode in DEPENDS_MODES else DEPENDS_MERGED
    owner = _text(src.get("human_owner"))
    if owner:
        out["human_owner"] = owner
    budgets = _budgets(src.get("budgets"))
    if budgets:
        out["budgets"] = budgets
    if src.get("spawn_owner") in SPAWN_OWNERS:
        out["spawn_owner"] = src["spawn_owner"]
    return out


def project_base_branch(project: FactoryProject) -> str:
    """:returns: The branch the project's PRs target, e.g. ``"temp-main"`` (default ``main``)."""
    return _text(project.base_branch) or DEFAULT_BASE_BRANCH


def _repo_ref(project: FactoryProject) -> str:
    repo = project.repo or "(repo not set)"
    return f"{repo}#{project_base_branch(project)}"


def _base_line(project: FactoryProject, charter: Mapping[str, Any]) -> str:
    base = project_base_branch(project)
    env = f"; the dev env tracks origin/{base}" if charter["env_needed"] else ""
    return f"Base branch: {base} (open every PR against it{env})"


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


def project_kind(project: FactoryProject) -> str:
    """:returns: The project's ``charter.kind``: ``work``, ``integration`` or ``self``."""
    return normalize_charter(project.charter)["kind"]


def _integration_charter(project: FactoryProject, charter: Mapping[str, Any]) -> str:
    """The CHARTER of an integration project: a live env at the base head, nothing else."""
    base = project_base_branch(project)
    return "\n".join(
        [
            f"CHARTER · {project.slug}",
            (
                f"KIND: integration · keep a live env at the head of {_repo_ref(project)}; "
                "no tasks, ICs or PRs"
            ),
            f"Title: {project.title} · priority P{project.priority}",
            f"Goal: {charter['goal'] or f'Keep the env at the head of origin/{base}.'}",
            f"Repo#branch: {_repo_ref(project)}",
            f"Base branch: {base} (the env tracks origin/{base}; redeploy it when the base moves)",
            "Env needed: yes · factory previews: off",
            (
                "Merge policy: human. You open no PRs and merge nothing; humans merge into "
                f"{base} on GitHub."
            ),
        ]
    )


def render_pl_charter(project: FactoryProject) -> str:
    """
    Render the CHARTER block for a project's PL.

    :param project: The stored project row.
    :returns: Plain text starting ``CHARTER · <slug>``.
    """
    charter = normalize_charter(project.charter)
    if charter["kind"] == PROJECT_KIND_INTEGRATION:
        return _integration_charter(project, charter)
    repo = project.repo or "the repo"
    lines = [
        f"CHARTER · {project.slug}",
        f"Title: {project.title} · priority P{project.priority}",
        f"Goal: {charter['goal'] or '(ask your lead)'}",
        "Definition of Done:",
        f"- {_fixed_dod(charter)}",
        *(f"- {item}" for item in charter["dod"]),
        f"Repo#branch: {_repo_ref(project)} · branches factory/{project.slug}/<task>",
        _base_line(project, charter),
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
        *_optional_lines(charter),
        (
            f"Merge policy: human. Humans merge {repo}; the factory never merges. Report "
            "READY FOR HUMAN MERGE <url> to your lead once every gate is green; never run "
            "`gh pr merge` or the merge gate."
        ),
    ]
    return "\n".join(lines)


def _optional_lines(charter: Mapping[str, Any]) -> list[str]:
    """Charter lines for fields only some projects set (none for most)."""
    lines: list[str] = []
    if charter.get("depends_on"):
        until = (
            "ready for human merge"
            if charter.get("depends_mode") == DEPENDS_READY
            else "merged into the base"
        )
        lines.append(
            f"Depends on: {', '.join(charter['depends_on'])} (the server starts you once "
            f"each is {until})"
        )
    if charter.get("human_owner"):
        lines.append(f"Human owner: {charter['human_owner']}")
    budgets = charter.get("budgets") or {}
    if budgets:
        parts = [
            f"{label} ${budgets[key]:g}"
            for key, label in (("task_usd", "per task"), ("project_usd", "project"))
            if key in budgets
        ]
        lines.append(f"Budgets: {' · '.join(parts)}")
    return lines


def project_depends_on(project: FactoryProject) -> tuple[list[str], str]:
    """:returns: ``(depends_on slugs, depends_mode)`` of the project's charter."""
    charter = normalize_charter(project.charter)
    return list(charter.get("depends_on") or []), charter.get("depends_mode", DEPENDS_MERGED)


def spawn_owner(project: FactoryProject) -> str | None:
    """:returns: ``server`` or ``lead`` when the charter records who spawns the PL."""
    return normalize_charter(project.charter).get("spawn_owner")


def _project_line(index: int, project: FactoryProject) -> str:
    charter = normalize_charter(project.charter)
    if charter["kind"] == PROJECT_KIND_INTEGRATION:
        env = "integration env (heartbeat 0)"
    elif not charter["env_needed"]:
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
    brief: str | None = None,
    held: Sequence[str] = (),
    reply_to: str | None = None,
) -> str:
    """
    Render the brief the root lead receives when a human starts a factory.

    :param name: Factory name, e.g. ``"Atlas factory"``.
    :param cadence: ``demo`` or ``normal``.
    :param human: Display name of the human who started it.
    :param projects: The stored project rows, in start order.
    :param factory_id: Root session id, for the cockpit link.
    :param brief: The human's description of the work, when they gave one.
    :param held: Projects waiting on dependencies: the server spawns their PLs.
    :param reply_to: The brief's mail id, which proposals answering it cite.
    :returns: Plain text (the server adds the envelope).
    """
    beats = CADENCE_HEARTBEATS.get(cadence, CADENCE_HEARTBEATS[CADENCE_DEMO])
    lead_s, pl_s = beats[ROLE_LEAD], beats[ROLE_PROJECT_LEAD]
    integration = [p.slug for p in projects if project_kind(p) == PROJECT_KIND_INTEGRATION]
    planning = len(integration) == len(projects)
    lines = [
        f"FACTORY BRIEF · {name}",
        (
            f"Started by {human} from the cockpit. Cadence: {cadence} "
            f"(heartbeats: leads {lead_s} s, PLs {pl_s} s)."
        ),
    ]
    if factory_id:
        lines.append(f"Cockpit: /factory/{factory_id}")
    if projects:
        lines += [
            "",
            (
                f"Projects ({len(projects)}). Their charters are stored on the server and "
                "attach automatically when you spawn each PL:"
            ),
            *(_project_line(i, p) for i, p in enumerate(projects, start=1)),
        ]
    if brief:
        lines += ["", f"Brief from {human}:", brief.strip()]

    def spawn(heartbeat_s: int) -> str:
        return (
            'sys_factory_spawn(agent="factory-pl", role="project_lead", project="<slug>", '
            'title="PL · <slug>", workspaces=["https://github.com/<repo>#<branch>"], '
            f'heartbeat_s={heartbeat_s}, message="Start. Your charter follows.").'
        )

    if planning:
        step2 = (
            "2. No projects yet: plan them from the brief. Propose them in ONE call, "
            'sys_factory_project(changes=[{op: "create", project: {slug, title, goal, dod, '
            "scope_paths, demo_focus, depends_on}}, …], rationale=<why this split>"
            + (f', reply_to="{reply_to}"' if reply_to else "")
            + "): one project per independently reviewable outcome, shared groundwork "
            "first and the rest depends_on it. The server applies it (or asks the human) "
            "and spawns each PL itself; never sys_factory_spawn them."
        )
        if integration:
            step2 += f" Spawn {', '.join(integration)} yourself: {spawn(0)}"
    else:
        step2 = (
            f"2. Spawn one PL per project, in the order above, never a duplicate: {spawn(pl_s)}"
            + (
                f" Spawn {', '.join(integration)} the same way but with heartbeat_s=0: "
                "it keeps the live env at its base head and wakes only on notices."
                if integration
                else ""
            )
            + (
                f" Do not spawn {', '.join(held)}: each waits for its depends_on, and the "
                "server spawns its PL then."
                if held
                else ""
            )
        )
    lines += [
        "",
        "First turn:",
        (
            f"1. sys_factory_set(heartbeat_s={lead_s}). If the roster has no other lead, "
            f"spawn Sentinel (playbook charter, heartbeat_s={lead_s})."
        ),
        step2,
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
    "BRANCH_RE",
    "DEFAULT_BASE_BRANCH",
    "DEFAULT_BASE_ENV",
    "DEPENDS_MERGED",
    "DEPENDS_MODES",
    "DEPENDS_READY",
    "FIXED_DOD",
    "PREVIEW_TOOLING_REF_ENV",
    "REPO_RE",
    "RETIRED_PREVIEW_TOOLING_REFS",
    "SPAWN_LEAD",
    "SPAWN_OWNERS",
    "SPAWN_SERVER",
    "normalize_charter",
    "preview_tooling_ref",
    "project_base_branch",
    "project_depends_on",
    "project_kind",
    "render_kickoff_brief",
    "render_pl_charter",
    "resolve_base_branch",
    "server_default_base",
    "spawn_owner",
]
