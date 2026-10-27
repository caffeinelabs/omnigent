"""The server's sharing-mode gates for new permission grants.

``PUT /v1/sessions/{id}/permissions`` and the factory People API both grant
access, so both ask :func:`require_sharing_allows` before writing a grant:
``off`` refuses every new grant, ``read_only`` caps grants at read, and
``restricted_read_only`` also refuses sessions whose working directory is a
home directory or the filesystem root.
"""

from __future__ import annotations

import asyncio
from typing import Any

from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import LEVEL_READ, SharingMode, workspace_sharing_blocked
from omnigent.stores import ConversationStore

SHARING_OFF_MESSAGE = "Sharing has been disabled for this Omnigent server."
WORKSPACE_BLOCKED_MESSAGE = (
    "This session's working directory (a home or root directory) "
    "cannot be shared on this Omnigent server."
)
READ_ONLY_MESSAGE = "Sharing is limited to read-only access on this Omnigent server."


def sharing_mode(app_state: Any) -> SharingMode:
    """
    The server's current sharing mode.

    :param app_state: ``app.state``; a hand-built app without ``sharing_mode``
        (a focused test) reads as ``on``.
    :returns: The mode.
    """
    return getattr(app_state, "sharing_mode", lambda: SharingMode.ON)()


def sharing_refusal(mode: SharingMode, level: int, workspace: str | None) -> str | None:
    """
    Why a new grant at *level* is refused under *mode*, if it is.

    :param mode: The server's sharing mode.
    :param level: The requested grant level (1 read … 3 manage).
    :param workspace: The session's working directory, or ``None`` when unknown.
    :returns: The refusal message, or ``None`` when the grant is allowed.
    """
    if mode == SharingMode.OFF:
        return SHARING_OFF_MESSAGE
    if mode == SharingMode.RESTRICTED_READ_ONLY and workspace_sharing_blocked(workspace):
        return WORKSPACE_BLOCKED_MESSAGE
    if mode in (SharingMode.READ_ONLY, SharingMode.RESTRICTED_READ_ONLY) and level > LEVEL_READ:
        return READ_ONLY_MESSAGE
    return None


async def require_sharing_allows(
    app_state: Any,
    conversation_store: ConversationStore,
    session_id: str,
    level: int,
) -> None:
    """
    Refuse a new grant on *session_id* the sharing mode does not allow.

    :param app_state: ``app.state`` carrying ``sharing_mode``.
    :param conversation_store: Session rows (the workspace check reads one).
    :param session_id: The session the grant is for.
    :param level: The requested grant level.
    :raises OmnigentError: 403 with the refusal message.
    """
    mode = sharing_mode(app_state)
    workspace = None
    if mode == SharingMode.RESTRICTED_READ_ONLY:
        conv = await asyncio.to_thread(conversation_store.get_conversation, session_id)
        workspace = conv.workspace if conv is not None else None
    refusal = sharing_refusal(mode, level, workspace)
    if refusal is not None:
        raise OmnigentError(refusal, code=ErrorCode.FORBIDDEN)


__all__ = [
    "READ_ONLY_MESSAGE",
    "SHARING_OFF_MESSAGE",
    "WORKSPACE_BLOCKED_MESSAGE",
    "require_sharing_allows",
    "sharing_mode",
    "sharing_refusal",
]
