// Typed fixtures for the factory cockpit, built from the 2026-10-26 live run
// (`snapshot.liveRun.json`, which the server's contract test validates
// against `schemas.py`). Stories and tests across the cockpit share these, so
// every export is a fresh deep copy a caller may mutate.

import type { Session } from "@/lib/types";
import {
  FACTORY_ID_LABEL_KEY,
  FACTORY_NAME_LABEL_KEY,
  FACTORY_PARENT_LABEL_KEY,
  FACTORY_PROJECT_LABEL_KEY,
  FACTORY_ROLE_LABEL_KEY,
  FACTORY_SUMMARY_LABEL_KEY,
} from "@/lib/factory";
import {
  FACTORY_STAGES,
  FactoryApiError,
  type EventPage,
  type FactoryListResponse,
  type FactorySnapshot,
  type FactoryStage,
  type FactorySummary,
  type OpenDecisionsResponse,
  type Preflight,
  type ProjectView,
} from "@/lib/factoryApi";
import liveRunJson from "./snapshot.liveRun.json";

const clone = <T>(value: T): T => structuredClone(value);

const LIVE_RUN = liveRunJson as unknown as FactorySnapshot;

export const LIVE_RUN_FACTORY_ID = LIVE_RUN.factory.id;
/** The fixture's "now" (epoch seconds): pin clocks here for stable relative times. */
export const LIVE_RUN_NOW_S = LIVE_RUN.server_time;
export const LIVE_RUN_ATLAS_ID = LIVE_RUN.factory.root_session_id;
export const LIVE_RUN_PL_API_ID = "b408c5f09ff659e6b907fd9f688829e9";
export const LIVE_RUN_PL_DB_ID = "a2fc64b9e92e54c8b2f0f78038df202d";
export const LIVE_RUN_IC_API_ID = "9c5729417ac856a58105fd08ab3637f7";

/** The live-run snapshot: Atlas, Sentinel, 3 PLs, #9630 ready, #9631 building, D-2/D-3 open. */
export function liveRunSnapshot(): FactorySnapshot {
  return clone(LIVE_RUN);
}

/** The index card a snapshot rolls up into (what `GET /v1/factory/factories` lists). */
export function summaryOf(snapshot: FactorySnapshot): FactorySummary {
  const stageCounts: Partial<Record<FactoryStage, number>> = {};
  for (const stage of FACTORY_STAGES) stageCounts[stage] = 0;
  for (const project of snapshot.projects) {
    stageCounts[project.stage] = (stageCounts[project.stage] ?? 0) + 1;
  }
  const { factory } = snapshot;
  return {
    id: factory.id,
    name: factory.name,
    state: factory.state,
    cadence: factory.cadence,
    started_at: factory.started_at,
    root_session_id: factory.root_session_id,
    elapsed_s: factory.elapsed_s,
    cost_usd: factory.cost_usd,
    stage_counts: stageCounts,
    projects_total: snapshot.projects.length + snapshot.hidden_projects,
    decisions_open: snapshot.totals.decisions_open,
    prs_ready: snapshot.totals.prs_ready,
    members_total: snapshot.members.length,
    members_running: snapshot.members.filter((m) => m.running).length,
    drifted_members: snapshot.health.drifted_members,
    url: factory.url,
  };
}

/** A factory whose projects all finished: PLs done, PRs merged, nothing open. */
export function allDoneSnapshot(): FactorySnapshot {
  const snapshot = liveRunSnapshot();
  const doneAt = LIVE_RUN_NOW_S - 3 * 3600;
  snapshot.factory = {
    ...snapshot.factory,
    id: "0d6e1c2b3a4f5e6d7c8b9a0f1e2d3c4b",
    root_session_id: "0d6e1c2b3a4f5e6d7c8b9a0f1e2d3c4b",
    name: "Docs factory",
    state: "done",
    cadence: "normal",
    started_at: LIVE_RUN_NOW_S - 2 * 86_400,
    elapsed_s: 2 * 86_400,
    cost_usd: 9.4,
    next_beats: [],
    url: "/factory/0d6e1c2b3a4f5e6d7c8b9a0f1e2d3c4b",
  };
  snapshot.projects = snapshot.projects.map((project) => ({
    ...project,
    stage: "done",
    stage_since: doneAt,
    overlay: null,
    overlay_reason: null,
    claim_mismatch: false,
    open_decision_ids: [],
  }));
  snapshot.prs = snapshot.prs.map((pr) => ({
    ...pr,
    state: "merged",
    ready: false,
    merged: pr.merged ?? { by: "luka", sha: pr.head_sha, at: doneAt },
  }));
  snapshot.decisions = snapshot.decisions.filter((d) => d.status !== "open");
  snapshot.members = snapshot.members.map((m) => ({
    ...m,
    running: false,
    status: "idle",
    state: m.is_top_level ? "done" : m.state,
    health: "ok",
    launch_drift: null,
  }));
  snapshot.health.drifted_members = 0;
  snapshot.totals = { ...snapshot.totals, prs_open: 0, prs_ready: 0, decisions_open: 0 };
  snapshot.totals.prs_merged = snapshot.prs.length;
  return snapshot;
}

/** The live run with GitHub disconnected: gates unknown, a Connect prompt per PR. */
export function githubDisconnectedSnapshot(): FactorySnapshot {
  const snapshot = liveRunSnapshot();
  snapshot.health.github = {
    state: "disconnected",
    last_poll_at: LIVE_RUN_NOW_S - 1800,
    error: "GitHub token expired",
    rate_limit_remaining: null,
  };
  snapshot.prs = snapshot.prs.map((pr) =>
    pr.state === "open" ? { ...pr, poll_error: "GitHub: token expired · Connect" } : pr,
  );
  return snapshot;
}

const EXTRA_STAGES: FactoryStage[] = [
  "queued",
  "planning",
  "building",
  "review",
  "verify",
  "ready",
  "merged",
];

/** Ten projects (perf and dense layouts): the live run plus seven generated ones. */
export function tenProjectsSnapshot(): FactorySnapshot {
  const snapshot = liveRunSnapshot();
  snapshot.factory = {
    ...snapshot.factory,
    id: "7e3f0a9b1c2d4e5f8a6b7c8d9e0f1a2b",
    root_session_id: "7e3f0a9b1c2d4e5f8a6b7c8d9e0f1a2b",
    name: "Perf factory",
    url: "/factory/7e3f0a9b1c2d4e5f8a6b7c8d9e0f1a2b",
  };
  const template = snapshot.projects[0] as ProjectView;
  for (let i = 0; snapshot.projects.length < 10; i++) {
    const stage = EXTRA_STAGES[i % EXTRA_STAGES.length] as FactoryStage;
    const slug = `perf-${stage}-${i + 1}`;
    snapshot.projects.push({
      ...clone(template),
      slug,
      title: `Perf project ${i + 1} (${stage})`,
      priority: i % 4,
      stage,
      stage_since: LIVE_RUN_NOW_S - (i + 1) * 600,
      overlay: i === 3 ? "paused" : null,
      overlay_reason: i === 3 ? "Paused by luka" : null,
      agent_stage: null,
      claim_mismatch: false,
      pl: null,
      team: [],
      env: null,
      pr_ids: [],
      open_decision_ids: [],
      cost_usd: 0.4 * (i + 1),
      elapsed_s: (i + 1) * 900,
      provisioning: stage === "queued" ? { stage: "cloning", label: "Cloning repository" } : null,
      paused_by: i === 3 ? "luka" : null,
    });
  }
  return snapshot;
}

export function liveRunSummary(): FactorySummary {
  return summaryOf(liveRunSnapshot());
}

/** `GET /v1/factory/factories` with the live run and a finished factory. */
export function factoryList(): FactoryListResponse {
  return { data: [liveRunSummary(), summaryOf(allDoneSnapshot())] };
}

/** The empty index: no factory has been started yet. */
export function emptyFactoryList(): FactoryListResponse {
  return { data: [] };
}

/** The index with ten-project factory (perf). */
export function tenProjectsFactoryList(): FactoryListResponse {
  return { data: [summaryOf(tenProjectsSnapshot()), liveRunSummary()] };
}

/** Everything a factory needs is in place. */
export function preflightOk(): Preflight {
  return {
    github: { connected: true, login: "luka" },
    agents: { factory_lead: true, factory_pl: true },
    sandbox_host: true,
    factory_enabled: true,
  };
}

/** GitHub not connected (the other checks pass). */
export function preflightGithubDisconnected(): Preflight {
  return { ...preflightOk(), github: { connected: false, login: null } };
}

/** Nothing is set up: every preflight row fails. */
export function preflightAllFailing(): Preflight {
  return {
    github: { connected: false, login: null },
    agents: { factory_lead: false, factory_pl: true },
    sandbox_host: false,
    factory_enabled: false,
  };
}

/** A failed read, as the API client raises it. */
export function factoryReadError(status = 503): FactoryApiError {
  return new FactoryApiError(
    status === 503 ? "Service unavailable" : `HTTP ${status}`,
    status,
    null,
    null,
    null,
  );
}

/** `GET /v1/factory/decisions?status=open` for the live run. */
export function liveRunOpenDecisions(): OpenDecisionsResponse {
  const snapshot = liveRunSnapshot();
  const open = snapshot.decisions.filter((d) => d.status === "open");
  return {
    data: open.map((decision) => ({
      factory: { id: snapshot.factory.id, name: snapshot.factory.name },
      decision,
    })),
    count: open.length,
  };
}

/** A short activity page for the live run (newest first). */
export function liveRunEvents(): EventPage {
  const t = LIVE_RUN_NOW_S * 1000;
  const base = { severity: "info" as const, subject: null, data: null };
  return {
    data: [
      {
        ...base,
        id: "ev5",
        ts: t - 60_000,
        kind: "decision.opened",
        severity: "warning",
        project_slug: "db-version",
        from_id: LIVE_RUN_PL_DB_ID,
        to_id: null,
        from_title: "PL · db-version",
        to_title: null,
        subject: "decision:D-2",
        summary: "D-2 · Which table is the source of truth for the DB version?",
      },
      {
        ...base,
        id: "ev4",
        ts: t - 760_000,
        kind: "pr.ready",
        severity: "notice",
        project_slug: "api-version",
        from_id: null,
        to_id: LIVE_RUN_PL_API_ID,
        from_title: "GitHub",
        to_title: "PL · api-version",
        subject: "pr:caffeinelabs/app#9630",
        summary: "#9630 ready for human merge · 6/6 gates",
      },
      {
        ...base,
        id: "ev3",
        ts: t - 840_000,
        kind: "demo.published",
        severity: "notice",
        project_slug: "api-version",
        from_id: "2a45dc3fefe355e295e9c88c6fcc8c1b",
        to_id: null,
        from_title: "demo-version-endpoint",
        to_title: null,
        subject: "pr:caffeinelabs/app#9630",
        summary:
          "demo PASS of caffeinelabs/app#9630 at 7767f0d: GET /version and the Settings API version row",
        data: {
          demo_id: "df2503ab72c051c6bdfa1e15f1570e31",
          pr_id: "f74d5967c53455139e85ba1b402a5bd4",
          status: "PASS",
        },
      },
      {
        ...base,
        id: "ev2",
        ts: t - 1_140_000,
        kind: "preview.reported",
        project_slug: "api-version",
        from_id: "827319c3ac5b55d3a0998a2c6ac6cdcd",
        to_id: null,
        from_title: "env_keeper",
        to_title: null,
        summary: "Preview p9630 ready at 7767f0d",
      },
      {
        ...base,
        id: "ev1",
        ts: t - 1_200_000,
        kind: "dispatch",
        project_slug: "api-version",
        from_id: LIVE_RUN_PL_API_ID,
        to_id: "827319c3ac5b55d3a0998a2c6ac6cdcd",
        from_title: "PL · api-version",
        to_title: "env_keeper",
        summary: "PREVIEW pr=9630 head=7767f0d",
      },
      {
        // The demo of #9630's previous head, before 7767f0d was pushed.
        ...base,
        id: "ev0",
        ts: t - 1_500_000,
        kind: "demo.published",
        severity: "notice",
        project_slug: "api-version",
        from_id: "2a45dc3fefe355e295e9c88c6fcc8c1b",
        to_id: null,
        from_title: "demo-version-endpoint",
        to_title: null,
        subject: "pr:caffeinelabs/app#9630",
        summary: "demo PASS of caffeinelabs/app#9630 at bfbef12: GET /version returns the commit",
        data: {
          demo_id: "8b0e4f6a2c9d41e7b3a5c6d7e8f90a1b",
          pr_id: "f74d5967c53455139e85ba1b402a5bd4",
          status: "PASS",
        },
      },
    ],
    has_more: false,
  };
}

function session(id: string, title: string, overrides: Partial<Session> = {}): Session {
  return {
    id,
    agentId: "ag_factory",
    agentName: "factory-pl",
    runnerId: null,
    status: "idle",
    createdAt: LIVE_RUN.factory.started_at ?? 0,
    title,
    labels: {},
    items: [],
    pendingElicitations: [],
    permissionLevel: 4,
    parentSessionId: null,
    subAgentName: null,
    kind: "default",
    ...overrides,
  };
}

/** Atlas, the factory root (a lead). */
export function atlasSession(): Session {
  return session(LIVE_RUN_ATLAS_ID, "Atlas", {
    agentName: "factory-lead",
    labels: {
      [FACTORY_ID_LABEL_KEY]: LIVE_RUN_ATLAS_ID,
      [FACTORY_ROLE_LABEL_KEY]: "lead",
      [FACTORY_SUMMARY_LABEL_KEY]: "2 projects in flight · #9630 ready",
      [FACTORY_NAME_LABEL_KEY]: "Atlas factory",
    },
  });
}

/** The api-version PL (a top-level member in its own sandbox). */
export function plSession(): Session {
  return session(LIVE_RUN_PL_API_ID, "PL · api-version", {
    labels: {
      [FACTORY_ID_LABEL_KEY]: LIVE_RUN_ATLAS_ID,
      [FACTORY_ROLE_LABEL_KEY]: "project_lead",
      [FACTORY_PROJECT_LABEL_KEY]: "api-version",
      [FACTORY_PARENT_LABEL_KEY]: LIVE_RUN_ATLAS_ID,
    },
  });
}

/** The api-version PL's Codex IC: a sub-agent without factory labels of its own. */
export function icSession(): Session {
  return session(LIVE_RUN_IC_API_ID, "ic_codex:version-endpoint", {
    agentName: "ic_codex",
    parentSessionId: LIVE_RUN_PL_API_ID,
    subAgentName: "ic_codex",
  });
}

/** A read that never settles, for loading states (`queryFn: pendingForever`). */
export function pendingForever<T>(): Promise<T> {
  return new Promise<T>(() => {
    // Never settles.
  });
}
