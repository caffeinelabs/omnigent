// Fixtures for the Room, cards, Decisions and Graph, built on the round-3
// fixture (`snapshot.round3.json`: threads, 15 messages, proposals C-2/C-4/
// C-11/C-14/C-15, 11 tasks, loops, incidents) plus generated scale: a busy
// thread to page through, 60 agents, a 40-task work graph and a 12-agent
// bus. Every export is a fresh copy a caller may mutate.

import type {
  ChatMessageView,
  FactorySnapshot,
  IssueView,
  LoopView,
  MemberView,
  ProposalView,
  TaskView,
  ThreadView,
  ViewerRole,
} from "@/lib/factoryApi";
import type {
  BusGraphResponse,
  ChatPage,
  FlowGraphResponse,
  OrgGraphResponse,
  OrgNode,
  TasksResponse,
  WorkGraphProject,
  WorkGraphResponse,
} from "@/lib/factoryRoomApi";
import round3Json from "../../__fixtures__/snapshot.round3.json";
import type { StoryRoutes } from "../../factoryStoryFixtures";
import { ROUND3_NOW_S, round3People, round3Snapshot } from "../../shell/__fixtures__/shellFixtures";

const clone = <T>(value: T): T => structuredClone(value);

interface Round3Room {
  threads: ThreadView[];
  chat: ChatMessageView[];
  proposals: ProposalView[];
  tasks: TaskView[];
  loops: LoopView[];
  issues: IssueView[];
}

const ROUND3 = round3Json as unknown as Round3Room;
export const ROOM_FID = round3Snapshot().factory.id;
export const ROOM_NOW_S = ROUND3_NOW_S;
export const ATLAS_ID = round3Snapshot().factory.root_session_id;

/**
 * The Room's threads as `GET …/chat/threads` lists them (Luka's unread
 * counts): the fixed ones, one per project of `snapshot` (quiet ones with no
 * messages yet), then the PR threads.
 */
export function roomThreads(snapshot: FactorySnapshot = round3Snapshot()): ThreadView[] {
  const recorded = clone(ROUND3.threads);
  const projects = snapshot.projects.map((project): ThreadView => {
    const key = `project:${project.slug}`;
    const found = recorded.find((t) => t.key === key);
    if (found) return found;
    const pl = snapshot.members.find(
      (m) => m.role === "project_lead" && m.project === project.slug,
    );
    return {
      key,
      kind: "project",
      title: `#${project.slug}`,
      project_slug: project.slug,
      pr_id: null,
      pr_number: null,
      responder_session_id: pl?.session_id ?? snapshot.factory.root_session_id,
      responder_title: pl?.title ?? "Atlas",
      last_seq: 0,
      last_at: null,
      messages: 0,
      unread: 0,
    };
  });
  return [
    ...recorded.filter((t) => t.kind === "factory" || t.kind === "improvements"),
    ...projects,
    ...recorded.filter((t) => t.kind === "pr"),
  ];
}

/** Every Room message of the round-3 fixture, oldest first. */
export function roomMessages(thread?: string): ChatMessageView[] {
  const all = clone(ROUND3.chat);
  return thread ? all.filter((m) => m.thread === thread) : all;
}

export function roomProposals(): ProposalView[] {
  return clone(ROUND3.proposals);
}

export function roomLoops(): LoopView[] {
  return clone(ROUND3.loops);
}

export function roomIssues(): IssueView[] {
  return clone(ROUND3.issues);
}

export function roomTasks(): TaskView[] {
  return clone(ROUND3.tasks);
}

function proposalBy(ref: string): ProposalView {
  return roomProposals().find((p) => p.ref === ref)!;
}

/** C-4 (Atlas's billing split, pending) in each state a card shows. */
export function proposalInState(
  state:
    | "pending"
    | "applied"
    | "auto_applied"
    | "refused"
    | "rejected"
    | "expired"
    | "withdrawn"
    | "undone",
  nowS = ROOM_NOW_S,
): ProposalView {
  const p = proposalBy("C-4");
  switch (state) {
    case "pending":
      return p;
    case "applied":
      return {
        ...p,
        status: "applied",
        decided_by: "mia@caffeine.ai",
        decided_at: nowS - 20,
        applied_at: nowS - 20,
        undo_until: nowS + 580,
        result: { spawning: ["billing-schema"], held: { "billing-api": ["billing-schema"] } },
        updated_at: nowS - 20,
      };
    case "auto_applied":
      return {
        ...p,
        status: "applied",
        approval: "auto",
        decided_by: p.author,
        applied_at: nowS - 40,
        undo_until: nowS + 560,
        result: { spawning: ["billing-schema"] },
        updated_at: nowS - 40,
      };
    case "refused":
      return {
        ...p,
        status: "refused",
        decided_by: "luka@caffeine.ai",
        result: { error: "over_caps" },
        updated_at: nowS - 60,
      };
    case "rejected":
      return {
        ...p,
        status: "rejected",
        decided_by: "luka@caffeine.ai",
        decided_at: nowS - 300,
        result: { note: "billing waits for Q1; keep the API contract stable" },
        updated_at: nowS - 300,
      };
    case "expired":
      return { ...p, status: "expired", updated_at: nowS - 3600 };
    case "withdrawn":
      return { ...p, status: "withdrawn", updated_at: nowS - 3600 };
    case "undone":
      return {
        ...p,
        status: "undone",
        reverted_by: "mia@caffeine.ai",
        reverted_at: nowS - 30,
        updated_at: nowS - 30,
      };
  }
}

/** A thread with `count` messages (people and agents talking), for paging and long text. */
export function busyThread(thread = "factory", count = 120): ChatMessageView[] {
  const base = roomMessages("factory")[0]!;
  const people = ["luka@caffeine.ai", "mia@caffeine.ai"];
  const out: ChatMessageView[] = [];
  for (let i = 1; i <= count; i += 1) {
    const agent = i % 3 === 0;
    const author = agent ? ATLAS_ID : people[i % 2]!;
    out.push({
      ...clone(base),
      id: `busy${String(i).padStart(28, "0")}`,
      thread,
      seq: 1000 + i,
      author_kind: agent ? "agent" : "human",
      author,
      author_title: agent ? "Atlas" : author === people[0] ? "Luka" : "Mia",
      author_role: agent ? "lead" : author === people[0] ? "owner" : "operator",
      body: agent
        ? `Step ${i}: re-ran the CI for !9631 and posted D-${30 + (i % 5)}; #db-version waits on T-4.`
        : i % 17 === 0
          ? `A long note to check wrapping: ${"supercalifragilistic-expialidocious-".repeat(4)} and a very long path services/caffeine-api/src/modules/db/migrations/knex/2026-10-27-add-version-table.ts`
          : `@atlas message ${i} about #api-version`,
      mentions: agent ? [] : ["@atlas"],
      refs: [],
      reply_to: null,
      card: null,
      routed: agent
        ? []
        : [
            {
              session_id: ATLAS_ID,
              title: "Atlas",
              mail_id: `m${i}`,
              delivered_at: ROOM_NOW_S - (count - i) * 60 + 5,
              replied_at: null,
            },
          ],
      created_at: ROOM_NOW_S - (count - i) * 60,
    });
  }
  return out;
}

/** `GET …/chat?thread=&before_seq=&limit=` over `messages` (oldest first, latest page). */
export function chatPage(messages: readonly ChatMessageView[], path: string): ChatPage {
  const query = new URL(path, "http://story").searchParams;
  const thread = query.get("thread");
  const before = query.get("before_seq");
  const limit = Number(query.get("limit") ?? 50);
  const inThread = messages.filter((m) => !thread || m.thread === thread);
  const older = before ? inThread.filter((m) => m.seq < Number(before)) : inThread;
  const page = older.slice(-limit);
  return { data: clone(page), has_more: older.length > page.length, thread };
}

/** The round-3 snapshot with `n` agents (sub-agents added under the PLs). */
export function scaleSnapshot(n = 60, role: ViewerRole = "creator"): FactorySnapshot {
  const snapshot = round3Snapshot(role);
  const pls = snapshot.members.filter((m) => m.role === "project_lead");
  let i = 0;
  while (snapshot.members.length < n) {
    const pl = pls[i % pls.length]!;
    const sid = `scale${String(i).padStart(27, "0")}`;
    const member: MemberView = {
      ...clone(snapshot.members.find((m) => m.role === "ic")!),
      session_id: sid,
      title: `ic:task-${i}`,
      project: pl.project,
      parent_session_id: pl.session_id,
      running: i % 4 === 0,
      url: `/c/${sid}`,
    };
    snapshot.members.push(member);
    i += 1;
  }
  return snapshot;
}

// ── Graphs ─────────────────────────────────────────────────────

function summaryOf(tasks: readonly TaskView[], ready: number): WorkGraphProject["summary"] {
  return {
    total: tasks.length,
    done: tasks.filter((t) => t.state === "done").length,
    ready,
    running: tasks.filter((t) => t.state === "running" || t.state === "review").length,
    blocked: tasks.filter((t) => t.state === "blocked").length,
    failed: tasks.filter((t) => t.state === "failed").length,
    critical_path_min: null,
  };
}

/**
 * A 40-task work graph over 4 projects: layered deps (up to 5 waves), a
 * critical path per project, a decision-blocked task, ready tasks, a serial
 * pair, a scope-overlap warning and a project dependency.
 */
export function workGraph40(): WorkGraphResponse {
  const base = roomTasks().find((t) => t.ref === "T-5")!;
  const projects = ["db-version", "api-version", "ux-badge", "settings-row"];
  const states: TaskView["state"][] = [
    "done",
    "done",
    "running",
    "review",
    "planned",
    "planned",
    "planned",
    "blocked",
    "planned",
    "planned",
  ];
  const tasks: TaskView[] = [];
  let n = 0;
  for (const slug of projects) {
    for (let k = 0; k < 10; k += 1) {
      n += 1;
      const layer = k < 2 ? 1 : k < 5 ? 2 : k < 8 ? 3 : k < 9 ? 4 : 5;
      const key = `${slug.split("-")[0]}-t${k + 1}`;
      const deps =
        layer === 1
          ? []
          : layer === 2
            ? [`${slug.split("-")[0]}-t${(k % 2) + 1}`]
            : layer === 3
              ? [`${slug.split("-")[0]}-t${k - 2}`]
              : [`${slug.split("-")[0]}-t${k - 1}`];
      tasks.push({
        ...clone(base),
        id: `task${String(n).padStart(28, "0")}`,
        ref: `T-${n}`,
        number: n,
        project_slug: slug,
        key,
        title: [
          "Schema migration",
          "Docs stub",
          "Route handler",
          "Unit tests",
          "Settings row",
          "Contract test",
          "Preview check",
          "Demo script",
          "E2E smoke",
          "Release notes",
        ][k]!,
        state: states[k]!,
        deps: k === 7 ? [...deps, "decision:D-2"] : deps,
        scope:
          k === 5 || k === 6 ? [`services/${slug}/src/shared/**`] : [`services/${slug}/t${k}/**`],
        wave: layer,
        critical: [0, 2, 5, 8, 9].includes(k),
        waiting_on: k === 7 ? ["decision:D-2"] : [],
        attempts: k === 2 ? 2 : 0,
        loop:
          k === 2
            ? { id: `loop${n}`, kind: "implement", state: "running", iteration: 2, limit: 3 }
            : null,
        pr_id: null,
        pr_number: k === 3 ? 9600 + n : null,
        estimate_min: 30 + k * 10,
      });
    }
  }
  const layers = Object.fromEntries(tasks.map((t) => [t.id, t.wave ?? 1]));
  const edges: WorkGraphResponse["edges"] = [];
  for (const task of tasks) {
    for (const dep of task.deps) {
      const from = tasks.find((t) => t.project_slug === task.project_slug && t.key === dep);
      if (from) edges.push({ from: from.id, to: task.id, kind: "dep", met: from.state === "done" });
    }
  }
  const serialA = tasks.find((t) => t.project_slug === "api-version" && t.key === "api-t6")!;
  const serialB = tasks.find((t) => t.project_slug === "api-version" && t.key === "api-t7")!;
  edges.push({ from: serialA.id, to: serialB.id, kind: "serial", met: false });
  const blocked = tasks.filter((t) => t.waiting_on.length > 0);
  const graphProjects: WorkGraphProject[] = projects.map((slug) => {
    const mine = tasks.filter((t) => t.project_slug === slug);
    const ready = mine
      .filter(
        (t) =>
          t.state === "planned" &&
          t.deps.every((d) => mine.find((m) => m.key === d)?.state === "done"),
      )
      .map((t) => t.key);
    return {
      slug,
      project: slug,
      title: round3Snapshot().projects.find((p) => p.slug === slug)?.title ?? slug,
      status: "active",
      stage: round3Snapshot().projects.find((p) => p.slug === slug)?.stage ?? "building",
      depends_on: slug === "settings-row" ? ["api-version", "db-version"] : [],
      ready,
      waves: [ready],
      critical_path: mine.filter((t) => t.critical).map((t) => t.key),
      critical_path_min: 310,
      blocked: mine
        .filter((t) => t.waiting_on.length)
        .map((t) => ({ key: t.key, waiting_on: t.waiting_on })),
      slots: { ics_free: 1, previews_free: 1 },
      paused: false,
      warnings: [],
      summary: { ...summaryOf(mine, ready.length), critical_path_min: 310 },
    };
  });
  return {
    kind: "work",
    factory_id: ROOM_FID,
    project: null,
    generated_at: ROOM_NOW_S,
    projects: graphProjects,
    tasks,
    layers,
    edges,
    external_edges: blocked.map((t) => ({
      from: "decision:D-2",
      to: t.id,
      kind: "decision",
      met: false,
      assignee: "mia@caffeine.ai",
      status: "open",
    })),
    project_edges: [
      { from: "api-version", to: "settings-row", kind: "depends_on", met: true },
      { from: "db-version", to: "settings-row", kind: "depends_on", met: false },
    ],
    warnings: [
      {
        code: "scope_overlap",
        tasks: ["db-t6", "db-t7"],
        paths: ["services/db-version/src/shared/**"],
        message:
          "db-t6 and db-t7 both touch services/db-version/src/shared/**; they run one after the other unless one depends on the other",
        project: "db-version",
      },
    ],
  };
}

/** The round-3 tasks as `GET …/graph?kind=work` returns them (11 tasks, 5 projects). */
export function workGraphRound3(): WorkGraphResponse {
  const tasks = roomTasks();
  const slugs = [...new Set(tasks.map((t) => t.project_slug))];
  const edges: WorkGraphResponse["edges"] = [];
  for (const task of tasks) {
    for (const dep of task.deps) {
      const from = tasks.find((t) => t.project_slug === task.project_slug && t.key === dep);
      if (from) edges.push({ from: from.id, to: task.id, kind: "dep", met: from.state === "done" });
    }
  }
  return {
    kind: "work",
    factory_id: ROOM_FID,
    project: null,
    generated_at: ROOM_NOW_S,
    projects: slugs.map((slug) => {
      const mine = tasks.filter((t) => t.project_slug === slug);
      const ready = mine.filter((t) => t.state === "ready").map((t) => t.key);
      return {
        slug,
        project: slug,
        title: round3Snapshot().projects.find((p) => p.slug === slug)?.title ?? slug,
        status: "active",
        stage: null,
        depends_on: [],
        ready,
        waves: [ready],
        critical_path: mine.filter((t) => t.critical).map((t) => t.key),
        critical_path_min: slug === "db-version" ? 190 : null,
        blocked: [],
        slots: { ics_free: 1, previews_free: 1 },
        paused: false,
        warnings: [],
        summary: summaryOf(mine, ready.length),
      };
    }),
    tasks,
    layers: Object.fromEntries(tasks.map((t) => [t.id, t.wave ?? 1])),
    edges,
    external_edges: tasks
      .filter((t) => t.waiting_on.some((w) => w.startsWith("decision:")))
      .map((t) => ({
        from: t.waiting_on[0]!,
        to: t.id,
        kind: "decision" as const,
        met: false,
        assignee: "operators",
        status: "open" as const,
      })),
    project_edges: [],
    warnings: [],
  };
}

/** The tasks list (`GET …/tasks`) of the 40-task graph. */
export function tasksOf(graph: WorkGraphResponse): TasksResponse {
  return { tasks: graph.tasks, projects: graph.projects };
}

/** A 12-node bus: 2 people, the server, Atlas, Sentinel, 3 PLs and 4 sub-agents. */
export function busGraph12(nowMs = ROOM_NOW_S * 1000): BusGraphResponse {
  const snapshot = round3Snapshot();
  const pick = (title: string) => snapshot.members.find((m) => m.title === title)!;
  const agents = [
    pick("Atlas"),
    pick("Lead B · Sentinel"),
    pick("PL · db-version"),
    pick("PL · api-version"),
    pick("PL · ux-badge"),
    pick("ic:route-r2"),
    pick("review-route-r1"),
    pick("ic_codex:badge-tests"),
    snapshot.members.find((m) => m.title === "env_keeper" && m.project === "db-version")!,
  ];
  const nodes: BusGraphResponse["nodes"] = [
    { id: "human:luka@caffeine.ai", kind: "human", title: "luka@caffeine.ai" },
    { id: "human:operators", kind: "human", title: "operators" },
    { id: "server", kind: "server", title: "server" },
    ...agents.map((m) => ({
      id: m.session_id,
      kind: "agent" as const,
      title: m.title,
      role: m.role,
      project: m.project,
      health: m.health,
    })),
  ];
  const [atlas, sentinel, plDb, plApi, plUx, ic, rev, codex, env] = agents.map(
    (a) => a.session_id,
  ) as string[];
  const message = (
    from: string,
    to: string,
    count: number,
    extra: Partial<Record<"bounced" | "unanswered" | "requests", number>> = {},
    agoMin = 5,
  ) => ({
    from,
    to,
    kind: "message" as const,
    count,
    kinds: { request: extra.requests ?? Math.ceil(count / 2), reply: Math.floor(count / 2) },
    delivered: count - (extra.bounced ?? 0),
    bounced: extra.bounced ?? 0,
    requests: extra.requests ?? Math.ceil(count / 2),
    unanswered: extra.unanswered ?? 0,
    p50_reply_s: 42,
    last_at: nowMs - agoMin * 60_000,
  });
  return {
    kind: "bus",
    factory_id: ROOM_FID,
    project: null,
    generated_at: ROOM_NOW_S,
    window_s: 3600 * 6,
    since_ms: nowMs - 6 * 3600_000,
    until_ms: nowMs,
    nodes,
    edges: [
      message(atlas!, sentinel!, 12),
      message(sentinel!, atlas!, 11),
      message(atlas!, plDb!, 4),
      message(plDb!, atlas!, 5),
      message(plDb!, ic!, 7),
      message(ic!, plDb!, 6, { bounced: 1 }),
      message(plDb!, rev!, 3),
      message(plUx!, sentinel!, 2, { unanswered: 2, requests: 2 }, 150),
      message(plUx!, codex!, 3),
      message(plApi!, env!, 1),
      message("server", plDb!, 9),
      message("server", atlas!, 3),
      { from: atlas!, to: plDb!, kind: "supervise" },
      { from: plDb!, to: ic!, kind: "spawn" },
      {
        from: plDb!,
        to: "human:luka@caffeine.ai",
        kind: "waits_on",
        label: "D-2",
        blocking: true,
        since: ROOM_NOW_S - 2040,
      },
      {
        from: plDb!,
        to: "human:operators",
        kind: "waits_on",
        label: "D-31",
        blocking: true,
        since: ROOM_NOW_S - 600,
      },
      { from: "server", to: plUx!, kind: "alert", count: 2 },
    ],
  };
}

export function flowGraph(): FlowGraphResponse {
  const node = (
    id: string,
    count: number,
    n: number,
    dwell: number | null,
    usd: number | null,
  ) => ({ id, count, n, p50_dwell_s: dwell, usd });
  return {
    kind: "flow",
    factory_id: ROOM_FID,
    project: null,
    generated_at: ROOM_NOW_S,
    window_s: 7 * 86400,
    since_ms: (ROOM_NOW_S - 7 * 86400) * 1000,
    nodes: [
      node("plan", 1, 6, 1800, 2.4),
      node("build", 3, 14, 5400, 31.2),
      node("review", 1, 12, 2400, 6.8),
      node("verify", 0, 11, 1500, 4.1),
      node("demo", 1, 9, 900, 3.2),
      node("ready", 1, 8, 9600, null),
      node("merged", 2, 7, null, null),
    ],
    edges: ["plan", "build", "review", "verify", "demo", "ready", "merged"]
      .slice(1)
      .map((to, i, all) => ({
        from: i === 0 ? "plan" : all[i - 1]!,
        to,
      })),
    back_edges: [
      { from: "review", to: "build", label: "changes", count: 3 },
      { from: "build", to: "build", label: "CI fail", count: 2 },
      { from: "verify", to: "build", label: "FAIL(env)", count: 1 },
    ],
  };
}

/** Every member of `snapshot` as `GET …/graph?kind=org` lists them (any depth). */
export function orgGraphOf(snapshot: FactorySnapshot = round3Snapshot()): OrgGraphResponse {
  const depth = (m: MemberView): number => {
    let d = 0;
    let at: MemberView | undefined = m;
    while (at && !at.is_top_level && at.parent_session_id) {
      d += 1;
      at = snapshot.members.find((x) => x.session_id === at!.parent_session_id);
      if (d > 10) break;
    }
    return d;
  };
  const nodes: OrgNode[] = snapshot.members.map((m) => ({
    id: m.session_id,
    title: m.title,
    role: m.role,
    project: m.project,
    parent: m.parent_session_id,
    supervisor:
      m.is_top_level && m.role === "project_lead" ? snapshot.factory.root_session_id : null,
    health: m.health,
    status: m.running ? "running" : m.status,
    state: m.state,
    is_top_level: m.is_top_level,
    depth: depth(m),
  }));
  const groups = new Map<string | null, string[]>();
  for (const node of nodes)
    groups.set(node.project, [...(groups.get(node.project) ?? []), node.id]);
  return {
    kind: "org",
    factory_id: ROOM_FID,
    project: null,
    generated_at: ROOM_NOW_S,
    truncated: false,
    nodes,
    edges: nodes
      .filter((n) => !n.is_top_level && n.parent)
      .map((n) => ({ from: n.parent!, to: n.id, kind: "spawn" as const })),
    groups: [...groups].map(([project, members]) => ({
      project,
      members,
      health: members.some((id) => nodes.find((n) => n.id === id)?.health === "dead")
        ? "dead"
        : "ok",
      by_health: { ok: members.length },
    })),
  };
}

// ── Routes ─────────────────────────────────────────────────────

export interface RoomRouteOptions {
  threads?: ThreadView[] | "pending" | { error: number };
  messages?: ChatMessageView[] | "pending" | { error: number };
  proposals?: ProposalView[];
  work?: WorkGraphResponse;
  bus?: BusGraphResponse;
  flow?: FlowGraphResponse;
  org?: OrgGraphResponse;
  role?: ViewerRole;
}

/** The Room, proposals, graph, loops, issues and decisions routes of `factoryId`, for the story kit. */
export function roomRoutes(factoryId = ROOM_FID, options: RoomRouteOptions = {}): StoryRoutes {
  const base = `/v1/factory/factories/${factoryId}`;
  const messages = options.messages ?? roomMessages();
  const work = options.work ?? workGraphRound3();
  return {
    [`${base}/chat/threads`]:
      options.threads === "pending"
        ? "pending"
        : options.threads && "error" in options.threads
          ? { error: options.threads.error }
          : { body: { data: (options.threads as ThreadView[] | undefined) ?? roomThreads() } },
    [`${base}/chat`]:
      messages === "pending"
        ? "pending"
        : "error" in messages
          ? { error: messages.error }
          : (path) => ({ body: chatPage(messages, path) }),
    [`${base}/chat/reads`]: { body: { thread: "factory", last_seq: 1 } },
    [`${base}/status-digest`]: {
      body: {
        markdown:
          "**Atlas factory** · active · 6 projects · 1 ready for human merge · 6 decisions open · $84.20\n- `api-version` ready · PL · api-version idle · !9630 ready for human merge\n- `db-version` building (blocked: D-31) · PL · db-version running",
        slug: null,
      },
    },
    [`${base}/proposals`]: { body: { data: options.proposals ?? roomProposals() } },
    [`${base}/loops`]: { body: { data: roomLoops() } },
    [`${base}/issues`]: { body: { data: roomIssues(), count: roomIssues().length, mine: [] } },
    [`${base}/tasks`]: { body: tasksOf(work) },
    [`${base}/graph`]: (path) => {
      const kind = new URL(path, "http://story").searchParams.get("kind") ?? "work";
      if (kind === "bus") return { body: options.bus ?? busGraph12() };
      if (kind === "flow") return { body: options.flow ?? flowGraph() };
      if (kind === "org") return { body: options.org ?? orgGraphOf() };
      return { body: work };
    },
    [`${base}/people`]: { body: round3People(options.role ?? "creator") },
    // The dock's project page reads its demos (none recorded in these stories).
    [`${base}/projects/db-version/demos`]: { body: { data: [], count: 0 } },
  };
}
