// Pure model of the Insights page: which KPI tiles show what, how a metric's
// headline, delta and n read, when n is too small to trust, the day series a
// sparkline draws, and where version markers fall on a window.

import type {
  DefinitionMarker,
  MetricAggregate,
  MetricGroupBy,
  MetricResult,
  PlatformMarker,
} from "@/lib/factoryApi";
import type { FactoryTone } from "../factoryFormat";
import { formatElapsed } from "../factoryFormat";

export const WINDOWS = ["24h", "7d", "30d", "90d"] as const;
export type InsightsWindow = (typeof WINDOWS)[number];
export const DEFAULT_WINDOW: InsightsWindow = "7d";

/** Below this n a number is shown but called inconclusive (trials need 3 merged PRs too). */
export const MIN_N = 3;

/** The window's length in seconds. */
export function windowSeconds(window: string): number {
  const amount = Number.parseFloat(window);
  const unit = window.slice(-1);
  const scale = unit === "h" ? 3600 : unit === "m" ? 60 : 86_400;
  return Number.isFinite(amount) && amount > 0 ? amount * scale : 7 * 86_400;
}

export interface GroupOption {
  key: MetricGroupBy | "factory";
  label: string;
  /** Per-person numbers are for owners only. */
  ownersOnly?: boolean;
}

export const GROUP_OPTIONS: GroupOption[] = [
  { key: "factory", label: "Factory" },
  { key: "project", label: "Project" },
  { key: "role", label: "Role" },
  { key: "agent", label: "Agent" },
  { key: "version", label: "Platform version" },
  { key: "def_version", label: "Definition version" },
  { key: "day", label: "Day" },
  { key: "person", label: "Person", ownersOnly: true },
];

/** Whether a higher value is better; `null` for numbers with no good direction. */
export type GoodDirection = "up" | "down" | null;

export interface KpiSpec {
  name: string;
  title: string;
  good: GoodDirection;
}

/** The header tiles in order (the server's `KPIS`). */
export const KPI_SPECS: KpiSpec[] = [
  { name: "merge_acceptance", title: "Merged · acceptance", good: "up" },
  { name: "lead_time", title: "Lead time p50 / p90", good: "down" },
  { name: "human_wait", title: "Human wait", good: "down" },
  { name: "human_prompts_per_day", title: "Human load", good: null },
  { name: "cost_per_merged_pr", title: "$ / merged PR", good: "down" },
  { name: "incidents", title: "Incidents · MTTR", good: "down" },
  { name: "engine_wake_efficiency", title: "Engine", good: "up" },
];

/** Good directions of the other catalog metrics (deltas and guards read them). */
const GOOD: Record<string, GoodDirection> = {
  ...Object.fromEntries(KPI_SPECS.map((k) => [k.name, k.good])),
  time_to_first_pr: "down",
  task_cycle_time: "down",
  stage_dwell: "down",
  throughput: "up",
  loop_iterations: "down",
  loop_exhaustion_rate: "down",
  first_pass_yield: "up",
  review_rounds: "down",
  escape_rate: "down",
  sentinel_agreement: "up",
  claim_mismatch_rate: "down",
  human_ratings: "up",
  interventions_per_merged_pr: "down",
  decision_latency: "down",
  unnecessary_questions: "down",
  chat_reply_time: "down",
  proposal_health: "up",
  cost_usd: "down",
  heartbeat_share: "down",
  wasted_usd: "down",
  subagent_failure_rate: "down",
  mail_health: "down",
  env_availability: "up",
};

/** Every catalog metric (Tier 0: the server's `CATALOG`), for "expected to move". */
export const METRIC_NAMES = [
  "lead_time",
  "time_to_first_pr",
  "task_cycle_time",
  "stage_dwell",
  "human_wait",
  "throughput",
  "parallelism",
  "critical_path_accuracy",
  "loop_iterations",
  "loop_exit_mix",
  "loop_exhaustion_rate",
  "first_pass_yield",
  "review_rounds",
  "budget_extensions",
  "merge_acceptance",
  "escape_rate",
  "sentinel_agreement",
  "claim_mismatch_rate",
  "human_ratings",
  "human_prompts_per_day",
  "interventions_per_merged_pr",
  "decision_latency",
  "unnecessary_questions",
  "chat_reply_time",
  "proposal_health",
  "cost_usd",
  "cost_per_merged_pr",
  "heartbeat_share",
  "wasted_usd",
  "incidents",
  "subagent_failure_rate",
  "mail_health",
  "env_availability",
  "engine_wake_efficiency",
] as const;

export function goodDirection(metric: string): GoodDirection {
  return GOOD[metric] ?? null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * The single number a tile or guard compares: `value`, else `p50`, else the
 * sum over a per-key breakdown, else a bounce rate (the server's `headline`).
 */
export function headline(result: MetricAggregate | null | undefined): number | null {
  if (!result) return null;
  for (const key of ["value", "p50", "bounce_rate"] as const) {
    const found = num(result[key]);
    if (found != null) return found;
  }
  if (result.by) {
    const parts = Object.values(result.by)
      .map((agg) => headline(agg))
      .filter((v): v is number => v != null);
    return parts.length ? parts.reduce((a, b) => a + b, 0) : null;
  }
  return null;
}

/** `"3h 10m"`, `"79%"`, `"$8.20"`, `"34/day"`, `"4.2★"`, `"1.3×"`, `"2.5"`; `"—"` for no data. */
export function formatValue(value: number | null | undefined, unit: string): string {
  if (value == null || !Number.isFinite(value)) return "—";
  switch (unit) {
    case "s":
      return formatElapsed(value) ?? "—";
    case "share":
      return `${Math.round(value * 100)}%`;
    case "usd":
      return value > 0 && value < 0.01 ? "<$0.01" : `$${value.toFixed(2)}`;
    case "per_day":
      return `${value < 10 ? value.toFixed(1) : Math.round(value)}/day`;
    case "rating":
      return `${value.toFixed(1)}★`;
    case "ratio":
      return `${value.toFixed(2)}×`;
    case "count":
      return String(Math.round(value));
    default:
      return Number.isInteger(value) ? String(value) : value.toFixed(1);
  }
}

/** `"+6 pp"`, `"−12m"`, `"+$1.10"`, `"+2"`; null for no change or no data. */
export function formatDelta(delta: number | null | undefined, unit: string): string | null {
  if (delta == null || !Number.isFinite(delta) || delta === 0) return null;
  const sign = delta > 0 ? "+" : "−";
  const abs = Math.abs(delta);
  switch (unit) {
    case "share":
      return `${sign}${Math.round(abs * 100)} pp`;
    case "s":
      return `${sign}${formatElapsed(abs)}`;
    case "usd":
      return `${sign}$${abs.toFixed(2)}`;
    case "per_day":
      return `${sign}${abs < 10 ? abs.toFixed(1) : Math.round(abs)}/day`;
    default:
      return `${sign}${Number.isInteger(abs) ? abs : abs.toFixed(1)}`;
  }
}

/** Green when the delta goes the good way, amber the other way, grey without a direction. */
export function deltaTone(delta: number | null | undefined, good: GoodDirection): FactoryTone {
  if (delta == null || delta === 0 || good == null) return "muted";
  return delta > 0 === (good === "up") ? "pass" : "warning";
}

/** How trustworthy the number is: none at n=0, inconclusive below {@link MIN_N}. */
export type NState = "none" | "small" | "ok";

export function nState(n: number | null | undefined): NState {
  if (!n) return "none";
  return n < MIN_N ? "small" : "ok";
}

/** `"n=14"`, `"n=2 — inconclusive"`, `"no data yet"`. */
export function nText(n: number | null | undefined): string {
  switch (nState(n)) {
    case "none":
      return "no data yet";
    case "small":
      return `n=${n} — inconclusive`;
    default:
      return `n=${n}`;
  }
}

/** UTC days `YYYY-MM-DD` covering `[since, until)`, oldest first (the server's day groups). */
export function windowDays(since: number, until: number): string[] {
  const days: string[] = [];
  const start = new Date(since * 1000);
  start.setUTCHours(0, 0, 0, 0);
  for (let t = start.getTime(); t < until * 1000; t += 86_400_000) {
    days.push(new Date(t).toISOString().slice(0, 10));
  }
  return days;
}

/** A metric's headline per day of the window (null on a day without samples). */
export function daySeries(
  groups: Record<string, MetricAggregate> | undefined,
  days: readonly string[],
): (number | null)[] {
  return days.map((day) => headline(groups?.[day]));
}

/** A day series summed to running totals (cumulative flow of merged PRs). */
export function cumulative(series: readonly (number | null)[]): number[] {
  let total = 0;
  return series.map((v) => (total += v ?? 0));
}

/** One version marker on the page: `def v7` or `img staging-0bb15c1`. */
export interface VersionMark {
  key: string;
  kind: "def" | "platform";
  label: string;
  at: number;
  /** The definition version (def markers): clicking filters to it. */
  version: number | null;
  title: string;
  current: boolean;
}

function platformLabel(p: PlatformMarker): string {
  if (p.image_tag) return p.image_tag.replace(/^staging-/, "img ");
  if (p.server_sha) return `img ${p.server_sha.slice(0, 7)}`;
  return `img ${p.id.slice(0, 7)}`;
}

/**
 * The markers to show for a window: every definition and platform version
 * that started inside it, plus the one in force when it opened (so a window
 * with no change still says which versions it measured).
 */
export function versionMarks(
  definitions: readonly DefinitionMarker[],
  platform: readonly PlatformMarker[],
  since: number,
): VersionMark[] {
  const pick = <T extends { created_at: number }>(rows: readonly T[]) => {
    const sorted = [...rows].sort((a, b) => a.created_at - b.created_at);
    const before = sorted.filter((r) => r.created_at < since);
    const inside = sorted.filter((r) => r.created_at >= since);
    return [...before.slice(-1), ...inside];
  };
  const defs: VersionMark[] = pick(definitions).map((d) => ({
    key: `def-${d.version}`,
    kind: "def",
    label: `def v${d.version}`,
    at: d.created_at,
    version: d.version,
    title: `Definition v${d.version} · ${d.created_via}${d.proposal_ref ? ` · ${d.proposal_ref}` : ""} by ${d.created_by}`,
    current: d.pinned,
  }));
  const imgs: VersionMark[] = pick(platform).map((p) => ({
    key: `img-${p.id}`,
    kind: "platform",
    label: platformLabel(p),
    at: p.created_at,
    version: null,
    title: `Platform ${p.image_tag ?? p.id}${p.server_sha ? ` · ${p.server_sha.slice(0, 12)}` : ""}`,
    current: p.current,
  }));
  return [...defs, ...imgs].sort((a, b) => a.at - b.at);
}

/** Where `at` falls on `[since, until)` as 0..1 (clamped; before the window is 0). */
export function markPosition(at: number, since: number, until: number): number {
  if (until <= since) return 0;
  return Math.min(1, Math.max(0, (at - since) / (until - since)));
}

/** Per-key values of a breakdown metric, largest first (`stage_dwell`, `first_pass_yield`, …). */
export function byRows(
  result: MetricResult | MetricAggregate | null | undefined,
): { key: string; value: number | null; p90: number | null; n: number }[] {
  return Object.entries(result?.by ?? {})
    .map(([key, agg]) => ({
      key: key || "other",
      value: headline(agg),
      p90: num(agg.p90),
      n: agg.n ?? 0,
    }))
    .sort((a, b) => (b.value ?? -1) - (a.value ?? -1));
}

/** Shares of a mix metric, largest first (`loop_exit_mix`). */
export function mixRows(
  result: MetricResult | null | undefined,
): { key: string; count: number; share: number }[] {
  return Object.entries(result?.mix ?? {})
    .map(([key, v]) => ({ key: key || "other", count: v.count, share: v.share ?? 0 }))
    .sort((a, b) => b.count - a.count);
}

/** A record of counts (closure reasons, a rating distribution), largest first. */
export function countRows(value: unknown): { key: string; count: number }[] {
  if (value == null || typeof value !== "object") return [];
  return Object.entries(value as Record<string, unknown>)
    .map(([key, count]) => ({ key, count: typeof count === "number" ? count : 0 }))
    .filter((r) => r.count > 0)
    .sort((a, b) => b.count - a.count);
}

/** `"wrong_scope"` → `"wrong scope"`. */
export function humanize(key: string): string {
  return key.replaceAll("_", " ");
}

// Metric names whose words alone read poorly ("cost per merged pr").
const METRIC_LABEL: Record<string, string> = {
  cost_per_merged_pr: "$ / merged PR",
  cost_usd: "cost $",
  wasted_usd: "$ wasted",
  heartbeat_usd_per_day: "heartbeat $ / day",
  human_prompts_per_day: "human prompts / day",
  interventions_per_merged_pr: "interventions / merged PR",
  decisions_per_pr: "decisions / PR",
  engine_wake_efficiency: "NEXT wakes",
  time_to_first_pr: "time to first PR",
};

/** A metric key as people read it: `cost_per_merged_pr` → `$ / merged PR`. */
export function metricLabel(key: string): string {
  return METRIC_LABEL[key] ?? humanize(key);
}

/** The Daisy band of human prompts per day (30–50), and the lead/PL/other split. */
export function promptBand(result: MetricResult | null | undefined): {
  perDay: number | null;
  band: [number, number];
  inBand: boolean | null;
  split: { lead: number | null; pl: number | null; other: number | null };
} {
  const raw = (result?.band as number[] | undefined) ?? [30, 50];
  const split = (result?.split as Record<string, number | null> | undefined) ?? {};
  return {
    perDay: num(result?.value),
    band: [raw[0] ?? 30, raw[1] ?? 50],
    inBand: typeof result?.in_band === "boolean" ? result.in_band : null,
    split: {
      lead: num(split.lead),
      pl: num(split.project_lead),
      other: num(split.other),
    },
  };
}

/** `"L58 P36 O6"`: the lead / PL / other split in percent. */
export function splitText(split: ReturnType<typeof promptBand>["split"]): string | null {
  const parts = [
    ["L", split.lead],
    ["P", split.pl],
    ["O", split.other],
  ] as const;
  const shown = parts.filter(([, v]) => v != null);
  if (shown.length === 0) return null;
  return shown.map(([k, v]) => `${k}${Math.round((v ?? 0) * 100)}`).join(" ");
}
