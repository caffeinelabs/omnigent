// The fleet as the Agents page shows it: one fact sheet per agent (state,
// what it runs now, $/h, how long it has been quiet, which saved views it
// falls in), grouped by project (leads last) or by role. Pure; the page,
// the bulk bar and the query read the same facts.

import type {
  AgentRowView,
  FactoryRole,
  FactorySnapshot,
  MemberView,
  NowState,
} from "@/lib/factoryApi";
import { ROLE_SHORT } from "../board/TeamStrip";
import { type FleetFacts, type FleetQuery, type SavedView, matchesFleetQuery } from "./fleetQuery";

export type AgentState = "running" | "waiting" | "idle" | "failed" | "dead" | "done";

/** One agent with what the page and the query read. */
export interface FleetAgent {
  row: AgentRowView;
  facts: FleetFacts;
  state: AgentState;
}

/** Running agents silent this long count as quiet (the detector's threshold). */
export const QUIET_MIN = 10;
/** $/h at which an agent is "expensive" now, and $ spent so far. */
export const EXPENSIVE_RATE = 1;
export const EXPENSIVE_COST = 3;
const LOOP_BAD = new Set(["exhausted", "thrashing", "stalled", "escalated"]);

export function agentState(
  row: Pick<AgentRowView, "status" | "health" | "flags" | "state">,
): AgentState {
  if (row.health === "dead" || row.flags.includes("dead")) return "dead";
  if (row.state === "done") return "done";
  switch (row.status) {
    case "running":
      return "running";
    case "waiting":
      return "waiting";
    case "failed":
      return "failed";
    default:
      return "idle";
  }
}

/** The saved views an agent falls in. */
export function savedViewsOf(
  row: AgentRowView,
  state: AgentState,
  idleMin: number | null,
): Set<SavedView> {
  const views = new Set<SavedView>();
  const rate = row.now?.usd_per_h ?? null;
  if (row.flags.includes("repeat") || (row.loop && LOOP_BAD.has(row.loop.state))) {
    views.add("looping");
  }
  if (row.decisions_pending > 0 || state === "waiting") views.add("waiting");
  if ((rate ?? 0) >= EXPENSIVE_RATE || (row.cost_usd ?? 0) >= EXPENSIVE_COST) {
    views.add("expensive");
  }
  if (
    row.flags.includes("quiet") ||
    (state === "running" && idleMin != null && idleMin >= QUIET_MIN)
  ) {
    views.add("quiet");
  }
  return views;
}

/** The agent's fact sheet at `nowS` (epoch seconds, the server's clock). */
export function fleetAgent(row: AgentRowView, nowS: number): FleetAgent {
  const state = agentState(row);
  const last = row.now?.last_event_at ?? row.last_activity_at;
  const idleMin = last != null ? Math.max(0, (nowS - last) / 60) : null;
  const facts: FleetFacts = {
    state,
    role: row.role,
    roleShort: row.role ? ROLE_SHORT[row.role] : null,
    project: row.project,
    harness: row.harness,
    model: row.model ?? row.now?.model ?? null,
    tool: row.now?.tool ?? null,
    toolArg: row.now?.tool_arg ?? null,
    flags: row.flags,
    task: row.task?.ref ?? null,
    loop: row.loop?.kind ?? null,
    loopState: row.loop?.state ?? null,
    title: row.title,
    summary: row.summary,
    cost: row.cost_usd,
    rate: row.now?.usd_per_h ?? null,
    turn: row.now?.turn_n ?? null,
    idleMin,
    mail: row.mail_in + row.mail_out,
    decisions: row.decisions_pending,
    views: savedViewsOf(row, state, idleMin),
  };
  return { row, facts, state };
}

/**
 * Rows from the snapshot's members when the fleet API is unavailable (an
 * older server): no task, loop, mail or own cost, but the same page.
 */
export function rowsFromSnapshot(snapshot: FactorySnapshot): AgentRowView[] {
  return snapshot.members.map((m: MemberView) => ({
    session_id: m.session_id,
    title: m.title,
    role: m.role,
    harness: m.harness,
    model: m.now?.model ?? null,
    project: m.project,
    parent_session_id: m.parent_session_id,
    is_top_level: m.is_top_level,
    status: m.now?.status ?? m.status,
    state: m.state,
    health: m.health,
    summary: m.summary,
    task: null,
    loop: null,
    now: m.now ?? null,
    cost_usd: m.cost_usd,
    subtree_cost_usd: m.cost_usd,
    mail_in: 0,
    mail_out: 0,
    decisions_pending: 0,
    flags: [...(m.now?.flags ?? []), ...(m.health === "dead" ? ["dead"] : [])],
    issues: [],
    last_activity_at: m.now?.last_event_at ?? m.last_activity_at,
    url: m.url,
  }));
}

export type FleetGroupBy = "project" | "role" | "none";

export interface FleetGroup {
  key: string;
  /** `db-version`, `Leads`, `Implementer`, or `All agents`. */
  label: string;
  project: string | null;
  agents: FleetAgent[];
  running: number;
  cost: number | null;
}

const STATE_RANK: Record<AgentState, number> = {
  dead: 0,
  failed: 1,
  waiting: 2,
  running: 3,
  idle: 4,
  done: 5,
};

const ROLE_RANK: Partial<Record<FactoryRole, number>> = {
  lead: 0,
  project_lead: 1,
  env_keeper: 2,
  ic: 3,
  reviewer: 4,
  demo: 5,
  planner: 6,
};

/** Top-level first, then trouble before calm, then the most recently active. */
function compareAgents(a: FleetAgent, b: FleetAgent): number {
  return (
    Number(b.row.is_top_level) - Number(a.row.is_top_level) ||
    STATE_RANK[a.state] - STATE_RANK[b.state] ||
    (b.row.last_activity_at ?? 0) - (a.row.last_activity_at ?? 0) ||
    a.row.title.localeCompare(b.row.title)
  );
}

function sumCost(agents: FleetAgent[]): number | null {
  const priced = agents.map((a) => a.row.cost_usd).filter((c): c is number => c != null);
  return priced.length ? priced.reduce((s, c) => s + c, 0) : null;
}

function group(
  key: string,
  label: string,
  project: string | null,
  agents: FleetAgent[],
): FleetGroup {
  const sorted = [...agents].sort(compareAgents);
  return {
    key,
    label,
    project,
    agents: sorted,
    running: sorted.filter((a) => a.state === "running").length,
    cost: sumCost(sorted),
  };
}

/**
 * The agents in groups: by project in `projectOrder` (unknown projects after,
 * leads last), by role, or one group.
 */
export function groupFleet(
  agents: FleetAgent[],
  by: FleetGroupBy,
  projectOrder: readonly string[] = [],
): FleetGroup[] {
  if (by === "none") return agents.length ? [group("all", "All agents", null, agents)] : [];
  if (by === "role") {
    const byRole = new Map<string, FleetAgent[]>();
    for (const agent of agents) {
      const role = agent.row.role ?? "agent";
      byRole.set(role, [...(byRole.get(role) ?? []), agent]);
    }
    return [...byRole.entries()]
      .sort(
        ([a], [b]) =>
          (ROLE_RANK[a as FactoryRole] ?? 9) - (ROLE_RANK[b as FactoryRole] ?? 9) ||
          a.localeCompare(b),
      )
      .map(([role, list]) => group(`role:${role}`, roleLabel(role), null, list));
  }
  const byProject = new Map<string, FleetAgent[]>();
  for (const agent of agents) {
    const key = agent.row.project ?? "";
    byProject.set(key, [...(byProject.get(key) ?? []), agent]);
  }
  const order = (slug: string) => {
    if (slug === "") return Number.MAX_SAFE_INTEGER;
    const at = projectOrder.indexOf(slug);
    return at === -1 ? projectOrder.length : at;
  };
  return [...byProject.entries()]
    .sort(([a], [b]) => order(a) - order(b) || a.localeCompare(b))
    .map(([slug, list]) =>
      group(slug ? `project:${slug}` : "leads", slug || "Leads", slug || null, list),
    );
}

const ROLE_LABEL: Record<string, string> = {
  lead: "Leads",
  project_lead: "Project leads",
  ic: "Implementers",
  env_keeper: "Env keepers",
  reviewer: "Reviewers",
  demo: "Demo recorders",
  planner: "Planners",
  agent: "Other agents",
};

export function roleLabel(role: string): string {
  return ROLE_LABEL[role] ?? role;
}

/** The fleet narrowed by the query, keeping the API's order for grouping. */
export function filterFleet(agents: FleetAgent[], query: FleetQuery): FleetAgent[] {
  return query.terms.length ? agents.filter((a) => matchesFleetQuery(a.facts, query)) : agents;
}

export interface FleetCounts {
  total: number;
  running: number;
  waiting: number;
  dead: number;
  flagged: number;
}

/** `41 (23▶ · 3 waiting · 1 dead · 2 flagged)`. */
export function fleetCounts(agents: FleetAgent[]): FleetCounts {
  return {
    total: agents.length,
    running: agents.filter((a) => a.state === "running").length,
    waiting: agents.filter((a) => a.state === "waiting").length,
    dead: agents.filter((a) => a.state === "dead").length,
    flagged: agents.filter((a) => a.row.flags.some((f) => f !== "dead") || a.row.issues.length > 0)
      .length,
  };
}

/** What the agent runs now, in a line: `Bash pnpm -F caffeine-api test`. */
export function nowText(now: NowState | null | undefined): string | null {
  if (!now?.tool) return null;
  return now.tool_arg ? `${now.tool} ${now.tool_arg}` : now.tool;
}

/** The fleet's distinct values for a filter dropdown, in first-seen order. */
export function distinct(values: readonly (string | null | undefined)[]): string[] {
  const seen = new Set<string>();
  for (const value of values) if (value) seen.add(value);
  return [...seen];
}
