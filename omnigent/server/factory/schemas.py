"""The cockpit contract: pydantic response models for the ``/v1/factory`` human API.

``web/src/lib/factoryApi.ts`` mirrors these types and
``web/src/components/factory/__fixtures__/snapshot.liveRun.json`` validates
against :class:`FactorySnapshot`, so change both sides together. Models forbid
unknown keys: snapshot builders map store rows field by field.

Times are epoch seconds, except :attr:`EventView.ts` (epoch milliseconds).
Money is USD; ``None`` means unpriced or unknown, never zero.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Stage = Literal["queued", "planning", "building", "review", "verify", "ready", "merged", "done"]
AgentStage = Literal["planning", "building", "review", "verify", "ready", "merged"]
Overlay = Literal["paused", "blocked", "failed", "pr_closed"]
Role = Literal["lead", "project_lead", "ic", "env_keeper", "reviewer", "demo"]
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
DecisionSource = Literal["agent", "relaunch_failed", "blocked_approval", "pr_closed", "storm"]
DecisionStatus = Literal["open", "answered", "withdrawn", "expired"]
Severity = Literal["info", "notice", "warning", "critical"]


class _View(BaseModel):
    model_config = ConfigDict(extra="forbid")


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


# ── Factory header and health ──────────────────────────────────


class NextBeatView(_View):
    """When a member's next heartbeat is due (quiet beats may delay it)."""

    session_id: str
    title: str
    at: int


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
    mergeable: str | None = None
    files: list[str] = Field(default_factory=list)
    protected: list[str] = Field(default_factory=list)


class GatesView(_View):
    """
    Gate states computed from GitHub by the server.

    ``demo`` is ``None`` when the project has no previews.
    """

    draft: GateState = "unknown"
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


# ── Projects ───────────────────────────────────────────────────


class CharterPreview(_View):
    enabled: bool
    slots: int = Field(ge=1, le=3)
    tooling_ref: str | None = None


class CharterView(_View):
    """A project charter (``charter.normalize_charter`` shape)."""

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


# ── Decisions and events ───────────────────────────────────────


class DecisionOptionView(_View):
    """
    One answer option.

    :param action: A server action id (``restart``, ``relaunch``, ...) run on answer.
    :param href: A link-only option (e.g. ``/inbox``).
    :param target: What the action acts on, e.g. ``"member:<id>"`` or ``"project:<slug>"``.
    """

    id: str
    label: str
    description: str | None = None
    recommended: bool = False
    action: str | None = None
    href: str | None = None
    target: str | None = None


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


class FactorySnapshot(_View):
    """
    ``GET /v1/factory/factories/{fid}``: everything one cockpit page renders.

    :param decisions: Open decisions plus the last 10 answered.
    :param hidden_projects: Projects whose PL the viewer cannot read.
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


class FactorySummary(_View):
    """One card on the factory index (``GET /v1/factory/factories``)."""

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
    factory: FactoryRef
    decision: DecisionView


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
    """``GET /v1/factory/preflight``: can this user start a factory here?"""

    github: PreflightGithub
    agents: PreflightAgents
    sandbox_host: bool
    factory_enabled: bool


__all__ = [
    "DecisionView",
    "DemoListResponse",
    "DemoView",
    "EnvView",
    "EventPage",
    "EventView",
    "FactoryListResponse",
    "FactorySnapshot",
    "FactorySummary",
    "HealthView",
    "MemberRef",
    "MemberView",
    "OpenDecisionsResponse",
    "PrView",
    "Preflight",
    "PreviewView",
    "ProjectDemoView",
    "ProjectView",
]
