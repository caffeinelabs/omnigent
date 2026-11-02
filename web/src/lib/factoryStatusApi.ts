// The Overview's "Now": `GET /v1/factory/factories/<id>/status-summary`.
//
// The types mirror `omnigent/server/factory/status_summary.py` field for field
// (snake_case wire shape). Times are epoch seconds and texts carry no
// durations: the cockpit ages them against its own clock. Every `href` is a
// cockpit path (`/factory/<id>/…`), the same the factory routes build.

import { authenticatedFetch } from "./identity";
import { factoryErrorFromResponse } from "./factoryApi";

export type WaitingKind = "decision" | "pr" | "incident";

/** One thing that waits on a person. */
export interface StatusWaitingItem {
  kind: WaitingKind;
  key: string;
  /** `D-19`, `PR #9774` or `INC-3`. */
  ref: string;
  /** What to do, e.g. "approve Atlas's proposal" (an incident's summary). */
  text: string;
  /** The question or PR title. */
  detail?: string | null;
  /** `you`, `operators`, `owners` or a person's short name. */
  who: string;
  /** The viewer may act on it (theirs, or their role's). */
  for_you: boolean;
  /** A blocking decision or a critical incident. */
  urgent: boolean;
  since: number;
  project?: string | null;
  severity?: string | null;
  href: string;
}

export interface StatusWorkingPr {
  id: string;
  number: number;
  state: string;
  ready: boolean;
  title: string;
  href: string;
}

/** One project in progress. */
export interface StatusWorkingItem {
  project: string;
  title: string;
  stage: string;
  /** When it entered its stage. */
  since: number;
  pr?: StatusWorkingPr | null;
  /** Its lead agent's own one-liner. */
  doing?: string | null;
  /** Plain words for a blocked, failed or paused project. */
  problem?: string | null;
  running: boolean;
  href: string;
}

/** A project that has not started yet, and why. */
export interface StatusNextItem {
  project: string;
  title: string;
  /** Why it waits, read after its name: "waits for api-version to finish". */
  reason: string;
  /** Set when it can't start (its lead's machine failed, blocked, paused). */
  problem?: string | null;
  since: number;
  href: string;
}

/** What the root lead (Atlas) last said it is doing. */
export interface StatusLeadNote {
  who: string;
  text: string;
  /** When the lead was last active (the one-liner is at most this fresh). */
  since?: number | null;
  session_id: string;
  href: string;
}

/** When the root lead (Atlas) next wakes up on its own. */
export interface StatusCheckIn {
  who: string;
  at: number;
  session_id: string;
  href: string;
}

export interface StatusSpend {
  /** The last 24 h (yesterday's UTC day prorated); null when nothing was priced. */
  last_24h_usd?: number | null;
  today_usd?: number | null;
  usd_per_h?: number | null;
  total_usd?: number | null;
  /** No project in progress or queued. */
  idle: boolean;
  idle_since?: number | null;
  /** Idle, yet still spending at least a cent an hour. */
  idle_spend: boolean;
  href: string;
}

export type StatusHealthKind =
  "paused" | "done" | "blocked" | "failed" | "project_paused" | "github";

export interface StatusHealthItem {
  kind: StatusHealthKind;
  text: string;
  /** Who paused the factory. */
  by?: string | null;
  /** The note they left. */
  note?: string | null;
  project?: string | null;
  since?: number | null;
  href: string;
}

export interface StatusHealth {
  state: "ok" | "paused" | "done" | "attention";
  items: StatusHealthItem[];
}

export interface StatusCounts {
  waiting_for_you: number;
  waiting_total: number;
  working: number;
  queued: number;
  /** Queued projects that can't start (counted in `queued` too). */
  cant_start?: number;
}

/** The factory's "Now" for one viewer. */
export interface StatusSummary {
  factory_id: string;
  factory_name: string;
  generated_at: number;
  /** One plain sentence without times (digests read it). */
  headline: string;
  state: "working" | "idle" | "paused" | "done";
  waiting: StatusWaitingItem[];
  working: StatusWorkingItem[];
  next: StatusNextItem[];
  lead?: StatusLeadNote | null;
  check_in?: StatusCheckIn | null;
  spend: StatusSpend;
  health: StatusHealth;
  counts: StatusCounts;
}

/** Read one factory's "Now" as the viewer may see it. */
export async function fetchFactoryStatusSummary(
  factoryId: string,
  signal?: AbortSignal,
): Promise<StatusSummary> {
  const res = await authenticatedFetch(
    `/v1/factory/factories/${encodeURIComponent(factoryId)}/status-summary`,
    { signal },
  );
  if (!res.ok) throw await factoryErrorFromResponse(res);
  return (await res.json()) as StatusSummary;
}
