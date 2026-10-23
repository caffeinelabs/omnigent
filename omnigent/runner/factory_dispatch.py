"""Runner-side executors for the software-factory tools.

The runner has no store access, so each factory tool proxies the server's
``/v1/factory`` routes over ``server_client``, which already carries the
runner's owner bearer and its tunnel-binding token (the server uses that token
to prove the sender is who it claims). ``sys_factory_spawn`` composes those
routes with the ordinary ``GET /v1/agents`` and the JSON ``POST /v1/sessions``
managed create.

Factory roles also get factory-scoped session reads: ``sys_session_get_history``
and ``sys_session_get_info`` refuse targets outside the caller's factory, and
``sys_session_list`` lists roster members instead of every session the human
owns. That bounds what a prompt-injected member can read.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any, Final

import httpx

from omnigent.factory import (
    DEFAULT_HEARTBEAT_S,
    HEALTH,
    LABEL_HEARTBEAT_S,
    LABEL_ID,
    LABEL_PARENT,
    LABEL_PROJECT,
    LABEL_ROLE,
    LABEL_STATE,
    LABEL_SUPERVISOR,
    MESSAGE_KINDS,
    MESSAGE_MAX_CHARS,
    ROLE_LEAD,
    ROLE_PROJECT_LEAD,
    STATE_ACTIVE,
    STATE_DONE,
    STATES,
    SUMMARY_MAX_LEN,
    clamp_label_value,
    factory_role_from_spec,
    valid_heartbeat_s,
)
from omnigent.tools.builtins.factory import (
    SPAWN_ROLES,
    SYS_FACTORY_ROSTER,
    SYS_FACTORY_SET,
    SYS_FACTORY_SPAWN,
    SYS_SESSION_POST,
)

_logger = logging.getLogger(__name__)

_JsonObject = dict[str, Any]
_PublishEvent = Callable[[str, _JsonObject], None]

_TIMEOUT_S: Final = 30.0
DEFAULT_MAX_PODS: Final = 12
_AGENT_PAGE_LIMIT: Final = 1000
_AGENT_MAX_PAGES: Final = 10
_AGENT_NAMES_IN_ERROR: Final = 50

# Membership answers are stable for a session's life, so one lookup serves a
# whole turn of scoped reads; only definitive answers (200/403) are cached.
_MEMBERSHIP_CACHE_TTL_S: Final = 60.0
_MEMBERSHIP_CACHE_MAX: Final = 1024
_membership_cache: dict[tuple[str, str], tuple[float, int, _JsonObject | None]] = {}

# Codes the factory routes return; recognised in any server error envelope.
_FACTORY_ERROR_CODES: Final = frozenset(
    {
        "session_out_of_factory",
        "restart_not_allowed",
        "unknown_target",
        "sender_not_bound",
        "rate_limited",
        "message_too_large",
    }
)
_STATUS_ERROR_CODES: Final = {413: "message_too_large", 429: "rate_limited"}
_CODE_TOKEN_RE: Final = re.compile(r"[a-z][a-z0-9_]*")

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
        extra = {
            k: v
            for k, v in body.items()
            if k not in {"error", "detail", "message"} and isinstance(v, (str, int, float, bool))
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


async def _request(
    server_client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    tool_name: str,
    params: dict[str, str | int] | None = None,
    body: _JsonObject | None = None,
) -> httpx.Response | str:
    """Send one server request; a transport failure becomes a JSON error string."""
    try:
        return await server_client.request(
            method, path, params=params, json=body, timeout=_TIMEOUT_S
        )
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
) -> str:
    """Proxy a factory route, returning its JSON on success or ``{error}``."""
    resp = await _request(
        server_client, method, path, tool_name=tool_name, params=params, body=body
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
    return f"/v1/factory/membership/{session_id}"


async def execute_factory_tool(
    tool_name: str,
    args: _JsonObject,
    *,
    server_client: httpx.AsyncClient | None,
    conversation_id: str | None,
    agent_spec: object = None,
    publish_event: _PublishEvent | None = None,
) -> str:
    """
    Dispatch one factory tool call.

    :param tool_name: One of :data:`omnigent.tools.builtins.factory.FACTORY_TOOL_NAMES`.
    :param args: Parsed tool arguments.
    :param server_client: HTTP client pointed at the Omnigent server.
    :param conversation_id: The calling session id, stamped as the sender.
    :param agent_spec: The caller's spec; spawn reads ``factory_max_pods``.
    :param publish_event: SSE publish callback for spawn's ``session.created``.
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
    if not isinstance(target, str):
        return _error(f"{tool} 'session_id' must be a string")
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
    for key in ("supervisor_session_id", "title"):
        value = args.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            return _error(f"{tool} '{key}' must be a non-empty string")
        patch[key] = value.strip()
    if not patch:
        return _error(f"{tool} requires at least one field to change")
    return await _passthrough(
        server_client,
        "PATCH",
        f"/v1/factory/sessions/{target}",
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

    :returns: ``(agent_id, None)`` on a match, else ``(None, error_json)``.
    """
    tool = SYS_FACTORY_SPAWN
    after: str | None = None
    names: list[str] = []
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
                return row_id, None
            if isinstance(row_name, str):
                names.append(row_name)
        last_id = page.get("last_id")
        if page.get("has_more") is not True or not isinstance(last_id, str) or not last_id:
            break
        after = last_id
    return None, _error(
        "agent_not_found",
        agent=name,
        available=sorted(set(names))[:_AGENT_NAMES_IN_ERROR],
    )


def _default_supervisor(
    role: str,
    caller: str,
    members: list[_JsonObject],
) -> str:
    """The peer lead supervises a project lead; a new lead is supervised by its spawner."""
    if role == ROLE_PROJECT_LEAD:
        for member in members:
            session_id = member.get("session_id")
            if member.get("role") == ROLE_LEAD and isinstance(session_id, str):
                if session_id and session_id != caller:
                    return session_id
    return caller


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

    Steps: caller must be a lead; enforce the pod cap from the roster;
    resolve the built-in agent; ``POST /v1/sessions`` (``host_type: managed``,
    factory labels, no ``parent_session_id`` and no ``initial_items`` — managed
    initial items are stored as history only); post the brief; set mutual
    supervision for a second lead; announce ``session.created``.
    """
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

    agent_id, agent_error = await _resolve_builtin_agent(server_client, agent.strip())
    if agent_id is None:
        return agent_error or _error("agent_not_found", agent=agent)

    supervisor_id = (
        supervisor.strip() if supervisor else _default_supervisor(role, caller, members)
    )
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
    if project:
        labels[LABEL_PROJECT] = clamp_label_value(project.strip())
    create_body: _JsonObject = {
        "agent_id": agent_id,
        "host_type": "managed",
        "title": title.strip(),
        "labels": labels,
    }
    if workspaces:
        create_body["workspaces"] = [w.strip() for w in workspaces]
    resp = await _request(server_client, "POST", "/v1/sessions", tool_name=tool, body=create_body)
    if isinstance(resp, str):
        return resp
    created = _response_body(resp)
    if resp.status_code == 404:
        return _error("agent_not_found", agent=agent, agent_id=agent_id)
    if resp.status_code in (401, 403):
        return _error("access_denied", agent=agent, status=resp.status_code)
    if resp.status_code >= 400:
        return json.dumps(_error_payload(resp.status_code, created, resp.text, tool))
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
    if project:
        result["project"] = labels[LABEL_PROJECT]

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
                f"/v1/factory/sessions/{target}",
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
    if resp.status_code in (200, 403):
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
    rows: list[_JsonObject] = []
    for member in _roster_members(roster):
        row = _roster_row(member, parent_session_id=None)
        spawned_by = member.get("parent_session_id")
        if spawned_by is not None:
            row["spawned_by"] = spawned_by
        rows.append(row)
        children = member.get("children")
        for child in children if isinstance(children, list) else []:
            if isinstance(child, dict):
                rows.append(_roster_row(child, parent_session_id=member.get("session_id")))
    if isinstance(agent_name, str) and agent_name:
        rows = [r for r in rows if r.get("agent_name") == agent_name]
    result["factory_id"] = roster.get("factory_id")
    result["sessions"] = rows
    return json.dumps(result)


async def scoped_session_query(
    tool_name: str,
    args: _JsonObject,
    *,
    conversation_id: str | None,
    server_client: httpx.AsyncClient | None,
    agent_spec: object,
) -> str | None:
    """
    Factory-scope a session read for callers whose spec has a factory role.

    :returns: ``None`` to run the normal executor (non-factory caller, an
        allowed read, or a tool this does not scope); otherwise the tool result
        (a refusal, or the factory-scoped ``sys_session_list``).
    """
    if factory_role_from_spec(agent_spec) is None:
        return None
    if server_client is None or not conversation_id:
        return None
    if tool_name == _HISTORY_TOOL:
        target = args.get("conversation_id")
        if not isinstance(target, str) or not target:
            return None
        return await scope_check(
            conversation_id,
            target,
            server_client,
            tool_name=tool_name,
            id_key="conversation_id",
        )
    if tool_name == _INFO_TOOL:
        target = args.get("session_id") or conversation_id
        if not isinstance(target, str):
            return None
        return await scope_check(conversation_id, target, server_client, tool_name=tool_name)
    if tool_name == _LIST_TOOL:
        return await _factory_session_list(conversation_id, server_client, args.get("agent_name"))
    return None
