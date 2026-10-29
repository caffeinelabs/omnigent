// Typed client for the factory cockpit's human API (`/v1/factory/...`).
//
// The types mirror `omnigent/server/factory/schemas.py`, the cockpit contract,
// field for field and in its snake_case wire shape: the live-run fixture
// (`components/factory/__fixtures__/snapshot.liveRun.json`) validates against
// the pydantic models and is imported here unchanged, so change both sides
// together. Times are epoch seconds except `EventView.ts` and `SpanView`
// times (milliseconds); a `null` cost means unpriced, never zero. Round-3
// additions to existing views are optional: older servers omit them.

import { authenticatedFetch } from "./identity";
import { ApiError, sessionCreateHeaders } from "./sessionsApi";

// ── Enums ──────────────────────────────────────────────────────

export type FactoryStage =
  "queued" | "planning" | "building" | "review" | "verify" | "ready" | "merged" | "done";

/** Every stage in pipeline order. */
export const FACTORY_STAGES: readonly FactoryStage[] = [
  "queued",
  "planning",
  "building",
  "review",
  "verify",
  "ready",
  "merged",
  "done",
];

export type AgentStage = "planning" | "building" | "review" | "verify" | "ready" | "merged";
/** `cancelled` / `merged` close a project; `waiting` holds it on its `depends_on`. */
export type FactoryOverlay =
  "paused" | "blocked" | "failed" | "pr_closed" | "cancelled" | "merged" | "waiting";
export type FactoryRole =
  "lead" | "project_lead" | "ic" | "env_keeper" | "reviewer" | "demo" | "planner";
export type MemberState = "active" | "paused" | "done";
export type FactoryCadence = "demo" | "normal";
export type GateState = "pass" | "pending" | "fail" | "skip" | "unknown";
export type PrState = "open" | "merged" | "closed";
export type EnvState = "starting" | "ready" | "degraded" | "failed" | "parked" | "down";
export type PreviewState =
  "deploying" | "ready" | "stale" | "gone" | "failed" | "parking" | "parked" | "down";
export type ProbeHealth = "ok" | "degraded" | "down" | "unknown";
export type DemoStatus = "PASS" | "PARTIAL" | "FAIL" | "ERROR";
export type DemoStepKind = "ui" | "api" | "check";
export type DemoStepStatus = "pass" | "fail" | "skipped";
export type DemoFileKind =
  | "video"
  | "screenshot"
  | "poster"
  | "api"
  | "har"
  | "trace"
  | "console"
  | "script"
  | "log"
  | "other";
/** `charter.kind`: product work, the live env at the base head, or factory self-work. */
export type ProjectKind = "work" | "integration" | "self";
export type DecisionSource =
  | "agent"
  | "relaunch_failed"
  | "blocked_approval"
  | "pr_closed"
  | "storm"
  | "proposal"
  | "loop"
  | "access_request"
  | "done_check";
export type DecisionStatus = "open" | "answered" | "withdrawn" | "expired";
export type EventSeverity = "info" | "notice" | "warning" | "critical";

// Round 3.
export type PersonRole = "viewer" | "operator" | "owner";
export type ViewerRole = PersonRole | "creator";
export type ProjectStatus = "active" | "cancelled" | "merged" | "done";
export type DependsMode = "merged" | "ready";
export type EngineMode = "observe" | "assist" | "enforce";
export type ActorKind = "human" | "agent" | "server";
export type ChatAuthorKind = "human" | "agent" | "system";
export type ChatCardType =
  "decision" | "proposal" | "control" | "pr" | "demo" | "loop" | "issue" | "system";
export type ThreadKind = "factory" | "improvements" | "project" | "pr";
export type ProposalKind = "projects" | "tuning" | "rules" | "code";
export type ProposalStatus =
  | "pending"
  | "applied"
  | "trial"
  | "kept"
  | "reverted"
  | "rejected"
  | "expired"
  | "withdrawn"
  | "refused"
  | "undone"
  | "awaiting_cosign";
export type ProposalApproval = "auto" | "operator" | "owner";
export type ProposalClass = "A" | "B" | "human";
export type TaskKind = "implement" | "test" | "docs" | "spike" | "ops" | "adhoc";
export type TaskState =
  "planned" | "ready" | "running" | "review" | "blocked" | "done" | "failed" | "cancelled";
export type TaskHarness = "ic" | "ic_codex";
export type LoopKind =
  "implement" | "review" | "verify" | "demo" | "ci" | "claim" | "plan" | "intake" | "improve";
export type LoopState =
  | "running"
  | "converged"
  | "stalled"
  | "thrashing"
  | "exhausted"
  | "escalated"
  | "cancelled"
  | "superseded";
export type IssueClass = "incident" | "finding" | "retro";
export type IssueSource = "detector" | "sentinel" | "human" | "agent" | "server";
export type IssueStatus = "open" | "acked" | "snoozed" | "resolved" | "wontfix";
export type SpanKind =
  | "project"
  | "stage"
  | "pr"
  | "pr_round"
  | "gate"
  | "ready_wait"
  | "decision"
  | "task"
  | "loop"
  | "loop_iter"
  | "wait"
  | "env"
  | "preview"
  | "demo"
  | "incident"
  | "intervention"
  | "proposal"
  | "chat_turn"
  | "spawn"
  | "turn"
  | "tool";
export type SpanOutcome =
  "open" | "ok" | "fail" | "abandoned" | "superseded" | "timeout" | "cancelled" | "exhausted";
export type DefinitionSource = "seed" | "human" | "proposal" | "revert";
export type NowFlag = "repeat" | "quiet" | "disconnected";

// ── Round-3 building blocks ────────────────────────────────────

/** What one agent is doing right now, as the server observed it. */
export interface NowState {
  session_id: string;
  /** `idle` / `running` / `waiting` / `failed`. */
  status: string;
  turn_n: number | null;
  tool: string | null;
  /** The tool's main argument, redacted, ≤ 80 chars. */
  tool_arg: string | null;
  tool_since: number | null;
  last_event_at: number | null;
  model: string | null;
  cost_usd: number | null;
  usd_per_h: number | null;
  /** Only when the harness reports it. */
  ctx_pct: number | null;
  flags: NowFlag[];
}

/** A compact loop badge, e.g. `implement 3/3`. */
export interface LoopChipView {
  id: string;
  kind: LoopKind;
  state: LoopState;
  iteration: number;
  limit: number | null;
}

export interface TasksSummaryView {
  total: number;
  done: number;
  ready: number;
  running: number;
  blocked: number;
  failed: number;
  critical_path_min: number | null;
}

export interface LoopsSummaryView {
  running: number;
  converged: number;
  stalled: number;
  thrashing: number;
  exhausted: number;
  escalated: number;
}

/** Which levers the viewer may pull; the cockpit disables the rest. */
export interface ViewerCan {
  post: boolean;
  answer: boolean;
  review: boolean;
  control: boolean;
  intervene: boolean;
  apply_projects: boolean;
  apply_tuning: boolean;
  restart: boolean;
  stop: boolean;
  invite: boolean;
  settings: boolean;
  terminal: boolean;
  start: boolean;
}

/** The requesting user's place in the factory (per viewer, never cached). */
export interface ViewerBlock {
  user_id: string | null;
  role: ViewerRole;
  /** Session permission level on the root (1 read … 4 owner). */
  level: number;
  is_creator: boolean;
  can: ViewerCan;
}

/** One human currently in the factory. */
export interface PresenceEntry {
  user_id: string;
  name: string | null;
  /** `overview` / `room:<thread>` / `project:<slug>` / `pr:<id>` / `agent:<sid>` / … */
  location: string | null;
  idle: boolean;
  since: number | null;
  /** Member sessions the person has open. */
  sessions: string[];
}

export interface CountsView {
  issues_open: number;
  proposals_pending: number;
  on_trial: number;
}

// ── Members ────────────────────────────────────────────────────

export interface LaunchDriftView {
  fields: string[];
  stored: Record<string, string | null>;
  spec: Record<string, string | null>;
}

/** A compact member chip (project PL and team strip). */
export interface MemberRef {
  session_id: string;
  title: string;
  role: FactoryRole | null;
  status: string;
  harness: string | null;
  running: boolean;
  health: string | null;
  summary: string | null;
  last_activity_at: number | null;
  cost_usd: number | null;
  has_launch_drift: boolean;
  url: string;
}

/** One factory member: a top-level lead/PL or a sub-agent. */
export interface MemberView {
  session_id: string;
  title: string;
  role: FactoryRole | null;
  project: string | null;
  parent_session_id: string | null;
  is_top_level: boolean;
  /** Live activity: `idle` / `running` / `waiting` / `failed`. */
  status: string;
  state: MemberState | null;
  health: string | null;
  summary: string | null;
  harness: string | null;
  running: boolean;
  last_activity_at: number | null;
  heartbeat_s: number | null;
  cost_usd: number | null;
  launch_drift: LaunchDriftView | null;
  url: string;
  now?: NowState | null;
}

// ── Factory header and health ──────────────────────────────────

export interface NextBeatView {
  session_id: string;
  title: string;
  at: number;
}

/** The factory's default base, the head the server last saw and the live env at it. */
export interface FactoryBaseView {
  repo: string;
  branch: string;
  head_sha: string | null;
  /** When the server saw the head move (null before the first move). */
  moved_at: number | null;
  /** The integration project's env (`base-env`), when there is one. */
  env: EnvView | null;
  env_project: string | null;
}

export interface FactoryView {
  id: string;
  name: string;
  state: MemberState;
  cadence: FactoryCadence;
  started_at: number | null;
  root_session_id: string;
  folder_project_id: string | null;
  elapsed_s: number | null;
  cost_usd: number | null;
  next_beats: NextBeatView[];
  /** Absent from servers before the temp-main lane. */
  base?: FactoryBaseView | null;
  url: string;
}

export interface HealthView {
  supervisor: { last_tick_at: number | null; ok: boolean | null };
  mail: { queued: number; oldest_queued_s: number | null };
  github: {
    state: "ok" | "degraded" | "disconnected" | "off";
    last_poll_at: number | null;
    error: string | null;
    rate_limit_remaining: number | null;
  };
  probes: { state: "ok" | "unreachable" | "off"; last_run_at: number | null };
  drifted_members: number;
  demo_storage_bytes: number;
}

// ── Envs and previews ──────────────────────────────────────────

export interface EnvUrls {
  app: string | null;
  api: string | null;
  minio: string | null;
  tilt_ui: string | null;
}

/** One Tilt resource (`ready`/`restarts` from previews, `status` from dev envs). */
export interface ResourceView {
  name: string;
  ready?: boolean | null;
  restarts?: number | null;
  status?: string | null;
  detail?: string | null;
}

export interface ProbeTarget {
  code?: number | null;
  ms?: number | null;
  sha?: string | null;
}

/** What the server's prober saw (never the agent's report). */
export interface ProbeView {
  app: ProbeTarget | null;
  api: ProbeTarget | null;
  health: ProbeHealth;
  fails: number;
  at: number | null;
}

export interface DeployedRef {
  sha: string | null;
  ref: string | null;
}

export interface EnvReported {
  state: EnvState;
  env_id: string | null;
  urls: EnvUrls;
  deployed: DeployedRef;
  resources: ResourceView[];
  note: string | null;
  at: number | null;
}

/** A project's long-running dev env (tracks the base branch, for ICs). */
export interface EnvView {
  reported: EnvReported | null;
  probe: ProbeView | null;
}

export interface PreviewDeployed {
  sha: string | null;
  ref: string | null;
  at: number | null;
  /** Sha of the preview tooling overlaid on the PR head, if any. */
  tooling_overlay: string | null;
}

/** What `GET <app>/__factory/preview` returned to the server's prober. */
export interface PreviewObserved {
  sha: string | null;
  at: number | null;
  reachable: boolean | null;
  failures: number;
}

/** The factory preview of one PR: a namespace deployed at the PR head. */
export interface PreviewView {
  /** `p<PR number>`. */
  name: string;
  env_id: string | null;
  namespace: string | null;
  urls: EnvUrls;
  state: PreviewState;
  deployed: PreviewDeployed;
  observed: PreviewObserved | null;
  resources: ResourceView[];
  /** Omnigent redirect that always points at the current app URL. */
  stable_url: string | null;
  note: string | null;
  slot: number | null;
  reported_at: number | null;
  probe: ProbeView | null;
}

// ── Demos ──────────────────────────────────────────────────────

export interface DemoStepView {
  n: number;
  title: string;
  kind: DemoStepKind;
  status: DemoStepStatus;
  screenshot_file_id: string | null;
  duration_ms: number | null;
  note: string | null;
  /** Indexes into the demo's `api.json` file. */
  api_calls: number[] | null;
}

export interface DemoFileView {
  id: string;
  name: string;
  kind: DemoFileKind;
  caption: string | null;
  content_type: string;
  bytes: number;
  size: number;
  url: string;
  step: number | null;
}

/** A recorded demo of a PR head. */
export interface DemoView {
  id: string;
  status: DemoStatus;
  head_sha: string;
  is_current_head: boolean;
  title: string;
  summary: string;
  plan_source: string | null;
  duration_s: number | null;
  console_errors: number | null;
  steps: DemoStepView[];
  files: DemoFileView[];
  video_file_id: string | null;
  poster_file_id: string | null;
  preview_url: string | null;
  created_at: number;
  by: string | null;
}

/** One demo in a factory's demos list: the demo and the PR it records. */
export interface ProjectDemoView extends DemoView {
  pr_number: number;
  /** The tracked PR's id, when the demo was linked to one. */
  pr_id: string | null;
  repo: string;
  project_slug: string | null;
}

/** `GET /v1/factory/factories/{fid}/demos`: newest first. */
export interface DemoListResponse {
  data: ProjectDemoView[];
}

/** One PR's demos on the project page: every run, newest first. */
export interface ProjectDemoGroupView {
  repo: string;
  pr_number: number;
  pr_id: string | null;
  /** The PR's current head as the board tracks it (`""` when untracked). */
  head_sha: string;
  runs: ProjectDemoView[];
}

/** `GET /v1/factory/factories/{fid}/projects/{slug}/demos`: groups by their newest run. */
export interface ProjectDemosResponse {
  data: ProjectDemoGroupView[];
  count: number;
}

/** Whether an invitee could sign in through the server's OIDC policy. */
export type Admission = "ok" | "blocked";

/** `GET /v1/factory/factories/{fid}/people`. */
export interface PeopleResponse {
  data: PersonView[];
  /** Whose GitHub and Databricks identity the agents use. */
  runs_as: { user_id: string; name: string } | null;
  count: number;
  viewer: ViewerBlock;
}

/** `PUT /v1/factory/factories/{fid}/people/{user}`. */
export interface PutPersonResponse {
  person: PersonView;
  created: boolean;
  previous_role: PersonRole | null;
  /** Members the role was granted on. */
  granted: number;
  admission: Admission;
}

/** `DELETE /v1/factory/factories/{fid}/people/{user}`. */
export interface RemovePersonResponse {
  removed: string;
  left: boolean;
  previous_role: PersonRole | null;
  revoked: number;
}

/** `GET /v1/factory/factories/{fid}/people/search`: someone to invite. */
export interface PersonSearchResult {
  user_id: string;
  name: string;
  /** A user the server already knows (else a typed email). */
  known: boolean;
  role: PersonRole | null;
  admission: Admission;
}

/** `POST /v1/factory/factories/{fid}/access-requests`. */
export interface AccessRequestResponse {
  decision: DecisionView;
  deduped: boolean;
}

/** `GET|POST /v1/factory/factories/{fid}/presence`. */
export interface PresenceResponse {
  data?: PresenceEntry[];
  present?: number;
  heartbeat_s: number;
  ttl_s: number;
}

/** `GET /v1/factory/factories/{fid}/spans`: oldest first. */
export interface SpanPage {
  data: SpanView[];
  has_more: boolean;
  next_since: number | null;
}

/** Filters of the demos list. */
export interface DemoFilters {
  project?: string;
  /** A PR number. */
  pr?: number;
  /** At most this many (server default 50, max 200). */
  limit?: number;
}

// ── PRs ────────────────────────────────────────────────────────

export interface GateEvidence {
  checks: {
    passing: number;
    failing: number;
    pending: number;
    total: number;
    url: string | null;
    failing_names: string[];
  } | null;
  review: {
    verdict: string | null;
    sha: string | null;
    url: string | null;
    at: number | null;
    by: string | null;
    /** The marker's `blocking=<n>`, when the reviewer gave one. */
    blocking?: number | null;
    /** The marker's `findings=<fp,…>` fingerprints, stable across rounds. */
    findings?: string[] | null;
  } | null;
  verify: {
    result: string | null;
    sha: string | null;
    env: string | null;
    url: string | null;
    at: number | null;
    /** `code:<what>` or `env:<what>`: how a FAIL is classified. */
    reason?: string | null;
  } | null;
  demo: {
    status: DemoStatus | null;
    sha: string | null;
    demo_id: string | null;
    url: string | null;
    at: number | null;
  } | null;
  /** The branch the PR targets and the one its project expects. */
  base?: { actual: string | null; expected: string | null } | null;
  mergeable: string | null;
  files: string[];
  protected: string[];
}

/**
 * The seven gates every PR is held to, in display order. `demo` is separate
 * because it is null when the project has no previews; when previews are on
 * it is a ready gate too (a PASS or PARTIAL demo of the head is required).
 * `base` is missing from snapshots of older servers.
 */
export const READY_GATES = [
  "draft",
  "base",
  "checks",
  "review",
  "verify",
  "mergeable",
  "protected_paths",
] as const;
export type ReadyGate = (typeof READY_GATES)[number];

/** Gate states computed from GitHub by the server; `demo` is null without previews. */
export interface GatesView extends Record<ReadyGate, GateState> {
  demo: GateState | null;
  reasons: string[];
  notes: string[];
  evidence: GateEvidence;
}

export interface SentinelView {
  verdict: "verified" | "not_verified";
  head_sha: string | null;
  note: string | null;
  by: string | null;
  at: number | null;
}

export interface HumanReviewView {
  verdict: "approved" | "changes_requested";
  text: string | null;
  by: string;
  at: number;
  head_sha: string | null;
  source: "cockpit" | "github";
}

/** One tracked PR with its gates, preview and newest demo. */
export interface PrView {
  id: string;
  project_slug: string | null;
  repo: string;
  number: number;
  url: string;
  title: string;
  task: string | null;
  head_sha: string;
  head_ref: string;
  base_ref: string | null;
  state: PrState;
  is_draft: boolean;
  additions: number;
  deletions: number;
  changed_files: number;
  gates: GatesView;
  ready: boolean;
  ready_since: number | null;
  sentinel: SentinelView | null;
  human_review: HumanReviewView | null;
  preview: PreviewView | null;
  demo: DemoView | null;
  /** Demos recorded before `demo`. */
  older_demos: number;
  merged: { by: string | null; sha: string | null; at: number } | null;
  closed_at: number | null;
  last_polled_at: number | null;
  poll_error: string | null;
  source: "tool" | "discovered" | null;
  task_id?: string | null;
  /** The bound task, e.g. `T-4`. */
  task_ref?: string | null;
  opened_at?: number | null;
  first_ready_at?: number | null;
  loops?: LoopChipView[] | null;
}

// ── Projects ───────────────────────────────────────────────────

export interface CharterPreview {
  enabled: boolean;
  slots: number;
  tooling_ref?: string | null;
}

/** A project charter (`charter.normalize_charter` shape). */
export interface CharterView {
  /** `work` when absent (older servers). */
  kind?: ProjectKind;
  goal: string;
  dod: string[];
  scope_paths: string[];
  constraints: string | null;
  env_needed: boolean;
  preview: CharterPreview;
  preview_slots: number;
  demo_focus: string | null;
  demo: string | null;
  budget_ics: number;
  version_path?: string | null;
  health_path?: string | null;
}

/** One project card. */
export interface ProjectView {
  slug: string;
  title: string;
  repo: string | null;
  base_branch: string | null;
  priority: number;
  charter: CharterView | null;
  stage: FactoryStage;
  stage_since: number;
  overlay: FactoryOverlay | null;
  overlay_reason: string | null;
  agent_stage: AgentStage | null;
  claim_mismatch: boolean;
  pl: MemberRef | null;
  team: MemberRef[];
  env: EnvView | null;
  pr_ids: string[];
  open_decision_ids: string[];
  cost_usd: number | null;
  elapsed_s: number | null;
  provisioning: { stage: string; label: string } | null;
  keep_previews: boolean;
  paused_by: string | null;
  kind?: ProjectKind | null;
  status?: ProjectStatus | null;
  /** The project this one was split from or merged into. */
  parent_slug?: string | null;
  closed_at?: number | null;
  depends_on?: string[] | null;
  depends_mode?: DependsMode | null;
  human_owner?: string | null;
  tasks_summary?: TasksSummaryView | null;
  loops_summary?: LoopsSummaryView | null;
  def_version?: number | null;
}

// ── Decisions and events ───────────────────────────────────────

export interface DecisionOptionView {
  id: string;
  label: string;
  description: string | null;
  recommended: boolean;
  /** A server action id (`restart`, `relaunch`, …) run on answer. */
  action: string | null;
  /** A link-only option (e.g. `/inbox`). */
  href: string | null;
  /** What the action acts on, e.g. `"member:<id>"`, `"project:<slug>"` or `"person:<user>"`. */
  target: string | null;
  /** The roster role a `grant_role` option gives (access requests). */
  role?: PersonRole | null;
  /** A required picker: the answer names one of these (a closed PR's closure reason). */
  reasons?: string[] | null;
}

/** A question for the human, shown as `ref` (`D-<number>`). */
export interface DecisionView {
  id: string;
  ref: string;
  number: number;
  factory_id: string;
  project_slug: string | null;
  asked_by_session_id: string | null;
  asked_by_title: string | null;
  asked_by_role: FactoryRole | null;
  source: DecisionSource;
  blocking: boolean;
  question: string;
  context: string | null;
  options: DecisionOptionView[];
  allow_text: boolean;
  status: DecisionStatus;
  answer_option_id: string | null;
  answer_text: string | null;
  answered_by: string | null;
  answered_at: number | null;
  delivered_mail_id: string | null;
  /** When the answer reached the asker; with `nobody_to_tell`, when it was recorded. */
  delivered_at: number | null;
  delivered_to_title: string | null;
  /** Answered, but the asker and its project's PL were gone: recorded, not mailed. */
  nobody_to_tell?: boolean;
  created_at: number;
  updated_at: number;
  /** `owners`, `operators` or a user id; absent or null means operators. */
  assignee?: string | null;
  claimed_by?: string | null;
  claimed_at?: number | null;
  /** Room thread of its card, e.g. `project:db-version`. */
  thread?: string | null;
  /** e.g. `task:T-4`, `pr:<id>`, `proposal:C-4`. */
  subject?: string | null;
}

/** One activity row. `ts` is epoch milliseconds. */
export interface EventView {
  id: string;
  ts: number;
  kind: string;
  severity: EventSeverity;
  project_slug: string | null;
  from_id: string | null;
  to_id: string | null;
  from_title: string | null;
  to_title: string | null;
  subject: string | null;
  summary: string;
  data: Record<string, unknown> | null;
}

// ── Top level ──────────────────────────────────────────────────

export interface TotalsView {
  cost_usd: number | null;
  prs_open: number;
  prs_ready: number;
  prs_merged: number;
  decisions_open: number;
  usd_per_h?: number | null;
}

/** `GET /v1/factory/factories/{fid}`: everything one cockpit page renders. */
export interface FactorySnapshot {
  factory: FactoryView;
  health: HealthView;
  projects: ProjectView[];
  prs: PrView[];
  /** Open decisions plus the last 10 answered. */
  decisions: DecisionView[];
  members: MemberView[];
  /** Projects whose PL the viewer cannot read. */
  hidden_projects: number;
  totals: TotalsView;
  server_time: number;
  viewer?: ViewerBlock | null;
  people_count?: number | null;
  presence?: PresenceEntry[] | null;
  counts?: CountsView | null;
  /** The factory definition version in force. */
  def_version?: number | null;
  engine_mode?: EngineMode | null;
}

// ── Round 3: people, Room, proposals, work graph, loops, issues ─

/** One human on the factory roster. */
export interface PersonView {
  user_id: string;
  name: string | null;
  role: PersonRole;
  is_creator: boolean;
  invited_by: string | null;
  invited_at: number;
  last_seen_at: number | null;
  present: boolean;
  location: string | null;
  idle: boolean;
  notify: Record<string, unknown> | null;
}

/** A structured object mirrored into a thread as a live card. */
export interface ChatCardView {
  type: ChatCardType;
  /** e.g. `D-7`, `C-4`, `!9630`, `INC-13`. */
  ref: string | null;
  id: string | null;
}

/** Delivery of one message to one agent. */
export interface RoutedView {
  session_id: string;
  title: string | null;
  mail_id: string | null;
  delivered_at: number | null;
  replied_at: number | null;
}

/** One Room message. `seq` is per factory, shared by every thread. */
export interface ChatMessageView {
  id: string;
  factory_id: string;
  thread: string;
  seq: number;
  author_kind: ChatAuthorKind;
  /** User id, session id, or `server`. */
  author: string;
  author_title: string | null;
  author_role: string | null;
  body: string;
  mentions: string[];
  refs: string[];
  reply_to: string | null;
  card: ChatCardView | null;
  routed: RoutedView[];
  /** The server posted the agent's turn output for it. */
  auto_reply: boolean;
  created_at: number;
}

/** One Room thread: `factory`, `improvements`, `project:<slug>` or `pr:<id>`. */
export interface ThreadView {
  key: string;
  kind: ThreadKind;
  /** e.g. `#db-version` or `!9631`. */
  title: string;
  project_slug: string | null;
  pr_id: string | null;
  pr_number: number | null;
  responder_session_id: string | null;
  responder_title: string | null;
  last_seq: number;
  last_at: number | null;
  messages: number;
  unread: number;
  /** The newest unread message that @mentions the reader. */
  mention?: ThreadMentionView | null;
}

export interface ThreadMentionView {
  seq: number;
  body: string;
}

export interface ExpectedEffectView {
  metric: string;
  direction: "up" | "down";
  delta: number | null;
}

/** A proposed change, `C-<number>`: a project plan or a definition change. */
export interface ProposalView {
  id: string;
  ref: string;
  number: number;
  factory_id: string;
  kind: ProposalKind;
  status: ProposalStatus;
  approval: ProposalApproval;
  /** Tier-1 approval class. */
  class: ProposalClass | null;
  title: string;
  /** Project ops or definition patches `{op, path, value}`. */
  ops: Record<string, unknown>[];
  before: unknown;
  after: unknown;
  rationale: string;
  /** The planner's analysis (cohesion, hub files, size estimates). */
  analysis: unknown;
  /** Ids the proposal cites, e.g. `F-9`, `D-2`. */
  evidence: unknown[];
  expected_effect: ExpectedEffectView | null;
  warnings: string[];
  author_kind: ActorKind;
  author: string;
  author_title: string | null;
  author_role: string | null;
  on_behalf_of: string | null;
  cosigned_by: string | null;
  thread: string | null;
  reply_to_chat: string | null;
  decision_id: string | null;
  decision_ref: string | null;
  based_on: Record<string, unknown> | null;
  trial_until: number | null;
  undo_until: number | null;
  result: Record<string, unknown> | null;
  def_version_from: number | null;
  def_version_to: number | null;
  decided_by: string | null;
  decided_at: number | null;
  applied_at: number | null;
  reverted_at: number | null;
  reverted_by: string | null;
  created_at: number;
  updated_at: number;
}

/** What a worker is told to do. */
export interface TaskCardView {
  text: string | null;
  /** Commands the PL runs verbatim to accept the work. */
  acceptance: string[];
  env_checks: string[];
  preview: boolean | null;
  demo_focus: string | null;
  harness: TaskHarness | null;
}

/** One node of the work graph, `T-<number>`. */
export interface TaskView {
  id: string;
  ref: string;
  number: number;
  project_slug: string;
  /** Branch suffix: `factory/<slug>/<key>`. */
  key: string;
  title: string;
  kind: TaskKind;
  state: TaskState;
  card: TaskCardView;
  /** Task keys, `project:<slug>` and `decision:D-n`. */
  deps: string[];
  scope: string[];
  priority: number;
  estimate_min: number | null;
  vendor: string | null;
  session_ids: string[];
  pr_id: string | null;
  pr_number: number | null;
  attempts: number;
  budget: { attempts: number | null; usd: number | null } | null;
  cost_usd: number | null;
  created_by: string;
  created_via: ActorKind;
  def_version: number | null;
  started_at: number | null;
  closed_at: number | null;
  created_at: number;
  updated_at: number;
  /** Layer of the ready order (1 = startable now), when computed. */
  wave: number | null;
  critical: boolean;
  waiting_on: string[];
  loop: LoopChipView | null;
}

export interface LoopSpentView {
  attempts: number;
  usd: number | null;
  wall_s: number | null;
  per_head: Record<string, number>;
  env_retries: number;
}

/** One loop: implement, review, verify, demo, ci, claim, plan, intake, improve. */
export interface LoopView {
  id: string;
  kind: LoopKind;
  /** e.g. `task:<id>` or `pr:<id>@<sha7>`. */
  subject: string;
  project_slug: string | null;
  task_id: string | null;
  task_ref: string | null;
  pr_id: string | null;
  pr_number: number | null;
  state: LoopState;
  iteration: number;
  limit: number | null;
  /** The resolved budgets the loop started with. */
  spec: Record<string, unknown>;
  spent: LoopSpentView;
  last_verdict: string | null;
  last_reason: string | null;
  decision_id: string | null;
  decision_ref: string | null;
  def_version: number | null;
  started_at: number;
  updated_at: number;
  ended_at: number | null;
}

/** An incident (`INC-n`), finding (`F-n`) or retro (no ref). */
export interface IssueView {
  id: string;
  ref: string | null;
  class: IssueClass;
  number: number;
  rule: string;
  category: string | null;
  severity: EventSeverity;
  source: IssueSource;
  project_slug: string | null;
  /** e.g. `member:<id>`, `pr:<id>`, `project:<slug>`. */
  target: string;
  target_title: string | null;
  summary: string;
  body: string | null;
  evidence: unknown;
  status: IssueStatus;
  assignee: string | null;
  snoozed_until: number | null;
  acked_by: string | null;
  resolved_by: string | null;
  count: number;
  first_seen: number;
  last_seen: number;
  resolved_at: number | null;
  proposal_id: string | null;
  proposal_ref: string | null;
  def_version: number | null;
  /** Action ids the incident page offers. */
  runbook: string[];
}

/** One span; `started_at` and `ended_at` are epoch milliseconds. */
export interface SpanView {
  id: string;
  trace_key: string;
  parent_id: string | null;
  kind: SpanKind;
  name: string;
  project_slug: string | null;
  pr_id: string | null;
  task_id: string | null;
  loop_id: string | null;
  head_sha: string | null;
  session_id: string | null;
  role: string | null;
  agent_name: string | null;
  actor: string | null;
  started_at: number;
  ended_at: number | null;
  outcome: SpanOutcome;
  attempt: number | null;
  cost_usd: number | null;
  tokens_in: number | null;
  tokens_out: number | null;
  version_id: string | null;
  def_version: number | null;
  attrs: Record<string, unknown> | null;
}

/** One version of the factory's Tier-1 definition. */
export interface DefinitionView {
  factory_id: string;
  version: number;
  parent_version: number | null;
  hash: string;
  created_by: string;
  /** The member's title when `created_by` is an agent's session id. */
  created_by_title?: string | null;
  created_via: DefinitionSource;
  proposal_id: string | null;
  proposal_ref: string | null;
  created_at: number;
  /** The version the factory runs. */
  pinned: boolean;
  /** Engine, cadence, loops, budgets, autonomy, alerts, models, rules, … */
  doc: Record<string, unknown>;
}

/** One card on the factory index (`GET /v1/factory/factories`). */
export interface FactorySummary {
  id: string;
  name: string;
  state: MemberState;
  cadence: FactoryCadence;
  started_at: number | null;
  root_session_id: string;
  elapsed_s: number | null;
  cost_usd: number | null;
  stage_counts: Partial<Record<FactoryStage, number>>;
  projects_total: number;
  decisions_open: number;
  prs_ready: number;
  members_total: number;
  members_running: number;
  drifted_members: number;
  url: string;
  /** The viewer's role in this factory (absent on older servers). */
  viewer_role?: ViewerRole | null;
  /** Who started the factory, when it is not the viewer ("Shared with you by …"). */
  shared_by?: string | null;
  people_count?: number | null;
  /** Open decisions assigned to the viewer or a role they hold. */
  mine?: number | null;
  usd_per_h?: number | null;
}

export interface FactoryListResponse {
  data: FactorySummary[];
}

/** `GET /v1/factory/factories/{fid}/events`. */
export interface EventPage {
  data: EventView[];
  has_more: boolean;
  /** The next page's cursor (it skips past events the viewer cannot read). */
  next_before?: number | null;
  next_before_id?: string | null;
}

/** Where an older page of events starts: strictly before `(before, before_id)`. */
export interface EventCursor {
  before: number;
  before_id: string | null;
}

export interface OpenDecisionItem {
  factory: { id: string; name: string };
  decision: DecisionView;
  /** Assigned to the caller or to a role they hold (absent on older servers). */
  mine?: boolean | null;
}

/** `GET /v1/factory/decisions`. */
export interface OpenDecisionsResponse {
  data: OpenDecisionItem[];
  count: number;
}

/** `GET /v1/factory/preflight`: can this user start a factory here, and with what defaults? */
export interface Preflight {
  github: { connected: boolean; login: string | null };
  agents: { factory_lead: boolean; factory_pl: boolean };
  sandbox_host: boolean;
  factory_enabled: boolean;
  /** The server's default repo and base for new factories (`OMNIGENT_FACTORY_DEFAULT_BASE`). */
  default_repo?: string | null;
  default_base?: string | null;
  /** Projects one factory can run (one PL pod each). */
  max_projects?: number;
  /** The Factory Smith lane (code PRs to omnigent): off until its prerequisites hold. */
  tier2?: Tier2StatusView | null;
}

// ── Requests ───────────────────────────────────────────────────

/** One project of a start form or an "Add project" dialog (charter fields inline). */
export interface FactoryProjectInput {
  title: string;
  slug: string;
  repo: string;
  base_branch: string;
  goal: string;
  dod: string[];
  scope_paths: string[];
  constraints?: string | null;
  env_needed: boolean;
  preview?: { enabled: boolean; slots: number };
  preview_slots?: number;
  demo_focus?: string | null;
  version_path?: string | null;
  health_path?: string | null;
  priority: number;
  budget_ics: number;
}

/**
 * `PATCH /v1/factory/factories/{fid}/projects/{slug}`: only the fields sent
 * change. `env_needed`, the title, slug and repo are fixed once added.
 */
export interface CharterPatchRequest {
  goal?: string;
  dod?: string[] | null;
  scope_paths?: string[] | null;
  constraints?: string | null;
  preview?: { enabled?: boolean; slots?: number };
  demo_focus?: string | null;
  budget_ics?: number;
  priority?: number;
}

/** A stored project row as the add/patch routes return it. */
export interface FactoryProjectRow {
  slug: string;
  title: string;
  repo: string | null;
  base_branch: string | null;
  priority: number;
  charter: CharterView | null;
}

export interface AddProjectResponse {
  project: FactoryProjectRow;
}

export interface CharterPatchResponse {
  project: FactoryProjectRow;
  /** False when the rendered charter is unchanged (nothing was sent). */
  changed: boolean;
  /** The PL's CHARTER UPDATE mail, or null when the project has no PL yet. */
  mail_id: string | null;
}

/** `POST …/projects/{slug}/start`: how a missing PL starts. */
export interface StartPlResponse {
  project: FactoryProjectRow;
  /** `server` (spawning), `lead` (Atlas was asked), `held` (waiting on dependencies). */
  started: string;
  /** The unmet dependencies of a held project. */
  waiting_on: string[];
  mail_id: string | null;
}

/** The factory-level default repo and base; a project's blank branch inherits it. */
export interface FactoryDefaultsInput {
  repo: string | null;
  base_branch: string | null;
  /** Keep a live env at the base head (project `base-env`). */
  integration_env: boolean;
}

/** `POST /v1/factory/factories`. */
export interface CreateFactoryRequest {
  root_session_id: string;
  name: string;
  cadence: FactoryCadence;
  defaults?: FactoryDefaultsInput;
  projects: FactoryProjectInput[];
  /** A chat-first start: what to build; Atlas plans projects from it in `#factory`. */
  brief?: string;
  /** Which project proposals apply on their own (the server default is `requested`). */
  autonomy?: "confirm" | "requested" | "grace";
  /** How far the engine acts (the server default is `assist`). */
  engine_mode?: EngineMode;
}

export interface CreateFactoryResponse {
  factory_id: string;
  url: string;
}

/** Levers on any agent, sub-agents included (operators; the PL is told). */
export type InterventionAction =
  "message" | "interrupt" | "redispatch" | "reassign" | "rerun_demo" | "rereview";

export type FactoryControlAction =
  | "pause"
  | "resume"
  | "stop"
  | "restart"
  | "relaunch"
  | "cadence"
  | "preview"
  | "env_status"
  | InterventionAction;

export type FactoryControlTarget =
  "factory" | `project:${string}` | `member:${string}` | `task:${string}` | `pr:${string}`;

/** `POST /v1/factory/factories/{fid}/control`. */
export interface FactoryControlRequest {
  action: FactoryControlAction;
  target: FactoryControlTarget;
  note?: string;
  options?: Record<string, unknown>;
}

export interface FactoryControlResponse {
  /** The `control.<action>` event (its id is what `control.ack` names). */
  events: EventView[];
  /** Mail queued per recipient session id. */
  mail_ids?: Record<string, string>;
}

export interface EventFilters {
  project?: string;
  member?: string;
  kinds?: string[];
  limit?: number;
}

export interface PrFeedbackRequest {
  verdict: "approved" | "changes_requested";
  text?: string;
  /** 1–5 stars with reason tags, stored as feedback on the PR. */
  rating?: number;
  tags?: string[];
}

/** `POST /v1/factory/prs/{id}/feedback`: the review as stored and the PL's mail id. */
export interface PrFeedbackResponse {
  pr_id: string;
  human_review: HumanReviewView | null;
  mail_id: string | null;
}

/**
 * `POST /v1/factory/decisions/{id}/answer` refused with 409 `decision_not_open`:
 * someone answered first, or it was withdrawn or expired meanwhile.
 */
export interface DecisionConflictBody {
  status?: DecisionStatus;
  answered_by?: string | null;
  answered_at?: number | null;
  decision?: DecisionView;
}

// ── Transport ──────────────────────────────────────────────────

/**
 * A failed factory call. Adds the offending form `field` (400 validation
 * errors) and the parsed `body` (e.g. a 409's `{answered_by, answered_at}`)
 * to the usual status and machine-readable `code`.
 */
export class FactoryApiError extends ApiError {
  readonly field: string | null;
  readonly body: unknown;
  constructor(
    message: string,
    status: number,
    code: string | null,
    field: string | null,
    body: unknown,
  ) {
    super(message, status, code);
    this.name = "FactoryApiError";
    this.field = field;
    this.body = body;
  }
}

/**
 * Whether an answer was refused because the decision is no longer open (a
 * lost race, a withdrawal or an expiry), not for another reason.
 */
export function isDecisionConflict(error: unknown): error is FactoryApiError {
  return (
    error instanceof FactoryApiError && error.status === 409 && error.code === "decision_not_open"
  );
}

function pick(source: unknown, key: string): unknown {
  return source !== null && typeof source === "object"
    ? (source as Record<string, unknown>)[key]
    : undefined;
}

function firstString(...values: unknown[]): string | null {
  for (const value of values) if (typeof value === "string" && value) return value;
  return null;
}

/** Build a {@link FactoryApiError} from any of the server's error envelopes. */
export async function factoryErrorFromResponse(res: Response): Promise<FactoryApiError> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  const error = pick(body, "error");
  const detail = pick(body, "detail");
  const message =
    firstString(pick(error, "message"), detail, pick(detail, "message"), pick(body, "message")) ??
    `${res.status} ${res.statusText}`.trim();
  const code = firstString(pick(error, "code"), pick(detail, "code"), pick(body, "code"));
  const field = firstString(pick(body, "field"), pick(error, "field"), pick(detail, "field"));
  return new FactoryApiError(message, res.status, code, field, body);
}

async function readJson<T>(res: Response): Promise<T> {
  if (!res.ok) throw await factoryErrorFromResponse(res);
  return (await res.json()) as T;
}

function postJson(path: string, body: unknown, method = "POST"): Promise<Response> {
  return authenticatedFetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

const enc = encodeURIComponent;

// ── Reads ──────────────────────────────────────────────────────

export async function fetchFactoryPreflight(): Promise<Preflight> {
  return readJson(await authenticatedFetch("/v1/factory/preflight"));
}

export async function fetchFactories(): Promise<FactoryListResponse> {
  return readJson(await authenticatedFetch("/v1/factory/factories"));
}

/** A snapshot read: fresh data with its ETag, or "unchanged since that ETag". */
export type SnapshotResult =
  { notModified: false; data: FactorySnapshot; etag: string | null } | { notModified: true };

/**
 * Read one factory's snapshot, conditionally when `etag` is given.
 *
 * @param factoryId - The factory (root session) id.
 * @param options.etag - The last ETag seen; a 304 answers `{notModified: true}`.
 */
export async function fetchFactorySnapshot(
  factoryId: string,
  options: { etag?: string | null; signal?: AbortSignal } = {},
): Promise<SnapshotResult> {
  const res = await authenticatedFetch(`/v1/factory/factories/${enc(factoryId)}`, {
    headers: options.etag ? { "If-None-Match": options.etag } : undefined,
    signal: options.signal,
  });
  if (res.status === 304) return { notModified: true };
  const data = await readJson<FactorySnapshot>(res);
  return { notModified: false, data, etag: res.headers.get("ETag") };
}

/**
 * One page of a factory's activity, newest first.
 *
 * @param options.before - Return events strictly older than this (epoch ms).
 */
export async function fetchFactoryEvents(
  factoryId: string,
  options: EventFilters & { cursor?: EventCursor | null } = {},
): Promise<EventPage> {
  const params = new URLSearchParams();
  if (options.cursor) {
    params.set("before", String(options.cursor.before));
    if (options.cursor.before_id) params.set("before_id", options.cursor.before_id);
  }
  params.set("limit", String(options.limit ?? 100));
  if (options.project) params.set("project", options.project);
  if (options.member) params.set("member", options.member);
  if (options.kinds?.length) params.set("kinds", options.kinds.join(","));
  return readJson(
    await authenticatedFetch(`/v1/factory/factories/${enc(factoryId)}/events?${params}`),
  );
}

export async function fetchOpenDecisions(): Promise<OpenDecisionsResponse> {
  return readJson(await authenticatedFetch("/v1/factory/decisions?status=open"));
}

/** Open decisions assigned to the viewer or a role they hold (badge counts). */
export async function fetchOpenDecisionCount(): Promise<number> {
  const body = await readJson<OpenDecisionsResponse>(
    await authenticatedFetch("/v1/factory/decisions?status=open&assigned=me&count_only=1"),
  );
  return body.count;
}

/**
 * A factory's recorded demos, newest first, as the viewer may see them.
 *
 * @param filters.project - Only this project's demos.
 * @param filters.pr - Only demos of this PR number.
 */
export async function fetchFactoryDemos(
  factoryId: string,
  filters: DemoFilters = {},
  signal?: AbortSignal,
): Promise<DemoListResponse> {
  const params = new URLSearchParams();
  if (filters.project) params.set("project", filters.project);
  if (filters.pr != null) params.set("pr", String(filters.pr));
  if (filters.limit != null) params.set("limit", String(filters.limit));
  const qs = params.toString();
  return readJson(
    await authenticatedFetch(`/v1/factory/factories/${enc(factoryId)}/demos${qs ? `?${qs}` : ""}`, {
      signal,
    }),
  );
}

/** Same-origin URL of one demo file (video, screenshot, …); the server answers Range. */
export function demoFileUrl(demoId: string, fileId: string): string {
  return `/v1/factory/demos/${enc(demoId)}/files/${enc(fileId)}`;
}

/** Stable link that redirects to a PR's current factory preview. */
export function prPreviewUrl(prId: string): string {
  return `/v1/factory/prs/${enc(prId)}/preview`;
}

/**
 * Read a demo's JSON file (`api.json`, `console.json`) from the demo store.
 *
 * @param url - The file's same-origin URL (`DemoFileView.url` or {@link demoFileUrl}).
 */
export async function fetchDemoJson(url: string, signal?: AbortSignal): Promise<unknown> {
  return readJson(await authenticatedFetch(url, { signal }));
}

/** Every demo of one project's PRs, grouped by PR (newest run first). */
export async function fetchProjectDemos(
  factoryId: string,
  slug: string,
  signal?: AbortSignal,
): Promise<ProjectDemosResponse> {
  return readJson(
    await authenticatedFetch(
      `/v1/factory/factories/${enc(factoryId)}/projects/${enc(slug)}/demos`,
      {
        signal,
      },
    ),
  );
}

/** The factory's spans started at or after `since` (and before `until`; epoch ms), oldest first. */
export async function fetchFactorySpans(
  factoryId: string,
  options: {
    since?: number;
    until?: number;
    kinds?: SpanKind[];
    limit?: number;
    project?: string;
    prId?: string;
  } = {},
  signal?: AbortSignal,
): Promise<SpanPage> {
  const params = new URLSearchParams();
  if (options.since != null) params.set("since", String(options.since));
  if (options.until != null) params.set("until", String(options.until));
  if (options.kinds?.length) params.set("kind", options.kinds.join(","));
  if (options.project) params.set("project", options.project);
  if (options.prId) params.set("pr_id", options.prId);
  params.set("limit", String(options.limit ?? 500));
  return readJson(
    await authenticatedFetch(`/v1/factory/factories/${enc(factoryId)}/spans?${params}`, {
      signal,
    }),
  );
}

/** The roster, who agents run as and the caller's own levers. */
export async function fetchFactoryPeople(
  factoryId: string,
  signal?: AbortSignal,
): Promise<PeopleResponse> {
  return readJson(
    await authenticatedFetch(`/v1/factory/factories/${enc(factoryId)}/people`, { signal }),
  );
}

/** Known users matching `query` (owners only; at least 3 characters). */
export async function searchFactoryPeople(
  factoryId: string,
  query: string,
  signal?: AbortSignal,
): Promise<{ data: PersonSearchResult[] }> {
  return readJson(
    await authenticatedFetch(
      `/v1/factory/factories/${enc(factoryId)}/people/search?q=${enc(query)}`,
      { signal },
    ),
  );
}

// ── Writes ─────────────────────────────────────────────────────

/** Invite `userId` as `role`, or change their role (owners). */
export async function putFactoryPerson(
  factoryId: string,
  userId: string,
  role: PersonRole,
): Promise<PutPersonResponse> {
  return readJson(
    await postJson(
      `/v1/factory/factories/${enc(factoryId)}/people/${enc(userId)}`,
      { role },
      "PUT",
    ),
  );
}

/** Remove `userId` from the roster (owners), or leave (yourself). */
export async function removeFactoryPerson(
  factoryId: string,
  userId: string,
): Promise<RemovePersonResponse> {
  return readJson(
    await authenticatedFetch(`/v1/factory/factories/${enc(factoryId)}/people/${enc(userId)}`, {
      method: "DELETE",
    }),
  );
}

/** Ask the owners for `role`; they answer an `access_request` decision. */
export async function requestFactoryAccess(
  factoryId: string,
  body: { role?: "operator" | "owner"; note?: string } = {},
): Promise<AccessRequestResponse> {
  return readJson(await postJson(`/v1/factory/factories/${enc(factoryId)}/access-requests`, body));
}

/**
 * The cockpit's presence heartbeat: where the viewer is (`overview`,
 * `project:<slug>`, `pr:<id>`, …) and whether the tab is idle; `leave` drops
 * them at once.
 */
export async function postFactoryPresence(
  factoryId: string,
  body: { location: string | null; idle: boolean; leave?: boolean },
): Promise<PresenceResponse> {
  return readJson(await postJson(`/v1/factory/factories/${enc(factoryId)}/presence`, body));
}

export async function createFactory(body: CreateFactoryRequest): Promise<CreateFactoryResponse> {
  return readJson(await postJson("/v1/factory/factories", body));
}

export async function addFactoryProject(
  factoryId: string,
  project: FactoryProjectInput,
): Promise<AddProjectResponse> {
  return readJson(await postJson(`/v1/factory/factories/${enc(factoryId)}/projects`, project));
}

export async function patchFactoryProject(
  factoryId: string,
  slug: string,
  patch: CharterPatchRequest,
): Promise<CharterPatchResponse> {
  return readJson(
    await postJson(`/v1/factory/factories/${enc(factoryId)}/projects/${enc(slug)}`, patch, "PATCH"),
  );
}

/** Start the PL of a project that has none (owners and operators). */
export async function startFactoryProjectPl(
  factoryId: string,
  slug: string,
): Promise<StartPlResponse> {
  return readJson(
    await postJson(`/v1/factory/factories/${enc(factoryId)}/projects/${enc(slug)}/start`, {}),
  );
}

export async function sendFactoryControl(
  factoryId: string,
  request: FactoryControlRequest,
): Promise<FactoryControlResponse> {
  return readJson(await postJson(`/v1/factory/factories/${enc(factoryId)}/control`, request));
}

/**
 * Answer a decision. A lost race rejects with a 409 `decision_not_open`
 * {@link FactoryApiError} whose `body` carries `{answered_by, answered_at}`;
 * other refusals carry the server's reason as the error message.
 */
export async function answerDecision(
  decisionId: string,
  answer: { option_id?: string; text?: string; reason?: string },
): Promise<{ decision: DecisionView }> {
  return readJson(await postJson(`/v1/factory/decisions/${enc(decisionId)}/answer`, answer));
}

export async function withdrawDecision(
  decisionId: string,
  reason?: string,
): Promise<{ decision: DecisionView }> {
  return readJson(
    await postJson(`/v1/factory/decisions/${enc(decisionId)}/withdraw`, reason ? { reason } : {}),
  );
}

/** Ask the PR poller to re-read a PR now (rate-limited server-side to 1 per 10 s). */
export async function refreshFactoryPr(prId: string): Promise<unknown> {
  return readJson(await postJson(`/v1/factory/prs/${enc(prId)}/refresh`, {}));
}

export async function sendPrFeedback(
  prId: string,
  feedback: PrFeedbackRequest,
): Promise<PrFeedbackResponse> {
  return readJson(await postJson(`/v1/factory/prs/${enc(prId)}/feedback`, feedback));
}

// ── Ops: fleet, agent, loops, issues ───────────────────────────

/** One "Live now" line (`ts` in epoch seconds). */
export interface LiveLineView {
  id: string;
  ts: number;
  session_id: string;
  title: string;
  role: FactoryRole | null;
  project: string | null;
  kind: "tool" | "result" | "message" | "thinking" | "human";
  tool: string | null;
  text: string;
  failed: boolean | null;
}

/** The compact task of an agent row. */
export interface AgentTaskRef {
  id: string;
  ref: string;
  key: string;
  title: string;
  state: TaskState;
  attempts: number;
  pr_id: string | null;
}

/** One row of the fleet (`GET …/agents`): a lead, PL or sub-agent. */
export interface AgentRowView {
  session_id: string;
  title: string;
  role: FactoryRole | null;
  harness: string | null;
  model: string | null;
  project: string | null;
  parent_session_id: string | null;
  is_top_level: boolean;
  status: string;
  state: MemberState | null;
  health: string | null;
  summary: string | null;
  task: AgentTaskRef | null;
  loop: LoopChipView | null;
  now: NowState | null;
  /** Own cost; `subtree_cost_usd` adds its sub-agents. */
  cost_usd: number | null;
  subtree_cost_usd: number | null;
  mail_in: number;
  mail_out: number;
  decisions_pending: number;
  /** `repeat`, `quiet`, `disconnected` and `dead`. */
  flags: string[];
  /** Open issue refs on this agent, e.g. `INC-13`. */
  issues: string[];
  last_activity_at: number | null;
  url: string;
}

export interface FleetLiveView {
  polled_at: number | null;
  interval_s: number;
  fresh: boolean;
  usd_per_h: number | null;
}

/** `GET /v1/factory/factories/{fid}/agents`. */
export interface AgentsResponse {
  data: AgentRowView[];
  lines: LiveLineView[];
  live: FleetLiveView;
}

/** One tool call of an agent (`started_at` epoch seconds). */
export interface AgentToolCallView {
  call_id: string;
  tool: string;
  arg: string | null;
  started_at: number;
  ended_at: number | null;
  duration_s: number | null;
  failed: boolean;
  output_head: string | null;
}

/** One mail in or out of an agent (`ts` epoch ms, like events). */
export interface AgentMailView {
  id: string;
  ts: number;
  kind: string;
  direction: "in" | "out";
  peer_id: string | null;
  peer_title: string | null;
  mail_kind: string | null;
  mail_id: string | null;
  summary: string;
}

export interface CostDayView {
  day: string;
  cost_usd: number;
  tokens_in: number;
  tokens_out: number;
  turns: number;
}

/** `GET /v1/factory/factories/{fid}/agents/{sid}`. */
export interface AgentDetailResponse {
  agent: AgentRowView;
  tools: AgentToolCallView[];
  lines: LiveLineView[];
  mail: AgentMailView[];
  tasks: AgentTaskRef[];
  issues: IssueView[];
  cost_series: CostDayView[];
}

/** `GET /v1/factory/factories/{fid}/loops`. */
export interface LoopsResponse {
  data: LoopView[];
  engine_mode: EngineMode | null;
}

/** `GET /v1/factory/factories/{fid}/issues`. */
export interface IssuesResponse {
  data: IssueView[];
  count: number;
  /** Ids of the listed issues that are for the viewer or their role. */
  mine: string[];
}

/** `GET /v1/factory/factories/{fid}/issues/{ref}`: the incident page. */
export interface IssuePageResponse {
  issue: IssueView;
  mine: boolean;
  /** Events before the incident on its target and PL, newest first. */
  events: EventView[];
  spans: SpanView[];
  sessions: { session_id: string; title: string | null }[];
}

export interface IssueFilters {
  class?: IssueClass;
  status?: IssueStatus[];
  project?: string;
  /** `me`: only issues for the viewer or a role they hold. */
  assigned?: "me" | "all";
  limit?: number;
}

/** `GET /v1/factory/factories/{fid}/cost`, one row per group. */
export interface CostResponse {
  group_by: string[];
  data: (Record<string, string | number | boolean> & { cost_usd: number; priced: boolean })[];
  totals: { cost_usd: number; tokens_in: number; tokens_out: number; turns: number };
}

/** The fleet, with each agent's live "now" and the newest Live lines. */
export async function fetchFactoryAgents(
  factoryId: string,
  filters: { project?: string; role?: string } = {},
  signal?: AbortSignal,
): Promise<AgentsResponse> {
  const params = new URLSearchParams();
  if (filters.project) params.set("project", filters.project);
  if (filters.role) params.set("role", filters.role);
  const qs = params.toString();
  return readJson(
    await authenticatedFetch(
      `/v1/factory/factories/${enc(factoryId)}/agents${qs ? `?${qs}` : ""}`,
      {
        signal,
      },
    ),
  );
}

/** One agent: tool calls, activity, mail, tasks, issues and cost by day. */
export async function fetchFactoryAgent(
  factoryId: string,
  sessionId: string,
  signal?: AbortSignal,
): Promise<AgentDetailResponse> {
  return readJson(
    await authenticatedFetch(`/v1/factory/factories/${enc(factoryId)}/agents/${enc(sessionId)}`, {
      signal,
    }),
  );
}

/** The factory's loops, newest activity first. */
export async function fetchFactoryLoops(
  factoryId: string,
  filters: { project?: string; pr?: string; states?: LoopState[]; kinds?: LoopKind[] } = {},
  signal?: AbortSignal,
): Promise<LoopsResponse> {
  const params = new URLSearchParams();
  if (filters.project) params.set("project", filters.project);
  if (filters.pr) params.set("pr", filters.pr);
  if (filters.states?.length) params.set("state", filters.states.join(","));
  if (filters.kinds?.length) params.set("kind", filters.kinds.join(","));
  const qs = params.toString();
  return readJson(
    await authenticatedFetch(`/v1/factory/factories/${enc(factoryId)}/loops${qs ? `?${qs}` : ""}`, {
      signal,
    }),
  );
}

/** Incidents, findings and retros, newest first. */
export async function fetchFactoryIssues(
  factoryId: string,
  filters: IssueFilters = {},
  signal?: AbortSignal,
): Promise<IssuesResponse> {
  const params = new URLSearchParams();
  if (filters.class) params.set("class", filters.class);
  if (filters.status?.length) params.set("status", filters.status.join(","));
  if (filters.project) params.set("project", filters.project);
  if (filters.assigned) params.set("assigned", filters.assigned);
  params.set("limit", String(filters.limit ?? 200));
  return readJson(
    await authenticatedFetch(`/v1/factory/factories/${enc(factoryId)}/issues?${params}`, {
      signal,
    }),
  );
}

/** One incident or finding (`INC-n`, `F-n` or its id) with what preceded it. */
export async function fetchFactoryIssue(
  factoryId: string,
  ref: string,
  signal?: AbortSignal,
): Promise<IssuePageResponse> {
  return readJson(
    await authenticatedFetch(`/v1/factory/factories/${enc(factoryId)}/issues/${enc(ref)}`, {
      signal,
    }),
  );
}

export type IssueLifecycle =
  | { action: "ack" }
  | { action: "assign"; to: string | null }
  | { action: "snooze"; minutes: number }
  | { action: "resolve"; note?: string; root_cause?: string; wontfix?: boolean };

/** Ack, assign, snooze or resolve an issue (operators). */
export async function updateFactoryIssue(
  factoryId: string,
  ref: string,
  change: IssueLifecycle,
): Promise<{ issue: IssueView }> {
  const { action, ...body } = change;
  return readJson(
    await postJson(
      `/v1/factory/factories/${enc(factoryId)}/issues/${enc(ref)}/${action}`,
      action === "ack" ? {} : body,
    ),
  );
}

/** `GET …/alerts/routing`: where incidents go (the Slack URL itself is never returned). */
export interface AlertRoutingView {
  cockpit: boolean;
  browser: boolean;
  slack: boolean;
  slack_min_severity?: string | null;
}

export async function fetchAlertRouting(
  factoryId: string,
  signal?: AbortSignal,
): Promise<AlertRoutingView> {
  return readJson(
    await authenticatedFetch(`/v1/factory/factories/${enc(factoryId)}/alerts/routing`, { signal }),
  );
}

/** The cost ledger summed by `groupBy` (display cost). */
export async function fetchFactoryCost(
  factoryId: string,
  options: { groupBy?: string[]; project?: string; since?: string } = {},
  signal?: AbortSignal,
): Promise<CostResponse> {
  const params = new URLSearchParams();
  params.set("group_by", (options.groupBy ?? ["day"]).join(","));
  if (options.project) params.set("project", options.project);
  if (options.since) params.set("since", options.since);
  return readJson(
    await authenticatedFetch(`/v1/factory/factories/${enc(factoryId)}/cost?${params}`, {
      signal,
    }),
  );
}

// ── Start ──────────────────────────────────────────────────────

export const FACTORY_LEAD_AGENT_NAME = "factory-lead";
export const FACTORY_LEAD_TITLE = "Atlas";
/** The branch a project starts from when the form leaves it blank (the server's default). */
export const DEFAULT_BASE_BRANCH = "main";
/** Attempts at registering the factory once its lead session exists. */
export const START_REGISTER_ATTEMPTS = 4;

/** What the New factory form submits. */
export interface StartFactoryForm {
  name: string;
  cadence: FactoryCadence;
  defaults?: FactoryDefaultsInput;
  /** The first project's repo and branch become the lead's workspace (else the defaults'). */
  projects: FactoryProjectInput[];
  /** "Describe it": no projects, a brief Atlas plans from. */
  brief?: string;
  autonomy?: CreateFactoryRequest["autonomy"];
  engineMode?: EngineMode;
}

/**
 * The branch a project's blank base resolves to: the factory default for the
 * default repo, else `main` (the server's resolution).
 */
export function inheritedBase(repo: string, defaults?: FactoryDefaultsInput | null): string {
  const base = defaults?.base_branch?.trim();
  const defaultRepo = defaults?.repo?.trim();
  const sameRepo = !defaultRepo || !repo || repo.toLowerCase() === defaultRepo.toLowerCase();
  return base && sameRepo ? base : DEFAULT_BASE_BRANCH;
}

/**
 * A start that created the lead session but could not register the factory.
 * `cause` is the last registration error (a 400's `field` maps onto the form).
 */
export class StartFactoryError extends Error {
  readonly rootSessionId: string;
  constructor(message: string, rootSessionId: string, cause: unknown) {
    super(message, { cause });
    this.name = "StartFactoryError";
    this.rootSessionId = rootSessionId;
  }
}

interface AgentListPage {
  data: { id: string; name: string }[];
  has_more?: boolean;
  last_id?: string | null;
}

/** Resolve a registered agent's id by name from `GET /v1/agents`. */
export async function resolveAgentId(name: string): Promise<string> {
  let after: string | null = null;
  /* oxlint-disable no-await-in-loop -- each page names the next cursor */
  do {
    const path: string =
      after == null ? "/v1/agents" : `/v1/agents?after=${encodeURIComponent(after)}`;
    const page: AgentListPage = await readJson(await authenticatedFetch(path));
    const found = page.data.find((agent) => agent.name === name);
    if (found) return found.id;
    after = page.has_more === true && page.last_id ? page.last_id : null;
  } while (after != null);
  /* oxlint-enable no-await-in-loop */
  throw new FactoryApiError(`Agent "${name}" is not installed`, 404, "agent_missing", null, null);
}

function retryable(error: unknown): boolean {
  return !(error instanceof ApiError) || error.status >= 500 || error.status === 404;
}

const defaultSleep = (ms: number) =>
  new Promise<void>((resolve) => {
    setTimeout(resolve, ms);
  });

/** Which call of the two-call start is running. */
export type StartFactoryStep = "lead" | "register";

export interface StartFactoryOptions {
  /** Backoff hook (tests pass a no-op). */
  sleep?: (ms: number) => Promise<void>;
  /** Called as each step begins (the form shows "Creating Atlas…" / "Storing charters…"). */
  onStep?: (step: StartFactoryStep) => void;
  /** A lead created by an earlier attempt: skip step 1 and register against it. */
  rootSessionId?: string | null;
}

/**
 * Start a factory in two calls: create the lead (Atlas) as a managed session,
 * then register the factory against it.
 *
 * The lead's spec owns its launch mode, so step 1 is the ordinary session
 * create. Step 2 is idempotent per root id and is retried with the same id on
 * transient failures; a 4xx other than 404 (validation, conflict) is final.
 * A retry after a failed step 2 passes `rootSessionId` and skips step 1.
 *
 * @param form - Name, cadence and projects (the first one's repo is the lead's workspace).
 * @returns The new factory's id and cockpit url.
 * @throws StartFactoryError when the lead was created but registering failed
 *   (its `rootSessionId` names the orphan lead, so the UI can open or retry it).
 */
export async function startFactory(
  form: StartFactoryForm,
  options: StartFactoryOptions = {},
): Promise<CreateFactoryResponse & { root_session_id: string }> {
  // A blank branch means the factory default, so the lead's workspace never ends in `#`.
  const projects = form.projects.map((project) => ({
    ...project,
    base_branch: project.base_branch.trim() || inheritedBase(project.repo, form.defaults),
  }));
  const first = projects[0];
  // A chat-first start runs Atlas on the factory's default repo and base.
  const defaultRepo = form.defaults?.repo?.trim();
  const workspaceRepo = first?.repo ?? defaultRepo;
  const workspaceBase =
    first?.base_branch ?? inheritedBase(defaultRepo ?? "", form.defaults ?? null);
  if (!workspaceRepo) throw new Error("A factory needs a project or a default repository");
  let rootId = options.rootSessionId ?? null;
  if (!rootId) {
    options.onStep?.("lead");
    const agentId = await resolveAgentId(FACTORY_LEAD_AGENT_NAME);
    const session = await readJson<{ id: string }>(
      await authenticatedFetch("/v1/sessions", {
        method: "POST",
        headers: sessionCreateHeaders(),
        body: JSON.stringify({
          agent_id: agentId,
          host_type: "managed",
          workspaces: [`https://github.com/${workspaceRepo}#${workspaceBase}`],
          title: FACTORY_LEAD_TITLE,
        }),
      }),
    );
    rootId = session.id;
  }
  options.onStep?.("register");
  const sleep = options.sleep ?? defaultSleep;
  const request: CreateFactoryRequest = {
    root_session_id: rootId,
    name: form.name,
    cadence: form.cadence,
    ...(form.defaults ? { defaults: form.defaults } : {}),
    projects,
    ...(form.brief?.trim() ? { brief: form.brief.trim() } : {}),
    ...(form.autonomy ? { autonomy: form.autonomy } : {}),
    ...(form.engineMode ? { engine_mode: form.engineMode } : {}),
  };
  let lastError: unknown = null;
  /* oxlint-disable no-await-in-loop -- retries are sequential by design */
  for (let attempt = 1; attempt <= START_REGISTER_ATTEMPTS; attempt++) {
    try {
      const created = await createFactory(request);
      return { ...created, root_session_id: rootId };
    } catch (error) {
      lastError = error;
      if (!retryable(error) || attempt === START_REGISTER_ATTEMPTS) break;
      await sleep(500 * 2 ** (attempt - 1));
    }
  }
  /* oxlint-enable no-await-in-loop */
  const reason = lastError instanceof Error ? lastError.message : String(lastError);
  throw new StartFactoryError(
    `Atlas was created but the factory was not registered: ${reason}`,
    rootId,
    lastError,
  );
}

// ── Round 3: learning (Insights, Improvements, Settings, feedback) ──
//
// Mirrors `omnigent/server/factory/api/{insights,definition,feedback,learn,
// issues,telemetry_read}.py`. Metric values carry `n`; a missing number is
// `null` (no data), never zero.

/** The Factory Smith lane (code PRs to omnigent); `reason` says which prerequisite is missing. */
export interface Tier2StatusView {
  enabled: boolean;
  reason: string | null;
  checks?: Record<string, unknown> | null;
}

export type MetricFamily = "flow" | "loops" | "quality" | "human" | "cost" | "reliability";
export type MetricGroupBy =
  "day" | "project" | "role" | "agent" | "person" | "version" | "def_version";

/** One aggregate of a metric: a value, p50/p90, a per-key breakdown or a mix, always with n. */
export interface MetricAggregate {
  n: number;
  value?: number | null;
  p50?: number | null;
  p90?: number | null;
  total?: number | null;
  by?: Record<string, MetricAggregate>;
  mix?: Record<string, { count: number; share: number | null }>;
  peak?: number;
  mean?: number | null;
  bounce_rate?: number | null;
  queue_age_p90_s?: number | null;
  env_ready_share?: number | null;
  preview_ready_share?: number | null;
  /** Metric-specific extras (closure reasons, the prompt band, $ by model, …). */
  [extra: string]: unknown;
}

/** One catalog metric over the window (`groups` when grouped, `previous` for KPI tiles). */
export interface MetricResult extends MetricAggregate {
  name: string;
  family: MetricFamily;
  title: string;
  unit: string;
  definition: string;
  groups?: Record<string, MetricAggregate>;
  previous?: MetricAggregate;
  /** Headline now minus headline in the previous window. */
  delta?: number | null;
  /** `unavailable` when the metric failed on the server (the others still show). */
  error?: string;
}

export interface MetricCatalogEntry {
  name: string;
  family: MetricFamily;
  title: string;
  unit: string;
  definition: string;
}

export interface InsightsWindowView {
  since: number;
  until: number;
  label: string;
}

/** Per-person numbers (owners only). */
export interface PersonInsightsView {
  decisions_answered: number;
  decision_p50_s: number | null;
  chat_posts: number;
  interventions: number;
  escalated_away: number;
}

/** `GET /v1/factory/factories/{fid}/insights`. */
export interface InsightsResponse {
  window: InsightsWindowView;
  def_version: number | null;
  kpis: Record<string, MetricResult>;
  findings: IssueView[];
  findings_open: number;
  incidents_open: number;
  incidents: IssueView[];
  /** The latest retro per project (`project_slug` null: factory-wide). */
  retros: IssueView[];
  experiments: { on_trial: number; kept: number; reverted: number };
  catalog: MetricCatalogEntry[];
  people?: Record<string, PersonInsightsView>;
}

/** `GET …/metrics`. */
export interface MetricsResponse {
  window: InsightsWindowView;
  group_by: MetricGroupBy | null;
  metrics: Record<string, MetricResult>;
}

export interface InsightsFilters {
  /** `24h`, `7d`, `30d`… (the server's default is 7d). */
  window?: string;
  project?: string | null;
  version?: string | null;
  def_version?: number | null;
}

export interface DefinitionRule {
  id: string;
  role: string;
  project: string;
  text: string;
  origin: string | null;
  state: "trial" | "active";
}

/** The definition doc: engine, cadence, loops, budgets, autonomy, alerts, models, rules, … */
export type DefinitionDoc = Record<string, unknown> & { rules?: DefinitionRule[] };

/** `GET …/definition`: the version in force, effective values, bounds, classes and trials. */
export interface DefinitionResponse {
  def_version: number;
  version: DefinitionView | null;
  effective: DefinitionDoc;
  /** Path → `[min, max]` (an env ceiling already applied). */
  bounds: Record<string, [number, number]>;
  env_ceilings: string[];
  classes: { paths: string; class: string }[];
  paths: string[];
  on_trial: ProposalView[];
  tier2: Tier2StatusView;
}

export interface DefinitionVersionsResponse {
  data: DefinitionView[];
  pinned: number | null;
}

export interface DefinitionDiffEntry {
  path: string;
  before: unknown;
  after: unknown;
}

export interface DefinitionDiffResponse {
  from: number;
  to: number;
  changes: DefinitionDiffEntry[];
  lines: string[];
}

/** A settings value is replaced; a rule is added at `rules` and changed at `rules/R-<n>`. */
export interface DefinitionChange {
  op: "replace" | "add" | "remove";
  path: string;
  value?: unknown;
}

export interface EffectInput {
  metric: string;
  direction: "up" | "down";
  delta?: number;
}

/** `PUT …/definition`: an owner's edit, applied at once as a human `C-n`. */
export interface DefinitionEditRequest {
  changes: DefinitionChange[];
  note?: string;
  trial?: boolean;
  trial_h?: number;
  expected_effect?: EffectInput;
  /** The version the edit was made on; a newer one in force answers 409 `stale_definition`. */
  base_version?: number;
}

export interface DefinitionEditResponse {
  proposal: string;
  proposal_id: string;
  status: ProposalStatus;
  def_version: number | null;
}

export interface PinResponse {
  def_version: number;
  changed: boolean;
  from?: number;
  ended?: string[];
}

/** `POST …/definition/proposals`: an operator's change for the owners. */
export interface HumanChangeRequest {
  kind?: "tuning" | "rules";
  changes: DefinitionChange[];
  rationale: string;
  evidence?: string[];
  expected_effect?: EffectInput;
  trial_h?: number;
}

export interface HumanChangeResponse {
  proposal: ProposalView;
  decision: string;
}

export interface DefinitionMarker {
  version: number;
  created_at: number;
  created_via: DefinitionSource;
  created_by: string;
  proposal_ref: string | null;
  pinned: boolean;
}

export interface PlatformMarker {
  id: string;
  created_at: number;
  image_tag: string | null;
  server_sha: string | null;
  current: boolean;
}

/** `GET …/versions`: definition and platform versions, oldest first. */
export interface VersionsResponse {
  definition: DefinitionMarker[];
  platform: PlatformMarker[];
}

export interface BundleMemberView {
  session_id: string;
  title: string;
  /** The agent's name, e.g. `factory-pl` (agent ids are opaque). */
  bundle?: string | null;
  role: string | null;
  project: string | null;
  /** `<agent id>/<sha12>` the member was launched with. */
  running: string | null;
  /** The bundle the server seeds now. */
  current: string | null;
  drift: boolean;
}

/** `GET …/bundles`. */
export interface BundlesResponse {
  members: BundleMemberView[];
  drifted: number;
}

export interface GuardView {
  metric: string;
  baseline: number | null;
  trial: number | null;
  n_baseline: number;
  n_trial: number;
  /** e.g. `≥ baseline − 10 pp`. */
  allowed: string;
  status: "ok" | "regressed" | "no_data";
}

/** A trial against its baseline: the server's evaluation (live while on trial). */
export interface ScorecardView {
  outcome: "kept" | "reverted" | "inconclusive";
  reason: string;
  n: number;
  min_n: number;
  target: {
    metric: string | null;
    met: boolean;
    baseline?: number | null;
    trial?: number | null;
    direction?: "up" | "down" | null;
  };
  guards: GuardView[];
  trial_window: [number, number];
  baseline_window: [number, number];
  evaluated_at: number;
  live: boolean;
}

export interface ScorecardResponse {
  proposal: string;
  scorecard: ScorecardView | null;
}

export type FeedbackTargetKind =
  "pr" | "demo" | "decision" | "project" | "member" | "factory" | "issue" | "proposal";
export type FeedbackKind =
  "rating" | "useful" | "unneeded" | "closure" | "calibration" | "root_cause";

/** `POST /v1/factory/feedback` (viewers too). */
export interface FeedbackRequest {
  factory_id: string;
  target_kind: FeedbackTargetKind;
  target_id: string;
  kind: FeedbackKind;
  /** 1–5 for ratings, +1/−1 for thumbs and calibration. */
  rating?: number;
  tags?: string[];
  body?: string;
  head_sha?: string;
  project?: string;
}

export interface FeedbackView {
  id: string;
  factory_id: string;
  target_kind: FeedbackTargetKind;
  target_id: string;
  author_kind: ActorKind;
  author: string;
  kind: FeedbackKind;
  project_slug: string | null;
  head_sha: string | null;
  rating: number | null;
  tags: string[];
  body: string | null;
  def_version: number | null;
  created_at: number;
}

/** `POST …/issues`: "Report a problem" (anyone who reads the factory). */
export interface ReportIssueRequest {
  summary: string;
  body?: string;
  /** e.g. `member:<sid>`, `pr:<id>`, `project:<slug>`. */
  target?: string;
  class?: "incident" | "finding";
  severity?: "info" | "notice" | "warning";
  project?: string;
}

function factoryUrl(factoryId: string, rest: string): string {
  return `/v1/factory/factories/${enc(factoryId)}${rest}`;
}

function insightsQuery(filters: InsightsFilters, extra: Record<string, string> = {}): string {
  const params = new URLSearchParams(extra);
  if (filters.window) params.set("window", filters.window);
  if (filters.project) params.set("project", filters.project);
  if (filters.version) params.set("version", filters.version);
  if (filters.def_version != null) params.set("def_version", String(filters.def_version));
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

/** KPI tiles (with n and deltas), findings, incidents, retros, experiments. */
export async function fetchFactoryInsights(
  factoryId: string,
  filters: InsightsFilters = {},
  signal?: AbortSignal,
): Promise<InsightsResponse> {
  return readJson(
    await authenticatedFetch(factoryUrl(factoryId, `/insights${insightsQuery(filters)}`), {
      signal,
    }),
  );
}

/** Catalog metrics (every one when `names` is empty), grouped when `groupBy` is set. */
export async function fetchFactoryMetrics(
  factoryId: string,
  options: InsightsFilters & { names?: readonly string[]; groupBy?: MetricGroupBy | null } = {},
  signal?: AbortSignal,
): Promise<MetricsResponse> {
  const extra: Record<string, string> = {};
  if (options.names?.length) extra.names = options.names.join(",");
  if (options.groupBy) extra.group_by = options.groupBy;
  return readJson(
    await authenticatedFetch(factoryUrl(factoryId, `/metrics${insightsQuery(options, extra)}`), {
      signal,
    }),
  );
}

export async function fetchFactoryVersions(
  factoryId: string,
  signal?: AbortSignal,
): Promise<VersionsResponse> {
  return readJson(await authenticatedFetch(factoryUrl(factoryId, "/versions"), { signal }));
}

export async function fetchFactoryBundles(
  factoryId: string,
  signal?: AbortSignal,
): Promise<BundlesResponse> {
  return readJson(await authenticatedFetch(factoryUrl(factoryId, "/bundles"), { signal }));
}

export async function fetchFactoryDefinition(
  factoryId: string,
  signal?: AbortSignal,
): Promise<DefinitionResponse> {
  return readJson(await authenticatedFetch(factoryUrl(factoryId, "/definition"), { signal }));
}

export async function fetchDefinitionVersions(
  factoryId: string,
  signal?: AbortSignal,
): Promise<DefinitionVersionsResponse> {
  return readJson(
    await authenticatedFetch(factoryUrl(factoryId, "/definition/versions?limit=100"), { signal }),
  );
}

export async function fetchDefinitionDiff(
  factoryId: string,
  from: number,
  to: number,
  signal?: AbortSignal,
): Promise<DefinitionDiffResponse> {
  return readJson(
    await authenticatedFetch(factoryUrl(factoryId, `/definition/diff?from=${from}&to=${to}`), {
      signal,
    }),
  );
}

/** An owner's edit: applied at once as a human `C-n` (a trial only when asked). */
export async function putFactoryDefinition(
  factoryId: string,
  edit: DefinitionEditRequest,
): Promise<DefinitionEditResponse> {
  return readJson(await postJson(factoryUrl(factoryId, "/definition"), edit, "PUT"));
}

/** Put a stored version back in force (owners). */
export async function pinFactoryDefinition(
  factoryId: string,
  version: number,
): Promise<PinResponse> {
  return readJson(await postJson(factoryUrl(factoryId, "/definition/pin"), { version }));
}

/** An operator's tuning or rules proposal; owners approve it as a trial. */
export async function proposeDefinitionChange(
  factoryId: string,
  request: HumanChangeRequest,
): Promise<HumanChangeResponse> {
  return readJson(await postJson(factoryUrl(factoryId, "/definition/proposals"), request));
}

/** One proposal (`C-4`, `4` or its id). */
export async function fetchFactoryProposal(
  factoryId: string,
  ref: string,
  signal?: AbortSignal,
): Promise<{ proposal: ProposalView }> {
  return readJson(
    await authenticatedFetch(factoryUrl(factoryId, `/proposals/${enc(ref)}`), { signal }),
  );
}

export async function fetchProposalScorecard(
  factoryId: string,
  ref: string,
  signal?: AbortSignal,
): Promise<ScorecardResponse> {
  return readJson(
    await authenticatedFetch(factoryUrl(factoryId, `/proposals/${enc(ref)}/scorecard`), {
      signal,
    }),
  );
}

/** End a trial now and keep it, or revert it (re-pins the parent version). Owners. */
export async function decideTrial(
  factoryId: string,
  ref: string,
  action: "keep" | "revert",
  note?: string,
): Promise<{ proposal: ProposalView }> {
  return readJson(
    await postJson(factoryUrl(factoryId, `/proposals/${enc(ref)}/${action}`), note ? { note } : {}),
  );
}

/** Ask Sentinel for a retro now (owners; at most one per hour). */
export async function requestFactoryRetro(
  factoryId: string,
): Promise<{ sent: boolean; subject: string }> {
  return readJson(await postJson(factoryUrl(factoryId, "/retro"), {}));
}

export async function postFactoryFeedback(
  request: FeedbackRequest,
): Promise<{ feedback: FeedbackView }> {
  return readJson(await postJson("/v1/factory/feedback", request));
}

/** "Report a problem": files an incident or finding as you. */
export async function reportFactoryIssue(
  factoryId: string,
  request: ReportIssueRequest,
): Promise<{ issue: IssueView }> {
  return readJson(await postJson(factoryUrl(factoryId, "/issues"), request));
}

/** Set (or clear with `null`) the Slack webhook; the server never returns it. Owners. */
export async function putSlackWebhook(
  factoryId: string,
  url: string | null,
): Promise<{ slack: boolean }> {
  return readJson(await postJson(factoryUrl(factoryId, "/alerts/slack"), { url }, "PUT"));
}

/** The telemetry export (never transcripts) as a file body. */
export async function fetchFactoryExport(
  factoryId: string,
  format: "ndjson" | "csv",
): Promise<Blob> {
  const res = await authenticatedFetch(factoryUrl(factoryId, `/export?format=${format}`));
  if (!res.ok) throw await factoryErrorFromResponse(res);
  return res.blob();
}
