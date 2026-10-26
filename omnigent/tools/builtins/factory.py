"""Software-factory tools: messaging, roster, beacon, spawn and cockpit reports.

A factory is a labelled tree of ordinary sessions (see :mod:`omnigent.factory`).
These classes are schema-only: the runner dispatches every call to the server's
``/v1/factory`` routes (and ``POST /v1/sessions`` for spawn) in
:mod:`omnigent.runner.factory_dispatch`. ``ToolManager`` registers them only for
specs that declare ``executor.config.factory_role``: ``sys_factory_spawn`` is
lead-only, ``sys_factory_env`` is for the project lead and env_keeper, and
``sys_factory_demo`` for those two and the demo role.
"""

from __future__ import annotations

from typing import Any, Final

from omnigent.factory import (
    AGENT_STAGES,
    DECISION_CONTEXT_MAX,
    DECISION_MAX_OPTIONS,
    DECISION_OPTION_DESCRIPTION_MAX,
    DECISION_OPTION_LABEL_MAX,
    DECISION_QUESTION_MAX,
    DEMO_FILE_KINDS,
    DEMO_MAX_FILES,
    DEMO_STATUSES,
    ENV_STATES,
    HEALTH,
    MAX_HEARTBEAT_S,
    MESSAGE_KINDS,
    MESSAGE_MAX_CHARS,
    MIN_HEARTBEAT_S,
    PR_CLAIMS,
    PREVIEW_REPORT_STATES,
    ROLE_LEAD,
    ROLE_PROJECT_LEAD,
    SENTINEL_VERDICTS,
    STAGES,
    STATES,
    SUMMARY_MAX_LEN,
)
from omnigent.tools.base import Tool

SYS_SESSION_POST: Final = "sys_session_post"
SYS_FACTORY_ROSTER: Final = "sys_factory_roster"
SYS_FACTORY_SET: Final = "sys_factory_set"
SYS_FACTORY_SPAWN: Final = "sys_factory_spawn"
SYS_FACTORY_ASK: Final = "sys_factory_ask"
SYS_FACTORY_PR: Final = "sys_factory_pr"
SYS_FACTORY_ENV: Final = "sys_factory_env"
SYS_FACTORY_DEMO: Final = "sys_factory_demo"
FACTORY_TOOL_NAMES: Final = frozenset(
    {
        SYS_SESSION_POST,
        SYS_FACTORY_ROSTER,
        SYS_FACTORY_SET,
        SYS_FACTORY_SPAWN,
        SYS_FACTORY_ASK,
        SYS_FACTORY_PR,
        SYS_FACTORY_ENV,
        SYS_FACTORY_DEMO,
    }
)
SPAWN_ROLES: Final = (ROLE_PROJECT_LEAD, ROLE_LEAD)
AGENT_STAGE_ORDER: Final = [stage for stage in STAGES if stage in AGENT_STAGES]
_SHA_PATTERN: Final = "^[0-9a-fA-F]{40}$"

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
            "on its own sub-agents. A project lead also claims its project's "
            "pipeline stage on itself with `stage` at each transition; the cockpit "
            "shows the claim next to the stage the server derives from GitHub. "
            "Pass only the fields you want to change."
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
                    "stage": {
                        "type": "string",
                        "enum": AGENT_STAGE_ORDER,
                        "description": (
                            "Project lead only, on itself: the stage your project is in."
                        ),
                    },
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


class SysFactoryAskTool(Tool):
    """Ask the human a question through the cockpit (a tracked decision)."""

    @classmethod
    def name(cls) -> str:
        """:returns: ``"sys_factory_ask"``."""
        return SYS_FACTORY_ASK

    @classmethod
    def description(cls) -> str:
        """:returns: Human-readable description of the tool."""
        return (
            "Ask the human a question. It becomes a decision (D-<n>) in the factory "
            "cockpit and the human's inbox, with one-click options. Use it for every "
            "question or decision you need from the human; never post DECISIONS "
            "NEEDED lists in chat. Returns at once with {decision: 'D-7', status, "
            "deduped}; keep working; the answer arrives as `[Factory decision · D-7 "
            "answered …]`. Asking the same open question again returns the same D-n "
            f"(deduped). At most {DECISION_MAX_OPTIONS} options; mark the one you "
            "recommend. Set blocking=false when you can carry on without the answer. "
            "To take back a question you no longer need, pass withdraw='D-7' (question "
            "then gives the reason)."
        )

    def get_schema(self) -> dict[str, Any]:
        """:returns: The OpenAI-format tool schema."""
        option = {
            "type": "object",
            "properties": {
                "label": {"type": "string", "maxLength": DECISION_OPTION_LABEL_MAX},
                "description": {
                    "type": "string",
                    "maxLength": DECISION_OPTION_DESCRIPTION_MAX,
                },
                "recommended": {"type": "boolean", "default": False},
            },
            "required": ["label"],
            "additionalProperties": False,
        }
        return _function_schema(
            self.name(),
            self.description(),
            {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "maxLength": DECISION_QUESTION_MAX,
                        "description": "One clear question, e.g. 'Where does the badge go?'.",
                    },
                    "options": {
                        "type": "array",
                        "items": option,
                        "maxItems": DECISION_MAX_OPTIONS,
                        "description": "Answer choices; omit for a free-text answer.",
                    },
                    "context": {
                        "type": "string",
                        "maxLength": DECISION_CONTEXT_MAX,
                        "description": "Markdown background the human needs to decide.",
                    },
                    "blocking": {
                        "type": "boolean",
                        "default": True,
                        "description": "Whether your work waits on the answer.",
                    },
                    "allow_text": {
                        "type": "boolean",
                        "default": True,
                        "description": "Whether the human may answer in free text.",
                    },
                    "dedupe_key": {
                        "type": "string",
                        "maxLength": 64,
                        "description": "Stable key for this question; defaults to its text.",
                    },
                    "withdraw": {
                        "type": "string",
                        "description": (
                            "A decision you asked earlier, e.g. 'D-7', to withdraw "
                            "instead of asking."
                        ),
                    },
                },
                "required": ["question"],
                "additionalProperties": False,
            },
        )


class SysFactoryPrTool(Tool):
    """Register a PR with the factory and read back its server-computed gates."""

    @classmethod
    def name(cls) -> str:
        """:returns: ``"sys_factory_pr"``."""
        return SYS_FACTORY_PR

    @classmethod
    def description(cls) -> str:
        """:returns: Human-readable description of the tool."""
        return (
            "Register a pull request with the factory cockpit, optionally with a claim "
            "about it, and get back the gates the server computes from GitHub: "
            "{pr_id, ready, gates, reasons[], claim_mismatch?}. Call it right after "
            "`gh pr create` (claim='opened'), when you report READY FOR HUMAN MERGE "
            "(claim='ready_for_human_merge'), and when a PR is dropped "
            "(claim='abandoned'). The server re-checks GitHub before answering (up "
            "to 10 s), so a premature 'ready' claim comes back with "
            "claim_mismatch=true and the reasons; fix those instead of reporting "
            "ready. Leads (Sentinel) record their independent verification with "
            "sentinel_verdict, head_sha and note. The PR must be on a repo of one "
            "of this factory's projects."
        )

    def get_schema(self) -> dict[str, Any]:
        """:returns: The OpenAI-format tool schema."""
        return _function_schema(
            self.name(),
            self.description(),
            {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "PR URL, e.g. 'https://github.com/org/repo/pull/123'.",
                    },
                    "task": {
                        "type": "string",
                        "description": "Task slug, e.g. 'status-endpoint'.",
                    },
                    "claim": {"type": "string", "enum": list(PR_CLAIMS)},
                    "sentinel_verdict": {
                        "type": "string",
                        "enum": list(SENTINEL_VERDICTS),
                        "description": "Leads only: your independent verification of the head.",
                    },
                    "head_sha": {
                        "type": "string",
                        "description": "The head commit your claim or verdict is about.",
                    },
                    "note": {
                        "type": "string",
                        "maxLength": 1000,
                        "description": "One or two lines of evidence.",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        )


class SysFactoryEnvTool(Tool):
    """Report a project's dev env, or one PR's factory preview, to the cockpit."""

    @classmethod
    def name(cls) -> str:
        """:returns: ``"sys_factory_env"``."""
        return SYS_FACTORY_ENV

    @classmethod
    def description(cls) -> str:
        """:returns: Human-readable description of the tool."""
        return (
            "Report env truth to the factory cockpit. Without `pr` it reports your "
            "project's long-running dev env (state "
            f"{'/'.join(ENV_STATES)}); with `pr` it reports that PR's factory "
            f"preview (state {'/'.join(PREVIEW_REPORT_STATES)}). Report after every "
            "UP, STATUS, PREVIEW, park and DOWN, with the URLs, the deployed commit "
            "(deployed.sha = `git rev-parse HEAD` of what Tilt built) and the Tilt "
            "resources. state='deploying' for a preview is refused with "
            "error='preview_cap' (and park candidates) when the factory already "
            "runs its maximum number of previews: park one first. URLs must be "
            "https on the env domain."
        )

    def get_schema(self) -> dict[str, Any]:
        """:returns: The OpenAI-format tool schema."""
        url = {"type": "string"}
        resource = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "status": {"type": "string"},
                "ready": {"type": "boolean"},
                "restarts": {"type": "integer", "minimum": 0},
                "detail": {"type": "string"},
            },
            "required": ["name"],
            "additionalProperties": False,
        }
        return _function_schema(
            self.name(),
            self.description(),
            {
                "type": "object",
                "properties": {
                    "state": {
                        "type": "string",
                        "enum": sorted({*ENV_STATES, *PREVIEW_REPORT_STATES}),
                    },
                    "env_id": {
                        "type": "string",
                        "description": "Env id, e.g. '1a2b3c4d-5e6f7a-9f3c' or '<base>-p9653'.",
                    },
                    "urls": {
                        "type": "object",
                        "properties": {"app": url, "api": url, "minio": url, "tilt_ui": url},
                        "additionalProperties": False,
                    },
                    "deployed": {
                        "type": "object",
                        "properties": {
                            "sha": {"type": "string"},
                            "ref": {"type": "string"},
                            "tooling_overlay": {
                                "type": "string",
                                "description": "Sha of preview tooling overlaid on the PR head.",
                            },
                        },
                        "additionalProperties": False,
                    },
                    "pr": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "PR number; set it to report that PR's preview.",
                    },
                    "slot": {"type": "integer", "minimum": 1, "maximum": 3},
                    "namespace": {"type": "string", "description": "Preview namespace."},
                    "resources": {"type": "array", "items": resource, "maxItems": 100},
                    "note": {"type": "string", "maxLength": 500},
                },
                "required": ["state", "env_id", "urls", "deployed"],
                "additionalProperties": False,
            },
        )


class SysFactoryDemoTool(Tool):
    """Publish a recorded demo of a PR head (files are uploaded by the runner)."""

    @classmethod
    def name(cls) -> str:
        """:returns: ``"sys_factory_demo"``."""
        return SYS_FACTORY_DEMO

    @classmethod
    def description(cls) -> str:
        """:returns: Human-readable description of the tool."""
        return (
            "Publish a recorded demo of a PR head so humans can review it in the "
            "factory cockpit: the video plays inline, screenshots open in a "
            "lightbox, API calls expand. The runner uploads each file in `files` "
            "(paths under ~/factory or your workspace; .webm .mp4 .png .jpg .jpeg "
            ".gif .json .har .txt .md .log .ts .zip; video ≤ 50 MB, image ≤ 5 MB, "
            f"text/json ≤ 2 MB, ≤ {DEMO_MAX_FILES} files, ≤ 120 MB in all). With "
            "manifest_path (the harness's demo.json) the steps, timings and the "
            "artifacts it lists are attached too. Returns {demo_id, demo_url, "
            "status, files, comment_markdown}: post comment_markdown on the PR with "
            "`gh pr comment <url> --body-file <file>` (none for ERROR)."
        )

    def get_schema(self) -> dict[str, Any]:
        """:returns: The OpenAI-format tool schema."""
        demo_file = {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Local file path."},
                "kind": {"type": "string", "enum": list(DEMO_FILE_KINDS)},
                "caption": {"type": "string", "maxLength": 200},
                "step": {"type": "integer", "minimum": 1},
            },
            "required": ["path", "kind"],
            "additionalProperties": False,
        }
        return _function_schema(
            self.name(),
            self.description(),
            {
                "type": "object",
                "properties": {
                    "pr_url": {"type": "string"},
                    "head_sha": {
                        "type": "string",
                        "pattern": _SHA_PATTERN,
                        "description": "The 40-char commit the demo ran against.",
                    },
                    "title": {"type": "string", "maxLength": 256},
                    "summary": {
                        "type": "string",
                        "maxLength": 1000,
                        "description": "What the demo shows, one or two sentences.",
                    },
                    "status": {"type": "string", "enum": list(DEMO_STATUSES), "default": "PASS"},
                    "files": {"type": "array", "items": demo_file, "maxItems": DEMO_MAX_FILES},
                    "manifest_path": {
                        "type": "string",
                        "description": (
                            "The harness's demo.json, e.g. "
                            "'~/factory/demos/9630/7767f0d/demo.json'."
                        ),
                    },
                    "plan_source": {
                        "type": "string",
                        "enum": ["pr-body", "derived"],
                        "description": "Whether the steps came from the PR's Demo plan.",
                    },
                    "preview_url": {"type": "string", "description": "Preview app URL."},
                },
                "required": ["pr_url", "head_sha", "title", "summary", "files"],
                "additionalProperties": False,
            },
        )
