// Initiatives: one outcome a person asked for that several projects build.
//
// The types mirror `omnigent/server/factory/initiative_view.py` field for
// field (snake case wire shape). `InitiativeLine` is the one line Home's card,
// the status summary and the Slack digest show (it rides on `GET …/asks` as
// `initiatives`, absent when there are none); `InitiativeView` is the
// initiative page (`GET …/initiatives/<I-n>`). Times are epoch seconds; every
// `href` is a cockpit path; evidence links are server paths
// (`/v1/factory/demos/<id>/files/<id>`).

import { factoryErrorFromResponse } from "./factoryApi";
import { authenticatedFetch } from "./identity";

/**
 * Computed from the projects and the acceptance runs: `planned` (nothing
 * started), `building`, `on_base` (the walking skeleton merged and the base
 * env serves it), `accepting` (a run is on its way), `failing` (the last run
 * failed a step), `in_review` (built, no passing done review yet),
 * `follow_ups` (a done review's follow-ups run), `done` (a done review
 * passed, or a person said so), `closed` (a person closed it), `cancelled`.
 */
export type InitiativeState =
  | "planned"
  | "building"
  | "on_base"
  | "accepting"
  | "failing"
  | "in_review"
  | "follow_ups"
  | "done"
  | "closed"
  | "cancelled";

export type InitiativeRole = "skeleton" | "surface" | "part";

/** How the last acceptance run went. */
export interface InitiativeTally {
  total: number;
  passed: number;
  failed: number;
  skipped: number;
  /** `PASS`, `PARTIAL`, `FAIL` or `ERROR` of the last finished run. */
  status: string | null;
  run: number | null;
  /** A run is on its way now. */
  running: boolean;
}

/** One initiative as one line. */
export interface InitiativeLine {
  ref: string;
  title: string;
  state: InitiativeState;
  /** `Sample assistant: 2/4 merged, skeleton not on base env yet, waiting on …`. */
  line: string;
  merged: number;
  total: number;
  skeleton?: string | null;
  skeleton_on_base: boolean;
  acceptance: InitiativeTally;
  waiting_on: string[];
  /** `wave 2: Call the connectors`. */
  next_wave?: string | null;
  /** Its done review in one line: `Review: not done · 2 follow-ups started`. */
  review?: string | null;
  cost_usd?: number | null;
  /** When it was asked. */
  since: number;
  href: string;
  /** The base env's app once the walking skeleton is on it: people try it there. */
  try_url?: string | null;
  /** The flag key it ships behind (turn it on to try it). */
  flag?: string | null;
}

export interface InitiativeAsker {
  kind: "human" | "agent";
  id?: string | null;
  name: string;
}

/** One acceptance step with how it went in the last finished run. */
export interface InitiativeStep {
  n: number;
  step: string;
  expect?: string | null;
  /** The project whose code it exercises (a failure becomes its task). */
  owner?: string | null;
  /** What an env may lack: `login`, `build`, `ai`, `external`. */
  needs?: string[] | null;
  last: {
    status: "pass" | "fail" | "skipped" | string | null;
    note?: string | null;
    screenshot_url?: string | null;
    run?: number | null;
  } | null;
}

export interface InitiativeDependency {
  slug: string;
  /** The real code or API dependency it waits for. */
  why?: string | null;
  met: boolean;
}

export interface InitiativeProject {
  slug: string;
  hidden?: boolean;
  title?: string;
  outcome?: string | null;
  stage?: string | null;
  status?: string | null;
  role: InitiativeRole;
  wave?: number;
  merged?: boolean;
  depends_on?: InitiativeDependency[];
  /** `core`, `a merge of !12`, `an answer to D-42`. */
  waiting_on?: string[];
  prs?: { open: number; ready: number; merged: number };
  cost_usd?: number | null;
  href?: string;
}

export interface InitiativeRun {
  n: number;
  /** `due`, `requested`, `blocked`, `expired`, `PASS`, `PARTIAL`, `FAIL`, `ERROR`. */
  status: string;
  sha?: string | null;
  pr?: number | null;
  due_at?: number | null;
  requested_at?: number | null;
  reported_at?: number | null;
  requested_of?: string | null;
  note?: string | null;
  video_url?: string | null;
  demo_id?: string | null;
  /** Tasks its failed steps became (`T-12`). */
  tasks: string[];
  app_url?: string | null;
}

export interface InitiativeWave {
  n: number;
  proposal?: string | null;
  added: string[];
  at?: number | null;
}

export interface InitiativeView {
  id: string;
  ref: string;
  number: number;
  title: string;
  /** The person's ask, verbatim. */
  ask: string;
  asked_by: InitiativeAsker | null;
  asked_at: number;
  age_s: number | null;
  ask_ref?: string | null;
  thread?: string | null;
  outcome?: string | null;
  flag?: { key?: string; none?: string } | null;
  status: "open" | "done" | "cancelled";
  state: InitiativeState;
  skeleton?: string | null;
  skeleton_on_base: boolean;
  merged: number;
  total: number;
  hidden: number;
  projects: InitiativeProject[];
  acceptance: InitiativeStep[];
  tally: InitiativeTally;
  runs: InitiativeRun[];
  next_wave: { n: number; goal: string; projects: string[] } | null;
  waves: InitiativeWave[];
  waiting_on: string[];
  cost_usd: number | null;
  base: {
    repo?: string | null;
    branch?: string | null;
    head_sha?: string | null;
    env_state?: string | null;
    env_sha?: string | null;
    app_url?: string | null;
    env_project?: string | null;
  } | null;
  opportunity?: string | null;
  created_by?: string | null;
  created_at?: number | null;
  updated_at?: number | null;
  closed_at?: number | null;
  closed_by?: string | null;
  href: string;
  /** The same one line Home shows. */
  line?: string;
}

const base = (factoryId: string) =>
  `/v1/factory/factories/${encodeURIComponent(factoryId)}/initiatives`;

/** `GET …/initiatives/<ref>`. */
export async function fetchInitiative(
  factoryId: string,
  ref: string,
  signal?: AbortSignal,
): Promise<InitiativeView> {
  const resp = await authenticatedFetch(`${base(factoryId)}/${encodeURIComponent(ref)}`, {
    signal,
  });
  if (!resp.ok) throw await factoryErrorFromResponse(resp);
  return (await resp.json()) as InitiativeView;
}

/** `POST …/initiatives/<ref>/acceptance`: run the acceptance steps on the base env now. */
export async function runInitiativeAcceptance(
  factoryId: string,
  ref: string,
): Promise<InitiativeView> {
  const resp = await authenticatedFetch(
    `${base(factoryId)}/${encodeURIComponent(ref)}/acceptance`,
    { method: "POST" },
  );
  if (!resp.ok) throw await factoryErrorFromResponse(resp);
  return (await resp.json()) as InitiativeView;
}

/** `PATCH …/initiatives/<ref>`: rename, rewrite the steps, close, cancel or reopen. */
export async function patchInitiative(
  factoryId: string,
  ref: string,
  body: {
    title?: string;
    outcome?: string | null;
    acceptance?: { step: string; expect?: string; owner?: string; needs?: string[] }[];
    status?: "open" | "done" | "cancelled";
  },
): Promise<InitiativeView> {
  const resp = await authenticatedFetch(`${base(factoryId)}/${encodeURIComponent(ref)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw await factoryErrorFromResponse(resp);
  return (await resp.json()) as InitiativeView;
}
