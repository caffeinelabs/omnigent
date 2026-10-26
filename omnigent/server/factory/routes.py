"""``/v1/factory`` routes: membership, roster, messages, beacon updates.

Callers are runner tools acting for a session (``from_session_id``) with the
runner owner's credentials. Every route requires EDIT on that session; when
the runner tunnel token header is present it must bind to the sender's runner
(or its root's, for co-located sub-agents) or be on the server's tunnel-token
allow-list, so one pod cannot speak for another. Factory scope is label-based:
sender and target must share ``omnigent.factory.id``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from omnigent.entities import Conversation
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.factory import (
    HEALTH,
    LABEL_HEALTH,
    LABEL_HEARTBEAT_S,
    LABEL_STATE,
    LABEL_SUMMARY,
    LABEL_SUPERVISOR,
    MESSAGE_KINDS,
    MESSAGE_MAX_CHARS,
    ROLE_LEAD,
    ROLE_PROJECT_LEAD,
    STATES,
    SUMMARY_MAX_LEN,
    clamp_label_value,
    valid_heartbeat_s,
)
from omnigent.runner.identity import RUNNER_TUNNEL_TOKEN_HEADER, token_bound_runner_id
from omnigent.server.auth import LEVEL_EDIT, LEVEL_OWNER, LEVEL_READ
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.envelope import display_title, format_envelope
from omnigent.server.factory.mailer import MailItem, get_mailer, new_mail_id
from omnigent.server.factory.membership import (
    Membership,
    UnknownTargetError,
    ensure_member,
    factory_members,
    is_session_id,
    is_top_level,
    membership_of,
    resolve_target,
    root_conversation,
)
from omnigent.server.factory.roster import build_roster
from omnigent.server.factory.server_decisions import open_storm_decision
from omnigent.server.routes._auth_helpers import (
    get_user_id,
    require_access,
    require_access_and_level,
)

_BEACON_FIELDS = frozenset({"summary", "health"})


class FactoryRouteError(Exception):
    """A factory-specific refusal with its own machine-readable code."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        """
        :param status_code: HTTP status, e.g. ``403``.
        :param code: Error code, e.g. ``"session_out_of_factory"``.
        :param message: Human-readable detail.
        """
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


async def factory_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Render :class:`FactoryRouteError` in the server's ``{"error": …}`` shape."""
    if not isinstance(exc, FactoryRouteError):
        raise exc
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


def _out_of_factory(detail: str = "target session is not in your factory") -> FactoryRouteError:
    return FactoryRouteError(403, "session_out_of_factory", detail)


class PostMessageBody(BaseModel):
    """``POST /v1/factory/messages`` body."""

    from_session_id: str
    to: str
    message: str
    kind: str = "info"
    restart: bool = False


class PatchSessionBody(BaseModel):
    """``PATCH /v1/factory/sessions/{id}`` body; unset fields are left alone."""

    from_session_id: str
    state: str | None = None
    summary: str | None = None
    health: str | None = None
    heartbeat_s: int | None = None
    supervisor_session_id: str | None = None
    title: str | None = None


async def _get_session(deps: FactoryDeps, session_id: str) -> Conversation:
    conv = await asyncio.to_thread(deps.conversation_store.get_conversation, session_id)
    if conv is None:
        raise OmnigentError(f"session {session_id!r} not found", code=ErrorCode.NOT_FOUND)
    return conv


async def _authorize_sender(
    request: Request,
    deps: FactoryDeps,
    from_session_id: str,
) -> tuple[str | None, Conversation]:
    """
    Authenticate the caller as *from_session_id* and enrol it lazily.

    :returns: ``(user_id, sender_row)``.
    :raises FactoryRouteError: 403 ``sender_not_bound`` on a token mismatch.
    """
    user_id = get_user_id(request, deps.auth_provider)
    access = await require_access_and_level(
        user_id,
        from_session_id,
        LEVEL_EDIT,
        deps.permission_store,
        deps.conversation_store,
    )
    sender = access.conversation or await _get_session(deps, from_session_id)
    token = request.headers.get(RUNNER_TUNNEL_TOKEN_HEADER)
    allow_list = deps.runner_tunnel_tokens
    # An allow-listed token proves a runner with a stable id, as on the tunnel route.
    if token is not None and not (allow_list is not None and token.strip() in allow_list):
        bound = token_bound_runner_id(token) if token.strip() else None
        allowed = {sender.runner_id}
        if not is_top_level(sender):
            root = await asyncio.to_thread(root_conversation, sender, deps.conversation_store)
            allowed.add(root.runner_id)
        allowed.discard(None)
        if bound is None or bound not in allowed:
            raise FactoryRouteError(
                403,
                "sender_not_bound",
                "the runner token does not belong to from_session_id",
            )
    sender = await asyncio.to_thread(ensure_member, sender, deps)
    return user_id, sender


async def _can_read(user_id: str | None, session_id: str, deps: FactoryDeps) -> bool:
    try:
        await require_access(
            user_id, session_id, LEVEL_READ, deps.permission_store, deps.conversation_store
        )
    except OmnigentError:
        return False
    return True


async def _same_factory_target(
    deps: FactoryDeps,
    sender: Membership,
    target: Conversation,
) -> Membership:
    target_m = await asyncio.to_thread(membership_of, target, deps)
    if sender.factory_id is None or target_m.factory_id != sender.factory_id:
        raise _out_of_factory()
    return target_m


def _storm_alert(sender: Membership, target: Membership, limit: str) -> str:
    return (
        f'[Factory alert · message storm · "{display_title(sender.title, sender.session_id)}" '
        f"({sender.session_id}) hit the limit of {limit}; latest target "
        f'"{display_title(target.title, target.session_id)}" ({target.session_id})]'
    )


def _restart_allowed(sender: Membership, target: Membership) -> bool:
    if not target.is_top_level or target.session_id == sender.session_id:
        return False
    return sender.role == ROLE_LEAD or target.supervisor_session_id == sender.session_id


def create_factory_router() -> APIRouter:
    """
    Build the ``/v1/factory`` router (hidden from the public schema).

    :returns: The router; its handlers read :class:`FactoryDeps` from
        ``request.app.state``.
    """
    router = APIRouter(prefix="/v1/factory", include_in_schema=False, tags=["factory"])

    @router.get("/membership/{session_id}")
    async def get_membership(
        request: Request,
        session_id: str,
        from_session_id: str = Query(...),
    ) -> dict[str, Any]:
        """Return *session_id*'s membership (403 when in another factory)."""
        deps = factory_deps(request.app.state)
        user_id, sender = await _authorize_sender(request, deps, from_session_id)
        sender_m = await asyncio.to_thread(membership_of, sender, deps)
        if session_id == sender.id:
            return sender_m.to_dict()
        await require_access(
            user_id, session_id, LEVEL_READ, deps.permission_store, deps.conversation_store
        )
        target = await _get_session(deps, session_id)
        return (await _same_factory_target(deps, sender_m, target)).to_dict()

    @router.get("/roster")
    async def get_roster(
        request: Request,
        from_session_id: str = Query(...),
    ) -> dict[str, Any]:
        """Return the sender's factory: top-level members and their sub-agents."""
        deps = factory_deps(request.app.state)
        user_id, sender = await _authorize_sender(request, deps, from_session_id)
        sender_m = await asyncio.to_thread(membership_of, sender, deps)
        if sender_m.factory_id is None:
            return {"factory_id": None, "members": []}
        members = await asyncio.to_thread(
            factory_members, sender_m.factory_id, deps.conversation_store
        )
        # Labels are not an ACL: list only sessions this user can read.
        visible = [m for m in members if await _can_read(user_id, m.id, deps)]
        return await build_roster(sender_m.factory_id, visible, deps, request.app.state)

    @router.post("/messages", status_code=202)
    async def post_message(request: Request, body: PostMessageBody) -> dict[str, Any]:
        """Queue a message to a factory peer; delivery happens in the background."""
        deps = factory_deps(request.app.state)
        user_id, sender = await _authorize_sender(request, deps, body.from_session_id)
        if body.kind not in MESSAGE_KINDS:
            raise FactoryRouteError(
                400, "invalid_kind", f"kind must be one of {sorted(MESSAGE_KINDS)}"
            )
        if not body.message.strip():
            raise FactoryRouteError(400, "empty_message", "message must not be empty")
        if len(body.message) > MESSAGE_MAX_CHARS:
            raise FactoryRouteError(
                413,
                "message_too_large",
                f"message exceeds {MESSAGE_MAX_CHARS} chars; put bulk in a file and send its path",
            )
        sender_m = await asyncio.to_thread(membership_of, sender, deps)
        if sender_m.factory_id is None:
            raise _out_of_factory("from_session_id is not a factory member")
        try:
            target = await asyncio.to_thread(
                resolve_target, body.to, sender_m, deps.conversation_store
            )
        except UnknownTargetError as exc:
            raise FactoryRouteError(400, "unknown_target", str(exc)) from exc
        # Delivery types into the target, so the caller must be allowed to.
        # Checked before the factory match so the 403 below does not reveal
        # labels of a session the caller cannot access.
        await require_access(
            user_id, target.id, LEVEL_EDIT, deps.permission_store, deps.conversation_store
        )
        target_m = await _same_factory_target(deps, sender_m, target)
        if body.restart:
            # A restart is the UI's Stop, which only the owner may run.
            await require_access(
                user_id, target.id, LEVEL_OWNER, deps.permission_store, deps.conversation_store
            )
            if not _restart_allowed(sender_m, target_m):
                raise FactoryRouteError(
                    403,
                    "restart_not_allowed",
                    "restart is for top-level peers, from a lead or the target's supervisor",
                )
        mailer = get_mailer(request.app.state)
        limit = mailer.try_acquire(sender.id, target.id, factory_id=sender_m.factory_id)
        if limit is not None:
            if mailer.claim_storm_alert(sender_m.factory_id):
                mailer.notify(sender_m.factory_id, _storm_alert(sender_m, target_m, limit))
                await open_storm_decision(request.app.state, sender_m, limit)
            raise FactoryRouteError(429, "rate_limited", f"rate limit reached: {limit}")
        mailer.record_post(sender.id, target.id, body.kind)
        mail_id = new_mail_id()
        mailer.submit(
            MailItem(
                mail_id=mail_id,
                sender_id=sender.id,
                target_id=target.id,
                target_title=target.title,
                text=format_envelope(
                    sender_id=sender.id,
                    sender_title=sender.title,
                    sender_role=sender_m.role,
                    sender_project=sender_m.project,
                    kind=body.kind,
                    body=body.message,
                    mail_id=mail_id,
                ),
                kind=body.kind,
                restart=body.restart,
                factory_id=sender_m.factory_id,
                project=target_m.project or sender_m.project,
            )
        )
        return {"mail_id": mail_id, "to_session_id": target.id, "status": "queued"}

    @router.patch("/sessions/{session_id}")
    async def patch_session(
        request: Request,
        session_id: str,
        body: PatchSessionBody,
    ) -> dict[str, Any]:
        """Update a member's factory labels (state, beacon, heartbeat, supervisor, title)."""
        deps = factory_deps(request.app.state)
        store = deps.conversation_store
        user_id, sender = await _authorize_sender(request, deps, body.from_session_id)
        sender_m = await asyncio.to_thread(membership_of, sender, deps)
        if sender_m.factory_id is None:
            raise _out_of_factory("from_session_id is not a factory member")
        if session_id == sender.id:
            target, target_m = sender, sender_m
        else:
            await require_access(user_id, session_id, LEVEL_EDIT, deps.permission_store, store)
            target = await _get_session(deps, session_id)
            target_m = await _same_factory_target(deps, sender_m, target)
        fields = body.model_fields_set - {"from_session_id"}
        beacon_only = fields <= _BEACON_FIELDS
        allowed = (
            target.id == sender.id
            or sender_m.role == ROLE_LEAD
            or (
                sender_m.role == ROLE_PROJECT_LEAD
                and not target_m.is_top_level
                and target.root_conversation_id == sender.id
                and beacon_only
            )
        )
        if not allowed:
            raise FactoryRouteError(403, "not_allowed", "you may not update this session")
        if not target_m.is_top_level and not beacon_only:
            raise FactoryRouteError(
                400, "top_level_only", "sub-agents carry only summary and health"
            )

        labels: dict[str, str] = {}
        clear_supervisor = False
        if body.state is not None:
            if body.state not in STATES:
                raise FactoryRouteError(
                    400, "invalid_state", f"state must be one of {sorted(STATES)}"
                )
            labels[LABEL_STATE] = body.state
        if body.health is not None:
            if body.health not in HEALTH:
                raise FactoryRouteError(
                    400, "invalid_health", f"health must be one of {sorted(HEALTH)}"
                )
            labels[LABEL_HEALTH] = body.health
        if body.summary is not None:
            labels[LABEL_SUMMARY] = clamp_label_value(
                " ".join(body.summary.split()), SUMMARY_MAX_LEN
            )
        if body.heartbeat_s is not None:
            if not valid_heartbeat_s(body.heartbeat_s):
                raise FactoryRouteError(
                    400, "invalid_heartbeat_s", "heartbeat_s must be 0 or between 120 and 86400"
                )
            labels[LABEL_HEARTBEAT_S] = str(body.heartbeat_s)
        if body.supervisor_session_id is not None:
            supervisor_id = body.supervisor_session_id.strip()
            if not supervisor_id:
                clear_supervisor = True
            else:
                supervisor = (
                    await asyncio.to_thread(store.get_conversation, supervisor_id)
                    if is_session_id(supervisor_id)
                    else None
                )
                if supervisor is None or not is_top_level(supervisor):
                    raise FactoryRouteError(
                        400, "unknown_target", "supervisor must be a top-level session"
                    )
                await _same_factory_target(deps, sender_m, supervisor)
                labels[LABEL_SUPERVISOR] = supervisor_id

        if body.title is not None:
            from omnigent.server.routes._sessions.helpers import _persist_external_session_title
            from omnigent.server.schemas import SessionEventInput

            # Same validation, write and session.title broadcast as a /rename.
            await _persist_external_session_title(
                target.id,
                target,
                SessionEventInput(type="external_session_title", data={"title": body.title}),
                store,
            )
        if labels:
            await asyncio.to_thread(store.set_labels, target.id, labels)
        if clear_supervisor:
            await asyncio.to_thread(store.delete_label, target.id, LABEL_SUPERVISOR)
        refreshed = await _get_session(deps, target.id)
        return (await asyncio.to_thread(membership_of, refreshed, deps)).to_dict()

    return router
