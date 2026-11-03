// The Overview's "Now": `GET /v1/factory/factories/<id>/status-summary`.
//
// The types mirror `omnigent/server/factory/status_summary.py` field for field
// (snake_case wire shape). Times are epoch seconds and texts carry no
// durations: the cockpit ages them against its own clock. Every `href` is a
// cockpit path (`/factory/<id>/…`), the same the factory routes build.

import { authenticatedFetch } from "./identity";
import { factoryErrorFromResponse } from "./factoryApi";
import type { InitiativeLine } from "./factoryInitiativesApi";

export type WaitingKind = "decision" | "pr" | "incident";

/** One Slack DM the viewer got about a waiting item. */
export interface StatusSlackReceipt {
  at: number;
  /** `new` / `ready` / `opened` (the first DM), `r4h` / `r24h` (reminders),
   * `owners` (escalated) or `deadline` (last call). */
  stage: string;
}

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
  /** All its project still needs: the project is not also under Working on. */
  holds?: boolean;
  /** When an open decision defaults by itself, and what happens then. */
  deadline_at?: number | null;
  deadline_effect?: string | null;
  /** The viewer's Slack DMs about it, oldest first. */
  slack?: StatusSlackReceipt[];
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
  /** One plain line on where it is, from its stage, task and PR (never the raw label). */
  status?: string;
  /** `facts`, or `lead` when only the lead's own label (trimmed) says anything. */
  status_source?: "facts" | "lead";
  /** Its lead agent's own one-liner, verbatim: not for display. */
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
  /** When the start step the reason names began (e.g. pulling its image). */
  step_since?: number | null;
  href: string;
}

/** A run of text that links when it names a page (a PR, decision or incident). */
export interface StatusTextPart {
  text: string;
  href?: string | null;
}

/** What the root lead (Atlas) last said, trimmed at a word. */
export interface StatusLeadNote {
  who: string;
  text: string;
  /** `text` split so the ids it names link (newer servers). */
  parts?: StatusTextPart[];
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
  /** Projects that only wait on people (not in `working`). */
  held?: number;
  /** Rows of the one Needs-you list (`GET …/asks`) for the viewer (absent on older servers). */
  needs_you?: number;
}

/** Red broken, amber waits on you, grey paused / finished / idle, green working. */
export type VerdictDot = "red" | "amber" | "green" | "grey";

/** One fact behind the verdict's dot, worst first (`blocked` projects last: named, no colour of their own). */
export interface StatusVerdictReason {
  kind: "project" | "agent" | "incident" | "github" | "supervisor" | "waiting" | "blocked";
  text: string;
  href: string;
}

/** The top bar's one answer, built from facts by fixed rules (never by an agent). */
export interface StatusVerdict {
  dot: VerdictDot;
  /** e.g. `Running, 1 project stopped` or `Running smoothly`. */
  sentence: string;
  reasons: StatusVerdictReason[];
  usd_per_h?: number | null;
  /** The factory's hourly spend alert, when known. */
  usd_per_h_alert?: number | null;
}

/** The factory's "Now" for one viewer. */
export interface StatusSummary {
  factory_id: string;
  factory_name: string;
  generated_at: number;
  /** One plain line without times, e.g. `4 PRs wait on you · 1 project in progress`
   * (the card and the Slack digest both show it). */
  headline: string;
  /** The top bar's dot and sentence (absent on older servers). */
  verdict?: StatusVerdict | null;
  state: "working" | "idle" | "paused" | "done";
  waiting: StatusWaitingItem[];
  working: StatusWorkingItem[];
  next: StatusNextItem[];
  lead?: StatusLeadNote | null;
  check_in?: StatusCheckIn | null;
  spend: StatusSpend;
  health: StatusHealth;
  counts: StatusCounts;
  /** Initiatives being built, one line each (absent when none, and on older servers). */
  initiatives?: InitiativeLine[];
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
