"""Retros and findings filed by agents, and Sentinel's daily retro beat (rule S12).

``sys_factory_retro`` (``POST /v1/factory/agent/retros``) writes one
``factory_issues`` row of class ``retro`` or ``finding``: what went well, what
slowed it, root causes mapped onto the seeded taxonomy (each with evidence
ids) and suggested changes. A call that cites no evidence is refused (422).

The retro beat: once a day at ``OMNIGENT_FACTORY_RETRO_HOUR`` (UTC, default 6)
and when a project is done, Sentinel gets ``[Factory heartbeat · retro · …]``
and runs the ``factory-retro`` skill. Each beat is recorded as a
``retro.requested`` event and sent once, also across a server restart.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from omnigent.factory import (
    LABEL_ROLE,
    LABEL_STATE,
    ROLE_LEAD,
    ROLE_PROJECT_LEAD,
    ROOT_CAUSES,
    STAGE_DONE,
    STATE_ACTIVE,
)
from omnigent.server.factory import control, definition, events, issues
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.start import parse_body

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.server.factory.agent_scope import AgentCaller

_logger = logging.getLogger(__name__)

RETRO_HOUR_ENV: Final = "OMNIGENT_FACTORY_RETRO_HOUR"
DEFAULT_RETRO_HOUR: Final = 6
REQUESTED_KIND: Final = "retro.requested"
ADDED_KIND: Final = "retro.added"
FIRST_RETRO_AFTER_S: Final = 86400
"""The daily retro starts once the factory is a day old."""
PROJECT_DONE_LOOKBACK_S: Final = 7 * 86400
"""A project done longer ago than this gets no retro beat (no backfill on first deploy)."""
TEXT_MAX: Final = 1000


# ── sys_factory_retro ──────────────────────────────────────────


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RootCauseIn(_Body):
    category: str
    text: str = Field(min_length=1, max_length=TEXT_MAX)
    evidence: list[definition.EvidenceId] = Field(default_factory=list, max_length=20)


class SuggestionIn(_Body):
    area: str = Field(min_length=1, max_length=64)
    change: str = Field(min_length=1, max_length=TEXT_MAX)
    expected_effect: str | None = Field(None, max_length=300)
    metric: str | None = Field(None, max_length=64)


class RetroIn(_Body):
    """``POST /v1/factory/agent/retros`` (``from_session_id`` aside)."""

    kind: Literal["retro", "finding"] = "retro"
    target: str = Field(min_length=1, max_length=128)
    summary: str | None = Field(None, max_length=300)
    went_well: str | None = Field(None, max_length=TEXT_MAX)
    slowed: str | None = Field(None, max_length=TEXT_MAX)
    root_causes: list[RootCauseIn] = Field(default_factory=list, max_length=10)
    proposals: list[SuggestionIn] = Field(default_factory=list, max_length=5)
    evidence: list[definition.EvidenceId] = Field(default_factory=list, max_length=20)
    severity: Literal["info", "notice", "warning", "critical"] | None = None


def _target(raw: str, caller: AgentCaller) -> str:
    """Canonical target: ``project:<slug>``, ``pr:<n>``, ``factory`` or a ref."""
    text = raw.strip()
    if text.startswith("#") and len(text) > 1:
        text = f"project:{text[1:]}"
    elif text.startswith("!") and text[1:].isdigit():
        text = f"pr:{text[1:]}"
    if caller.membership.role == ROLE_PROJECT_LEAD:
        own = caller.membership.project
        if text.startswith("project:") and text != f"project:{own}":
            raise CockpitError(
                403, "not_your_project", "a project lead files retros for its project"
            )
    return text


def _body_md(body: RetroIn) -> str:
    lines: list[str] = []
    if body.went_well:
        lines += [f"**Went well:** {body.went_well}"]
    if body.slowed:
        lines += [f"**Slowed:** {body.slowed}"]
    for cause in body.root_causes:
        refs = ", ".join(cause.evidence)
        lines.append(f"- `{cause.category}` {cause.text}" + (f" ({refs})" if refs else ""))
    for item in body.proposals:
        metric = f" → {item.metric}" if item.metric else ""
        lines.append(f"- propose ({item.area}): {item.change}{metric}")
    return "\n".join(lines)[:8000]


async def file_retro(
    app_state: Any, caller: AgentCaller, payload: Mapping[str, Any]
) -> dict[str, Any]:
    """
    Record a retro or a finding for a lead or project lead.

    :raises CockpitError: 422 ``evidence_required`` without any evidence id;
        400 ``unknown_category`` outside the taxonomy; 403 for other roles.
    """
    body = parse_body(RetroIn, payload)
    role = caller.membership.role
    if role not in (ROLE_LEAD, ROLE_PROJECT_LEAD) or not caller.membership.is_top_level:
        raise CockpitError(403, "not_allowed", "leads and project leads file retros")
    for i, cause in enumerate(body.root_causes):
        if cause.category not in ROOT_CAUSES:
            raise CockpitError(
                400,
                "unknown_category",
                f"root_causes[{i}].category must be one of {', '.join(ROOT_CAUSES)}",
                field=f"root_causes[{i}].category",
            )
    evidence = [*body.evidence, *(e for c in body.root_causes for e in c.evidence)]
    if not evidence:
        raise CockpitError(
            422,
            "evidence_required",
            "cite at least one evidence id (D-n, F-n, INC-n, !pr, T-n, an event or span id)",
        )
    target = _target(body.target, caller)
    deps = caller.deps
    category = body.root_causes[0].category if body.root_causes else None
    summary = (
        body.summary
        or body.slowed
        or body.went_well
        or (body.root_causes[0].text if body.root_causes else body.kind)
    )
    sentinel = role == ROLE_LEAD and not control.same_id(caller.session.id, caller.factory_id)
    project = caller.membership.project if role == ROLE_PROJECT_LEAD else _project_of(target)
    issue = await issues.raise_issue(
        app_state,
        caller.factory_id,
        rule=f"{body.kind}.{category or 'general'}"[:48],
        target=target,
        summary=control.clip(summary, 300),
        class_=body.kind,
        severity=body.severity or ("notice" if body.kind == "finding" else "info"),
        project=project,
        evidence={
            "refs": evidence,
            "root_causes": [c.model_dump() for c in body.root_causes],
            "proposals": [p.model_dump(exclude_none=True) for p in body.proposals],
            "went_well": body.went_well,
            "slowed": body.slowed,
        },
        body=_body_md(body),
        source="sentinel" if sentinel else "agent",
        category=category,
        by={"by": caller.session.id, "by_kind": "agent", "by_role": role},
    )
    if issue is None:
        raise CockpitError(502, "not_stored", "the retro could not be stored; try again")
    def_version = await asyncio.to_thread(definition.pinned_version, deps, caller.factory_id)
    await events.emit(
        app_state,
        caller.factory_id,
        ADDED_KIND,
        severity="info",
        project=project,
        from_id=caller.session.id,
        subject=f"issue:{issue.ref or issue.id}",
        summary=f"{caller.title} filed a {body.kind} on {target}: {control.clip(summary, 160)}",
        data={
            "issue_id": issue.id,
            "class": body.kind,
            "category": category,
            "evidence": evidence[:20],
            "by": caller.session.id,
            "by_kind": "agent",
            "by_role": role,
            "def_version": def_version,
        },
    )
    return {
        "issue": issue.ref or issue.id,
        "id": issue.id,
        "class": issue.class_,
        "count": issue.count,
    }


def _project_of(target: str) -> str | None:
    return target.removeprefix("project:") if target.startswith("project:") else None


# ── The retro beat (S12) ───────────────────────────────────────


def retro_hour() -> int:
    """:returns: ``OMNIGENT_FACTORY_RETRO_HOUR`` (0-23, default 6)."""
    raw = os.environ.get(RETRO_HOUR_ENV, "").strip()
    try:
        value = int(raw) if raw else DEFAULT_RETRO_HOUR
    except ValueError:
        return DEFAULT_RETRO_HOUR
    return value if 0 <= value <= 23 else DEFAULT_RETRO_HOUR


def retro_text(window: str, budget_usd: float, project: str | None = None) -> str:
    """:returns: ``[Factory heartbeat · retro · window 24h · budget $3]`` plus the instruction."""
    scope = f" · project {project} done" if project else ""
    return (
        f"[Factory heartbeat · retro{scope} · window {window} · budget ${budget_usd:g}] "
        'Run load_skill("factory-retro") and follow it: read sys_factory_insights, code the '
        "failures, file at most 2 proposals with evidence, post the digest in #improvements."
    )


def sentinel_of(deps: FactoryDeps, factory_id: str) -> Conversation | None:
    """:returns: The factory's active peer lead (Sentinel), else the root lead."""
    from omnigent.server.factory.membership import factory_members

    members = factory_members(factory_id, deps.conversation_store)
    leads = [
        m
        for m in members
        if m.labels.get(LABEL_ROLE) == ROLE_LEAD and m.labels.get(LABEL_STATE) == STATE_ACTIVE
    ]
    peer = [m for m in leads if not control.same_id(m.id, factory_id)]
    return (peer or leads or [None])[0]


async def _requested(deps: FactoryDeps, factory_id: str, subject: str) -> bool:
    found = await asyncio.to_thread(
        deps.factory_store.find_events, factory_id, REQUESTED_KIND, 0, subject, limit=1
    )
    return bool(found)


_sent: set[tuple[str, str]] = set()


async def request_retro(
    app_state: Any,
    factory_id: str,
    subject: str,
    *,
    window: str = "24h",
    project: str | None = None,
    by: str = "server",
) -> bool:
    """Mail Sentinel one retro beat for *subject* (once). :returns: Whether it went out."""
    deps = factory_deps(app_state)
    key = (factory_id, subject)
    if key in _sent or await _requested(deps, factory_id, subject):
        _sent.add(key)
        return False
    target = await asyncio.to_thread(sentinel_of, deps, factory_id)
    if target is None:
        return False
    budget = float(definition.value(deps, factory_id, "loops.improve.usd", 3.0) or 3.0)
    mail_id = control.send_text(
        app_state, target.id, retro_text(window, budget, project), kind="info"
    )
    _sent.add(key)
    await events.emit(
        app_state,
        factory_id,
        REQUESTED_KIND,
        severity="info",
        project=project,
        to_id=target.id,
        subject=subject,
        summary=f"Retro beat sent to {target.title or target.id}"
        + (f" · project {project} done" if project else f" · window {window}"),
        data={"mail_id": mail_id, "window": window, "budget_usd": budget, "by": by},
    )
    return True


async def tick(app_state: Any, factory_id: str, now: float | None = None) -> list[str]:
    """The daily beat after the retro hour, and one per project done. :returns: Subjects sent."""
    deps = factory_deps(app_state)
    now = now if now is not None else time.time()
    sent: list[str] = []
    stamp = datetime.fromtimestamp(now, tz=UTC)
    root = await control.get_conv(deps, factory_id)
    # A factory on its first day has nothing to look back on yet.
    old_enough = root is not None and now - root.created_at >= FIRST_RETRO_AFTER_S
    if old_enough and stamp.hour >= retro_hour():
        subject = f"retro:{stamp.strftime('%Y-%m-%d')}"
        if await request_retro(app_state, factory_id, subject):
            sent.append(subject)
    for project in await asyncio.to_thread(deps.factory_store.list_projects, factory_id):
        done_at = project.closed_at or project.stage_changed_at
        done = project.stage == STAGE_DONE or project.status == "done"
        if not done or not done_at or now - done_at > PROJECT_DONE_LOOKBACK_S:
            continue
        subject = f"retro:project:{project.slug}"
        if await request_retro(app_state, factory_id, subject, project=project.slug):
            sent.append(subject)
    return sent


def reset() -> None:
    """Forget the in-process memo of sent beats (tests)."""
    _sent.clear()


__all__ = [
    "ADDED_KIND",
    "REQUESTED_KIND",
    "RETRO_HOUR_ENV",
    "RetroIn",
    "file_retro",
    "request_retro",
    "reset",
    "retro_hour",
    "retro_text",
    "sentinel_of",
    "tick",
]
