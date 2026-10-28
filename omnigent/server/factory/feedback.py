"""Qualitative signals from people (``factory_feedback``).

- PR ratings 1-5 with reason tags (the PR page's Approve / Request changes);
- demo useful 👍/👎 (+1/-1), "this question was unnecessary" on a decision;
- the closure reason of a ``pr_closed`` decision (its options carry
  ``reasons``; an answer without one records no closure);
- "Is this right?" calibration of retro and finding labels (+1/-1, or a
  corrected category in ``tags``) and root-cause confirmations on incidents.

Taxonomies are the seeded Tier-0 constants :data:`~omnigent.factory.CLOSURE_REASONS`
and :data:`~omnigent.factory.ROOT_CAUSES`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from omnigent.factory import CLOSURE_REASONS, FEEDBACK_KINDS, FEEDBACK_TARGETS, ROOT_CAUSES
from omnigent.server.auth import LEVEL_READ
from omnigent.server.factory import control, definition, events, live
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.start import parse_body

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryDecision, FactoryFeedback

_logger = logging.getLogger(__name__)

TAGS_MAX: Final = 8
BODY_MAX: Final = 2000
CLOSURE_SOURCE: Final = "pr_closed"
_TAG_SETS: Final[dict[str, tuple[str, ...]]] = {
    "closure": CLOSURE_REASONS,
    "root_cause": ROOT_CAUSES,
    "calibration": ROOT_CAUSES,
}


class FeedbackIn(BaseModel):
    """``POST /v1/factory/feedback``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    factory_id: str
    target_kind: Literal[
        "pr", "demo", "decision", "project", "member", "factory", "issue", "proposal"
    ]
    target_id: str = Field(min_length=1, max_length=64)
    kind: Literal["rating", "useful", "unneeded", "closure", "calibration", "root_cause"]
    rating: int | None = None
    tags: list[str] = Field(default_factory=list, max_length=TAGS_MAX)
    body: str | None = Field(None, max_length=BODY_MAX)
    head_sha: str | None = Field(None, max_length=40)
    project: str | None = Field(None, max_length=64)


def check(kind: str, rating: int | None, tags: Sequence[str]) -> None:
    """
    Validate one signal's rating and tags.

    :raises CockpitError: 400 ``invalid_rating`` / ``invalid_tag``.
    """
    if kind == "rating" and (rating is None or not 1 <= rating <= 5):
        raise CockpitError(400, "invalid_rating", "a rating is 1-5", field="rating")
    if kind in ("useful", "calibration") and rating not in (None, -1, 1):
        raise CockpitError(400, "invalid_rating", "thumbs are +1 or -1", field="rating")
    if kind == "useful" and rating is None:
        raise CockpitError(400, "invalid_rating", "useful needs +1 or -1", field="rating")
    allowed = _TAG_SETS.get(kind)
    if allowed is not None:
        bad = [t for t in tags if t not in allowed]
        if bad:
            raise CockpitError(
                400,
                "invalid_tag",
                f"{kind} tags must be from: {', '.join(allowed)}",
                field="tags",
            )
    if kind in ("closure", "root_cause") and not tags:
        raise CockpitError(400, "invalid_tag", f"{kind} needs a tag", field="tags")


async def add(
    app_state: Any,
    factory_id: str,
    *,
    author: str,
    author_kind: str,
    target_kind: str,
    target_id: str,
    kind: str,
    rating: int | None = None,
    tags: Sequence[str] = (),
    body: str | None = None,
    head_sha: str | None = None,
    project: str | None = None,
) -> FactoryFeedback:
    """Store one signal (validated) and emit ``feedback.added``."""
    if target_kind not in FEEDBACK_TARGETS or kind not in FEEDBACK_KINDS:
        raise CockpitError(400, "invalid_body", "unknown target kind or feedback kind")
    check(kind, rating, tags)
    deps = factory_deps(app_state)
    version = await asyncio.to_thread(definition.pinned_version, deps, factory_id)
    row = await asyncio.to_thread(
        lambda: deps.factory_store.add_feedback(
            factory_id,
            target_kind=target_kind,
            target_id=target_id,
            author_kind=author_kind,
            author=author,
            kind=kind,
            project_slug=project,
            head_sha=head_sha,
            rating=rating,
            tags=list(tags),
            body=body,
            def_version=version,
        )
    )
    await events.emit(
        app_state,
        factory_id,
        "feedback.added",
        severity="info",
        project=project,
        subject=f"{target_kind}:{target_id}",
        summary=f"{control.human_name(author) if author_kind == 'human' else author} · {kind}"
        + (f" {rating}" if rating is not None else "")
        + (f" [{', '.join(tags)}]" if tags else ""),
        data={
            "feedback_id": row.id,
            "kind": kind,
            "rating": rating,
            "tags": list(tags),
            "target_kind": target_kind,
            "target_id": target_id,
            "by": author,
            "by_kind": author_kind,
            "def_version": version,
        },
    )
    live.announce_factory_changed(app_state, factory_id, ["insights"])
    return row


async def _check_project(app_state: Any, user_id: str | None, root: Any, slug: str) -> None:
    """:raises CockpitError: 404 ``unknown_project`` unless *user_id* sees project *slug*."""
    from omnigent.server.factory.snapshot import get_snapshot, readable_for

    raw = await get_snapshot(app_state, root)
    pl = raw.project_pl.get(slug)
    visible = slug in raw.project_pl and (
        pl is None or pl in await readable_for(user_id, raw, factory_deps(app_state))
    )
    if not visible:
        raise CockpitError(404, "unknown_project", f"no project {slug!r}", field="project")


async def post(app_state: Any, user_id: str | None, payload: Any) -> dict[str, Any]:
    """``POST /v1/factory/feedback``: anyone who reads the factory (viewers too) may rate."""
    body = parse_body(FeedbackIn, payload if payload is not None else {})
    deps = factory_deps(app_state)
    root = await control.load_factory_root(deps, user_id, body.factory_id)
    await control.require(deps, user_id, root.id, LEVEL_READ)
    if body.project is not None:
        await _check_project(app_state, user_id, root, body.project)
    row = await add(
        app_state,
        root.id,
        author=user_id or "local",
        author_kind="human",
        target_kind=body.target_kind,
        target_id=body.target_id,
        kind=body.kind,
        rating=body.rating,
        tags=body.tags,
        body=body.body,
        head_sha=body.head_sha,
        project=body.project,
    )
    out = row.to_dict()
    out.pop("workspace_id", None)
    return {"feedback": out}


# ── PR closure reasons ─────────────────────────────────────────


def closure_options(options: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """:returns: The ``pr_closed`` options, each carrying the required ``reasons`` picker."""
    return [{**o, "reasons": list(CLOSURE_REASONS)} for o in options]


def closure_reason(
    decision: FactoryDecision, option: Mapping[str, Any] | None, reason: str | None
) -> str | None:
    """
    The closure reason of an answer, validated.

    An answer without one is still accepted (and records no closure): the
    cockpit's answer forms do not send a reason yet.

    :returns: The reason, or ``None`` when *decision* asks for none or none was given.
    :raises CockpitError: 400 ``invalid_tag`` for a reason outside the picker.
    """
    if decision.source != CLOSURE_SOURCE or not reason:
        return None
    pickers = [o.get("reasons") for o in decision.options if isinstance(o.get("reasons"), list)]
    if not pickers:
        return None
    allowed = option.get("reasons") if option is not None else pickers[0]
    if reason not in (allowed or CLOSURE_REASONS):
        raise CockpitError(
            400, "invalid_tag", f"unknown closure reason {reason!r}", field="reason"
        )
    return reason


async def record_closure(
    app_state: Any, decision: FactoryDecision, reason: str, user_id: str | None
) -> None:
    """Store the closure reason of an answered ``pr_closed`` decision. Never raises."""
    pr_id = (decision.dedupe_key or "").removeprefix("pr_closed:") or decision.ref
    try:
        await add(
            app_state,
            decision.factory_id,
            author=user_id or "local",
            author_kind="human",
            target_kind="pr",
            target_id=pr_id,
            kind="closure",
            tags=[reason],
            project=decision.project_slug,
        )
    except Exception:  # noqa: BLE001 - the answer itself is stored
        _logger.warning("factory: could not record the closure of %s", decision.ref, exc_info=True)


async def record_pr_rating(
    app_state: Any,
    pr: Any,
    user_id: str | None,
    rating: int | None,
    tags: Sequence[str],
    text: str | None,
) -> None:
    """Store the rating a person gave with a cockpit review (when they gave one)."""
    if rating is None:
        return
    await add(
        app_state,
        pr.factory_id,
        author=user_id or "local",
        author_kind="human",
        target_kind="pr",
        target_id=pr.id,
        kind="rating",
        rating=rating,
        tags=list(tags),
        body=text,
        head_sha=pr.head_sha or None,
        project=pr.project_slug,
    )


__all__ = [
    "FeedbackIn",
    "add",
    "check",
    "closure_options",
    "closure_reason",
    "post",
    "record_closure",
    "record_pr_rating",
]
