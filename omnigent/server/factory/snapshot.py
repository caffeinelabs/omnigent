"""The cockpit snapshot: one factory as the cockpit renders it, from server truth.

:func:`get_snapshot` builds (and caches for :data:`SNAPSHOT_TTL_S`) the
unfiltered ``FactorySnapshot`` of a factory from the store rows, the member
roster, the sandbox launch cache, launch-mode drift, labels, the derived
stages, subtree costs and the services' health registry.
:func:`filter_snapshot` then cuts it down to what one viewer may read: the
projects, events, decisions and demos of PLs they cannot read are dropped and
counted in ``hidden_projects``.

Agent-reported JSON (env, preview, gates, demo manifests) is validated piece by
piece against :mod:`~omnigent.server.factory.schemas`; a piece that does not
fit is dropped, never the whole snapshot.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel, ValidationError

from omnigent.entities.factory import FactoryProject
from omnigent.factory import (
    AGENT_STAGES,
    CADENCE_DEMO,
    CADENCE_HEARTBEATS,
    CADENCE_NORMAL,
    CADENCES,
    FOLDER_DELETED,
    LABEL_CADENCE,
    LABEL_FOLDER_PROJECT,
    LABEL_HEALTH,
    LABEL_HEARTBEAT_S,
    LABEL_ID,
    LABEL_KEEP_PREVIEWS,
    LABEL_LAST_BEAT,
    LABEL_NAME,
    LABEL_PARENT,
    LABEL_PROJECT,
    LABEL_ROLE,
    LABEL_STARTED_AT,
    LABEL_STATE,
    LABEL_SUMMARY,
    PROJECT_KIND_INTEGRATION,
    ROLE_LEAD,
    ROLE_PROJECT_LEAD,
    ROLES,
    STATE_ACTIVE,
    STATES,
)
from omnigent.server.factory import activity_now, detectors, schemas, status
from omnigent.server.factory.activity_now import factory_usd_per_h
from omnigent.server.factory.base import base_view
from omnigent.server.factory.charter import normalize_charter
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.envelope import display_title
from omnigent.server.factory.gating import unmet_dependencies
from omnigent.server.factory.issues import counts_open, visible_to
from omnigent.server.factory.launch import launch_drift
from omnigent.server.factory.membership import factory_members, load_session_spec
from omnigent.server.factory.notify import parse_iso
from omnigent.server.factory.roster import build_roster, session_url
from omnigent.server.factory.stage import StageResult, derive_stage
from omnigent.stores.factory_store import norm_id
from omnigent.util.session_lifecycle import is_session_closed

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactoryDecision, FactoryDemo, FactoryEvent, FactoryPr
    from omnigent.stores import ConversationStore

_logger = logging.getLogger(__name__)

SNAPSHOT_TTL_S: Final = 2.0
COST_TTL_S: Final = 60.0
MAIL_WINDOW_S: Final = 6 * 3600
CONTROL_WINDOW_S: Final = 7 * 86400
ANSWERED_DECISIONS: Final = 10
PL_TITLE_PREFIX: Final = "PL · "

SANDBOX_STAGE_LABELS: Final = {
    "provisioning": "Provisioning sandbox",
    "cloning": "Cloning repository",
    "starting": "Connecting host",
    "connecting": "Starting agent",
    "briefing": "Briefing the PL",
}
"""Provisioning steps of a queued project (the web's ``SANDBOX_STAGE_LABELS`` plus the brief)."""


# ── Small helpers ──────────────────────────────────────────────


def bare_id(value: str) -> str:
    """:returns: *value* as bare hex, or unchanged when it is not a uuid."""
    try:
        return norm_id(value)
    except ValueError:
        return value


def is_factory_root(conv: Conversation) -> bool:
    """:returns: Whether *conv* is a factory root: a lead labelled with its own id."""
    own = conv.labels.get(LABEL_ID)
    return (
        bool(own)
        and conv.labels.get(LABEL_ROLE) == ROLE_LEAD
        and bare_id(own or "") == bare_id(conv.id)
        and conv.parent_conversation_id is None
    )


def list_factory_roots(store: ConversationStore) -> list[Conversation]:
    """:returns: Every non-archived factory root on this server, oldest first."""
    ids = store.list_conversation_ids_with_label(LABEL_ID)
    rows = store.get_conversations(ids)
    return [rows[i] for i in ids if i in rows and is_factory_root(rows[i])]


def _lenient(model: type[BaseModel], data: Any) -> dict[str, Any] | None:
    """Validate agent-reported JSON against *model*; drop unknown top-level keys, else ``None``."""
    if not isinstance(data, Mapping):
        return None
    try:
        return model.model_validate(dict(data)).model_dump(mode="json")
    except ValidationError:
        pass
    known = {k: v for k, v in data.items() if k in model.model_fields}
    try:
        return model.model_validate(known).model_dump(mode="json")
    except ValidationError:
        _logger.debug("factory snapshot: dropping a %s that does not fit", model.__name__)
        return None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def _heartbeat_s(conv: Conversation) -> int | None:
    value = _int_or_none(conv.labels.get(LABEL_HEARTBEAT_S))
    return value if value and value > 0 else None


def _role(value: str | None) -> str | None:
    return value if value in ROLES else None


def pl_title_slug(title: str | None) -> str:
    """:returns: A PL session title without its ``"PL · "`` prefix."""
    text = (title or "").strip()
    return text[len(PL_TITLE_PREFIX) :].strip() if text.startswith(PL_TITLE_PREFIX) else text


# ── Costs (cached) ─────────────────────────────────────────────

_cost_cache: dict[str, tuple[float, float | None]] = {}


def subtree_cost(conv: Conversation, store: ConversationStore) -> float | None:
    """
    Priced USD spend of *conv* and every sub-agent under it (cached 60 s).

    :returns: The cost, or ``None`` when nothing in the tree was priced.
    """
    now = time.monotonic()
    cached = _cost_cache.get(conv.id)
    if cached is not None and now - cached[0] < COST_TTL_S:
        return cached[1]
    from omnigent.runtime.policies.builder import load_session_usage

    try:
        usage = load_session_usage(conv.id, store, root_conversation_id=conv.root_conversation_id)
        raw = usage.get("total_cost_usd")
        value = float(raw) if isinstance(raw, int | float) else None
    except Exception:  # noqa: BLE001 - cost is informational
        _logger.debug("factory snapshot: could not load usage of %s", conv.id, exc_info=True)
        value = None
    _cost_cache[conv.id] = (now, value)
    return value


def _own_cost(conv: Conversation) -> float | None:
    raw = (conv.session_usage or {}).get("total_cost_usd")
    return float(raw) if isinstance(raw, int | float) else None


def _sum_costs(values: Iterable[float | None]) -> float | None:
    priced = [v for v in values if v is not None]
    return round(sum(priced), 4) if priced else None


# ── Stage inputs ───────────────────────────────────────────────


def pending_brief_targets(mail: Sequence[FactoryEvent]) -> set[str]:
    """:returns: Sessions with a queued ``brief`` that has no delivered/bounced event yet."""
    closed = {e.subject for e in mail if e.kind in ("message.delivered", "message.bounced")}
    return {
        e.to_id
        for e in mail
        if e.kind == "message.queued"
        and e.to_id
        and (e.data or {}).get("kind") == "brief"
        and e.subject not in closed
    }


def sandbox_stage(session_id: str) -> str | None:
    """:returns: The managed-sandbox launch stage of a session, or ``None`` when settled."""
    from omnigent.server.routes._sessions.common import _session_sandbox_status_cache

    cached = _session_sandbox_status_cache.get(session_id)
    stage = getattr(cached, "stage", None)
    return stage if isinstance(stage, str) and stage != "ready" else None


def pick_pl(
    project: FactoryProject | None, slug: str, members: Sequence[Conversation]
) -> Conversation | None:
    """
    The PL session of a project.

    :returns: The row's linked PL when it is a member, else the active (then
        newest) member labelled ``project_lead`` for *slug*.
    """
    by_id = {m.id: m for m in members}
    if project is not None and project.pl_session_id:
        linked = by_id.get(project.pl_session_id) or by_id.get(bare_id(project.pl_session_id))
        if linked is not None:
            return linked
    candidates = [
        m
        for m in members
        if m.labels.get(LABEL_PROJECT) == slug and m.labels.get(LABEL_ROLE) == ROLE_PROJECT_LEAD
    ]
    candidates.sort(key=lambda c: (c.labels.get(LABEL_STATE) != STATE_ACTIVE, -c.created_at))
    return candidates[0] if candidates else None


def project_stage(
    project: FactoryProject,
    prs: Sequence[FactoryPr],
    pl: Conversation | None,
    open_decisions: Sequence[FactoryDecision],
    pending_briefs: set[str],
    waiting_on: Sequence[str] = (),
) -> StageResult:
    """:returns: The derived stage of *project* (see :func:`.stage.derive_stage`)."""
    own = [p for p in prs if p.project_slug == project.slug]
    sandbox = sandbox_stage(pl.id) if pl is not None else None
    brief_delivered = pl is not None and pl.id not in pending_briefs and sandbox is None
    return derive_stage(
        project,
        own,
        pl,
        open_decisions,
        brief_delivered=brief_delivered,
        sandbox_failed=sandbox == "failed",
        waiting_on=waiting_on,
    )


# ── The unfiltered snapshot ────────────────────────────────────


@dataclass
class RawSnapshot:
    """
    One factory's unfiltered snapshot plus what per-viewer filtering needs.

    :param factory_id: Bare root id.
    :param body: The ``FactorySnapshot`` JSON for a viewer who can read everything.
    :param top_level_of: Session id -> the top-level member it belongs to.
    :param project_pl: Project slug -> PL session id (``None`` before spawn).
    :param demo_owner: Demo id -> the PL session owning its files.
    :param titles: Session id -> display title.
    :param built_at: ``time.monotonic()`` of the build.
    :param incident_scopes: ``(project, target)`` of each open or acked incident.
    """

    factory_id: str
    body: dict[str, Any]
    top_level_of: dict[str, str] = field(default_factory=dict)
    project_pl: dict[str, str | None] = field(default_factory=dict)
    demo_owner: dict[str, str] = field(default_factory=dict)
    titles: dict[str, str] = field(default_factory=dict)
    built_at: float = 0.0
    incident_scopes: list[tuple[str | None, str]] = field(default_factory=list)


@dataclass
class _Loaded:
    """Everything the builder reads, loaded in one worker thread."""

    members: list[Conversation]
    closed_members: list[Conversation]
    children: dict[str, list[Conversation]]
    projects: list[FactoryProject]
    prs: list[FactoryPr]
    open_decisions: list[FactoryDecision]
    closed_decisions: list[FactoryDecision]
    demos: list[FactoryDemo]
    mail: list[FactoryEvent]
    controls: list[FactoryEvent]
    harness: dict[str, str | None]
    drift: dict[str, dict[str, Any] | None]
    costs: dict[str, float | None]
    proposals_pending: int = 0
    issues: list[Any] = field(default_factory=list)
    now: dict[str, dict[str, Any]] = field(default_factory=dict)


def _harness(
    conv: Conversation, deps: FactoryDeps, memo: dict[tuple[Any, ...], Any]
) -> str | None:
    if conv.harness_override:
        return conv.harness_override
    key = (conv.agent_id, conv.sub_agent_name)
    if key not in memo:
        spec = load_session_spec(conv, deps)
        config = getattr(getattr(spec, "executor", None), "config", None)
        harness = config.get("harness") if isinstance(config, dict) else None
        memo[key] = harness if isinstance(harness, str) else None
    return memo[key]


def _load(deps: FactoryDeps, factory_id: str, now: float) -> _Loaded:
    store = deps.conversation_store
    fstore = deps.factory_store
    everyone = sorted(
        factory_members(factory_id, store), key=lambda m: (m.id != factory_id, m.created_at)
    )
    members = [m for m in everyone if not is_session_closed(m.labels, m.title)]
    closed = [m for m in everyone if is_session_closed(m.labels, m.title)]
    children: dict[str, list[Conversation]] = {}
    for member in members:
        # The newest 200 (re-dispatches must not fall off the page), shown oldest first.
        page = store.list_conversations(
            limit=200, kind="sub_agent", parent_conversation_id=member.id, order="desc"
        )
        children[member.id] = list(reversed(page.data))
    now_ms = int(now * 1000)
    memo: dict[tuple[Any, ...], Any] = {}
    harness = {
        c.id: _harness(c, deps, memo)
        for c in [*members, *(k for kids in children.values() for k in kids)]
    }
    return _Loaded(
        members=members,
        closed_members=closed,
        children=children,
        projects=fstore.list_projects(factory_id),
        prs=fstore.list_prs(factory_id),
        open_decisions=fstore.list_decisions(factory_id=factory_id, status="open", limit=500),
        closed_decisions=fstore.list_decisions(
            factory_id=factory_id,
            status=["answered", "withdrawn", "expired"],
            limit=ANSWERED_DECISIONS,
        ),
        demos=fstore.list_demos(factory_id, limit=1000),
        mail=fstore.find_events(
            factory_id,
            ["message.queued", "message.delivered", "message.bounced"],
            now_ms - MAIL_WINDOW_S * 1000,
            limit=5000,
        ),
        controls=fstore.find_events(
            factory_id, "control.*", now_ms - CONTROL_WINDOW_S * 1000, limit=2000
        ),
        harness=harness,
        drift={m.id: launch_drift(m, deps) for m in members},
        costs={m.id: subtree_cost(m, store) for m in members},
        proposals_pending=len(
            fstore.list_proposals(factory_id, status=["pending", "awaiting_cosign"], limit=500)
        ),
        issues=fstore.list_issues(factory_id, class_="incident", statuses=["open", "acked"]),
    )


def _next_beat(conv: Conversation, now: float) -> int | None:
    """When a member's next heartbeat is due at the latest (quiet beats may skip it)."""
    interval = _heartbeat_s(conv)
    if conv.labels.get(LABEL_STATE) != STATE_ACTIVE or interval is None:
        return None
    last = parse_iso(conv.labels.get(LABEL_LAST_BEAT)) or 0.0
    due = max(last, float(conv.updated_at)) + interval
    if due < now:
        due += ((now - due) // interval + 1) * interval
    return int(due)


def _folder_id(root: Conversation) -> str | None:
    folder = root.labels.get(LABEL_FOLDER_PROJECT)
    return None if folder == FOLDER_DELETED else folder


def _cadence(root: Conversation) -> str:
    label = root.labels.get(LABEL_CADENCE)
    if label is not None and label in CADENCES:
        return label
    lead_demo = CADENCE_HEARTBEATS[CADENCE_DEMO][ROLE_LEAD]
    hb = _heartbeat_s(root)
    return CADENCE_DEMO if hb is not None and hb <= lead_demo else CADENCE_NORMAL


def _member_view(
    conv: Conversation,
    row: Mapping[str, Any] | None,
    *,
    loaded: _Loaded,
    top: Conversation,
) -> dict[str, Any]:
    is_top = conv.id == top.id
    labels = conv.labels
    status_value = (row or {}).get("status") or "idle"
    drift = loaded.drift.get(conv.id) if is_top else None
    state = labels.get(LABEL_STATE) if is_top else None
    role = _role(labels.get(LABEL_ROLE)) if is_top else _role((row or {}).get("role"))
    return {
        "session_id": conv.id,
        "title": display_title((row or {}).get("title") or conv.title, conv.id),
        "role": role,
        "project": top.labels.get(LABEL_PROJECT),
        "parent_session_id": labels.get(LABEL_PARENT) if is_top else conv.parent_conversation_id,
        "is_top_level": is_top,
        "status": status_value,
        "state": state if state in STATES else None,
        "health": labels.get(LABEL_HEALTH),
        "summary": labels.get(LABEL_SUMMARY),
        "harness": loaded.harness.get(conv.id),
        "running": status_value == "running",
        "last_activity_at": _int_or_none((row or {}).get("last_activity_at")),
        "heartbeat_s": _heartbeat_s(conv) if is_top else None,
        "cost_usd": loaded.costs.get(conv.id) if is_top else _own_cost(conv),
        "launch_drift": _lenient(schemas.LaunchDriftView, drift) if drift else None,
        "url": session_url(conv.id),
        "now": _lenient(schemas.NowState, live) if (live := loaded.now.get(conv.id)) else None,
    }


def _member_ref(view: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "session_id",
        "title",
        "role",
        "status",
        "harness",
        "running",
        "health",
        "summary",
        "last_activity_at",
        "cost_usd",
        "url",
    )
    ref = {k: view[k] for k in keys}
    ref["has_launch_drift"] = view.get("launch_drift") is not None
    return ref


def _preview_view(pr: FactoryPr) -> dict[str, Any] | None:
    if not pr.preview:
        return None
    raw = dict(pr.preview)
    if raw.get("state") == "starting":
        raw["state"] = "deploying"
    raw.setdefault("name", f"p{pr.number}")
    raw["stable_url"] = f"/v1/factory/prs/{pr.id}/preview"
    return _lenient(schemas.PreviewView, raw)


_STEP_STATUS: Final = {
    "pass": "pass",
    "passed": "pass",
    "ok": "pass",
    "fail": "fail",
    "failed": "fail",
    "skip": "skipped",
    "skipped": "skipped",
}
_FILE_KINDS: Final = frozenset(
    {"video", "screenshot", "poster", "api", "har", "trace", "console", "script", "log", "other"}
)


def _demo_steps(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    raw_steps = manifest.get("steps")
    for i, raw in enumerate(raw_steps if isinstance(raw_steps, list) else [], start=1):
        if not isinstance(raw, Mapping):
            continue
        kind = raw.get("kind")
        step = {
            "n": _int_or_none(raw.get("n")) or i,
            "title": str(raw.get("title") or f"Step {i}"),
            "kind": kind if kind in ("ui", "api", "check") else "check",
            "status": _STEP_STATUS.get(str(raw.get("status") or "").lower(), "fail"),
            "screenshot_file_id": raw.get("screenshot_file_id"),
            "duration_ms": _int_or_none(raw.get("duration_ms")),
            "note": raw.get("note"),
            "api_calls": raw.get("api_calls"),
        }
        view = _lenient(schemas.DemoStepView, step)
        if view is not None:
            steps.append(view)
    return steps


def _demo_view(demo: FactoryDemo, head_sha: str, titles: Mapping[str, str]) -> dict[str, Any]:
    manifest = demo.manifest or {}
    files: list[dict[str, Any]] = []
    for raw in demo.files or []:
        file_id = raw.get("file_id") or raw.get("id")
        if not file_id:
            continue
        size = _int_or_none(raw.get("bytes") if "bytes" in raw else raw.get("size")) or 0
        kind = raw.get("kind")
        files.append(
            {
                "id": str(file_id),
                "name": str(raw.get("name") or file_id),
                "kind": kind if kind in _FILE_KINDS else "other",
                "caption": raw.get("caption"),
                "content_type": str(raw.get("content_type") or "application/octet-stream"),
                "bytes": size,
                "size": size,
                "url": f"/v1/factory/demos/{demo.id}/files/{file_id}",
                "step": _int_or_none(raw.get("step")),
            }
        )
    errors = manifest.get("console_errors")
    duration = manifest.get("duration_s")
    return {
        "id": demo.id,
        "status": demo.status,
        "head_sha": demo.head_sha,
        "is_current_head": bool(head_sha) and demo.head_sha == head_sha,
        "title": demo.title,
        "summary": demo.summary,
        "plan_source": manifest.get("plan_source")
        if isinstance(manifest.get("plan_source"), str)
        else None,
        "duration_s": float(duration) if isinstance(duration, int | float) else None,
        "console_errors": len(errors) if isinstance(errors, list) else _int_or_none(errors),
        "steps": _demo_steps(manifest),
        "files": files,
        "video_file_id": next((f["id"] for f in files if f["kind"] == "video"), None),
        "poster_file_id": next((f["id"] for f in files if f["kind"] == "poster"), None),
        "preview_url": demo.preview_url,
        "created_at": demo.created_at,
        "by": titles.get(demo.created_by_session),
    }


def project_demo_view(
    demo: FactoryDemo, head_sha: str, titles: Mapping[str, str]
) -> dict[str, Any] | None:
    """A demo as the demos list shows it (``ProjectDemoView``), or ``None`` when it doesn't fit."""
    view = {
        **_demo_view(demo, head_sha, titles),
        "pr_number": demo.pr_number,
        "pr_id": demo.pr_id,
        "repo": demo.repo,
        "project_slug": demo.project_slug or None,
    }
    return _lenient(schemas.ProjectDemoView, view)


def _pr_view(pr: FactoryPr, demos: Sequence[FactoryDemo], titles: Mapping[str, str]) -> dict:
    # Newest first; a demo of the current head wins a same-second tie.
    demos = sorted(demos, key=lambda d: (d.created_at, d.head_sha == pr.head_sha), reverse=True)
    merged = None
    if pr.state == "merged" and pr.merged_at:
        merged = {"by": pr.merged_by, "sha": pr.merged_sha, "at": pr.merged_at}
    source = pr.source if pr.source in ("tool", "discovered") else None
    return {
        "id": pr.id,
        "project_slug": pr.project_slug,
        "repo": pr.repo,
        "number": pr.number,
        "url": pr.url,
        "title": pr.title,
        "task": pr.task,
        "head_sha": pr.head_sha,
        "head_ref": pr.head_ref,
        "base_ref": pr.base_ref or None,
        "state": pr.state,
        "is_draft": pr.is_draft,
        "additions": pr.additions,
        "deletions": pr.deletions,
        "changed_files": pr.changed_files,
        "gates": _lenient(schemas.GatesView, pr.gates or {}) or schemas.GatesView().model_dump(),
        "ready": pr.ready,
        "ready_since": pr.ready_at,
        "sentinel": _lenient(schemas.SentinelView, pr.sentinel),
        "human_review": _lenient(schemas.HumanReviewView, pr.human_review),
        "preview": _preview_view(pr),
        "demo": _lenient(schemas.DemoView, _demo_view(demos[0], pr.head_sha, titles))
        if demos
        else None,
        "older_demos": max(0, len(demos) - 1),
        "merged": merged,
        "closed_at": pr.closed_at,
        "last_polled_at": pr.last_polled_at,
        "poll_error": pr.poll_error,
        "source": source,
    }


def _options(raw: Iterable[Any]) -> list[dict[str, Any]]:
    out = []
    for option in raw or []:
        view = _lenient(schemas.DecisionOptionView, option)
        if view is not None:
            out.append(view)
    return out


def decision_view(
    decision: FactoryDecision,
    titles: Mapping[str, str],
    roles: Mapping[str, str | None],
    delivered_to: Mapping[str, str],
) -> dict[str, Any]:
    """:returns: The ``DecisionView`` JSON of one decision."""
    asker = decision.asked_by_session_id
    target = delivered_to.get(decision.delivered_mail_id or "")
    return {
        "id": decision.id,
        "ref": decision.ref,
        "number": decision.number,
        "factory_id": decision.factory_id,
        "project_slug": decision.project_slug,
        "asked_by_session_id": asker,
        "asked_by_title": titles.get(asker) if asker else None,
        "asked_by_role": _role(roles.get(asker)) if asker else None,
        "source": decision.source,
        "blocking": decision.blocking,
        "question": decision.question,
        "context": decision.context,
        "options": _options(decision.options),
        "allow_text": decision.allow_text,
        "status": decision.status,
        "answer_option_id": decision.answer_option_id,
        "answer_text": decision.answer_text,
        "answered_by": decision.answered_by,
        "answered_at": decision.answered_at,
        "delivered_mail_id": decision.delivered_mail_id,
        "delivered_at": decision.delivered_at,
        "delivered_to_title": titles.get(target) if target else None,
        "created_at": decision.created_at,
        "updated_at": decision.updated_at,
        "assignee": decision.assignee,
        "claimed_by": decision.claimed_by,
        "claimed_at": decision.claimed_at,
        "thread": decision.thread,
        "subject": decision.subject,
    }


def _health(app_state: Any, drifted: int, demo_bytes: int) -> dict[str, Any]:
    from omnigent.server.factory.mailer import MAILER_STATE_ATTR

    supervisor = status.get_status("supervisor") or {}
    github = status.get_status("github") or {}
    probes = status.get_status("probes") or {}
    mailer = getattr(app_state, MAILER_STATE_ATTR, None)
    queued, oldest = mailer.queue_stats() if mailer is not None else (0, None)
    github_state = github.get("state")
    probes_state = probes.get("state")
    return {
        "supervisor": {
            "last_tick_at": _int_or_none(supervisor.get("last_tick_at")),
            "ok": supervisor.get("ok") if isinstance(supervisor.get("ok"), bool) else None,
        },
        "mail": {"queued": queued, "oldest_queued_s": int(oldest) if oldest is not None else None},
        "github": {
            "state": github_state
            if github_state in ("ok", "degraded", "disconnected", "off")
            else "off",
            "last_poll_at": _int_or_none(github.get("last_poll_at")),
            "error": github.get("error") if isinstance(github.get("error"), str) else None,
            "rate_limit_remaining": _int_or_none(github.get("rate_limit_remaining")),
        },
        "probes": {
            "state": probes_state if probes_state in ("ok", "unreachable", "off") else "off",
            "last_run_at": _int_or_none(probes.get("last_run_at")),
        },
        "drifted_members": drifted,
        "demo_storage_bytes": demo_bytes,
    }


def _paused_by(controls: Sequence[FactoryEvent]) -> dict[str, str]:
    """Project slug -> who paused it, from the newest pause/resume control per project."""
    out: dict[str, str] = {}
    for event in controls:
        if not event.project_slug or event.kind not in ("control.pause", "control.resume"):
            continue
        data = event.data or {}
        who = data.get("by") or data.get("from")
        if event.kind == "control.pause" and isinstance(who, str):
            out[event.project_slug] = who
        else:
            out.pop(event.project_slug, None)
    return out


def _project_view(
    project: FactoryProject,
    pl: Conversation | None,
    result: StageResult,
    *,
    views: Mapping[str, dict[str, Any]],
    loaded: _Loaded,
    pending: set[str],
    paused_by: Mapping[str, str],
    now: float,
) -> dict[str, Any]:
    pr_ids = [p.id for p in loaded.prs if p.project_slug == project.slug]
    open_ids = [
        d.id
        for d in loaded.open_decisions
        if d.project_slug == project.slug or (pl is not None and d.asked_by_session_id == pl.id)
    ]
    team = [_member_ref(views[c.id]) for c in loaded.children.get(pl.id, [])] if pl else []
    provisioning = None
    if result.stage == "queued" and pl is not None:
        stage = sandbox_stage(pl.id) or ("briefing" if pl.id in pending else None)
        if stage in SANDBOX_STAGE_LABELS:
            provisioning = {"stage": stage, "label": SANDBOX_STAGE_LABELS[stage]}
    charter = _lenient(schemas.CharterView, normalize_charter(project.charter))
    agent_stage = project.agent_stage if project.agent_stage in AGENT_STAGES else None
    env = _lenient(schemas.EnvView, project.env) if project.env else None
    # Until reconcile persists a new stage, the stored stamp stands in (stable for ETags).
    since = project.stage_changed_at or project.created_at
    keep = pl is None or pl.labels.get(LABEL_KEEP_PREVIEWS) != "0"
    return {
        "slug": project.slug,
        "title": project.title,
        "repo": project.repo,
        "base_branch": project.base_branch,
        "priority": project.priority,
        "charter": charter if project.charter is not None else None,
        "stage": result.stage,
        "stage_since": since or int(now),
        "overlay": result.overlay,
        "overlay_reason": result.overlay_reason,
        "agent_stage": agent_stage,
        "claim_mismatch": result.claim_mismatch,
        "pl": _member_ref(views[pl.id]) if pl is not None and pl.id in views else None,
        "team": team,
        "env": env,
        "pr_ids": pr_ids,
        "open_decision_ids": open_ids,
        "cost_usd": loaded.costs.get(pl.id) if pl is not None else None,
        "elapsed_s": max(0, int(now - (project.created_at or now))),
        "provisioning": provisioning,
        "keep_previews": keep,
        "paused_by": paused_by.get(project.slug) if result.overlay == "paused" else None,
        **_lifecycle(project),
    }


def _lifecycle(project: FactoryProject) -> dict[str, Any]:
    """The project's kind, lifecycle status and dependencies (round-3 fields)."""
    charter = normalize_charter(project.charter)
    return {
        "kind": charter["kind"],
        "status": project.status,
        "parent_slug": project.parent_slug,
        "closed_at": project.closed_at,
        "depends_on": charter.get("depends_on") or None,
        "depends_mode": charter.get("depends_mode"),
        "human_owner": charter.get("human_owner"),
        "def_version": project.def_version,
    }


def _virtual_projects(
    factory_id: str, projects: Sequence[FactoryProject], members: Sequence[Conversation]
) -> list[FactoryProject]:
    """Projects a PL label names but no row records yet (reconcile backfills them)."""
    known = {p.slug for p in projects}
    out: list[FactoryProject] = []
    for member in sorted(members, key=lambda m: m.created_at):
        slug = member.labels.get(LABEL_PROJECT)
        if member.labels.get(LABEL_ROLE) != ROLE_PROJECT_LEAD or not slug or slug in known:
            continue
        known.add(slug)
        out.append(
            FactoryProject(
                id="",
                factory_id=factory_id,
                slug=slug,
                title=pl_title_slug(member.title) or slug,
                pl_session_id=member.id,
                stage_changed_at=member.created_at,
                created_at=member.created_at,
                updated_at=member.created_at,
            )
        )
    return out


async def build_snapshot(app_state: Any, root: Conversation) -> RawSnapshot:
    """
    Build the unfiltered snapshot of the factory rooted at *root*.

    :param app_state: ``app.state`` with the factory deps registered.
    :param root: The factory root session.
    :returns: The :class:`RawSnapshot`.
    """
    deps = factory_deps(app_state)
    now = time.time()
    factory_id = root.id
    loaded = await asyncio.to_thread(_load, deps, factory_id, now)
    loaded.now = activity_now.now_states(factory_id)
    roster = await build_roster(factory_id, loaded.members, deps, app_state)
    rows = {r["session_id"]: r for r in roster["members"]}
    child_rows = {c["session_id"]: c for r in roster["members"] for c in r.get("children", [])}

    views: dict[str, dict[str, Any]] = {}
    top_level_of: dict[str, str] = {}
    titles: dict[str, str] = {}
    roles: dict[str, str | None] = {}
    for member in loaded.members:
        views[member.id] = _member_view(member, rows.get(member.id), loaded=loaded, top=member)
        top_level_of[member.id] = member.id
        for child in loaded.children.get(member.id, []):
            views[child.id] = _member_view(
                child, child_rows.get(child.id), loaded=loaded, top=member
            )
            top_level_of[child.id] = member.id
    for member in loaded.closed_members:
        top_level_of.setdefault(member.id, member.id)
        titles.setdefault(member.id, display_title(member.title, member.id))
    for sid, view in views.items():
        titles[sid] = view["title"]
        roles[sid] = view["role"]

    projects = [
        *loaded.projects,
        *_virtual_projects(factory_id, loaded.projects, loaded.members),
    ]
    pending = pending_brief_targets(loaded.mail)
    paused = _paused_by(loaded.controls)
    project_views: list[dict[str, Any]] = []
    project_pl: dict[str, str | None] = {}
    by_slug = {p.slug: p for p in projects}
    for project in projects:
        pl = pick_pl(project, project.slug, loaded.members)
        waiting = (
            unmet_dependencies(project, by_slug, loaded.prs)
            if pl is None and project.status == "active"
            else []
        )
        result = project_stage(
            project, loaded.prs, pl, loaded.open_decisions, pending, waiting_on=waiting
        )
        project_pl[project.slug] = pl.id if pl is not None else None
        project_views.append(
            _project_view(
                project,
                pl,
                result,
                views=views,
                loaded=loaded,
                pending=pending,
                paused_by=paused,
                now=now,
            )
        )

    # Closed projects (cancelled, merged into another, done) fold below the live ones.
    project_views.sort(key=lambda v: v.get("status") not in (None, "active"))
    demos_by_pr: dict[tuple[str, int], list[FactoryDemo]] = {}
    for demo in loaded.demos:  # newest first
        demos_by_pr.setdefault((demo.repo, demo.pr_number), []).append(demo)
    pr_views = [
        _pr_view(pr, demos_by_pr.get((pr.repo, pr.number), []), titles) for pr in loaded.prs
    ]

    delivered_to = {
        (e.data or {}).get("mail_id", ""): e.to_id
        for e in loaded.mail
        if e.kind == "message.delivered" and e.to_id
    }
    decisions = [
        view
        for d in [*sorted(loaded.open_decisions, key=lambda d: d.number), *loaded.closed_decisions]
        if (view := _lenient(schemas.DecisionView, decision_view(d, titles, roles, delivered_to)))
    ]

    member_views = list(views.values())
    drifted = sum(1 for v in member_views if v["launch_drift"] is not None)
    demo_bytes = sum(
        _int_or_none(f.get("bytes") if "bytes" in f else f.get("size")) or 0
        for d in loaded.demos
        for f in d.files or []
    )
    started_at = _int_or_none(root.labels.get(LABEL_STARTED_AT)) or root.created_at
    root_state = root.labels.get(LABEL_STATE)
    cost = _sum_costs(loaded.costs.values())
    next_beats = [
        {"session_id": m.id, "title": titles[m.id], "at": at}
        for m in loaded.members
        if (at := _next_beat(m, now)) is not None
    ]
    body: dict[str, Any] = {
        "factory": {
            "id": bare_id(factory_id),
            "name": root.labels.get(LABEL_NAME) or display_title(root.title, root.id),
            "state": root_state if root_state in STATES else STATE_ACTIVE,
            "cadence": _cadence(root),
            "started_at": started_at,
            "root_session_id": root.id,
            "folder_project_id": _folder_id(root),
            "elapsed_s": max(0, int(now - started_at)),
            "cost_usd": cost,
            "next_beats": sorted(next_beats, key=lambda b: b["at"]),
            "base": base_view(
                root, projects, {view["slug"]: view["env"] for view in project_views}
            ),
            "url": f"/factory/{bare_id(factory_id)}",
        },
        "health": _health(app_state, drifted, demo_bytes),
        "projects": project_views,
        "prs": pr_views,
        "decisions": decisions,
        "members": member_views,
        "hidden_projects": 0,
        "totals": {},
        "server_time": int(now),
        "counts": {
            "proposals_pending": loaded.proposals_pending,
            "issues_open": counts_open(loaded.issues, frozenset()),
        },
    }
    rate = detectors.spend_rate(factory_id)
    body["totals"] = _totals(
        body, cost, rate if rate is not None else factory_usd_per_h(factory_id)
    )
    validated = schemas.FactorySnapshot.model_validate(body).model_dump(mode="json")
    return RawSnapshot(
        factory_id=bare_id(factory_id),
        body=validated,
        top_level_of=top_level_of,
        project_pl=project_pl,
        demo_owner={d.id: d.session_id for d in loaded.demos},
        titles=titles,
        built_at=time.monotonic(),
        incident_scopes=[(i.project_slug, i.target) for i in loaded.issues],
    )


def _totals(
    body: Mapping[str, Any], cost: float | None, usd_per_h: float | None = None
) -> dict[str, Any]:
    prs = body["prs"]
    return {
        "cost_usd": cost,
        "prs_open": sum(1 for p in prs if p["state"] == "open"),
        "prs_ready": sum(1 for p in prs if p["state"] == "open" and p["ready"]),
        "prs_merged": sum(1 for p in prs if p["state"] == "merged"),
        "decisions_open": sum(1 for d in body["decisions"] if d["status"] == "open"),
        "usd_per_h": usd_per_h,
    }


# ── Cache and per-viewer filtering ─────────────────────────────

_snapshots: dict[str, RawSnapshot] = {}
_locks: dict[tuple[int, str], asyncio.Lock] = {}
_viewers: dict[tuple[str | None, str], tuple[float, set[str]]] = {}
_VIEWER_CACHE_MAX: Final = 1000


async def get_snapshot(app_state: Any, root: Conversation) -> RawSnapshot:
    """The unfiltered snapshot of *root*'s factory, rebuilt at most every 2 s."""
    key = bare_id(root.id)
    cached = _snapshots.get(key)
    if cached is not None and time.monotonic() - cached.built_at < SNAPSHOT_TTL_S:
        return cached
    lock = _locks.setdefault((id(asyncio.get_running_loop()), key), asyncio.Lock())
    async with lock:
        cached = _snapshots.get(key)
        if cached is not None and time.monotonic() - cached.built_at < SNAPSHOT_TTL_S:
            return cached
        raw = await build_snapshot(app_state, root)
        _snapshots[key] = raw
        return raw


def reset_cache() -> None:
    """Forget cached snapshots, viewer permissions and costs (tests)."""
    _snapshots.clear()
    _cost_cache.clear()
    _locks.clear()
    _viewers.clear()


def readable_set(
    user_id: str | None,
    session_ids: Iterable[str],
    deps: FactoryDeps,
) -> set[str]:
    """
    The sessions *user_id* may read, by the same check the session routes run.

    Synchronous (run it in a worker thread).
    """
    from omnigent.server.factory.org_chart import _may_read

    ids = set(session_ids)
    if deps.permission_store is None:
        return ids
    return {
        sid
        for sid in ids
        if _may_read(user_id, sid, deps.conversation_store, deps.permission_store)
    }


def viewer_readable(raw: RawSnapshot, readable_top: set[str]) -> set[str]:
    """Every member whose top-level member is in *readable_top* (sub-agents inherit READ)."""
    return {sid for sid, top in raw.top_level_of.items() if top in readable_top}


async def readable_for(user_id: str | None, raw: RawSnapshot, deps: FactoryDeps) -> set[str]:
    """The members of *raw* a viewer may read (cached as long as a snapshot)."""
    key = (user_id, raw.factory_id)
    cached = _viewers.get(key)
    if cached is not None and time.monotonic() - cached[0] < SNAPSHOT_TTL_S:
        return cached[1]
    tops = set(raw.top_level_of.values())
    readable = viewer_readable(raw, await asyncio.to_thread(readable_set, user_id, tops, deps))
    if len(_viewers) >= _VIEWER_CACHE_MAX:
        _viewers.clear()
    _viewers[key] = (time.monotonic(), readable)
    return readable


def filter_snapshot(raw: RawSnapshot, readable: set[str]) -> dict[str, Any]:
    """
    Cut *raw* down to what a viewer reading *readable* may see.

    Projects whose PL is unreadable are dropped (and counted in
    ``hidden_projects``) with their PRs; decisions whose asker and demos whose
    PL are unreadable are dropped; members and team chips likewise.
    """
    body = copy.deepcopy(raw.body)
    hidden = {slug for slug, pl in raw.project_pl.items() if pl is not None and pl not in readable}
    visible_projects = [p for p in body["projects"] if p["slug"] not in hidden]
    body["hidden_projects"] = len(body["projects"]) - len(visible_projects)
    body["decisions"] = [
        d
        for d in body["decisions"]
        if not d["asked_by_session_id"] or d["asked_by_session_id"] in readable
    ]
    open_ids = {d["id"] for d in body["decisions"]}
    for project in visible_projects:
        project["team"] = [m for m in project["team"] if m["session_id"] in readable]
        project["open_decision_ids"] = [i for i in project["open_decision_ids"] if i in open_ids]
    body["projects"] = visible_projects
    prs = [p for p in body["prs"] if p["project_slug"] not in hidden]
    for pr in prs:
        demo = pr.get("demo")
        if demo is not None and raw.demo_owner.get(demo["id"]) not in readable:
            pr["demo"], pr["older_demos"] = None, 0
    body["prs"] = prs
    visible_pr_ids = {p["id"] for p in prs}
    for project in visible_projects:
        project["pr_ids"] = [i for i in project["pr_ids"] if i in visible_pr_ids]
    body["members"] = [m for m in body["members"] if m["session_id"] in readable]
    factory = body["factory"]
    factory["next_beats"] = [b for b in factory["next_beats"] if b["session_id"] in readable]
    base = factory.get("base")
    if base is not None and base.get("env_project") in hidden:
        base["env"] = None
    rate = body["totals"].get("usd_per_h")
    if len(readable) < len(raw.top_level_of):
        member_costs = [m["cost_usd"] for m in body["members"] if m["is_top_level"]]
        factory["cost_usd"] = _sum_costs(member_costs)
        rate = None
    body["totals"] = _totals(body, factory["cost_usd"], rate)
    body["counts"] = {
        **(body.get("counts") or {}),
        "issues_open": sum(
            1
            for project, target in raw.incident_scopes
            if visible_to(
                project,
                target,
                hidden=hidden,
                top_level_of=raw.top_level_of,
                readable=readable,
            )
        ),
    }
    return body


def snapshot_etag(body: Mapping[str, Any]) -> str:
    """
    A weak ETag over the snapshot.

    Ignores what only moves with the clock (``server_time`` and the
    ``elapsed_s`` counters, which the cockpit ticks itself), so an unchanged
    factory answers 304.
    """
    stable = {k: v for k, v in body.items() if k != "server_time"}
    stable["factory"] = {k: v for k, v in body["factory"].items() if k != "elapsed_s"}
    stable["projects"] = [
        {k: v for k, v in p.items() if k != "elapsed_s"} for p in body["projects"]
    ]
    digest = hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return f'W/"{digest[:32]}"'


def summary_from(body: Mapping[str, Any]) -> dict[str, Any]:
    """:returns: The ``FactorySummary`` JSON of a (filtered) snapshot."""
    factory = body["factory"]
    # The live base env is not pipeline work, and closed projects are folded away.
    work = [
        p
        for p in body["projects"]
        if (p.get("charter") or {}).get("kind") != PROJECT_KIND_INTEGRATION
        and p.get("status") in (None, "active")
    ]
    counts: dict[str, int] = {}
    for project in work:
        counts[project["stage"]] = counts.get(project["stage"], 0) + 1
    members = body["members"]
    return {
        "id": factory["id"],
        "name": factory["name"],
        "state": factory["state"],
        "cadence": factory["cadence"],
        "started_at": factory["started_at"],
        "root_session_id": factory["root_session_id"],
        "elapsed_s": factory["elapsed_s"],
        "cost_usd": factory["cost_usd"],
        "stage_counts": counts,
        "projects_total": len(work),
        "decisions_open": body["totals"]["decisions_open"],
        "prs_ready": body["totals"]["prs_ready"],
        "members_total": len(members),
        "members_running": sum(1 for m in members if m["running"]),
        "drifted_members": sum(1 for m in members if m["launch_drift"] is not None),
        "url": factory["url"],
    }


__all__ = [
    "SANDBOX_STAGE_LABELS",
    "SNAPSHOT_TTL_S",
    "RawSnapshot",
    "build_snapshot",
    "decision_view",
    "filter_snapshot",
    "get_snapshot",
    "is_factory_root",
    "list_factory_roots",
    "pending_brief_targets",
    "pick_pl",
    "pl_title_slug",
    "project_demo_view",
    "project_stage",
    "readable_for",
    "readable_set",
    "reset_cache",
    "snapshot_etag",
    "subtree_cost",
    "summary_from",
    "viewer_readable",
]
