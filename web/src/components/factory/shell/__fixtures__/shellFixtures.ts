// Fixtures for the factory shell (header, Overview, Projects, Share and
// People, ⌘K) built from the round-3 fixture (`snapshot.round3.json`: 7
// projects, 41 agents, 3 people, a loop at 3/3, an incident, a proposal) and
// the live run. Every export is a fresh copy the caller may mutate.

import type {
  EventPage,
  EventView,
  FactoryListResponse,
  FactorySnapshot,
  MemberView,
  PeopleResponse,
  PersonView,
  ProjectView,
  PrView,
  SpanPage,
  SpanView,
  ViewerBlock,
  ViewerRole,
} from "@/lib/factoryApi";
import {
  LIVE_RUN_FACTORY_ID,
  allDoneSnapshot,
  summaryOf,
  tenProjectsSnapshot,
} from "../../__fixtures__/liveRun";
import round3Json from "../../__fixtures__/snapshot.round3.json";

const clone = <T>(value: T): T => structuredClone(value);

interface Round3 {
  snapshot: FactorySnapshot;
  people: PersonView[];
  spans: SpanView[];
}

const ROUND3 = round3Json as unknown as Round3;

/** The round-3 fixture's "now" (epoch seconds). */
export const ROUND3_NOW_S = ROUND3.snapshot.server_time;
export const ROUND3_FACTORY_ID = ROUND3.snapshot.factory.id;
export const BASE_HEAD = "57ab6719c3d2e1f0a9b8c7d6e5f4a3b2c1d0e9f8";

const LEVEL: Record<ViewerRole, number> = { viewer: 1, operator: 2, owner: 3, creator: 4 };
const NEED: Record<keyof ViewerBlock["can"], number> = {
  post: 2,
  answer: 2,
  review: 2,
  control: 2,
  intervene: 2,
  apply_projects: 2,
  apply_tuning: 3,
  restart: 3,
  stop: 3,
  invite: 3,
  settings: 3,
  terminal: 4,
  start: 4,
};

const USER: Record<ViewerRole, string> = {
  creator: "luka@caffeine.ai",
  owner: "luka@caffeine.ai",
  operator: "mia@caffeine.ai",
  viewer: "ops@caffeine.ai",
};

/** The snapshot's viewer block for `role` (the server's levels per lever). */
export function viewerOf(role: ViewerRole, userId: string = USER[role]): ViewerBlock {
  const level = LEVEL[role];
  return {
    user_id: userId,
    role,
    level: Math.min(level, 3),
    is_creator: role === "creator",
    can: Object.fromEntries(
      Object.entries(NEED).map(([lever, need]) => [lever, level >= need]),
    ) as unknown as ViewerBlock["can"],
  };
}

/** The base block of a temp-main factory: head 57ab671, the base env at it. */
function baseBlock(snapshot: FactorySnapshot): NonNullable<FactorySnapshot["factory"]["base"]> {
  const env = snapshot.projects.find((p) => p.slug === "base-env")?.env ?? null;
  return {
    repo: "caffeinelabs/app",
    branch: "temp-main",
    head_sha: BASE_HEAD,
    moved_at: ROUND3_NOW_S - 12 * 60,
    env,
    env_project: "base-env",
  };
}

/**
 * The round-3 factory: Atlas factory on temp-main with 7 projects, 41 agents,
 * Luka (creator) and Mia here, Ops away, D-2 for Mia, a loop at 3/3.
 *
 * @param role - Whose view (the viewer block); the creator's by default.
 */
export function round3Snapshot(role: ViewerRole = "creator"): FactorySnapshot {
  const snapshot = clone(ROUND3.snapshot);
  snapshot.factory.base = baseBlock(snapshot);
  snapshot.viewer = viewerOf(role);
  // The fixture's PR heads are ahead of their demos; keep whatever it says.
  return snapshot;
}

/** The roster `GET …/people` serves for the round-3 factory, as `role` sees it. */
export function round3People(role: ViewerRole = "creator"): PeopleResponse {
  const data = clone(ROUND3.people);
  return {
    data,
    runs_as: { user_id: "luka@caffeine.ai", name: "luka@caffeine.ai" },
    count: data.length,
    viewer: viewerOf(role),
  };
}

let spanSeq = 0;
/** A span as the recorder writes it, `fromMin`..`toMin` minutes ago (null: still open). */
export function serverSpan(
  kind: SpanView["kind"],
  name: string,
  project: string,
  fromMin: number,
  toMin: number | null,
  extra: Partial<SpanView> = {},
): SpanView {
  spanSeq += 1;
  return {
    ...clone(ROUND3.spans[0]!),
    id: `span-r3-${spanSeq}`,
    trace_key: `${kind}:${project}`,
    task_id: null,
    role: null,
    agent_name: null,
    kind,
    name,
    project_slug: project,
    started_at: (ROUND3_NOW_S - fromMin * 60) * 1000,
    ended_at: toMin == null ? null : (ROUND3_NOW_S - toMin * 60) * 1000,
    outcome: toMin == null ? "open" : "ok",
    ...extra,
  };
}

/**
 * The round-3 spans (`GET …/spans`): the db-version loop, its tools, waits
 * and gates, plus the long-lived episodes the recorder keeps open (env and
 * preview states, the current PR round, a ready wait).
 */
export function round3Spans(): SpanPage {
  spanSeq = 0;
  const readySince = ROUND3.snapshot.projects.find((p) => p.slug === "api-version")!.stage_since;
  const episodes = [
    serverSpan("env", "ready", "db-version", 180, null),
    serverSpan("env", "ready", "api-version", 300, null),
    serverSpan("env", "starting", "ux-badge", 70, 64),
    serverSpan("env", "ready", "ux-badge", 64, null),
    serverSpan("preview", "deploying", "ux-badge", 8, 4),
    serverSpan("preview", "ready", "ux-badge", 4, null),
    serverSpan("pr_round", "0b1c2d3", "ux-badge", 120, null),
    serverSpan("gate", "checks", "cache-warm", 24, null),
    serverSpan("ready_wait", "ready", "api-version", (ROUND3_NOW_S - readySince) / 60, null),
  ];
  return { data: [...clone(ROUND3.spans), ...episodes], has_more: false, next_since: null };
}

let seq = 0;
function event(
  minutesAgo: number,
  kind: string,
  summary: string,
  extra: Partial<EventView> = {},
): EventView {
  seq += 1;
  return {
    id: `ev-r3-${seq}`,
    ts: (ROUND3_NOW_S - minutesAgo * 60) * 1000,
    kind,
    severity: "info",
    project_slug: null,
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

/** Recent round-3 activity, newest first: tools, gates, demos, people and controls. */
export function round3Events(): EventPage {
  seq = 0;
  const data: EventView[] = [
    event(0.2, "message.delivered", "PL · db-version → ic · route-r2 · re-run with knex seed", {
      project_slug: "db-version",
    }),
    event(1, "gate.changed", "!9631 CI caffeine-api test FAIL (same check as r0, r1)", {
      project_slug: "db-version",
      severity: "warning",
      subject: "pr:6d8b4c2a1e9f4a7b8c3d5e6f7a8b9c0d",
    }),
    event(3, "demo.published", "demo PASS of caffeinelabs/app#9630 at 7767f0d", {
      project_slug: "api-version",
      severity: "notice",
    }),
    event(4, "preview.deployed", "preview p9653 at 3c9d8e7", { project_slug: "ux-badge" }),
    event(6, "people.invited", "ops@caffeine.ai joined as viewer", {
      severity: "notice",
      data: { by: "luka@caffeine.ai", user: "ops@caffeine.ai", role: "viewer" },
    }),
    event(9, "control.pause", "Pause cache-warm · keep previews", {
      project_slug: "cache-warm",
      severity: "notice",
      data: { by: "mia@caffeine.ai" },
    }),
    event(11, "decision.opened", "D-2 · Which table is the source of truth?", {
      project_slug: "db-version",
      severity: "notice",
    }),
    event(14, "dispatch", "PL · db-version dispatched route-r2 (attempt 3/3)", {
      project_slug: "db-version",
    }),
    event(18, "pr.ready", "!9630 ready for human merge · 7/7 gates", {
      project_slug: "api-version",
      severity: "notice",
    }),
    event(22, "people.role_changed", "mia@caffeine.ai is now operator", {
      severity: "notice",
      data: { by: "luka@caffeine.ai" },
    }),
    event(26, "access.requested", "ops@caffeine.ai asks for operator access (D-34)", {
      severity: "notice",
      data: { by: "ops@caffeine.ai" },
    }),
    event(31, "control.relaunch", "Relaunch PL · cache-warm with spec mode", {
      project_slug: "cache-warm",
      data: { by: "luka@caffeine.ai" },
    }),
    event(38, "gate.changed", "!9653 review CHANGES · 3 blocking", { project_slug: "ux-badge" }),
    event(47, "message.delivered", "Atlas → PL · ux-badge · rebase on temp-main", {
      project_slug: "ux-badge",
    }),
    event(55, "env.ready", "base-env temp-main@57ab671 = head", { project_slug: "base-env" }),
  ];
  return { data, has_more: false };
}

/** What Luka asked of #9630 from the cockpit in {@link changesRequestedSnapshot}. */
export const CHANGES_REQUESTED_TEXT =
  "Give the version row top spacing; it sits flush against the section header.";

/**
 * *snapshot* as the server serves it a minute after a human asked for changes
 * on PR *number* from the cockpit: every gate still passes at the head, but
 * the head is held not ready and its project is back to building until a new
 * head passes the gates. Mutates and returns *snapshot*.
 */
export function requestChanges(
  snapshot: FactorySnapshot,
  number: number,
  by = "luka@caffeine.ai",
): FactorySnapshot {
  const now = snapshot.server_time;
  const pr = snapshot.prs.find((p) => p.number === number)!;
  const wasReady = pr.ready;
  pr.ready = false;
  pr.human_review = {
    verdict: "changes_requested",
    text: CHANGES_REQUESTED_TEXT,
    by,
    at: now - 60,
    head_sha: pr.head_sha,
    source: "cockpit",
  };
  const project = snapshot.projects.find((p) => p.slug === pr.project_slug)!;
  Object.assign(project, { stage: "building", stage_since: now - 60, agent_stage: "building" });
  if (project.pl) project.pl = { ...project.pl, summary: "human CHANGES · IC r1 fixing" };
  if (wasReady) snapshot.totals = { ...snapshot.totals, prs_ready: snapshot.totals.prs_ready - 1 };
  return snapshot;
}

/** The round-3 factory a minute after Luka asked for changes on #9630 (api-version). */
export function changesRequestedSnapshot(role: ViewerRole = "creator"): FactorySnapshot {
  return requestChanges(round3Snapshot(role), 9630);
}

/** {@link round3Events} with the change request on #9630 on top. */
export function changesRequestedEvents(): EventPage {
  const page = round3Events();
  const extra = { project_slug: "api-version", severity: "notice" as const };
  return {
    ...page,
    data: [
      event(0.9, "stage.changed", "api-version: ready → building", extra),
      event(1, "pr.not_ready", "PR #9630 no longer ready", extra),
      event(1, "pr.human_review", "luka@caffeine.ai requested changes on PR #9630 @7767f0d", {
        ...extra,
        data: { by: "luka@caffeine.ai", verdict: "changes_requested" },
      }),
      ...page.data,
    ],
  };
}

/** Only the audit kinds of {@link round3Events} (people, access, controls). */
export function round3AuditEvents(): EventPage {
  const page = round3Events();
  return {
    ...page,
    data: page.data.filter((e) => /^(people|access|control)\./.test(e.kind)),
  };
}

/** A factory with no projects yet: Atlas planning, nobody else here. */
export function emptyFactorySnapshot(): FactorySnapshot {
  const snapshot = round3Snapshot();
  snapshot.projects = [];
  snapshot.prs = [];
  snapshot.decisions = [];
  snapshot.members = snapshot.members.filter((m) => m.role === "lead");
  snapshot.presence = snapshot.presence?.slice(0, 1) ?? null;
  snapshot.totals = {
    ...snapshot.totals,
    prs_open: 0,
    prs_ready: 0,
    prs_merged: 0,
    decisions_open: 0,
  };
  snapshot.counts = { issues_open: 0, proposals_pending: 0, on_trial: 0 };
  snapshot.factory.base = { ...snapshot.factory.base!, env: null };
  return snapshot;
}

const SCALE_STAGES: ProjectView["stage"][] = [
  "building",
  "review",
  "verify",
  "planning",
  "ready",
  "building",
  "queued",
];

/**
 * The round-3 factory grown to `projects` projects and `agents` agents (the
 * 60-agent scale): copies of db-version and ux-badge with their own PRs.
 */
export function scaleSnapshot(projects = 12, agents = 60): FactorySnapshot {
  const snapshot = round3Snapshot();
  const templates = snapshot.projects.filter(
    (p) => p.slug === "db-version" || p.slug === "ux-badge",
  );
  const memberTemplates = snapshot.members.filter((m) => !m.is_top_level);
  const extraProjects: ProjectView[] = [];
  const extraPrs: PrView[] = [];
  const extraMembers: MemberView[] = [];
  let number = 9700;
  for (let i = snapshot.projects.length; i < projects; i += 1) {
    const template = clone(templates[i % templates.length]!);
    const slug = `${["billing", "search", "auth", "export", "audit", "quota"][i % 6]}-${i}`;
    const pr = clone(snapshot.prs.find((p) => p.id === template.pr_ids[0])!);
    number += 1;
    pr.id = `scale-pr-${i}`;
    pr.number = number;
    pr.project_slug = slug;
    pr.title = `feat(${slug}): step ${i}`;
    pr.ready = false;
    extraPrs.push(pr);
    Object.assign(template, {
      slug,
      title: `${slug.replace(/-\d+$/, "")} ${i}`,
      priority: (i % 3) + 1,
      stage: SCALE_STAGES[i % SCALE_STAGES.length],
      pr_ids: [pr.id],
      open_decision_ids: [],
      human_owner: i % 2 === 0 ? "mia@caffeine.ai" : null,
    });
    extraProjects.push(template);
  }
  const allProjects = [...snapshot.projects, ...extraProjects];
  for (let i = snapshot.members.length; i < agents; i += 1) {
    const member = clone(memberTemplates[i % memberTemplates.length]!);
    const project = allProjects[i % allProjects.length]!;
    member.session_id = `scale-member-${i}`;
    member.title = `ic · ${project.slug}-r${i % 4}`;
    member.project = project.slug;
    member.running = i % 3 === 0;
    extraMembers.push(member);
  }
  snapshot.projects = allProjects;
  snapshot.prs = [...snapshot.prs, ...extraPrs];
  snapshot.members = [...snapshot.members, ...extraMembers];
  return snapshot;
}

/** Story routes the shell reads beyond the snapshot: people, spans, presence. */
export function shellRoutes(
  factoryId: string = LIVE_RUN_FACTORY_ID,
  role: ViewerRole = "creator",
): Record<string, { body: unknown }> {
  const base = `/v1/factory/factories/${factoryId}`;
  return {
    [`${base}/people`]: { body: round3People(role) },
    [`${base}/spans`]: { body: round3Spans() },
    [`${base}/presence`]: { body: { present: 2, heartbeat_s: 20, ttl_s: 60 } },
  };
}

/**
 * The index of someone invited to two shared factories (one as operator,
 * one as viewer) who also runs two of their own: five rows, the dense table.
 */
export function sharedFactoryList(): FactoryListResponse {
  const atlas = {
    ...summaryOf(round3Snapshot()),
    shared_by: "luka@caffeine.ai",
    viewer_role: "operator" as const,
    people_count: 3,
    mine: 2,
    usd_per_h: 6.1,
  };
  const billing = {
    ...summaryOf(tenProjectsSnapshot()),
    id: "b1c2d3e4f5a60718293a4b5c6d7e8f90",
    name: "Billing factory",
    shared_by: "ana@caffeine.ai",
    viewer_role: "viewer" as const,
    people_count: 5,
    mine: 0,
    usd_per_h: 11.4,
  };
  const own = {
    ...summaryOf(round3Snapshot()),
    id: "c2d3e4f5a6b70819203a4b5c6d7e8f91",
    name: "Docs sweep with a much longer factory name that has to truncate",
    viewer_role: "creator" as const,
    shared_by: null,
    people_count: 1,
    mine: 1,
    usd_per_h: 0.8,
  };
  const done = {
    ...summaryOf(allDoneSnapshot()),
    viewer_role: "creator" as const,
    shared_by: null,
    people_count: 2,
    mine: 0,
    usd_per_h: null,
  };
  return {
    data: [
      atlas,
      billing,
      own,
      done,
      { ...done, id: "d3e4f5a6b7c8091a2b3c4d5e6f7a8b92", name: "Old spike" },
    ],
  };
}
