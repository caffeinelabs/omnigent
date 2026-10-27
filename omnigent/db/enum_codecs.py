"""Name↔int codecs for enum-like columns stored as ``SMALLINT``.

Several low-cardinality closed-set columns (``conversations.kind``,
``conversation_items.type``/``status``, ``comments.status``,
``account_tokens.kind``, ``policies.type``, ``policies.scope``,
``hosts.status``, ``agents.kind``, ``scheduled_tasks.state``,
``scheduled_tasks.execution_target``,
``scheduled_task_runs.status``, and the ``factory_*`` cockpit tables) are
stored as integer codes rather
than their string names — smaller rows and a tighter ``CHECK`` than a
free ``VARCHAR``. The string names remain the
contract for entities, the HTTP API, the web client, and the SDKs; the
integer form never leaves the store row↔entity boundary. These codecs are
the single place that translates between the two.

Codes are STABLE and append-only: never renumber or reuse a shipped code,
and leave gaps rather than reordering, so old rows keep their meaning.
This mirrors :data:`omnigent.server.auth.LEVEL_READ` and friends, the
existing int-coded ``session_permissions.level``.
"""

from __future__ import annotations

from omnigent.entities.conversation import ITEM_TYPE_TO_DATA_CLS

# ── Code tables (name → stable int code) ───────────────

CONVERSATION_KIND: dict[str, int] = {
    "default": 1,
    "sub_agent": 2,
}

# Item type codes. The key set is kept in lock-step with
# ITEM_TYPE_TO_DATA_CLS (the app-layer source of truth) by
# _assert_item_type_codes_cover_data_classes below, so a newly added item
# type cannot ship without a code. Codes are append-only.
ITEM_TYPE: dict[str, int] = {
    "message": 1,
    "function_call": 2,
    "function_call_output": 3,
    "reasoning": 4,
    "error": 5,
    "compaction": 6,
    "native_tool": 7,
    "resource_event": 8,
    "routing_decision": 9,
    "slash_command": 10,
    "terminal_command": 11,
}

# Item status codes. Only "completed" is written today (items are final on
# append), but the field is semantically an OpenAI-style status that may
# widen, so codes for the rest of that vocabulary are reserved up front and
# the column CHECK admits all of them.
ITEM_STATUS: dict[str, int] = {
    "completed": 1,
    "in_progress": 2,
    "incomplete": 3,
    "failed": 4,
}

COMMENT_STATUS: dict[str, int] = {
    "draft": 1,
    "addressed": 2,
}

# Last relay-observed turn status persisted on the conversation's metadata
# row (``omnigent_conversation_metadata.live_status``) so any server replica
# can serve the sidebar's activity state, not just the pod holding the
# runner tunnel.
SESSION_LIVE_STATUS: dict[str, int] = {
    "idle": 1,
    "running": 2,
    "waiting": 3,
    "failed": 4,
}

ACCOUNT_TOKEN_KIND: dict[str, int] = {
    "invite": 1,
    "magic": 2,
}

POLICY_TYPE: dict[str, int] = {
    "python": 1,
    "url": 2,
}

DEVICE_GRANT_STATUS: dict[str, int] = {
    "pending": 1,
    "approved": 2,
    "denied": 3,
    "redeemed": 4,
    "revoked": 5,
}

HOST_STATUS: dict[str, int] = {
    "online": 1,
    "offline": 2,
}

AGENT_KIND: dict[str, int] = {
    "template": 1,
    "session": 2,
}

POLICY_SCOPE: dict[str, int] = {
    "default": 1,
    "session": 2,
}

SCHEDULED_TASK_STATE: dict[str, int] = {
    "active": 1,
    "paused": 2,
    "deleted": 3,
}

SCHEDULED_TASK_EXECUTION_TARGET: dict[str, int] = {
    "connected_host": 1,
    "managed_sandbox": 2,
}

SCHEDULED_TASK_RUN_STATUS: dict[str, int] = {
    "scheduled": 1,
    "running": 2,
    "succeeded": 3,
    "failed": 4,
    "skipped": 5,
}

# Software-factory cockpit tables. Stages are in pipeline order.
FACTORY_PROJECT_STAGE: dict[str, int] = {
    "queued": 1,
    "planning": 2,
    "building": 3,
    "review": 4,
    "verify": 5,
    "ready": 6,
    "merged": 7,
    "done": 8,
}

FACTORY_PR_STATE: dict[str, int] = {
    "open": 1,
    "merged": 2,
    "closed": 3,
}

FACTORY_DECISION_SOURCE: dict[str, int] = {
    "agent": 1,
    "relaunch_failed": 2,
    "blocked_approval": 3,
    "pr_closed": 4,
    "storm": 5,
    "proposal": 6,
    "loop": 7,
    "access_request": 8,
    "done_check": 9,
}

FACTORY_DECISION_STATUS: dict[str, int] = {
    "open": 1,
    "answered": 2,
    "withdrawn": 3,
    "expired": 4,
}

FACTORY_EVENT_SEVERITY: dict[str, int] = {
    "info": 0,
    "notice": 1,
    "warning": 2,
    "critical": 3,
}

# Demo outcomes keep the harness's upper-case spelling.
FACTORY_DEMO_STATUS: dict[str, int] = {
    "PASS": 1,
    "PARTIAL": 2,
    "FAIL": 3,
    "ERROR": 4,
}

# Round-3 factory tables (people, Room, proposals, work graph, loops, issues,
# telemetry, definitions, feedback).
FACTORY_PROJECT_STATUS: dict[str, int] = {
    "active": 1,
    "cancelled": 2,
    "merged": 3,
    "done": 4,
}

FACTORY_PERSON_ROLE: dict[str, int] = {
    "viewer": 1,
    "operator": 2,
    "owner": 3,
}

FACTORY_CHAT_AUTHOR_KIND: dict[str, int] = {
    "human": 1,
    "agent": 2,
    "system": 3,
}

# Who acted: proposal and feedback authors, task creators.
FACTORY_ACTOR_KIND: dict[str, int] = {
    "human": 1,
    "agent": 2,
    "server": 3,
}

FACTORY_PROPOSAL_KIND: dict[str, int] = {
    "projects": 1,
    "tuning": 2,
    "rules": 3,
    "code": 4,
}

FACTORY_PROPOSAL_STATUS: dict[str, int] = {
    "pending": 1,
    "applied": 2,
    "trial": 3,
    "kept": 4,
    "reverted": 5,
    "rejected": 6,
    "expired": 7,
    "withdrawn": 8,
    "refused": 9,
    "undone": 10,
    "awaiting_cosign": 11,
}

FACTORY_PROPOSAL_APPROVAL: dict[str, int] = {
    "auto": 1,
    "operator": 2,
    "owner": 3,
}

FACTORY_PROPOSAL_CLASS: dict[str, int] = {
    "A": 1,
    "B": 2,
    "human": 3,
}

FACTORY_TASK_KIND: dict[str, int] = {
    "implement": 1,
    "test": 2,
    "docs": 3,
    "spike": 4,
    "ops": 5,
    "adhoc": 6,
}

FACTORY_TASK_STATE: dict[str, int] = {
    "planned": 1,
    "ready": 2,
    "running": 3,
    "review": 4,
    "blocked": 5,
    "done": 6,
    "failed": 7,
    "cancelled": 8,
}

FACTORY_LOOP_KIND: dict[str, int] = {
    "implement": 1,
    "review": 2,
    "verify": 3,
    "demo": 4,
    "ci": 5,
    "claim": 6,
    "plan": 7,
    "intake": 8,
    "improve": 9,
}

FACTORY_LOOP_STATE: dict[str, int] = {
    "running": 1,
    "converged": 2,
    "stalled": 3,
    "thrashing": 4,
    "exhausted": 5,
    "escalated": 6,
    "cancelled": 7,
    "superseded": 8,
}

FACTORY_ISSUE_CLASS: dict[str, int] = {
    "incident": 1,
    "finding": 2,
    "retro": 3,
}

FACTORY_ISSUE_SOURCE: dict[str, int] = {
    "detector": 1,
    "sentinel": 2,
    "human": 3,
    "agent": 4,
    "server": 5,
}

FACTORY_ISSUE_STATUS: dict[str, int] = {
    "open": 1,
    "acked": 2,
    "snoozed": 3,
    "resolved": 4,
    "wontfix": 5,
}

FACTORY_SPAN_KIND: dict[str, int] = {
    "project": 1,
    "stage": 2,
    "pr": 3,
    "pr_round": 4,
    "gate": 5,
    "ready_wait": 6,
    "decision": 7,
    "task": 8,
    "loop": 9,
    "loop_iter": 10,
    "wait": 11,
    "env": 12,
    "preview": 13,
    "demo": 14,
    "incident": 15,
    "intervention": 16,
    "proposal": 17,
    "chat_turn": 18,
    "spawn": 19,
    "turn": 20,
    "tool": 21,
}

FACTORY_SPAN_OUTCOME: dict[str, int] = {
    "open": 1,
    "ok": 2,
    "fail": 3,
    "abandoned": 4,
    "superseded": 5,
    "timeout": 6,
    "cancelled": 7,
    "exhausted": 8,
}

FACTORY_DEFINITION_VIA: dict[str, int] = {
    "seed": 1,
    "human": 2,
    "proposal": 3,
    "revert": 4,
}

FACTORY_FEEDBACK_TARGET: dict[str, int] = {
    "pr": 1,
    "demo": 2,
    "decision": 3,
    "project": 4,
    "member": 5,
    "factory": 6,
    "issue": 7,
    "proposal": 8,
}

FACTORY_FEEDBACK_KIND: dict[str, int] = {
    "rating": 1,
    "useful": 2,
    "unneeded": 3,
    "closure": 4,
    "calibration": 5,
    "root_cause": 6,
}


def _assert_item_type_codes_cover_data_classes() -> None:
    """
    Guard that :data:`ITEM_TYPE` matches the app's item-type registry.

    Raised at import time (and asserted by a unit test) so a new item type
    added to ``ITEM_TYPE_TO_DATA_CLS`` without a corresponding code fails
    loudly instead of silently breaking persistence.

    :raises RuntimeError: If the two key sets diverge.
    """
    missing = set(ITEM_TYPE_TO_DATA_CLS) - set(ITEM_TYPE)
    extra = set(ITEM_TYPE) - set(ITEM_TYPE_TO_DATA_CLS)
    if missing or extra:
        raise RuntimeError(
            "ITEM_TYPE codes are out of sync with ITEM_TYPE_TO_DATA_CLS "
            f"(missing codes for {sorted(missing)}, "
            f"unknown types {sorted(extra)})."
        )


_assert_item_type_codes_cover_data_classes()


# ── Encode / decode ────────────────────────────────────


def _invert(table: dict[str, int]) -> dict[int, str]:
    """Return the code→name inverse of a name→code table."""
    return {code: name for name, code in table.items()}


_CODE_TO_NAME: dict[int, dict[int, str]] = {}

for _factory_table in (
    FACTORY_PROJECT_STAGE,
    FACTORY_PR_STATE,
    FACTORY_DECISION_SOURCE,
    FACTORY_DECISION_STATUS,
    FACTORY_EVENT_SEVERITY,
    FACTORY_DEMO_STATUS,
    FACTORY_PROJECT_STATUS,
    FACTORY_PERSON_ROLE,
    FACTORY_CHAT_AUTHOR_KIND,
    FACTORY_ACTOR_KIND,
    FACTORY_PROPOSAL_KIND,
    FACTORY_PROPOSAL_STATUS,
    FACTORY_PROPOSAL_APPROVAL,
    FACTORY_PROPOSAL_CLASS,
    FACTORY_TASK_KIND,
    FACTORY_TASK_STATE,
    FACTORY_LOOP_KIND,
    FACTORY_LOOP_STATE,
    FACTORY_ISSUE_CLASS,
    FACTORY_ISSUE_SOURCE,
    FACTORY_ISSUE_STATUS,
    FACTORY_SPAN_KIND,
    FACTORY_SPAN_OUTCOME,
    FACTORY_DEFINITION_VIA,
    FACTORY_FEEDBACK_TARGET,
    FACTORY_FEEDBACK_KIND,
):
    _CODE_TO_NAME[id(_factory_table)] = _invert(_factory_table)


def _encode(table: dict[str, int], name: str, *, field: str) -> int:
    """
    Map an enum *name* to its stable integer code.

    :param table: The name→code table for the field.
    :param name: The string enum name, e.g. ``"sub_agent"``.
    :param field: Field label used in the error message, e.g.
        ``"conversations.kind"``.
    :returns: The integer code.
    :raises ValueError: If *name* is not a known value for the field.
    """
    try:
        return table[name]
    except KeyError:
        raise ValueError(f"unknown {field} value: {name!r}") from None


def _decode(table: dict[str, int], code: int, *, field: str) -> str:
    """
    Map an integer *code* back to its enum name.

    :param table: The name→code table for the field.
    :param code: The stored integer code.
    :param field: Field label used in the error message, e.g.
        ``"conversations.kind"``.
    :returns: The string enum name.
    :raises ValueError: If *code* is not a known code for the field.
    """
    inverse = _CODE_TO_NAME.get(id(table))
    if inverse is None:
        inverse = _invert(table)
        _CODE_TO_NAME[id(table)] = inverse
    try:
        return inverse[code]
    except KeyError:
        raise ValueError(f"unknown {field} code: {code!r}") from None


def encode_conversation_kind(name: str) -> int:
    """Encode a ``conversations.kind`` name to its int code."""
    return _encode(CONVERSATION_KIND, name, field="conversations.kind")


def decode_conversation_kind(code: int) -> str:
    """Decode a ``conversations.kind`` int code to its name."""
    return _decode(CONVERSATION_KIND, code, field="conversations.kind")


def encode_item_type(name: str) -> int:
    """Encode a ``conversation_items.type`` name to its int code."""
    return _encode(ITEM_TYPE, name, field="conversation_items.type")


def decode_item_type(code: int) -> str:
    """Decode a ``conversation_items.type`` int code to its name."""
    return _decode(ITEM_TYPE, code, field="conversation_items.type")


def encode_item_status(name: str) -> int:
    """Encode a ``conversation_items.status`` name to its int code."""
    return _encode(ITEM_STATUS, name, field="conversation_items.status")


def decode_item_status(code: int) -> str:
    """Decode a ``conversation_items.status`` int code to its name."""
    return _decode(ITEM_STATUS, code, field="conversation_items.status")


def encode_comment_status(name: str) -> int:
    """Encode a ``comments.status`` name to its int code."""
    return _encode(COMMENT_STATUS, name, field="comments.status")


def decode_comment_status(code: int) -> str:
    """Decode a ``comments.status`` int code to its name."""
    return _decode(COMMENT_STATUS, code, field="comments.status")


def encode_session_live_status(name: str) -> int:
    """Encode an ``omnigent_conversation_metadata.live_status`` name to its int code."""
    return _encode(SESSION_LIVE_STATUS, name, field="omnigent_conversation_metadata.live_status")


def decode_session_live_status(code: int) -> str:
    """Decode an ``omnigent_conversation_metadata.live_status`` int code to its name."""
    return _decode(SESSION_LIVE_STATUS, code, field="omnigent_conversation_metadata.live_status")


def encode_account_token_kind(name: str) -> int:
    """Encode an ``account_tokens.kind`` name to its int code."""
    return _encode(ACCOUNT_TOKEN_KIND, name, field="account_tokens.kind")


def decode_account_token_kind(code: int) -> str:
    """Decode an ``account_tokens.kind`` int code to its name."""
    return _decode(ACCOUNT_TOKEN_KIND, code, field="account_tokens.kind")


def encode_device_grant_status(name: str) -> int:
    """Encode a ``device_grants.status`` name to its int code."""
    return _encode(DEVICE_GRANT_STATUS, name, field="device_grants.status")


def decode_device_grant_status(code: int) -> str:
    """Decode a ``device_grants.status`` int code to its name."""
    return _decode(DEVICE_GRANT_STATUS, code, field="device_grants.status")


def encode_policy_type(name: str) -> int:
    """Encode a ``policies.type`` name to its int code."""
    return _encode(POLICY_TYPE, name, field="policies.type")


def decode_policy_type(code: int) -> str:
    """Decode a ``policies.type`` int code to its name."""
    return _decode(POLICY_TYPE, code, field="policies.type")


def encode_host_status(name: str) -> int:
    """Encode a ``hosts.status`` name to its int code."""
    return _encode(HOST_STATUS, name, field="hosts.status")


def decode_host_status(code: int) -> str:
    """Decode a ``hosts.status`` int code to its name."""
    return _decode(HOST_STATUS, code, field="hosts.status")


def encode_agent_kind(name: str) -> int:
    """Encode an ``agents.kind`` name to its int code."""
    return _encode(AGENT_KIND, name, field="agents.kind")


def decode_agent_kind(code: int) -> str:
    """Decode an ``agents.kind`` int code to its name."""
    return _decode(AGENT_KIND, code, field="agents.kind")


def encode_policy_scope(name: str) -> int:
    """Encode a ``policies.scope`` name to its int code."""
    return _encode(POLICY_SCOPE, name, field="policies.scope")


def decode_policy_scope(code: int) -> str:
    """Decode a ``policies.scope`` int code to its name."""
    return _decode(POLICY_SCOPE, code, field="policies.scope")


def encode_scheduled_task_state(name: str) -> int:
    """Encode a ``scheduled_tasks.state`` name to its int code."""
    return _encode(SCHEDULED_TASK_STATE, name, field="scheduled_tasks.state")


def decode_scheduled_task_state(code: int) -> str:
    """Decode a ``scheduled_tasks.state`` int code to its name."""
    return _decode(SCHEDULED_TASK_STATE, code, field="scheduled_tasks.state")


def encode_scheduled_task_execution_target(name: str) -> int:
    """Encode a ``scheduled_tasks.execution_target`` name to its int code."""
    return _encode(SCHEDULED_TASK_EXECUTION_TARGET, name, field="scheduled_tasks.execution_target")


def decode_scheduled_task_execution_target(code: int) -> str:
    """Decode a ``scheduled_tasks.execution_target`` int code to its name."""
    return _decode(SCHEDULED_TASK_EXECUTION_TARGET, code, field="scheduled_tasks.execution_target")


def encode_scheduled_task_run_status(name: str) -> int:
    """Encode a ``scheduled_task_runs.status`` name to its int code."""
    return _encode(SCHEDULED_TASK_RUN_STATUS, name, field="scheduled_task_runs.status")


def decode_scheduled_task_run_status(code: int) -> str:
    """Decode a ``scheduled_task_runs.status`` int code to its name."""
    return _decode(SCHEDULED_TASK_RUN_STATUS, code, field="scheduled_task_runs.status")


def encode_factory_project_stage(name: str) -> int:
    """Encode a ``factory_projects.stage`` name to its int code."""
    return _encode(FACTORY_PROJECT_STAGE, name, field="factory_projects.stage")


def decode_factory_project_stage(code: int) -> str:
    """Decode a ``factory_projects.stage`` int code to its name."""
    return _decode(FACTORY_PROJECT_STAGE, code, field="factory_projects.stage")


def encode_factory_pr_state(name: str) -> int:
    """Encode a ``factory_prs.state`` name to its int code."""
    return _encode(FACTORY_PR_STATE, name, field="factory_prs.state")


def decode_factory_pr_state(code: int) -> str:
    """Decode a ``factory_prs.state`` int code to its name."""
    return _decode(FACTORY_PR_STATE, code, field="factory_prs.state")


def encode_factory_decision_source(name: str) -> int:
    """Encode a ``factory_decisions.source`` name to its int code."""
    return _encode(FACTORY_DECISION_SOURCE, name, field="factory_decisions.source")


def decode_factory_decision_source(code: int) -> str:
    """Decode a ``factory_decisions.source`` int code to its name."""
    return _decode(FACTORY_DECISION_SOURCE, code, field="factory_decisions.source")


def encode_factory_decision_status(name: str) -> int:
    """Encode a ``factory_decisions.status`` name to its int code."""
    return _encode(FACTORY_DECISION_STATUS, name, field="factory_decisions.status")


def decode_factory_decision_status(code: int) -> str:
    """Decode a ``factory_decisions.status`` int code to its name."""
    return _decode(FACTORY_DECISION_STATUS, code, field="factory_decisions.status")


def encode_factory_event_severity(name: str) -> int:
    """Encode a ``factory_events.severity`` name to its int code."""
    return _encode(FACTORY_EVENT_SEVERITY, name, field="factory_events.severity")


def decode_factory_event_severity(code: int) -> str:
    """Decode a ``factory_events.severity`` int code to its name."""
    return _decode(FACTORY_EVENT_SEVERITY, code, field="factory_events.severity")


def encode_factory_demo_status(name: str) -> int:
    """Encode a ``factory_demos.status`` name to its int code."""
    return _encode(FACTORY_DEMO_STATUS, name, field="factory_demos.status")


def decode_factory_demo_status(code: int) -> str:
    """Decode a ``factory_demos.status`` int code to its name."""
    return _decode(FACTORY_DEMO_STATUS, code, field="factory_demos.status")


def encode_factory_project_status(name: str) -> int:
    """Encode a ``factory_projects.status`` name to its stable code."""
    return _encode(FACTORY_PROJECT_STATUS, name, field="factory_projects.status")


def decode_factory_project_status(code: int) -> str:
    """Decode a stored ``factory_projects.status`` code to its name."""
    return _decode(FACTORY_PROJECT_STATUS, code, field="factory_projects.status")


def encode_factory_person_role(name: str) -> int:
    """Encode a ``factory_people.role`` name to its stable code."""
    return _encode(FACTORY_PERSON_ROLE, name, field="factory_people.role")


def decode_factory_person_role(code: int) -> str:
    """Decode a stored ``factory_people.role`` code to its name."""
    return _decode(FACTORY_PERSON_ROLE, code, field="factory_people.role")


def encode_factory_chat_author_kind(name: str) -> int:
    """Encode a ``factory_chat_messages.author_kind`` name to its stable code."""
    return _encode(FACTORY_CHAT_AUTHOR_KIND, name, field="factory_chat_messages.author_kind")


def decode_factory_chat_author_kind(code: int) -> str:
    """Decode a stored ``factory_chat_messages.author_kind`` code to its name."""
    return _decode(FACTORY_CHAT_AUTHOR_KIND, code, field="factory_chat_messages.author_kind")


def encode_factory_actor_kind(name: str) -> int:
    """Encode a ``factory actor kind`` name to its stable code."""
    return _encode(FACTORY_ACTOR_KIND, name, field="factory actor kind")


def decode_factory_actor_kind(code: int) -> str:
    """Decode a stored ``factory actor kind`` code to its name."""
    return _decode(FACTORY_ACTOR_KIND, code, field="factory actor kind")


def encode_factory_proposal_kind(name: str) -> int:
    """Encode a ``factory_proposals.kind`` name to its stable code."""
    return _encode(FACTORY_PROPOSAL_KIND, name, field="factory_proposals.kind")


def decode_factory_proposal_kind(code: int) -> str:
    """Decode a stored ``factory_proposals.kind`` code to its name."""
    return _decode(FACTORY_PROPOSAL_KIND, code, field="factory_proposals.kind")


def encode_factory_proposal_status(name: str) -> int:
    """Encode a ``factory_proposals.status`` name to its stable code."""
    return _encode(FACTORY_PROPOSAL_STATUS, name, field="factory_proposals.status")


def decode_factory_proposal_status(code: int) -> str:
    """Decode a stored ``factory_proposals.status`` code to its name."""
    return _decode(FACTORY_PROPOSAL_STATUS, code, field="factory_proposals.status")


def encode_factory_proposal_approval(name: str) -> int:
    """Encode a ``factory_proposals.approval`` name to its stable code."""
    return _encode(FACTORY_PROPOSAL_APPROVAL, name, field="factory_proposals.approval")


def decode_factory_proposal_approval(code: int) -> str:
    """Decode a stored ``factory_proposals.approval`` code to its name."""
    return _decode(FACTORY_PROPOSAL_APPROVAL, code, field="factory_proposals.approval")


def encode_factory_proposal_class(name: str) -> int:
    """Encode a ``factory_proposals.class`` name to its stable code."""
    return _encode(FACTORY_PROPOSAL_CLASS, name, field="factory_proposals.class")


def decode_factory_proposal_class(code: int) -> str:
    """Decode a stored ``factory_proposals.class`` code to its name."""
    return _decode(FACTORY_PROPOSAL_CLASS, code, field="factory_proposals.class")


def encode_factory_task_kind(name: str) -> int:
    """Encode a ``factory_tasks.kind`` name to its stable code."""
    return _encode(FACTORY_TASK_KIND, name, field="factory_tasks.kind")


def decode_factory_task_kind(code: int) -> str:
    """Decode a stored ``factory_tasks.kind`` code to its name."""
    return _decode(FACTORY_TASK_KIND, code, field="factory_tasks.kind")


def encode_factory_task_state(name: str) -> int:
    """Encode a ``factory_tasks.state`` name to its stable code."""
    return _encode(FACTORY_TASK_STATE, name, field="factory_tasks.state")


def decode_factory_task_state(code: int) -> str:
    """Decode a stored ``factory_tasks.state`` code to its name."""
    return _decode(FACTORY_TASK_STATE, code, field="factory_tasks.state")


def encode_factory_loop_kind(name: str) -> int:
    """Encode a ``factory_loops.kind`` name to its stable code."""
    return _encode(FACTORY_LOOP_KIND, name, field="factory_loops.kind")


def decode_factory_loop_kind(code: int) -> str:
    """Decode a stored ``factory_loops.kind`` code to its name."""
    return _decode(FACTORY_LOOP_KIND, code, field="factory_loops.kind")


def encode_factory_loop_state(name: str) -> int:
    """Encode a ``factory_loops.state`` name to its stable code."""
    return _encode(FACTORY_LOOP_STATE, name, field="factory_loops.state")


def decode_factory_loop_state(code: int) -> str:
    """Decode a stored ``factory_loops.state`` code to its name."""
    return _decode(FACTORY_LOOP_STATE, code, field="factory_loops.state")


def encode_factory_issue_class(name: str) -> int:
    """Encode a ``factory_issues.class`` name to its stable code."""
    return _encode(FACTORY_ISSUE_CLASS, name, field="factory_issues.class")


def decode_factory_issue_class(code: int) -> str:
    """Decode a stored ``factory_issues.class`` code to its name."""
    return _decode(FACTORY_ISSUE_CLASS, code, field="factory_issues.class")


def encode_factory_issue_source(name: str) -> int:
    """Encode a ``factory_issues.source`` name to its stable code."""
    return _encode(FACTORY_ISSUE_SOURCE, name, field="factory_issues.source")


def decode_factory_issue_source(code: int) -> str:
    """Decode a stored ``factory_issues.source`` code to its name."""
    return _decode(FACTORY_ISSUE_SOURCE, code, field="factory_issues.source")


def encode_factory_issue_status(name: str) -> int:
    """Encode a ``factory_issues.status`` name to its stable code."""
    return _encode(FACTORY_ISSUE_STATUS, name, field="factory_issues.status")


def decode_factory_issue_status(code: int) -> str:
    """Decode a stored ``factory_issues.status`` code to its name."""
    return _decode(FACTORY_ISSUE_STATUS, code, field="factory_issues.status")


def encode_factory_span_kind(name: str) -> int:
    """Encode a ``factory_spans.kind`` name to its stable code."""
    return _encode(FACTORY_SPAN_KIND, name, field="factory_spans.kind")


def decode_factory_span_kind(code: int) -> str:
    """Decode a stored ``factory_spans.kind`` code to its name."""
    return _decode(FACTORY_SPAN_KIND, code, field="factory_spans.kind")


def encode_factory_span_outcome(name: str) -> int:
    """Encode a ``factory_spans.outcome`` name to its stable code."""
    return _encode(FACTORY_SPAN_OUTCOME, name, field="factory_spans.outcome")


def decode_factory_span_outcome(code: int) -> str:
    """Decode a stored ``factory_spans.outcome`` code to its name."""
    return _decode(FACTORY_SPAN_OUTCOME, code, field="factory_spans.outcome")


def encode_factory_definition_via(name: str) -> int:
    """Encode a ``factory_definitions.created_via`` name to its stable code."""
    return _encode(FACTORY_DEFINITION_VIA, name, field="factory_definitions.created_via")


def decode_factory_definition_via(code: int) -> str:
    """Decode a stored ``factory_definitions.created_via`` code to its name."""
    return _decode(FACTORY_DEFINITION_VIA, code, field="factory_definitions.created_via")


def encode_factory_feedback_target(name: str) -> int:
    """Encode a ``factory_feedback.target_kind`` name to its stable code."""
    return _encode(FACTORY_FEEDBACK_TARGET, name, field="factory_feedback.target_kind")


def decode_factory_feedback_target(code: int) -> str:
    """Decode a stored ``factory_feedback.target_kind`` code to its name."""
    return _decode(FACTORY_FEEDBACK_TARGET, code, field="factory_feedback.target_kind")


def encode_factory_feedback_kind(name: str) -> int:
    """Encode a ``factory_feedback.kind`` name to its stable code."""
    return _encode(FACTORY_FEEDBACK_KIND, name, field="factory_feedback.kind")


def decode_factory_feedback_kind(code: int) -> str:
    """Decode a stored ``factory_feedback.kind`` code to its name."""
    return _decode(FACTORY_FEEDBACK_KIND, code, field="factory_feedback.kind")
