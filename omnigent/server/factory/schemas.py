"""The cockpit contract: pydantic response models for the ``/v1/factory`` human API.

``web/src/lib/factoryApi.ts`` mirrors these types and
``web/src/components/factory/__fixtures__/snapshot.liveRun.json`` validates
against :class:`FactorySnapshot` (``snapshot.round3.json`` against the round-3
views too), so change both sides together. Models forbid unknown keys:
snapshot builders map store rows field by field. Round-3 additions to the
snapshot are optional, so older servers and clients keep working.

Times are epoch seconds, except :attr:`EventView.ts` (epoch milliseconds).
Money is USD; ``None`` means unpriced or unknown, never zero.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Stage = Literal["queued", "planning", "building", "review", "verify", "ready", "merged", "done"]
AgentStage = Literal["planning", "building", "review", "verify", "ready", "merged"]
Overlay = Literal["paused", "blocked", "failed", "pr_closed", "cancelled", "merged", "waiting"]
Role = Literal["lead", "project_lead", "ic", "env_keeper", "reviewer", "demo", "planner"]
MemberState = Literal["active", "paused", "done"]
Cadence = Literal["demo", "normal"]
GateState = Literal["pass", "pending", "fail", "skip", "unknown"]
PrState = Literal["open", "merged", "closed"]
EnvState = Literal["starting", "ready", "degraded", "failed", "parked", "down"]
PreviewState = Literal[
    "deploying", "ready", "stale", "gone", "failed", "parking", "parked", "down"
]
ProbeHealth = Literal["ok", "degraded", "down", "unknown"]
DemoStatus = Literal["PASS", "PARTIAL", "FAIL", "ERROR"]
DemoStepKind = Literal["ui", "api", "check"]
DemoStepStatus = Literal["pass", "fail", "skipped"]
DemoFileKind = Literal[
    "video", "screenshot", "poster", "api", "har", "trace", "console", "script", "log", "other"
]
ProjectKind = Literal["work", "integration", "self"]
DecisionSource = Literal[
    "agent",
    "relaunch_failed",
    "blocked_approval",
    "pr_closed",
    "storm",
    "proposal",
    "loop",
    "access_request",
    "done_check",
]
DecisionStatus = Literal["open", "answered", "withdrawn", "expired"]
Severity = Literal["info", "notice", "warning", "critical"]

# Round 3.
PersonRole = Literal["viewer", "operator", "owner"]
ViewerRole = Literal["viewer", "operator", "owner", "creator"]
ProjectStatus = Literal["active", "cancelled", "merged", "done"]
DependsMode = Literal["merged", "ready"]
EngineMode = Literal["observe", "assist", "enforce"]
ActorKind = Literal["human", "agent", "server"]
ChatAuthorKind = Literal["human", "agent", "system"]
ChatCardType = Literal["decision", "proposal", "control", "pr", "demo", "loop", "issue", "system"]
ThreadKind = Literal["factory", "improvements", "project", "pr"]
ProposalKind = Literal["projects", "tuning", "rules", "code"]
ProposalStatus = Literal[
    "pending",
    "applied",
    "trial",
    "kept",
    "reverted",
    "rejected",
    "expired",
    "withdrawn",
    "refused",
    "undone",
    "awaiting_cosign",
]
ProposalApproval = Literal["auto", "operator", "owner"]
ProposalClass = Literal["A", "B", "human"]
TaskKind = Literal["implement", "test", "docs", "spike", "ops", "adhoc"]
TaskState = Literal[
    "planned", "ready", "running", "review", "blocked", "done", "failed", "cancelled"
]
TaskHarness = Literal["ic", "ic_codex"]
LoopKind = Literal[
    "implement", "review", "verify", "demo", "ci", "claim", "plan", "intake", "improve"
]
LoopState = Literal[
    "running",
    "converged",
    "stalled",
    "thrashing",
    "exhausted",
    "escalated",
    "cancelled",
    "superseded",
]
IssueClass = Literal["incident", "finding", "retro"]
IssueSource = Literal["detector", "sentinel", "human", "agent", "server"]
IssueStatus = Literal["open", "acked", "snoozed", "resolved", "wontfix"]
SpanKind = Literal[
    "project",
    "stage",
    "pr",
    "pr_round",
    "gate",
    "ready_wait",
    "decision",
    "task",
    "loop",
    "loop_iter",
    "wait",
    "env",
    "preview",
    "demo",
    "incident",
    "intervention",
    "proposal",
    "chat_turn",
    "spawn",
    "turn",
    "tool",
]
SpanOutcome = Literal[
    "open", "ok", "fail", "abandoned", "superseded", "timeout", "cancelled", "exhausted"
]
DefinitionSource = Literal["seed", "human", "proposal", "revert"]
NowFlag = Literal["repeat", "quiet", "disconnected"]


class _View(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ClassView(_View):
    """A view whose ``class_`` attribute is the JSON key ``class``."""

    # populate_by_name and the by_alias dump default also hold on pydantic < 2.11,
    # which has neither validate_by_name nor serialize_by_alias.
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)

    def model_dump(self, *, by_alias: bool | None = True, **kwargs: Any) -> dict[str, Any]:
        return super().model_dump(by_alias=by_alias, **kwargs)

    def model_dump_json(self, *, by_alias: bool | None = True, **kwargs: Any) -> str:
        return super().model_dump_json(by_alias=by_alias, **kwargs)


# ── Round-3 building blocks (live "now", loop chips, summaries, viewer) ──


class NowState(_View):
    """
    What one agent is doing right now, as the server observed it.

    :param status: ``idle`` / ``running`` / ``waiting`` / ``failed``.
    :param tool_arg: The tool's main argument, redacted, ≤ 80 chars.
    :param tool_since: When the current tool call started (epoch seconds).
    :param ctx_pct: Context window use, only when the harness reports it.
    """

    session_id: str
    status: str
    turn_n: int | None = None
    tool: str | None = None
    tool_arg: str | None = Field(default=None, max_length=80)
    tool_since: int | None = None
    last_event_at: int | None = None
    model: str | None = None
    cost_usd: float | None = None
    usd_per_h: float | None = None
    ctx_pct: float | None = None
    flags: list[NowFlag] = Field(default_factory=list)


class LoopChipView(_View):
    """
    A compact loop badge on a PR, task or project row, e.g. ``implement 3/3``.

    :param limit: The iteration budget, when the loop has one.
    """

    id: str
    kind: LoopKind
    state: LoopState
    iteration: int
    limit: int | None = None


class TasksSummaryView(_View):
    """
    A project's work graph at a glance.

    :param critical_path_min: Remaining critical-path estimate in minutes.
    """

    total: int = 0
    done: int = 0
    ready: int = 0
    running: int = 0
    blocked: int = 0
    failed: int = 0
    critical_path_min: int | None = None


class LoopsSummaryView(_View):
    """Loop counts of a project by state."""

    running: int = 0
    converged: int = 0
    stalled: int = 0
    thrashing: int = 0
    exhausted: int = 0
    escalated: int = 0


class ViewerCan(_View):
    """Which levers the viewer may pull (the web disables the rest)."""

    post: bool = False
    answer: bool = False
    review: bool = False
    control: bool = False
    intervene: bool = False
    apply_projects: bool = False
    apply_tuning: bool = False
    restart: bool = False
    stop: bool = False
    invite: bool = False
    settings: bool = False
    terminal: bool = False
    start: bool = False


class ViewerBlock(_View):
    """
    The requesting user's place in the factory (computed per viewer, never cached).

    :param level: Session permission level on the root (1 read … 4 owner).
    """

    user_id: str | None = None
    role: ViewerRole
    level: int = Field(ge=0, le=4)
    is_creator: bool = False
    can: ViewerCan = Field(default_factory=ViewerCan)


class PresenceEntry(_View):
    """
    One human currently in the factory.

    :param location: ``overview`` / ``room:<thread>`` / ``project:<slug>`` /
        ``pr:<id>`` / ``agent:<sid>`` / ``graph`` / … .
    :param sessions: Member sessions the person has open.
    """

    user_id: str
    name: str | None = None
    location: str | None = None
    idle: bool = False
    since: int | None = None
    sessions: list[str] = Field(default_factory=list)


class CountsView(_View):
    """Header counters of the round-3 objects."""

    issues_open: int = 0
    proposals_pending: int = 0
    on_trial: int = 0


# ── Members ─────────────────────────────────────────────────────


class LaunchDriftView(_View):
    """
    A top-level member whose stored launch args differ from its spec's.

    :param fields: Differing flags, e.g. ``["permission_mode"]``.
    :param stored: Flag -> stored value (``None`` = absent), e.g. ``{"permission_mode": "auto"}``.
    :param spec: Flag -> spec value, e.g. ``{"permission_mode": "bypassPermissions"}``.
    """

    fields: list[str]
    stored: dict[str, str | None]
    spec: dict[str, str | None]


class MemberRef(_View):
    """A compact member chip (project PL and team strip)."""

    session_id: str
    title: str
    role: Role | None
    status: str
    harness: str | None = None
    running: bool = False
    health: str | None = None
    summary: str | None = None
    last_activity_at: int | None = None
    cost_usd: float | None = None
    has_launch_drift: bool = False
    url: str


class MemberView(_View):
    """
    One factory member: a top-level lead/PL or a sub-agent.

    :param status: Live activity: ``idle`` / ``running`` / ``waiting`` / ``failed``.
    :param state: Factory state label of a top-level member, else ``None``.
    """

    session_id: str
    title: str
    role: Role | None
    project: str | None = None
    parent_session_id: str | None = None
    is_top_level: bool
    status: str
    state: MemberState | None = None
    health: str | None = None
    summary: str | None = None
    harness: str | None = None
    running: bool = False
    last_activity_at: int | None = None
    heartbeat_s: int | None = None
    cost_usd: float | None = None
    launch_drift: LaunchDriftView | None = None
    url: str
    now: NowState | None = None


# ── Factory header and health ──────────────────────────────────


class NextBeatView(_View):
    """When a member's next heartbeat is due (quiet beats may delay it)."""

    session_id: str
    title: str
    at: int


class FactoryBaseView(_View):
    """
    The factory's default base and the live env at its head (``base-env``).

    :param head_sha: The base head the server last saw on GitHub, or ``None``.
    :param moved_at: When the server saw it move (``None`` before the first move).
    :param env: The integration project's env, when there is one.
    :param env_project: Slug of the integration project, e.g. ``"base-env"``.
    """

    repo: str
    branch: str
    head_sha: str | None = None
    moved_at: int | None = None
    env: EnvView | None = None
    env_project: str | None = None


class FactoryView(_View):
    """The snapshot header."""

    id: str
    name: str
    state: MemberState
    cadence: Cadence
    started_at: int | None
    root_session_id: str
    folder_project_id: str | None = None
    elapsed_s: int | None = None
    cost_usd: float | None = None
    next_beats: list[NextBeatView] = Field(default_factory=list)
    base: FactoryBaseView | None = None
    url: str


class SupervisorHealth(_View):
    last_tick_at: int | None = None
    ok: bool | None = None


class MailHealth(_View):
    queued: int = 0
    oldest_queued_s: int | None = None


class GithubHealth(_View):
    state: Literal["ok", "degraded", "disconnected", "off"]
    last_poll_at: int | None = None
    error: str | None = None
    rate_limit_remaining: int | None = None


class ProbesHealth(_View):
    state: Literal["ok", "unreachable", "off"]
    last_run_at: int | None = None


class HealthView(_View):
    """The cockpit health strip: the factory services, never agent claims."""

    supervisor: SupervisorHealth
    mail: MailHealth
    github: GithubHealth
    probes: ProbesHealth
    drifted_members: int = 0
    demo_storage_bytes: int = 0


# ── Envs and previews ──────────────────────────────────────────


class EnvUrls(_View):
    app: str | None = None
    api: str | None = None
    minio: str | None = None
    tilt_ui: str | None = None


class ResourceView(_View):
    """One Tilt resource (``ready``/``restarts`` from previews, ``status`` from dev envs)."""

    name: str
    ready: bool | None = None
    restarts: int | None = None
    status: str | None = None
    detail: str | None = None


class ProbeTarget(_View):
    code: int | None = None
    ms: int | None = None
    sha: str | None = None


class ProbeView(_View):
    """What the server's prober saw (never the agent's report)."""

    app: ProbeTarget | None = None
    api: ProbeTarget | None = None
    health: ProbeHealth = "unknown"
    fails: int = 0
    at: int | None = None


class DeployedRef(_View):
    sha: str | None = None
    ref: str | None = None


class EnvReported(_View):
    """The dev env as env_keeper reported it."""

    state: EnvState
    env_id: str | None = None
    urls: EnvUrls = Field(default_factory=EnvUrls)
    deployed: DeployedRef = Field(default_factory=DeployedRef)
    resources: list[ResourceView] = Field(default_factory=list)
    note: str | None = None
    at: int | None = None


class EnvView(_View):
    """A project's long-running dev env (tracks the base branch, for ICs)."""

    reported: EnvReported | None = None
    probe: ProbeView | None = None


class PreviewDeployed(_View):
    """
    What was deployed into the preview.

    :param tooling_overlay: Sha of the preview tooling overlaid on the PR head, if any.
    """

    sha: str | None = None
    ref: str | None = None
    at: int | None = None
    tooling_overlay: str | None = None


class PreviewObserved(_View):
    """What ``GET <app>/__factory/preview`` returned to the server's prober."""

    sha: str | None = None
    at: int | None = None
    reachable: bool | None = None
    failures: int = 0


class PreviewView(_View):
    """
    The factory preview of one PR: a namespace deployed at the PR head.

    :param name: ``p<PR number>``.
    :param stable_url: Omnigent redirect that always points at the current app URL.
    """

    name: str
    env_id: str | None = None
    namespace: str | None = None
    urls: EnvUrls = Field(default_factory=EnvUrls)
    state: PreviewState
    deployed: PreviewDeployed = Field(default_factory=PreviewDeployed)
    observed: PreviewObserved | None = None
    resources: list[ResourceView] = Field(default_factory=list)
    stable_url: str | None = None
    note: str | None = None
    slot: int | None = None
    reported_at: int | None = None
    probe: ProbeView | None = None


# ── Demos ──────────────────────────────────────────────────────


class DemoStepView(_View):
    """
    One step of a demo run.

    :param api_calls: Indexes into the demo's ``api.json`` file.
    """

    n: int
    title: str
    kind: DemoStepKind
    status: DemoStepStatus
    screenshot_file_id: str | None = None
    duration_ms: int | None = None
    note: str | None = None
    api_calls: list[int] | None = None


class DemoFileView(_View):
    """
    One demo artifact, served by ``url`` (Range-capable for video).

    ``bytes`` and ``size`` carry the same byte count.
    """

    id: str
    name: str
    kind: DemoFileKind
    caption: str | None = None
    content_type: str
    bytes: int
    size: int
    url: str
    step: int | None = None


class DemoView(_View):
    """
    A recorded demo of a PR head.

    :param is_current_head: Recorded at the PR's current head sha.
    :param plan_source: ``pr-body`` (the PR's Demo plan) or ``derived``.
    :param by: Title of the session that published it.
    """

    id: str
    status: DemoStatus
    head_sha: str
    is_current_head: bool
    title: str
    summary: str
    plan_source: str | None = None
    duration_s: float | None = None
    console_errors: int | None = None
    steps: list[DemoStepView] = Field(default_factory=list)
    files: list[DemoFileView] = Field(default_factory=list)
    video_file_id: str | None = None
    poster_file_id: str | None = None
    preview_url: str | None = None
    created_at: int
    by: str | None = None


class ProjectDemoView(DemoView):
    """
    One demo in the demos list: the demo and the PR it records.

    :param pr_id: The tracked PR's id, when the demo was linked to one.
    :param project_slug: The project the demo belongs to (``None`` when unknown).
    """

    pr_number: int
    pr_id: str | None = None
    repo: str
    project_slug: str | None = None


class DemoListResponse(_View):
    """``GET /v1/factory/factories/{fid}/demos``: newest first."""

    data: list[ProjectDemoView] = Field(default_factory=list)


# ── PRs ────────────────────────────────────────────────────────


class ChecksEvidence(_View):
    passing: int = 0
    failing: int = 0
    pending: int = 0
    total: int = 0
    url: str | None = None
    failing_names: list[str] = Field(default_factory=list)


class ReviewEvidence(_View):
    verdict: str | None = None
    sha: str | None = None
    url: str | None = None
    at: int | None = None
    by: str | None = None


class VerifyEvidence(_View):
    result: str | None = None
    sha: str | None = None
    env: str | None = None
    url: str | None = None
    at: int | None = None


class BaseEvidence(_View):
    """The branch a PR targets and the one its project expects."""

    actual: str | None = None
    expected: str | None = None


class DemoEvidence(_View):
    status: DemoStatus | None = None
    sha: str | None = None
    demo_id: str | None = None
    url: str | None = None
    at: int | None = None


class GateEvidence(_View):
    checks: ChecksEvidence | None = None
    review: ReviewEvidence | None = None
    verify: VerifyEvidence | None = None
    demo: DemoEvidence | None = None
    base: BaseEvidence | None = None
    mergeable: str | None = None
    files: list[str] = Field(default_factory=list)
    protected: list[str] = Field(default_factory=list)


class GatesView(_View):
    """
    Gate states computed from GitHub by the server.

    ``demo`` is ``None`` when the project has no previews.
    """

    draft: GateState = "unknown"
    base: GateState = "unknown"
    checks: GateState = "unknown"
    review: GateState = "unknown"
    verify: GateState = "unknown"
    mergeable: GateState = "unknown"
    protected_paths: GateState = "unknown"
    demo: GateState | None = None
    reasons: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    evidence: GateEvidence = Field(default_factory=GateEvidence)


class SentinelView(_View):
    verdict: Literal["verified", "not_verified"]
    head_sha: str | None = None
    note: str | None = None
    by: str | None = None
    at: int | None = None


class HumanReviewView(_View):
    verdict: Literal["approved", "changes_requested"]
    text: str | None = None
    by: str
    at: int
    head_sha: str | None = None
    source: Literal["cockpit", "github"]


class MergedView(_View):
    by: str | None = None
    sha: str | None = None
    at: int


class PrView(_View):
    """
    One tracked PR with its gates, preview and newest demo.

    :param older_demos: Demos recorded before :attr:`demo`.
    """

    id: str
    project_slug: str | None
    repo: str
    number: int
    url: str
    title: str
    task: str | None = None
    head_sha: str
    head_ref: str
    base_ref: str | None = None
    state: PrState
    is_draft: bool
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0
    gates: GatesView
    ready: bool
    ready_since: int | None = None
    sentinel: SentinelView | None = None
    human_review: HumanReviewView | None = None
    preview: PreviewView | None = None
    demo: DemoView | None = None
    older_demos: int = 0
    merged: MergedView | None = None
    closed_at: int | None = None
    last_polled_at: int | None = None
    poll_error: str | None = None
    source: Literal["tool", "discovered"] | None = None
    task_id: str | None = None
    task_ref: str | None = None
    opened_at: int | None = None
    first_ready_at: int | None = None
    loops: list[LoopChipView] | None = None


# ── Projects ───────────────────────────────────────────────────


class CharterPreview(_View):
    enabled: bool
    slots: int = Field(ge=1, le=3)
    tooling_ref: str | None = None


class CharterView(_View):
    """A project charter (``charter.normalize_charter`` shape)."""

    kind: ProjectKind = "work"
    goal: str
    dod: list[str] = Field(default_factory=list)
    scope_paths: list[str] = Field(default_factory=list)
    constraints: str | None = None
    env_needed: bool
    preview: CharterPreview
    preview_slots: int = Field(ge=1, le=3)
    demo_focus: str | None = None
    demo: str | None = None
    budget_ics: int
    version_path: str | None = None
    health_path: str | None = None


class ProvisioningView(_View):
    """The PL sandbox's provisioning step while the project is queued."""

    stage: str
    label: str


class ProjectView(_View):
    """One project card."""

    slug: str
    title: str
    repo: str | None
    base_branch: str | None
    priority: int = Field(ge=0, le=3)
    charter: CharterView | None
    stage: Stage
    stage_since: int
    overlay: Overlay | None = None
    overlay_reason: str | None = None
    agent_stage: AgentStage | None = None
    claim_mismatch: bool = False
    pl: MemberRef | None = None
    team: list[MemberRef] = Field(default_factory=list)
    env: EnvView | None = None
    pr_ids: list[str] = Field(default_factory=list)
    open_decision_ids: list[str] = Field(default_factory=list)
    cost_usd: float | None = None
    elapsed_s: int | None = None
    provisioning: ProvisioningView | None = None
    keep_previews: bool = True
    paused_by: str | None = None
    kind: ProjectKind | None = None
    status: ProjectStatus | None = None
    parent_slug: str | None = None
    closed_at: int | None = None
    depends_on: list[str] | None = None
    depends_mode: DependsMode | None = None
    human_owner: str | None = None
    tasks_summary: TasksSummaryView | None = None
    loops_summary: LoopsSummaryView | None = None
    def_version: int | None = None


# ── Decisions and events ───────────────────────────────────────


class DecisionOptionView(_View):
    """
    One answer option.

    :param action: A server action id (``restart``, ``relaunch``, ...) run on answer.
    :param href: A link-only option (e.g. ``/inbox``).
    :param target: What the action acts on, e.g. ``"member:<id>"``, ``"project:<slug>"``
        or ``"person:<user id>"``.
    :param role: The roster role a ``grant_role`` option gives.
    """

    id: str
    label: str
    description: str | None = None
    recommended: bool = False
    action: str | None = None
    href: str | None = None
    target: str | None = None
    role: PersonRole | None = None


class DecisionView(_View):
    """A question for the human, shown as ``ref`` (``D-<number>``)."""

    id: str
    ref: str
    number: int
    factory_id: str
    project_slug: str | None = None
    asked_by_session_id: str | None = None
    asked_by_title: str | None = None
    asked_by_role: Role | None = None
    source: DecisionSource
    blocking: bool
    question: str
    context: str | None = None
    options: list[DecisionOptionView] = Field(default_factory=list)
    allow_text: bool = True
    status: DecisionStatus
    answer_option_id: str | None = None
    answer_text: str | None = None
    answered_by: str | None = None
    answered_at: int | None = None
    delivered_mail_id: str | None = None
    delivered_at: int | None = None
    delivered_to_title: str | None = None
    created_at: int
    updated_at: int
    assignee: str | None = None
    claimed_by: str | None = None
    claimed_at: int | None = None
    thread: str | None = None
    subject: str | None = None


class EventView(_View):
    """One activity row. ``ts`` is epoch milliseconds."""

    id: str
    ts: int
    kind: str
    severity: Severity
    project_slug: str | None = None
    from_id: str | None = None
    to_id: str | None = None
    from_title: str | None = None
    to_title: str | None = None
    subject: str | None = None
    summary: str
    data: dict[str, Any] | None = None


# ── Top level ──────────────────────────────────────────────────


class TotalsView(_View):
    cost_usd: float | None = None
    prs_open: int = 0
    prs_ready: int = 0
    prs_merged: int = 0
    decisions_open: int = 0
    usd_per_h: float | None = None


class FactorySnapshot(_View):
    """
    ``GET /v1/factory/factories/{fid}``: everything one cockpit page renders.

    :param decisions: Open decisions plus the last 10 answered.
    :param hidden_projects: Projects whose PL the viewer cannot read.
    :param viewer: The requesting user's role and levers.
    :param def_version: The factory definition version in force.
    """

    factory: FactoryView
    health: HealthView
    projects: list[ProjectView]
    prs: list[PrView]
    decisions: list[DecisionView]
    members: list[MemberView]
    hidden_projects: int = 0
    totals: TotalsView
    server_time: int
    viewer: ViewerBlock | None = None
    people_count: int | None = None
    presence: list[PresenceEntry] | None = None
    counts: CountsView | None = None
    def_version: int | None = None
    engine_mode: EngineMode | None = None


class FactorySummary(_View):
    """
    One card on the factory index (``GET /v1/factory/factories``).

    :param stage_counts: Work projects per stage (the live base env is left out).
    :param projects_total: Work projects, the same set ``stage_counts`` counts.
    """

    id: str
    name: str
    state: MemberState
    cadence: Cadence
    started_at: int | None = None
    root_session_id: str
    elapsed_s: int | None = None
    cost_usd: float | None = None
    stage_counts: dict[Stage, int] = Field(default_factory=dict)
    projects_total: int = 0
    decisions_open: int = 0
    prs_ready: int = 0
    members_total: int = 0
    members_running: int = 0
    drifted_members: int = 0
    url: str


class FactoryListResponse(_View):
    data: list[FactorySummary]


class EventPage(_View):
    """``GET /v1/factory/factories/{fid}/events``."""

    data: list[EventView]
    has_more: bool
    next_before: int | None = None
    """``before`` of the next page (past any hidden events the scan skipped)."""
    next_before_id: str | None = None
    """``before_id`` of the next page."""


class FactoryRef(_View):
    id: str
    name: str


class OpenDecisionItem(_View):
    """
    One decision in the cross-factory list.

    :param mine: Assigned to the caller, or to a role they hold.
    """

    factory: FactoryRef
    decision: DecisionView
    mine: bool | None = None


class OpenDecisionsResponse(_View):
    """``GET /v1/factory/decisions``."""

    data: list[OpenDecisionItem] = Field(default_factory=list)
    count: int


class PreflightGithub(_View):
    connected: bool
    login: str | None = None


class PreflightAgents(_View):
    factory_lead: bool
    factory_pl: bool


class Preflight(_View):
    """
    ``GET /v1/factory/preflight``: can this user start a factory here?

    :param default_repo: The server's default repo for new factories, e.g. ``"caffeinelabs/app"``.
    :param default_base: Its default base branch, e.g. ``"temp-main"``.
    :param max_projects: Projects one factory can run (one PL pod each).
    """

    github: PreflightGithub
    agents: PreflightAgents
    sandbox_host: bool
    factory_enabled: bool
    default_repo: str | None = None
    default_base: str | None = None
    max_projects: int = 10


# ── Round 3: people, Room, proposals, work graph, loops, issues, telemetry ──


class PersonView(_View):
    """
    One human on the factory roster.

    :param name: Display name, when the user store has one.
    :param present: In the factory right now (presence heartbeat).
    """

    user_id: str
    name: str | None = None
    role: PersonRole
    is_creator: bool = False
    invited_by: str | None = None
    invited_at: int
    last_seen_at: int | None = None
    present: bool = False
    location: str | None = None
    idle: bool = False
    notify: dict[str, Any] | None = None


class ChatCardView(_View):
    """
    A structured object mirrored into a thread as a live card.

    :param ref: e.g. ``"D-7"``, ``"C-4"``, ``"!9630"`` or ``"INC-13"``.
    :param id: Row id of the object, when it has one.
    """

    type: ChatCardType
    ref: str | None = None
    id: str | None = None


class RoutedView(_View):
    """Delivery of one message to one agent."""

    session_id: str
    title: str | None = None
    mail_id: str | None = None
    delivered_at: int | None = None
    replied_at: int | None = None


class ChatMessageView(_View):
    """
    One Room message.

    :param seq: Per-factory sequence (shared by every thread).
    :param author: User id, session id, or ``"server"``.
    :param auto_reply: The server posted the agent's turn output for it.
    """

    id: str
    factory_id: str
    thread: str
    seq: int
    author_kind: ChatAuthorKind
    author: str
    author_title: str | None = None
    author_role: str | None = None
    body: str
    mentions: list[str] = Field(default_factory=list)
    refs: list[str] = Field(default_factory=list)
    reply_to: str | None = None
    card: ChatCardView | None = None
    routed: list[RoutedView] = Field(default_factory=list)
    auto_reply: bool = False
    created_at: int


class ThreadView(_View):
    """
    One Room thread.

    :param key: ``factory``, ``improvements``, ``project:<slug>`` or ``pr:<id>``.
    :param title: Display name, e.g. ``"#db-version"`` or ``"!9631"``.
    :param responder_session_id: The agent that answers by default.
    """

    key: str
    kind: ThreadKind
    title: str
    project_slug: str | None = None
    pr_id: str | None = None
    pr_number: int | None = None
    responder_session_id: str | None = None
    responder_title: str | None = None
    last_seq: int = 0
    last_at: int | None = None
    messages: int = 0
    unread: int = 0


class ExpectedEffectView(_View):
    """The metric a proposal expects to move."""

    metric: str
    direction: Literal["up", "down"]
    delta: float | None = None


class ProposalView(_ClassView):
    """
    A proposed change, ``C-<number>``: a project plan or a definition change.

    :param class_: Tier-1 approval class (JSON key ``class``).
    :param ops: Project ops or definition patches ``{op, path, value}``.
    :param analysis: The planner's analysis (cohesion, hub files, size estimates).
    :param evidence: Ids the proposal cites, e.g. ``["F-9", "D-2"]``.
    """

    id: str
    ref: str
    number: int
    factory_id: str
    kind: ProposalKind
    status: ProposalStatus
    approval: ProposalApproval
    class_: ProposalClass | None = Field(default=None, alias="class")
    title: str
    ops: list[dict[str, Any]] = Field(default_factory=list)
    before: Any = None
    after: Any = None
    rationale: str = ""
    analysis: Any = None
    evidence: list[Any] = Field(default_factory=list)
    expected_effect: ExpectedEffectView | None = None
    warnings: list[str] = Field(default_factory=list)
    author_kind: ActorKind
    author: str
    author_title: str | None = None
    author_role: str | None = None
    on_behalf_of: str | None = None
    cosigned_by: str | None = None
    thread: str | None = None
    reply_to_chat: str | None = None
    decision_id: str | None = None
    decision_ref: str | None = None
    based_on: dict[str, Any] | None = None
    trial_until: int | None = None
    undo_until: int | None = None
    result: dict[str, Any] | None = None
    def_version_from: int | None = None
    def_version_to: int | None = None
    decided_by: str | None = None
    decided_at: int | None = None
    applied_at: int | None = None
    reverted_at: int | None = None
    reverted_by: str | None = None
    created_at: int
    updated_at: int


class TaskCardView(_View):
    """
    What a worker is told to do.

    :param acceptance: Commands the PL runs verbatim to accept the work.
    """

    text: str | None = None
    acceptance: list[str] = Field(default_factory=list)
    env_checks: list[str] = Field(default_factory=list)
    preview: bool | None = None
    demo_focus: str | None = None
    harness: TaskHarness | None = None


class TaskBudgetView(_View):
    attempts: int | None = None
    usd: float | None = None


class TaskView(_View):
    """
    One node of the work graph, ``T-<number>``.

    :param key: Branch suffix (``factory/<slug>/<key>``).
    :param deps: Task keys, ``project:<slug>`` and ``decision:D-n``.
    :param wave: Layer of the ready-order (1 = startable now), when computed.
    :param critical: On the project's critical path.
    :param waiting_on: Unmet dependencies.
    """

    id: str
    ref: str
    number: int
    project_slug: str
    key: str
    title: str
    kind: TaskKind
    state: TaskState
    card: TaskCardView = Field(default_factory=TaskCardView)
    deps: list[str] = Field(default_factory=list)
    scope: list[str] = Field(default_factory=list)
    priority: int = Field(default=2, ge=0, le=3)
    estimate_min: int | None = None
    vendor: str | None = None
    session_ids: list[str] = Field(default_factory=list)
    pr_id: str | None = None
    pr_number: int | None = None
    attempts: int = 0
    budget: TaskBudgetView | None = None
    cost_usd: float | None = None
    created_by: str
    created_via: ActorKind
    def_version: int | None = None
    started_at: int | None = None
    closed_at: int | None = None
    created_at: int
    updated_at: int
    wave: int | None = None
    critical: bool = False
    waiting_on: list[str] = Field(default_factory=list)
    loop: LoopChipView | None = None


class LoopSpentView(_View):
    """
    What a loop has used so far.

    :param per_head: Iterations per head sha (verify/demo loops).
    """

    attempts: int = 0
    usd: float | None = None
    wall_s: int | None = None
    per_head: dict[str, int] = Field(default_factory=dict)
    env_retries: int = 0


class LoopView(_View):
    """
    One loop (implement, review, verify, demo, ci, claim, plan, intake, improve).

    :param subject: e.g. ``"task:<id>"`` or ``"pr:<id>@<sha7>"``.
    :param limit: The iteration budget from :attr:`spec`, when it has one.
    :param spec: The resolved budgets the loop started with.
    """

    id: str
    kind: LoopKind
    subject: str
    project_slug: str | None = None
    task_id: str | None = None
    task_ref: str | None = None
    pr_id: str | None = None
    pr_number: int | None = None
    state: LoopState
    iteration: int = 0
    limit: int | None = None
    spec: dict[str, Any] = Field(default_factory=dict)
    spent: LoopSpentView = Field(default_factory=LoopSpentView)
    last_verdict: str | None = None
    last_reason: str | None = None
    decision_id: str | None = None
    decision_ref: str | None = None
    def_version: int | None = None
    started_at: int
    updated_at: int
    ended_at: int | None = None


class IssueView(_ClassView):
    """
    An incident (``INC-n``), finding (``F-n``) or retro.

    :param class_: JSON key ``class``.
    :param ref: ``None`` for retros.
    :param target: e.g. ``"member:<id>"``, ``"pr:<id>"`` or ``"project:<slug>"``.
    :param runbook: Action ids the incident page offers, e.g. ``["interrupt", "redispatch"]``.
    """

    id: str
    ref: str | None = None
    class_: IssueClass = Field(alias="class")
    number: int
    rule: str
    category: str | None = None
    severity: Severity
    source: IssueSource
    project_slug: str | None = None
    target: str
    target_title: str | None = None
    summary: str
    body: str | None = None
    evidence: Any = None
    status: IssueStatus
    assignee: str | None = None
    snoozed_until: int | None = None
    acked_by: str | None = None
    resolved_by: str | None = None
    count: int = 1
    first_seen: int
    last_seen: int
    resolved_at: int | None = None
    proposal_id: str | None = None
    proposal_ref: str | None = None
    def_version: int | None = None
    runbook: list[str] = Field(default_factory=list)


class SpanView(_View):
    """One span; ``started_at`` and ``ended_at`` are epoch milliseconds."""

    id: str
    trace_key: str
    parent_id: str | None = None
    kind: SpanKind
    name: str
    project_slug: str | None = None
    pr_id: str | None = None
    task_id: str | None = None
    loop_id: str | None = None
    head_sha: str | None = None
    session_id: str | None = None
    role: str | None = None
    agent_name: str | None = None
    actor: str | None = None
    started_at: int
    ended_at: int | None = None
    outcome: SpanOutcome
    attempt: int | None = None
    cost_usd: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    version_id: str | None = None
    def_version: int | None = None
    attrs: dict[str, Any] | None = None


class DefinitionView(_View):
    """
    One version of the factory's Tier-1 definition.

    :param pinned: The version the factory runs (``omnigent.factory.def_version``).
    :param doc: Engine, cadence, supervisor, mail, previews, caps, loops,
        budgets, autonomy, decisions, alerts, models and rules.
    """

    factory_id: str
    version: int
    parent_version: int | None = None
    hash: str
    created_by: str
    created_via: DefinitionSource
    proposal_id: str | None = None
    proposal_ref: str | None = None
    created_at: int
    pinned: bool = False
    doc: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "ChatMessageView",
    "CountsView",
    "DecisionView",
    "DefinitionView",
    "DemoListResponse",
    "DemoView",
    "EnvView",
    "EventPage",
    "EventView",
    "FactoryBaseView",
    "FactoryListResponse",
    "FactorySnapshot",
    "FactorySummary",
    "HealthView",
    "IssueView",
    "LoopView",
    "MemberRef",
    "MemberView",
    "NowState",
    "OpenDecisionsResponse",
    "PersonView",
    "PrView",
    "Preflight",
    "PresenceEntry",
    "PreviewView",
    "ProjectDemoView",
    "ProjectView",
    "ProposalView",
    "SpanView",
    "TaskView",
    "ThreadView",
    "ViewerBlock",
]
