// Typed client for the factory cockpit's human API (`/v1/factory/...`).
//
// The types mirror `omnigent/server/factory/schemas.py`, the cockpit contract,
// field for field and in its snake_case wire shape: the live-run fixture
// (`components/factory/__fixtures__/snapshot.liveRun.json`) validates against
// the pydantic models and is imported here unchanged, so change both sides
// together. Times are epoch seconds except `EventView.ts` (milliseconds); a
// `null` cost means unpriced, never zero.

import { authenticatedFetch } from "./identity";
import { ApiError } from "./sessionsApi";

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
export type FactoryOverlay = "paused" | "blocked" | "failed" | "pr_closed";
export type FactoryRole = "lead" | "project_lead" | "ic" | "env_keeper" | "reviewer" | "demo";
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
export type DecisionSource =
  "agent" | "relaunch_failed" | "blocked_approval" | "pr_closed" | "storm";
export type DecisionStatus = "open" | "answered" | "withdrawn" | "expired";
export type EventSeverity = "info" | "notice" | "warning" | "critical";

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
}

// ── Factory header and health ──────────────────────────────────

export interface NextBeatView {
  session_id: string;
  title: string;
  at: number;
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
  } | null;
  verify: {
    result: string | null;
    sha: string | null;
    env: string | null;
    url: string | null;
    at: number | null;
  } | null;
  demo: {
    status: DemoStatus | null;
    sha: string | null;
    demo_id: string | null;
    url: string | null;
    at: number | null;
  } | null;
  mergeable: string | null;
  files: string[];
  protected: string[];
}

/** The six ready gates, in display order (`demo` is a badge, not a gate). */
export const READY_GATES = [
  "draft",
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
}

// ── Projects ───────────────────────────────────────────────────

export interface CharterPreview {
  enabled: boolean;
  slots: number;
  tooling_ref?: string | null;
}

/** A project charter (`charter.normalize_charter` shape). */
export interface CharterView {
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
  /** What the action acts on, e.g. `"member:<id>"` or `"project:<slug>"`. */
  target: string | null;
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
  delivered_at: number | null;
  delivered_to_title: string | null;
  created_at: number;
  updated_at: number;
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
}

export interface FactoryListResponse {
  data: FactorySummary[];
}

/** `GET /v1/factory/factories/{fid}/events`. */
export interface EventPage {
  data: EventView[];
  has_more: boolean;
}

export interface OpenDecisionItem {
  factory: { id: string; name: string };
  decision: DecisionView;
}

/** `GET /v1/factory/decisions`. */
export interface OpenDecisionsResponse {
  data: OpenDecisionItem[];
  count: number;
}

/** `GET /v1/factory/preflight`: can this user start a factory here? */
export interface Preflight {
  github: { connected: boolean; login: string | null };
  agents: { factory_lead: boolean; factory_pl: boolean };
  sandbox_host: boolean;
  factory_enabled: boolean;
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

/** `POST /v1/factory/factories`. */
export interface CreateFactoryRequest {
  root_session_id: string;
  name: string;
  cadence: FactoryCadence;
  projects: FactoryProjectInput[];
}

export interface CreateFactoryResponse {
  factory_id: string;
  url: string;
}

export type FactoryControlAction =
  "pause" | "resume" | "stop" | "restart" | "relaunch" | "cadence" | "preview" | "env_status";

export type FactoryControlTarget = "factory" | `project:${string}` | `member:${string}`;

/** `POST /v1/factory/factories/{fid}/control`. */
export interface FactoryControlRequest {
  action: FactoryControlAction;
  target: FactoryControlTarget;
  note?: string;
  options?: Record<string, unknown>;
}

export interface FactoryControlResponse {
  events: EventView[];
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
  options: EventFilters & { before?: number | null } = {},
): Promise<EventPage> {
  const params = new URLSearchParams();
  if (options.before != null) params.set("before", String(options.before));
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

export async function fetchOpenDecisionCount(): Promise<number> {
  const body = await readJson<OpenDecisionsResponse>(
    await authenticatedFetch("/v1/factory/decisions?status=open&count_only=1"),
  );
  return body.count;
}

/** Same-origin URL of one demo file (video, screenshot, …); the server answers Range. */
export function demoFileUrl(demoId: string, fileId: string): string {
  return `/v1/factory/demos/${enc(demoId)}/files/${enc(fileId)}`;
}

/** Stable link that redirects to a PR's current factory preview. */
export function prPreviewUrl(prId: string): string {
  return `/v1/factory/prs/${enc(prId)}/preview`;
}

// ── Writes ─────────────────────────────────────────────────────

export async function createFactory(body: CreateFactoryRequest): Promise<CreateFactoryResponse> {
  return readJson(await postJson("/v1/factory/factories", body));
}

export async function addFactoryProject(
  factoryId: string,
  project: FactoryProjectInput,
): Promise<unknown> {
  return readJson(await postJson(`/v1/factory/factories/${enc(factoryId)}/projects`, project));
}

export async function patchFactoryProject(
  factoryId: string,
  slug: string,
  patch: Partial<FactoryProjectInput>,
): Promise<unknown> {
  return readJson(
    await postJson(`/v1/factory/factories/${enc(factoryId)}/projects/${enc(slug)}`, patch, "PATCH"),
  );
}

export async function sendFactoryControl(
  factoryId: string,
  request: FactoryControlRequest,
): Promise<FactoryControlResponse> {
  return readJson(await postJson(`/v1/factory/factories/${enc(factoryId)}/control`, request));
}

/**
 * Answer a decision. A lost race rejects with a 409 {@link FactoryApiError}
 * whose `body` carries `{answered_by, answered_at}`.
 */
export async function answerDecision(
  decisionId: string,
  answer: { option_id?: string; text?: string },
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

export async function sendPrFeedback(prId: string, feedback: PrFeedbackRequest): Promise<unknown> {
  return readJson(await postJson(`/v1/factory/prs/${enc(prId)}/feedback`, feedback));
}

// ── Start ──────────────────────────────────────────────────────

export const FACTORY_LEAD_AGENT_NAME = "factory-lead";
export const FACTORY_LEAD_TITLE = "Atlas";
/** Attempts at registering the factory once its lead session exists. */
export const START_REGISTER_ATTEMPTS = 4;

/** What the New factory form submits. */
export interface StartFactoryForm {
  name: string;
  cadence: FactoryCadence;
  /** The first project's repo and branch become the lead's workspace. */
  projects: FactoryProjectInput[];
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

/**
 * Start a factory in two calls: create the lead (Atlas) as a managed session,
 * then register the factory against it.
 *
 * The lead's spec owns its launch mode, so step 1 is the ordinary session
 * create. Step 2 is idempotent per root id and is retried with the same id on
 * transient failures; a 4xx other than 404 (validation, conflict) is final.
 *
 * @param form - Name, cadence and projects (the first one's repo is the lead's workspace).
 * @param options.sleep - Backoff hook (tests pass a no-op).
 * @returns The new factory's id and cockpit url.
 * @throws StartFactoryError when the lead was created but registering failed
 *   (its `rootSessionId` names the orphan lead, so the UI can open or retry it).
 */
export async function startFactory(
  form: StartFactoryForm,
  options: { sleep?: (ms: number) => Promise<void> } = {},
): Promise<CreateFactoryResponse & { root_session_id: string }> {
  const first = form.projects[0];
  if (!first) throw new Error("A factory needs at least one project");
  const agentId = await resolveAgentId(FACTORY_LEAD_AGENT_NAME);
  const session = await readJson<{ id: string }>(
    await postJson("/v1/sessions", {
      agent_id: agentId,
      host_type: "managed",
      workspaces: [`https://github.com/${first.repo}#${first.base_branch}`],
      title: FACTORY_LEAD_TITLE,
    }),
  );
  const sleep = options.sleep ?? defaultSleep;
  const request: CreateFactoryRequest = {
    root_session_id: session.id,
    name: form.name,
    cadence: form.cadence,
    projects: form.projects,
  };
  let lastError: unknown = null;
  /* oxlint-disable no-await-in-loop -- retries are sequential by design */
  for (let attempt = 1; attempt <= START_REGISTER_ATTEMPTS; attempt++) {
    try {
      const created = await createFactory(request);
      return { ...created, root_session_id: session.id };
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
    session.id,
    lastError,
  );
}
