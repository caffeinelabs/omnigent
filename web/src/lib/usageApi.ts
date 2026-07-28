// Client for the aggregated usage + cost analytics endpoint
// (`GET /v1/usage/summary`). Mirrors the server's `UsageSummaryResponse`
// (omnigent/server/schemas.py) so the Usage settings section can render a
// caller's own spend — or, for an admin, any user's or every user's — broken
// down by provider / harness / model over a time window.

import { authenticatedFetch } from "@/lib/identity";

export type UsagePeriod = "today" | "7days" | "30days";
export type UsageGroupBy = "provider" | "harness" | "model";

/** One rollup row for a single value of the requested grouping dimension. */
export interface UsageGroupEntry {
  group_key: string;
  input_tokens: number;
  output_tokens: number;
  total_cost_usd: number;
}

/** One point in the day/hour spend time series. */
export interface UsageBucketEntry {
  /** Unix epoch seconds of the bucket start (top of hour, or midnight UTC). */
  bucket_start: number;
  input_tokens: number;
  output_tokens: number;
  total_cost_usd: number;
}

/** Response shape of `GET /v1/usage/summary`. */
export interface UsageSummary {
  period: UsagePeriod;
  group_by: UsageGroupBy;
  start_epoch: number;
  /** Scope actually applied — a user id, or null for the all-users view. */
  user: string | null;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost_usd: number;
  groups: UsageGroupEntry[];
  buckets: UsageBucketEntry[];
}

/** The all-users sentinel the server accepts for the admin `user` param. */
export const USAGE_USER_ALL = "all";

export interface UsageSummaryParams {
  period: UsagePeriod;
  groupBy: UsageGroupBy;
  /**
   * Admin-only scope. Omit for the caller's own spend; `"all"` for every
   * user; otherwise a specific user id. A non-admin who sends it gets 403;
   * single-user deploys reject it with 400 — callers gate this in the UI.
   */
  user?: string;
}

export async function fetchUsageSummary({
  period,
  groupBy,
  user,
}: UsageSummaryParams): Promise<UsageSummary> {
  const qs = new URLSearchParams({ period, group_by: groupBy });
  if (user !== undefined) qs.set("user", user);
  const res = await authenticatedFetch(`/v1/usage/summary?${qs.toString()}`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as UsageSummary;
}

// ── Formatting ──────────────────────────────────────────────────────

/**
 * Format a USD amount. Sub-cent spend collapses to `<$0.01` so a tiny but
 * non-zero cost never renders as a misleading `$0.00`; zero stays `$0.00`.
 */
export function formatUsd(value: number): string {
  if (value > 0 && value < 0.01) return "<$0.01";
  return value.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/** Compact token count, e.g. `1.2M`, `34K`, `512`. */
export function formatTokens(value: number): string {
  return new Intl.NumberFormat(undefined, {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

/** Em dash shown for a token count a harness didn't report (see below). */
export const TOKENS_NOT_REPORTED = "—";

/**
 * Format a token count, distinguishing "not reported" from a real zero.
 * Native harnesses (e.g. claude-native) forward a cumulative *cost* with no
 * token counts, so a group/window can carry spend while its token totals stay
 * 0. Render those as `—` (not `0`) so the gap reads as missing data, not
 * absence of usage; a genuine zero-cost, zero-token row still shows `0`.
 */
export function formatTokensOrDash(tokens: number, costUsd: number): string {
  if (tokens > 0) return formatTokens(tokens);
  return costUsd > 0 ? TOKENS_NOT_REPORTED : formatTokens(0);
}

/**
 * Label a bucket start for an axis / tooltip. `today` buckets by hour so it
 * shows the hour; day buckets show a short month + day. Epoch is seconds.
 */
export function formatBucketLabel(epochSeconds: number, period: UsagePeriod): string {
  const d = new Date(epochSeconds * 1000);
  if (period === "today") {
    return d.toLocaleTimeString(undefined, { hour: "numeric" });
  }
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** Human label for the grouping dimension (used in headings / column title). */
export function groupByLabel(groupBy: UsageGroupBy): string {
  return groupBy === "provider" ? "Provider" : groupBy === "harness" ? "Harness" : "Model";
}
