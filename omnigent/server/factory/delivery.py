"""Server-side delivery of a factory message into a session.

:func:`deliver_message` injects text as a user message the same way
``POST /v1/sessions/{id}/events`` does for a typed message: wait out an
in-flight managed launch, wake or relaunch the session's runner, heal a stale
sub-agent binding, run the session-init handshake after a relaunch, arm the
relay, then dispatch. It composes the module-level helpers that route uses
rather than re-entering the route, so it works with no HTTP request and no
live sender (the sender's pod may already be gone).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from omnigent.entities import Conversation
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.factory.context import factory_deps
from omnigent.server.routes._sessions.helpers import _stop_session_host_runner
from omnigent.server.routes._sessions.orchestration import (
    _await_settled_managed_launch,
    _dispatch_session_event_to_runner,
    _ensure_runner_relay_ready,
    _ensure_runner_session_initialized,
    _get_runner_client,
    _heal_subagent_runner_binding_via_parent,
    _intentional_stop_sessions,
    _interrupt_fenced_sessions,
    _is_native_terminal_session,
    _stop_session_via_runner,
    ensure_runner_connected,
)
from omnigent.server.schemas import SessionEventInput
from omnigent.stores import ConversationStore
from omnigent.util.session_lifecycle import is_session_closed

_logger = logging.getLogger(__name__)

# How long a restart waits for the stopped runner's tunnel to drop before
# delivering, so the message is not forwarded into the exiting runner.
_RESTART_TUNNEL_DROP_TIMEOUT_S = 15.0
_RESTART_TUNNEL_DROP_POLL_S = 0.25

REASON_RUNNER_UNAVAILABLE = "runner_unavailable"
REASON_TRANSPORT_ERROR = "transport_error"
REASON_SESSION_NOT_FOUND = "session_not_found"
REASON_SESSION_CLOSED = "session_closed"

_RETRYABLE_CODES = frozenset({ErrorCode.RUNNER_UNAVAILABLE, ErrorCode.WRONG_REPLICA})


@dataclass(frozen=True)
class DeliveryOutcome:
    """
    Result of one delivery attempt.

    :param delivered: ``True`` once the runner accepted the message.
    :param reason: Why it was not delivered, e.g. ``"runner_unavailable"``.
    :param retryable: Whether a later attempt may succeed (runner down,
        503, transport error) as opposed to a permanent refusal.
    """

    delivered: bool
    reason: str | None = None
    retryable: bool = False


def _message_event(text: str) -> SessionEventInput:
    return SessionEventInput(
        type="message",
        data={"role": "user", "content": [{"type": "input_text", "text": text}]},
    )


async def _reread(
    store: ConversationStore, session_id: str, fallback: Conversation
) -> Conversation:
    refreshed = await asyncio.to_thread(store.get_conversation, session_id)
    return refreshed if refreshed is not None else fallback


async def _wait_for_tunnel_drop(app_state: Any, runner_id: str) -> None:
    registry = getattr(app_state, "tunnel_registry", None)
    if registry is None:
        return
    deadline = time.monotonic() + _RESTART_TUNNEL_DROP_TIMEOUT_S
    while registry.get(runner_id) is not None and time.monotonic() < deadline:
        await asyncio.sleep(_RESTART_TUNNEL_DROP_POLL_S)


async def _stop_for_restart(
    app_state: Any,
    conv: Conversation,
    store: ConversationStore,
    runner_router: Any,
) -> None:
    """
    Run the web UI's Stop on *conv*: kill the pane, then its host runner.

    Mirrors the ``stop_session`` event branch (turn fence, intentional-stop
    marker, best-effort host teardown). Failures are logged, not raised: the
    relaunch ladder that follows recovers a runner that is already gone.
    """
    session_id = conv.id
    _interrupt_fenced_sessions.add(session_id)
    stop_delivered = False
    try:
        stop_delivered = await _stop_session_via_runner(session_id, runner_router)
    except Exception:  # noqa: BLE001 - a wedged pane must not block the restart
        _logger.warning("factory restart: stop of %s failed", session_id, exc_info=True)
    if not stop_delivered:
        _interrupt_fenced_sessions.discard(session_id)
    stop_conv = await _reread(store, session_id, conv)
    if not (stop_conv.host_id and stop_conv.runner_id):
        return
    _intentional_stop_sessions.add(session_id)
    teardown_delivered = False
    try:
        teardown_delivered = await _stop_session_host_runner(
            session_id,
            stop_conv.host_id,
            stop_conv.runner_id,
            getattr(app_state, "host_registry", None),
        )
    except Exception:  # noqa: BLE001 - best-effort, like the Stop route
        _logger.warning(
            "factory restart: host runner stop of %s failed", session_id, exc_info=True
        )
    if not teardown_delivered:
        _intentional_stop_sessions.discard(session_id)
        return
    await _wait_for_tunnel_drop(app_state, stop_conv.runner_id)


async def _has_mcp_servers(conv: Conversation, deps: Any) -> tuple[str | None, bool]:
    """:returns: ``(agent_name, has_mcp_servers)`` exactly as post_event computes them."""
    agent = deps.agent_store.get(conv.agent_id) if conv.agent_id else None
    if agent is None:
        return None, False
    has_mcp = False
    if deps.agent_cache is not None and agent.bundle_location:
        try:
            loaded = await asyncio.to_thread(
                deps.agent_cache.load, agent.id, agent.bundle_location
            )
            has_mcp = bool(loaded.spec.mcp_servers)
        except Exception:  # noqa: BLE001 - only the MCP hint is lost
            _logger.warning("factory: MCP hint lookup failed for %s", conv.id, exc_info=True)
    return agent.name, has_mcp


async def _deliver(
    app_state: Any,
    session_id: str,
    text: str,
    *,
    restart: bool,
) -> DeliveryOutcome:
    deps = factory_deps(app_state)
    store = deps.conversation_store
    runner_router = getattr(app_state, "runner_router", None)

    conv = await asyncio.to_thread(store.get_conversation, session_id)
    if conv is None:
        return DeliveryOutcome(False, REASON_SESSION_NOT_FOUND)
    if is_session_closed(conv.labels, conv.title):
        return DeliveryOutcome(False, REASON_SESSION_CLOSED)

    if restart:
        await _stop_for_restart(app_state, conv, store, runner_router)
        conv = await _reread(store, session_id, conv)

    runner_client = await _get_runner_client(session_id, runner_router, conversation=conv)
    # Managed-launch rendezvous: a just-spawned managed session has no host
    # binding until its background provision settles.
    if runner_client is None and conv.host_id is None:
        tracker = getattr(app_state, "managed_launches", None)
        launch = tracker.get(session_id) if tracker is not None else None
        if launch is not None:
            await _await_settled_managed_launch(launch)
            conv = await _reread(store, session_id, conv)
            runner_client = await _get_runner_client(session_id, runner_router, conversation=conv)

    needs_init = False
    if runner_client is None:
        runner_client, conv = await ensure_runner_connected(
            session_id=session_id,
            conv=conv,
            app_state=app_state,
            conversation_store=store,
            runner_router=runner_router,
        )
        needs_init = runner_client is not None

    if runner_client is None and conv.kind == "sub_agent":
        # A co-located sub-agent rides its root's runner: wake the root, then
        # repoint the child's stale runner binding at it.
        root = await asyncio.to_thread(store.get_conversation, conv.root_conversation_id)
        if root is not None and root.id != conv.id:
            await ensure_runner_connected(
                session_id=root.id,
                conv=root,
                app_state=app_state,
                conversation_store=store,
                runner_router=runner_router,
            )
        runner_client = await _heal_subagent_runner_binding_via_parent(
            conv,
            runner_router,
            getattr(app_state, "tunnel_registry", None),
            store,
        )
        if runner_client is not None:
            conv = await _reread(store, session_id, conv)
            needs_init = _is_native_terminal_session(conv)

    if runner_client is None:
        return DeliveryOutcome(False, REASON_RUNNER_UNAVAILABLE, retryable=True)

    conv = await _reread(store, session_id, conv)
    native_terminal_ready = False
    if needs_init:
        # suppress_recovery_turn: the dispatch below is the turn's only trigger.
        native_terminal_ready = await _ensure_runner_session_initialized(
            session_id,
            conv,
            runner_client,
            store,
            initializer=getattr(app_state, "runner_session_initializer", None),
            suppress_recovery_turn=True,
        )
    await _ensure_runner_relay_ready(session_id, conv.runner_id, runner_client, store)
    agent_name, has_mcp_servers = await _has_mcp_servers(conv, deps)
    await _dispatch_session_event_to_runner(
        session_id,
        conv,
        _message_event(text),
        store,
        runner_client,
        agent_name=agent_name,
        file_store=deps.file_store,
        artifact_store=deps.artifact_store,
        has_mcp_servers=has_mcp_servers,
        runner_router=runner_router,
        native_terminal_ready=native_terminal_ready,
        host_store=getattr(app_state, "host_store", None),
        # The envelope is not a human prompt; never derive a title from it.
        background_titles_enabled=False,
    )
    return DeliveryOutcome(True)


async def deliver_message(
    app_state: Any,
    session_id: str,
    text: str,
    *,
    restart: bool = False,
) -> DeliveryOutcome:
    """
    Deliver *text* to *session_id* as a user message, waking it if needed.

    :param app_state: ``app.state`` carrying the runner registries, managed
        launch tracker and the factory deps.
    :param session_id: Target session id, e.g. ``"conv_pl1"``.
    :param text: The full message (already enveloped by the caller).
    :param restart: Stop the session (pane and host runner) first, so the
        ladder relaunches it fresh; used for wedged TUIs.
    :returns: The :class:`DeliveryOutcome`; never raises for delivery errors.
    """
    try:
        return await _deliver(app_state, session_id, text, restart=restart)
    except (httpx.HTTPError, ConnectionError) as exc:
        _logger.info("factory delivery to %s failed: %s", session_id, exc)
        return DeliveryOutcome(False, REASON_TRANSPORT_ERROR, retryable=True)
    except OmnigentError as exc:
        _logger.info("factory delivery to %s failed: %s (%s)", session_id, exc.message, exc.code)
        retryable = exc.code in _RETRYABLE_CODES or exc.http_status >= 500
        return DeliveryOutcome(False, str(exc.code), retryable=retryable)
