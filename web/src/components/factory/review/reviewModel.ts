// Pure logic behind the review drawer: the demo's media and API transcript,
// which commit the preview runs, the gate count, older demo heads and the
// merge summary a human pastes elsewhere. Components render these.

import type {
  DemoFileKind,
  DemoFileView,
  DemoView,
  EventView,
  PrView,
  PreviewView,
  ProjectView,
} from "@/lib/factoryApi";
import { READY_GATES, demoFileUrl, prPreviewUrl } from "@/lib/factoryApi";
import { GATE_KEY_LABEL, type GateKey, shownGates } from "../board/GateChip";
import { type FactoryTone, shortSha } from "../factoryFormat";
import { previewHeadTruth } from "../board/boardModel";

// ── Gates ──────────────────────────────────────────────────────

/** Gates that count toward "ready" (the demo gate when the project records demos). */
export function readyGates(pr: PrView): GateKey[] {
  return [...READY_GATES, ...(pr.gates.demo != null ? (["demo"] as const) : [])];
}

/** `{passing, total}` over the ready gates; a skipped gate counts as passing. */
export function gateTally(pr: PrView): { passing: number; total: number } {
  const gates = readyGates(pr);
  const passing = gates.filter((gate) => {
    const state = gate === "demo" ? pr.gates.demo : pr.gates[gate];
    return state === "pass" || state === "skip";
  }).length;
  return { passing, total: gates.length };
}

/** Sentinel verified this exact head. */
export function sentinelVerifiedHead(pr: PrView): boolean {
  return pr.sentinel?.verdict === "verified" && pr.sentinel.head_sha === pr.head_sha;
}

const UX_PATH = /^services\/caffeine-ux\//;

/** The PR changes the caffeine-ux app, so a reviewer expects to see it run. */
export function touchesUx(pr: PrView): boolean {
  return pr.gates.evidence.files.some((file) => UX_PATH.test(file));
}

/** A UI PR with no demo of its current head yet. */
export function uxDemoMissing(pr: PrView): boolean {
  return pr.state === "open" && touchesUx(pr) && (!pr.demo || !pr.demo.is_current_head);
}

// ── Demo media ─────────────────────────────────────────────────

/** Where a demo file is served (same origin; the server answers Range). */
export function demoFileHref(demo: DemoView, file: DemoFileView): string {
  return file.url || demoFileUrl(demo.id, file.id);
}

export function demoFile(demo: DemoView, id: string | null | undefined): DemoFileView | null {
  return id ? (demo.files.find((f) => f.id === id) ?? null) : null;
}

/** The demo's recording, if it has one. */
export function demoVideo(demo: DemoView): DemoFileView | null {
  return demoFile(demo, demo.video_file_id) ?? demo.files.find((f) => f.kind === "video") ?? null;
}

/** Step screenshots in step order (then file name), each with the step it shows. */
export function demoScreenshots(demo: DemoView): DemoFileView[] {
  const stepOf = new Map<string, number>();
  for (const step of demo.steps) {
    if (step.screenshot_file_id) stepOf.set(step.screenshot_file_id, step.n);
  }
  const shots = demo.files.filter((f) => f.kind === "screenshot");
  const step = (f: DemoFileView) => f.step ?? stepOf.get(f.id) ?? Number.MAX_SAFE_INTEGER;
  return shots.sort((a, b) => step(a) - step(b) || a.name.localeCompare(b.name));
}

/** Kinds offered as downloads, in the order the drawer lists them. */
export const DOWNLOAD_KINDS: readonly DemoFileKind[] = [
  "script",
  "har",
  "trace",
  "console",
  "api",
  "log",
  "other",
];

export const DOWNLOAD_LABEL: Record<DemoFileKind, string> = {
  video: "Video",
  screenshot: "Screenshot",
  poster: "Poster",
  api: "API calls",
  har: "HAR",
  trace: "Trace",
  console: "Console",
  script: "Script",
  log: "Log",
  other: "File",
};

/** Files a reviewer downloads rather than watches (HAR, trace, script, …). */
export function demoDownloads(demo: DemoView): DemoFileView[] {
  const rank = (f: DemoFileView) => DOWNLOAD_KINDS.indexOf(f.kind);
  return demo.files
    .filter((f) => DOWNLOAD_KINDS.includes(f.kind))
    .sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
}

/** The demo's `api.json`, when it recorded API calls. */
export function demoApiFile(demo: DemoView): DemoFileView | null {
  return demo.files.find((f) => f.kind === "api") ?? null;
}

// ── API transcript (`api.json`) ────────────────────────────────

export interface ApiMessage {
  headers: Record<string, string>;
  body: string | null;
  contentType: string | null;
  bytes: number | null;
  truncated: boolean;
}

/** One request/response pair the demo harness recorded. */
export interface DemoApiCall {
  i: number;
  step: number | null;
  method: string;
  url: string;
  status: number | null;
  ms: number | null;
  request: ApiMessage | null;
  response: ApiMessage | null;
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function headers(value: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, v] of Object.entries(record(value) ?? {})) {
    if (v != null) out[key] = String(v);
  }
  return out;
}

function bodyText(value: unknown): string | null {
  if (value == null) return null;
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function message(value: unknown): ApiMessage | null {
  if (value == null) return null;
  const rec = record(value);
  // An older harness stored the response body itself.
  if (!rec || !("body" in rec || "headers" in rec)) {
    return { headers: {}, body: bodyText(value), contentType: null, bytes: null, truncated: false };
  }
  const hdrs = headers(rec.headers);
  return {
    headers: hdrs,
    body: bodyText(rec.body),
    contentType:
      typeof rec.content_type === "string" ? rec.content_type : (hdrs["content-type"] ?? null),
    bytes: num(rec.bytes),
    truncated: rec.truncated === true,
  };
}

/**
 * Parse `api.json`: `[{i, step, method, url, status, ms, request{headers, body},
 * response{headers, content_type, body, bytes, truncated}}]` (FACTORY.md).
 * Lenient: unknown fields are ignored and malformed entries dropped.
 */
export function parseApiCalls(raw: unknown): DemoApiCall[] {
  const list = Array.isArray(raw) ? raw : (record(raw)?.calls ?? record(raw)?.data);
  if (!Array.isArray(list)) return [];
  const calls: DemoApiCall[] = [];
  list.forEach((entry, index) => {
    const rec = record(entry);
    if (!rec || typeof rec.url !== "string") return;
    calls.push({
      i: num(rec.i) ?? index,
      step: num(rec.step),
      method: typeof rec.method === "string" ? rec.method.toUpperCase() : "GET",
      url: rec.url,
      status: num(rec.status),
      ms: num(rec.ms),
      request: message(rec.request),
      response: message(rec.response),
    });
  });
  return calls;
}

/** `/version?x=1` from an absolute URL (the host is the preview's, shown elsewhere). */
export function apiCallPath(url: string): string {
  try {
    const parsed = new URL(url);
    return `${parsed.pathname}${parsed.search}`;
  } catch {
    return url;
  }
}

/** A JSON body pretty-printed; anything else as is. */
export function prettyBody(body: string | null): string {
  if (!body) return "";
  const trimmed = body.trim();
  if (!/^[[{]/.test(trimmed)) return body;
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2);
  } catch {
    return body;
  }
}

/** A one-line body preview for the collapsed row. */
export function bodyPreview(body: string | null, max = 72): string {
  if (!body) return "";
  const flat = body.replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

export function statusTone(status: number | null): FactoryTone {
  if (status == null) return "muted";
  if (status >= 500) return "fail";
  if (status >= 400) return "warning";
  if (status >= 200 && status < 400) return "pass";
  return "muted";
}

// ── Preview ────────────────────────────────────────────────────

export interface PreviewHealth {
  tone: FactoryTone;
  label: string;
  /** The preview is up (ready or stale) and its links work. */
  reachable: boolean;
}

/** The preview panel's headline: healthy, behind head, deploying, parked, … */
export function previewHealth(pr: PrView, preview: PreviewView): PreviewHealth {
  const truth = previewHeadTruth(preview, pr.head_sha);
  switch (preview.state) {
    case "ready":
      return truth.matches
        ? { tone: "pass", label: "healthy", reachable: true }
        : { tone: "warning", label: "behind head", reachable: true };
    case "stale":
      return { tone: "warning", label: "behind head", reachable: true };
    case "deploying":
      return { tone: "pending", label: "deploying", reachable: false };
    case "parking":
      return { tone: "muted", label: "parking", reachable: false };
    case "parked":
      return { tone: "muted", label: "parked", reachable: false };
    case "gone":
      return { tone: "fail", label: "unreachable", reachable: false };
    case "failed":
      return { tone: "fail", label: "deploy failed", reachable: false };
    case "down":
      return { tone: "muted", label: "torn down", reachable: false };
  }
}

/** When the server last looked at the preview (probe, else the agent's report). */
export function previewSeenAt(preview: PreviewView): {
  at: number | null;
  source: "probed" | "reported";
} {
  const probed = preview.observed?.at ?? preview.probe?.at ?? null;
  if (probed != null) return { at: probed, source: "probed" };
  return { at: preview.reported_at ?? preview.deployed.at, source: "reported" };
}

/** The preview's links in display order, with what the prober measured on each. */
export function previewLinks(preview: PreviewView): {
  key: "app" | "api" | "minio" | "tilt";
  url: string;
  code: number | null;
  ms: number | null;
}[] {
  const out: {
    key: "app" | "api" | "minio" | "tilt";
    url: string;
    code: number | null;
    ms: number | null;
  }[] = [];
  const probe = preview.probe;
  if (preview.urls.app) {
    out.push({
      key: "app",
      url: preview.urls.app,
      code: probe?.app?.code ?? null,
      ms: probe?.app?.ms ?? null,
    });
  }
  if (preview.urls.api) {
    out.push({
      key: "api",
      url: preview.urls.api,
      code: probe?.api?.code ?? null,
      ms: probe?.api?.ms ?? null,
    });
  }
  if (preview.urls.minio) out.push({ key: "minio", url: preview.urls.minio, code: null, ms: null });
  if (preview.urls.tilt_ui) {
    out.push({ key: "tilt", url: preview.urls.tilt_ui, code: null, ms: null });
  }
  return out;
}

/** An absolute URL for a same-origin path (clipboard text leaves the page). */
export function absoluteUrl(path: string, origin: string): string {
  try {
    return new URL(path, origin).toString();
  } catch {
    return path;
  }
}

// ── Older demos ────────────────────────────────────────────────

export interface OlderDemo {
  demoId: string;
  sha7: string | null;
  status: string | null;
  at: number;
}

/**
 * Earlier demos of `pr`, newest first, from its `demo.published` events
 * (the snapshot carries only the newest demo and a count).
 */
export function olderDemos(events: EventView[], pr: PrView, currentId: string | null): OlderDemo[] {
  const subject = `pr:${pr.repo}#${pr.number}`;
  const seen = new Set<string>();
  const out: OlderDemo[] = [];
  for (const event of events) {
    if (event.kind !== "demo.published") continue;
    const data = event.data ?? {};
    const prId = typeof data.pr_id === "string" ? data.pr_id : null;
    if (event.subject !== subject && prId !== pr.id) continue;
    const demoId = typeof data.demo_id === "string" ? data.demo_id : null;
    if (!demoId || demoId === currentId || seen.has(demoId)) continue;
    seen.add(demoId);
    out.push({
      demoId,
      sha7: event.summary.match(/\bat ([0-9a-f]{7,40})\b/)?.[1]?.slice(0, 7) ?? null,
      status: typeof data.status === "string" ? data.status : null,
      at: Math.floor(event.ts / 1000),
    });
  }
  return out.sort((a, b) => b.at - a.at);
}

// ── Merge summary ──────────────────────────────────────────────

/**
 * What "Copy merge summary" puts on the clipboard: the PR, its goal, each
 * gate with its evidence, Sentinel, and the demo and preview links.
 */
export function mergeSummary(
  pr: PrView,
  project: ProjectView | null,
  origin: string,
  factoryId: string,
): string {
  const lines = [`${pr.repo}#${pr.number} · ${pr.title}`, pr.url];
  if (project?.charter?.goal) lines.push(`Goal: ${project.charter.goal}`);
  lines.push(`Head ${shortSha(pr.head_sha)} · +${pr.additions} −${pr.deletions}`, "", "Gates:");
  const { evidence } = pr.gates;
  for (const gate of shownGates(pr)) {
    const state = gate === "demo" ? pr.gates.demo : pr.gates[gate];
    let detail = "";
    if (gate === "checks" && evidence.checks) {
      detail = ` ${evidence.checks.passing}/${evidence.checks.total}`;
    } else if (gate === "review" && evidence.review) {
      detail = ` ${evidence.review.verdict ?? ""} @${shortSha(evidence.review.sha)}`;
    } else if (gate === "verify" && evidence.verify) {
      detail = ` ${evidence.verify.result ?? ""} @${shortSha(evidence.verify.sha)}`;
    } else if (gate === "demo" && pr.demo) {
      detail = ` ${pr.demo.status} @${shortSha(pr.demo.head_sha)}`;
    }
    lines.push(`- ${GATE_KEY_LABEL[gate]}: ${state ?? "n/a"}${detail}`);
  }
  if (pr.sentinel) {
    lines.push(
      `- Sentinel: ${pr.sentinel.verdict} @${shortSha(pr.sentinel.head_sha)}${pr.sentinel.note ? ` (${pr.sentinel.note})` : ""}`,
    );
  }
  lines.push("");
  lines.push(`Review: ${absoluteUrl(`/factory/${factoryId}?review=${pr.number}`, origin)}`);
  if (pr.demo) {
    const video = demoVideo(pr.demo);
    lines.push(
      `Demo: ${pr.demo.status} at ${shortSha(pr.demo.head_sha)}${video ? ` · ${absoluteUrl(demoFileHref(pr.demo, video), origin)}` : ""}`,
    );
  }
  if (pr.preview) {
    lines.push(`Preview: ${absoluteUrl(pr.preview.stable_url ?? prPreviewUrl(pr.id), origin)}`);
  }
  return lines.join("\n");
}
