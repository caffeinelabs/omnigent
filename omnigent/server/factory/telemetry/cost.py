"""The factory cost ledger (``factory_cost_daily``): every turn's spend, attributed.

``routes/_sessions/helpers._record_daily_cost`` already sees each turn's
cost delta; its hook calls :func:`record`, which attributes the session to
its factory, project (``""`` for the leads), role and agent through the
labels of the session or of its root (sub-agents), cached for 60 s, and adds
the delta to that day's ledger cell. The cost is the display cost
(``total_cost_usd``); an unpriced turn adds its tokens with ``$0``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import LABEL_ID, LABEL_PROJECT, LABEL_ROLE, ROLE_IC, ROLE_PROJECT_LEAD
from omnigent.server.factory.membership import is_top_level, resolve_role, root_conversation

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.server.factory.context import FactoryDeps

_logger = logging.getLogger(__name__)

TTL_S: Final = 60.0
_CACHE_MAX: Final = 10_000
_NAME_MAX: Final = 64
_ROLE_MAX: Final = 16
_INPUT_KEYS: Final = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


@dataclass(frozen=True)
class Attribution:
    """
    Where one session's spend goes in the ledger.

    :param project: Project slug, ``""`` for the leads and their sub-agents.
    :param role: Factory role, e.g. ``"ic"``.
    :param agent_name: Session title (top-level) or sub-agent name.
    """

    factory_id: str
    project: str
    role: str
    agent_name: str


_deps: FactoryDeps | None = None
_cache: dict[str, tuple[float, Attribution | None]] = {}


def install(deps: FactoryDeps) -> Callable[[], None]:
    """Start recording turn costs into *deps*'s ledger. :returns: The uninstaller."""
    global _deps
    _deps = deps

    def _uninstall() -> None:
        global _deps
        if _deps is deps:
            _deps = None

    return _uninstall


def reset() -> None:
    """Forget the attribution cache (tests)."""
    _cache.clear()


def _compute(deps: FactoryDeps, conv: Conversation) -> Attribution | None:
    title = (conv.title or "").strip()
    if is_top_level(conv):
        factory_id = conv.labels.get(LABEL_ID)
        if not factory_id:
            return None
        # The ledger's role column is 16 wide: a longer label must not fail every booking.
        role = (conv.labels.get(LABEL_ROLE) or "")[:_ROLE_MAX]
        project = (conv.labels.get(LABEL_PROJECT) or "") if role == ROLE_PROJECT_LEAD else ""
        return Attribution(factory_id, project, role, (title or conv.id)[:_NAME_MAX])
    root = root_conversation(conv, deps.conversation_store)
    factory_id = root.labels.get(LABEL_ID) if root is not conv else None
    if not factory_id:
        return None
    root_role = root.labels.get(LABEL_ROLE)
    project = (root.labels.get(LABEL_PROJECT) or "") if root_role == ROLE_PROJECT_LEAD else ""
    role = (resolve_role(conv, deps) or ROLE_IC)[:_ROLE_MAX]
    name = conv.sub_agent_name or title or conv.id
    return Attribution(factory_id, project, role, name[:_NAME_MAX])


def attribution(deps: FactoryDeps, conv: Conversation) -> Attribution | None:
    """
    The ledger cell owner of *conv*, or ``None`` outside any factory (cached 60 s).

    A top-level member uses its own labels; a sub-agent its root's factory and
    project and its own spec role.
    """
    now = time.monotonic()
    cached = _cache.get(conv.id)
    if cached is not None and now - cached[0] < TTL_S:
        return cached[1]
    found = _compute(deps, conv)
    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    _cache[conv.id] = (now, found)
    return found


def utc_day(epoch_s: float | None = None) -> str:
    """:returns: ``YYYY-MM-DD`` of *epoch_s* (now when ``None``) in UTC."""
    when = time.time() if epoch_s is None else epoch_s
    return datetime.fromtimestamp(when, tz=UTC).date().isoformat()


def token_growth(old: Mapping[str, Any], new: Mapping[str, Any]) -> tuple[int, int]:
    """
    Input and output tokens one cumulative usage report added.

    :param old: The session usage before the report.
    :param new: The session usage after it.
    :returns: ``(tokens_in, tokens_out)``, never negative.
    """

    def total(usage: Mapping[str, Any], keys: tuple[str, ...]) -> int:
        return sum(int(usage.get(k) or 0) for k in keys)

    grown_in = total(new, _INPUT_KEYS) - total(old, _INPUT_KEYS)
    grown_out = total(new, ("output_tokens",)) - total(old, ("output_tokens",))
    return max(0, grown_in), max(0, grown_out)


def record(
    conv: Conversation | None,
    delta_usd: float,
    tokens: tuple[int, int] = (0, 0),
    model: str | None = None,
    *,
    day: str | None = None,
) -> Attribution | None:
    """
    Add one turn's cost to the ledger when *conv* is a factory session. Never raises.

    :param conv: The session whose turn this was.
    :param delta_usd: The turn's display cost (``0`` when unpriced).
    :param tokens: ``(tokens_in, tokens_out)`` of the turn.
    :param model: The model that ran the turn, if known.
    :param day: ``YYYY-MM-DD`` (tests); defaults to today in UTC.
    :returns: Where it was booked, or ``None`` when nothing was.
    """
    deps = _deps
    tokens_in, tokens_out = (max(0, int(t or 0)) for t in tokens)
    cost = max(0.0, float(delta_usd or 0.0))
    if deps is None or conv is None or (cost <= 0 and not tokens_in and not tokens_out):
        return None
    try:
        who = attribution(deps, conv)
        if who is None:
            return None
        deps.factory_store.add_cost_daily(
            who.factory_id,
            project_slug=who.project,
            role=who.role,
            agent_name=who.agent_name,
            model=(model or "")[:_NAME_MAX],
            day_utc=day or utc_day(),
            cost_usd=cost,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            turns=1,
        )
        return who
    except Exception:  # noqa: BLE001 - the ledger must never break a turn
        _logger.warning("factory cost: could not book a turn of %s", conv.id, exc_info=True)
        return None


__all__ = [
    "Attribution",
    "attribution",
    "install",
    "record",
    "reset",
    "token_growth",
    "utc_day",
]
