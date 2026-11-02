// Sample (fictional, product-agnostic) data for Teams, the agent panel and the
// bench: two lead seats, three project teams (one working with a stuck demo
// agent, one waiting for a merge, one stopped), a shared environment, each
// agent's 24 h strip, and the bench. Every visible string is marked "(sample)"
// or is a neutral name.

import type {
  AgentRowView,
  AgentsResponse,
  FactorySnapshot,
  MemberView,
  ProjectView,
} from "@/lib/factoryApi";
import type {
  AgentTimelineResponse,
  BenchResponse,
  BenchRowView,
  StripPiece,
  StripState,
} from "@/lib/factoryTeamsApi";
import type { StoryRoutes } from "../../factoryStoryFixtures";
import { preflightOk } from "../../__fixtures__/liveRun";
import { roomRoutes } from "../../room/__fixtures__/roomFixtures";
import { emptyFactorySnapshot, round3Snapshot } from "../../shell/__fixtures__/shellFixtures";

const H = 3600;

interface SampleAgent {
  handle: string;
  role: AgentRowView["role"] | "scout";
  project: string | null;
  harness: string;
  model: string;
  label: string;
  status: "running" | "waiting" | "idle" | "failed";
  state?: MemberView["state"];
  summary: string | null;
  tool?: [string, string];
  cost: number;
  stuck?: string;
  dead?: string;
  top?: boolean;
}

const CLAUDE = "claude-native";
const CODEX = "codex-native";

/** The sample teams (handles are stable short names, as the server assigns them). */
export const SAMPLE_AGENTS: readonly SampleAgent[] = [
  // csv-export: building, with a stuck demo agent
  {
    handle: "nova",
    role: "project_lead",
    project: "csv-export",
    harness: CLAUDE,
    model: "opus",
    label: "opus",
    status: "idle",
    summary: "(sample) T-3 in review; asked whether exports include archived rows",
    cost: 6.2,
    top: true,
  },
  {
    handle: "ivy",
    role: "ic",
    project: "csv-export",
    harness: CLAUDE,
    model: "claude-sonnet-4-5",
    label: "sonnet",
    status: "running",
    summary: "(sample) Writing the export endpoint",
    tool: ["Edit", "src/export/csv.ts"],
    cost: 3.1,
  },
  {
    handle: "rex",
    role: "ic",
    project: "csv-export",
    harness: CODEX,
    model: "gpt-5-codex",
    label: "gpt-5-codex",
    status: "idle",
    summary: "(sample) Opened !41 with the column picker",
    cost: 2.4,
  },
  {
    handle: "quill",
    role: "reviewer",
    project: "csv-export",
    harness: CODEX,
    model: "gpt-5-codex",
    label: "gpt-5-codex",
    status: "running",
    summary: "(sample) Reviewing !41",
    tool: ["Bash", "git diff main...HEAD"],
    cost: 0.9,
  },
  {
    handle: "dot",
    role: "demo",
    project: "csv-export",
    harness: CLAUDE,
    model: "claude-sonnet-4-5",
    label: "sonnet",
    status: "running",
    summary: "(sample) Recording the export demo",
    tool: ["Bash", "npx playwright test demo/export.spec.ts"],
    cost: 2.1,
    stuck: "same failing Bash call 3 times",
  },
  {
    handle: "kip",
    role: "env_keeper",
    project: "csv-export",
    harness: CLAUDE,
    model: "claude-sonnet-4-5",
    label: "sonnet",
    status: "running",
    summary: "(sample) Restarting the preview after a migration",
    tool: ["Bash", "make preview-up"],
    cost: 0.7,
  },
  // dark-mode: waits for a person to merge
  {
    handle: "juno",
    role: "project_lead",
    project: "dark-mode",
    harness: CLAUDE,
    model: "opus",
    label: "opus",
    status: "idle",
    summary: "(sample) !38 is ready for your merge",
    cost: 4.4,
    top: true,
  },
  {
    handle: "pip",
    role: "ic",
    project: "dark-mode",
    harness: CLAUDE,
    model: "claude-sonnet-4-5",
    label: "sonnet",
    status: "idle",
    state: "done",
    summary: "(sample) Theme choice saved per person",
    cost: 1.8,
  },
  {
    handle: "oro",
    role: "reviewer",
    project: "dark-mode",
    harness: CODEX,
    model: "gpt-5-codex",
    label: "gpt-5-codex",
    status: "idle",
    state: "done",
    summary: "(sample) Approved !38",
    cost: 0.5,
  },
  {
    handle: "bo",
    role: "env_keeper",
    project: "dark-mode",
    harness: CLAUDE,
    model: "claude-sonnet-4-5",
    label: "sonnet",
    status: "idle",
    summary: "(sample) Preview p38 is up",
    cost: 0.4,
  },
  // search-filters: its lead stopped
  {
    handle: "mira",
    role: "project_lead",
    project: "search-filters",
    harness: CLAUDE,
    model: "opus",
    label: "opus",
    status: "idle",
    summary: "(sample) Planning the date filter",
    cost: 1.2,
    dead: "(sample) relaunch failed: the sandbox did not start",
    top: true,
  },
];

/** The sample factory's clock (the round-3 snapshot's server time). */
export const TEAMS_NOW_S = round3Snapshot().server_time;

function sid(n: number): string {
  return `5a${n.toString(16).padStart(30, "0")}`;
}

function project(
  template: ProjectView,
  slug: string,
  title: string,
  stage: ProjectView["stage"],
  extra: Partial<ProjectView> = {},
): ProjectView {
  return {
    ...template,
    slug,
    title,
    stage,
    charter: null,
    overlay: null,
    overlay_reason: null,
    agent_stage: null,
    pl: null,
    team: [],
    env: null,
    pr_ids: [],
    open_decision_ids: [],
    status: "active",
    kind: "work",
    ...extra,
  };
}

function memberOf(
  template: MemberView,
  agent: SampleAgent,
  id: string,
  parent: string | null,
): MemberView {
  const running = agent.status === "running";
  return {
    ...template,
    session_id: id,
    title: agent.top ? `PL · ${agent.project}` : `${agent.role}:${agent.handle}`,
    role: agent.role as MemberView["role"],
    project: agent.project,
    parent_session_id: parent,
    is_top_level: agent.top ?? false,
    status: agent.dead ? "idle" : agent.status,
    state: agent.top ? (agent.state ?? "active") : (agent.state ?? null),
    health: agent.dead ? "dead" : "ok",
    summary: agent.summary,
    harness: agent.harness,
    running,
    last_activity_at: TEAMS_NOW_S - (running ? 30 : 2 * H),
    heartbeat_s: agent.top ? 600 : null,
    cost_usd: agent.cost,
    launch_drift: null,
    url: `/c/${id}`,
    now: running
      ? {
          session_id: id,
          status: "running",
          turn_n: 12,
          tool: agent.tool?.[0] ?? null,
          tool_arg: agent.tool?.[1] ?? null,
          tool_since: TEAMS_NOW_S - (agent.stuck ? 14 * 60 : 90),
          last_event_at: TEAMS_NOW_S - (agent.stuck ? 14 * 60 : 20),
          model: agent.model,
          cost_usd: agent.cost,
          usd_per_h: agent.role === "ic" ? 1.6 : 0.6,
          ctx_pct: null,
          flags: agent.stuck ? ["repeat"] : [],
        }
      : null,
    handle: agent.handle,
    stuck: agent.stuck != null,
    stuck_reason: agent.stuck ?? null,
    stuck_since: agent.stuck ? TEAMS_NOW_S - 14 * 60 : null,
    stuck_issue: agent.stuck ? "INC-7" : null,
  };
}

/** Every sample agent a snapshot was built with, by handle (the rows read their models here). */
const REGISTRY = new Map<string, SampleAgent>(SAMPLE_AGENTS.map((a) => [a.handle, a]));

/** The sample factory: two lead seats, three project teams and a shared environment. */
export function teamsSnapshot(agents: readonly SampleAgent[] = SAMPLE_AGENTS): FactorySnapshot {
  for (const agent of agents) REGISTRY.set(agent.handle, agent);
  const base = emptyFactorySnapshot();
  const round3 = round3Snapshot();
  const template = round3.projects[0]!;
  const integration = round3.projects.find((p) => p.kind === "integration")!;
  const memberTemplate = base.members[0]!;
  base.factory = {
    ...base.factory,
    name: "(sample) factory",
    base: base.factory.base ? { ...base.factory.base, repo: "sample/repo", branch: "main" } : null,
  };
  base.viewer = base.viewer ? { ...base.viewer, user_id: "owner@example.test" } : base.viewer;
  base.members = base.members.map((m) =>
    m.session_id === base.factory.root_session_id
      ? {
          ...m,
          handle: "atlas",
          status: "idle",
          running: false,
          now: null,
          health: "ok",
          launch_drift: null,
          summary: "(sample) 3 projects in flight · !38 ready for a merge",
        }
      : {
          ...m,
          handle: "sentinel",
          status: "running",
          running: true,
          now: null,
          health: "ok",
          launch_drift: null,
          summary: "(sample) Verifying !41 before it reaches ready",
        },
  );
  const slugs = [...new Set(agents.map((a) => a.project).filter((s): s is string => s != null))];
  const titles: Record<string, [string, ProjectView["stage"], Partial<ProjectView>]> = {
    "csv-export": ["(sample) People can export a report as CSV", "verify", {}],
    "dark-mode": ["(sample) Settings remember the dark theme", "ready", {}],
    "search-filters": [
      "(sample) Search results can be filtered by date",
      "planning",
      { overlay: "failed", overlay_reason: "(sample) its lead stopped" },
    ],
  };
  base.projects = [
    ...slugs.map((slug) => {
      const [title, stage, extra] = titles[slug] ?? [`(sample) Project ${slug}`, "building", {}];
      const cost = agents.filter((a) => a.project === slug).reduce((t, a) => t + a.cost, 0);
      return project(template, slug, title, stage, {
        cost_usd: Math.round(cost * 100) / 100,
        ...extra,
      });
    }),
    project(integration, "base-env", "(sample) Shared environment", "building", {
      kind: "integration",
      env: {
        reported: {
          state: "ready",
          env_id: "base",
          urls: { app: null, api: null, minio: null, tilt_ui: null },
          deployed: { sha: null, ref: null },
          resources: [],
          note: "(sample) keeps main running for previews",
          at: TEAMS_NOW_S - H,
        },
        probe: null,
      },
    }),
  ];
  const pls = new Map<string, string>();
  agents.forEach((agent, i) => {
    if (agent.top && agent.project) pls.set(agent.project, sid(i));
  });
  base.members = [
    ...base.members,
    ...agents.map((agent, i) =>
      memberOf(
        memberTemplate,
        agent,
        sid(i),
        agent.top ? base.factory.root_session_id : (pls.get(agent.project ?? "") ?? null),
      ),
    ),
  ];
  return base;
}

/** The fleet rows of the sample factory, as `GET …/agents` serves them. */
export function teamsAgentRows(snapshot: FactorySnapshot = teamsSnapshot()): AgentRowView[] {
  return snapshot.members.map((m) => {
    const sample = m.handle ? REGISTRY.get(m.handle) : undefined;
    const lead = m.role === "lead";
    return {
      session_id: m.session_id,
      handle: m.handle ?? null,
      title: m.title,
      role: m.role,
      harness: m.harness ?? CLAUDE,
      vendor: (m.harness ?? CLAUDE).includes("codex") ? "codex" : "claude",
      model: sample?.model ?? (lead ? "opus" : null),
      model_label: sample?.label ?? (lead ? "opus" : null),
      project: m.project,
      parent_session_id: m.parent_session_id,
      is_top_level: m.is_top_level,
      status: m.now?.status ?? m.status,
      state: m.state,
      health: m.health,
      last_error: sample?.dead ?? null,
      summary: m.summary,
      task:
        sample?.role === "ic" && sample.status === "running"
          ? {
              id: "t3",
              ref: "T-3",
              key: "export-endpoint",
              title: "(sample) Export endpoint",
              state: "running",
              attempts: 1,
              pr_id: null,
            }
          : null,
      loop: null,
      now: m.now ?? null,
      cost_usd: m.cost_usd,
      subtree_cost_usd: m.cost_usd,
      mail_in: 0,
      mail_out: 0,
      decisions_pending: 0,
      flags: [...(m.now?.flags ?? []), ...(m.health === "dead" ? ["dead"] : [])],
      stuck: m.stuck ?? false,
      stuck_reason: m.stuck_reason ?? null,
      stuck_since: m.stuck_since ?? null,
      stuck_issue: m.stuck_issue ?? null,
      issues: m.stuck_issue ? [m.stuck_issue] : [],
      last_activity_at: m.now?.last_event_at ?? m.last_activity_at,
      url: m.url,
    };
  });
}

export function teamsAgentsResponse(snapshot: FactorySnapshot = teamsSnapshot()): AgentsResponse {
  return {
    data: teamsAgentRows(snapshot),
    lines: [],
    live: { polled_at: TEAMS_NOW_S - 4, interval_s: 10, fresh: true, usd_per_h: 4.1 },
  };
}

function hash(text: string): number {
  let h = 7;
  for (const ch of text) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return h;
}

/** A deterministic strip for one agent, ending in its current state. */
export function timelineFor(
  row: AgentRowView,
  window: "6h" | "24h" | "7d" = "24h",
  options: { partialSinceH?: number } = {},
): AgentTimelineResponse {
  const spanS = window === "6h" ? 6 * H : window === "7d" ? 7 * 24 * H : 24 * H;
  const until = TEAMS_NOW_S;
  const since = until - spanS;
  const bucket = window === "6h" ? 900 : window === "7d" ? 6 * H : H;
  const h = hash(row.handle ?? row.session_id);
  const last: StripState = row.stuck
    ? "stuck"
    : row.status === "running"
      ? "working"
      : row.status === "waiting"
        ? "waiting"
        : "idle";
  const plan: [StripState, number][] = [
    ["idle", 4 + (h % 5)],
    ["working", 3 + (h % 4)],
    ["checks", 1 + (h % 2)],
    ["idle", 3 + ((h >> 3) % 4)],
    ["working", 4],
    ["waiting", h % 3 === 0 ? 1 : 0],
    [last, 3],
  ];
  const total = plan.reduce((t, [, w]) => t + w, 0);
  const segments: StripPiece[] = [];
  let at = since * 1000;
  for (const [state, weight] of plan) {
    if (!weight) continue;
    const end = at + (weight / total) * spanS * 1000;
    const prev = segments.at(-1);
    if (prev && prev.state === state) prev.end = Math.round(end);
    else segments.push({ state, start: Math.round(at), end: Math.round(end) });
    at = end;
  }
  segments.at(-1)!.end = until * 1000;
  const totals = { working: 0, checks: 0, waiting: 0, stuck: 0, idle: 0 } as Record<
    StripState,
    number
  >;
  for (const s of segments) totals[s.state] += Math.round((s.end - s.start) / 1000);
  const n = Math.ceil(spanS / bucket);
  const series = Array.from({ length: n }, (_, i) => {
    const busy = segments.some(
      (s) =>
        (s.state === "working" || s.state === "stuck") &&
        s.start <= (since + i * bucket) * 1000 &&
        s.end > (since + i * bucket) * 1000,
    );
    return {
      at: since + i * bucket,
      tool_calls: busy ? 6 + ((h >> (i % 16)) % 9) : (h >> (i % 8)) % 2,
      cost_usd: busy ? Math.round((0.12 + ((h >> (i % 12)) % 5) * 0.04) * 100) / 100 : 0,
    };
  });
  const recorded = options.partialSinceH != null ? until - options.partialSinceH * H : since - H;
  return {
    session_id: row.session_id,
    handle: row.handle ?? null,
    window,
    since,
    until,
    bucket_s: bucket,
    segments,
    totals,
    series,
    recorded_since: recorded,
    partial: recorded > since,
    tool_calls_partial: false,
  };
}

function benchRow(
  role: string,
  vendor: string,
  model: string,
  quality: [string, number, number] | null,
  rounds: number | null,
  cost: [number, string, number],
): BenchRowView {
  const n = quality ? quality[2] : cost[2];
  return {
    role,
    vendor,
    model,
    n,
    quality: quality ? { label: quality[0], value: quality[1], n: quality[2] } : null,
    rounds: rounds != null ? { value: rounds, n } : null,
    cost: {
      usd: Math.round(cost[0] * cost[2] * 100) / 100,
      unit: cost[1],
      units: cost[2],
      per_unit: cost[0],
    },
    low_n: n < 5,
  };
}

/** The sample bench: builders on two vendors (one row with too few samples), reviewers, demo, leads. */
export function benchResponse(ready = true): BenchResponse {
  const rows: BenchRowView[] = [
    benchRow("project_lead", "claude", "opus", null, null, [3.1, "active day", 9]),
    benchRow("ic", "claude", "sonnet", ["first-pass review", 0.61, 18], 1.6, [
      4.4,
      "merged PR",
      18,
    ]),
    benchRow("ic", "codex", "gpt-5-codex", ["first-pass review", 0.75, 12], 1.3, [
      3.9,
      "merged PR",
      12,
    ]),
    benchRow("ic", "claude", "opus", ["first-pass review", 0.67, 3], 1.3, [9.8, "merged PR", 3]),
    benchRow("reviewer", "codex", "gpt-5-codex", ["no later change requests", 0.88, 24], null, [
      0.7,
      "reviewed PR",
      24,
    ]),
    benchRow("reviewer", "claude", "opus", ["no later change requests", 0.75, 4], null, [
      1.9,
      "reviewed PR",
      4,
    ]),
    benchRow("demo", "claude", "sonnet", ["demo passes first run", 0.8, 15], null, [
      0.8,
      "demo",
      15,
    ]),
    benchRow("env_keeper", "claude", "sonnet", null, null, [0.6, "active day", 20]),
  ];
  // Before stamped work, per-unit roles have no units the server can attribute.
  const perUnit = new Set(["ic", "reviewer", "demo"]);
  const shown = ready
    ? rows
    : rows.map((r) => {
        const units = perUnit.has(r.role) ? 0 : (r.cost?.units ?? 0);
        return {
          ...r,
          quality: null,
          rounds: null,
          n: units,
          low_n: units < 5,
          cost: r.cost ? { ...r.cost, units, per_unit: units ? r.cost.per_unit : null } : null,
        };
      });

  return {
    window: { since: TEAMS_NOW_S - 30 * 24 * H, until: TEAMS_NOW_S, label: "30d" },
    rows: shown,
    quality_ready: ready,
    stamped_since: ready ? TEAMS_NOW_S - 20 * 24 * H : null,
    note: ready
      ? null
      : "Quality by model needs a few more days of data. Until then the bench shows cost by model only.",
    low_n: 5,
  };
}

/** A factory grown to `projects` teams of five and `agents` agents in all (the 60-agent scale). */
export function scaleTeamsSnapshot(projects = 12, agents = 60): FactorySnapshot {
  const roles: [SampleAgent["role"], string, string, string][] = [
    ["project_lead", CLAUDE, "opus", "opus"],
    ["ic", CLAUDE, "claude-sonnet-4-5", "sonnet"],
    ["ic", CODEX, "gpt-5-codex", "gpt-5-codex"],
    ["reviewer", CODEX, "gpt-5-codex", "gpt-5-codex"],
    ["env_keeper", CLAUDE, "claude-sonnet-4-5", "sonnet"],
  ];
  const sample: SampleAgent[] = [];
  for (let i = 0; i < agents - 2; i += 1) {
    const p = i % projects;
    const [role, harness, model, label] = roles[Math.floor(i / projects) % roles.length]!;
    sample.push({
      handle: `agent-${i + 2}`,
      role,
      project: `project-${p + 1}`,
      harness,
      model,
      label,
      status: i % 3 === 0 ? "running" : "idle",
      summary: `(sample) Task ${i + 1}`,
      tool: i % 3 === 0 ? ["Bash", "make test"] : undefined,
      cost: 0.5 + (i % 7) * 0.3,
      top: role === "project_lead",
    });
  }
  return teamsSnapshot(sample);
}

/** `/v1/factory/*` routes for Teams over `snapshot`: preflight (cockpit v2 on), fleet, strips, bench. */
export function teamsRoutes(
  snapshot: FactorySnapshot = teamsSnapshot(),
  options: { bench?: BenchResponse; partialSinceH?: number; rows?: AgentRowView[] } = {},
): StoryRoutes {
  const base = `/v1/factory/factories/${snapshot.factory.id}`;
  const rows = options.rows ?? teamsAgentRows(snapshot);
  const routes: StoryRoutes = {
    // A quiet Room beside the page: no sample product chatter.
    ...roomRoutes(snapshot.factory.id, { messages: [], proposals: [], issues: [], threads: [] }),
    "/v1/factory/preflight": { body: { ...preflightOk(), cockpit_v2: true } },
    [`${base}/agents`]: { body: { ...teamsAgentsResponse(snapshot), data: rows } },
    [`${base}/bench`]: { body: options.bench ?? benchResponse() },
  };
  for (const row of rows) {
    const timeline = (path: string) => {
      const window = (new URL(path, "http://story").searchParams.get("window") ?? "24h") as
        "6h" | "24h" | "7d";
      return { body: timelineFor(row, window, { partialSinceH: options.partialSinceH }) };
    };
    routes[`${base}/agents/${row.session_id}/timeline`] = timeline;
    if (row.handle) routes[`${base}/agents/${row.handle}/timeline`] = timeline;
  }
  return routes;
}
