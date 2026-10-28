"""The work graph's server side: ``factory_tasks`` is the source of truth.

A project lead registers its tasks from the charter's Definition of Done
(``sys_factory_task(op="upsert")``); people add and edit tasks in the cockpit.
The server validates the graph (409 ``cycle`` with the path, 422
``unknown_dep``), keeps each task's state, renders the card a worker gets
(:func:`card_text`) and the title it runs under (:func:`title_to_use`), and
answers the ready set, the waves and the critical path (:mod:`.graph`).

States: ``planned`` → ``ready`` (dependencies met) → ``running`` (``start``,
or the binder saw a worker or a draft PR) → ``review`` (its PR is out of
draft) → ``done`` (merged, or the PL says so); ``blocked`` (the worker
reported BLOCKED), ``failed`` and ``cancelled`` close a task without
finishing it. The binder (:mod:`.binder`) keeps the graph true from what the
server observes.

Every change emits ``task.created`` / ``task.state``; the binder stamps each
worker's ``task`` span with its task through the telemetry writer, and those
spans give the observed durations the critical path weighs. Budgets are read
here; enforcing them is the loop engine's.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import re
import time
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from omnigent.entities.factory import FactoryDecision, FactoryPr, FactoryProject, FactoryTask
from omnigent.factory import (
    LABEL_PREFIX,
    LABEL_STATE,
    STATE_PAUSED,
    TASK_KEY_RE,
)
from omnigent.server.factory import events, schemas
from omnigent.server.factory.actor import Actor, task_key_ref
from omnigent.server.factory.charter import (
    DEPENDS_MERGED,
    normalize_charter,
    project_base_branch,
)
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.envelope import quote_spoofed_lines
from omnigent.server.factory.graph import (
    DECISION_DEP_PREFIX,
    OCCUPYING,
    PENDING,
    PROJECT_DEP_PREFIX,
    Slots,
    TaskNode,
    WavePlan,
    deps_met,
    find_cycle,
    layers,
    observed_p50,
    overlap_warnings,
    plan_waves,
    ready_set,
    unknown_deps,
    waiting_on,
    weight_min,
)
from omnigent.server.factory.graph import critical_path as _critical_path
from omnigent.server.factory.telemetry.spans import span_context
from omnigent.stores.factory_store import norm_id

_logger = logging.getLogger(__name__)

MAX_UPSERT: Final = 30
"""Tasks one ``upsert`` may carry."""
DEFAULT_ATTEMPTS: Final = 3
DEFAULT_TASK_USD: Final = 15.0
MAX_ATTEMPTS: Final = 10
HARNESSES: Final = ("ic", "ic_codex")
DEFAULT_HARNESS: Final = "ic"
VENDORS: Final[dict[str, str]] = {"ic": "claude", "ic_codex": "codex"}
CARD_KEYS: Final = ("text", "acceptance", "env_checks", "preview", "demo_focus", "harness")
LABEL_ENGINE_MODE: Final = f"{LABEL_PREFIX}engine_mode"
DEFAULT_ENGINE_MODE: Final = "assist"
LIVE_PREVIEW_STATES: Final = frozenset({"deploying", "starting", "ready", "stale", "degraded"})
REPORT_RESULTS: Final = ("PASS", "FAIL", "BLOCKED")
WORKTREE_ROOT: Final = "/home/omnigent/workspace/wt"
OBSERVED_TTL_S: Final = 60.0
_T_REF: Final = re.compile(r"^T-([1-9][0-9]{0,8})$")
# ``D-7`` or ``decision:d-7``; a bare ``d-7`` stays a task key.
_D_REF: Final = re.compile(r"^(?:(?i:decision:d)|D)-([1-9][0-9]{0,8})$")
_P_REF: Final = re.compile(r"^(?:project:|p:|#)([a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?)$")


# ── Input ──────────────────────────────────────────────────────


class TaskBudgetIn(BaseModel):
    """A task's budget overrides (read by the loop engine)."""

    model_config = ConfigDict(extra="forbid")

    attempts: int | None = Field(None, ge=1, le=MAX_ATTEMPTS)
    usd: float | None = Field(None, ge=0, le=1000)


class TaskFields(BaseModel):
    """The editable fields of a task (``do`` is the card's instruction text)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    title: str | None = Field(None, min_length=1, max_length=256)
    kind: Literal["implement", "test", "docs", "spike", "ops"] | None = None
    text: str | None = Field(None, alias="do", max_length=4000)
    deps: list[str] | None = Field(None, max_length=30)
    scope: list[str] | None = Field(None, max_length=30)
    acceptance: list[str] | None = Field(None, max_length=20)
    env_checks: list[str] | None = Field(None, max_length=20)
    preview: bool | None = None
    demo_focus: str | None = Field(None, max_length=1000)
    harness: Literal["ic", "ic_codex"] | None = None
    estimate_min: int | None = Field(None, ge=1, le=10080)
    priority: int | None = Field(None, ge=0, le=3)


class TaskIn(TaskFields):
    """One task of ``PUT /v1/factory/agent/tasks``."""

    key: str

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        key = value.strip()
        if not TASK_KEY_RE.match(key):
            raise ValueError(
                "a task key is its branch suffix: lowercase letters, digits and '-', "
                "starting with a letter or digit, at most 48 characters"
            )
        return key


class TaskAddIn(TaskIn):
    """``POST /v1/factory/factories/{fid}/tasks``: a person adds a task."""

    project: str
    budget: TaskBudgetIn | None = None


class TaskEditIn(TaskFields):
    """``PATCH /v1/factory/factories/{fid}/tasks/{id}``: a person edits a task."""

    budget: TaskBudgetIn | None = None
    cancel: bool = False


# ── Small helpers ──────────────────────────────────────────────


def title_to_use(key: str, attempt: int) -> str:
    """
    The dispatch title of an attempt.

    :returns: *key* on the first attempt, ``<key>-r<k>`` for attempt ``k + 1``.
    """
    return key if attempt <= 1 else f"{key}-r{attempt - 1}"


def task_subject(task: FactoryTask) -> str:
    """:returns: The event subject of a task, e.g. ``"task:db-version/route"``."""
    try:
        return task_key_ref(task.project_slug, task.key)
    except ValueError:
        return f"task:{task.project_slug}/{task.key}"[:128]


def harness_of(task: FactoryTask) -> str:
    """:returns: The worker the card asks for (``ic`` by default)."""
    harness = (task.card or {}).get("harness")
    return harness if harness in HARNESSES else DEFAULT_HARNESS


def engine_mode(deps: FactoryDeps, factory_id: str) -> str:
    """:returns: The factory's engine mode from its root label (``assist`` by default)."""
    root = deps.conversation_store.get_conversation(factory_id)
    mode = root.labels.get(LABEL_ENGINE_MODE) if root is not None else None
    return mode if mode in ("observe", "assist", "enforce") else DEFAULT_ENGINE_MODE


def role_rules(deps: FactoryDeps, factory_id: str, role: str, project: str | None) -> list[str]:
    """
    The factory definition's rules for *role* in *project*, appended to cards.

    Empty until versioned factory definitions carry rules.
    """
    del deps, factory_id, role, project
    return []


def node_of(task: FactoryTask) -> TaskNode:
    """:returns: The graph node of a task row."""
    return TaskNode(
        key=task.key,
        number=task.number,
        kind=task.kind,
        state=task.state,
        deps=tuple(task.deps or ()),
        scope=tuple(task.scope or ()),
        priority=task.priority,
        estimate_min=task.estimate_min,
        preview=bool((task.card or {}).get("preview")),
    )


def _clean_list(values: Iterable[str] | None, limit: int = 500) -> list[str]:
    out: list[str] = []
    for value in values or ():
        text = " ".join(str(value).split())[:limit]
        if text and text not in out:
            out.append(text)
    return out


def _now() -> int:
    return int(time.time())


# ── Loading ────────────────────────────────────────────────────


@dataclass
class ProjectCtx:
    """
    Everything the graph computations need about one project.

    :param external_met: ``project:``/``decision:`` dependency → met.
    """

    factory_id: str
    project: FactoryProject | None
    slug: str
    tasks: list[FactoryTask]
    charter: dict[str, Any]
    external_met: dict[str, bool]
    paused: bool
    active: bool
    slots: Slots
    observed: dict[str, float]
    prs: dict[str, FactoryPr] = field(default_factory=dict)
    hub_files: list[str] = field(default_factory=list)

    def nodes(self) -> list[TaskNode]:
        """:returns: The project's graph nodes."""
        return [node_of(t) for t in self.tasks]

    def by_key(self) -> dict[str, FactoryTask]:
        """:returns: Task rows by key."""
        return {t.key: t for t in self.tasks}


@dataclass
class Analysis:
    """The computed work graph of one project."""

    plan: WavePlan
    ready: list[str]
    path: list[str]
    path_min: float | None
    warnings: list[dict[str, Any]]
    waiting: dict[str, list[str]]
    layers: dict[str, int]


_observed: dict[str, tuple[float, dict[str, float]]] = {}


def observed_weights(deps: FactoryDeps, factory_id: str) -> dict[str, float]:
    """
    Kind → observed p50 task duration in minutes (cached 60 s).

    A task's duration runs from its first implement worker's start to its
    last one's end (the sub-agent ``task`` spans the binder stamps with the
    task); only tasks whose workers all ended, and kinds with at least five
    such tasks, count.
    """
    key = norm_id(factory_id)
    cached = _observed.get(key)
    if cached is not None and time.monotonic() - cached[0] < OBSERVED_TTL_S:
        return cached[1]
    runs: dict[str, list[Any]] = defaultdict(list)
    try:
        spans = deps.factory_store.list_spans(factory_id, kinds=["task"], limit=5000)
    except Exception:  # noqa: BLE001 - estimates stand in for observations
        _logger.warning("factory tasks: no task spans for %s", factory_id, exc_info=True)
        spans = []
    for span in spans:
        attrs = span.attrs or {}
        if span.task_id and attrs.get("loop") == "implement":
            runs[span.task_id].append(span)
    samples: dict[str, list[float]] = defaultdict(list)
    for items in runs.values():
        kind = (items[0].attrs or {}).get("task_kind")
        if not isinstance(kind, str) or any(s.ended_at is None for s in items):
            continue
        if not any(s.outcome == "ok" for s in items):
            continue
        end = max(int(s.ended_at or 0) for s in items)
        samples[kind].append(max(0, end - min(s.started_at for s in items)) / 60000)
    weights = observed_p50(samples)
    if len(_observed) > 1024:
        _observed.clear()
    _observed[key] = (time.monotonic(), weights)
    return weights


def project_dep_met(
    slug: str, projects: Mapping[str, FactoryProject], prs: Sequence[FactoryPr]
) -> bool:
    """
    Whether ``project:<slug>`` is met, as project gating counts a ``merged`` dependency.

    A project merged into another stands for the one that absorbed it.
    """
    from omnigent.server.factory import gating

    target = gating.resolve_alias(slug, projects)
    return target is not None and gating.dependency_met(target, prs, DEPENDS_MERGED)


def _paths(value: Any) -> list[str]:
    items = value if isinstance(value, list) else []
    out = [i.get("path") if isinstance(i, Mapping) else i for i in items]
    return [p for p in out if isinstance(p, str) and p.strip()]


def _proposal_slugs(ops: Any) -> set[str]:
    slugs: set[str] = set()
    for op in ops if isinstance(ops, list) else []:
        if not isinstance(op, Mapping):
            continue
        into = op.get("into")
        projects = [op.get("project"), *(into if isinstance(into, list) else [])]
        values = [
            op.get("slug"),
            into,
            *(p.get("slug") for p in projects if isinstance(p, Mapping)),
        ]
        slugs.update(v for v in values if isinstance(v, str))
    return slugs


def hub_files(deps: FactoryDeps, factory_id: str) -> dict[str, list[str]]:
    """
    Project slug → hub files a planner analysis flagged (best effort, never raises).

    Reads the ``hub_files`` of each projects proposal's analysis (overall, or
    under ``projects.<slug>``) for the projects its ops touch.
    """
    out: dict[str, list[str]] = defaultdict(list)
    try:
        proposals = deps.factory_store.list_proposals(factory_id, kind="projects", limit=50)
    except Exception:  # noqa: BLE001 - no analysis, no hub warnings
        return {}
    for proposal in proposals:
        analysis = proposal.analysis if isinstance(proposal.analysis, Mapping) else {}
        per_project = analysis.get("projects")
        per_project = per_project if isinstance(per_project, Mapping) else {}
        for slug in _proposal_slugs(proposal.ops):
            scoped = per_project.get(slug)
            found = _paths(analysis.get("hub_files"))
            if isinstance(scoped, Mapping):
                found += _paths(scoped.get("hub_files"))
            out[slug] += [p for p in found if p not in out[slug]]
    return dict(out)


def _decision_met(decision: FactoryDecision | None) -> bool:
    return decision is not None and decision.status in ("answered", "withdrawn")


def _is_paused(deps: FactoryDeps, factory_id: str, project: FactoryProject | None) -> bool:
    store = deps.conversation_store
    root = store.get_conversation(factory_id)
    if root is not None and root.labels.get(LABEL_STATE) == STATE_PAUSED:
        return True
    if project is not None and project.pl_session_id:
        pl = store.get_conversation(project.pl_session_id)
        return pl is not None and pl.labels.get(LABEL_STATE) == STATE_PAUSED
    return False


def _worker_running(deps: FactoryDeps, task: FactoryTask) -> bool:
    """:returns: Whether one of the task's bound sessions is running now."""
    from omnigent.server.routes._sessions.helpers import _session_status_from_cache

    store = deps.conversation_store
    for session_id in task.session_ids or ():
        conv = store.get_conversation(session_id)
        if conv is not None and _session_status_from_cache(conv.id, conv.live_status) == "running":
            return True
    return False


def _slots(
    charter: Mapping[str, Any],
    tasks: Sequence[FactoryTask],
    prs: Sequence[FactoryPr],
    slug: str,
    idle: Collection[str] = (),
) -> Slots:
    """:param idle: Ids of occupying tasks that hold no IC slot (no worker running)."""
    budget_ics = int(charter.get("budget_ics") or 3)
    running = sum(1 for t in tasks if t.state in OCCUPYING and t.id not in idle)
    preview = charter.get("preview") or {}
    previews_on = bool(charter.get("env_needed")) and bool(preview.get("enabled"))
    preview_slots = int(preview.get("slots") or 0) if previews_on else 0
    live = sum(
        1
        for p in prs
        if p.project_slug == slug
        and p.state == "open"
        and isinstance(p.preview, Mapping)
        and p.preview.get("state") in LIVE_PREVIEW_STATES
    )
    return Slots(
        ics_free=max(0, budget_ics - running),
        budget_ics=budget_ics,
        previews_free=max(0, preview_slots - live),
        preview_slots=preview_slots,
        previews_on=previews_on,
    )


@dataclass
class FactoryData:
    """One read of a factory's projects, tasks, PRs and the decisions tasks wait on."""

    factory_id: str
    projects: dict[str, FactoryProject]
    tasks: list[FactoryTask]
    prs: list[FactoryPr]
    decisions: dict[str, FactoryDecision | None]
    observed: dict[str, float]
    hubs: dict[str, list[str]] = field(default_factory=dict)

    def project_ctx(self, deps: FactoryDeps, slug: str) -> ProjectCtx:
        """:returns: The context of one project (paused state read from labels)."""
        project = self.projects.get(slug)
        tasks = sorted((t for t in self.tasks if t.project_slug == slug), key=lambda t: t.number)
        charter = normalize_charter(project.charter if project is not None else None)
        external: dict[str, bool] = {}
        for task in tasks:
            for dep in task.deps or ():
                if dep.startswith(PROJECT_DEP_PREFIX):
                    external[dep] = project_dep_met(
                        dep.removeprefix(PROJECT_DEP_PREFIX), self.projects, self.prs
                    )
                elif dep.startswith(DECISION_DEP_PREFIX):
                    ref = dep.removeprefix(DECISION_DEP_PREFIX)
                    external[dep] = _decision_met(self.decisions.get(ref))
        active = project is None or project.status in (None, "active")
        # Ad-hoc work holds an IC slot only while one of its workers runs (the
        # PL never started it, so nothing else closes it).
        idle = {
            t.id
            for t in tasks
            if t.kind == "adhoc" and t.state in OCCUPYING and not _worker_running(deps, t)
        }
        return ProjectCtx(
            factory_id=self.factory_id,
            project=project,
            slug=slug,
            tasks=tasks,
            charter=charter,
            external_met=external,
            paused=_is_paused(deps, self.factory_id, project),
            active=active,
            slots=_slots(charter, tasks, self.prs, slug, idle),
            observed=self.observed,
            prs={p.id: p for p in self.prs},
            hub_files=self.hubs.get(slug, []),
        )


def load_factory(
    deps: FactoryDeps, factory_id: str, slug: str | None = None, *, full: bool = True
) -> FactoryData:
    """
    Read what the work graph of *factory_id* (or one project of it) needs (synchronous).

    :param slug: Only this project's tasks.
    :param full: Also read observed durations and planner hub files (not
        needed to re-derive states).
    """
    store = deps.factory_store
    tasks = store.list_tasks(factory_id, project_slug=slug)
    refs = {
        dep.removeprefix(DECISION_DEP_PREFIX)
        for t in tasks
        for dep in t.deps or ()
        if dep.startswith(DECISION_DEP_PREFIX)
    }
    decisions: dict[str, FactoryDecision | None] = {}
    for ref in refs:
        match = _D_REF.match(ref)
        decisions[ref] = (
            store.get_decision_by_number(factory_id, int(match.group(1))) if match else None
        )
    return FactoryData(
        factory_id=factory_id,
        projects={p.slug: p for p in store.list_projects(factory_id)},
        tasks=tasks,
        prs=store.list_prs(factory_id) if tasks else [],
        decisions=decisions,
        observed=observed_weights(deps, factory_id) if tasks and full else {},
        hubs=hub_files(deps, factory_id) if tasks and full else {},
    )


def load_ctx(deps: FactoryDeps, factory_id: str, slug: str) -> ProjectCtx:
    """:returns: One project's context (synchronous)."""
    return load_factory(deps, factory_id, slug).project_ctx(deps, slug)


def analyze(ctx: ProjectCtx) -> Analysis:
    """Compute the ready set, waves, critical path and warnings of a project."""
    nodes = ctx.nodes()
    by_key = {n.key: n for n in nodes}
    plan = plan_waves(nodes, ctx.external_met, ctx.slots, active=ctx.active, paused=ctx.paused)
    path, total = _critical_path(nodes, lambda n: weight_min(n, ctx.observed))
    waiting = {
        n.key: found
        for n in nodes
        if n.state in PENDING and (found := waiting_on(n, by_key, ctx.external_met))
    }
    return Analysis(
        plan=plan,
        ready=ready_set(nodes, ctx.external_met, active=ctx.active, paused=ctx.paused),
        path=path,
        path_min=total if path else None,
        warnings=[
            {**w, "project": ctx.slug} for w in overlap_warnings(nodes, hub_files=ctx.hub_files)
        ],
        waiting=waiting,
        layers=layers(nodes),
    )


# ── Views and summaries ────────────────────────────────────────


def _card_view(card: Mapping[str, Any] | None) -> dict[str, Any]:
    src = card or {}
    out: dict[str, Any] = {k: src.get(k) for k in CARD_KEYS}
    out["acceptance"] = list(src.get("acceptance") or [])
    out["env_checks"] = list(src.get("env_checks") or [])
    if out["harness"] not in HARNESSES:
        out["harness"] = None
    return out


def _budget_view(budget: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not budget:
        return None
    return {"attempts": budget.get("attempts"), "usd": budget.get("usd")}


def task_view(
    task: FactoryTask,
    *,
    analysis: Analysis | None = None,
    prs: Mapping[str, FactoryPr] | None = None,
    loop: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """
    :returns: The :class:`~omnigent.server.factory.schemas.TaskView` JSON of a task.

    :param loop: Its implement loop's chip, when known.
    """
    pr = (prs or {}).get(task.pr_id or "")
    waves = analysis.plan.wave_of() if analysis is not None else {}
    body = {
        "id": task.id,
        "ref": task.ref,
        "number": task.number,
        "project_slug": task.project_slug,
        "key": task.key,
        "title": task.title,
        "kind": task.kind,
        "state": task.state,
        "card": _card_view(task.card),
        "deps": list(task.deps or []),
        "scope": list(task.scope or []),
        "priority": task.priority,
        "estimate_min": task.estimate_min,
        "vendor": task.vendor,
        "session_ids": list(task.session_ids or []),
        "pr_id": task.pr_id,
        "pr_number": pr.number if pr is not None else None,
        "attempts": task.attempts,
        "budget": _budget_view(task.budget),
        "cost_usd": task.cost_usd,
        "created_by": task.created_by or "server",
        "created_via": task.created_via,
        "def_version": task.def_version,
        "started_at": task.started_at,
        "closed_at": task.closed_at,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "wave": waves.get(task.key),
        "critical": analysis is not None and task.key in analysis.path,
        "waiting_on": list((analysis.waiting if analysis is not None else {}).get(task.key, [])),
        "loop": loop,
    }
    return schemas.TaskView.model_validate(body).model_dump(mode="json")


def summary(ctx: ProjectCtx, analysis: Analysis) -> dict[str, Any]:
    """:returns: The project's :class:`~omnigent.server.factory.schemas.TasksSummaryView` JSON."""
    live = [t for t in ctx.tasks if t.state != "cancelled"]
    count = defaultdict(int)
    for task in live:
        count[task.state] += 1
    body = {
        "total": len(live),
        "done": count["done"],
        "ready": len(analysis.ready),
        "running": count["running"] + count["review"],
        "blocked": count["blocked"] + len(analysis.plan.blocked),
        "failed": count["failed"],
        "critical_path_min": round(analysis.path_min) if analysis.path_min else None,
    }
    return schemas.TasksSummaryView.model_validate(body).model_dump(mode="json")


def snapshot_fields(
    deps: FactoryDeps, factory_id: str
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """
    What the snapshot shows of the work graph (synchronous; never raises).

    :returns: ``(project slug → tasks summary, task id → "T-n")``.
    """
    try:
        data = load_factory(deps, factory_id)
        out: dict[str, dict[str, Any]] = {}
        for slug in sorted({t.project_slug for t in data.tasks}):
            ctx = data.project_ctx(deps, slug)
            out[slug] = summary(ctx, analyze(ctx))
        return out, {t.id: t.ref for t in data.tasks}
    except Exception:  # noqa: BLE001 - the snapshot renders without them
        _logger.warning("factory tasks: no snapshot fields for %s", factory_id, exc_info=True)
        return {}, {}


def graph_view(ctx: ProjectCtx, analysis: Analysis) -> dict[str, Any]:
    """:returns: The project's work-graph answer: ready, waves, critical path, warnings."""
    return {
        "project": ctx.slug,
        "ready": analysis.ready,
        "waves": analysis.plan.nonempty(),
        "critical_path": analysis.path,
        "critical_path_min": analysis.path_min,
        "blocked": [{"key": k, "waiting_on": v} for k, v in analysis.plan.blocked.items()],
        "slots": {"ics_free": ctx.slots.ics_free, "previews_free": ctx.slots.previews_free},
        "paused": ctx.paused,
        "warnings": analysis.warnings,
    }


# ── Cards and budgets ──────────────────────────────────────────


def task_budget(task: FactoryTask, project: FactoryProject | None) -> tuple[int, float]:
    """
    The task's attempt and dollar budgets (read only; the loop engine enforces them).

    :returns: ``(attempts, usd)``: the task's override, else the charter's
        ``budgets.task_usd`` (for dollars), else the code defaults.
    """
    from omnigent.server.factory.loops import budget as loop_budget

    budget = task.budget or {}
    attempts = budget.get("attempts")
    usd = budget.get("usd")
    if usd is None and project is not None:
        charter_budgets = (project.charter or {}).get("budgets")
        if isinstance(charter_budgets, Mapping):
            usd = charter_budgets.get("task_usd")
    return (
        int(attempts)
        if isinstance(attempts, int) and attempts > 0
        else int(loop_budget(task.factory_id, "implement", "attempts")),
        float(usd)
        if isinstance(usd, int | float) and usd >= 0
        else float(loop_budget(task.factory_id, "implement", "usd")),
    )


def budget_left(task: FactoryTask, project: FactoryProject | None) -> dict[str, Any]:
    """:returns: ``{attempts, usd}`` left (never below zero)."""
    attempts, usd = task_budget(task, project)
    return {
        "attempts": max(0, attempts - task.attempts),
        "usd": round(max(0.0, usd - (task.cost_usd or 0.0)), 2),
    }


def cost_budget_for(
    task: FactoryTask, harness: str, usd_left: float, mode: str
) -> dict[str, Any] | None:
    """
    The ``cost_budget`` a dispatch carries, or ``None``.

    Only in ``enforce`` mode, never for ``ic_codex`` (unpriced sessions
    fail the cost policy closed) and never at $0 (a dispatch refuses it).
    """
    del task
    if mode != "enforce" or harness == "ic_codex" or round(usd_left, 2) <= 0:
        return None
    return {"max_cost_usd": round(usd_left, 2)}


def _bullets(items: Sequence[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def card_text(
    task: FactoryTask,
    project: FactoryProject | None,
    *,
    pl_id: str | None,
    attempt: int,
    rules: Sequence[str] = (),
) -> str:
    """
    The task card a worker gets, rendered by the server.

    It carries the card itself, the base (``Base: <base>``; the worker branches
    from ``origin/<base>``), the acceptance commands to run verbatim, the
    factory definition's rules for the role, and the budget left.

    :param attempt: The attempt this card is for (1-based).
    """
    card = task.card or {}
    slug = task.project_slug
    base = project_base_branch(project) if project is not None else "main"
    repo = (project.repo if project is not None else None) or "(the project's repo)"
    attempts, usd = task_budget(task, project)
    left = budget_left(task, project)
    scope = ", ".join(task.scope or []) or "the files this task needs"
    acceptance = list(card.get("acceptance") or [])
    env_checks = list(card.get("env_checks") or [])
    lines = [
        f"TASK {task.key} · {task.title} · {task.ref} · attempt {attempt}/{attempts}",
        f"Project: {slug} · PL: {pl_id or '(your project lead)'}",
        f"Repo: {repo} · Base: {base} (branch from origin/{base}; open the PR with --base {base})",
        f"Worktree: {WORKTREE_ROOT}/{task.key} · Branch: factory/{slug}/{task.key}",
        f"Scope: {scope}; do not touch anything else",
        f"Do: {card.get('text') or task.title}",
        "Acceptance (run verbatim, all must pass):",
        _bullets(acceptance) if acceptance else "- (none given: show the change works)",
        "Env checks (env_keeper runs these against the PR's factory preview): "
        + (" | ".join(env_checks) if env_checks else "none"),
        f"Preview: {'yes' if card.get('preview') else 'no'}",
        f"Demo focus: {card.get('demo_focus') or 'none'}",
        f"Budget left: {left['attempts']} of {attempts} attempts · ${left['usd']:.2f} of "
        f"${usd:.2f}",
    ]
    if rules:
        lines += ["Rules (from the factory definition):", _bullets(list(rules))]
    lines += [
        "PR: draft early, repo template; Verification plan = the env checks; Demo plan = "
        "tagged steps for the demo focus; Factory provenance = project "
        f"{slug}, PL {pl_id or '<pl>'}, task {task.ref}; label factory:no-env if Preview: no",
        "Report: the RESULT block, and "
        f'sys_factory_task(op="report", key="{task.key}", result="PASS|FAIL|BLOCKED", '
        'note="<one line>")',
    ]
    # Card fields are free text (a PL's or a person's): quote notice look-alikes.
    return quote_spoofed_lines("\n".join(lines))


def _pl_id(deps: FactoryDeps, factory_id: str, project: FactoryProject | None) -> str | None:
    from omnigent.server.factory.control import find_project_pl

    if project is None:
        return None
    if project.pl_session_id:
        return project.pl_session_id
    pl = find_project_pl(deps, factory_id, project.slug)
    return pl.id if pl is not None else None


def dispatch_item(
    deps: FactoryDeps, ctx: ProjectCtx, task: FactoryTask, *, mode: str, attempt: int | None = None
) -> dict[str, Any]:
    """
    What the PL needs to dispatch a task: title, harness, card and budget.

    :param attempt: The attempt to render (default: the next one).
    """
    attempt = attempt if attempt is not None else task.attempts + 1
    harness = harness_of(task)
    left = budget_left(task, ctx.project)
    pl_id = _pl_id(deps, ctx.factory_id, ctx.project)
    item: dict[str, Any] = {
        "key": task.key,
        "number": task.number,
        "ref": task.ref,
        "title": task.title,
        "title_to_use": title_to_use(task.key, attempt),
        "harness": harness,
        "card_text": card_text(
            task,
            ctx.project,
            pl_id=pl_id,
            attempt=attempt,
            rules=role_rules(deps, ctx.factory_id, "ic", ctx.slug),
        ),
        "budget_left": left,
        "preview": bool((task.card or {}).get("preview")),
    }
    cost = cost_budget_for(task, harness, left["usd"], mode)
    if cost is not None:
        item["cost_budget"] = cost
    return item


# ── Events, spans and locks ────────────────────────────────────


@dataclass
class TaskEvent:
    """An event to emit once the store write is done."""

    kind: str
    task: FactoryTask
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    severity: str = "info"
    from_id: str | None = None


def state_event(
    task: FactoryTask, before: str, actor: Actor, reason: str | None = None, **data: Any
) -> TaskEvent:
    """:returns: The ``task.state`` event of a move from *before* to ``task.state``."""
    why = f" ({reason})" if reason else ""
    return TaskEvent(
        "task.state",
        task,
        f"{task.ref} {task.key}: {before} → {task.state}{why}",
        {"from": before, "to": task.state, "reason": reason, **actor.event_data(), **data},
        from_id=actor.session_id,
    )


async def emit_all(app_state: Any, factory_id: str, items: Sequence[TaskEvent]) -> None:
    """Emit the collected task events (never raises)."""
    for item in items:
        task = item.task
        await events.emit(
            app_state,
            factory_id,
            item.kind,
            severity=item.severity,
            project=task.project_slug,
            from_id=item.from_id,
            subject=task_subject(task),
            summary=item.summary,
            data={
                "task_id": task.id,
                "ref": task.ref,
                "key": task.key,
                "def_version": task.def_version,
                **item.data,
            },
        )


_locks: dict[tuple[int, str], asyncio.Lock] = {}
_bound_first: dict[str, tuple[int, float]] = {}
"""Task id → (attempt, when) the binder counted before the PL's ``start`` landed."""
BOUND_FIRST_S: Final = 60


def note_bound_attempt(task_id: str, attempt: int) -> None:
    """The binder counted *attempt* from a worker's title (``start`` then reuses it)."""
    if len(_bound_first) > 4096:
        _bound_first.clear()
    _bound_first[norm_id(task_id)] = (attempt, time.monotonic())


def _claim_bound_attempt(task: FactoryTask) -> bool:
    """:returns: Whether the binder, not ``start``, just counted the task's current attempt."""
    found = _bound_first.pop(norm_id(task.id), None)
    return (
        found is not None
        and found[0] == task.attempts
        and time.monotonic() - found[1] <= BOUND_FIRST_S
    )


def lock(factory_id: str) -> asyncio.Lock:
    """One work-graph write at a time per factory (in this process)."""
    key = (id(asyncio.get_running_loop()), norm_id(factory_id))
    found = _locks.get(key)
    if found is None:
        if len(_locks) > 4096:
            _locks.clear()
        found = _locks[key] = asyncio.Lock()
    return found


def move(
    deps: FactoryDeps,
    task: FactoryTask,
    to: str,
    actor: Actor,
    *,
    reason: str | None = None,
    **fields: Any,
) -> tuple[FactoryTask, TaskEvent | None]:
    """
    Move a task to state *to* (synchronous), stamping start and close times.

    :returns: ``(row, the task.state event or None when the state is unchanged)``.
    """
    before = task.state
    values: dict[str, Any] = dict(fields)
    if to != before:
        values["state"] = to
    now = _now()
    if to in ("running", "review") and task.started_at is None:
        values["started_at"] = now
    if to in ("done", "failed", "cancelled") and before not in ("done", "failed", "cancelled"):
        values["closed_at"] = fields.get("closed_at") or now
    if to not in ("done", "failed", "cancelled") and task.closed_at is not None:
        values["closed_at"] = None
    if not values:
        return task, None
    row = deps.factory_store.update_task(task.id, **values) or task
    return row, state_event(row, before, actor, reason) if to != before else None


def refresh_states(deps: FactoryDeps, ctx: ProjectCtx) -> list[TaskEvent]:
    """
    Persist ``planned`` ↔ ``ready`` from the dependencies (synchronous; the server moves them).

    Updates *ctx.tasks* in place. A paused project keeps its ready tasks ready.
    """
    who = Actor.server()
    nodes = {t.key: node_of(t) for t in ctx.tasks}
    out: list[TaskEvent] = []
    for index, task in enumerate(ctx.tasks):
        if task.state not in PENDING:
            continue
        want = "ready" if deps_met(nodes[task.key], nodes, ctx.external_met) else "planned"
        if want == task.state:
            continue
        reason = "dependencies met" if want == "ready" else "a dependency is open again"
        row, event = move(deps, task, want, who, reason=reason)
        ctx.tasks[index] = row
        if event is not None:
            out.append(event)
    return out


async def refresh_ready(app_state: Any, factory_id: str, slug: str | None = None) -> None:
    """Re-derive ready states of a factory (or one project) and emit the moves. Never raises."""
    deps = factory_deps(app_state)

    def run() -> list[TaskEvent]:
        data = load_factory(deps, factory_id, slug, full=False)
        out: list[TaskEvent] = []
        for project in sorted({t.project_slug for t in data.tasks}):
            out += refresh_states(deps, data.project_ctx(deps, project))
        return out

    try:
        async with lock(factory_id):
            moves = await asyncio.to_thread(run)
        await emit_all(app_state, factory_id, moves)
    except Exception:  # noqa: BLE001 - the next tick retries
        _logger.warning("factory tasks: ready refresh failed for %s", factory_id, exc_info=True)


# ── Upsert (PL and people) ─────────────────────────────────────


def _canonical_dep(raw: str, numbers: Mapping[int, str]) -> str:
    """
    One dependency in its stored spelling.

    ``T-4`` becomes that task's key, ``D-7`` becomes ``decision:D-7``, and
    ``p:<slug>`` / ``#<slug>`` become ``project:<slug>``.
    """
    text = raw.strip()
    if match := _T_REF.match(text):
        return numbers.get(int(match.group(1)), text)
    if match := _D_REF.match(text):
        return f"{DECISION_DEP_PREFIX}D-{int(match.group(1))}"
    if match := _P_REF.match(text):
        return f"{PROJECT_DEP_PREFIX}{match.group(1)}"
    return text


def _card_from(fields: TaskFields, current: Mapping[str, Any] | None) -> dict[str, Any] | None:
    card = {k: v for k, v in (current or {}).items() if k in CARD_KEYS}
    given = fields.model_fields_set
    changed = False
    for key in CARD_KEYS:
        if key not in given:
            continue
        value = getattr(fields, key)
        if key in ("acceptance", "env_checks"):
            value = _clean_list(value, 1000)
        elif isinstance(value, str):
            value = value.strip() or None
        card[key] = value
        changed = True
    return card if changed or current is None else None


def _row_fields(
    fields: TaskFields,
    current: FactoryTask | None,
    numbers: Mapping[int, str],
    *,
    budget: TaskBudgetIn | None = None,
    budget_set: bool = False,
) -> dict[str, Any]:
    """The store fields one input sets (only what it names, on an update)."""
    given = fields.model_fields_set
    out: dict[str, Any] = {}
    title = " ".join((fields.title or "").split()) if "title" in given else ""
    if title:
        out["title"] = title
    if "kind" in given and fields.kind:
        out["kind"] = fields.kind
    elif current is not None and current.kind == "adhoc":
        out["kind"] = "implement"
    if "deps" in given:
        out["deps"] = _clean_list(_canonical_dep(d, numbers) for d in fields.deps or [])
    if "scope" in given:
        out["scope"] = _clean_list(fields.scope)
    if "priority" in given and fields.priority is not None:
        out["priority"] = fields.priority
    if "estimate_min" in given:
        out["estimate_min"] = fields.estimate_min
    card = _card_from(fields, current.card if current is not None else None)
    if card is not None:
        out["card"] = card
    if budget_set:
        out["budget"] = budget.model_dump(exclude_none=True) if budget is not None else None
    return out


def _validate(
    nodes: Sequence[TaskNode], projects: Iterable[str], decisions: Iterable[str]
) -> None:
    """:raises CockpitError: 422 ``unknown_dep`` or 409 ``cycle``."""
    unknown = unknown_deps(nodes, projects=projects, decisions=decisions)
    if unknown:
        task, dep = unknown[0]
        raise CockpitError(
            422,
            "unknown_dep",
            f"{task} depends on {dep!r}, which is no task of this project, project:<slug> "
            "or decision:D-<n> of this factory",
            unknown=[{"task": t, "dep": d} for t, d in unknown],
        )
    cycle = find_cycle(nodes)
    if cycle is not None:
        raise CockpitError(
            409,
            "cycle",
            "the dependencies form a cycle: " + " → ".join(cycle),
            path=cycle,
        )


def _known_decisions(deps: FactoryDeps, factory_id: str, nodes: Sequence[TaskNode]) -> set[str]:
    refs = {
        d.removeprefix(DECISION_DEP_PREFIX)
        for n in nodes
        for d in n.deps
        if d.startswith(DECISION_DEP_PREFIX)
    }
    found: set[str] = set()
    for ref in refs:
        match = _D_REF.match(ref)
        if match and deps.factory_store.get_decision_by_number(factory_id, int(match.group(1))):
            found.add(ref)
    return found


@dataclass
class Write:
    """The outcome of one work-graph write."""

    ctx: ProjectCtx
    events: list[TaskEvent]
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    before: dict[str, FactoryTask] = field(default_factory=dict)


def apply_fields(
    deps: FactoryDeps,
    factory_id: str,
    slug: str,
    changes: Sequence[tuple[str, dict[str, Any]]],
    actor: Actor,
) -> Write:
    """
    Validate the graph after *changes* and write them (synchronous).

    :param changes: ``(key, store fields)``; a new key creates its task.
    :raises CockpitError: 404 ``unknown_project``, 422 ``unknown_dep``, 409 ``cycle``.
    """
    data = load_factory(deps, factory_id, slug)
    if slug not in data.projects:
        raise CockpitError(404, "unknown_project", f"no project {slug!r} in this factory")
    current = {t.key: t for t in data.tasks}
    merged = {k: node_of(t) for k, t in current.items()}
    for key, fields in changes:
        base = merged.get(key) or TaskNode(key=key, number=10**9)
        merged[key] = TaskNode(
            key=key,
            number=base.number,
            kind=fields.get("kind", base.kind),
            state=base.state,
            deps=tuple(fields.get("deps", base.deps)),
            scope=tuple(fields.get("scope", base.scope)),
            priority=fields.get("priority", base.priority),
            estimate_min=fields.get("estimate_min", base.estimate_min),
        )
    nodes = list(merged.values())
    others = [p for p in data.projects if p != slug]
    _validate(nodes, others, _known_decisions(deps, factory_id, nodes))
    def_version = span_context(deps, factory_id).def_version
    write = Write(ctx=data.project_ctx(deps, slug), events=[], before=dict(current))
    store = deps.factory_store
    for key, fields in changes:
        existing = current.get(key)
        if existing is None:
            values = {
                "title": key,
                "kind": "implement",
                "card": {},
                "created_by": actor.id,
                "created_via": actor.kind,
                "def_version": def_version,
                **fields,
            }
            row, created = store.upsert_task(factory_id, slug, key, **values)
            current[key] = row
            if created:
                write.created.append(key)
                write.events.append(
                    TaskEvent(
                        "task.created",
                        row,
                        f"{row.ref} {key} added to {slug} by {actor.title}",
                        {"kind": row.kind, "via": actor.kind, **actor.event_data()},
                        from_id=actor.session_id,
                    )
                )
            else:
                write.updated.append(key)
        elif fields:
            current[key] = store.update_task(existing.id, **fields) or existing
            write.updated.append(key)
    data = load_factory(deps, factory_id, slug)
    write.ctx = data.project_ctx(deps, slug)
    write.events += refresh_states(deps, write.ctx)
    return write


def _numbers(deps: FactoryDeps, factory_id: str, slug: str) -> dict[int, str]:
    return {t.number: t.key for t in deps.factory_store.list_tasks(factory_id, project_slug=slug)}


async def upsert_tasks(
    app_state: Any, factory_id: str, slug: str, items: Sequence[TaskIn], actor: Actor
) -> dict[str, Any]:
    """
    ``sys_factory_task(op="upsert")``: create or update a project's tasks.

    :returns: ``{created, updated, ready, waves, critical_path, critical_path_min,
        blocked, slots, warnings}``.
    :raises CockpitError: 400 too many or duplicate keys, 422 ``unknown_dep``, 409 ``cycle``.
    """
    if not items:
        raise CockpitError(400, "invalid_body", "tasks must name at least one task")
    if len(items) > MAX_UPSERT:
        raise CockpitError(400, "too_many_tasks", f"at most {MAX_UPSERT} tasks per call")
    keys = [i.key for i in items]
    if len(set(keys)) != len(keys):
        raise CockpitError(400, "duplicate_key", "each task key may appear once per call")
    deps = factory_deps(app_state)

    def run() -> Write:
        numbers = _numbers(deps, factory_id, slug)
        current = {t.key: t for t in deps.factory_store.list_tasks(factory_id, project_slug=slug)}
        changes = [(i.key, _row_fields(i, current.get(i.key), numbers)) for i in items]
        return apply_fields(deps, factory_id, slug, changes, actor)

    async with lock(factory_id):
        write = await asyncio.to_thread(run)
    await emit_all(app_state, factory_id, write.events)
    analysis = analyze(write.ctx)
    return {"created": write.created, "updated": write.updated, **graph_view(write.ctx, analysis)}


# ── Reads ──────────────────────────────────────────────────────


def list_view(deps: FactoryDeps, factory_id: str, slug: str | None) -> dict[str, Any]:
    """
    Tasks with their computed graph, per project (synchronous).

    :returns: ``{"tasks": [TaskView], "projects": [graph view + summary]}``.
    """
    from omnigent.server.factory.loops import loop_chip

    data = load_factory(deps, factory_id, slug)
    slugs = [slug] if slug else sorted({t.project_slug for t in data.tasks})
    chips = {
        norm_id(loop.task_id): loop_chip(loop)
        for loop in deps.factory_store.list_loops(factory_id, kinds=["implement"])
        if loop.task_id
    }
    tasks: list[dict[str, Any]] = []
    projects: list[dict[str, Any]] = []
    for project in slugs:
        ctx = data.project_ctx(deps, project)
        analysis = analyze(ctx)
        tasks += [
            task_view(t, analysis=analysis, prs=ctx.prs, loop=chips.get(norm_id(t.id)))
            for t in ctx.tasks
        ]
        projects.append({**graph_view(ctx, analysis), "summary": summary(ctx, analysis)})
    return {"tasks": tasks, "projects": projects}


def get_view(deps: FactoryDeps, factory_id: str, slug: str, key: str) -> dict[str, Any]:
    """
    One task with its card and budget (synchronous).

    :raises CockpitError: 404 ``unknown_task``.
    """
    ctx = load_ctx(deps, factory_id, slug)
    task = ctx.by_key().get(key)
    if task is None:
        raise CockpitError(404, "unknown_task", f"no task {key!r} in project {slug}")
    analysis = analyze(ctx)
    return {
        "task": task_view(task, analysis=analysis, prs=ctx.prs),
        "dispatch": dispatch_item(deps, ctx, task, mode=engine_mode(deps, factory_id)),
    }


async def ready_view(app_state: Any, factory_id: str, slug: str) -> dict[str, Any]:
    """
    ``sys_factory_task(op="ready")``: the wave to dispatch now, with cards.

    :returns: ``{wave: [dispatch items], next_waves, blocked, slots, critical_path,
        warnings, paused}``.
    """
    deps = factory_deps(app_state)

    def run() -> tuple[ProjectCtx, list[TaskEvent]]:
        ctx = load_ctx(deps, factory_id, slug)
        return ctx, refresh_states(deps, ctx)

    async with lock(factory_id):
        ctx, moves = await asyncio.to_thread(run)
    await emit_all(app_state, factory_id, moves)

    def render() -> dict[str, Any]:
        analysis = analyze(ctx)
        by_key = ctx.by_key()
        mode = engine_mode(deps, factory_id)
        view = graph_view(ctx, analysis)
        return {
            "wave": [dispatch_item(deps, ctx, by_key[k], mode=mode) for k in analysis.plan.now],
            "next_waves": [list(w) for w in analysis.plan.waves[1:] if w],
            "blocked": view["blocked"],
            "slots": view["slots"],
            "ready": analysis.ready,
            "critical_path": analysis.path,
            "critical_path_min": analysis.path_min,
            "paused": ctx.paused,
            "warnings": analysis.warnings,
        }

    return await asyncio.to_thread(render)


# ── Transitions (PL and IC) ────────────────────────────────────


def _require_task(ctx: ProjectCtx, key: str) -> FactoryTask:
    task = ctx.by_key().get(key)
    if task is None:
        raise CockpitError(404, "unknown_task", f"no task {key!r} in project {ctx.slug}")
    return task


async def start_task(
    app_state: Any,
    factory_id: str,
    slug: str,
    key: str,
    actor: Actor,
    *,
    harness: str | None = None,
) -> dict[str, Any]:
    """
    ``sys_factory_task(op="start")``: the next attempt of a task.

    :returns: ``{key, ref, title_to_use, attempt, of, usd_left, harness, card_text,
        cost_budget?}``.
    :raises CockpitError: 404 ``unknown_task``, 409 ``task_closed`` or ``not_ready``.
    """
    if harness is not None and harness not in HARNESSES:
        raise CockpitError(400, "invalid_harness", f"harness must be one of {list(HARNESSES)}")
    deps = factory_deps(app_state)

    def run() -> tuple[dict[str, Any], list[TaskEvent]]:
        ctx = load_ctx(deps, factory_id, slug)
        task = _require_task(ctx, key)
        if task.state in ("done", "cancelled"):
            raise CockpitError(409, "task_closed", f"{task.ref} {key} is {task.state}")
        if task.state in PENDING:
            nodes = {t.key: node_of(t) for t in ctx.tasks}
            missing = waiting_on(nodes[key], nodes, ctx.external_met)
            if ctx.paused or not ctx.active:
                missing = [*missing, "the project is paused" if ctx.paused else "project closed"]
            if missing:
                raise CockpitError(
                    409,
                    "not_ready",
                    f"{task.ref} {key} waits on {', '.join(missing)}",
                    waiting_on=missing,
                )
        from omnigent.server.factory import loops

        mode = engine_mode(deps, factory_id)
        refusal = loops.start_refusal(deps, factory_id, task, mode)
        if refusal is not None:
            raise refusal  # enforce: 409 budget_exhausted past the attempt budget
        warning = loops.start_warning(deps, factory_id, task) if mode == "assist" else None
        use = harness or harness_of(task)
        # A start sent alongside its dispatch may land after the binder counted it.
        racing = task.state == "running" and task.attempts > 0 and _claim_bound_attempt(task)
        attempt = task.attempts if racing else task.attempts + 1
        card = dict(task.card or {})
        if harness is not None:
            card["harness"] = harness
        row, event = move(
            deps,
            task,
            "running",
            actor,
            reason=f"attempt {attempt}",
            attempts=attempt,
            vendor=VENDORS.get(use),
            card=card,
        )
        ctx.tasks = [row if t.id == row.id else t for t in ctx.tasks]
        item = dispatch_item(deps, ctx, row, mode=mode, attempt=attempt)
        attempts_cap, _ = task_budget(row, ctx.project)
        out = {
            **item,
            "attempt": attempt,
            "of": attempts_cap,
            "usd_left": item["budget_left"]["usd"],
            "state": row.state,
        }
        if warning:
            out["budget_warning"] = warning
        evs = [event] if event is not None else []
        if event is not None:
            event.data["attempt"] = attempt
        return out, evs

    async with lock(factory_id):
        out, evs = await asyncio.to_thread(run)
    await emit_all(app_state, factory_id, evs)
    if not evs:
        # A re-dispatch of a running task moves no state: tell the loop engine directly.
        from omnigent.server.factory import loops

        await loops.observe_started(app_state, factory_id, slug, key, int(out["attempt"]))
    return out


CLOSING_OPS: Final[dict[str, str]] = {"done": "done", "fail": "failed", "cancel": "cancelled"}


async def close_task(
    app_state: Any,
    factory_id: str,
    slug: str,
    key: str,
    op: str,
    actor: Actor,
    *,
    note: str | None = None,
) -> dict[str, Any]:
    """
    ``sys_factory_task(op="done"|"fail"|"cancel")``.

    :returns: ``{task, ready}``: the task and the project's ready set after it.
    :raises CockpitError: 404 ``unknown_task``, 409 ``task_closed`` (cancelled).
    """
    to = CLOSING_OPS[op]
    deps = factory_deps(app_state)

    def run() -> tuple[dict[str, Any], list[TaskEvent]]:
        ctx = load_ctx(deps, factory_id, slug)
        task = _require_task(ctx, key)
        if task.state == "cancelled" and to != "cancelled":
            raise CockpitError(409, "task_closed", f"{task.ref} {key} is cancelled")
        row, event = move(deps, task, to, actor, reason=note[:200] if note else op)
        evs = [event] if event is not None else []
        data = load_factory(deps, factory_id, slug)
        ctx = data.project_ctx(deps, slug)
        evs += refresh_states(deps, ctx)
        analysis = analyze(ctx)
        return {
            "task": task_view(row, analysis=analysis, prs=ctx.prs),
            "ready": analysis.ready,
        }, evs

    async with lock(factory_id):
        out, evs = await asyncio.to_thread(run)
    await emit_all(app_state, factory_id, evs)
    return out


async def report_task(
    app_state: Any,
    factory_id: str,
    slug: str,
    key: str,
    actor: Actor,
    *,
    result: str,
    note: str | None = None,
) -> dict[str, Any]:
    """
    ``sys_factory_task(op="report")`` by the worker bound to the task.

    ``BLOCKED`` marks a running task blocked; ``PASS`` clears a block. The
    report is recorded either way (``task.state`` with ``result``).

    :raises CockpitError: 404 ``unknown_task``, 403 ``not_your_task``.
    """
    if result not in REPORT_RESULTS:
        raise CockpitError(400, "invalid_result", f"result must be one of {list(REPORT_RESULTS)}")
    deps = factory_deps(app_state)

    def run() -> tuple[dict[str, Any], list[TaskEvent]]:
        ctx = load_ctx(deps, factory_id, slug)
        task = _require_task(ctx, key)
        if actor.session_id is None or not any(
            norm_id(s) == norm_id(actor.session_id) for s in task.session_ids or []
        ):
            raise CockpitError(
                403, "not_your_task", f"{task.ref} {key} is not the task you were dispatched for"
            )
        to = task.state
        if result == "BLOCKED" and task.state == "running":
            to = "blocked"
        elif result == "PASS" and task.state == "blocked":
            to = "running"
        before = task.state
        row, _ = move(deps, task, to, actor, reason=f"worker reported {result}")
        extra = {"result": result, "note": (note or "")[:300] or None}
        event = state_event(row, before, actor, f"worker reported {result}", **extra)
        return {"task": task_view(row, prs=ctx.prs), "recorded": result}, [event]

    async with lock(factory_id):
        out, evs = await asyncio.to_thread(run)
    await emit_all(app_state, factory_id, evs)
    return out


# ── People (cockpit) ───────────────────────────────────────────


def _diff_lines(task: FactoryTask | None) -> list[str]:
    """One physical line per element (multi-line fields split), notice look-alikes quoted."""
    if task is None:
        return []
    card = task.card or {}
    budget = task.budget or {}
    fields = [
        f"{task.ref} {task.key} · {task.title}",
        f"State: {task.state} · Kind: {task.kind} · Priority: P{task.priority}",
        f"Deps: {', '.join(task.deps or []) or 'none'}",
        f"Scope: {', '.join(task.scope or []) or 'none'}",
        f"Do: {card.get('text') or '-'}",
        f"Acceptance: {' | '.join(card.get('acceptance') or []) or 'none'}",
        f"Env checks: {' | '.join(card.get('env_checks') or []) or 'none'}",
        f"Preview: {'yes' if card.get('preview') else 'no'} · "
        f"Demo focus: {card.get('demo_focus') or 'none'}",
        f"Harness: {card.get('harness') or DEFAULT_HARNESS} · "
        f"Estimate: {task.estimate_min or '-'} min",
        f"Budget: {budget.get('attempts') or '-'} attempts · ${budget.get('usd') or '-'}",
    ]
    return quote_spoofed_lines("\n".join(fields)).splitlines()


def task_diff(before: FactoryTask | None, after: FactoryTask) -> str:
    """:returns: A unified diff of two renderings of a task (all ``+`` for a new one)."""
    return "\n".join(
        difflib.unified_diff(
            _diff_lines(before),
            _diff_lines(after),
            "task (before)",
            "task (after)",
            lineterm="",
        )
    )


def _diff_block(diff: str) -> str:
    """:returns: *diff* fenced (longer than any backtick run in it), notice look-alikes quoted."""
    runs = [len(run) for run in re.findall(r"`+", diff)]
    fence = "`" * max(3, max(runs, default=0) + 1)
    return f"{fence}diff\n{quote_spoofed_lines(diff)}\n{fence}"


def task_add_text(by: str, task: FactoryTask, diff: str, waiting: Sequence[str]) -> str:
    """:returns: The ``[Factory control · TASK ADD · by <human>]`` mail."""
    from omnigent.server.factory.control import control_header

    waits = f" It waits on {', '.join(waiting)}." if waiting else " Its dependencies are met."
    return (
        f"{control_header('TASK ADD', by)} {task.ref} {task.key} was added to your work "
        f'graph.{waits} Plan it in: call sys_factory_task(op="ready") and dispatch from its '
        "wave when a slot is free; note it in plan.md. No reply needed."
        f"\n{_diff_block(diff)}"
    )


def task_edit_text(by: str, task: FactoryTask, diff: str) -> str:
    """:returns: The ``[Factory control · TASK EDIT · by <human>]`` mail."""
    from omnigent.server.factory.control import control_header

    if task.state == "cancelled":
        action = (
            "It is cancelled: stop its worker if one runs (sys_cancel_task) and drop it "
            "from plan.md; leave its PR to the humans."
        )
    else:
        action = (
            f'Follow the new card from its next dispatch (sys_factory_task(op="get", '
            f'key="{task.key}")) and tell a running worker what changed.'
        )
    return (
        f"{control_header('TASK EDIT', by)} {task.ref} {task.key} changed. {action} "
        f"No reply needed.\n{_diff_block(diff)}"
    )


def resolve_task_ref(deps: FactoryDeps, factory_id: str, ref: str) -> FactoryTask:
    """
    A task by row id or ``T-<n>`` (synchronous).

    :raises CockpitError: 404 ``unknown_task``.
    """
    match = _T_REF.match(ref.strip())
    task: FactoryTask | None = None
    if match:
        task = deps.factory_store.get_task_by_number(factory_id, int(match.group(1)))
    else:
        try:
            task = deps.factory_store.get_task(ref)
        except ValueError:
            task = None
    if task is None or norm_id(task.factory_id) != norm_id(factory_id):
        raise CockpitError(404, "unknown_task", f"no task {ref!r} in this factory")
    return task


async def _mail_pl(
    app_state: Any, factory_id: str, slug: str, text: str
) -> tuple[str | None, str | None]:
    from omnigent.server.factory import control

    deps = factory_deps(app_state)
    pl = await asyncio.to_thread(control.find_project_pl, deps, factory_id, slug)
    if pl is None:
        return None, None
    return pl.id, control.send_text(app_state, pl.id, text, kind="request")


async def _control_event(
    app_state: Any,
    factory_id: str,
    kind: str,
    task: FactoryTask,
    by: str,
    actor: Actor,
    pl_id: str | None,
    mail_id: str | None,
    diff: str,
    summary_text: str,
) -> None:
    await events.emit(
        app_state,
        factory_id,
        kind,
        severity="notice",
        project=task.project_slug,
        to_id=pl_id,
        subject=task_subject(task),
        summary=summary_text,
        data={
            "task_id": task.id,
            "ref": task.ref,
            "from": by,
            "diff": diff,
            "recipients": [norm_id(pl_id)] if pl_id else [],
            "mail_ids": {pl_id: mail_id} if pl_id and mail_id else {},
            **actor.event_data(),
            "by": by,
        },
    )


async def add_task(
    app_state: Any, factory_id: str, body: TaskAddIn, actor: Actor, by: str
) -> dict[str, Any]:
    """
    A person adds a task; its PL gets ``[Factory control · TASK ADD · by …]`` with the card.

    :returns: ``{task, mail_id, warnings}``.
    :raises CockpitError: 409 ``task_exists``, plus the graph validation errors.
    """
    deps = factory_deps(app_state)
    slug = body.project.strip()

    def run() -> tuple[Write, FactoryTask]:
        numbers = _numbers(deps, factory_id, slug)
        if deps.factory_store.get_task_by_key(factory_id, slug, body.key) is not None:
            raise CockpitError(409, "task_exists", f"{slug} already has a task {body.key!r}")
        fields = _row_fields(
            body, None, numbers, budget=body.budget, budget_set="budget" in body.model_fields_set
        )
        write = apply_fields(deps, factory_id, slug, [(body.key, fields)], actor)
        return write, write.ctx.by_key()[body.key]

    async with lock(factory_id):
        write, task = await asyncio.to_thread(run)
    await emit_all(app_state, factory_id, write.events)
    analysis = analyze(write.ctx)
    diff = task_diff(None, task)
    text = task_add_text(by, task, diff, analysis.waiting.get(task.key, []))
    pl_id, mail_id = await _mail_pl(app_state, factory_id, slug, text)
    await _control_event(
        app_state,
        factory_id,
        "control.task_add",
        task,
        by,
        actor,
        pl_id,
        mail_id,
        diff,
        f"{by} added {task.ref} {task.key} to {slug}",
    )
    return {
        "task": task_view(task, analysis=analysis, prs=write.ctx.prs),
        "mail_id": mail_id,
        "warnings": analysis.warnings,
    }


async def edit_task(
    app_state: Any, factory_id: str, ref: str, body: TaskEditIn, actor: Actor, by: str
) -> dict[str, Any]:
    """
    A person edits a task (priority, deps, budget, card, or cancels it).

    Its PL gets ``[Factory control · TASK EDIT · by …]`` with the diff; an
    edit that changes nothing sends nothing.

    :returns: ``{task, changed, mail_id, warnings}``.
    """
    deps = factory_deps(app_state)

    def run() -> tuple[FactoryTask, FactoryTask, Write | None]:
        task = resolve_task_ref(deps, factory_id, ref)
        numbers = _numbers(deps, factory_id, task.project_slug)
        fields = _row_fields(
            body, task, numbers, budget=body.budget, budget_set="budget" in body.model_fields_set
        )
        if task.kind == "adhoc" and "kind" not in body.model_fields_set:
            fields.pop("kind", None)
        write: Write | None = None
        if fields:
            write = apply_fields(deps, factory_id, task.project_slug, [(task.key, fields)], actor)
        after = deps.factory_store.get_task(task.id) or task
        if body.cancel and after.state != "cancelled":
            after, event = move(deps, after, "cancelled", actor, reason="cancelled by a person")
            if write is None:
                write = Write(ctx=load_ctx(deps, factory_id, task.project_slug), events=[])
            if event is not None:
                write.events.append(event)
            write.ctx = load_ctx(deps, factory_id, task.project_slug)
            write.events += refresh_states(deps, write.ctx)
        return task, after, write

    async with lock(factory_id):
        before, after, write = await asyncio.to_thread(run)
    if write is not None:
        await emit_all(app_state, factory_id, write.events)
    ctx = (
        write.ctx
        if write is not None
        else await asyncio.to_thread(load_ctx, deps, factory_id, before.project_slug)
    )
    analysis = analyze(ctx)
    diff = task_diff(before, after)
    view = task_view(after, analysis=analysis, prs=ctx.prs)
    if not diff:
        return {"task": view, "changed": False, "mail_id": None, "warnings": analysis.warnings}
    pl_id, mail_id = await _mail_pl(
        app_state, factory_id, after.project_slug, task_edit_text(by, after, diff)
    )
    await _control_event(
        app_state,
        factory_id,
        "control.task_edit",
        after,
        by,
        actor,
        pl_id,
        mail_id,
        diff,
        f"{by} edited {after.ref} {after.key} of {after.project_slug}",
    )
    return {"task": view, "changed": True, "mail_id": mail_id, "warnings": analysis.warnings}


def reset() -> None:
    """Forget cached observations and locks (tests)."""
    _observed.clear()
    _locks.clear()
    _bound_first.clear()


__all__ = [
    "CLOSING_OPS",
    "DEFAULT_ATTEMPTS",
    "DEFAULT_TASK_USD",
    "MAX_UPSERT",
    "Analysis",
    "ProjectCtx",
    "TaskAddIn",
    "TaskEditIn",
    "TaskEvent",
    "TaskIn",
    "add_task",
    "analyze",
    "budget_left",
    "card_text",
    "close_task",
    "dispatch_item",
    "edit_task",
    "emit_all",
    "engine_mode",
    "get_view",
    "hub_files",
    "list_view",
    "load_ctx",
    "load_factory",
    "lock",
    "move",
    "node_of",
    "note_bound_attempt",
    "project_dep_met",
    "ready_view",
    "refresh_ready",
    "refresh_states",
    "report_task",
    "resolve_task_ref",
    "role_rules",
    "snapshot_fields",
    "start_task",
    "summary",
    "task_budget",
    "task_diff",
    "task_subject",
    "task_view",
    "title_to_use",
    "upsert_tasks",
]
