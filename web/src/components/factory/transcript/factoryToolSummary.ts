// Pure one-line summaries of the factory runner tools (`sys_factory_*`), read
// from a call's arguments and its JSON output. Claude-native wraps MCP output
// in a content-block list, so both shapes are accepted.

import type { FactoryMarkerTone } from "@/lib/factoryMessages";
import { isFactoryWarningHealth } from "@/lib/factory";
import type { ToolState } from "@/lib/renderItems";

const MCP_PREFIX = "mcp__omnigent__";

export const FACTORY_POST_TOOL = "sys_session_post";

/** Factory tools that render as a one-liner instead of the generic tool row. */
export const FACTORY_LINE_TOOLS: ReadonlySet<string> = new Set([
  "sys_factory_roster",
  "sys_factory_set",
  "sys_factory_spawn",
  "sys_factory_ask",
  "sys_factory_pr",
  "sys_factory_env",
  "sys_factory_demo",
]);

/** One-liners that stay visible after a settled turn folds (they are outcomes). */
export const FACTORY_OUTCOME_TOOLS: ReadonlySet<string> = new Set([
  "sys_factory_spawn",
  "sys_factory_ask",
  "sys_factory_pr",
  "sys_factory_env",
  "sys_factory_demo",
]);

/** A tool name without the claude-native `mcp__omnigent__` prefix. */
export function factoryToolBaseName(name: string): string {
  return name.startsWith(MCP_PREFIX) ? name.slice(MCP_PREFIX.length) : name;
}

/** Whether `name` (bare or MCP-prefixed) is a factory one-liner tool. */
export function isFactoryLineTool(name: string): boolean {
  return FACTORY_LINE_TOOLS.has(factoryToolBaseName(name));
}

type Json = Record<string, unknown>;

function asObject(value: unknown): Json | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Json)
    : null;
}

/**
 * Parse a tool's output as a JSON object: a bare object, or the first text
 * block of an MCP content list that holds one.
 *
 * @returns The object, or `null` for missing or non-JSON output.
 */
export function parseToolOutputJson(output: string | null | undefined): Json | null {
  if (!output) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(output);
  } catch {
    return null;
  }
  if (Array.isArray(parsed)) {
    for (const block of parsed) {
      const text = asObject(block)?.text;
      if (typeof text === "string") return parseToolOutputJson(text);
    }
    return null;
  }
  return asObject(parsed);
}

function str(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** The error code of a `{error: …}` tool output, if any. */
export function toolErrorCode(out: Json | null): string | null {
  const error = out?.error;
  if (typeof error === "string") return error;
  return str(asObject(error)?.code) ?? str(asObject(error)?.message);
}

/** PR number from a GitHub PR URL, e.g. `.../pull/9630` → 9630. */
export function prNumberFromUrl(url: string | null | undefined): number | null {
  const match = url ? /\/pull\/(\d+)/.exec(url) : null;
  return match ? Number(match[1]) : null;
}

function excerpt(text: string, max: number): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

const ROLE_SHORT: Record<string, string> = {
  lead: "Lead",
  project_lead: "PL",
  env_keeper: "Env",
  reviewer: "Reviewer",
  demo: "Demo",
  ic: "IC",
};

/** A roster row as `sys_factory_roster` returns it. */
export interface RosterRow {
  id: string;
  title: string;
  role: string | null;
  project: string | null;
  status: string | null;
  health: string | null;
  summary: string | null;
  depth: 0 | 1;
  warn: boolean;
}

/** Everything a factory one-liner needs to render. */
export interface FactoryToolSummary {
  tool: string;
  /** Bold leading verb, e.g. "Asked". */
  verb: string;
  /** Rest of the line, e.g. "D-3 · Should /version show the short sha?". */
  text: string;
  tone: FactoryMarkerTone;
  running: boolean;
  error: string | null;
  /** Session to link to ("Open ↗"). */
  sessionId?: string;
  /** Cockpit query to link to (needs the factory id). */
  cockpit?: Record<string, string | number>;
  /** Off-app URL (preview app, GitHub PR). */
  externalUrl?: string;
  /** Target of a `sys_factory_set` on another member. */
  targetSessionId?: string;
  roster?: RosterRow[];
  /** Small key/value lines for the expanded view. */
  facts?: [string, string][];
  pr?: number;
  sha?: string;
  /** Demo status (PASS, PARTIAL, FAIL, ERROR) of a published demo. */
  demoStatus?: string;
  demoUrl?: string;
  demoTitle?: string;
  demoSummary?: string;
  demoFiles?: { name: string; kind: string }[];
}

function rosterRow(raw: Json, depth: 0 | 1): RosterRow {
  const health = str(raw.health);
  const status = str(raw.status);
  return {
    id: str(raw.session_id) ?? "",
    title: str(raw.title) ?? str(raw.session_id) ?? "untitled",
    role: str(raw.role),
    project: str(raw.project),
    status,
    health,
    summary: str(raw.summary),
    depth,
    warn: isFactoryWarningHealth(health) || status === "failed",
  };
}

function summarizeRoster(out: Json | null): Partial<FactoryToolSummary> {
  const members = Array.isArray(out?.members) ? (out!.members as unknown[]) : [];
  const rows: RosterRow[] = [];
  for (const m of members) {
    const member = asObject(m);
    if (!member) continue;
    rows.push(rosterRow(member, 0));
    const kids = Array.isArray(member.children) ? member.children : [];
    for (const k of kids) {
      const kid = asObject(k);
      if (kid) rows.push(rosterRow(kid, 1));
    }
  }
  const top = rows.filter((r) => r.depth === 0).length;
  const subs = rows.length - top;
  const warn = rows.filter((r) => r.warn).length;
  const parts = [`${top} member${top === 1 ? "" : "s"}`];
  if (subs > 0) parts.push(`${subs} sub-agent${subs === 1 ? "" : "s"}`);
  if (warn > 0) parts.push(`${warn} ⚠`);
  return {
    verb: "Roster",
    text: out ? parts.join(" · ") : "",
    tone: warn > 0 ? "warning" : "muted",
    roster: rows,
  };
}

function heartbeatLabel(seconds: number): string {
  if (seconds === 0) return "off";
  if (seconds % 3600 === 0) return `every ${seconds / 3600}h`;
  if (seconds % 60 === 0) return `every ${seconds / 60}m`;
  return `every ${seconds}s`;
}

function summarizeSet(args: Json): Partial<FactoryToolSummary> {
  const target = str(args.session_id) ?? undefined;
  const stage = str(args.stage);
  const summary = typeof args.summary === "string" ? args.summary : null;
  const health = str(args.health);
  const state = str(args.state);
  const heartbeat = num(args.heartbeat_s);
  const title = str(args.title);
  const base = { targetSessionId: target };
  if (stage) return { ...base, verb: "Stage claim", text: `→ ${stage}`, tone: "info" };
  if (summary !== null) {
    const healthNote = health && health !== "ok" ? ` · ${health}` : "";
    return {
      ...base,
      verb: "Beacon",
      text: `→ ${excerpt(summary, 80) || "(cleared)"}${healthNote}`,
      tone: isFactoryWarningHealth(health) ? "warning" : "muted",
    };
  }
  if (health) {
    return {
      ...base,
      verb: "Health",
      text: `→ ${health}`,
      tone: isFactoryWarningHealth(health) ? "warning" : "muted",
    };
  }
  if (state) return { ...base, verb: "State", text: `→ ${state}`, tone: "muted" };
  if (heartbeat !== null) {
    return { ...base, verb: "Heartbeat", text: `→ ${heartbeatLabel(heartbeat)}`, tone: "muted" };
  }
  if (title) return { ...base, verb: "Title", text: `→ ${title}`, tone: "muted" };
  if (typeof args.supervisor_session_id === "string") {
    return { ...base, verb: "Supervisor", text: "updated", tone: "muted" };
  }
  return { ...base, verb: "Factory labels", text: "updated", tone: "muted" };
}

function summarizeSpawn(args: Json, out: Json | null): Partial<FactoryToolSummary> {
  const role = str(out?.role) ?? str(args.role) ?? "project_lead";
  const project = str(out?.project) ?? str(args.project);
  const who = [ROLE_SHORT[role] ?? role, project ?? str(args.title)].filter(Boolean).join(" · ");
  const sessionId = str(out?.session_id) ?? undefined;
  const status = str(out?.status) ?? (sessionId ? "provisioning" : "");
  const briefFailed = asObject(out?.brief_error) !== null;
  return {
    verb: "Spawned",
    text: [who, status, briefFailed ? "brief not delivered" : null].filter(Boolean).join(" · "),
    tone: briefFailed ? "warning" : "info",
    sessionId,
  };
}

function summarizeAsk(args: Json, out: Json | null): Partial<FactoryToolSummary> {
  const decision = str(out?.decision);
  const question = str(args.question);
  const deduped = out?.deduped === true;
  const options = Array.isArray(args.options)
    ? args.options.map((o) => str(asObject(o)?.label)).filter((l): l is string => l !== null)
    : [];
  const facts: [string, string][] = [];
  if (question) facts.push(["Question", question]);
  if (options.length > 0) facts.push(["Options", options.join(" · ")]);
  if (args.blocking === false) facts.push(["Blocking", "no"]);
  return {
    verb: "Asked",
    text: [
      decision ? `${decision}${deduped ? " (already open)" : ""}` : null,
      question ? excerpt(question, 90) : null,
    ]
      .filter(Boolean)
      .join(" · "),
    tone: "info",
    cockpit: decision ? { decision } : undefined,
    facts,
  };
}

function gatesFact(gates: unknown): string | null {
  const obj = asObject(gates);
  if (!obj) return null;
  const parts = Object.entries(obj).map(([name, value]) => {
    const state = str(asObject(value)?.state) ?? str(value) ?? "?";
    return `${name} ${state}`;
  });
  return parts.length > 0 ? parts.join(" · ") : null;
}

function summarizePr(args: Json, out: Json | null): Partial<FactoryToolSummary> {
  const url = str(args.url);
  const pr = prNumberFromUrl(url) ?? num(out?.number) ?? undefined;
  const ready = out?.ready;
  const reasons = Array.isArray(out?.reasons)
    ? out!.reasons.filter((r): r is string => typeof r === "string")
    : [];
  const mismatch = out?.claim_mismatch === true;
  const verdict = str(args.sentinel_verdict);
  const claim = str(args.claim);
  const label = pr !== undefined ? `PR #${pr}` : "PR";
  let lead = `${label} registered`;
  if (verdict) lead = `${label} · Sentinel ${verdict.replace(/_/g, " ")}`;
  else if (claim === "ready_for_human_merge") lead = `${label} claimed ready`;
  else if (claim === "abandoned") lead = `${label} abandoned`;
  const readiness =
    ready === true ? "ready ✓" : ready === false ? `ready ✗ ${reasons[0] ?? ""}`.trim() : null;
  const facts: [string, string][] = [];
  const gates = gatesFact(out?.gates);
  if (gates) facts.push(["Gates", gates]);
  if (reasons.length > 1) facts.push(["Blocking", reasons.join(" · ")]);
  const note = str(args.note);
  if (note) facts.push(["Note", note]);
  return {
    verb: "PR",
    text: [lead.replace(/^PR /, ""), readiness, mismatch ? "claim ≠ gates" : null]
      .filter(Boolean)
      .join(" · "),
    tone: mismatch ? "warning" : ready === true ? "success" : "muted",
    pr,
    sha: str(args.head_sha) ?? undefined,
    cockpit: pr !== undefined ? { review: pr } : undefined,
    externalUrl: url ?? undefined,
    facts,
  };
}

const ENV_TONE: Record<string, FactoryMarkerTone> = {
  ready: "success",
  starting: "info",
  deploying: "info",
  degraded: "warning",
  stale: "warning",
  failed: "danger",
  parked: "muted",
  parking: "muted",
  down: "muted",
};

function summarizeEnv(args: Json, out: Json | null): Partial<FactoryToolSummary> {
  const pr = num(args.pr) ?? undefined;
  const state = str(args.state) ?? "reported";
  const urls = asObject(args.urls);
  const app = str(urls?.app);
  const api = str(urls?.api);
  const sha = str(asObject(args.deployed)?.sha) ?? undefined;
  const error = toolErrorCode(out);
  const facts: [string, string][] = [];
  if (app) facts.push(["App", app]);
  if (api) facts.push(["API", api]);
  const envId = str(args.env_id);
  if (envId) facts.push(["Env", envId]);
  const note = str(args.note);
  if (note) facts.push(["Note", note]);
  const live = state === "ready" || state === "degraded";
  return {
    verb: pr !== undefined ? "Preview" : "Dev env",
    text: [pr !== undefined ? `#${pr}` : null, state, sha ? `@${sha.slice(0, 7)}` : null]
      .filter(Boolean)
      .join(" "),
    tone: error ? "danger" : (ENV_TONE[state] ?? "muted"),
    pr,
    sha,
    externalUrl: live && app ? app : undefined,
    cockpit: pr !== undefined ? { review: pr } : undefined,
    facts,
  };
}

function summarizeDemo(args: Json, out: Json | null): Partial<FactoryToolSummary> {
  const prUrl = str(args.pr_url);
  const pr = prNumberFromUrl(prUrl) ?? undefined;
  const sha = str(args.head_sha) ?? str(args.sha) ?? undefined;
  const status = (str(out?.status) ?? str(args.status))?.toUpperCase();
  const fileList = Array.isArray(out?.files)
    ? out!.files
    : Array.isArray(args.files)
      ? args.files
      : [];
  const files = fileList
    .map((f) => asObject(f))
    .filter((f): f is Json => f !== null)
    .map((f) => ({
      name: str(f.name) ?? str(f.path)?.split("/").pop() ?? "file",
      kind: str(f.kind) ?? "file",
    }));
  const tone: FactoryMarkerTone =
    status === "PASS"
      ? "success"
      : status === "PARTIAL"
        ? "warning"
        : status === "FAIL" || status === "ERROR"
          ? "danger"
          : "accent";
  return {
    verb: "Demo published",
    text: [pr !== undefined ? `PR #${pr}` : null, sha ? `@${sha.slice(0, 7)}` : null, status]
      .filter(Boolean)
      .join(" · "),
    tone,
    pr,
    sha,
    demoStatus: status,
    demoUrl: str(out?.demo_url) ?? str(out?.review_url) ?? undefined,
    demoTitle: str(args.title) ?? undefined,
    demoSummary: str(args.summary) ?? str(out?.summary) ?? undefined,
    demoFiles: files,
    cockpit: pr !== undefined ? { review: pr } : undefined,
  };
}

/**
 * Summarize one factory tool call for its one-line row.
 *
 * @param name - Tool name, bare or `mcp__omnigent__`-prefixed.
 * @param args - The call's arguments.
 * @param output - The raw tool output (JSON, possibly MCP-wrapped).
 * @param state - The tool card state (running, done, errored …).
 * @returns The summary, or `null` for a tool without a one-liner.
 */
export function summarizeFactoryTool(
  name: string,
  args: Record<string, unknown>,
  output: string | null,
  state: ToolState,
): FactoryToolSummary | null {
  const tool = factoryToolBaseName(name);
  if (!FACTORY_LINE_TOOLS.has(tool)) return null;
  const out = parseToolOutputJson(output);
  const error = toolErrorCode(out) ?? (state === "output-error" ? "failed" : null);
  const running = state === "input-available";
  let partial: Partial<FactoryToolSummary>;
  switch (tool) {
    case "sys_factory_roster":
      partial = summarizeRoster(out);
      break;
    case "sys_factory_set":
      partial = summarizeSet(args);
      break;
    case "sys_factory_spawn":
      partial = summarizeSpawn(args, out);
      break;
    case "sys_factory_ask":
      partial = summarizeAsk(args, out);
      break;
    case "sys_factory_pr":
      partial = summarizePr(args, out);
      break;
    case "sys_factory_env":
      partial = summarizeEnv(args, out);
      break;
    default:
      partial = summarizeDemo(args, out);
  }
  const verb =
    (running ? RUNNING_VERB[tool] : error ? FAILED_VERB[tool] : undefined) ?? partial.verb ?? tool;
  return {
    tool,
    text: partial.text ?? "",
    ...partial,
    verb,
    tone: error ? "danger" : (partial.tone ?? "muted"),
    running,
    error,
  };
}

/** Verbs while the call is still running (the outcome is not known yet). */
const RUNNING_VERB: Record<string, string> = {
  sys_factory_roster: "Reading roster",
  sys_factory_spawn: "Spawning",
  sys_factory_ask: "Asking",
  sys_factory_pr: "Registering PR",
  sys_factory_demo: "Publishing demo",
};

/** Verbs for a call the runner or server refused. */
const FAILED_VERB: Record<string, string> = {
  sys_factory_roster: "Roster failed",
  sys_factory_spawn: "Spawn failed",
  sys_factory_ask: "Ask failed",
  sys_factory_pr: "PR registration failed",
  sys_factory_demo: "Demo publish failed",
};

/** Arguments and receipt of a `sys_session_post` call. */
export interface FactoryPostSummary {
  to: string;
  kind: string;
  message: string;
  restart: boolean;
  mailId: string | null;
  toSessionId: string | null;
  /** Server status from the output (`queued`), or `null`. */
  status: string | null;
  error: string | null;
}

/**
 * Read a `sys_session_post` call: its arguments and the `{mail_id,
 * to_session_id, status}` receipt (or the `{error}` the runner returned).
 */
export function summarizeFactoryPost(
  args: Record<string, unknown>,
  output: string | null,
  state: ToolState,
): FactoryPostSummary {
  const out = parseToolOutputJson(output);
  return {
    to: str(args.to) ?? "?",
    kind: str(args.kind) ?? "info",
    message: typeof args.message === "string" ? args.message : "",
    restart: args.restart === true,
    mailId: str(out?.mail_id),
    toSessionId: str(out?.to_session_id),
    status: str(out?.status),
    error:
      toolErrorCode(out) ??
      (state === "output-error"
        ? "failed"
        : output && !out && state !== "input-available"
          ? excerpt(output, 120)
          : null),
  };
}
