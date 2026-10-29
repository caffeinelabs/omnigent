"""Turn identity for per-turn policy state.

A *turn* starts whenever a new user-role message reaches a session: a human
prompt, factory mail, a heartbeat, a decision answer, a sub-agent wake notice,
or a prompt typed straight into a native terminal. The server stamps a fresh
opaque id into the server-owned :data:`TURN_LABEL` label at each of those
starts (see :mod:`omnigent.runtime.policies.turns`), and every policy event
carries it as ``event["context"]["turn"]``. Per-turn policies (e.g.
``spawn_bounds``) key their counters by that id, so a new turn gets a fresh
budget whatever started it, without relying on a separate request-phase event
reaching the evaluator.

Neither policies nor API clients can write the label; only the server's
turn-start points do. Paths without a server-side engine (the runner-local
gate) carry no turn id and fall back to their own per-turn reset.
"""

from __future__ import annotations

from collections.abc import Mapping

TURN_LABEL = "omnigent.turn"
"""Server-owned label holding the session's current turn id, e.g. ``"turn_3f2a…"``."""


def turn_from_labels(labels: Mapping[str, str] | None) -> str | None:
    """
    Return the turn id stamped in a conversation's labels.

    :param labels: The conversation's labels, e.g.
        ``{"omnigent.turn": "turn_ab12"}``. ``None`` when unavailable.
    :returns: The turn id, or ``None`` when no turn was stamped yet.
    """
    if not labels:
        return None
    value = labels.get(TURN_LABEL)
    return value if isinstance(value, str) and value else None


def event_turn(event: Mapping[str, object]) -> str | None:
    """
    Return the turn id a policy event belongs to.

    :param event: A V0 policy event, whose ``context.turn`` the server-side
        engine fills from :data:`TURN_LABEL`.
    :returns: The turn id, or ``None`` on paths that carry none (the
        runner-local gate, or a session whose first turn predates stamping).
    """
    context = event.get("context")
    if not isinstance(context, Mapping):
        return None
    turn = context.get("turn")
    return turn if isinstance(turn, str) and turn else None
