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
LABEL_STARTED_AT: Final = "omnigent.factory.started_at"
"""Epoch seconds the factory was started from the cockpit (root only)."""
LABEL_BEAT_N: Final = "omnigent.factory.beat_n"
"""Number of the last delivered heartbeat (top-level; survives a server roll)."""
LABEL_DEAD_ALERTED_AT: Final = "omnigent.factory.dead_alerted_at"
"""Epoch seconds of the last dead-member alert (top-level; dedupes across rolls)."""
LABEL_KEEP_PREVIEWS: Final = "omnigent.factory.keep_previews"
"""``"0"`` lets a paused PL's pod idle out despite live previews (PL only)."""

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
