"""Launch-mode truth for factory members: what the spec wants vs what was stored.

A session's launch args (e.g. Claude's ``--permission-mode``) are fixed when
it is created. A factory member created before its spec owned the mode can
keep a stale one (the live-run lead kept ``auto``); :func:`launch_drift`
reports that so the cockpit can offer a relaunch with :func:`spec_launch_args`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from omnigent.server.factory.membership import is_top_level, load_session_spec

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.server.factory.context import FactoryDeps

_logger = logging.getLogger(__name__)


def spec_launch_args(conv: Conversation, deps: FactoryDeps) -> list[str] | None:
    """
    Return the terminal launch args *conv*'s own spec declares.

    Uses the interactive (top-level) derivation, so only explicit spec
    declarations count (e.g. ``permission_mode: bypassPermissions``).

    :param conv: A session row.
    :param deps: Factory stores (agent store + cache).
    :returns: e.g. ``["--permission-mode", "bypassPermissions"]``, or ``None``
        when the spec declares nothing or cannot be loaded.
    """
    from omnigent.server.routes._sessions.helpers import (
        _derive_terminal_launch_args_from_spec,
    )

    spec = load_session_spec(conv, deps)
    if spec is None:
        return None
    try:
        return _derive_terminal_launch_args_from_spec(spec, headless_defaults=False)
    except ValueError:
        _logger.warning("factory: spec of %s declares invalid launch args", conv.id, exc_info=True)
        return None


def _flag_values(args: list[str] | None) -> dict[str, str]:
    """
    Parse CLI flags into a name -> value map.

    ``["--permission-mode", "auto", "--yolo"]`` gives
    ``{"permission_mode": "auto", "yolo": "true"}``.
    """
    out: dict[str, str] = {}
    items = list(args or [])
    i = 0
    while i < len(items):
        token = items[i]
        if token.startswith("--"):
            name, _, inline = token[2:].partition("=")
            key = name.replace("-", "_")
            if inline:
                out[key] = inline
            elif i + 1 < len(items) and not items[i + 1].startswith("--"):
                out[key] = items[i + 1]
                i += 1
            else:
                out[key] = "true"
        i += 1
    return out


def launch_drift(conv: Conversation, deps: FactoryDeps) -> dict[str, Any] | None:
    """
    Compare a top-level member's stored launch args with its spec's.

    :param conv: A session row; sub-agents always return ``None``.
    :param deps: Factory stores.
    :returns: ``{"fields": [...], "stored": {...}, "spec": {...}}`` naming each
        differing flag (e.g. ``permission_mode``) with its stored and spec
        value (``None`` when absent), or ``None`` when they agree or the spec
        declares no launch args.
    """
    if not is_top_level(conv):
        return None
    expected = spec_launch_args(conv, deps)
    if expected is None:
        return None
    stored = _flag_values(conv.terminal_launch_args)
    wanted = _flag_values(expected)
    fields = sorted(k for k in stored.keys() | wanted.keys() if stored.get(k) != wanted.get(k))
    if not fields:
        return None
    return {
        "fields": fields,
        "stored": {k: stored.get(k) for k in fields},
        "spec": {k: wanted.get(k) for k in fields},
    }


__all__ = ["launch_drift", "spec_launch_args"]
