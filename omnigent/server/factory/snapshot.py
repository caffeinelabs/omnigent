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
import contextvars
import copy
import functools
import hashlib
import json
import logging
import re
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel, ValidationError

from omnigent.entities.factory import FactoryProject, FactoryTask
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
    STAGE_QUEUED,
    STATE_ACTIVE,
    STATES,
)
from omnigent.server.auth import LEVEL_READ, RESERVED_USER_PUBLIC
from omnigent.server.factory import (
    activity_now,
    auto_merge,
    definition,
    detectors,
    identity,
    lead_update,
    loops,
    product_profile,
    schemas,
    status,
    stuck,
    tasks,
    team,
    waits,
)
from omnigent.server.factory.activity_now import factory_usd_per_h
from omnigent.server.factory.base import base_view
from omnigent.server.factory.charter import (
    SPAWN_LEAD,
    SPAWN_SERVER,
    normalize_charter,
    spawn_owner,
)
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.envelope import display_title
from omnigent.server.factory.gating import unmet_dependencies
from omnigent.server.factory.issues import counts_open, visible_to
from omnigent.server.factory.launch import launch_drift
from omnigent.server.factory.membership import (
    agent_rows_memo,
    factory_members,
    load_session_spec,
)
from omnigent.server.factory.notify import parse_iso
from omnigent.server.factory.roster import build_roster, session_url
from omnigent.server.factory.slackdm import rules as slack_rules
from omnigent.server.factory.stage import (
    OPEN_TASK_STATES,
    StageResult,
    derive_stage,
    ready_for_human,
)
from omnigent.stores.factory_store import norm_id
from omnigent.util.session_lifecycle import is_session_closed

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import (
        FactoryDecision,
        FactoryDemo,
        FactoryEvent,
        FactoryPr,
        FactoryProposal,
    )
    from omnigent.stores import ConversationStore

_logger = logging.getLogger(__name__)

SNAPSHOT_TTL_S: Final = 2.0
SNAPSHOT_STALE_S: Final = 60.0
"""A snapshot older than the TTL but younger than this is served while one rebuild runs."""
COST_TTL_S: Final = 60.0
_COST_READERS: Final = 4
"""Members whose spend is read at once on a cold cache (each read is a few queries)."""
MAIL_WINDOW_S: Final = 6 * 3600
NOTIFIED_WINDOW_S: Final = 30 * 86400
"""How far back the snapshot reads Slack DM receipts (``notify.slack``)."""
NOTIFIED_ROWS: Final = 5000
"""At most this many of the newest receipts per snapshot."""
CONTROL_WINDOW_S: Final = 7 * 86400
ANSWERED_DECISIONS: Final = 10
PL_TITLE_PREFIX: Final = "PL · "
_MAX_PARENT_HOPS: Final = 16
"""Sub-agent -> parent hops :func:`readable_set` follows to a top-level session."""

SANDBOX_STAGE_LABELS: Final = {
    "provisioning": "Provisioning sandbox",
    "cloning": "Cloning repository",
    "starting": "Connecting host",
    "connecting": "Starting agent",
    "briefing": "Briefing the PL",
}
"""Provisioning steps of a queued project (the web's ``SANDBOX_STAGE_LABELS`` plus the brief)."""

LAUNCH_STEP_LABELS: Final = {
    "node": "Waiting for a node",
    "image": "Pulling the image",
    "clone": "Cloning the repo",
    "containers": "Starting containers",
    "host": "Starting the host",
}
"""What a starting PL sandbox waits on, as the provider reports it (``LAUNCH_STEPS``)."""

START_LABELS: Final = {
    **SANDBOX_STAGE_LABELS,
    **LAUNCH_STEP_LABELS,
    "connect": "Waiting for the agent to connect",
    "creating": "Creating the PL session",
    "lead": "Waiting for Atlas to start its PL",
    "server": "Starts within a minute",
}
"""Phases of a queued project's start (``ProjectStartView.phase`` -> label)."""


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


def _subtree_costs(
    members: Sequence[Conversation], store: ConversationStore
) -> dict[str, float | None]:
    """:func:`subtree_cost` of each member; the uncached ones read side by side."""
    now = time.monotonic()
    stale = [
        m for m in members if (hit := _cost_cache.get(m.id)) is None or now - hit[0] >= COST_TTL_S
    ]
    if len(stale) > 1:
        with ThreadPoolExecutor(max_workers=min(_COST_READERS, len(stale))) as pool:
            # Each read runs in this thread's context (the workspace scope, for one).
            reads = [
                pool.submit(contextvars.copy_context().run, subtree_cost, m, store) for m in stale
            ]
            for read in reads:
                read.result()
    return {m.id: subtree_cost(m, store) for m in members}


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


@dataclass(frozen=True)
class SandboxProgress:
    """
    A PL sandbox's launch as the start view shows it.

    :param stage: The launch stage (see :func:`sandbox_stage`), ``None`` once settled.
    :param step: What the starting sandbox waits on (:data:`LAUNCH_STEP_LABELS`), if known.
    :param since: Epoch seconds the stage or step began, if known.
    :param tries: Sandbox launches of this start (``2`` on the server's retry).
    :param wake: The launch brings back the PL's own sandbox (a wake), not a new one.
    :param retry: The launch watch will retry it on its own once it fails.
    """

    stage: str | None = None
    step: str | None = None
    since: int | None = None
    tries: int = 1
    wake: bool = False
    retry: bool = False

    @property
    def new_try(self) -> int | None:
        """:returns: The try of a relaunch on a new sandbox (``2``…), else ``None``."""
        return self.tries if self.tries > 1 and not self.wake else None


def sandbox_progress(conv: Conversation | None) -> SandboxProgress:
    """:returns: The launch progress of *conv*'s managed sandbox (empty when settled)."""
    from omnigent.server.factory.new_sandbox import launch_tries, server_retried
    from omnigent.server.routes._sessions.common import _session_launch_progress_cache

    if conv is None:
        return SandboxProgress()
    stage = sandbox_stage(conv.id)
    tries = launch_tries(conv)
    retry = conv.labels.get(LABEL_STATE) == STATE_ACTIVE and not server_retried(conv)
    progress = _session_launch_progress_cache.get(conv.id)
    if stage is None or progress is None or progress.stage != stage:
        return SandboxProgress(stage=stage, tries=tries, retry=retry)
    step = progress.step if progress.step in LAUNCH_STEP_LABELS else None
    return SandboxProgress(stage, step, progress.since, tries, progress.wake, retry)


def _times(tries: int) -> str:
    return "" if tries < 2 else " twice" if tries == 2 else f" {tries} times"


def sandbox_failed_reason(progress: SandboxProgress) -> str | None:
    """
    :returns: The failed overlay's reason for a PL sandbox that did not come up,
        e.g. ``"PL sandbox failed · retrying on a new sandbox"`` or ``"PL sandbox
        did not wake twice"``; ``None`` while it is not failed.
    """
    if progress.stage != "failed":
        return None
    if progress.wake:
        if progress.retry:
            return "PL sandbox did not wake · waking it again"
        return f"PL sandbox did not wake{_times(progress.tries)}"
    if progress.retry:
        return "PL sandbox failed · retrying on a new sandbox"
    return f"PL sandbox failed{_times(progress.tries)}"


def _spawning(factory_id: str, slug: str) -> bool:
    """:returns: Whether this process is creating *slug*'s PL session now."""
    from omnigent.server.factory import spawn

    return spawn.in_flight(factory_id, slug)


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
    tasks: Sequence[FactoryTask] = (),
) -> StageResult:
    """
    :param tasks: The factory's open tasks (see :data:`.stage.OPEN_TASK_STATES`).
    :returns: The derived stage of *project* (see :func:`.stage.derive_stage`).
    """
    own = [p for p in prs if p.project_slug == project.slug]
    progress = sandbox_progress(pl)
    brief_delivered = pl is not None and pl.id not in pending_briefs and progress.stage is None
    return derive_stage(
        project,
        own,
        pl,
        open_decisions,
        brief_delivered=brief_delivered,
        sandbox_failed=progress.stage == "failed",
        sandbox_reason=sandbox_failed_reason(progress),
        waiting_on=waiting_on,
        tasks=tasks,
    )


def start_view(
    project: FactoryProject,
    result: StageResult,
    pl: Conversation | None,
    *,
    sandbox: str | None = None,
    briefing: bool = False,
    runner_online: bool | None = None,
    spawning: bool = False,
    lead_spawns: bool = False,
    pods: tuple[int, int | None] = (0, None),
    ahead: int = 0,
    ran_before: bool = False,
    progress: SandboxProgress | None = None,
) -> dict[str, Any] | None:
    """
    Why a plainly queued project has no working PL yet (``ProjectView.start``).

    ``starting`` while its PL session comes up (sandbox, runner, brief) or is
    being created; ``queued`` while nothing starts it yet, with the reason.

    :param sandbox: The PL's managed-sandbox launch stage (``None`` once ready).
    :param progress: What its starting sandbox waits on (a node, the image, the
        clone, ...) and since when, and its launch try; the phase names the step.
    :param briefing: The PL's brief is queued and not delivered yet.
    :param runner_online: Whether the PL's runner is attached (``None``: unknown).
    :param ran_before: The PL already worked (see :func:`pl_ran_before`): waking
        or relaunching it is a restart, timed from when the project went back
        to queued, not from the PL's creation days ago.
    :param spawning: The server is creating the PL session right now.
    :param lead_spawns: Atlas, not the server, spawns this project's PL.
    :param pods: ``(live members, the factory's pod cap)``.
    :param ahead: Earlier projects also waiting for a free pod.
    :returns: ``ProjectStartView`` JSON, or ``None`` when the project is not
        queued, or an overlay (waiting, failed, blocked, ...) says why.
    """
    if result.stage != "queued" or result.overlay is not None or project.status != "active":
        return None
    if pl is not None:
        phase_since: int | None = None
        if sandbox in SANDBOX_STAGE_LABELS:
            phase: str | None = sandbox
            if progress is not None and progress.stage == sandbox:
                phase = progress.step or sandbox
                phase_since = progress.since
        elif runner_online is False:
            phase = "connect"
        else:
            phase = "briefing" if briefing else None
        view: dict[str, Any] = {
            "state": "starting",
            "phase": phase,
            "label": START_LABELS[phase] if phase else "Starting",
            # A woken PL went back to queued long after it was made (reconcile
            # stores that stage within a minute).
            "since": max(int(pl.created_at), project.stage_changed_at or 0),
            "restart": ran_before,
        }
        if phase_since is not None:
            view["phase_since"] = phase_since
        if progress is not None and progress.new_try and sandbox is not None:
            view["attempt"] = progress.new_try
        return view
    since = project.created_at or None
    if spawning:
        return {
            "state": "starting",
            "phase": "creating",
            "label": START_LABELS["creating"],
            "since": since,
        }
    owner = spawn_owner(project)
    if lead_spawns or owner != SPAWN_SERVER:
        return {"state": "queued", "phase": "lead", "label": START_LABELS["lead"], "since": since}
    live, cap = pods
    if cap is not None and live >= cap:
        return {
            "state": "queued",
            "phase": "pod_cap",
            "label": f"All {cap} pods in use",
            "since": since,
            "ahead": ahead,
            "pods_in_use": live,
            "pod_cap": cap,
        }
    return {"state": "queued", "phase": "server", "label": START_LABELS["server"], "since": since}


_ACTIVE_TASK_ORDER: Final = {"running": 0, "review": 1, "blocked": 2}


def active_tasks(tasks: Iterable[FactoryTask]) -> dict[str, dict[str, Any]]:
    """
    Each project's task in hand (``ProjectView.active_task``): running first,
    then in review, then blocked; the lowest number within a state.

    :returns: Project slug -> ``{ref, title, state}``.
    """
    out: dict[str, dict[str, Any]] = {}
    ranked = sorted(
        (t for t in tasks if t.state in _ACTIVE_TASK_ORDER),
        key=lambda t: (_ACTIVE_TASK_ORDER[t.state], t.number),
    )
    for task in ranked:
        out.setdefault(
            task.project_slug,
            {"ref": f"T-{task.number}", "title": task.title, "state": task.state},
        )
    return out


def delivered_brief_targets(mail: Sequence[FactoryEvent]) -> set[str]:
    """:returns: Sessions that received a ``brief`` (in the mail window)."""
    return {
        e.to_id
        for e in mail
        if e.kind == "message.delivered" and e.to_id and (e.data or {}).get("kind") == "brief"
    }


def pl_ran_before(project: FactoryProject, pl: Conversation, briefed: set[str]) -> bool:
    """
    Whether *pl* already worked on *project*, so starting it now is a restart.

    It did when it received its brief, claimed a stage since it was made (every
    PL claims one at kickoff), or moved the stored stage past queued since it
    was made (before reconcile stores the wake's queued). ``member.spawned``
    does not count: the server logs it before a fresh PL's sandbox comes up.
    Nor does a queued stage stored after the PL was made: a replacement PL's
    project is stored queued too.

    :param briefed: Sessions that received a brief (:func:`delivered_brief_targets`).
    """
    made = int(pl.created_at)
    moved = project.stage != STAGE_QUEUED and (project.stage_changed_at or 0) > made
    return pl.id in briefed or (project.agent_stage_at or 0) > made or moved


def load_open_tasks(
    deps: FactoryDeps, factory_id: str, slug: str | None = None
) -> list[FactoryTask]:
    """
    The open tasks of a factory (or one project) for :func:`.stage.derive_stage` (never raises).

    :returns: The tasks, or ``[]`` when the work graph cannot be read (the
        stage then reads as the PRs alone say).
    """
    try:
        return deps.factory_store.list_tasks(
            factory_id, project_slug=slug, states=OPEN_TASK_STATES
        )
    except Exception:  # noqa: BLE001 - the stage renders from the PRs alone
        _logger.warning("factory: no open tasks for %s", factory_id, exc_info=True)
        return []


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
    :param notified: Subject -> Slack DM receipts (``NotifiedView`` rows), oldest first.
    :param handles: Handle -> session id of every member and sub-agent shown.
    :param proposals: The pending, co-signing and on-trial proposals, which
        the "Needs you" list sorts (``None`` when not loaded: readers query).
    """

    factory_id: str
    body: dict[str, Any]
    top_level_of: dict[str, str] = field(default_factory=dict)
    project_pl: dict[str, str | None] = field(default_factory=dict)
    demo_owner: dict[str, str] = field(default_factory=dict)
    titles: dict[str, str] = field(default_factory=dict)
    built_at: float = 0.0
    incident_scopes: list[tuple[str | None, str]] = field(default_factory=list)
    notified: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    handles: dict[str, str] = field(default_factory=dict)
    proposals: list[FactoryProposal] | None = None


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
    definition_proposals_pending: int = 0
    on_trial: int = 0
    def_version: int | None = None
    product: dict[str, Any] | None = None
    issues: list[Any] = field(default_factory=list)
    now: dict[str, dict[str, Any]] = field(default_factory=dict)
    pod_cap: int | None = None
    lead_spawns: bool = False
    notified: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    deadlines: dict[str, tuple[int, str]] = field(default_factory=dict)
    models: dict[str, str | None] = field(default_factory=dict)
    def_doc: dict[str, Any] = field(default_factory=dict)
    proposals: list[FactoryProposal] = field(default_factory=list)
    merge_targets: auto_merge.Targets = field(default_factory=auto_merge.Targets)


def notified_by_subject(rows: Iterable[FactoryEvent]) -> dict[str, list[dict[str, Any]]]:
    """:returns: Subject -> ``NotifiedView`` rows of ``notify.slack`` events, oldest first."""
    out: dict[str, list[dict[str, Any]]] = {}
    for event in rows:
        data = event.data or {}
        user = data.get("to_user")
        if not event.subject or not isinstance(user, str):
            continue
        out.setdefault(event.subject, []).append(
            {
                "user_id": user,
                "at": event.ts // 1000,
                "stage": str(data.get("stage") or ""),
                "ok": data.get("status") == "sent",
                "error": data.get("error") if isinstance(data.get("error"), str) else None,
            }
        )
    return out


def _notified(fstore: Any, factory_id: str, now_ms: int) -> dict[str, list[dict[str, Any]]]:
    """Slack DM receipts by subject, oldest first, from the newest ledger rows."""
    rows = fstore.find_events(
        factory_id,
        "notify.slack",
        now_ms - NOTIFIED_WINDOW_S * 1000,
        limit=NOTIFIED_ROWS,
        newest_first=True,
    )
    return notified_by_subject(reversed(rows))


def _executor(conv: Conversation, deps: FactoryDeps, memo: dict[tuple[Any, ...], Any]) -> Any:
    """:returns: The executor of *conv*'s spec, loaded once per agent and sub-agent."""
    key = (conv.agent_id, conv.sub_agent_name)
    if key not in memo:
        memo[key] = getattr(load_session_spec(conv, deps), "executor", None)
    return memo[key]


def _harness(
    conv: Conversation, deps: FactoryDeps, memo: dict[tuple[Any, ...], Any]
) -> str | None:
    if conv.harness_override:
        return conv.harness_override
    config = getattr(_executor(conv, deps, memo), "config", None)
    harness = config.get("harness") if isinstance(config, dict) else None
    return harness if isinstance(harness, str) else None


def _model(conv: Conversation, deps: FactoryDeps, memo: dict[tuple[Any, ...], Any]) -> str | None:
    """:returns: *conv*'s model: its override, else its bundle's (``None`` when unknown)."""
    if conv.model_override:
        return conv.model_override
    model = getattr(_executor(conv, deps, memo), "model", None)
    return model if isinstance(model, str) else None


def _def_doc(deps: FactoryDeps, factory_id: str) -> dict[str, Any]:
    try:
        resolved = definition.definition_for(deps, factory_id)
    except Exception:  # noqa: BLE001 - team specs render from the defaults then
        _logger.warning("factory snapshot: no definition for %s", factory_id, exc_info=True)
        return {}
    return resolved.doc if resolved is not None else {}


def _load(deps: FactoryDeps, factory_id: str, now: float, *, merger: bool = False) -> _Loaded:
    """
    Read what the snapshot shows (synchronous).

    :param merger: This server runs the merger: read its targets (with where
        each project sits, for the subtree scopes); else a person merges.
    """
    store = deps.conversation_store
    fstore = deps.factory_store
    everyone = sorted(
        factory_members(factory_id, store), key=lambda m: (m.id != factory_id, m.created_at)
    )
    members = [m for m in everyone if not is_session_closed(m.labels, m.title)]
    closed = [m for m in everyone if is_session_closed(m.labels, m.title)]
    # The newest 200 of each (re-dispatches must not fall off the page), shown oldest first.
    children = store.list_sub_agents_by_parent([m.id for m in members], 200)
    now_ms = int(now * 1000)
    open_decisions = fstore.list_decisions(factory_id=factory_id, status="open", limit=500)
    # One read of the open proposals: deadlines, Steer's counts and Needs you share it.
    proposals = fstore.list_proposals(
        factory_id, status=["pending", "awaiting_cosign", "trial"], limit=1000
    )
    try:
        deadlines = slack_rules.deadlines(
            deps,
            factory_id,
            open_decisions,
            store.get_conversation(factory_id),
            proposals={f"C-{p.number}": p for p in proposals},
        )
    except Exception:  # noqa: BLE001 - a deadline chip is never worth a broken snapshot
        _logger.warning("factory: could not compute decision deadlines", exc_info=True)
        deadlines = {}
    pending = [p for p in proposals if p.status in ("pending", "awaiting_cosign")]
    memo: dict[tuple[Any, ...], Any] = {}
    shown = [*members, *(k for kids in children.values() for k in kids)]
    harness = {c.id: _harness(c, deps, memo) for c in shown}
    return _Loaded(
        members=members,
        closed_members=closed,
        children=children,
        projects=fstore.list_projects(factory_id),
        prs=fstore.list_prs(factory_id),
        open_decisions=open_decisions,
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
        costs=_subtree_costs(members, store),
        proposals_pending=len(pending),
        # Steer's count: changes to how the factory works (plans and bets wait in Needs you).
        definition_proposals_pending=sum(1 for p in pending if p.kind != "projects"),
        on_trial=sum(1 for p in proposals if p.status == "trial"),
        proposals=proposals,
        def_version=definition.pinned_version(deps, factory_id),
        product=product_profile.product(deps, factory_id),
        issues=fstore.list_issues(factory_id, class_="incident", statuses=["open", "acked"]),
        notified=_notified(fstore, factory_id, now_ms),
        deadlines=deadlines,
        models={c.id: _model(c, deps, memo) for c in shown},
        def_doc=_def_doc(deps, factory_id),
        merge_targets=auto_merge.factory_targets(deps, factory_id)
        if merger
        else auto_merge.Targets(),
        **_spawn_limits(deps, factory_id, members),
    )


def _spawn_limits(
    deps: FactoryDeps, factory_id: str, members: Sequence[Conversation]
) -> dict[str, Any]:
    """The factory's pod cap and whether Atlas, not the server, spawns its PLs."""
    from omnigent.server.factory import spawn

    root = next((m for m in members if m.id == factory_id), None)
    if root is None:
        return {}
    mode = definition.value(deps, factory_id, "engine.spawn") or root.labels.get(
        spawn.LABEL_ENGINE_SPAWN
    )
    try:
        cap = spawn.pod_cap(deps, root)
    except Exception:  # noqa: BLE001 - the snapshot renders without the cap
        cap = None
    return {"pod_cap": cap, "lead_spawns": (mode or "").strip() == SPAWN_LEAD}


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
        "vendor": team.vendor_of(loaded.harness.get(conv.id)),
        "model": loaded.models.get(conv.id),
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


def _pr_view(
    pr: FactoryPr,
    demos: Sequence[FactoryDemo],
    titles: Mapping[str, str],
    auto: bool = False,
) -> dict:
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
        "ready": ready_for_human(pr),
        "ready_since": pr.ready_at,
        "auto_merge": auto,
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
    *,
    notified: Sequence[Mapping[str, Any]] = (),
    deadline: tuple[int, str] | None = None,
) -> dict[str, Any]:
    """
    :param notified: Its Slack DM receipts (``NotifiedView`` rows).
    :param deadline: ``(at, effect)`` when it defaults by itself.
    :returns: The ``DecisionView`` JSON of one decision.
    """
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
        "nobody_to_tell": decision.nobody_to_tell,
        "created_at": decision.created_at,
        "updated_at": decision.updated_at,
        "assignee": decision.assignee,
        "claimed_by": decision.claimed_by,
        "claimed_at": decision.claimed_at,
        "thread": decision.thread,
        "subject": decision.subject,
        "notified": [dict(n) for n in notified],
        "deadline_at": deadline[0] if deadline is not None else None,
        "deadline_effect": deadline[1] if deadline is not None else None,
    }


def _health(app_state: Any, drifted: int, demo_bytes: int) -> dict[str, Any]:
    from omnigent.server.factory import guard_repos
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
        "config": guard_repos.config_problems(),
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
    start: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    pr_ids = [p.id for p in loaded.prs if p.project_slug == project.slug]
    open_ids = [
        d.id
        for d in loaded.open_decisions
        if d.project_slug == project.slug or (pl is not None and d.asked_by_session_id == pl.id)
    ]
    team = [_member_ref(views[c.id]) for c in loaded.children.get(pl.id, [])] if pl else []
    provisioning: dict[str, Any] | None = None
    if result.stage == "queued" and pl is not None:
        progress = sandbox_progress(pl)
        stage = progress.stage or ("briefing" if pl.id in pending else None)
        if stage in SANDBOX_STAGE_LABELS:
            provisioning = {"stage": stage, "label": SANDBOX_STAGE_LABELS[stage]}
            if progress.step is not None:
                provisioning.update(
                    step=progress.step,
                    label=LAUNCH_STEP_LABELS[progress.step],
                    since=progress.since,
                )
            if progress.new_try:
                provisioning["attempt"] = progress.new_try
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
        "stage_note": result.note,
        "start": _lenient(schemas.ProjectStartView, start) if start else None,
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
        "team_spec": _team_spec(project, pl, loaded),
        **_lifecycle(project),
    }


def _health_views(
    project_views: list[dict[str, Any]],
    rows: Mapping[str, FactoryProject],
    pr_views: Sequence[Mapping[str, Any]],
    member_views: Mapping[str, Mapping[str, Any]],
    project_pl: Mapping[str, str | None],
    now: float,
) -> None:
    """Each live project's lead update and its health computed from facts (in place)."""
    for view in project_views:
        slug = view["slug"]
        row = rows.get(slug)
        pl = project_pl.get(slug)
        charter = normalize_charter(row.charter) if row is not None else {}
        budget = (charter.get("budgets") or {}).get("project_usd")
        try:
            view["update"], view["health"] = lead_update.views(
                view,
                row.lead_update if row is not None else None,
                [p for p in pr_views if p.get("project_slug") == slug],
                [m for m in member_views.values() if m.get("project") == slug],
                budget_usd=budget,
                now=now,
                handle=(member_views.get(pl) or {}).get("handle") if pl else None,
            )
        except Exception:  # noqa: BLE001 - a card renders without its health
            _logger.warning("factory snapshot: no health for %s", slug, exc_info=True)


def _waiting_views(
    deps: FactoryDeps,
    factory_id: str,
    project_views: list[dict[str, Any]],
    rows: Mapping[str, FactoryProject],
    loaded: _Loaded,
    starts: Mapping[str, Mapping[str, Any] | None],
    project_pl: Mapping[str, str | None],
    now: float,
    open_tasks: Sequence[FactoryTask],
    targets: auto_merge.TargetsLike = frozenset(),
) -> None:
    """
    Each live project's ``waiting_on``: why it is not moving (in place; never raises).

    :param open_tasks: The factory's open tasks, as the snapshot read them.
    :param targets: The auto-merge targets the server merges into now.
    """
    try:
        bad = deps.factory_store.list_loops(factory_id, states=sorted(waits.BAD_LOOP_STATES))
        facts = waits.Facts(
            projects=rows,
            prs=loaded.prs,
            decisions=loaded.open_decisions,
            proposals=loaded.proposals,
            tasks=open_tasks,
            loops=bad,
            leads=project_pl,
            now=now,
            targets=targets,
        )
    except Exception:  # noqa: BLE001 - the cards render without it
        _logger.warning("factory snapshot: no waits for %s", factory_id, exc_info=True)
        return
    for view in project_views:
        row = rows.get(view["slug"])
        if row is None or not row.id:
            continue
        try:
            found = waits.project_waits(row, facts, start=starts.get(row.slug))
        except Exception:  # noqa: BLE001 - one card renders without it
            _logger.warning("factory snapshot: no waits for %s", row.slug, exc_info=True)
            continue
        view["waiting_on"] = [w.to_json() for w in found]


def _team_spec(
    project: FactoryProject, pl: Conversation | None, loaded: _Loaded
) -> dict[str, Any] | None:
    """The project's team spec next to its live team (``None`` for a virtual project)."""
    if not project.id:
        return None
    try:
        return team.snapshot_view(
            loaded.def_doc,
            project,
            loaded.children.get(pl.id, []) if pl is not None else [],
            def_version=loaded.def_version,
        )
    except Exception:  # noqa: BLE001 - a card renders without its team spec
        _logger.warning("factory snapshot: no team spec for %s", project.slug, exc_info=True)
        return None


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
        "depends_why": charter.get("depends_why") or None,
        "human_owner": charter.get("human_owner"),
        "def_version": project.def_version,
        "why": _lenient(schemas.WhyView, charter.get("why")),
        "requested_by": _lenient(schemas.RequestedByView, charter.get("requested_by")),
        "initiative": _lenient(schemas.InitiativeRefView, charter.get("initiative")),
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


def _identify(
    deps: FactoryDeps,
    factory_id: str,
    loaded: _Loaded,
    views: dict[str, dict[str, Any]],
    now: float,
) -> dict[str, str]:
    """
    Stamp each member view with its handle and stuck fields (synchronous).

    Handles are stored on first sight; every session that ever held one in the
    factory (closed, archived, or past the shown page) keeps it from being reused.

    :returns: Session id -> handle of the shown members and sub-agents.
    """
    shown = [*loaded.members, *(k for kids in loaded.children.values() for k in kids)]
    sessions = [(c, views[c.id]["role"]) for c in shown if c.id in views]
    sessions += [(c, c.labels.get(LABEL_ROLE)) for c in loaded.closed_members]
    handles = identity.ensure_handles(deps.conversation_store, factory_id, sessions)
    blocked = stuck.stuck_issues(loaded.issues)
    for sid, view in views.items():
        view["handle"] = handles.get(sid)
        view.update(stuck.stuck_fields(blocked.get(norm_id(sid)), now))
    return {sid: h for sid, h in handles.items() if sid in views}


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
    loaded = await asyncio.to_thread(
        functools.partial(_load, merger=auto_merge.merger_on(app_state)), deps, factory_id, now
    )
    loaded.now = activity_now.now_states(factory_id)
    roster = await build_roster(
        factory_id, loaded.members, deps, app_state, children=loaded.children
    )
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
    handles = await asyncio.to_thread(_identify, deps, factory_id, loaded, views, now)

    projects = [
        *loaded.projects,
        *_virtual_projects(factory_id, loaded.projects, loaded.members),
    ]
    pending = pending_brief_targets(loaded.mail)
    briefed = delivered_brief_targets(loaded.mail)
    paused = _paused_by(loaded.controls)
    # Work graph: each project's tasks summary, each PR's task, the open tasks
    # the stage reads (one read; never raises).
    task_summaries, task_refs, open_tasks = await asyncio.to_thread(
        tasks.snapshot_fields, deps, factory_id
    )
    project_views: list[dict[str, Any]] = []
    project_pl: dict[str, str | None] = {}
    by_slug = {p.slug: p for p in projects}
    from omnigent.server.factory import spawn

    # Pods as the spawner counts them (spawn.live_members).
    live_pods = sum(1 for m in loaded.members if spawn.holds_pod(m))
    waiting_for_pod = 0  # projects (oldest first, as the spawner takes them) without a pod
    starts: dict[str, dict[str, Any] | None] = {}
    for project in projects:
        pl = pick_pl(project, project.slug, loaded.members)
        waiting = (
            unmet_dependencies(project, by_slug, loaded.prs, open_tasks)
            if pl is None and project.status == "active"
            else []
        )
        result = project_stage(
            project,
            loaded.prs,
            pl,
            loaded.open_decisions,
            pending,
            waiting_on=waiting,
            tasks=open_tasks,
        )
        start = start_view(
            project,
            result,
            pl,
            sandbox=sandbox_stage(pl.id) if pl is not None else None,
            progress=sandbox_progress(pl),
            briefing=pl is not None and pl.id in pending,
            runner_online=rows[pl.id].get("runner_online") if pl and pl.id in rows else None,
            spawning=_spawning(factory_id, project.slug),
            lead_spawns=loaded.lead_spawns,
            pods=(live_pods, loaded.pod_cap),
            ahead=waiting_for_pod,
            ran_before=pl is not None and pl_ran_before(project, pl, briefed),
        )
        if start is not None and start["phase"] == "pod_cap":
            waiting_for_pod += 1
        starts[project.slug] = start
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
                start=start,
            )
        )

    # Closed projects (cancelled, merged into another, done) fold below the live ones.
    project_views.sort(key=lambda v: v.get("status") not in (None, "active"))
    demos_by_pr: dict[tuple[str, int], list[FactoryDemo]] = {}
    for demo in loaded.demos:  # newest first
        demos_by_pr.setdefault((demo.repo, demo.pr_number), []).append(demo)
    # Only while this server runs the merger (else none were read): a person merges as before.
    targets = loaded.merge_targets
    projects_by_slug = {p.slug: p for p in loaded.projects}
    pr_views = [
        _pr_view(
            pr,
            demos_by_pr.get((pr.repo, pr.number), []),
            titles,
            auto_merge.merges_itself(pr, targets, projects_by_slug.get(pr.project_slug or "")),
        )
        for pr in loaded.prs
    ]
    loop_summaries, loop_chips = await asyncio.to_thread(
        loops.snapshot_fields, deps, factory_id, loaded.prs
    )
    active = active_tasks(open_tasks)
    for view in project_views:
        view["tasks_summary"] = task_summaries.get(view["slug"])
        view["loops_summary"] = loop_summaries.get(view["slug"])
        view["active_task"] = active.get(view["slug"])
    _health_views(project_views, by_slug, pr_views, views, project_pl, now)
    await asyncio.to_thread(
        _waiting_views,
        deps,
        factory_id,
        project_views,
        by_slug,
        loaded,
        starts,
        project_pl,
        now,
        open_tasks,
        targets,
    )
    for pr, view in zip(loaded.prs, pr_views, strict=True):
        view["task_id"], view["task_ref"] = pr.task_id, task_refs.get(pr.task_id or "")
        view["loops"] = loop_chips.get(norm_id(pr.id))

    delivered_to = {
        (e.data or {}).get("mail_id", ""): e.to_id
        for e in loaded.mail
        if e.kind == "message.delivered" and e.to_id
    }
    decisions = [
        view
        for d in [*sorted(loaded.open_decisions, key=lambda d: d.number), *loaded.closed_decisions]
        if (
            view := _lenient(
                schemas.DecisionView,
                decision_view(
                    d,
                    titles,
                    roles,
                    delivered_to,
                    notified=loaded.notified.get(f"decision:{d.ref}", ()),
                    deadline=loaded.deadlines.get(d.id),
                ),
            )
        )
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
            "product": loaded.product,
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
            "definition_proposals_pending": loaded.definition_proposals_pending,
            "issues_open": counts_open(loaded.issues, frozenset()),
            "on_trial": loaded.on_trial,
        },
        "def_version": loaded.def_version,
        "engine_mode": await asyncio.to_thread(loops.engine_mode, deps, factory_id),
    }
    rate = detectors.spend_rate(factory_id)
    if rate is None:
        # Right after a restart: the agents' own rates, else a few minutes of samples
        # (a turn's cost lands at once, so a shorter span would inflate it).
        rate = factory_usd_per_h(factory_id)
    if rate is None:
        rate = detectors.spend_rate(factory_id, early=True)
    body["totals"] = _totals(body, cost, rate)
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
        notified=loaded.notified,
        handles={h: sid for sid, h in handles.items()},
        proposals=loaded.proposals,
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
_generation: dict[str, int] = {}
"""Factory -> how many times it was forgotten (a build started before a forget is not kept)."""
_refreshes: dict[tuple[int, str], asyncio.Task[None]] = {}
_epoch: list[int] = [0]
"""Bumped by :func:`reset_cache`: no build started before it is kept."""


async def get_snapshot(app_state: Any, root: Conversation) -> RawSnapshot:
    """
    The unfiltered snapshot of *root*'s factory, rebuilt at most every 2 s.

    A snapshot older than that but younger than :data:`SNAPSHOT_STALE_S` is
    answered at once while one rebuild runs in the background; after
    :func:`forget` (or with none cached) the read waits for a fresh build.
    """
    key = bare_id(root.id)
    cached = _snapshots.get(key)
    if cached is not None:
        age = time.monotonic() - cached.built_at
        if age < SNAPSHOT_TTL_S:
            return cached
        if age < SNAPSHOT_STALE_S:
            _refresh_in_background(app_state, root, key)
            return cached
    lock = _locks.setdefault((id(asyncio.get_running_loop()), key), asyncio.Lock())
    async with lock:
        cached = _snapshots.get(key)
        if cached is not None and time.monotonic() - cached.built_at < SNAPSHOT_TTL_S:
            return cached
        return await _build_and_keep(app_state, root, key)


def _generation_of(key: str) -> tuple[int, int]:
    return _epoch[0], _generation.get(key, 0)


async def _build_and_keep(app_state: Any, root: Conversation, key: str) -> RawSnapshot:
    generation = _generation_of(key)
    with agent_rows_memo():
        raw = await build_snapshot(app_state, root)
    if _generation_of(key) == generation:
        _snapshots[key] = raw
    return raw


def _refresh_in_background(app_state: Any, root: Conversation, key: str) -> None:
    """Start one rebuild of *key* unless one runs already (on this loop)."""
    loop = asyncio.get_running_loop()
    task_key = (id(loop), key)
    running = _refreshes.get(task_key)
    if running is not None and not running.done():
        return

    async def refresh() -> None:
        lock = _locks.setdefault(task_key, asyncio.Lock())
        try:
            async with lock:
                cached = _snapshots.get(key)
                if cached is not None and time.monotonic() - cached.built_at < SNAPSHOT_TTL_S:
                    return
                await _build_and_keep(app_state, root, key)
        except Exception:  # noqa: BLE001 - the next read builds it in the open
            _logger.warning("factory: background snapshot of %s failed", key, exc_info=True)
        finally:
            if _refreshes.get(task_key) is task:
                _refreshes.pop(task_key, None)

    task = loop.create_task(refresh(), name=f"factory-snapshot-{key}")
    _refreshes[task_key] = task


def forget(factory_id: str) -> None:
    """Drop one factory's cached snapshot (a write the next read must see)."""
    key = bare_id(factory_id)
    _generation[key] = _generation.get(key, 0) + 1
    _snapshots.pop(key, None)
    with _views_lock:
        _views.pop(key, None)


def reset_cache() -> None:
    """Forget cached snapshots, viewer permissions, costs and handles (tests)."""
    _epoch[0] += 1
    _snapshots.clear()
    _refreshes.clear()
    _cost_cache.clear()
    _locks.clear()
    _viewers.clear()
    with _views_lock:
        _views.clear()
    identity.reset_cache()


# ── One viewer's cut, kept with the snapshot it was cut from ───

_views: dict[str, dict[frozenset[str], tuple[RawSnapshot, dict[str, Any], str]]] = {}
_VIEWS_PER_FACTORY: Final = 32
_views_lock = threading.Lock()


def filtered_view(raw: RawSnapshot, readable: Iterable[str]) -> tuple[dict[str, Any], str]:
    """
    :func:`filter_snapshot` of *raw* for *readable*, with a digest of it,
    computed once per snapshot and set of readable sessions.

    :returns: ``(body, digest)``; the body is shared: callers copy before changing it.
    """
    who = frozenset(readable)
    with _views_lock:
        hit = _views.get(raw.factory_id, {}).get(who)
    if hit is not None and hit[0] is raw:
        return hit[1], hit[2]
    body = filter_snapshot(raw, set(who))
    digest = _stable_digest(body)
    with _views_lock:
        per = _views.setdefault(raw.factory_id, {})
        if len(per) >= _VIEWS_PER_FACTORY or any(r is not raw for r, _, _ in per.values()):
            per.clear()
        per[who] = (raw, body, digest)
    return body, digest


def readable_set(
    user_id: str | None,
    session_ids: Iterable[str],
    deps: FactoryDeps,
) -> set[str]:
    """
    The sessions *user_id* may read, by the same check the session routes run.

    Synchronous (run it in a worker thread).
    """
    ids = set(session_ids)
    perms = deps.permission_store
    if perms is None:
        return ids
    if user_id is None or not ids:
        return set()
    # check_session_access for every id, in a few batched reads: admin bypass,
    # a sub-agent reads as its top-level session, then a direct or public grant.
    if perms.is_admin(user_id):
        return ids
    store = deps.conversation_store
    top_of: dict[str, str] = {}
    pending = {sid: sid for sid in ids}
    for _ in range(_MAX_PARENT_HOPS):
        if not pending:
            break
        rows = store.get_conversations(list(set(pending.values())))
        nxt: dict[str, str] = {}
        for sid, at in pending.items():
            conv = rows.get(at)
            if conv is None:
                continue
            if conv.parent_conversation_id is None:
                top_of[sid] = at
            else:
                nxt[sid] = conv.parent_conversation_id
        pending = nxt
    if not top_of:
        return set()
    grants = perms.list_for_sessions(sorted(set(top_of.values())))
    allowed = {
        cid
        for cid, rows in grants.items()
        if any(
            g.user_id in (user_id, RESERVED_USER_PUBLIC) and g.level >= LEVEL_READ for g in rows
        )
    }
    return {sid for sid, top in top_of.items() if top in allowed}


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


_REF_WORD_RE: Final = re.compile(r"[A-Za-z0-9][\w#-]*")


def _visible_wait(
    wait: dict[str, Any], open_ids: set[str], dropped: set[str], hidden: set[str]
) -> dict[str, Any]:
    """
    A ``waiting_on`` item as a viewer may read it: one that quotes a decision they
    cannot open, or names a project they cannot see, keeps only its ref.
    """
    label = str(wait.get("label") or "")
    words = set(_REF_WORD_RE.findall(label))
    decision_id = wait.get("decision_id")
    hides = bool(decision_id and decision_id not in open_ids) or bool(words & (dropped | hidden))
    if not hides:
        return wait
    ref = str(wait.get("ref") or "")
    head = f"{wait['task']} held: " if wait.get("kind") == "hold" and wait.get("task") else ""
    return {
        **wait,
        "label": f"{head}waiting on {ref}",
        "decision_id": decision_id if decision_id in open_ids else None,
    }


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
    dropped = {d["ref"] for d in raw.body["decisions"] if d["id"] not in open_ids and d.get("ref")}
    for project in visible_projects:
        project["team"] = [m for m in project["team"] if m["session_id"] in readable]
        project["open_decision_ids"] = [i for i in project["open_decision_ids"] if i in open_ids]
        if project.get("waiting_on"):
            project["waiting_on"] = [
                _visible_wait(w, open_ids, dropped, hidden) for w in project["waiting_on"]
            ]
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
        base["deployed"] = None
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
    return f'W/"{_stable_digest(body)[:32]}"'


def view_etag(digest: str, extras: Mapping[str, Any]) -> str:
    """A weak ETag over a cut's digest (:func:`filtered_view`) and the viewer's own fields."""
    tail = json.dumps(extras, sort_keys=True, separators=(",", ":"), default=str)
    return f'W/"{hashlib.sha256((digest + tail).encode()).hexdigest()[:32]}"'


def _stable_digest(body: Mapping[str, Any]) -> str:
    stable = {k: v for k, v in body.items() if k != "server_time"}
    stable["factory"] = {k: v for k, v in body["factory"].items() if k != "elapsed_s"}
    stable["projects"] = [
        {k: v for k, v in p.items() if k != "elapsed_s"} for p in body["projects"]
    ]
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


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
    "delivered_brief_targets",
    "filter_snapshot",
    "filtered_view",
    "forget",
    "get_snapshot",
    "is_factory_root",
    "list_factory_roots",
    "load_open_tasks",
    "pending_brief_targets",
    "pick_pl",
    "pl_ran_before",
    "pl_title_slug",
    "project_demo_view",
    "project_stage",
    "readable_for",
    "readable_set",
    "reset_cache",
    "snapshot_etag",
    "start_view",
    "subtree_cost",
    "summary_from",
    "view_etag",
    "viewer_readable",
]
