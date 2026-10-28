// Fixtures for the ops pages (Agents, an agent, Timeline, Alerts, an
// incident, loops), built from the round-3 fixture (`snapshot.round3.json`:
// 41 agents, the db-version loop at 3/3, INC-13 on ic:route-r2, INC-12 on
// the dead cache-warm PL) and the live run's transcript. `opsRoutes` serves
// them as `/v1/factory/*` (with the Room's routes for the dock, and the
// session routes the Agent page reads) for stories and page tests. Every
// export is a fresh copy.

import transcriptFixture from "@/lib/__fixtures__/factory-transcript.json";
import type {
  AgentDetailResponse,
  AgentMailView,
  AgentRowView,
  AgentToolCallView,
  AgentsResponse,
  EventView,
  FactorySnapshot,
  IssuePageResponse,
  IssueView,
  IssuesResponse,
  LiveLineView,
  LoopView,
  LoopsResponse,
  MemberView,
  SpanPage,
  SpanView,
  TaskView,
} from "@/lib/factoryApi";
import round3Json from "../../__fixtures__/snapshot.round3.json";
import type { StoryRoutes } from "../../factoryStoryFixtures";
import { roomRoutes } from "../../room/__fixtures__/roomFixtures";
import {
  ROUND3_NOW_S,
  round3Snapshot,
  round3Spans,
  scaleSnapshot,
} from "../../shell/__fixtures__/shellFixtures";

const clone = <T>(value: T): T => structuredClone(value);

interface Round3 {
  tasks: TaskView[];
  loops: LoopView[];
  issues: IssueView[];
  spans: SpanView[];
}
const ROUND3 = round3Json as unknown as Round3;

export const IC_ROUTE_R2 = "9ba28e67c11a5a2fba4b7e0053ab6150";
export const PL_DB = "a2fc64b9e92e54c8b2f0f78038df202d";
export const PL_CACHE = "9d803e0e87b5587d844039f6cd433257";
export const ENV_DB = "d289bc6187e25a7391d640cc597531a1";
export const PR_9631 = "8f15c0f9a98e5831a39615fb58f35036";

const norm = (id: string | null | undefined) => (id ?? "").replace(/-/g, "").toLowerCase();

export function round3Tasks(): TaskView[] {
  return clone(ROUND3.tasks);
}

export function round3LoopList(): LoopView[] {
  return clone(ROUND3.loops);
}

export function round3IssueList(): IssueView[] {
  return clone(ROUND3.issues);
}

function rowOf(member: MemberView, tasks: TaskView[], issues: IssueView[]): AgentRowView {
  const task =
    tasks.find((t) => t.session_ids.some((s) => norm(s) === norm(member.session_id))) ?? null;
  const mine = issues.filter(
    (i) => i.status !== "resolved" && norm(i.target) === norm(`member:${member.session_id}`),
  );
  const flags = [...(member.now?.flags ?? []), ...(member.health === "dead" ? ["dead"] : [])];
  return {
    session_id: member.session_id,
    title: member.title,
    role: member.role,
    harness: member.harness,
    model: member.harness?.includes("codex") ? "gpt-5-codex" : "claude-sonnet-4-5",
    project: member.project,
    parent_session_id: member.parent_session_id,
    is_top_level: member.is_top_level,
    status: member.now?.status ?? member.status,
    state: member.state,
    health: member.health,
    summary: member.summary,
    task: task
      ? {
          id: task.id,
          ref: task.ref,
          key: task.key,
          title: task.title,
          state: task.state,
          attempts: task.attempts,
          pr_id: task.pr_id,
        }
      : null,
    loop: task?.loop ?? null,
    now: member.now ?? null,
    cost_usd: member.cost_usd,
    subtree_cost_usd: member.cost_usd,
    mail_in: member.session_id === PL_DB ? 2 : 0,
    mail_out: member.session_id === IC_ROUTE_R2 ? 1 : 0,
    decisions_pending: member.session_id === PL_DB ? 1 : 0,
    flags,
    issues: mine.map((i) => i.ref!).filter(Boolean),
    last_activity_at: member.now?.last_event_at ?? member.last_activity_at,
    url: member.url,
  };
}

/** The fleet rows of `snapshot` (the round-3 factory by default). */
export function agentRows(snapshot: FactorySnapshot = round3Snapshot()): AgentRowView[] {
  const tasks = round3Tasks();
  const issues = round3IssueList();
  return snapshot.members.map((m) => rowOf(m, tasks, issues));
}

function line(
  minutesAgo: number,
  member: Pick<MemberView, "session_id" | "title" | "role" | "project">,
  kind: LiveLineView["kind"],
  text: string,
  extra: Partial<LiveLineView> = {},
): LiveLineView {
  return {
    id: `line-${member.session_id.slice(0, 6)}-${kind}-${minutesAgo}`,
    ts: ROUND3_NOW_S - minutesAgo * 60,
    session_id: member.session_id,
    title: member.title,
    role: member.role,
    project: member.project,
    kind,
    tool: kind === "tool" ? text.split(" ")[0]! : null,
    text,
    failed: kind === "result" ? true : null,
    ...extra,
  };
}

/** `GET …/agents` of the round-3 factory (or `snapshot`). */
export function agentsResponse(snapshot: FactorySnapshot = round3Snapshot()): AgentsResponse {
  const ic = snapshot.members.find((m) => m.session_id === IC_ROUTE_R2);
  return {
    data: agentRows(snapshot),
    lines: ic
      ? [
          line(1.2, ic, "tool", "Bash pnpm -F caffeine-api test"),
          line(0.6, ic, "result", 'Error: relation "knex_migrations" does not exist'),
        ]
      : [],
    live: { polled_at: ROUND3_NOW_S - 4, interval_s: 10, fresh: true, usd_per_h: 6.1 },
  };
}

/** The fleet at 60 agents across 12 projects. */
export function scaleAgentsResponse(agents = 60): {
  snapshot: FactorySnapshot;
  body: AgentsResponse;
} {
  const snapshot = scaleSnapshot(12, agents);
  for (const [i, member] of snapshot.members.entries()) {
    if (member.running && !member.now) {
      member.now = {
        session_id: member.session_id,
        status: "running",
        turn_n: 10 + i,
        tool: ["Bash", "Edit", "Read", "Grep"][i % 4]!,
        tool_arg: ["pnpm test", "src/routes/index.ts", "README.md", "TODO"][i % 4]!,
        tool_since: ROUND3_NOW_S - (i % 50),
        last_event_at: ROUND3_NOW_S - (i % 7),
        model: null,
        cost_usd: member.cost_usd,
        usd_per_h: 0.3 + (i % 5) * 0.4,
        ctx_pct: null,
        flags: [],
      };
    }
  }
  return { snapshot, body: { ...agentsResponse(snapshot), data: agentRows(snapshot) } };
}

function tool(
  minutesAgo: number,
  name: string,
  arg: string,
  durationS: number | null,
  failed: boolean,
  out: string | null,
): AgentToolCallView {
  const started = ROUND3_NOW_S - minutesAgo * 60;
  return {
    call_id: `call-${name}-${minutesAgo}`,
    tool: name,
    arg,
    started_at: started,
    ended_at: durationS != null ? started + durationS : null,
    duration_s: durationS,
    failed,
    output_head: out,
  };
}

function mail(
  minutesAgo: number,
  direction: "in" | "out",
  peer: string,
  peerTitle: string,
  summary: string,
  kind = "message.delivered",
): AgentMailView {
  return {
    id: `mail-${direction}-${minutesAgo}`,
    ts: (ROUND3_NOW_S - minutesAgo * 60) * 1000,
    kind,
    direction,
    peer_id: peer,
    peer_title: peerTitle,
    mail_kind: direction === "in" ? "request" : "reply",
    mail_id: `m-${minutesAgo}`,
    summary,
  };
}

const KNEX = 'relation "knex_migrations" does not exist';

/** `GET …/agents/{sid}`: ic:route-r2 repeating its failing test (the default), or any agent. */
export function agentDetail(
  sessionId: string = IC_ROUTE_R2,
  snapshot: FactorySnapshot = round3Snapshot(),
): AgentDetailResponse | null {
  const member = snapshot.members.find((m) => norm(m.session_id) === norm(sessionId));
  if (!member) return null;
  const row = rowOf(member, round3Tasks(), round3IssueList());
  const issues = round3IssueList().filter(
    (i) => i.status !== "resolved" && norm(i.target) === norm(`member:${member.session_id}`),
  );
  const tasks = round3Tasks().filter((t) =>
    t.session_ids.some((s) => norm(s) === norm(member.session_id)),
  );
  if (member.session_id !== IC_ROUTE_R2) {
    return {
      agent: row,
      tools: member.now?.tool
        ? [tool(0.4, member.now.tool, member.now.tool_arg ?? "", null, false, null)]
        : [],
      lines: member.summary ? [line(3, member, "message", member.summary)] : [],
      mail: [],
      tasks: tasks.map((t) => ({
        id: t.id,
        ref: t.ref,
        key: t.key,
        title: t.title,
        state: t.state,
        attempts: t.attempts,
        pr_id: t.pr_id,
      })),
      issues,
      cost_series: [],
    };
  }
  const tools: AgentToolCallView[] = [
    tool(0.7, "Bash", "pnpm -F caffeine-api test", null, false, null),
  ];
  for (let i = 1; i <= 6; i += 1) {
    tools.push(
      tool(
        i * 3.5,
        "Bash",
        "pnpm -F caffeine-api test",
        41,
        true,
        `FAIL src/modules/db/version.test.ts\n  ● GET /db/version › returns the latest migration\n    error: ${KNEX}\nexit 1`,
      ),
    );
    tools.push(tool(i * 3.5 + 1.2, "Edit", "src/modules/db/version.ts", 1, false, "+12 −3"));
  }
  tools.push(tool(25, "Read", "services/caffeine-api/src/modules/db/version.ts", 0.4, false, null));
  const lines: LiveLineView[] = [
    line(
      28,
      member,
      "human",
      "[Factory message · from PL · db-version] T-4 route attempt 3/3: fix the migration lookup",
    ),
    line(
      26,
      member,
      "thinking",
      "The test db is created from schema only; knex_migrations is never seeded. Maybe seed it in the test setup?",
    ),
    ...[6, 5, 4, 3, 2, 1].flatMap((i) => [
      line(i * 3.5 + 1.2, member, "tool", "Edit src/modules/db/version.ts"),
      line(i * 3.5, member, "tool", "Bash pnpm -F caffeine-api test"),
      line(i * 3.5 - 0.6, member, "result", `Error: ${KNEX} (exit 1)`),
    ]),
    line(
      1.5,
      member,
      "message",
      "Same failure again. The table is missing in the test database; asking the PL whether to seed it.",
    ),
    line(0.7, member, "tool", "Bash pnpm -F caffeine-api test"),
  ].sort((a, b) => a.ts - b.ts);
  return {
    agent: row,
    tools,
    lines,
    mail: [
      mail(
        28,
        "in",
        PL_DB,
        "PL · db-version",
        "T-4 route attempt 3/3 · fresh worker from NOTES.md",
      ),
      mail(14, "in", PL_DB, "PL · db-version", "re-run with knex seed"),
      mail(
        1.4,
        "out",
        PL_DB,
        "PL · db-version",
        "BLOCKED? the test db has no knex_migrations table",
        "message.queued",
      ),
      mail(0.2, "in", PL_DB, "PL · db-version", "re-run with knex seed"),
    ],
    tasks: tasks.map((t) => ({
      id: t.id,
      ref: t.ref,
      key: t.key,
      title: t.title,
      state: t.state,
      attempts: t.attempts,
      pr_id: t.pr_id,
    })),
    issues,
    cost_series: [
      { day: "2026-10-22", cost_usd: 0.4, tokens_in: 120_000, tokens_out: 8_000, turns: 14 },
      { day: "2026-10-23", cost_usd: 1.1, tokens_in: 310_000, tokens_out: 21_000, turns: 38 },
      { day: "2026-10-24", cost_usd: 0.7, tokens_in: 190_000, tokens_out: 12_000, turns: 22 },
      { day: "2026-10-25", cost_usd: 1.7, tokens_in: 480_000, tokens_out: 30_000, turns: 44 },
    ],
  };
}

/** `GET …/loops` (one project's, or all). */
export function loopsResponse(project?: string | null): LoopsResponse {
  const data = round3LoopList().filter((l) => !project || l.project_slug === project);
  return { data: data.sort((a, b) => b.updated_at - a.updated_at), engine_mode: "assist" };
}

/** `GET …/issues` narrowed by the query's class, status and project. */
export function issuesResponse(path = "", role: "creator" | "viewer" = "creator"): IssuesResponse {
  const query = new URL(path || "/", "http://story").searchParams;
  const cls = query.get("class");
  const statuses = (query.get("status") ?? "").split(",").filter(Boolean);
  const project = query.get("project");
  const data = round3IssueList().filter(
    (i) =>
      (!cls || i.class === cls) &&
      (statuses.length === 0 || statuses.includes(i.status)) &&
      (!project || i.project_slug === project),
  );
  return {
    data,
    count: data.length,
    mine:
      role === "viewer"
        ? []
        : data.filter((i) => i.status === "open" || i.status === "acked").map((i) => i.id),
  };
}

/** Spans of the last hour on the db-version agents: the loop, waits, tools, interventions. */
export function timelineSpans(): SpanView[] {
  const base = round3Spans().data;
  const at = (min: number) => (ROUND3_NOW_S - min * 60) * 1000;
  const extra = (
    kind: SpanView["kind"],
    name: string,
    fromMin: number,
    toMin: number | null,
    patch: Partial<SpanView>,
  ): SpanView => ({
    ...clone(base[0]!),
    id: `tl-${kind}-${name}-${fromMin}`.replace(/\s+/g, "-"),
    kind,
    name,
    trace_key: `${kind}:${name}`,
    parent_id: null,
    loop_id: null,
    task_id: null,
    pr_id: null,
    head_sha: null,
    session_id: null,
    role: null,
    agent_name: null,
    actor: null,
    attempt: null,
    attrs: null,
    started_at: at(fromMin),
    ended_at: toMin == null ? null : at(toMin),
    outcome: toMin == null ? "open" : "ok",
    project_slug: "db-version",
    ...patch,
  });
  const tools: SpanView[] = [];
  for (let i = 1; i <= 7; i += 1) {
    tools.push(
      extra("tool", "Bash pnpm test", i * 3.5, i * 3.5 - 0.7, {
        session_id: IC_ROUTE_R2,
        role: "ic",
        outcome: "fail",
        attrs: { exit: 1 },
      }),
    );
  }
  return [
    ...base,
    ...tools,
    extra("task", "route-r2", 30, null, {
      session_id: IC_ROUTE_R2,
      role: "ic",
      agent_name: "ic:route-r2",
    }),
    extra("task", "env_keeper PREVIEW p9631", 52, 40, { session_id: ENV_DB, role: "env_keeper" }),
    extra("task", "env_keeper probe", 18, 15, { session_id: ENV_DB, role: "env_keeper" }),
    extra("turn", "heartbeat", 45, 44, {
      session_id: PL_DB,
      role: "project_lead",
      attrs: { heartbeat: true },
    }),
    extra("turn", "dispatch route-r2", 31, 29, { session_id: PL_DB, role: "project_lead" }),
    extra("intervention", "interrupt", 9, 9, {
      session_id: "9264e2b0d6105cd289754e95c8162752",
      actor: "mia@caffeine.ai",
      project_slug: "cache-warm",
      attrs: { action: "interrupt" },
    }),
    extra("decision", "D-2", 34, null, {
      session_id: PL_DB,
      role: "project_lead",
      attrs: { blocking: true, source: "agent" },
    }),
    extra("gate", "checks", 22, 20, { pr_id: PR_9631, outcome: "fail" }),
    extra("demo", "FAIL", 12, 12, { pr_id: PR_9631, outcome: "fail" }),
    extra("tool", "Edit Badge.tsx", 6, 5, {
      session_id: "ca26ef1ba9a154b984fa9dd292f7bb3f",
      role: "ic",
      project_slug: "ux-badge",
    }),
    extra("tool", "Read Header.tsx", 3, 2, {
      session_id: "05ba806f739e577ea6bf2df6a3be6bc3",
      role: "reviewer",
      project_slug: "ux-badge",
    }),
  ];
}

/** `GET …/spans` narrowed by kind, project, PR and time like the server does it. */
export function spansResponse(path = ""): SpanPage {
  const query = new URL(path || "/", "http://story").searchParams;
  const kinds = (query.get("kind") ?? "").split(",").filter(Boolean);
  const project = query.get("project");
  const pr = query.get("pr_id");
  const since = Number(query.get("since") ?? 0);
  const until = Number(query.get("until") ?? Number.POSITIVE_INFINITY);
  const data = timelineSpans()
    .filter(
      (s) =>
        (kinds.length === 0 || kinds.includes(s.kind)) &&
        (!project || s.project_slug === project) &&
        (!pr || s.pr_id === pr) &&
        s.started_at >= since - 7 * 86_400_000 &&
        s.started_at < until,
    )
    .sort((a, b) => a.started_at - b.started_at);
  return { data, has_more: false, next_since: null };
}

function event(
  minutesAgo: number,
  kind: string,
  summary: string,
  extra: Partial<EventView> = {},
): EventView {
  return {
    id: `inc-ev-${kind}-${minutesAgo}`,
    ts: (ROUND3_NOW_S - minutesAgo * 60) * 1000,
    kind,
    severity: "info",
    project_slug: "db-version",
    from_id: null,
    to_id: null,
    from_title: null,
    to_title: null,
    subject: null,
    summary,
    data: null,
    ...extra,
  };
}

/** `GET …/issues/{ref}`: the incident page (INC-13 by default). */
export function issuePage(ref = "INC-13"): IssuePageResponse | null {
  const issue = round3IssueList().find((i) => i.ref === ref || i.id === ref);
  if (!issue) return null;
  const spans = timelineSpans().filter(
    (s) =>
      s.project_slug === issue.project_slug &&
      (s.session_id === IC_ROUTE_R2 ||
        s.session_id === PL_DB ||
        s.kind === "loop_iter" ||
        s.kind === "loop"),
  );
  return {
    issue,
    mine: true,
    events: [
      event(
        0.5,
        "agent.repeat",
        "ic:route-r2 · same failing command 7× · pnpm -F caffeine-api test",
        { severity: "warning", from_id: IC_ROUTE_R2, from_title: "ic:route-r2" },
      ),
      event(4, "message.delivered", "PL · db-version → ic:route-r2 · re-run with knex seed", {
        from_id: PL_DB,
        to_id: IC_ROUTE_R2,
      }),
      event(14, "dispatch", "PL · db-version dispatched route-r2 (attempt 3/3)", {
        from_id: PL_DB,
        to_id: IC_ROUTE_R2,
      }),
      event(22, "gate.changed", "!9631 CI caffeine-api test FAIL (same check as r0, r1)", {
        severity: "warning",
        subject: `pr:${PR_9631}`,
      }),
      event(29, "loop.iteration", "T-4 implement r1 → CI fail", { severity: "notice" }),
      event(34, "decision.opened", "D-2 · Which table is the source of truth?", {
        severity: "notice",
        from_id: PL_DB,
      }),
    ],
    spans,
    sessions: [
      { session_id: IC_ROUTE_R2, title: "ic:route-r2" },
      { session_id: PL_DB, title: "PL · db-version" },
    ],
  };
}

/** The transcript the Agent page's Transcript tab reads (newest first, as the server pages). */
export function itemsPage(): { data: unknown[]; has_more: boolean } {
  return { data: [...clone(transcriptFixture.transcripts.pl_api)].reverse(), has_more: true };
}

/**
 * `/v1/factory/*` ops routes for factory `factoryId` (and the session routes
 * of its members' Agent pages) over `snapshot`.
 */
export function opsRoutes(
  factoryId: string,
  options: { snapshot?: FactorySnapshot; agents?: AgentsResponse; viewer?: boolean } = {},
): StoryRoutes {
  const snapshot = options.snapshot ?? round3Snapshot();
  const base = `/v1/factory/factories/${factoryId}`;
  const routes: StoryRoutes = {
    // The Room dock beside every page; its loops and issues give way to the ops ones below.
    ...roomRoutes(factoryId, { role: options.viewer ? "viewer" : "creator" }),
    [`${base}/agents`]: { body: options.agents ?? agentsResponse(snapshot) },
    [`${base}/loops`]: (path) => ({
      body: loopsResponse(new URL(path, "http://story").searchParams.get("project")),
    }),
    [`${base}/issues`]: (path) => ({
      body: issuesResponse(path, options.viewer ? "viewer" : "creator"),
    }),
    [`${base}/spans`]: (path) => ({ body: spansResponse(path) }),
    [`${base}/cost`]: {
      body: {
        group_by: ["role"],
        data: [
          { role: "ic", cost_usd: 14.7, tokens_in: 0, tokens_out: 0, turns: 410, priced: true },
          {
            role: "project_lead",
            cost_usd: 4.1,
            tokens_in: 0,
            tokens_out: 0,
            turns: 96,
            priced: true,
          },
          { role: "reviewer", cost_usd: 0.9, tokens_in: 0, tokens_out: 0, turns: 22, priced: true },
          {
            role: "env_keeper",
            cost_usd: 0.6,
            tokens_in: 0,
            tokens_out: 0,
            turns: 31,
            priced: true,
          },
        ],
        totals: { cost_usd: 20.3, tokens_in: 0, tokens_out: 0, turns: 559 },
      },
    },
    [`${base}/alerts/routing`]: {
      body: { cockpit: true, browser: true, slack: false, slack_min_severity: "warning" },
    },
  };
  // Project pages opened from the ops pages read their demos too.
  for (const project of snapshot.projects) {
    routes[`${base}/projects/${project.slug}/demos`] = { body: { data: [], count: 0 } };
  }
  for (const member of snapshot.members) {
    const detail = agentDetail(member.session_id, snapshot);
    if (detail) routes[`${base}/agents/${member.session_id}`] = { body: detail };
    routes[`/v1/sessions/${member.session_id}/stream`] = "pending";
    routes[`/v1/sessions/${member.session_id}/items`] = { body: itemsPage() };
    routes[`/v1/sessions/${member.session_id}/resources/terminals`] = { body: { data: [] } };
    routes[`/v1/sessions/${member.session_id}/resources/github`] = {
      body: { object: "session.github.info", available: false, reason: "no_os_env" },
    };
  }
  for (const issue of round3IssueList()) {
    const page = issuePage(issue.ref ?? issue.id);
    if (!page) continue;
    routes[`${base}/issues/${issue.ref ?? issue.id}`] = { body: page };
  }
  return routes;
}
