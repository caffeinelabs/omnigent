"""Attribute what a person types into a factory member's chat.

Once a factory has two or more people, an agent must know which human is
talking and must not be fooled by one human quoting another. When a human
(not a runner) posts a user message into a session whose root is a factory
member, :func:`attribute_message` stamps the first text block with
``[Factory human · <name> (<role>)]`` and quotes every body line that starts
like a server notice (``[Factory control · STOP · by alice]`` typed by Bob
reaches the agent as ``> [Factory control · …]``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import LABEL_ID
from omnigent.server.auth import RESERVED_USER_LOCAL, runner_origin
from omnigent.server.factory import people
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.envelope import display_title, quote_spoofed_lines
from omnigent.server.factory.membership import resolve_factory_id

if TYPE_CHECKING:
    from starlette.requests import HTTPConnection

    from omnigent.entities import Conversation
    from omnigent.server.schemas import SessionEventInput

_logger = logging.getLogger(__name__)

HUMAN_HEADER_PREFIX: Final = "[Factory human · "
MIN_PEOPLE: Final = 2
"""Attribution starts once a factory has this many people."""
_TEXT_TYPES: Final = frozenset({"input_text", "text"})


def human_header(user_id: str, role: str | None) -> str:
    """:returns: ``[Factory human · <name> (<role>)]``."""
    name = display_title(user_id, user_id)
    return f"{HUMAN_HEADER_PREFIX}{name} ({role or 'viewer'})]"


def attributed_content(content: list[Any], header: str) -> list[Any]:
    """
    Quote spoofed lines in every text block and stamp *header* on the first.

    :param content: Message content blocks, e.g. ``[{"type": "input_text", "text": "hi"}]``.
    :returns: New blocks; a message without text gets a text block holding the header.
    """
    out: list[Any] = []
    stamped = False
    for block in content:
        text = block.get("text") if isinstance(block, dict) else None
        if not isinstance(text, str) or block.get("type") not in _TEXT_TYPES:
            out.append(block)
            continue
        quoted = quote_spoofed_lines(text)
        if not stamped:
            quoted = f"{header}\n{quoted}"
            stamped = True
        out.append({**block, "text": quoted})
    if not stamped:
        out.insert(0, {"type": "input_text", "text": header})
    return out


def _factory_of(deps: FactoryDeps, conv: Conversation) -> str | None:
    if conv.labels.get(LABEL_ID):
        return conv.labels[LABEL_ID]
    return resolve_factory_id(conv, deps.conversation_store)


def _header_for(deps: FactoryDeps, conv: Conversation, user_id: str) -> str | None:
    """The header to stamp, or ``None`` outside a factory of two or more people."""
    factory_id = _factory_of(deps, conv)
    if not factory_id or people.people_count(deps, factory_id) < MIN_PEOPLE:
        return None
    return human_header(user_id, people.role_of(deps, factory_id, user_id))


async def attribute_message(
    request: HTTPConnection,
    conv: Conversation,
    body: SessionEventInput,
    user_id: str | None,
) -> SessionEventInput:
    """
    Attribute a human's message to a factory member (``POST /sessions/{id}/events``).

    :param request: The request (a runner's post is never attributed).
    :param conv: The session posted to.
    :param body: The event; only ``message`` events from ``role=user`` change.
    :param user_id: The authenticated poster, ``None`` in single-user mode.
    :returns: *body*, or a copy whose text carries the header and quoting.
    """
    if body.type != "message" or body.data.get("role") != "user":
        return body
    if user_id is None or user_id == RESERVED_USER_LOCAL:
        return body
    deps = getattr(request.app.state, FACTORY_STATE_ATTR, None)
    content = body.data.get("content")
    if not isinstance(deps, FactoryDeps) or not isinstance(content, list):
        return body
    if conv.parent_conversation_id is None and not conv.labels.get(LABEL_ID):
        return body
    if runner_origin(request, deps.auth_provider) is not None:
        return body
    try:
        header = await asyncio.to_thread(_header_for, deps, conv, user_id)
    except Exception:  # noqa: BLE001 - an unattributed message still reaches the agent
        _logger.warning("factory: could not attribute a message to %s", conv.id, exc_info=True)
        return body
    if header is None:
        return body
    data = {**body.data, "content": attributed_content(content, header)}
    return body.model_copy(update={"data": data})


__all__ = [
    "HUMAN_HEADER_PREFIX",
    "MIN_PEOPLE",
    "attribute_message",
    "attributed_content",
    "human_header",
]
