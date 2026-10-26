"""Factory previews as the supervisor sees them: live ones hold a pod, idle ones get parked.

A factory preview is a namespace deployed at a PR's head. It lives in the PL's
pod's lifetime, so the supervisor keeps that pod alive while a preview of an
open PR is live (K1), and asks the PL to park a ready preview no human has
looked at for ``OMNIGENT_FACTORY_PREVIEW_TTL_H`` hours (K3), so idle pods can
sleep. The preview JSON is ``factory_prs.preview`` (``schemas.PreviewView``).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryDecision, FactoryPr

_logger = logging.getLogger(__name__)

PREVIEW_TTL_ENV: Final = "OMNIGENT_FACTORY_PREVIEW_TTL_H"
DEFAULT_PREVIEW_TTL_H: Final = 72.0
LIVE_PREVIEW_STATES: Final = frozenset({"deploying", "ready", "stale", "starting"})
"""States that hold the owning pod (``starting`` is the older name of ``deploying``)."""
TTL_EVENT_KIND: Final = "preview.ttl"


def preview_ttl_h() -> float | None:
    """
    The preview idle TTL in hours.

    :returns: ``OMNIGENT_FACTORY_PREVIEW_TTL_H`` (default 72), or ``None`` when
        set to ``0`` (parking off).
    """
    raw = os.environ.get(PREVIEW_TTL_ENV, "").strip()
    if not raw:
        return DEFAULT_PREVIEW_TTL_H
    try:
        value = float(raw)
    except ValueError:
        _logger.warning("Ignoring invalid %s=%r", PREVIEW_TTL_ENV, raw)
        return DEFAULT_PREVIEW_TTL_H
    return value if value > 0 else None


def preview_state(preview: Mapping[str, Any] | None) -> str | None:
    """:returns: The preview's ``state``, or ``None`` without a preview."""
    state = (preview or {}).get("state")
    return state if isinstance(state, str) else None


def preview_is_live(preview: Mapping[str, Any] | None) -> bool:
    """:returns: Whether the preview is deploying, ready or stale (it holds its pod)."""
    return preview_state(preview) in LIVE_PREVIEW_STATES


def has_live_preview(prs: Iterable[FactoryPr], project: str) -> bool:
    """
    :param prs: PRs of one factory (any state).
    :param project: The PL's project slug.
    :returns: Whether an open PR of *project* has a live preview.
    """
    return any(
        pr.state == "open" and pr.project_slug == project and preview_is_live(pr.preview)
        for pr in prs
    )


def preview_name(pr: FactoryPr) -> str:
    """:returns: The preview's name, ``p<PR number>`` unless it reported another."""
    name = (pr.preview or {}).get("name")
    return name if isinstance(name, str) and name else f"p{pr.number}"


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and value > 0 else None


def attention_at(pr: FactoryPr, decisions: Iterable[FactoryDecision] = ()) -> float | None:
    """
    When a human (or a new head) last gave this preview reason to stay up.

    The newest of: the deploy of its current head, a recorded view, the human's
    review of the PR and an answered decision about its project. Falls back to
    the last report when the deploy time is unknown.

    :param pr: The PR row.
    :param decisions: Decisions of the PR's factory (only answered ones of its
        project count).
    :returns: Epoch seconds, or ``None`` when nothing is known.
    """
    preview = pr.preview or {}
    deployed = preview.get("deployed") if isinstance(preview.get("deployed"), Mapping) else {}
    review = pr.human_review or {}
    times = [
        _number(deployed.get("at")),
        _number(preview.get("last_viewed_at")),
        _number(review.get("at")),
        *(
            _number(d.answered_at)
            for d in decisions
            if d.status == "answered" and d.project_slug == pr.project_slug
        ),
    ]
    known = [t for t in times if t is not None]
    return max(known) if known else _number(preview.get("reported_at"))


def _hours(ttl_h: float) -> str:
    return f"{int(ttl_h)}h" if float(ttl_h).is_integer() else f"{ttl_h:g}h"


def ttl_notice_text(pr: FactoryPr, ttl_h: float) -> str:
    """:returns: The ``[Factory notice · preview p<N> idle 72h · park it]`` notice for the PL."""
    hours = _hours(ttl_h)
    return (
        f"[Factory notice · preview {preview_name(pr)} idle {hours} · park it] "
        f"No human has looked at the preview of PR {pr.repo}#{pr.number} for {hours}. "
        f"Send env_keeper PREVIEW_DOWN pr={pr.number} reason=ttl. The demo stays "
        "reviewable; the cockpit can redeploy the preview later."
    )


def pr_subject(pr: FactoryPr) -> str:
    """:returns: The event subject of a PR, e.g. ``"pr:caffeinelabs/app#9630"``."""
    return f"pr:{pr.repo}#{pr.number}"


__all__ = [
    "DEFAULT_PREVIEW_TTL_H",
    "LIVE_PREVIEW_STATES",
    "PREVIEW_TTL_ENV",
    "TTL_EVENT_KIND",
    "attention_at",
    "has_live_preview",
    "pr_subject",
    "preview_is_live",
    "preview_name",
    "preview_state",
    "preview_ttl_h",
    "ttl_notice_text",
]
