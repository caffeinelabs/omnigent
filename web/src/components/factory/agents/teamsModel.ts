// The Teams page as data: each agent's name, role, vendor · model and state
// in the words of the v2 cockpit; the three lead seats; one team per project
// (its lead, then its current members, older idle sessions folded); the state
// counts that double as filters; and the free-text filter that matches name,
// role, vendor, model, state and project. Pure; the page and the panel read it.

import type { AgentRowView, FactorySnapshot, ProjectView } from "@/lib/factoryApi";
import { rowsFromSnapshot } from "./fleetModel";

/** What a ring and a state word say about an agent. */
export type TeamState = "working" | "waiting" | "stuck" | "stopped" | "idle" | "done";

/** The state filters, in the order the counts show. */
export const STATE_FILTERS = ["working", "waiting", "stuck", "stopped", "idle"] as const;
export type StateFilter = (typeof STATE_FILTERS)[number];

export const STATE_WORD: Record<TeamState, string> = {
  working: "Working",
  waiting: "Waiting on a person",
  stuck: "Stuck",
  stopped: "Stopped",
  idle: "Idle",
  done: "Done",
};

/** The count chip's words (lowercase, after the number). */
export const FILTER_WORD: Record<StateFilter, string> = {
  working: "working",
  waiting: "waiting on a person",
  stuck: "stuck",
  stopped: "stopped",
  idle: "idle",
};

/** v2 role names: a builder, not an "ic". */
export const ROLE_WORD: Record<string, string> = {
  lead: "Lead",
  project_lead: "Project lead",
  ic: "Builder",
  reviewer: "Reviewer",
  demo: "Demo",
  env_keeper: "Env keeper",
  planner: "Planner",
  scout: "Discovery lead",
};

/** Members of a team in this order: the lead, who builds, who checks, who runs. */
export const ROLE_ORDER: readonly string[] = [
  "project_lead",
  "ic",
  "reviewer",
  "demo",
  "env_keeper",
  "planner",
];

export type Seat = "discovery" | "portfolio" | "reliability";

export const SEAT_WORD: Record<Seat, string> = {
  discovery: "Discovery seat",
  portfolio: "Portfolio seat",
  reliability: "Reliability seat",
};

export const SEAT_ROLE: Record<Seat, string> = {
  discovery: "Discovery lead",
  portfolio: "Portfolio lead",
  reliability: "Reliability lead",
};

/** What a lead seat owns, in one line. */
export const SEAT_OWNS: Record<Seat, string> = {
  discovery: "Finding and grouping evidence of what users need",
  portfolio: "Projects: what to start, plan and staff",
  reliability: "Verifying, restarting and auditing the factory's work",
};

const normId = (id: string) => id.replace(/-/g, "").toLowerCase();

/**
 * An agent's state from its row: dead and failed are stopped; a parked
 * prompt or an open question it asked the viewer is waiting, and beats stuck
 * (amber means a person must act, as on the server's strip); stuck beats
 * working.
 */
export function teamState(
  row: Pick<AgentRowView, "status" | "health" | "flags" | "state" | "stuck">,
  onYou = false,
): TeamState {
  if (row.health === "dead" || row.flags.includes("dead") || row.status === "failed") {
    return "stopped";
  }
  if (onYou || row.status === "waiting") return "waiting";
  if (row.stuck) return "stuck";
  if (row.status === "running") return "working";
  if (row.state === "done") return "done";
  return "idle";
}

/** The filter a state counts under (done counts as idle). */
export function filterOf(state: TeamState): StateFilter {
  return state === "done" ? "idle" : state;
}

/** `dot` → `Dot`; `builder-2` → `Builder 2`. */
export function handleName(handle: string): string {
  const spaced = handle.replace(/-/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** What to call an agent: its handle as a name, else its session title. */
export function agentName(row: Pick<AgentRowView, "handle" | "title">): string {
  return row.handle ? handleName(row.handle) : row.title;
}

/** The lead seat an agent holds, if any (the root lead is the portfolio seat). */
export function seatOf(
  row: Pick<AgentRowView, "session_id" | "role" | "is_top_level">,
  rootSessionId: string,
): Seat | null {
  if ((row.role as string) === "scout") return "discovery";
  if (row.role !== "lead" || !row.is_top_level) return null;
  return normId(row.session_id) === normId(rootSessionId) ? "portfolio" : "reliability";
}

/** `Builder`, `Project lead`, or a lead's seat role (`Portfolio lead`). */
export function roleWord(
  row: Pick<AgentRowView, "session_id" | "role" | "is_top_level">,
  rootSessionId: string,
): string {
  const seat = seatOf(row, rootSessionId);
  if (seat) return SEAT_ROLE[seat];
  return row.role ? (ROLE_WORD[row.role] ?? row.role) : "Agent";
}

/** A short model name from an id when the server sent none (`claude-sonnet-4-5` → `sonnet`). */
export function modelLabel(model: string | null | undefined): string | null {
  if (!model) return null;
  const bare = (model.split("/").pop() ?? model)
    .toLowerCase()
    .replace(/\[[^\]]*\]$/, "")
    .replace(/[-@]\d{8}$/, "");
  const family = bare.split("-").find((p) => p === "opus" || p === "sonnet" || p === "haiku");
  return family ?? (bare || null);
}

/** `claude`, from the row or its harness. */
export function vendorOf(row: Pick<AgentRowView, "vendor" | "harness">): string | null {
  if (row.vendor) return row.vendor;
  const harness = (row.harness ?? "").toLowerCase();
  if (harness.includes("claude")) return "claude";
  if (harness.includes("codex")) return "codex";
  return harness ? harness.replace(/-native$/, "") : null;
}

/** `claude · sonnet`, `codex · default`, or null when nothing is known. */
export function vendorModel(
  row: Pick<AgentRowView, "vendor" | "harness" | "model" | "model_label">,
): string | null {
  const vendor = vendorOf(row);
  const model = row.model_label ?? modelLabel(row.model);
  if (!vendor && !model) return null;
  return `${vendor ?? "agent"} · ${model ?? "default"}`;
}

/** One agent as Teams shows it. */
export interface TeamMember {
  row: AgentRowView;
  name: string;
  role: string;
  vendorModel: string | null;
  state: TeamState;
  seat: Seat | null;
  /** It waits on an answer the viewer gives. */
  onYou: boolean;
}

/**
 * @param waitingOnMe - Normalized session ids with an open question for the
 *   viewer (`waitingOnViewer` in the fleet model).
 */
export function teamMember(
  row: AgentRowView,
  rootSessionId: string,
  waitingOnMe: ReadonlySet<string> | null = null,
): TeamMember {
  const onYou = waitingOnMe?.has(normId(row.session_id)) ?? false;
  return {
    row,
    name: agentName(row),
    role: roleWord(row, rootSessionId),
    vendorModel: vendorModel(row),
    state: teamState(row, onYou),
    seat: seatOf(row, rootSessionId),
    onYou,
  };
}

/** `Waiting on you` when the viewer answers it, else the state's word. */
export function stateWord(member: Pick<TeamMember, "state" | "onYou">): string {
  return member.state === "waiting" && member.onYou ? "Waiting on you" : STATE_WORD[member.state];
}

/** The words a filter is matched against. */
export function memberHaystack(member: TeamMember, projectTitle?: string | null): string {
  const { row } = member;
  return [
    member.name,
    row.handle,
    row.title,
    member.role,
    row.role,
    vendorOf(row),
    row.model,
    row.model_label,
    member.vendorModel?.replace(" · ", " "),
    stateWord(member),
    member.state,
    row.project,
    projectTitle,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

/** Every word of `query` appears in the member's name, role, vendor, model, state or project. */
export function matchesTeamFilter(
  member: TeamMember,
  query: string,
  projectTitle?: string | null,
): boolean {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = memberHaystack(member, projectTitle);
  return words.every((w) => hay.includes(w));
}

export type StateCounts = Record<StateFilter, number>;

export function stateCounts(members: readonly TeamMember[]): StateCounts {
  const counts: StateCounts = { working: 0, waiting: 0, stuck: 0, stopped: 0, idle: 0 };
  for (const m of members) counts[filterOf(m.state)] += 1;
  return counts;
}

/** A project's team: its lead first, then current members by role; old idle sessions folded. */
export interface ProjectTeam {
  slug: string;
  title: string;
  project: ProjectView | null;
  members: TeamMember[];
  /** Idle sessions of a role that has a newer one (finished dispatches). */
  earlier: number;
  /** Spend of the shown members (own cost), when any is priced. */
  costUsd: number | null;
}

const ACTIVE: ReadonlySet<TeamState> = new Set(["working", "waiting", "stuck", "stopped"]);

function roleRank(member: TeamMember): number {
  const i = ROLE_ORDER.indexOf(member.row.role ?? "");
  return i < 0 ? ROLE_ORDER.length : i;
}

/**
 * The current team out of every session a project has had: the lead, every
 * busy or broken member, and the newest idle session of each other role.
 */
export function currentTeam(members: readonly TeamMember[]): {
  shown: TeamMember[];
  earlier: number;
} {
  const newestIdle = new Map<string, TeamMember>();
  for (const m of members) {
    if (ACTIVE.has(m.state) || m.row.role === "project_lead") continue;
    const key = m.row.role ?? "agent";
    const held = newestIdle.get(key);
    if (!held || (m.row.last_activity_at ?? 0) > (held.row.last_activity_at ?? 0)) {
      newestIdle.set(key, m);
    }
  }
  const keep = new Set(newestIdle.values());
  const shown = members.filter(
    (m) => ACTIVE.has(m.state) || m.row.role === "project_lead" || keep.has(m),
  );
  shown.sort((a, b) => roleRank(a) - roleRank(b) || a.name.localeCompare(b.name));
  return { shown, earlier: members.length - shown.length };
}

/** Agents outside every project team: the leads' helpers and members of shared projects. */
export interface OtherAgents {
  members: TeamMember[];
  /** Idle sessions of a role that has a newer one. */
  earlier: number;
}

/**
 * Leads (top-level lead seats), one team per project in the snapshot's
 * project order, and everyone else (`others`), so every agent the counts
 * include shows somewhere.
 *
 * @param shared - Slugs of shared projects (an integration environment): their
 *   members are listed with the others, not as a team.
 */
export function groupTeams(
  members: readonly TeamMember[],
  projects: readonly ProjectView[],
  shared: ReadonlySet<string> = new Set(),
): { leads: TeamMember[]; teams: ProjectTeam[]; others: OtherAgents } {
  const seatOrder: Seat[] = ["discovery", "portfolio", "reliability"];
  const leads = members
    .filter((m) => m.seat != null || (m.row.role === "lead" && m.row.is_top_level))
    .sort((a, b) => seatOrder.indexOf(a.seat!) - seatOrder.indexOf(b.seat!));
  const bySlug = new Map<string, TeamMember[]>();
  const rest: TeamMember[] = [];
  for (const m of members) {
    if (leads.includes(m)) continue;
    if (!m.row.project || shared.has(m.row.project)) {
      rest.push(m);
      continue;
    }
    const list = bySlug.get(m.row.project) ?? [];
    list.push(m);
    bySlug.set(m.row.project, list);
  }
  const order = [
    ...projects.map((p) => p.slug),
    ...[...bySlug.keys()].filter((s) => !projects.some((p) => p.slug === s)),
  ];
  const teams: ProjectTeam[] = [];
  for (const slug of order) {
    const list = bySlug.get(slug);
    if (!list?.length) continue;
    const project = projects.find((p) => p.slug === slug) ?? null;
    const { shown, earlier } = currentTeam(list);
    const costs = shown.map((m) => m.row.cost_usd).filter((c): c is number => c != null);
    teams.push({
      slug,
      title: project?.title ?? slug,
      project,
      members: shown,
      earlier,
      costUsd: costs.length ? costs.reduce((a, b) => a + b, 0) : null,
    });
  }
  const others = currentTeam(rest);
  return { leads, teams, others: { members: others.shown, earlier: others.earlier } };
}

/** `Builder sonnet, gpt-5-codex · Reviewer gpt-5-codex`: a team's shape in words (J2). */
export function teamLine(members: readonly TeamMember[]): string {
  const byRole = new Map<string, Set<string>>();
  for (const m of members) {
    const role = ROLE_WORD[m.row.role ?? ""] ?? m.role;
    const model = m.row.model_label ?? modelLabel(m.row.model) ?? vendorOf(m.row) ?? "?";
    const set = byRole.get(role) ?? new Set<string>();
    set.add(model);
    byRole.set(role, set);
  }
  return [...byRole.entries()]
    .map(([role, models]) => `${role} ${[...models].join(", ")}`)
    .join(" · ");
}

/** The rows Teams shows: the fleet API's, else the snapshot's members (older servers). */
export function teamRows(
  snapshot: FactorySnapshot,
  fleet: AgentRowView[] | undefined,
): AgentRowView[] {
  if (fleet) return fleet;
  const byId = new Map(snapshot.members.map((m) => [m.session_id, m]));
  return rowsFromSnapshot(snapshot).map((row) => {
    const m = byId.get(row.session_id);
    return {
      ...row,
      handle: m?.handle ?? null,
      vendor: m?.vendor ?? null,
      stuck: m?.stuck ?? false,
      stuck_reason: m?.stuck_reason ?? null,
      stuck_since: m?.stuck_since ?? null,
      stuck_issue: m?.stuck_issue ?? null,
    };
  });
}
