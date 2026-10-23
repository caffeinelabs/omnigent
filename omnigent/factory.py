"""Software-factory vocabulary shared by the server and the runner.

A factory is a labelled tree of ordinary sessions: two leads, one project lead
(PL) per project (each a top-level managed session in its own sandbox), and
the PLs' co-located sub-agent ICs. Membership and supervision live entirely in
``conversation_labels`` (no schema change); sub-agents inherit membership from
their root conversation, and their role comes from their spec's
``executor.config.factory_role``.
"""

from __future__ import annotations

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

ROLE_LEAD: Final = "lead"
ROLE_PROJECT_LEAD: Final = "project_lead"
ROLE_IC: Final = "ic"
ROLE_ENV_KEEPER: Final = "env_keeper"
ROLE_REVIEWER: Final = "reviewer"
ROLES: Final = frozenset({ROLE_LEAD, ROLE_PROJECT_LEAD, ROLE_IC, ROLE_ENV_KEEPER, ROLE_REVIEWER})
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
MIN_HEARTBEAT_S: Final = 120
MAX_HEARTBEAT_S: Final = 86400

LABEL_VALUE_MAX_LEN: Final = 256
SUMMARY_MAX_LEN: Final = 120
MESSAGE_MAX_CHARS: Final = 8000

MESSAGE_KINDS: Final = frozenset({"info", "request", "reply", "brief", "alert"})
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
