"""Stamp the start of a session turn for per-turn policy state.

The server calls :func:`begin_turn` wherever a new user-role message enters a
session, before the harness can act on it:

- every user message the server dispatches to a runner (web/API prompts,
  factory mail, heartbeats, decision answers, wake notices, scheduled runs),
  in ``_dispatch_session_event_to_runner_impl``;
- every request-phase hook a native harness posts when it submits a prompt to
  its model (``POST /sessions/{id}/policies/evaluate`` with ``PHASE_REQUEST``),
  which also covers prompts typed straight into the terminal.

The id lands in the server-owned :data:`~omnigent.policies.turns.TURN_LABEL`
label, which the policy engine exposes as ``event["context"]["turn"]``.
"""

from __future__ import annotations

import logging
import secrets

from omnigent.policies.turns import TURN_LABEL
from omnigent.stores import ConversationStore

_logger = logging.getLogger(__name__)


def new_turn_id() -> str:
    """:returns: A fresh opaque turn id, e.g. ``"turn_3f2a9c0d1e4b5a6f"``."""
    return f"turn_{secrets.token_hex(8)}"


def begin_turn(conversation_store: ConversationStore, session_id: str) -> str | None:
    """
    Stamp a fresh turn id on *session_id*.

    Best-effort: a failed write is logged and leaves the previous turn id in
    place, so per-turn budgets stay conservative (the next turn start
    retries) rather than failing the dispatch.

    :param conversation_store: Store holding the session's labels.
    :param session_id: The session whose turn starts, e.g. ``"conv_abc123"``.
    :returns: The new turn id, or ``None`` when the write failed.
    """
    turn_id = new_turn_id()
    try:
        conversation_store.set_labels(session_id, {TURN_LABEL: turn_id})
    except Exception:  # a missed stamp must not fail the turn
        _logger.warning(
            "could not stamp the turn start of %s",
            session_id,
            exc_info=True,
            extra={"session_id": session_id},
        )
        return None
    return turn_id
