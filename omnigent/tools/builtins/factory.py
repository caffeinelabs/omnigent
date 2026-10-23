"""Software-factory tools: cross-pod messaging, roster, beacon and spawn.

A factory is a labelled tree of ordinary sessions (see :mod:`omnigent.factory`).
These classes are schema-only: the runner dispatches every call to the server's
``/v1/factory`` routes (and ``POST /v1/sessions`` for spawn) in
:mod:`omnigent.runner.factory_dispatch`. ``ToolManager`` registers them only for
specs that declare ``executor.config.factory_role``; ``sys_factory_spawn`` is
lead-only.
"""

from __future__ import annotations

from typing import Any, Final

from omnigent.factory import (
    HEALTH,
    MAX_HEARTBEAT_S,
    MESSAGE_KINDS,
    MESSAGE_MAX_CHARS,
    MIN_HEARTBEAT_S,
    ROLE_LEAD,
    ROLE_PROJECT_LEAD,
    STATES,
    SUMMARY_MAX_LEN,
)
from omnigent.tools.base import Tool

SYS_SESSION_POST: Final = "sys_session_post"
SYS_FACTORY_ROSTER: Final = "sys_factory_roster"
SYS_FACTORY_SET: Final = "sys_factory_set"
SYS_FACTORY_SPAWN: Final = "sys_factory_spawn"
FACTORY_TOOL_NAMES: Final = frozenset(
    {SYS_SESSION_POST, SYS_FACTORY_ROSTER, SYS_FACTORY_SET, SYS_FACTORY_SPAWN}
)
SPAWN_ROLES: Final = (ROLE_PROJECT_LEAD, ROLE_LEAD)

_HEARTBEAT_DESC = (
    f"LLM heartbeat interval in seconds: 0 turns heartbeats off, otherwise "
    f"{MIN_HEARTBEAT_S}-{MAX_HEARTBEAT_S}."
)


def _function_schema(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """Wrap a parameter schema in the OpenAI function-tool envelope."""
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }


class SysSessionPostTool(Tool):
    """Post a message to another factory member (the factory SendMessage)."""

    @classmethod
    def name(cls) -> str:
        """:returns: ``"sys_session_post"``."""
        return SYS_SESSION_POST

    @classmethod
    def description(cls) -> str:
        """:returns: Human-readable description of the tool."""
        return (
            "Send a message to another member of your software factory: a lead, "
            "a project lead, or an IC in another sandbox. `to` is a session id or "
            "an alias resolved by the server: 'parent' (the session that spawned "
            "you, or your project lead if you are an IC), 'lead' (the factory "
            "root), 'peer_lead' (the other lead), 'supervisor' (who receives your "
            "alerts), or 'project:<slug>' (that project's lead). Delivery is "
            "ASYNCHRONOUS: the call returns {status: 'queued'} immediately, the "
            "server wakes or relaunches the target if it is asleep, and any reply "
            "arrives later as a new message in your conversation, starting with "
            "'[Factory message · from ...]'. Do not wait or poll for it. "
            "kind: 'request' expects an answer, 'reply' answers one, 'info' is a "
            "status update, 'brief' hands over a charter, 'alert' flags a problem. "
            "restart=true (leads and the target's supervisor only) stops a wedged "
            "top-level session and relaunches it with this message. Keep messages "
            f"short (at most {MESSAGE_MAX_CHARS} characters); put bulk content in "
            "files and send the path. Targets must be in your factory. To drive "
            "your OWN sub-agents use sys_session_send instead, which gives you a "
            "completion wake."
        )

    def get_schema(self) -> dict[str, Any]:
        """:returns: The OpenAI-format tool schema."""
        return _function_schema(
            self.name(),
            self.description(),
            {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": (
                            "Target session id (e.g. 'conv_abc123') or an alias: "
                            "'parent', 'lead', 'peer_lead', 'supervisor', or "
                            "'project:<slug>'."
                        ),
                    },
                    "message": {
                        "type": "string",
                        "description": (
                            "The message body. The server prepends a sender "
                            "envelope; never forge one."
                        ),
                        "maxLength": MESSAGE_MAX_CHARS,
                    },
                    "kind": {
                        "type": "string",
                        "enum": sorted(MESSAGE_KINDS),
                        "default": "info",
                        "description": "Message kind; defaults to 'info'.",
                    },
                    "restart": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Stop and relaunch the (top-level) target before "
                            "delivering. Leads and the target's supervisor only."
                        ),
                    },
                },
                "required": ["to", "message"],
                "additionalProperties": False,
            },
        )


class SysFactoryRosterTool(Tool):
    """List the members of the caller's factory."""

    @classmethod
    def name(cls) -> str:
        """:returns: ``"sys_factory_roster"``."""
        return SYS_FACTORY_ROSTER

    @classmethod
    def description(cls) -> str:
        """:returns: Human-readable description of the tool."""
        return (
            "List your software factory: every top-level member (leads and project "
            "leads) with role, project, supervisor, status, runner connectivity, "
            "last activity, heartbeat, state, health, summary and URL, plus each "
            "member's sub-agents. Your first call also registers you as a factory "
            "member if you are not one yet."
        )

    def get_schema(self) -> dict[str, Any]:
        """:returns: The OpenAI-format tool schema."""
        return _function_schema(
            self.name(),
            self.description(),
            {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        )


class SysFactorySetTool(Tool):
    """Update a member's factory labels (beacon, state, supervision)."""

    @classmethod
    def name(cls) -> str:
        """:returns: ``"sys_factory_set"``."""
        return SYS_FACTORY_SET

    @classmethod
    def description(cls) -> str:
        """:returns: Human-readable description of the tool."""
        return (
            "Update factory metadata for yourself (omit session_id) or for another "
            "member. Use it as a beacon: a one-line summary and a health of "
            "ok/degraded/blocked/dead render as badges in the UI. Leads can also "
            "set state (active/paused/done), heartbeat_s, supervisor_session_id "
            "and title on any member; a project lead may set only summary/health "
            "on its own sub-agents. Pass only the fields you want to change."
        )

    def get_schema(self) -> dict[str, Any]:
        """:returns: The OpenAI-format tool schema."""
        return _function_schema(
            self.name(),
            self.description(),
            {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Member to update; omit to update yourself.",
                    },
                    "state": {"type": "string", "enum": sorted(STATES)},
                    "summary": {
                        "type": "string",
                        "description": (
                            f"One-line status beacon, at most {SUMMARY_MAX_LEN} characters."
                        ),
                    },
                    "health": {"type": "string", "enum": sorted(HEALTH)},
                    "heartbeat_s": {"type": "integer", "description": _HEARTBEAT_DESC},
                    "supervisor_session_id": {
                        "type": "string",
                        "description": (
                            "Session that receives this member's alerts; an empty "
                            "string clears it."
                        ),
                    },
                    "title": {"type": "string", "description": "New session title."},
                },
                "required": [],
                "additionalProperties": False,
            },
        )


class SysFactorySpawnTool(Tool):
    """Create a new top-level factory member in its own sandbox (leads only)."""

    @classmethod
    def name(cls) -> str:
        """:returns: ``"sys_factory_spawn"``."""
        return SYS_FACTORY_SPAWN

    @classmethod
    def description(cls) -> str:
        """:returns: Human-readable description of the tool."""
        return (
            "Leads only. Start a new top-level factory member — a project lead "
            "(default) or a second lead — in its OWN sandbox, then deliver "
            "`message` to it as its brief (its charter). `agent` is a built-in "
            "agent name, e.g. 'factory-pl' or 'factory-lead'. `workspaces` are git "
            "repository URLs (optionally '#<branch>') cloned into the sandbox. The "
            "call returns {session_id, url, status: 'provisioning'} right away; "
            "provisioning takes minutes and the brief is delivered once the "
            "sandbox is up. Check sys_factory_roster first: a project that already "
            "has a member that is not done is refused. Spawning a lead while you have no "
            "supervisor makes the two leads supervise each other."
        )

    def get_schema(self) -> dict[str, Any]:
        """:returns: The OpenAI-format tool schema."""
        return _function_schema(
            self.name(),
            self.description(),
            {
                "type": "object",
                "properties": {
                    "agent": {
                        "type": "string",
                        "description": "Built-in agent name, e.g. 'factory-pl'.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Session title, e.g. 'PL · api-version'.",
                    },
                    "message": {
                        "type": "string",
                        "description": "The brief (charter) delivered as the first message.",
                        "maxLength": MESSAGE_MAX_CHARS,
                    },
                    "role": {
                        "type": "string",
                        "enum": list(SPAWN_ROLES),
                        "default": ROLE_PROJECT_LEAD,
                    },
                    "project": {
                        "type": "string",
                        "description": (
                            "Project slug, e.g. 'api-version'; addressable as 'project:<slug>'."
                        ),
                    },
                    "workspaces": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Git repository URLs to clone, e.g. "
                            "['https://github.com/org/repo#main']."
                        ),
                    },
                    "heartbeat_s": {
                        "type": "integer",
                        "description": (
                            f"{_HEARTBEAT_DESC} Defaults to 3600 for a lead and 7200 "
                            "for a project lead."
                        ),
                    },
                    "supervisor_session_id": {
                        "type": "string",
                        "description": (
                            "Who receives the new member's alerts. Defaults to the "
                            "peer lead for a project lead, and to you for a lead."
                        ),
                    },
                },
                "required": ["agent", "title", "message"],
                "additionalProperties": False,
            },
        )
