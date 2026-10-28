// Typed client for the factory's conversational and graph routes: the Room
// (threads, messages, read marks, the status digest), project proposals
// (`C-n`), the work graph (tasks and the four graph kinds), decision claims
// and history, and the loops and issues the Room's cards embed.
//
// Wire shapes mirror `omnigent/server/factory/api/{chat,proposals,tasks,
// graph,decision_assign}.py` in their snake_case form. Times are epoch
// seconds except graph `*_ms` fields and event `ts` (milliseconds).

import {
  type ChatMessageView,
  type DecisionStatus,
  type DecisionView,
  type FactoryControlResponse,
  type IssueView,
  type LoopView,
  type OpenDecisionsResponse,
  type ProposalKind,
  type ProposalStatus,
  type ProposalView,
  type TaskKind,
  type TaskHarness,
  type TaskState,
  type TaskView,
  type ThreadView,
  type EngineMode,
  factoryErrorFromResponse,
} from "./factoryApi";
import { authenticatedFetch } from "./identity";

const enc = encodeURIComponent;

async function readJson<T>(res: Response): Promise<T> {
  if (!res.ok) throw await factoryErrorFromResponse(res);
  return (await res.json()) as T;
}

function sendJson(path: string, body: unknown, method = "POST"): Promise<Response> {
  return authenticatedFetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

function factoryPath(factoryId: string, rest = ""): string {
  return `/v1/factory/factories/${enc(factoryId)}${rest}`;
}

// ── Room ───────────────────────────────────────────────────────

export interface ThreadsResponse {
  data: ThreadView[];
}

/** One page of a thread, oldest first; `has_more` means older messages exist. */
export interface ChatPage {
  data: ChatMessageView[];
  has_more: boolean;
  thread: string | null;
}

export interface PostChatRequest {
  thread: string;
  body: string;
  reply_to?: string | null;
  mentions?: string[];
}

export interface PostChatResponse {
  message: ChatMessageView;
  /** System lines routing added ("PL · x is paused; Atlas will answer"). */
  notes: ChatMessageView[];
  /** Names of the agents it was routed to. */
  routed: string[];
}

/** Every thread of the Room with its responder and the caller's unread count. */
export async function fetchRoomThreads(
  factoryId: string,
  signal?: AbortSignal,
): Promise<ThreadsResponse> {
  return readJson(await authenticatedFetch(factoryPath(factoryId, "/chat/threads"), { signal }));
}

/**
 * A page of one thread, oldest first: the latest page, or the one just
 * before `beforeSeq`.
 */
export async function fetchRoomMessages(
  factoryId: string,
  thread: string,
  options: { beforeSeq?: number | null; limit?: number } = {},
  signal?: AbortSignal,
): Promise<ChatPage> {
  const params = new URLSearchParams({ thread, limit: String(options.limit ?? 50) });
  if (options.beforeSeq != null) params.set("before_seq", String(options.beforeSeq));
  return readJson(await authenticatedFetch(factoryPath(factoryId, `/chat?${params}`), { signal }));
}

/** Post as yourself; the thread's agent (or the mentioned ones) answer. */
export async function postRoomMessage(
  factoryId: string,
  request: PostChatRequest,
): Promise<PostChatResponse> {
  return readJson(await sendJson(factoryPath(factoryId, "/chat"), request));
}

/** Move the caller's read mark in one thread forward (the server never moves it back). */
export async function putRoomRead(
  factoryId: string,
  thread: string,
  lastSeq: number,
): Promise<{ thread: string; last_seq: number }> {
  return readJson(
    await sendJson(factoryPath(factoryId, "/chat/reads"), { thread, last_seq: lastSeq }, "PUT"),
  );
}

/** `/status [slug]`: a markdown digest the server renders from the caller's snapshot. */
export async function fetchStatusDigest(
  factoryId: string,
  slug?: string | null,
): Promise<{ markdown: string; slug: string | null }> {
  const qs = slug ? `?slug=${enc(slug)}` : "";
  return readJson(await authenticatedFetch(factoryPath(factoryId, `/status-digest${qs}`)));
}

// ── Proposals ──────────────────────────────────────────────────

export interface ProposalsResponse {
  data: ProposalView[];
}

/** What applying or rejecting a proposal returns. */
export interface ProposalAnswerResponse {
  proposal: ProposalView;
  decision: DecisionView;
}

/** The factory's proposals, newest first. */
export async function fetchProposals(
  factoryId: string,
  filters: { kind?: ProposalKind; status?: ProposalStatus[]; limit?: number } = {},
  signal?: AbortSignal,
): Promise<ProposalsResponse> {
  const params = new URLSearchParams();
  if (filters.kind) params.set("kind", filters.kind);
  if (filters.status?.length) params.set("status", filters.status.join(","));
  params.set("limit", String(filters.limit ?? 100));
  return readJson(
    await authenticatedFetch(factoryPath(factoryId, `/proposals?${params}`), { signal }),
  );
}

/**
 * Apply a pending proposal; `changes` (Edit & apply) replaces its ops first.
 * A lost race or a changed project rejects with a 409 (`proposal_not_pending`,
 * `stale_proposal`).
 */
export async function applyProposal(
  factoryId: string,
  ref: string,
  changes?: Record<string, unknown>[],
): Promise<ProposalAnswerResponse> {
  return readJson(
    await sendJson(
      factoryPath(factoryId, `/proposals/${enc(ref)}/apply`),
      changes ? { changes } : {},
    ),
  );
}

export async function rejectProposal(
  factoryId: string,
  ref: string,
  note?: string,
): Promise<ProposalAnswerResponse> {
  return readJson(
    await sendJson(factoryPath(factoryId, `/proposals/${enc(ref)}/reject`), note ? { note } : {}),
  );
}

/**
 * File a projects proposal as yourself (e.g. a dependency edited on the
 * Graph). It opens pending, mirrored as a decision in `thread` (`#factory`).
 */
export async function proposeProjects(
  factoryId: string,
  request: { changes: Record<string, unknown>[]; rationale: string; thread?: string },
): Promise<{ proposal: ProposalView; decision: string }> {
  return readJson(await sendJson(factoryPath(factoryId, "/proposals"), request));
}

/** Undo an applied proposal within its window (before its PLs spawn). */
export async function undoProposal(
  factoryId: string,
  ref: string,
): Promise<{ proposal: ProposalView }> {
  return readJson(await sendJson(factoryPath(factoryId, `/proposals/${enc(ref)}/undo`), {}));
}

// ── Work graph: tasks ──────────────────────────────────────────

/** One project's computed graph, as the tasks list returns it. */
export interface ProjectGraphView {
  project: string;
  ready: string[];
  waves: string[][];
  critical_path: string[];
  critical_path_min: number | null;
  blocked: { key: string; waiting_on: string[] }[];
  slots: { ics_free: number; previews_free: number };
  paused: boolean;
  warnings: GraphWarning[];
}

/** A scope or hub warning: `scope_overlap` (tasks share paths) or `hub_file`. */
export interface GraphWarning {
  code: string;
  tasks?: string[];
  paths?: string[];
  message: string;
  project?: string;
}

export interface TasksResponse {
  tasks: TaskView[];
  projects: ProjectGraphView[];
}

/** The editable fields of a task (`do` is its card's instruction text). */
export interface TaskFieldsInput {
  title?: string;
  kind?: Exclude<TaskKind, "adhoc">;
  do?: string;
  deps?: string[];
  scope?: string[];
  acceptance?: string[];
  env_checks?: string[];
  preview?: boolean;
  demo_focus?: string;
  harness?: TaskHarness;
  estimate_min?: number;
  priority?: number;
}

export interface TaskAddRequest extends TaskFieldsInput {
  project: string;
  key: string;
  budget?: { attempts?: number; usd?: number };
}

export interface TaskEditRequest extends TaskFieldsInput {
  budget?: { attempts?: number; usd?: number };
  cancel?: boolean;
}

export interface TaskWriteResponse {
  task: TaskView;
  changed?: boolean;
  mail_id: string | null;
  warnings: GraphWarning[];
}

export async function fetchFactoryTasks(
  factoryId: string,
  project?: string | null,
  signal?: AbortSignal,
): Promise<TasksResponse> {
  const qs = project ? `?project=${enc(project)}` : "";
  return readJson(await authenticatedFetch(factoryPath(factoryId, `/tasks${qs}`), { signal }));
}

/** Add a task to a project; its PL gets a TASK ADD with the card. */
export async function addFactoryTask(
  factoryId: string,
  request: TaskAddRequest,
): Promise<TaskWriteResponse> {
  return readJson(await sendJson(factoryPath(factoryId, "/tasks"), request));
}

/** Edit a task (deps, card, budget) or cancel it; its PL gets a TASK EDIT. */
export async function patchFactoryTask(
  factoryId: string,
  taskRef: string,
  request: TaskEditRequest,
): Promise<TaskWriteResponse> {
  return readJson(
    await sendJson(factoryPath(factoryId, `/tasks/${enc(taskRef)}`), request, "PATCH"),
  );
}

// ── Work graph: the four graphs ────────────────────────────────

export type GraphKind = "work" | "bus" | "flow" | "org";

export interface GraphHead {
  kind: GraphKind;
  factory_id: string;
  project: string | null;
  generated_at: number;
  window_s?: number;
}

export interface WorkGraphProject extends ProjectGraphView {
  slug: string;
  title: string;
  status: string | null;
  stage: string | null;
  depends_on: string[];
  summary: {
    total: number;
    done: number;
    ready: number;
    running: number;
    blocked: number;
    failed: number;
    critical_path_min: number | null;
  };
}

export interface WorkEdge {
  /** Task ids (`dep`, `serial`). */
  from: string;
  to: string;
  kind: "dep" | "serial";
  met: boolean;
}

export interface ExternalEdge {
  /** `project:<slug>` or `decision:D-n`. */
  from: string;
  /** A task id. */
  to: string;
  kind: "project" | "decision";
  met: boolean;
  assignee?: string | null;
  status?: DecisionStatus;
}

export interface ProjectEdge {
  from: string;
  to: string;
  kind: "depends_on";
  met: boolean;
}

export interface WorkGraphResponse extends GraphHead {
  kind: "work";
  projects: WorkGraphProject[];
  tasks: TaskView[];
  /** Task id → layer of the topological order (1 = no unfinished deps). */
  layers: Record<string, number>;
  edges: WorkEdge[];
  external_edges: ExternalEdge[];
  project_edges: ProjectEdge[];
  warnings: GraphWarning[];
}

export interface BusNode {
  id: string;
  kind: "agent" | "human" | "server";
  title: string;
  role?: string | null;
  project?: string | null;
  health?: string | null;
}

export type BusEdge =
  | {
      from: string;
      to: string;
      kind: "message";
      count: number;
      kinds: Record<string, number>;
      delivered: number;
      bounced: number;
      requests: number;
      unanswered: number;
      p50_reply_s: number | null;
      last_at: number;
    }
  | { from: string; to: string; kind: "spawn" | "supervise" }
  | {
      from: string;
      to: string;
      kind: "waits_on";
      label?: string | null;
      blocking?: boolean;
      since?: number | null;
    }
  | { from: string; to: string; kind: "alert"; count: number };

export interface BusGraphResponse extends GraphHead {
  kind: "bus";
  since_ms: number;
  until_ms: number;
  nodes: BusNode[];
  edges: BusEdge[];
}

export interface FlowNode {
  id: string;
  /** Projects or PRs sitting there now. */
  count: number;
  /** Closed samples behind the dwell time. */
  n: number;
  p50_dwell_s: number | null;
  usd: number | null;
}

export interface FlowBackEdge {
  from: string;
  to: string;
  label: string;
  count: number;
}

export interface FlowGraphResponse extends GraphHead {
  kind: "flow";
  since_ms: number;
  nodes: FlowNode[];
  edges: { from: string; to: string }[];
  back_edges: FlowBackEdge[];
}

export interface OrgNode {
  id: string;
  title: string;
  role: string | null;
  project: string | null;
  parent: string | null;
  supervisor: string | null;
  health: string | null;
  status: string | null;
  state: string | null;
  is_top_level: boolean;
  depth: number;
}

export interface OrgGraphResponse extends GraphHead {
  kind: "org";
  truncated: boolean;
  nodes: OrgNode[];
  edges: { from: string; to: string; kind: "spawn" | "supervise" }[];
  groups: {
    project: string | null;
    members: string[];
    health: string;
    by_health: Record<string, number>;
  }[];
}

export type GraphResponse =
  WorkGraphResponse | BusGraphResponse | FlowGraphResponse | OrgGraphResponse;

/** One of the factory's graphs, as this viewer may see it. */
export async function fetchFactoryGraph<K extends GraphKind>(
  factoryId: string,
  kind: K,
  options: { project?: string | null; window?: string | null } = {},
  signal?: AbortSignal,
): Promise<Extract<GraphResponse, { kind: K }>> {
  const params = new URLSearchParams({ kind });
  if (options.project) params.set("project", options.project);
  if (options.window) params.set("window", options.window);
  return readJson(await authenticatedFetch(factoryPath(factoryId, `/graph?${params}`), { signal }));
}

// ── Decisions: claims, assignment, history ─────────────────────

/** Take an open decision you may answer, or release your claim. */
export async function claimDecision(
  decisionId: string,
  release = false,
): Promise<{ decision: DecisionView }> {
  return readJson(
    await sendJson(`/v1/factory/decisions/${enc(decisionId)}/claim`, release ? { release } : {}),
  );
}

/** Route a decision to `owners`, `operators` or a person on the roster. */
export async function assignDecision(
  decisionId: string,
  to: string,
): Promise<{ decision: DecisionView }> {
  return readJson(await sendJson(`/v1/factory/decisions/${enc(decisionId)}/assign`, { to }));
}

/** Decisions in `statuses` across the factories the caller reads (history, filters). */
export async function fetchDecisionsByStatus(
  statuses: DecisionStatus[],
  options: { assigned?: "me" | "all" } = {},
  signal?: AbortSignal,
): Promise<OpenDecisionsResponse> {
  const params = new URLSearchParams({ status: statuses.join(",") });
  if (options.assigned === "me") params.set("assigned", "me");
  return readJson(await authenticatedFetch(`/v1/factory/decisions?${params}`, { signal }));
}

// ── Loops and issues (the Room's cards) ────────────────────────

/** The factory's loops; a server without the loop engine answers 404. */
export async function fetchRoomLoops(
  factoryId: string,
  signal?: AbortSignal,
): Promise<{ data: LoopView[] }> {
  return readJson(await authenticatedFetch(factoryPath(factoryId, "/loops"), { signal }));
}

/** The factory's incidents and findings, newest first (the cards look theirs up). */
export async function fetchRoomIssues(
  factoryId: string,
  signal?: AbortSignal,
): Promise<{ data: IssueView[] }> {
  return readJson(
    await authenticatedFetch(factoryPath(factoryId, "/issues?limit=200"), { signal }),
  );
}

/** Acknowledge an incident (`/ack INC-4`). */
export async function ackRoomIssue(factoryId: string, ref: string): Promise<unknown> {
  return readJson(await sendJson(factoryPath(factoryId, `/issues/${enc(ref)}/ack`), {}));
}

// ── Interventions (Room slash commands) ────────────────────────

export type InterventionAction =
  "message" | "interrupt" | "redispatch" | "reassign" | "rerun_demo" | "rereview";

/** Message or interrupt any agent, re-dispatch or re-assign a task, re-run a demo or review. */
export async function sendIntervention(
  factoryId: string,
  request: {
    action: InterventionAction;
    target: string;
    note?: string;
    options?: Record<string, unknown>;
  },
): Promise<FactoryControlResponse> {
  return readJson(await sendJson(factoryPath(factoryId, "/control"), request));
}

// ── Start: Describe it ─────────────────────────────────────────

export type ProjectAutonomy = "confirm" | "requested" | "grace";

export const PROJECT_AUTONOMIES: readonly ProjectAutonomy[] = ["confirm", "requested", "grace"];
export const ENGINE_MODES: readonly EngineMode[] = ["observe", "assist", "enforce"];

/** A task state the graph draws as finished. */
export function taskFinished(state: TaskState): boolean {
  return state === "done" || state === "cancelled";
}
