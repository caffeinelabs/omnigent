"""Runner-side executors for the software-factory tools.

The runner has no store access, so each factory tool proxies the server's
``/v1/factory`` routes over ``server_client``, which already carries the
runner's owner bearer and its tunnel-binding token (the server uses that token
to prove the sender is who it claims). ``sys_factory_spawn`` composes those
routes with the ordinary ``GET /v1/agents`` and the JSON ``POST /v1/sessions``
managed create, and attaches the project's stored charter to the brief.

The cockpit reports (``sys_factory_ask`` / ``_pr`` / ``_env`` / ``_demo``)
call ``/v1/factory/agent/*``. ``sys_factory_demo`` is the one that reads local
files: only allowlisted types under ``~/factory`` or the session workspace
(symlinks followed) are uploaded, as multipart.

Factory roles also get factory-scoped session reads: ``sys_session_get_history``
and ``sys_session_get_info`` refuse targets outside the caller's factory, and
``sys_session_list`` lists roster members instead of every session the human
owns. That bounds what a prompt-injected member can read.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote

import httpx

from omnigent.factory import (
    AGENT_STAGES,
    DECISION_CONTEXT_MAX,
    DECISION_MAX_OPTIONS,
    DECISION_OPTION_DESCRIPTION_MAX,
    DECISION_OPTION_LABEL_MAX,
    DECISION_QUESTION_MAX,
    DEFAULT_HEARTBEAT_S,
    DEMO_CLASS_MAX_BYTES,
    DEMO_FILE_KINDS,
    DEMO_MANIFEST_KINDS,
    DEMO_MANIFEST_MAX_BYTES,
    DEMO_MAX_FILES,
    DEMO_MAX_TOTAL_BYTES,
    ENV_STATES,
    HEALTH,
    LABEL_BUNDLE,
    LABEL_HEARTBEAT_S,
    LABEL_ID,
    LABEL_PARENT,
    LABEL_PROJECT,
    LABEL_ROLE,
    LABEL_STATE,
    LABEL_SUPERVISOR,
    LABEL_VERSION,
    MESSAGE_KINDS,
    MESSAGE_MAX_CHARS,
    PR_CLAIMS,
    PREVIEW_REPORT_STATES,
    ROLE_LEAD,
    ROLE_PROJECT_LEAD,
    SENTINEL_VERDICTS,
    STATE_ACTIVE,
    STATE_DONE,
    STATES,
    SUMMARY_MAX_LEN,
    clamp_label_value,
    demo_file_type,
    demo_manifest_conflict,
    demo_status,
    factory_role_from_spec,
    is_full_sha,
    parse_pr_url,
    valid_heartbeat_s,
)
from omnigent.native.native_coding_agents import public_agent_name
from omnigent.tools.builtins.factory import (
    SPAWN_ROLES,
    SYS_FACTORY_ASK,
    SYS_FACTORY_DEMO,
    SYS_FACTORY_ENV,
    SYS_FACTORY_PR,
    SYS_FACTORY_ROSTER,
    SYS_FACTORY_SET,
    SYS_FACTORY_SPAWN,
    SYS_SESSION_POST,
)
from omnigent.util.session_lifecycle import is_session_closed

_logger = logging.getLogger(__name__)

_JsonObject = dict[str, Any]
_PublishEvent = Callable[[str, _JsonObject], None]

_TIMEOUT_S: Final = 30.0
DEFAULT_MAX_PODS: Final = 12
_AGENT_PAGE_LIMIT: Final = 1000
_AGENT_MAX_PAGES: Final = 10
_AGENT_NAMES_IN_ERROR: Final = 50
# Matches the roster's per-member sub-agent bound.
_CHILD_PAGE_LIMIT: Final = 200

# Membership answers are stable for a session's life, so one lookup serves a
# whole turn of scoped reads; only definitive answers (200/403/404) are cached.
_MEMBERSHIP_CACHE_TTL_S: Final = 60.0
_MEMBERSHIP_CACHE_MAX: Final = 1024
_membership_cache: dict[tuple[str, str], tuple[float, int, _JsonObject | None]] = {}
_CACHED_MEMBERSHIP_STATUSES: Final = frozenset({200, 403, 404})


@dataclass
class _CallerLock:
    """A per-caller lock and how many spawns hold or await it."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


# One spawn at a time per lead, so the pod-cap and duplicate-project checks
# see every member the lead created before.
_spawn_locks: dict[str, _CallerLock] = {}

# Codes the factory routes return; recognised in any server error envelope.
_FACTORY_ERROR_CODES: Final = frozenset(
    {
        "session_out_of_factory",
        "restart_not_allowed",
        "unknown_target",
        "sender_not_bound",
        "rate_limited",
        "message_too_large",
        "too_many_open_decisions",
        "decision_not_open",
        "not_a_pr_url",
        "pr_out_of_factory",
        "sentinel_lead_only",
        "not_your_project",
        "invalid_stage",
        "invalid_state",
        "invalid_url",
        "preview_cap",
        "demo_too_large",
        "file_not_allowed",
        "file_missing",
        "status_mismatch",
        "sha_mismatch",
        "pr_mismatch",
    }
)
# The server re-polls GitHub for up to 10 s before answering sys_factory_pr.
_PR_TIMEOUT_S: Final = 30.0
_DEMO_UPLOAD_TIMEOUT_S: Final = 120.0
_DECISION_REF_RE: Final = re.compile(r"(?:[Dd]-)?([1-9][0-9]{0,8})")
_ABBREV_SHA_RE: Final = re.compile(r"[0-9a-fA-F]{7,40}")
_NOTE_MAX: Final = 1000
_ENV_NOTE_MAX: Final = 500
_CAPTION_MAX: Final = 200
_STATUS_ERROR_CODES: Final = {413: "message_too_large", 429: "rate_limited"}
_CODE_TOKEN_RE: Final = re.compile(r"[a-z][a-z0-9_]*")
# Session ids are opaque tokens (hex uuids); refusing separators and dot
# segments keeps an LLM-supplied id from steering a request to another route.
_SESSION_ID_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")

_HISTORY_TOOL: Final = "sys_session_get_history"
_INFO_TOOL: Final = "sys_session_get_info"
_LIST_TOOL: Final = "sys_session_list"


def _error(code: str, **extra: object) -> str:
    """Serialize a tool error as ``{"error": code, ...}``."""
    return json.dumps({"error": code, **extra})


def _response_body(resp: httpx.Response) -> object:
    """Return the parsed JSON body, or ``None`` when it is not JSON."""
    try:
        return resp.json()
    except ValueError:
        return None


def _error_payload(status: int, body: object, text: str, tool_name: str) -> _JsonObject:
    """
    Normalize a server error response to ``{error, status, message?}``.

    Accepts the factory routes' ``{"error": "<code>"}``, the
    ``OmnigentError`` envelope ``{"error": {"code", "message"}}`` and FastAPI's
    ``{"detail": ...}``, preferring a known factory code wherever it appears.

    :param status: HTTP status code, e.g. ``403``.
    :param body: Parsed JSON body, or ``None``.
    :param text: Raw response text, used when the body is not JSON.
    :param tool_name: Tool name for the fallback code.
    :returns: The error object passed back to the LLM.
    """
    candidates: list[str] = []
    extra: _JsonObject = {}
    if isinstance(body, dict):
        for source in (body.get("error"), body.get("detail"), body):
            if isinstance(source, str):
                candidates.append(source)
            elif isinstance(source, dict):
                candidates.extend(
                    v for k in ("error", "code", "message") if isinstance(v := source.get(k), str)
                )
        if (validation := _validation_message(body.get("detail"))) is not None:
            candidates.append(validation)
        # Structured extras (e.g. preview_cap's park_candidates) reach the agent too.
        extra = {
            k: v
            for k, v in body.items()
            if k not in {"error", "detail", "message"}
            and isinstance(v, (str, int, float, bool, list, dict))
        }
    code = (
        next((c for c in candidates if c in _FACTORY_ERROR_CODES), None)
        or _STATUS_ERROR_CODES.get(status)
        or next((c for c in candidates if _CODE_TOKEN_RE.fullmatch(c)), None)
        or f"{tool_name} returned {status}"
    )
    message = next((c for c in candidates if c != code), None)
    if message is None and body is None and text:
        message = text[:200]
    payload: _JsonObject = {**extra, "error": code, "status": status}
    if message:
        payload["message"] = message
    return payload


def _validation_message(detail: object) -> str | None:
    """
    First message of a FastAPI 422 ``detail`` list, prefixed with its field.

    :param detail: The body's ``detail``, e.g.
        ``[{"loc": ["body", "title"], "msg": "String should have at most 200 characters"}]``.
    :returns: e.g. ``"title: String should have at most 200 characters"``, or ``None``.
    """
    if not isinstance(detail, list):
        return None
    for entry in detail:
        if isinstance(entry, dict) and isinstance(msg := entry.get("msg"), str) and msg:
            loc = entry.get("loc")
            parts = [str(p) for p in loc if p != "body"] if isinstance(loc, list) else []
            return f"{'.'.join(parts)}: {msg}" if parts else msg
    return None


def _server_message(payload: _JsonObject, tool_name: str) -> str | None:
    """The server's own words from a normalized error, if it gave any."""
    message = payload.get("message")
    if isinstance(message, str) and message:
        return message
    code = payload.get("error")
    if isinstance(code, str) and not code.startswith(f"{tool_name} returned"):
        return code
    return None


def _valid_session_id(value: object) -> bool:
    return isinstance(value, str) and _SESSION_ID_RE.fullmatch(value) is not None


def _session_path(prefix: str, session_id: str, suffix: str = "") -> str:
    """``{prefix}/{session_id}{suffix}`` with the id percent-encoded as one segment."""
    return f"{prefix}/{quote(session_id, safe='')}{suffix}"


async def _request(
    server_client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    tool_name: str,
    params: dict[str, str | int] | None = None,
    body: _JsonObject | None = None,
    timeout: float = _TIMEOUT_S,
) -> httpx.Response | str:
    """Send one server request; a transport failure becomes a JSON error string."""
    try:
        return await server_client.request(method, path, params=params, json=body, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — any transport error becomes a tool error.
        return _error(f"{tool_name} failed: {exc}")


async def _passthrough(
    server_client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    tool_name: str,
    params: dict[str, str | int] | None = None,
    body: _JsonObject | None = None,
    timeout: float = _TIMEOUT_S,
) -> str:
    """Proxy a factory route, returning its JSON on success or ``{error}``."""
    resp = await _request(
        server_client,
        method,
        path,
        tool_name=tool_name,
        params=params,
        body=body,
        timeout=timeout,
    )
    if isinstance(resp, str):
        return resp
    parsed = _response_body(resp)
    if resp.status_code >= 400:
        return json.dumps(_error_payload(resp.status_code, parsed, resp.text, tool_name))
    return json.dumps(parsed) if parsed is not None else json.dumps({"result": resp.text})


async def _get_object(
    server_client: httpx.AsyncClient,
    path: str,
    *,
    tool_name: str,
    params: dict[str, str | int] | None = None,
) -> _JsonObject | str:
    """GET a JSON object; any failure becomes a JSON error string."""
    resp = await _request(server_client, "GET", path, tool_name=tool_name, params=params)
    if isinstance(resp, str):
        return resp
    parsed = _response_body(resp)
    if resp.status_code >= 400:
        return json.dumps(_error_payload(resp.status_code, parsed, resp.text, tool_name))
    if not isinstance(parsed, dict):
        return _error(f"{tool_name}: unexpected response from {path}")
    return parsed


def _membership_path(session_id: str) -> str:
    return _session_path("/v1/factory/membership", session_id)


async def execute_factory_tool(
    tool_name: str,
    args: _JsonObject,
    *,
    server_client: httpx.AsyncClient | None,
    conversation_id: str | None,
    agent_spec: object = None,
    publish_event: _PublishEvent | None = None,
    workspace: Path | None = None,
) -> str:
    """
    Dispatch one factory tool call.

    :param tool_name: One of :data:`omnigent.tools.builtins.factory.FACTORY_TOOL_NAMES`.
    :param args: Parsed tool arguments.
    :param server_client: HTTP client pointed at the Omnigent server.
    :param conversation_id: The calling session id, stamped as the sender.
    :param agent_spec: The caller's spec; spawn reads ``factory_max_pods``.
    :param publish_event: SSE publish callback for spawn's ``session.created``.
    :param workspace: The session's workspace; demo files may live under it.
    :returns: The JSON tool result.
    """
    if server_client is None:
        return _error(f"{tool_name} requires server access")
    if not conversation_id:
        return _error(f"{tool_name} requires a session id")
    if tool_name == SYS_SESSION_POST:
        return await _post_message(args, server_client=server_client, caller=conversation_id)
    if tool_name == SYS_FACTORY_ROSTER:
        return await _passthrough(
            server_client,
            "GET",
            "/v1/factory/roster",
            tool_name=tool_name,
            params={"from_session_id": conversation_id},
        )
    if tool_name == SYS_FACTORY_SET:
        return await _set_member(args, server_client=server_client, caller=conversation_id)
    if tool_name == SYS_FACTORY_SPAWN:
        return await _spawn(
            args,
            server_client=server_client,
            caller=conversation_id,
            agent_spec=agent_spec,
            publish_event=publish_event,
        )
    if tool_name == SYS_FACTORY_ASK:
        return await _ask(args, server_client=server_client, caller=conversation_id)
    if tool_name == SYS_FACTORY_PR:
        return await _register_pr(args, server_client=server_client, caller=conversation_id)
    if tool_name == SYS_FACTORY_ENV:
        return await _report_env(args, server_client=server_client, caller=conversation_id)
    if tool_name == SYS_FACTORY_DEMO:
        return await _publish_demo(
            args, server_client=server_client, caller=conversation_id, workspace=workspace
        )
    return _error(f"unknown factory tool {tool_name!r}")


def _message_error(tool_name: str, message: object) -> str | None:
    """Validate a message body against the server's size cap."""
    if not isinstance(message, str) or not message.strip():
        return _error(f"{tool_name} requires a non-empty 'message' string")
    if len(message) > MESSAGE_MAX_CHARS:
        return _error(
            "message_too_large",
            max_chars=MESSAGE_MAX_CHARS,
            length=len(message),
            message="put bulk content in a file and send its path",
        )
    return None


async def _post_message(
    args: _JsonObject,
    *,
    server_client: httpx.AsyncClient,
    caller: str,
) -> str:
    """``sys_session_post`` → ``POST /v1/factory/messages`` stamped with the caller."""
    tool = SYS_SESSION_POST
    to = args.get("to")
    if not isinstance(to, str) or not to.strip():
        return _error(f"{tool} requires a non-empty 'to' (session id or alias)")
    message = args.get("message")
    if (invalid := _message_error(tool, message)) is not None:
        return invalid
    kind = args.get("kind") or "info"
    if kind not in MESSAGE_KINDS:
        return _error(f"{tool} 'kind' must be one of {sorted(MESSAGE_KINDS)}")
    restart = args.get("restart", False)
    if restart is None:
        restart = False
    if not isinstance(restart, bool):
        return _error(f"{tool} 'restart' must be a boolean")
    # The sender is always the calling session; an argument cannot override it.
    body: _JsonObject = {
        "from_session_id": caller,
        "to": to.strip(),
        "message": message,
        "kind": kind,
        "restart": restart,
    }
    return await _passthrough(
        server_client, "POST", "/v1/factory/messages", tool_name=tool, body=body
    )


async def _set_member(
    args: _JsonObject,
    *,
    server_client: httpx.AsyncClient,
    caller: str,
) -> str:
    """``sys_factory_set`` → ``PATCH /v1/factory/sessions/{target}``."""
    tool = SYS_FACTORY_SET
    target = args.get("session_id") or caller
    if isinstance(target, str):
        target = target.strip()
    if not _valid_session_id(target):
        return _error(
            "invalid_session_id",
            session_id=target,
            message=f"{tool} 'session_id' must be a member's session id from sys_factory_roster",
        )
    patch: _JsonObject = {}
    state = args.get("state")
    if state is not None:
        if state not in STATES:
            return _error(f"{tool} 'state' must be one of {sorted(STATES)}")
        patch["state"] = state
    summary = args.get("summary")
    if summary is not None:
        if not isinstance(summary, str):
            return _error(f"{tool} 'summary' must be a string")
        patch["summary"] = clamp_label_value(" ".join(summary.split()), SUMMARY_MAX_LEN)
    health = args.get("health")
    if health is not None:
        if health not in HEALTH:
            return _error(f"{tool} 'health' must be one of {sorted(HEALTH)}")
        patch["health"] = health
    heartbeat_s = args.get("heartbeat_s")
    if heartbeat_s is not None:
        if (invalid := _heartbeat_error(tool, heartbeat_s)) is not None:
            return invalid
        patch["heartbeat_s"] = heartbeat_s
    supervisor = args.get("supervisor_session_id")
    if supervisor is not None:
        if not isinstance(supervisor, str):
            return _error(f"{tool} 'supervisor_session_id' must be a string")
        # An empty string clears the supervisor.
        patch["supervisor_session_id"] = supervisor.strip()
    title = args.get("title")
    if title is not None:
        if not isinstance(title, str) or not title.strip():
            return _error(f"{tool} 'title' must be a non-empty string")
        patch["title"] = title.strip()
    stage = args.get("stage")
    if stage is not None:
        if stage not in AGENT_STAGES:
            return _error(
                "invalid_stage",
                stage=stage,
                message=f"{tool} 'stage' must be one of {sorted(AGENT_STAGES)}",
            )
        if target != caller:
            return _error(
                "not_your_project",
                message="a project lead claims its stage on itself; omit session_id",
            )
        patch["stage"] = stage
    if not patch:
        return _error(f"{tool} requires at least one field to change")
    return await _passthrough(
        server_client,
        "PATCH",
        _session_path("/v1/factory/sessions", target),
        tool_name=tool,
        body={"from_session_id": caller, **patch},
    )


def _heartbeat_error(tool_name: str, value: object) -> str | None:
    """Validate a heartbeat interval (``0`` or within the supervisor's bounds)."""
    if isinstance(value, bool) or not isinstance(value, int) or not valid_heartbeat_s(value):
        return _error(f"{tool_name} 'heartbeat_s' must be 0 (off) or 120-86400 seconds")
    return None


def _max_pods(agent_spec: object) -> int:
    """Read ``executor.config.factory_max_pods`` (default :data:`DEFAULT_MAX_PODS`)."""
    config = getattr(getattr(agent_spec, "executor", None), "config", None)
    raw = config.get("factory_max_pods") if isinstance(config, dict) else None
    if isinstance(raw, str) and raw.strip().isdigit():
        raw = int(raw.strip())
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return raw
    return DEFAULT_MAX_PODS


def _roster_members(roster: _JsonObject) -> list[_JsonObject]:
    members = roster.get("members")
    return [m for m in members if isinstance(m, dict)] if isinstance(members, list) else []


async def _resolve_builtin_agent(
    server_client: httpx.AsyncClient,
    name: str,
) -> tuple[str | None, str | None]:
    """
    Resolve a built-in agent name to its id via ``GET /v1/agents``.

    On a name collision a seeded built-in (``builtin: true``) wins over a
    user-registered template, as in the web picker; otherwise the oldest row.

    :returns: ``(agent_id, None)`` on a match, else ``(None, error_json)``.
    """
    tool = SYS_FACTORY_SPAWN
    after: str | None = None
    names: list[str] = []
    fallback_id: str | None = None
    for _ in range(_AGENT_MAX_PAGES):
        params: dict[str, str | int] = {"limit": _AGENT_PAGE_LIMIT, "order": "asc"}
        if after is not None:
            params["after"] = after
        page = await _get_object(server_client, "/v1/agents", tool_name=tool, params=params)
        if isinstance(page, str):
            return None, page
        rows = page.get("data")
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            row_name, row_id = row.get("name"), row.get("id")
            if row_name == name and isinstance(row_id, str) and row_id:
                if row.get("builtin") is True:
                    return row_id, None
                fallback_id = fallback_id or row_id
            if isinstance(row_name, str):
                names.append(row_name)
        last_id = page.get("last_id")
        if page.get("has_more") is not True or not isinstance(last_id, str) or not last_id:
            break
        after = last_id
    if fallback_id is not None:
        return fallback_id, None
    return None, _error(
        "agent_not_found",
        agent=name,
        available=sorted(set(names))[:_AGENT_NAMES_IN_ERROR],
    )


async def _version_labels(
    server_client: httpx.AsyncClient, caller: str, agent_id: str
) -> dict[str, str]:
    """The platform version and bundle labels for a new member; ``{}`` when unavailable."""
    found = await _get_object(
        server_client,
        "/v1/factory/agent/version",
        tool_name=SYS_FACTORY_SPAWN,
        params={"from_session_id": caller, "agent_id": agent_id},
    )
    labels = found.get("labels") if isinstance(found, dict) else None
    if not isinstance(labels, dict):
        return {}
    return {
        key: value
        for key, value in labels.items()
        if key in (LABEL_VERSION, LABEL_BUNDLE) and isinstance(value, str) and value
    }


def _default_supervisor(
    role: str,
    caller: str,
    members: list[_JsonObject],
) -> str:
    """The peer lead supervises a project lead; a new lead is supervised by its spawner."""
    if role == ROLE_PROJECT_LEAD:
        for member in members:
            session_id = member.get("session_id")
            # A retired (done) lead may still be listed; it no longer handles alerts.
            if member.get("role") != ROLE_LEAD or member.get("state") == STATE_DONE:
                continue
            if isinstance(session_id, str) and session_id and session_id != caller:
                return session_id
    return caller


def _live_project_member(members: list[_JsonObject], project: str) -> _JsonObject | None:
    """The not-done member already running *project*, if any."""
    for member in members:
        if member.get("project") == project and member.get("state") != STATE_DONE:
            return member
    return None


@asynccontextmanager
async def _spawn_lock(caller: str) -> AsyncIterator[None]:
    """Serialize spawns per lead; the entry is dropped once no call holds it."""
    entry = _spawn_locks.setdefault(caller, _CallerLock())
    entry.users += 1
    try:
        async with entry.lock:
            yield
    finally:
        entry.users -= 1
        if entry.users == 0:
            _spawn_locks.pop(caller, None)


def _publish_created(
    publish_event: _PublishEvent | None,
    *,
    caller: str,
    session_id: str,
    agent_id: str,
) -> None:
    """Announce the new member on the caller's stream so its Agents panel refreshes."""
    if publish_event is None:
        return
    from omnigent.server.schemas import SessionCreatedEvent

    try:
        evt = SessionCreatedEvent(
            type="session.created",
            conversation_id=caller,
            child_session_id=session_id,
            agent_id=agent_id,
            parent_session_id=caller,
        )
        publish_event(caller, evt.model_dump())
    except Exception:  # noqa: BLE001 — a UI refresh hint must never fail the spawn.
        _logger.warning("session.created publish failed for %s", session_id, exc_info=True)


@dataclass(frozen=True)
class _SpawnArgs:
    """Validated ``sys_factory_spawn`` arguments."""

    agent: str
    title: str
    message: str
    role: str
    project: str | None
    workspaces: list[str]
    heartbeat_s: int | None
    supervisor: str | None


def _spawn_args(args: _JsonObject) -> _SpawnArgs | str:
    """Validate spawn arguments locally; returns them or a JSON error."""
    tool = SYS_FACTORY_SPAWN
    agent, title, message = args.get("agent"), args.get("title"), args.get("message")
    if not isinstance(agent, str) or not agent.strip():
        return _error(f"{tool} requires a built-in 'agent' name")
    if not isinstance(title, str) or not title.strip():
        return _error(f"{tool} requires a non-empty 'title'")
    if (invalid := _message_error(tool, message)) is not None:
        return invalid
    role = args.get("role") or ROLE_PROJECT_LEAD
    if role not in SPAWN_ROLES:
        return _error(f"{tool} 'role' must be one of {list(SPAWN_ROLES)}")
    project = args.get("project")
    if project is not None and (not isinstance(project, str) or not project.strip()):
        return _error(f"{tool} 'project' must be a non-empty string")
    workspaces = args.get("workspaces")
    if workspaces is not None and (
        not isinstance(workspaces, list)
        or not all(isinstance(w, str) and w.strip() for w in workspaces)
    ):
        return _error(f"{tool} 'workspaces' must be a list of repository URLs")
    heartbeat_s = args.get("heartbeat_s")
    if heartbeat_s is not None and (invalid := _heartbeat_error(tool, heartbeat_s)) is not None:
        return invalid
    supervisor = args.get("supervisor_session_id")
    if supervisor is not None and (not isinstance(supervisor, str) or not supervisor.strip()):
        return _error(f"{tool} 'supervisor_session_id' must be a non-empty string")
    return _SpawnArgs(
        agent=agent.strip(),
        title=title.strip(),
        message=str(message),
        role=role,
        project=clamp_label_value(project.strip()) if project else None,
        workspaces=[w.strip() for w in workspaces or []],
        heartbeat_s=heartbeat_s,
        supervisor=supervisor.strip() if supervisor else None,
    )


async def _spawn(
    args: _JsonObject,
    *,
    server_client: httpx.AsyncClient,
    caller: str,
    agent_spec: object,
    publish_event: _PublishEvent | None,
) -> str:
    """
    ``sys_factory_spawn``: create a top-level managed member and brief it.

    Steps: caller must be a lead; enforce the pod cap and refuse a duplicate
    project from the roster; resolve the built-in agent; ``POST /v1/sessions``
    (``host_type: managed``, factory labels, no ``parent_session_id`` and no
    ``initial_items`` — managed initial items are stored as history only);
    post the brief; set mutual supervision for a second lead; announce
    ``session.created``. Spawns from one lead run one at a time.
    """
    spawn = _spawn_args(args)
    if isinstance(spawn, str):
        return spawn
    async with _spawn_lock(caller):
        return await _spawn_member(
            spawn,
            server_client=server_client,
            caller=caller,
            agent_spec=agent_spec,
            publish_event=publish_event,
        )


async def _spawn_member(
    spawn: _SpawnArgs,
    *,
    server_client: httpx.AsyncClient,
    caller: str,
    agent_spec: object,
    publish_event: _PublishEvent | None,
) -> str:
    """The server-side steps of :func:`_spawn`, run under the caller's spawn lock."""
    tool = SYS_FACTORY_SPAWN
    membership = await _get_object(
        server_client, _membership_path(caller), tool_name=tool, params={"from_session_id": caller}
    )
    if isinstance(membership, str):
        return membership
    if membership.get("role") != ROLE_LEAD:
        return _error(
            "spawn_requires_lead",
            role=membership.get("role"),
            message="only a factory lead may spawn; ask your lead with sys_session_post",
        )
    factory_id = membership.get("factory_id")
    if not isinstance(factory_id, str) or not factory_id:
        return _error("not_a_factory_member", session_id=caller)

    roster = await _get_object(
        server_client, "/v1/factory/roster", tool_name=tool, params={"from_session_id": caller}
    )
    if isinstance(roster, str):
        return roster
    members = _roster_members(roster)
    # Done members idle out and release their pod, so they don't count.
    live = sum(1 for m in members if m.get("state") != STATE_DONE)
    max_pods = _max_pods(agent_spec)
    if live >= max_pods:
        return _error(
            "pod_cap",
            max_pods=max_pods,
            members=live,
            message="mark finished members state=done or reuse an existing project lead",
        )
    if spawn.project and (existing := _live_project_member(members, spawn.project)):
        return _error(
            "duplicate_project",
            project=spawn.project,
            session_id=existing.get("session_id"),
            state=existing.get("state"),
            message=(
                "that project already has a member: message it with sys_session_post "
                "(resume a paused one with sys_factory_set state=active), or mark it done first"
            ),
        )

    agent_id, agent_error = await _resolve_builtin_agent(server_client, spawn.agent)
    if agent_id is None:
        return agent_error or _error("agent_not_found", agent=spawn.agent)

    role = spawn.role
    supervisor_id = spawn.supervisor or _default_supervisor(role, caller, members)
    heartbeat_s = spawn.heartbeat_s
    labels: dict[str, str] = {
        LABEL_ID: factory_id,
        LABEL_ROLE: role,
        LABEL_PARENT: caller,
        LABEL_SUPERVISOR: supervisor_id,
        LABEL_STATE: STATE_ACTIVE,
        LABEL_HEARTBEAT_S: str(
            heartbeat_s if heartbeat_s is not None else DEFAULT_HEARTBEAT_S[role]
        ),
    }
    if spawn.project:
        labels[LABEL_PROJECT] = spawn.project
    labels.update(await _version_labels(server_client, caller, agent_id))
    create_body: _JsonObject = {
        "agent_id": agent_id,
        "host_type": "managed",
        "title": spawn.title,
        "labels": labels,
    }
    if spawn.workspaces:
        create_body["workspaces"] = spawn.workspaces
    resp = await _request(server_client, "POST", "/v1/sessions", tool_name=tool, body=create_body)
    if isinstance(resp, str):
        return resp
    created = _response_body(resp)
    if resp.status_code == 404:
        return _error("agent_not_found", agent=spawn.agent, agent_id=agent_id)
    if resp.status_code >= 400:
        payload = _error_payload(resp.status_code, created, resp.text, tool)
        if resp.status_code not in (401, 403):
            return json.dumps(payload)
        denied: _JsonObject = {"agent": spawn.agent, "status": resp.status_code}
        if (server_message := _server_message(payload, tool)) is not None:
            denied["message"] = server_message
        return _error("access_denied", **denied)
    new_id = created.get("id") if isinstance(created, dict) else None
    if not isinstance(new_id, str) or not new_id:
        return _error("server did not return the new session id")

    result: _JsonObject = {
        "session_id": new_id,
        "url": f"/c/{new_id}",
        "status": "provisioning",
        "agent_id": agent_id,
        "role": role,
        "supervisor_session_id": supervisor_id,
    }
    if spawn.project:
        result["project"] = spawn.project

    message, charter = await _brief_with_charter(spawn, server_client=server_client, caller=caller)
    result.update(charter)
    brief = json.loads(
        await _passthrough(
            server_client,
            "POST",
            "/v1/factory/messages",
            tool_name=tool,
            body={
                "from_session_id": caller,
                "to": new_id,
                "message": message,
                "kind": "brief",
                "restart": False,
            },
        )
    )
    if isinstance(brief, dict) and "error" in brief:
        result["brief_error"] = brief
        result["hint"] = f'resend the brief with sys_session_post(to="{new_id}", kind="brief")'
    else:
        result["brief"] = brief

    if role == ROLE_LEAD and not membership.get("supervisor_session_id"):
        result["mutual_supervision"] = await _set_mutual_supervision(
            server_client, caller=caller, peer=new_id
        )

    _publish_created(publish_event, caller=caller, session_id=new_id, agent_id=agent_id)
    return json.dumps(result)


_TRUNCATED_MARK: Final = " […]"


def append_charter(message: str, charter: str) -> str:
    """
    Append the stored charter to a spawn brief within :data:`MESSAGE_MAX_CHARS`.

    On overflow the human-written part is shortened, never the charter.

    :param message: The lead's brief, e.g. ``"Start. Your charter follows."``.
    :param charter: The rendered ``CHARTER · <slug>`` block.
    :returns: ``"<message>\n\n<charter>"``, the message clipped to fit.
    """
    separator = "\n\n"
    head = message.rstrip()
    room = MESSAGE_MAX_CHARS - len(charter) - len(separator)
    if room <= len(_TRUNCATED_MARK):
        # Only a degenerate charter fills the whole budget; it still wins.
        return charter[:MESSAGE_MAX_CHARS]
    if len(head) > room:
        head = head[: room - len(_TRUNCATED_MARK)].rstrip() + _TRUNCATED_MARK
    return f"{head}{separator}{charter}"


async def _brief_with_charter(
    spawn: _SpawnArgs,
    *,
    server_client: httpx.AsyncClient,
    caller: str,
) -> tuple[str, _JsonObject]:
    """
    The brief to deliver: the lead's message plus the project's stored charter.

    Best effort: no project, no stored charter (404) or a failed lookup leave
    the message unchanged; a failure is reported back as ``charter_error``.

    :returns: ``(message, result fields)``.
    """
    if not spawn.project:
        return spawn.message, {}
    resp = await _request(
        server_client,
        "GET",
        "/v1/factory/agent/charter",
        tool_name=SYS_FACTORY_SPAWN,
        params={"from_session_id": caller, "project": spawn.project},
    )
    if isinstance(resp, str):
        return spawn.message, {"charter_error": json.loads(resp)}
    if resp.status_code == 404:
        return spawn.message, {}
    body = _response_body(resp)
    if resp.status_code >= 400:
        error = _error_payload(resp.status_code, body, resp.text, SYS_FACTORY_SPAWN)
        return spawn.message, {"charter_error": error}
    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str) or not text.strip():
        return spawn.message, {}
    return append_charter(spawn.message, text.strip()), {"charter_attached": True}


async def _set_mutual_supervision(
    server_client: httpx.AsyncClient,
    *,
    caller: str,
    peer: str,
) -> _JsonObject:
    """Make two leads each other's supervisor; reports per-side outcomes."""
    outcome: _JsonObject = {}
    for target, supervisor in ((caller, peer), (peer, caller)):
        reply = json.loads(
            await _passthrough(
                server_client,
                "PATCH",
                _session_path("/v1/factory/sessions", target),
                tool_name=SYS_FACTORY_SPAWN,
                body={"from_session_id": caller, "supervisor_session_id": supervisor},
            )
        )
        failed = isinstance(reply, dict) and "error" in reply
        outcome[target] = reply if failed else "ok"
    return outcome


async def _cached_membership(
    server_client: httpx.AsyncClient,
    caller: str,
    target: str,
) -> tuple[int, _JsonObject | None, str]:
    """
    Look up *target*'s membership as seen by *caller*, cached for a turn.

    :returns: ``(status, body, text)``; status ``0`` on a transport error.
    """
    key = (caller, target)
    now = time.monotonic()
    hit = _membership_cache.get(key)
    if hit is not None and hit[0] > now:
        return hit[1], hit[2], ""
    resp = await _request(
        server_client,
        "GET",
        _membership_path(target),
        tool_name="factory membership",
        params={"from_session_id": caller},
    )
    if isinstance(resp, str):
        return 0, None, resp
    parsed = _response_body(resp)
    body = parsed if isinstance(parsed, dict) else None
    if resp.status_code in _CACHED_MEMBERSHIP_STATUSES:
        if len(_membership_cache) >= _MEMBERSHIP_CACHE_MAX:
            _membership_cache.clear()
        _membership_cache[key] = (now + _MEMBERSHIP_CACHE_TTL_S, resp.status_code, body)
    return resp.status_code, body, resp.text


async def scope_check(
    caller: str,
    target: str,
    server_client: httpx.AsyncClient,
    *,
    tool_name: str,
    id_key: str = "session_id",
) -> str | None:
    """
    Refuse a factory member's read of a session outside its factory.

    Fails closed: an unanswered membership lookup refuses the read.

    :param caller: The calling (factory-role) session id.
    :param target: The session the caller wants to read.
    :param server_client: HTTP client pointed at the Omnigent server.
    :param tool_name: The read tool, for error messages.
    :param id_key: Key naming the target in the error (matches the tool's
        own error shape, e.g. ``"conversation_id"`` for get_history).
    :returns: ``None`` when allowed, else a JSON error string.
    """
    if target == caller:
        return None
    if not _valid_session_id(target):
        return _error("invalid_session_id", **{id_key: target})
    status, own, _ = await _cached_membership(server_client, caller, caller)
    factory_id = own.get("factory_id") if status == 200 and own is not None else None
    if not isinstance(factory_id, str) or not factory_id:
        return _error(
            "factory_membership_unavailable",
            status=status,
            message=f"{tool_name} is limited to your factory, and your membership is unknown",
            **{id_key: target},
        )
    status, body, text = await _cached_membership(server_client, caller, target)
    if status == 200 and body is not None:
        if body.get("factory_id") == factory_id:
            return None
        return _error("session_out_of_factory", **{id_key: target})
    if status == 403:
        payload = _error_payload(status, body, text, tool_name)
        if payload["error"] not in _FACTORY_ERROR_CODES:
            payload["error"] = "session_out_of_factory"
        return json.dumps({**payload, id_key: target})
    if status == 404:
        return _error("session_not_found", **{id_key: target})
    return _error("factory_membership_unavailable", status=status, **{id_key: target})


def _roster_row(entry: _JsonObject, *, parent_session_id: str | None) -> _JsonObject:
    """Project a roster member/child to the ``sys_session_list`` row shape."""
    row: _JsonObject = {
        "session_id": entry.get("session_id"),
        "agent_name": entry.get("agent"),
        "title": entry.get("title"),
        "status": entry.get("status"),
        "runner_online": entry.get("runner_online"),
        "parent_session_id": parent_session_id,
    }
    for key in ("role", "project", "state", "health", "summary", "last_activity_at", "url"):
        value = entry.get(key)
        if value is not None:
            row[key] = value
    return row


async def _get_json_or_none(
    server_client: httpx.AsyncClient,
    path: str,
    params: dict[str, str | int],
) -> _JsonObject | None:
    """Best-effort GET of a JSON object; any failure is ``None``."""
    try:
        resp = await server_client.get(path, params=params, timeout=_TIMEOUT_S)
    except Exception:  # noqa: BLE001 — enrichment must never fail the list.
        return None
    parsed = _response_body(resp) if resp.status_code == 200 else None
    return parsed if isinstance(parsed, dict) else None


async def _closed_child_ids(server_client: httpx.AsyncClient, parent_id: str) -> set[str]:
    """Ids of *parent_id*'s closed sub-agents (best-effort, like ``_collect_sub_agents``)."""
    if not _valid_session_id(parent_id):
        return set()
    page = await _get_json_or_none(
        server_client,
        _session_path("/v1/sessions", parent_id, "/child_sessions"),
        {"limit": _CHILD_PAGE_LIMIT},
    )
    rows = page.get("data") if page is not None else None
    closed: set[str] = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(child_id := row.get("id"), str):
            continue
        labels = row.get("labels")
        title = row.get("title")
        if is_session_closed(
            labels if isinstance(labels, dict) else None,
            title if isinstance(title, str) else None,
        ):
            closed.add(child_id)
    return closed


async def _member_agent_name(server_client: httpx.AsyncClient, session_id: object) -> str | None:
    """A member's bound agent name from its session snapshot (best-effort)."""
    if not _valid_session_id(session_id):
        return None
    snap = await _get_json_or_none(
        server_client,
        _session_path("/v1/sessions", str(session_id)),
        {"include_items": "false", "include_liveness": "false"},
    )
    name = snap.get("agent_name") if snap is not None else None
    return name if isinstance(name, str) and name else None


async def _factory_session_list(
    caller: str,
    server_client: httpx.AsyncClient,
    agent_name: object,
) -> str:
    """``sys_session_list`` for a factory member: own sub-agents plus roster members."""
    from omnigent.runner.tool_dispatch import _collect_sub_agents

    sub_agents = await _collect_sub_agents(caller, server_client)
    result: _JsonObject = {"sub_agents": sub_agents, "sessions": [], "scope": "factory"}
    roster = await _get_object(
        server_client,
        "/v1/factory/roster",
        tool_name=_LIST_TOOL,
        params={"from_session_id": caller},
    )
    if isinstance(roster, str):
        result["roster_error"] = json.loads(roster)
        return json.dumps(result)
    members = _roster_members(roster)
    name_filter = agent_name if isinstance(agent_name, str) and agent_name else None
    # The roster has no member agent names or closed flags; look them up.
    parents = [
        session_id
        for m in members
        if isinstance(session_id := m.get("session_id"), str) and m.get("children")
    ]
    closed_sets = await asyncio.gather(*(_closed_child_ids(server_client, p) for p in parents))
    closed = set().union(*closed_sets)
    looked_up: dict[int, str | None] = {}
    if name_filter is not None:
        missing = [i for i, m in enumerate(members) if not isinstance(m.get("agent"), str)]
        names = await asyncio.gather(
            *(_member_agent_name(server_client, members[i].get("session_id")) for i in missing)
        )
        looked_up = dict(zip(missing, names, strict=True))
    rows: list[_JsonObject] = []
    for index, member in enumerate(members):
        row = _roster_row(member, parent_session_id=None)
        raw_agent = member.get("agent") if index not in looked_up else looked_up[index]
        if isinstance(raw_agent, str):
            row["agent_name"] = public_agent_name(raw_agent)
        spawned_by = member.get("parent_session_id")
        if spawned_by is not None:
            row["spawned_by"] = spawned_by
        if name_filter is None or name_filter in (raw_agent, row["agent_name"]):
            rows.append(row)
        children = member.get("children")
        for child in children if isinstance(children, list) else []:
            if not isinstance(child, dict) or child.get("session_id") in closed:
                continue
            if name_filter is not None and child.get("agent") != name_filter:
                continue
            rows.append(_roster_row(child, parent_session_id=member.get("session_id")))
    result["factory_id"] = roster.get("factory_id")
    result["sessions"] = rows
    return json.dumps(result)


async def _is_factory_caller(
    agent_spec: object,
    caller: str,
    server_client: httpx.AsyncClient,
) -> bool:
    """
    Whether *caller*'s reads are factory-scoped.

    The spec's ``factory_role`` decides; when the spec could not be resolved,
    the caller's own membership record does, so a member never falls back to
    unscoped reads just because its spec is unknown.
    """
    if factory_role_from_spec(agent_spec) is not None:
        return True
    if agent_spec is not None:
        return False
    status, own, _ = await _cached_membership(server_client, caller, caller)
    return status == 200 and own is not None and bool(own.get("factory_id"))


async def scoped_session_query(
    tool_name: str,
    args: _JsonObject,
    *,
    conversation_id: str | None,
    server_client: httpx.AsyncClient | None,
    agent_spec: object,
) -> str | None:
    """
    Factory-scope a session read for factory members.

    A caller is a member when its spec has a factory role or, with no
    resolvable spec, when the server's membership record says so.

    :returns: ``None`` to run the normal executor (non-factory caller, an
        allowed read, or a tool this does not scope); otherwise the tool result
        (a refusal, or the factory-scoped ``sys_session_list``).
    """
    if server_client is None or not conversation_id:
        return None
    if tool_name == _HISTORY_TOOL:
        target, id_key = args.get("conversation_id"), "conversation_id"
        if not isinstance(target, str) or not target:
            return None
    elif tool_name == _INFO_TOOL:
        target, id_key = args.get("session_id") or conversation_id, "session_id"
        if not isinstance(target, str):
            return None
    elif tool_name == _LIST_TOOL:
        target, id_key = None, ""
    else:
        return None
    # Reading yourself needs no scope check (and no membership lookup).
    if target == conversation_id:
        return None
    if not await _is_factory_caller(agent_spec, conversation_id, server_client):
        return None
    if target is None:
        return await _factory_session_list(conversation_id, server_client, args.get("agent_name"))
    return await scope_check(
        conversation_id, target, server_client, tool_name=tool_name, id_key=id_key
    )


# ── Cockpit reports: decisions, PRs, envs and demos ──────────────────────────


def _clip(value: str, limit: int) -> str:
    """Trim free text to *limit* characters, marking the cut."""
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _optional_bool(args: _JsonObject, key: str, default: bool) -> bool | str:
    """A boolean argument (``None`` means the default), else a JSON error."""
    value = args.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        return _error(f"'{key}' must be a boolean")
    return value


def _decision_options(raw: object) -> list[_JsonObject] | str:
    """Validate ``sys_factory_ask`` options; returns them or a JSON error."""
    tool = SYS_FACTORY_ASK
    if raw is None:
        return []
    if not isinstance(raw, list):
        return _error(f"{tool} 'options' must be a list of {{label, description?, recommended?}}")
    if len(raw) > DECISION_MAX_OPTIONS:
        return _error(f"{tool} takes at most {DECISION_MAX_OPTIONS} options")
    options: list[_JsonObject] = []
    for entry in raw:
        if not isinstance(entry, dict):
            return _error(f"{tool} every option must be an object with a 'label'")
        label = entry.get("label")
        if not isinstance(label, str) or not label.strip():
            return _error(f"{tool} every option needs a non-empty 'label'")
        if len(label.strip()) > DECISION_OPTION_LABEL_MAX:
            return _error(
                f"{tool} option labels are at most {DECISION_OPTION_LABEL_MAX} characters; "
                "put detail in 'description'",
                label=label,
            )
        option: _JsonObject = {"label": label.strip()}
        description = entry.get("description")
        if isinstance(description, str) and description.strip():
            option["description"] = _clip(description.strip(), DECISION_OPTION_DESCRIPTION_MAX)
        if entry.get("recommended") is True:
            option["recommended"] = True
        options.append(option)
    return options


async def _ask(args: _JsonObject, *, server_client: httpx.AsyncClient, caller: str) -> str:
    """``sys_factory_ask`` → ``POST /v1/factory/agent/decisions`` (or ``…/{ref}/withdraw``)."""
    tool = SYS_FACTORY_ASK
    question = args.get("question")
    withdraw = args.get("withdraw")
    if withdraw is not None:
        ref = _DECISION_REF_RE.fullmatch(withdraw.strip()) if isinstance(withdraw, str) else None
        if ref is None:
            return _error(f"{tool} 'withdraw' must be a decision you asked, e.g. 'D-7'")
        reason = question.strip() if isinstance(question, str) else ""
        return await _passthrough(
            server_client,
            "POST",
            f"/v1/factory/agent/decisions/D-{ref.group(1)}/withdraw",
            tool_name=tool,
            body={"from_session_id": caller, "reason": _clip(reason, _NOTE_MAX) or None},
        )
    if not isinstance(question, str) or not question.strip():
        return _error(f"{tool} requires a non-empty 'question'")
    question = " ".join(question.split())
    if len(question) > DECISION_QUESTION_MAX:
        return _error(
            f"{tool} 'question' is at most {DECISION_QUESTION_MAX} characters; "
            "put background in 'context'",
            length=len(question),
        )
    options = _decision_options(args.get("options"))
    if isinstance(options, str):
        return options
    body: _JsonObject = {"from_session_id": caller, "question": question, "options": options}
    context = args.get("context")
    if context is not None:
        if not isinstance(context, str):
            return _error(f"{tool} 'context' must be a string")
        if context.strip():
            body["context"] = _clip(context.strip(), DECISION_CONTEXT_MAX)
    for key in ("blocking", "allow_text"):
        value = _optional_bool(args, key, True)
        if isinstance(value, str):
            return value
        body[key] = value
    if not options and body["allow_text"] is False:
        return _error(f"{tool} needs options when allow_text is false")
    dedupe_key = args.get("dedupe_key")
    if dedupe_key is not None:
        if not isinstance(dedupe_key, str) or not dedupe_key.strip():
            return _error(f"{tool} 'dedupe_key' must be a non-empty string")
        body["dedupe_key"] = dedupe_key.strip()[:64]
    for key, limit in (("for", 128), ("subject", 64)):
        value = args.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            return _error(f"{tool} '{key}' must be a string")
        if value.strip():
            body[key] = value.strip()[:limit]
    return await _passthrough(
        server_client, "POST", "/v1/factory/agent/decisions", tool_name=tool, body=body
    )


def _sha_arg(tool: str, args: _JsonObject, key: str) -> str | None | _JsonObject:
    """An optional commit sha argument, lowercased; an error dict when malformed."""
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not _ABBREV_SHA_RE.fullmatch(value.strip()):
        return {"error": f"{tool} '{key}' must be a commit sha (7-40 hex characters)"}
    return value.strip().lower()


async def _register_pr(args: _JsonObject, *, server_client: httpx.AsyncClient, caller: str) -> str:
    """``sys_factory_pr`` → ``POST /v1/factory/agent/prs``."""
    tool = SYS_FACTORY_PR
    url = args.get("url")
    if parse_pr_url(url) is None:
        return _error(
            "not_a_pr_url",
            url=url if isinstance(url, str) else None,
            message="pass a GitHub PR URL like https://github.com/<owner>/<repo>/pull/<n>",
        )
    assert isinstance(url, str)
    body: _JsonObject = {"from_session_id": caller, "url": url.strip()}
    claim = args.get("claim")
    if claim is not None:
        if claim not in PR_CLAIMS:
            return _error(f"{tool} 'claim' must be one of {list(PR_CLAIMS)}")
        body["claim"] = claim
    verdict = args.get("sentinel_verdict")
    if verdict is not None:
        if verdict not in SENTINEL_VERDICTS:
            return _error(f"{tool} 'sentinel_verdict' must be one of {list(SENTINEL_VERDICTS)}")
        body["sentinel_verdict"] = verdict
    head_sha = _sha_arg(tool, args, "head_sha")
    if isinstance(head_sha, dict):
        return json.dumps(head_sha)
    if verdict is not None and not is_full_sha(head_sha or ""):
        return _error(f"{tool} a 'sentinel_verdict' needs the full 40-character 'head_sha'")
    if head_sha is not None:
        body["head_sha"] = head_sha
    for key, limit in (("task", 64), ("note", _NOTE_MAX)):
        value = args.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            return _error(f"{tool} '{key}' must be a string")
        if value.strip():
            body[key] = _clip(value.strip(), limit)
    return await _passthrough(
        server_client,
        "POST",
        "/v1/factory/agent/prs",
        tool_name=tool,
        body=body,
        timeout=_PR_TIMEOUT_S,
    )


def _env_body(args: _JsonObject, caller: str) -> _JsonObject | str:
    """Validate ``sys_factory_env`` arguments into the route body, or a JSON error."""
    tool = SYS_FACTORY_ENV
    state = args.get("state")
    pr = args.get("pr")
    if pr is not None and (isinstance(pr, bool) or not isinstance(pr, int) or pr < 1):
        return _error(f"{tool} 'pr' must be a PR number")
    allowed = PREVIEW_REPORT_STATES if pr is not None else ENV_STATES
    # ``starting`` reads as ``deploying`` for a preview.
    if pr is not None and state == "starting":
        state = "deploying"
    if state not in allowed:
        what = f"preview of PR #{pr}" if pr is not None else "dev env"
        return _error(
            "invalid_state",
            state=state,
            message=f"a {what} reports state as one of {list(allowed)}",
        )
    env_id = args.get("env_id")
    if not isinstance(env_id, str) or not env_id.strip():
        return _error(f"{tool} requires 'env_id'")
    body: _JsonObject = {"from_session_id": caller, "state": state, "env_id": env_id.strip()}
    if pr is not None:
        body["pr"] = pr
    urls = args.get("urls")
    if urls is not None:
        if not isinstance(urls, dict) or not all(
            isinstance(v, str) for v in urls.values() if v is not None
        ):
            return _error(f"{tool} 'urls' must map app/api/minio/tilt_ui to URLs")
        body["urls"] = {k: v.strip() for k, v in urls.items() if isinstance(v, str) and v.strip()}
    deployed = args.get("deployed")
    if deployed is not None:
        if not isinstance(deployed, dict):
            return _error(f"{tool} 'deployed' must be {{sha, ref}}")
        clean: _JsonObject = {}
        for key in ("sha", "tooling_overlay"):
            sha = _sha_arg(tool, deployed, key)
            if isinstance(sha, dict):
                return json.dumps(sha)
            if sha is not None:
                clean[key] = sha
        ref = deployed.get("ref")
        if isinstance(ref, str) and ref.strip():
            clean["ref"] = ref.strip()[:255]
        body["deployed"] = clean
    slot = args.get("slot")
    if slot is not None:
        if isinstance(slot, bool) or not isinstance(slot, int) or not 1 <= slot <= 3:
            return _error(f"{tool} 'slot' must be 1-3")
        body["slot"] = slot
    for key, limit in (("namespace", 63), ("note", _ENV_NOTE_MAX)):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            body[key] = _clip(value.strip(), limit)
    resources = args.get("resources")
    if resources is not None:
        if not isinstance(resources, list) or not all(
            isinstance(r, dict) and isinstance(r.get("name"), str) for r in resources
        ):
            return _error(f"{tool} 'resources' must be a list of {{name, status?, ready?, …}}")
        body["resources"] = resources[:100]
    return body


async def _report_env(args: _JsonObject, *, server_client: httpx.AsyncClient, caller: str) -> str:
    """``sys_factory_env`` → ``PUT /v1/factory/agent/envs``."""
    body = _env_body(args, caller)
    if isinstance(body, str):
        return body
    return await _passthrough(
        server_client, "PUT", "/v1/factory/agent/envs", tool_name=SYS_FACTORY_ENV, body=body
    )


# ── sys_factory_demo ─────────────────────────────────────────────────────────


class _DemoFileError(Exception):
    """A demo file the runner refuses to upload."""

    def __init__(self, code: str, path: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.path = path
        self.message = message

    def to_json(self) -> str:
        """:returns: The tool error."""
        return _error(self.code, path=self.path, message=self.message)


@dataclass(frozen=True)
class _DemoFile:
    """One file to upload: where it is and how the demo describes it."""

    path: Path
    name: str
    kind: str
    size: int
    caption: str | None = None
    step: int | None = None

    def meta(self) -> _JsonObject:
        """:returns: The file's entry in the upload's ``meta.files``."""
        entry: _JsonObject = {"name": self.name, "kind": self.kind}
        if self.caption:
            entry["caption"] = self.caption
        if self.step is not None:
            entry["step"] = self.step
        return entry


def demo_roots(workspace: Path | None) -> list[Path]:
    """
    Directories demo files may be read from: ``~/factory`` and the workspace.

    :param workspace: The session's workspace, if known.
    :returns: Resolved real paths (symlinks followed).
    """
    roots = [Path.home() / "factory"]
    if workspace is not None:
        roots.append(workspace)
    return [Path(os.path.realpath(root)) for root in roots]


def resolve_demo_file(raw: str, roots: list[Path], base: Path | None = None) -> tuple[Path, int]:
    """
    Resolve one demo file path and check it against the upload rules.

    ``~`` is expanded and symlinks are followed, so a link out of the allowed
    roots is refused like any other outside path.

    :param raw: The path as given, e.g. ``"~/factory/demos/9630/7767f0d/video/demo.webm"``.
    :param roots: Allowed roots from :func:`demo_roots`.
    :param base: Directory a relative path is resolved against.
    :returns: ``(real path, size in bytes)``.
    :raises _DemoFileError: ``file_not_allowed``, ``file_missing`` or ``demo_too_large``.
    """
    candidate = Path(os.path.expanduser(raw.strip()))
    if not candidate.is_absolute():
        candidate = (base or roots[-1]) / candidate
    real = Path(os.path.realpath(candidate))
    if not any(real == root or root in real.parents for root in roots):
        raise _DemoFileError(
            "file_not_allowed",
            raw,
            "demo files must be under ~/factory or your workspace (symlinks are followed)",
        )
    file_type = demo_file_type(real.name)
    if file_type is None:
        raise _DemoFileError(
            "file_not_allowed",
            raw,
            "allowed: .webm .mp4 .png .jpg .jpeg .gif .json .har .txt .md .log .ts .zip",
        )
    if not real.is_file():
        raise _DemoFileError("file_missing", raw, "no such file")
    size = real.stat().st_size
    cap = DEMO_CLASS_MAX_BYTES[file_type[1]]
    if size > cap:
        raise _DemoFileError(
            "demo_too_large",
            raw,
            f"{file_type[1]} files are at most {cap // (1024 * 1024)} MB; this is {size} bytes",
        )
    return real, size


def _display_name(real: Path, manifest_dir: Path | None) -> str:
    """The file's name in the demo: relative to the manifest directory when inside it."""
    if manifest_dir is not None and manifest_dir in real.parents:
        return real.relative_to(manifest_dir).as_posix()
    return real.name


def _read_manifest(raw: object, roots: list[Path]) -> tuple[Path, _JsonObject] | str:
    """Read the harness's ``demo.json``; returns ``(path, manifest)`` or a JSON error."""
    if not isinstance(raw, str) or not raw.strip():
        return _error(f"{SYS_FACTORY_DEMO} 'manifest_path' must be a path")
    try:
        path, size = resolve_demo_file(raw, roots)
    except _DemoFileError as exc:
        return exc.to_json()
    if size > DEMO_MANIFEST_MAX_BYTES:
        return _error("demo_too_large", path=raw, message="demo.json is at most 2 MB")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _error(f"{SYS_FACTORY_DEMO} could not read the manifest: {exc}", path=raw)
    if not isinstance(manifest, dict):
        return _error(f"{SYS_FACTORY_DEMO} the manifest must be a JSON object", path=raw)
    return path, manifest


def _manifest_entries(manifest: _JsonObject) -> list[tuple[str, str, str | None, int | None]]:
    """
    Files a manifest points at: ``(relative path, kind, caption, step)``.

    The video comes first, then the step screenshots, then the other artifacts,
    so the caps drop the least useful files.
    """
    artifacts = manifest.get("artifacts")
    named: list[tuple[str, str, str | None, int | None]] = []
    for key, rel in artifacts.items() if isinstance(artifacts, dict) else []:
        if isinstance(key, str) and isinstance(rel, str) and rel.strip():
            kind = DEMO_MANIFEST_KINDS.get(key, "other")
            named.append((rel.strip(), kind, key if kind == "other" else None, None))
    shots: list[tuple[str, str, str | None, int | None]] = []
    steps = manifest.get("steps")
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, dict):
            continue
        shot, number, title = step.get("screenshot"), step.get("n"), step.get("title")
        if isinstance(shot, str) and shot.strip():
            shots.append(
                (
                    shot.strip(),
                    "screenshot",
                    title if isinstance(title, str) else None,
                    number if isinstance(number, int) and not isinstance(number, bool) else None,
                )
            )
    videos = [entry for entry in named if entry[1] == "video"]
    rest = [entry for entry in named if entry[1] != "video"]
    return [*videos, *shots, *rest]


@dataclass
class _DemoPlan:
    """
    The files of one demo upload, checked against the caps.

    A path is uploaded once, except that the poster gets its own copy even
    when it is also a step screenshot (the harness's usual setup), so the demo
    always has a ``poster`` file.
    """

    files: list[_DemoFile] = field(default_factory=list)
    skipped: list[_JsonObject] = field(default_factory=list)
    seen: set[tuple[Path, bool]] = field(default_factory=set)
    total: int = 0

    def add(self, item: _DemoFile, *, explicit: bool) -> None:
        """Add a file; an explicit one over a cap raises, a manifest one is skipped."""
        key = (item.path, item.kind == "poster")
        if key in self.seen:
            return
        reason: str | None = None
        if len(self.files) >= DEMO_MAX_FILES:
            reason = f"a demo has at most {DEMO_MAX_FILES} files"
        elif self.total + item.size > DEMO_MAX_TOTAL_BYTES:
            reason = f"a demo is at most {DEMO_MAX_TOTAL_BYTES // (1024 * 1024)} MB in all"
        if reason is not None:
            if explicit:
                raise _DemoFileError("demo_too_large", str(item.path), reason)
            self.skipped.append({"path": item.name, "reason": reason})
            return
        self.seen.add(key)
        self.files.append(item)
        self.total += item.size


def _plan_demo_files(
    raw_files: object,
    manifest: tuple[Path, _JsonObject] | None,
    roots: list[Path],
) -> _DemoPlan | str:
    """Resolve the explicit files, then the manifest's, into one checked plan."""
    tool = SYS_FACTORY_DEMO
    if raw_files is None:
        raw_files = []
    if not isinstance(raw_files, list):
        return _error(f"{tool} 'files' must be a list of {{path, kind, caption?}}")
    manifest_dir = manifest[0].parent if manifest is not None else None
    plan = _DemoPlan()
    try:
        for entry in raw_files:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                return _error(f"{tool} every file needs a 'path'")
            kind = entry.get("kind")
            if kind not in DEMO_FILE_KINDS:
                return _error(f"{tool} file 'kind' must be one of {list(DEMO_FILE_KINDS)}")
            caption = entry.get("caption")
            step = entry.get("step")
            path, size = resolve_demo_file(entry["path"], roots, manifest_dir)
            plan.add(
                _DemoFile(
                    path=path,
                    name=_display_name(path, manifest_dir),
                    kind=kind,
                    size=size,
                    caption=_clip(caption.strip(), _CAPTION_MAX)
                    if isinstance(caption, str) and caption.strip()
                    else None,
                    step=step if isinstance(step, int) and not isinstance(step, bool) else None,
                ),
                explicit=True,
            )
    except _DemoFileError as exc:
        return exc.to_json()
    if manifest is not None and manifest_dir is not None:
        for rel, kind, caption, step in _manifest_entries(manifest[1]):
            try:
                path, size = resolve_demo_file(rel, roots, manifest_dir)
            except _DemoFileError as exc:
                plan.skipped.append({"path": rel, "reason": exc.message})
                continue
            plan.add(
                _DemoFile(
                    path=path,
                    name=_display_name(path, manifest_dir),
                    kind=kind,
                    size=size,
                    caption=_clip(caption, _CAPTION_MAX) if caption else None,
                    step=step,
                ),
                explicit=False,
            )
    if not plan.files:
        return _error(f"{tool} needs at least one file (or a manifest that lists some)")
    return plan


def _demo_meta(args: _JsonObject, caller: str, manifest: _JsonObject | None) -> _JsonObject | str:
    """Validate the demo's descriptive fields into the upload ``meta``."""
    tool = SYS_FACTORY_DEMO
    pr_url = args.get("pr_url")
    if parse_pr_url(pr_url) is None:
        return _error(
            "not_a_pr_url",
            url=pr_url if isinstance(pr_url, str) else None,
            message="pass a GitHub PR URL like https://github.com/<owner>/<repo>/pull/<n>",
        )
    assert isinstance(pr_url, str)
    head_sha = args.get("head_sha")
    head = head_sha.strip().lower() if isinstance(head_sha, str) else ""
    if not is_full_sha(head):
        return _error(f"{tool} 'head_sha' must be the full 40-character commit sha")
    title, summary = args.get("title"), args.get("summary")
    if not isinstance(title, str) or not title.strip():
        return _error(f"{tool} requires a 'title'")
    if not isinstance(summary, str) or not summary.strip():
        summary = manifest.get("summary") if manifest else None
    if not isinstance(summary, str) or not summary.strip():
        return _error(f"{tool} requires a 'summary' (or a manifest with one)")
    mismatch = demo_manifest_conflict(manifest, pr_url, head) if manifest else None
    if mismatch is not None:
        return _error(mismatch[0], message=mismatch[1])
    status = demo_status(args.get("status"), manifest)
    if isinstance(status, tuple):
        return _error(status[0], message=status[1])
    meta: _JsonObject = {
        "from_session_id": caller,
        "pr_url": pr_url.strip(),
        "head_sha": head,
        "title": _clip(title.strip(), 256),
        "summary": _clip(summary.strip(), 1000),
        "status": status,
    }
    # The harness's plan source is a fact about the run, so it wins over the argument.
    plan_source = (manifest.get("plan_source") if manifest else None) or args.get("plan_source")
    if isinstance(plan_source, str) and plan_source.strip():
        meta["plan_source"] = plan_source.strip()[:32]
    preview_url = args.get("preview_url")
    if not isinstance(preview_url, str) and manifest is not None:
        preview = manifest.get("preview")
        preview_url = preview.get("app") if isinstance(preview, dict) else None
    if isinstance(preview_url, str) and preview_url.strip():
        meta["preview_url"] = preview_url.strip()
    if manifest is not None:
        meta["manifest"] = manifest
    return meta


def _comment_path(manifest: tuple[Path, _JsonObject] | None, pr: int, head: str) -> Path:
    """Where the PR comment body is written: next to the manifest, else ``~/factory/demos``."""
    if manifest is not None:
        return manifest[0].parent / "pr-comment.md"
    return Path.home() / "factory" / "demos" / str(pr) / head[:7] / "pr-comment.md"


def _write_comment(path: Path, markdown: str) -> str | None:
    """Write the PR comment body; returns the path, or ``None`` when it cannot be written."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
    except OSError:
        _logger.warning("could not write the demo PR comment to %s", path, exc_info=True)
        return None
    return str(path)


def _read_files(files: list[_DemoFile]) -> list[tuple[str, tuple[str, bytes, str]]]:
    """Read every file into multipart ``files`` parts (one ``files`` field each)."""
    parts: list[tuple[str, tuple[str, bytes, str]]] = []
    for item in files:
        content_type = (demo_file_type(item.path.name) or ("application/octet-stream", ""))[0]
        parts.append(("files", (item.path.name, item.path.read_bytes(), content_type)))
    return parts


async def _publish_demo(
    args: _JsonObject,
    *,
    server_client: httpx.AsyncClient,
    caller: str,
    workspace: Path | None,
) -> str:
    """
    ``sys_factory_demo``: check and read the files, then upload them as multipart.

    ``POST /v1/factory/agent/demos`` gets a ``meta`` JSON part and one
    ``files`` part per file, in ``meta.files`` order. The server's
    ``comment_markdown`` is also written to ``pr-comment.md`` for
    ``gh pr comment --body-file``.
    """
    tool = SYS_FACTORY_DEMO
    roots = demo_roots(workspace)
    manifest: tuple[Path, _JsonObject] | None = None
    if args.get("manifest_path") is not None:
        read = _read_manifest(args.get("manifest_path"), roots)
        if isinstance(read, str):
            return read
        manifest = read
    meta = _demo_meta(args, caller, manifest[1] if manifest else None)
    if isinstance(meta, str):
        return meta
    plan = await asyncio.to_thread(_plan_demo_files, args.get("files"), manifest, roots)
    if isinstance(plan, str):
        return plan
    meta["files"] = [item.meta() for item in plan.files]
    try:
        parts = await asyncio.to_thread(_read_files, plan.files)
    except OSError as exc:
        return _error("file_missing", message=f"could not read a demo file: {exc}")
    try:
        resp = await server_client.post(
            "/v1/factory/agent/demos",
            params={"from_session_id": caller},
            data={"meta": json.dumps(meta)},
            files=parts,
            timeout=_DEMO_UPLOAD_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001 — any transport error becomes a tool error.
        return _error(f"{tool} upload failed: {exc}")
    body = _response_body(resp)
    if resp.status_code >= 400:
        return json.dumps(_error_payload(resp.status_code, body, resp.text, tool))
    if not isinstance(body, dict):
        return _error(f"{tool}: unexpected response from the server")
    result: _JsonObject = dict(body)
    if plan.skipped:
        result["skipped"] = plan.skipped
    markdown = body.get("comment_markdown")
    parsed = parse_pr_url(meta["pr_url"])
    if isinstance(markdown, str) and markdown and parsed is not None:
        path = _comment_path(manifest, parsed[1], meta["head_sha"])
        written = await asyncio.to_thread(_write_comment, path, markdown)
        if written is not None:
            result["comment_path"] = written
            result["next"] = f"gh pr comment {meta['pr_url']} --body-file {written}"
    return json.dumps(result)
