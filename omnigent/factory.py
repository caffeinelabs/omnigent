"""Software-factory vocabulary shared by the server and the runner.

A factory is a labelled tree of ordinary sessions: two leads, one project lead
(PL) per project (each a top-level managed session in its own sandbox), and
the PLs' co-located sub-agent ICs. Membership and supervision live entirely in
``conversation_labels`` (no schema change); sub-agents inherit membership from
their root conversation, and their role comes from their spec's
``executor.config.factory_role``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any, Final

LABEL_PREFIX: Final = "omnigent.factory."

LABEL_ID: Final = "omnigent.factory.id"
"""Session id of the factory root (the first lead)."""
LABEL_ROLE: Final = "omnigent.factory.role"
LABEL_PARENT: Final = "omnigent.factory.parent"
"""Session id of the member that spawned this one (not a parent_session_id link)."""
LABEL_PROJECT: Final = "omnigent.factory.project"
LABEL_SUPERVISOR: Final = "omnigent.factory.supervisor"
"""Session id that receives this member's alerts."""
LABEL_STATE: Final = "omnigent.factory.state"
LABEL_HEARTBEAT_S: Final = "omnigent.factory.heartbeat_s"
"""LLM heartbeat interval in seconds; ``"0"`` disables heartbeats."""
LABEL_LAST_BEAT: Final = "omnigent.factory.last_beat"
"""ISO-8601 time of the last delivered heartbeat (restart-safe supervisor state)."""
LABEL_SUMMARY: Final = "omnigent.factory.summary"
LABEL_HEALTH: Final = "omnigent.factory.health"
LABEL_NAME: Final = "omnigent.factory.name"
"""Display name of the factory (root only), e.g. ``"Atlas factory"``."""
LABEL_CADENCE: Final = "omnigent.factory.cadence"
"""Heartbeat cadence of the factory (root only): ``demo`` or ``normal``."""
LABEL_FOLDER_PROJECT: Final = "omnigent.factory.folder_project"
"""Id of the sidebar project folder the factory's sessions are filed into (root only)."""
FOLDER_DELETED: Final = "deleted"
"""``folder_project`` value once the owner deleted the folder: it is not recreated."""
LABEL_STARTED_AT: Final = "omnigent.factory.started_at"
"""Epoch seconds the factory was started from the cockpit (root only)."""
LABEL_BEAT_N: Final = "omnigent.factory.beat_n"
"""Number of the last delivered heartbeat (top-level; survives a server roll)."""
LABEL_DEAD_ALERTED_AT: Final = "omnigent.factory.dead_alerted_at"
"""Epoch seconds of the last dead-member alert (top-level; dedupes across rolls)."""
LABEL_KEEP_PREVIEWS: Final = "omnigent.factory.keep_previews"
"""``"0"`` lets a paused PL's pod idle out despite live previews (PL only)."""
LABEL_DEFAULT_REPO: Final = "omnigent.factory.default_repo"
"""Repo new projects default to (root only), e.g. ``"caffeinelabs/app"``."""
LABEL_DEFAULT_BASE: Final = "omnigent.factory.default_base"
"""Base branch new projects of the default repo target (root only), e.g. ``"temp-main"``."""
LABEL_BASE_HEAD_PREFIX: Final = "omnigent.factory.base_head."
"""Root label prefix recording a watched base's head: ``<sha> <moved_at> <repo>#<base>``."""

PROJECT_KIND_WORK: Final = "work"
PROJECT_KIND_INTEGRATION: Final = "integration"
PROJECT_KIND_SELF: Final = "self"
PROJECT_KINDS: Final = (PROJECT_KIND_WORK, PROJECT_KIND_INTEGRATION, PROJECT_KIND_SELF)
"""``charter.kind``: product work, the live env at the base head, or factory self-work."""
INTEGRATION_ENV_SLUG: Final = "base-env"
"""Slug of the integration project a factory started with a live base env gets."""

ROLE_LEAD: Final = "lead"
ROLE_PROJECT_LEAD: Final = "project_lead"
ROLE_IC: Final = "ic"
ROLE_ENV_KEEPER: Final = "env_keeper"
ROLE_REVIEWER: Final = "reviewer"
ROLE_DEMO: Final = "demo"
ROLES: Final = frozenset(
    {ROLE_LEAD, ROLE_PROJECT_LEAD, ROLE_IC, ROLE_ENV_KEEPER, ROLE_REVIEWER, ROLE_DEMO}
)
TOP_LEVEL_ROLES: Final = frozenset({ROLE_LEAD, ROLE_PROJECT_LEAD})

STATE_ACTIVE: Final = "active"
STATE_PAUSED: Final = "paused"
STATE_DONE: Final = "done"
STATES: Final = frozenset({STATE_ACTIVE, STATE_PAUSED, STATE_DONE})

HEALTH_OK: Final = "ok"
HEALTH_DEGRADED: Final = "degraded"
HEALTH_BLOCKED: Final = "blocked"
HEALTH_DEAD: Final = "dead"
HEALTH: Final = frozenset({HEALTH_OK, HEALTH_DEGRADED, HEALTH_BLOCKED, HEALTH_DEAD})

DEFAULT_HEARTBEAT_S: Final[dict[str, int]] = {ROLE_LEAD: 3600, ROLE_PROJECT_LEAD: 7200}

CADENCE_DEMO: Final = "demo"
CADENCE_NORMAL: Final = "normal"
CADENCES: Final = frozenset({CADENCE_DEMO, CADENCE_NORMAL})
CADENCE_HEARTBEATS: Final[dict[str, dict[str, int]]] = {
    CADENCE_DEMO: {ROLE_LEAD: 300, ROLE_PROJECT_LEAD: 600},
    CADENCE_NORMAL: {ROLE_LEAD: 3600, ROLE_PROJECT_LEAD: 7200},
}
"""Heartbeat seconds per top-level role for each cadence."""

# Server-derived project stages, in pipeline order (the cockpit stepper).
STAGE_QUEUED: Final = "queued"
STAGE_PLANNING: Final = "planning"
STAGE_BUILDING: Final = "building"
STAGE_REVIEW: Final = "review"
STAGE_VERIFY: Final = "verify"
STAGE_READY: Final = "ready"
STAGE_MERGED: Final = "merged"
STAGE_DONE: Final = "done"
STAGES: Final = (
    STAGE_QUEUED,
    STAGE_PLANNING,
    STAGE_BUILDING,
    STAGE_REVIEW,
    STAGE_VERIFY,
    STAGE_READY,
    STAGE_MERGED,
    STAGE_DONE,
)
AGENT_STAGES: Final = frozenset(
    {STAGE_PLANNING, STAGE_BUILDING, STAGE_REVIEW, STAGE_VERIFY, STAGE_READY, STAGE_MERGED}
)
"""Stages a PL may claim for itself (``sys_factory_set(stage=...)``)."""

# Action ids a decision option can carry; the server runs them on answer.
ACTION_RESTART: Final = "restart"
ACTION_RELAUNCH: Final = "relaunch"
ACTION_PAUSE: Final = "pause"
ACTION_RESUME: Final = "resume"
ACTION_DISMISS: Final = "dismiss"
ACTION_ABANDON_TASK: Final = "abandon_task"
ACTION_REWORK: Final = "rework"
ACTION_REDEPLOY_PREVIEW: Final = "redeploy_preview"
DECISION_ACTIONS: Final = frozenset(
    {
        ACTION_RESTART,
        ACTION_RELAUNCH,
        ACTION_PAUSE,
        ACTION_RESUME,
        ACTION_DISMISS,
        ACTION_ABANDON_TASK,
        ACTION_REWORK,
        ACTION_REDEPLOY_PREVIEW,
    }
)

MIN_HEARTBEAT_S: Final = 120
MAX_HEARTBEAT_S: Final = 86400

# Agent reports through sys_factory_pr / sys_factory_env / sys_factory_ask.
PR_CLAIMS: Final = ("opened", "ready_for_human_merge", "abandoned")
SENTINEL_VERDICTS: Final = ("verified", "not_verified")
ENV_STATES: Final = ("starting", "ready", "degraded", "failed", "parked", "down")
"""States an agent may report for a project's dev env."""
PREVIEW_REPORT_STATES: Final = ("deploying", "ready", "failed", "parking", "parked", "down")
"""States an agent may report for a factory preview (``stale``/``gone`` are probed)."""
DECISION_MAX_OPTIONS: Final = 5
DECISION_MAX_OPEN_PER_ASKER: Final = 10
DECISION_QUESTION_MAX: Final = 1000
DECISION_CONTEXT_MAX: Final = 4000
DECISION_OPTION_LABEL_MAX: Final = 60
DECISION_OPTION_DESCRIPTION_MAX: Final = 200

# Recorded demos: file types, kinds and caps (the runner pre-checks, the server enforces).
_MIB: Final = 1024 * 1024
DEMO_STATUSES: Final = ("PASS", "PARTIAL", "FAIL", "ERROR")
DEMO_FILE_KINDS: Final = (
    "video",
    "screenshot",
    "poster",
    "api",
    "har",
    "trace",
    "console",
    "script",
    "log",
    "other",
)
DEMO_MANIFEST_KINDS: Final[dict[str, str]] = {
    "video": "video",
    "poster": "poster",
    "api": "api",
    "har": "har",
    "trace": "trace",
    "console": "console",
    "script": "script",
    "log": "log",
}
"""``demo.json`` ``artifacts`` key -> file kind; any other key is ``other``."""
DEMO_FILE_TYPES: Final[dict[str, tuple[str, str]]] = {
    ".webm": ("video/webm", "video"),
    ".mp4": ("video/mp4", "video"),
    ".png": ("image/png", "image"),
    ".jpg": ("image/jpeg", "image"),
    ".jpeg": ("image/jpeg", "image"),
    ".gif": ("image/gif", "image"),
    ".json": ("application/json", "text"),
    ".har": ("application/json", "text"),
    ".txt": ("text/plain; charset=utf-8", "text"),
    ".md": ("text/markdown; charset=utf-8", "text"),
    ".log": ("text/plain; charset=utf-8", "text"),
    ".ts": ("text/plain; charset=utf-8", "text"),
    ".zip": ("application/zip", "archive"),
}
"""Extension -> (served content type, size class). HTML and SVG are never allowed."""
DEMO_CLASS_MAX_BYTES: Final[dict[str, int]] = {
    "video": 50 * _MIB,
    "image": 5 * _MIB,
    "text": 2 * _MIB,
    "archive": 50 * _MIB,
}
DEMO_MAX_FILES: Final = 30
DEMO_MAX_TOTAL_BYTES: Final = 120 * _MIB
DEMO_MANIFEST_MAX_BYTES: Final = 2 * _MIB


_PR_URL_RE: Final = re.compile(
    r"https?://github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)/pull/(\d+)"
)
_SHA_RE: Final = re.compile(r"[0-9a-f]{40}")


def parse_pr_url(url: object) -> tuple[str, int] | None:
    """
    Parse a GitHub pull request URL.

    :param url: e.g. ``"https://github.com/caffeinelabs/app/pull/9630"`` (a
        trailing slash, query or fragment is tolerated).
    :returns: ``("caffeinelabs/app", 9630)`` with the repo lowercased, or ``None``.
    """
    if not isinstance(url, str):
        return None
    text = url.strip().split("#", 1)[0].split("?", 1)[0].rstrip("/")
    match = _PR_URL_RE.fullmatch(text)
    if match is None:
        return None
    owner, repo, number = match.groups()
    return f"{owner}/{repo}".lower(), int(number)


def canonical_pr_url(repo: str, number: int) -> str:
    """:returns: ``https://github.com/<repo>/pull/<number>``."""
    return f"https://github.com/{repo}/pull/{number}"


def is_full_sha(value: object) -> bool:
    """:returns: Whether *value* is a 40-char lowercase hex commit sha."""
    return isinstance(value, str) and _SHA_RE.fullmatch(value) is not None


def demo_file_type(name: str) -> tuple[str, str] | None:
    """
    Look up a demo file's served content type and size class by extension.

    :param name: File name or path, e.g. ``"shots/01-landing.png"``.
    :returns: e.g. ``("image/png", "image")``, or ``None`` when not allowed.
    """
    dot = name.rfind(".")
    if dot < 0 or "/" in name[dot:]:
        return None
    return DEMO_FILE_TYPES.get(name[dot:].lower())


def demo_manifest_conflict(
    manifest: Mapping[str, Any], pr_url: str, head_sha: str
) -> tuple[str, str] | None:
    """
    Check a harness ``demo.json`` against the PR head it is published for.

    :param manifest: The manifest; its ``sha`` and ``pr_url`` are checked when present.
    :param pr_url: The PR the demo is published on.
    :param head_sha: The full head sha the demo is published for.
    :returns: ``(error code, message)`` when the manifest names another commit
        or PR, else ``None``.
    """
    sha = manifest.get("sha")
    if isinstance(sha, str) and sha.strip():
        recorded = sha.strip().lower()
        if len(recorded) < 7 or not head_sha.lower().startswith(recorded):
            return (
                "sha_mismatch",
                f"demo.json was recorded at {recorded[:12]}, not at head {head_sha[:12]}",
            )
    url = manifest.get("pr_url")
    if isinstance(url, str) and url.strip() and parse_pr_url(url) != parse_pr_url(pr_url):
        return "pr_mismatch", f"demo.json is for {url.strip()[:200]}, not {pr_url[:200]}"
    return None


def demo_status(given: object, manifest: Mapping[str, Any] | None) -> str | tuple[str, str]:
    """
    The status a demo is recorded with: the manifest's when there is one.

    :param given: The status the agent passed, or ``None``.
    :param manifest: The harness's ``demo.json``, or ``None``.
    :returns: The status, or ``(error code, message)`` when the agent's status
        disagrees with the manifest or no valid status is given.
    """
    statuses = "|".join(DEMO_STATUSES)
    if given == "":
        given = None
    if manifest is not None and manifest.get("status") is not None:
        recorded = manifest["status"]
        if recorded not in DEMO_STATUSES:
            return "invalid_status", f"demo.json status {str(recorded)[:20]!r} is not {statuses}"
        if given is not None and given != recorded:
            return (
                "status_mismatch",
                f"demo.json says {recorded}; omit status or pass {recorded}",
            )
        assert isinstance(recorded, str)
        return recorded
    if given is None:
        return "invalid_status", f"pass status ({statuses}) or a manifest with one"
    if given not in DEMO_STATUSES:
        return "invalid_status", f"status must be one of {statuses}"
    assert isinstance(given, str)
    return given


LABEL_VALUE_MAX_LEN: Final = 256
SUMMARY_MAX_LEN: Final = 120
MESSAGE_MAX_CHARS: Final = 8000

MESSAGE_KINDS: Final = frozenset({"info", "request", "reply", "brief", "alert"})
PENDING_REPLY_KINDS: Final = frozenset({"request", "brief"})
"""Kinds that expect an answer; the target's next idle edge may notify the sender."""
ADDRESS_ALIASES: Final = frozenset({"parent", "lead", "peer_lead", "supervisor"})
PROJECT_ALIAS_PREFIX: Final = "project:"


def factory_role_from_spec(spec: Any) -> str | None:
    """Return the spec's ``executor.config.factory_role`` when it names a known role.

    :param spec: An :class:`~omnigent.spec.types.AgentSpec` (or ``None``).
    :returns: e.g. ``"project_lead"``, or ``None`` for non-factory agents.
    """
    executor = getattr(spec, "executor", None)
    config = getattr(executor, "config", None)
    if not isinstance(config, dict):
        return None
    role = config.get("factory_role")
    return role if isinstance(role, str) and role in ROLES else None


def clamp_label_value(value: str, limit: int = LABEL_VALUE_MAX_LEN) -> str:
    """Trim a label value to *limit* characters (labels are short scalars)."""
    return value if len(value) <= limit else value[: limit - 1] + "…"


def valid_heartbeat_s(value: int) -> bool:
    """``0`` (off) or within ``[MIN_HEARTBEAT_S, MAX_HEARTBEAT_S]``."""
    return value == 0 or MIN_HEARTBEAT_S <= value <= MAX_HEARTBEAT_S


FACTORY_ENABLED_ENV: Final = "OMNIGENT_FACTORY_ENABLED"


def factory_enabled() -> bool:
    """Whether the factory built-ins and supervisor are switched on (``=1``)."""
    return os.environ.get(FACTORY_ENABLED_ENV, "").strip() == "1"
