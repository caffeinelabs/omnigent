// Fixtures for the learning pages (Insights, Improvements, Settings and the
// feedback widgets), built on the round-3 factory (`snapshot.round3.json`:
// Atlas factory, 7 projects, 41 agents, Luka, Mia and Ops, C-14 on trial,
// C-15 pending, F-7/F-9 findings). The definition's bounds, classes and
// defaults come from the server (`definitionMeta.json`, generated from
// `omnigent/server/factory/definition.py`). Every export is a fresh copy.

import type {
  BundlesResponse,
  DefinitionDiffResponse,
  DefinitionDoc,
  DefinitionResponse,
  DefinitionView,
  DefinitionVersionsResponse,
  InsightsResponse,
  IssueView,
  MetricAggregate,
  MetricResult,
  MetricsResponse,
  ProposalView,
  ScorecardView,
  VersionsResponse,
} from "@/lib/factoryApi";
import round3Json from "../../__fixtures__/snapshot.round3.json";
import type { StoryResponse, StoryRoutes } from "../../factoryStoryFixtures";
import { ROUND3_NOW_S, round3Snapshot } from "../../shell/__fixtures__/shellFixtures";
import { KPI_SPECS } from "../insightsModel";
import meta from "./definitionMeta.json";

const clone = <T>(value: T): T => structuredClone(value);
const H = 3600;
const D = 86_400;
const NOW = ROUND3_NOW_S;

interface Round3 {
  proposals: ProposalView[];
  issues: IssueView[];
  definition: DefinitionView;
}
const ROUND3 = round3Json as unknown as Round3;

export const LEARN_NOW_S = NOW;

// ── Metrics ────────────────────────────────────────────────────

const CATALOG: Record<string, { family: MetricResult["family"]; title: string; unit: string }> = {
  lead_time: { family: "flow", title: "Lead time", unit: "s" },
  time_to_first_pr: { family: "flow", title: "Time to first PR", unit: "s" },
  task_cycle_time: { family: "flow", title: "Task cycle time", unit: "s" },
  stage_dwell: { family: "flow", title: "Stage dwell", unit: "s" },
  human_wait: { family: "flow", title: "Agent time vs human wait", unit: "s" },
  throughput: { family: "flow", title: "Throughput / WIP", unit: "per_day" },
  parallelism: { family: "flow", title: "Parallelism", unit: "tasks" },
  critical_path_accuracy: { family: "flow", title: "Critical-path accuracy", unit: "ratio" },
  loop_iterations: { family: "loops", title: "Iterations to converge", unit: "iterations" },
  loop_exit_mix: { family: "loops", title: "Exit-reason mix", unit: "share" },
  loop_exhaustion_rate: { family: "loops", title: "Loop exhaustion rate", unit: "share" },
  first_pass_yield: { family: "loops", title: "First-pass yield", unit: "share" },
  review_rounds: { family: "loops", title: "Review rounds", unit: "rounds" },
  budget_extensions: { family: "loops", title: "Budget extensions", unit: "count" },
  merge_acceptance: { family: "quality", title: "Merge acceptance", unit: "share" },
  escape_rate: { family: "quality", title: "Escape rate", unit: "share" },
  sentinel_agreement: { family: "quality", title: "Sentinel agreement", unit: "share" },
  claim_mismatch_rate: { family: "quality", title: "Claim calibration", unit: "share" },
  human_ratings: { family: "quality", title: "Human ratings", unit: "rating" },
  human_prompts_per_day: { family: "human", title: "Human prompts per day", unit: "per_day" },
  interventions_per_merged_pr: {
    family: "human",
    title: "Interventions per merged PR",
    unit: "acts",
  },
  decision_latency: { family: "human", title: "Decision latency", unit: "s" },
  unnecessary_questions: { family: "human", title: "Unnecessary questions", unit: "share" },
  chat_reply_time: { family: "human", title: "Chat time to reply", unit: "s" },
  proposal_health: { family: "human", title: "Proposal health", unit: "share" },
  cost_usd: { family: "cost", title: "$ spent", unit: "usd" },
  cost_per_merged_pr: { family: "cost", title: "$ per merged PR", unit: "usd" },
  heartbeat_share: { family: "cost", title: "Heartbeat and idle share", unit: "share" },
  wasted_usd: { family: "cost", title: "$ wasted", unit: "usd" },
  incidents: { family: "reliability", title: "Incidents and MTTR", unit: "count" },
  subagent_failure_rate: { family: "reliability", title: "Sub-agent failure rate", unit: "share" },
  mail_health: { family: "reliability", title: "Mail health", unit: "share" },
  env_availability: { family: "reliability", title: "Env and preview availability", unit: "share" },
  engine_wake_efficiency: { family: "reliability", title: "Engine wake efficiency", unit: "share" },
};

function metric(name: string, body: MetricAggregate): MetricResult {
  const entry = CATALOG[name]!;
  return {
    name,
    family: entry.family,
    title: entry.title,
    unit: entry.unit,
    definition: `${entry.title} (fixture definition)`,
    ...body,
  };
}

/** Every catalog metric over the default 7-day window of the round-3 factory. */
function populatedAggregates(): Record<string, MetricAggregate> {
  return {
    lead_time: { p50: 11_400, p90: 32_400, n: 14 },
    time_to_first_pr: { p50: 2_700, p90: 7_800, n: 7 },
    task_cycle_time: { p50: 5_400, p90: 14_400, n: 19 },
    stage_dwell: {
      by: {
        planning: { p50: 1_800, p90: 5_400, n: 7 },
        building: { p50: 7_200, p90: 18_000, n: 9 },
        review: { p50: 2_400, p90: 6_000, n: 9 },
        verify: { p50: 1_500, p90: 3_600, n: 8 },
        ready: { p50: 9_600, p90: 21_600, n: 6 },
      },
      n: 39,
    },
    human_wait: {
      by: {
        agent: { value: 61_200, n: 11 },
        human_merge: { value: 9_600, n: 11 },
        decision: { value: 2_460, n: 7 },
      },
      n: 29,
    },
    throughput: {
      value: 1.57,
      total: 11,
      n: 11,
      open_prs: 4,
      live_previews: 3,
      projects_by_stage: { building: 3, review: 1, ready: 1, queued: 1 },
    },
    parallelism: { peak: 6, mean: 2.4, n: 19 },
    critical_path_accuracy: { p50: 1.35, p90: 1.9, n: 3 },
    loop_iterations: {
      by: {
        implement: { p50: 2, p90: 3, n: 12 },
        review: { p50: 1, p90: 2, n: 10 },
        verify: { p50: 1, p90: 2, n: 9 },
        demo: { p50: 1, p90: 2, n: 8 },
        ci: { p50: 1, p90: 2, n: 11 },
      },
      n: 50,
    },
    loop_exit_mix: {
      mix: {
        converged: { count: 41, share: 0.82 },
        exhausted: { count: 4, share: 0.08 },
        stalled: { count: 2, share: 0.04 },
        thrashing: { count: 1, share: 0.02 },
        cancelled: { count: 2, share: 0.04 },
      },
      n: 50,
    },
    loop_exhaustion_rate: { value: 0.08, n: 50 },
    first_pass_yield: {
      by: {
        ci: { value: 0.72, n: 25 },
        review: { value: 0.58, n: 19 },
        verify: { value: 0.81, n: 16 },
        demo: { value: 0.77, n: 13 },
      },
      n: 73,
    },
    review_rounds: { p50: 1, p90: 3, n: 11 },
    budget_extensions: { by: { implement: { value: 3, n: 3 }, review: { value: 1, n: 1 } }, n: 4 },
    merge_acceptance: {
      value: 0.79,
      n: 14,
      closure_reasons: { wrong_scope: 2, test_run: 1 },
    },
    escape_rate: { value: 0.14, n: 14 },
    sentinel_agreement: { value: 0.86, n: 7 },
    claim_mismatch_rate: { value: 0.12, n: 17 },
    human_ratings: {
      value: 4.1,
      n: 12,
      distribution: { "2": 1, "3": 2, "4": 5, "5": 4 },
      demo_useful: 0.83,
    },
    human_prompts_per_day: {
      value: 34,
      total: 238,
      n: 238,
      split: { lead: 0.58, project_lead: 0.36, other: 0.06 },
      band: [30, 50],
      in_band: true,
    },
    interventions_per_merged_pr: { value: 2.36, n: 26, acts: 26, merged: 11 },
    decision_latency: { p50: 2_460, p90: 7_800, n: 18, escalation_rate: 0.11, opened: 19 },
    unnecessary_questions: { value: 0.28, n: 18 },
    chat_reply_time: { p50: 95, p90: 420, n: 31, auto_reply_share: 0.16 },
    proposal_health: {
      value: 0.78,
      n: 9,
      acceptance: 0.78,
      edited: 1,
      undo_rate: 0.1,
      trials_kept: 9,
      trials_reverted: 1,
      on_trial: 1,
    },
    cost_usd: {
      value: 312.4,
      n: 640,
      by_model: { sonnet: 241.3, opus: 52.8, "gpt-5-codex": 18.3 },
    },
    cost_per_merged_pr: { value: 8.2, n: 11 },
    heartbeat_share: { value: 0.19, turns: 212, usd_estimate: 14.6, n: 212 },
    wasted_usd: { value: 23.7, n: 4 },
    incidents: { value: 6, n: 6, mttr_p50_s: 1_320, resolved: 4 },
    subagent_failure_rate: { value: 0.06, n: 64 },
    mail_health: { bounce_rate: 0.01, queue_age_p90_s: 42, n: 388 },
    env_availability: {
      n: 22,
      env_ready_share: 0.93,
      preview_ready_share: 0.88,
      preview_deploy_success: 0.9,
      sha_mismatches: 1,
    },
    engine_wake_efficiency: { value: 0.71, n: 212 },
  };
}

const PREVIOUS: Record<string, MetricAggregate> = {
  merge_acceptance: { value: 0.73, n: 11 },
  lead_time: { p50: 13_900, p90: 36_000, n: 12 },
  human_wait: {
    by: {
      agent: { value: 70_000, n: 9 },
      human_merge: { value: 12_800, n: 9 },
      decision: { value: 3_000, n: 6 },
    },
    n: 24,
  },
  human_prompts_per_day: { value: 41, total: 287, n: 287 },
  cost_per_merged_pr: { value: 7.1, n: 9 },
  incidents: { value: 8, n: 8 },
  engine_wake_efficiency: { value: 0.55, n: 180 },
};

const DELTA: Record<string, number | null> = {
  merge_acceptance: 0.06,
  lead_time: -2_500,
  human_wait: null,
  human_prompts_per_day: -7,
  cost_per_merged_pr: 1.1,
  incidents: -2,
  engine_wake_efficiency: 0.16,
};

/** A window's metrics; `kind` picks populated, empty (n=0) or small n. */
export type MetricsKind = "populated" | "empty" | "small";

function emptyAggregate(name: string): MetricAggregate {
  const unit = CATALOG[name]!.unit;
  if (unit === "s" && !["human_wait", "stage_dwell"].includes(name)) {
    return { p50: null, p90: null, n: 0 };
  }
  if (["human_wait", "stage_dwell", "loop_iterations", "first_pass_yield"].includes(name)) {
    return { by: {}, n: 0 };
  }
  if (name === "loop_exit_mix") return { mix: {}, n: 0 };
  if (name === "human_prompts_per_day") {
    return { value: 0, total: 0, n: 0, split: {}, band: [30, 50], in_band: null };
  }
  return { value: null, n: 0 };
}

function smallAggregate(name: string, full: MetricAggregate): MetricAggregate {
  const n = name === "merge_acceptance" ? 2 : name === "lead_time" ? 1 : Math.min(full.n, 2);
  return { ...full, n };
}

export function allMetrics(kind: MetricsKind = "populated"): Record<string, MetricResult> {
  const full = populatedAggregates();
  return Object.fromEntries(
    Object.keys(CATALOG).map((name) => {
      const body =
        kind === "empty"
          ? emptyAggregate(name)
          : kind === "small"
            ? smallAggregate(name, full[name]!)
            : full[name]!;
      return [name, metric(name, clone(body))];
    }),
  );
}

/** The 7 UTC days of the default window, oldest first. */
export function fixtureDays(): string[] {
  const out: string[] = [];
  const start = new Date((NOW - 7 * D + 1) * 1000);
  start.setUTCHours(0, 0, 0, 0);
  for (let t = start.getTime(); t < (NOW + 1) * 1000; t += D * 1000) {
    out.push(new Date(t).toISOString().slice(0, 10));
  }
  return out;
}

const DAY_SHAPES: Record<string, (i: number) => MetricAggregate | null> = {
  merge_acceptance: (i) =>
    i < 1 ? null : { value: [0.5, 0.67, 0.75, 0.8, 0.83, 0.86, 0.8][i]!, n: 2 },
  lead_time: (i) => (i < 1 ? null : { p50: [16, 14, 12, 11, 10, 9.5, 9][i]! * 1200, n: 2 }),
  human_wait: (i) => ({
    by: { human_merge: { value: [6, 5, 4, 4.5, 3, 2.5, 2.7][i]! * 3600, n: 2 } },
    n: 2,
  }),
  human_prompts_per_day: (i) => ({ value: [52, 47, 41, 38, 35, 31, 34][i]!, total: 0, n: 30 }),
  cost_per_merged_pr: (i) =>
    i < 1 ? null : { value: [6.1, 6.8, 7.4, 7.9, 8.4, 8.2, 8.3][i]!, n: 2 },
  incidents: (i) => ({ value: [2, 1, 1, 0, 1, 0, 1][i]!, n: 1 }),
  engine_wake_efficiency: (i) => ({ value: [0.4, 0.52, 0.61, 0.66, 0.7, 0.74, 0.76][i]!, n: 30 }),
  cost_usd: (i) => ({ value: [31, 42, 48, 39, 51, 47, 54][i]!, n: 90 }),
  throughput: (i) => ({ value: 0, total: [0, 1, 2, 1, 3, 2, 2][i]!, n: 2 }),
};

export function metricsByDay(kind: MetricsKind = "populated"): Record<string, MetricResult> {
  const days = fixtureDays();
  const names = [...KPI_SPECS.map((k) => k.name), "cost_usd", "throughput"];
  return Object.fromEntries(
    names.map((name) => {
      const groups: Record<string, MetricAggregate> = {};
      if (kind !== "empty") {
        days.forEach((day, i) => {
          const found = DAY_SHAPES[name]?.(i);
          if (found && (kind === "populated" || i >= days.length - 2)) groups[day] = found;
        });
      }
      return [name, metric(name, { n: Object.keys(groups).length, groups })];
    }),
  );
}

/** Where the time went, per project (`scale` adds a dozen more projects). */
export function waitByProject(kind: MetricsKind = "populated", scale = false): MetricResult {
  const groups: Record<string, MetricAggregate> =
    kind === "empty"
      ? {}
      : {
          "api-version": {
            by: {
              agent: { value: 18_000, n: 3 },
              human_merge: { value: 9_600, n: 3 },
              decision: { value: 1_200, n: 1 },
            },
            n: 7,
          },
          "db-version": {
            by: { agent: { value: 26_400, n: 3 }, decision: { value: 2_460, n: 2 } },
            n: 5,
          },
          "docs-factory-envs": {
            by: { agent: { value: 7_200, n: 2 }, human_merge: { value: 3_600, n: 2 } },
            n: 4,
          },
          "ux-badge": { by: { agent: { value: 9_600, n: 2 } }, n: 2 },
        };
  if (scale) {
    for (let i = 1; i <= 12; i++) {
      groups[`billing-part-${i}`] = {
        by: {
          agent: { value: 3_000 + i * 1_700, n: 2 },
          decision: { value: (i % 3) * 900, n: 1 },
          human_merge: { value: (i % 4) * 2_100, n: 1 },
        },
        n: 4,
      };
    }
  }
  return metric("human_wait", { n: Object.keys(groups).length, groups });
}

export function costByRole(kind: MetricsKind = "populated"): MetricResult {
  const groups: Record<string, MetricAggregate> =
    kind === "empty"
      ? {}
      : {
          ic: { value: 168.2, n: 410 },
          project_lead: { value: 61.7, n: 96 },
          lead: { value: 38.9, n: 61 },
          reviewer: { value: 18.3, n: 22 },
          env_keeper: { value: 14.1, n: 31 },
          demo: { value: 8.9, n: 14 },
          planner: { value: 2.3, n: 6 },
        };
  return metric("cost_usd", { n: 640, groups });
}

/** The KPIs grouped by project (the group-by table). */
export function kpisByProject(): Record<string, MetricResult> {
  const projects = ["api-version", "db-version", "docs-factory-envs", "ux-badge", "cache-warm"];
  return Object.fromEntries(
    KPI_SPECS.map((spec, k) => {
      const groups: Record<string, MetricAggregate> = {};
      projects.forEach((slug, i) => {
        const n = Math.max(0, 5 - i);
        if (spec.name === "merge_acceptance") groups[slug] = { value: [1, 0.75, 1, 0.5, 0][i]!, n };
        else if (spec.name === "lead_time")
          groups[slug] = { p50: (2 + i) * 3_600, p90: (5 + i) * 3_600, n };
        else if (spec.name === "human_wait")
          groups[slug] = { by: { human_merge: { value: (i + 1) * 1_800, n } }, n };
        else groups[slug] = { value: [3.1, 8.2, 2.2, 5.5, 11][i]! * (k === 4 ? 1 : 0.1), n };
      });
      return [spec.name, metric(spec.name, { n: 14, groups })];
    }),
  );
}

function issue(ref: string): IssueView {
  return clone(ROUND3.issues.find((i) => i.ref === ref)!);
}

function retros(): IssueView[] {
  return clone(ROUND3.issues.filter((i) => i.class === "retro"));
}

export function insightsResponse(
  kind: MetricsKind = "populated",
  options: { owner?: boolean } = {},
): InsightsResponse {
  const all = allMetrics(kind);
  const kpis = Object.fromEntries(
    KPI_SPECS.map((spec) => {
      const m = clone(all[spec.name]!);
      if (kind === "populated") {
        m.previous = clone(PREVIOUS[spec.name]!);
        m.delta = DELTA[spec.name] ?? null;
      }
      return [spec.name, m];
    }),
  );
  const empty = kind === "empty";
  const body: InsightsResponse = {
    window: { since: NOW - 7 * D + 1, until: NOW + 1, label: "7d" },
    def_version: 7,
    kpis,
    findings: empty ? [] : [issue("F-7"), issue("F-9")],
    findings_open: empty ? 0 : 2,
    incidents_open: empty ? 0 : 2,
    incidents: empty ? [] : [issue("INC-13"), issue("INC-12")],
    retros: empty ? [] : retros(),
    experiments: empty
      ? { on_trial: 0, kept: 0, reverted: 0 }
      : { on_trial: 1, kept: 9, reverted: 1 },
    catalog: Object.entries(CATALOG).map(([name, c]) => ({
      name,
      family: c.family,
      title: c.title,
      unit: c.unit,
      definition: `${c.title} (fixture definition)`,
    })),
  };
  if (options.owner ?? true) {
    body.people = empty
      ? {}
      : {
          "luka@caffeine.ai": {
            decisions_answered: 11,
            decision_p50_s: 1_980,
            chat_posts: 24,
            interventions: 9,
            escalated_away: 0,
          },
          "mia@caffeine.ai": {
            decisions_answered: 6,
            decision_p50_s: 2_700,
            chat_posts: 17,
            interventions: 5,
            escalated_away: 1,
          },
          "ops@caffeine.ai": {
            decisions_answered: 0,
            decision_p50_s: null,
            chat_posts: 2,
            interventions: 0,
            escalated_away: 0,
          },
        };
  }
  return body;
}

function metricsPage(
  metrics: Record<string, MetricResult>,
  groupBy: string | null,
): MetricsResponse {
  return {
    window: { since: NOW - 7 * D + 1, until: NOW + 1, label: "7d" },
    group_by: groupBy as MetricsResponse["group_by"],
    metrics,
  };
}

// ── Versions and bundles ───────────────────────────────────────

export function versionsResponse(): VersionsResponse {
  return {
    definition: [
      {
        version: 1,
        created_at: NOW - 10 * D,
        created_via: "seed",
        created_by: "server",
        proposal_ref: null,
        pinned: false,
      },
      {
        version: 2,
        created_at: NOW - 8 * D,
        created_via: "human",
        created_by: "luka@caffeine.ai",
        proposal_ref: "C-1",
        pinned: false,
      },
      {
        version: 3,
        created_at: NOW - 6 * D,
        created_via: "proposal",
        created_by: "Lead B · Sentinel",
        proposal_ref: "C-9",
        pinned: false,
      },
      {
        version: 4,
        created_at: NOW - 5 * D,
        created_via: "proposal",
        created_by: "Lead B · Sentinel",
        proposal_ref: "C-12",
        pinned: false,
      },
      {
        version: 5,
        created_at: NOW - 4 * D,
        created_via: "revert",
        created_by: "server",
        proposal_ref: "C-12",
        pinned: false,
      },
      {
        version: 6,
        created_at: NOW - 3 * D,
        created_via: "proposal",
        created_by: "luka@caffeine.ai",
        proposal_ref: "C-11",
        pinned: false,
      },
      {
        version: 7,
        created_at: NOW - 31 * H,
        created_via: "proposal",
        created_by: "Lead B · Sentinel",
        proposal_ref: "C-14",
        pinned: true,
      },
    ],
    platform: [
      {
        id: "ver-e15e04c",
        created_at: NOW - 9 * D,
        image_tag: "staging-e15e04c",
        server_sha: "e15e04ca1",
        current: false,
      },
      {
        id: "ver-0bb15c1",
        created_at: NOW - 2 * D,
        image_tag: "staging-0bb15c1",
        server_sha: "0bb15c1aa",
        current: true,
      },
    ],
  };
}

export function bundlesResponse(drift = true): BundlesResponse {
  const members = round3Snapshot().members;
  const id = (title: string) => members.find((m) => m.title === title)?.session_id ?? title;
  const rows = [
    {
      session_id: id("PL · db-version"),
      title: "PL · db-version",
      role: "project_lead",
      project: "db-version",
      running: "factory-pl/1f2e3d4c5b6a",
      current: "factory-pl/a3f2b19c07de",
      drift,
    },
    {
      session_id: id("PL · api-version"),
      title: "PL · api-version",
      role: "project_lead",
      project: "api-version",
      running: "factory-pl/a3f2b19c07de",
      current: "factory-pl/a3f2b19c07de",
      drift: false,
    },
    {
      session_id: id("Atlas"),
      title: "Atlas",
      role: "lead",
      project: null,
      running: "factory-lead/91bc4e0a7f12",
      current: "factory-lead/91bc4e0a7f12",
      drift: false,
    },
  ];
  return { members: rows, drifted: rows.filter((m) => m.drift).length };
}

// ── Proposals and scorecards ───────────────────────────────────

function proposal(base: ProposalView, patch: Partial<ProposalView>): ProposalView {
  return { ...clone(base), ...patch };
}

/** Every proposal of the round-3 factory plus the learning ones (kept, reverted, code, co-sign). */
export function learnProposals(
  options: { afterSave?: boolean; scale?: boolean } = {},
): ProposalView[] {
  const base = clone(ROUND3.proposals);
  const c14 = base.find((p) => p.ref === "C-14")!;
  const c15 = base.find((p) => p.ref === "C-15")!;
  const extra: ProposalView[] = [
    proposal(c14, {
      id: "c12c12c12c12c12c12c12c12c12c12c1",
      ref: "C-12",
      number: 12,
      status: "reverted",
      class: "A",
      title: "cadence.lead 3600→7200",
      ops: [{ op: "replace", path: "cadence.lead", value: 7200 }],
      before: { "cadence.lead": 3600 },
      after: { "cadence.lead": 7200 },
      rationale: "Atlas's hourly beats found nothing new on 70% of runs.",
      evidence: ["F-5"],
      expected_effect: { metric: "heartbeat_share", direction: "down", delta: null },
      def_version_from: 3,
      def_version_to: 4,
      applied_at: NOW - 5 * D,
      trial_until: NOW - 3 * D,
      reverted_at: NOW - 4 * D,
      reverted_by: "server",
      result: { revert_reason: "guard regressed: human_prompts_per_day" },
      created_at: NOW - 5 * D - 600,
      updated_at: NOW - 4 * D,
    }),
    proposal(c15, {
      id: "c9c9c9c9c9c9c9c9c9c9c9c9c9c9c9c9",
      ref: "C-9",
      number: 9,
      status: "kept",
      title: "role=ic: run the package's unit tests before every push",
      ops: [
        {
          op: "add",
          path: "rules",
          value: {
            role: "ic",
            project: "*",
            text: "Run the package's unit tests before every push.",
          },
        },
      ],
      before: { "rules.R-3": null },
      after: {
        "rules.R-3": {
          id: "R-3",
          role: "ic",
          project: "*",
          text: "Run the package's unit tests before every push.",
          origin: "C-9",
          state: "active",
        },
      },
      rationale: "Two CI failures in a row were unit tests the IC never ran.",
      evidence: ["F-2", "!9617"],
      expected_effect: { metric: "first_pass_yield", direction: "up", delta: null },
      decision_id: null,
      decision_ref: "D-14",
      decided_by: "luka@caffeine.ai",
      decided_at: NOW - 6 * D,
      applied_at: NOW - 6 * D,
      def_version_from: 2,
      def_version_to: 3,
      result: { outcome: "kept", n: 5 },
      created_at: NOW - 6 * D - 3_600,
      updated_at: NOW - 4 * D,
    }),
    proposal(c15, {
      id: "c16c16c16c16c16c16c16c16c16c16c1",
      ref: "C-16",
      number: 16,
      kind: "code",
      status: "pending",
      title: "Promote rule R-3 into the IC skill text",
      ops: [],
      before: null,
      after: null,
      rationale: "R-3 held for 6 days with first-pass CI yield up 14 pp; it belongs in the bundle.",
      evidence: ["C-9", "F-2"],
      expected_effect: { metric: "first_pass_yield", direction: "up", delta: null },
      warnings: ["Tier 2 disabled: OMNIGENT_FACTORY_SMITH is not 1"],
      decision_id: null,
      decision_ref: null,
      result: { tier2: { enabled: false, reason: "OMNIGENT_FACTORY_SMITH is not 1" } },
      created_at: NOW - 3 * H,
      updated_at: NOW - 3 * H,
    }),
    proposal(c14, {
      id: "c18c18c18c18c18c18c18c18c18c18c1",
      ref: "C-18",
      number: 18,
      status: "awaiting_cosign",
      class: "A",
      approval: "auto",
      title: "alerts.quiet_m 10→15",
      ops: [{ op: "replace", path: "alerts.quiet_m", value: 15 }],
      before: { "alerts.quiet_m": 10 },
      after: { "alerts.quiet_m": 15 },
      rationale: "Quiet alerts on reviewers fired 9 times while codex was thinking.",
      evidence: ["F-11"],
      expected_effect: { metric: "incidents", direction: "down", delta: null },
      author: "f4f4ba9c77725331b12f0fa7072dc4d3",
      author_title: "Atlas",
      cosigned_by: null,
      decided_by: null,
      decided_at: null,
      applied_at: null,
      trial_until: null,
      def_version_from: null,
      def_version_to: null,
      created_at: NOW - 40 * 60,
      updated_at: NOW - 40 * 60,
    }),
  ];
  if (options.afterSave) {
    extra.push(
      proposal(c14, {
        id: "c17c17c17c17c17c17c17c17c17c17c1",
        ref: "C-17",
        number: 17,
        status: "applied",
        class: "human",
        approval: "owner",
        author_kind: "human",
        author: "luka@caffeine.ai",
        author_title: null,
        author_role: "owner",
        cosigned_by: null,
        title: "loops.implement.attempts: 3 → 4",
        ops: [{ op: "replace", path: "loops.implement.attempts", value: 4 }],
        before: { "loops.implement.attempts": 3 },
        after: { "loops.implement.attempts": 4 },
        rationale: "Billing tasks need a fourth try.",
        evidence: [],
        expected_effect: null,
        decided_by: "luka@caffeine.ai",
        decided_at: NOW - 60,
        applied_at: NOW - 60,
        trial_until: null,
        def_version_from: 7,
        def_version_to: 8,
        created_at: NOW - 60,
        updated_at: NOW - 60,
      }),
    );
  }
  if (options.scale) {
    for (let n = 19; n <= 48; n++) {
      extra.push(
        proposal(c14, {
          id: `scale${n}`.padEnd(32, "0"),
          ref: `C-${n}`,
          number: n,
          status: n % 7 === 0 ? "reverted" : n % 5 === 0 ? "rejected" : "kept",
          title: `loops.review.rounds ${2 + (n % 2)}→${3 - (n % 2)} · long title that should truncate cleanly in narrow columns`,
          trial_until: NOW - (60 - n) * H,
          applied_at: NOW - (62 - n) * H,
          created_at: NOW - (63 - n) * H,
          updated_at: NOW - (60 - n) * H,
        }),
      );
    }
  }
  return [...base, ...extra].sort((a, b) => b.number - a.number);
}

export function scorecard(ref: string): ScorecardView | null {
  const guards = (regressed: string | null): ScorecardView["guards"] => [
    {
      metric: "merge_acceptance",
      baseline: 0.75,
      trial: 0.8,
      n_baseline: 8,
      n_trial: 5,
      allowed: "≥ baseline − 10 pp",
      status: "ok",
    },
    {
      metric: "cost_per_merged_pr",
      baseline: 8.4,
      trial: 7.9,
      n_baseline: 6,
      n_trial: 4,
      allowed: "≤ +10 %",
      status: "ok",
    },
    {
      metric: "incidents",
      baseline: 3,
      trial: 2,
      n_baseline: 3,
      n_trial: 2,
      allowed: "≤ baseline + 1",
      status: "ok",
    },
    {
      metric: "human_prompts_per_day",
      baseline: 31,
      trial: regressed === "human_prompts_per_day" ? 44 : 33,
      n_baseline: 62,
      n_trial: 66,
      allowed: "≤ +20 %",
      status: regressed === "human_prompts_per_day" ? "regressed" : "ok",
    },
    {
      metric: "loop_exhaustion_rate",
      baseline: null,
      trial: null,
      n_baseline: 0,
      n_trial: 0,
      allowed: "≤ baseline + 10 pp",
      status: "no_data",
    },
  ];
  switch (ref) {
    case "C-14":
      return {
        outcome: "kept",
        reason: "target met, guards held",
        n: 31,
        min_n: 24,
        target: {
          metric: "heartbeat_share",
          met: true,
          baseline: 0.31,
          trial: 0.19,
          direction: "down",
        },
        guards: guards(null),
        trial_window: [NOW - 31 * H, NOW],
        baseline_window: [NOW - 62 * H, NOW - 31 * H],
        evaluated_at: NOW,
        live: true,
      };
    case "C-12":
      return {
        outcome: "reverted",
        reason: "guard regressed: human_prompts_per_day",
        n: 24,
        min_n: 24,
        target: {
          metric: "heartbeat_share",
          met: true,
          baseline: 0.3,
          trial: 0.22,
          direction: "down",
        },
        guards: guards("human_prompts_per_day"),
        trial_window: [NOW - 5 * D, NOW - 4 * D],
        baseline_window: [NOW - 6 * D, NOW - 5 * D],
        evaluated_at: NOW - 4 * D,
        live: false,
      };
    case "C-11":
    case "C-9":
      return {
        outcome: "kept",
        reason: "target met, guards held",
        n: 5,
        min_n: 3,
        target: {
          metric: "first_pass_yield",
          met: true,
          baseline: 0.58,
          trial: 0.72,
          direction: "up",
        },
        guards: guards(null),
        trial_window: [NOW - 6 * D, NOW - 4 * D],
        baseline_window: [NOW - 8 * D, NOW - 6 * D],
        evaluated_at: NOW - 4 * D,
        live: false,
      };
    default:
      return null;
  }
}

// ── The definition ─────────────────────────────────────────────

function merge(
  base: Record<string, unknown>,
  over: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = { ...base };
  for (const [key, value] of Object.entries(over)) {
    const prev = out[key];
    out[key] =
      value &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      prev &&
      typeof prev === "object"
        ? merge(prev as Record<string, unknown>, value as Record<string, unknown>)
        : value;
  }
  return out;
}

/** The doc in force: the server's defaults under the round-3 fixture's v7. */
export function effectiveDoc(afterSave = false): DefinitionDoc {
  const doc = merge(
    clone(meta.default_doc) as Record<string, unknown>,
    clone(ROUND3.definition.doc) as Record<string, unknown>,
  );
  delete doc.version;
  if (afterSave) {
    (doc.loops as { implement: { attempts: number } }).implement.attempts = 4;
  }
  return doc as DefinitionDoc;
}

export function definitionResponse(
  options: { afterSave?: boolean; tier2On?: boolean } = {},
): DefinitionResponse {
  const version = clone(ROUND3.definition);
  if (options.afterSave) {
    version.version = 8;
    version.parent_version = 7;
    version.created_via = "human";
    version.created_by = "luka@caffeine.ai";
    version.proposal_ref = "C-17";
  }
  return {
    def_version: version.version,
    version,
    effective: effectiveDoc(options.afterSave),
    bounds: clone(meta.bounds) as unknown as DefinitionResponse["bounds"],
    env_ceilings: [...meta.env_ceilings],
    classes: clone(meta.classes),
    paths: [...meta.paths],
    on_trial: learnProposals().filter((p) => p.status === "trial"),
    tier2: options.tier2On
      ? { enabled: true, reason: null }
      : { enabled: false, reason: "OMNIGENT_FACTORY_SMITH is not 1" },
  };
}

export function definitionVersions(afterSave = false): DefinitionVersionsResponse {
  const markers = versionsResponse().definition;
  const pinned = afterSave ? 8 : 7;
  const data: DefinitionView[] = markers.map((m) => ({
    factory_id: ROUND3.definition.factory_id,
    version: m.version,
    parent_version: m.version > 1 ? m.version - 1 : null,
    hash: `${m.version}`.repeat(64).slice(0, 64),
    created_by: m.created_by,
    created_via: m.created_via,
    proposal_id: null,
    proposal_ref: m.proposal_ref,
    created_at: m.created_at,
    pinned: m.version === pinned,
    doc: {},
  }));
  if (afterSave) {
    data.push({
      ...data[data.length - 1]!,
      version: 8,
      parent_version: 7,
      created_via: "human",
      created_by: "luka@caffeine.ai",
      proposal_ref: "C-17",
      created_at: NOW - 60,
      pinned: true,
    });
  }
  return { data: data.reverse(), pinned };
}

export function definitionDiff(from: number, to: number): DefinitionDiffResponse {
  const lines: Record<string, string[]> = {
    "6:7": ["cadence.project_lead: 7200 → 10800"],
    "5:6": ["loops.review.rounds: 2 → 3"],
    "7:8": ["loops.implement.attempts: 3 → 4"],
    "4:5": ["cadence.lead: 7200 → 3600"],
    "3:4": ["cadence.lead: 3600 → 7200"],
    "2:3": ['+ R-3 · ic · "Run the package\'s unit tests before every push."'],
    "1:2": ["budgets.project_usd: 100 → 150"],
  };
  const key = `${Math.min(from, to)}:${Math.max(from, to)}`;
  const found = lines[key] ?? [
    "cadence.project_lead: 7200 → 10800",
    "loops.review.rounds: 2 → 3",
    "budgets.project_usd: 100 → 150",
  ];
  return {
    from,
    to,
    changes: found.map((line) => ({ path: line.split(":")[0]!, before: null, after: null })),
    lines: found,
  };
}

// ── Routes ─────────────────────────────────────────────────────

export interface LearnRouteOptions {
  kind?: MetricsKind;
  owner?: boolean;
  afterSave?: boolean;
  tier2On?: boolean;
  scale?: boolean;
  bundlesDrift?: boolean;
  /** Replace the Insights read (e.g. `"pending"` or an error). */
  insights?: StoryResponse;
  definition?: StoryResponse;
  proposals?: StoryResponse | ((path: string) => StoryResponse);
}

function query(path: string): URLSearchParams {
  return new URL(path, "http://story").searchParams;
}

/** `/v1/factory/*` routes for the learning pages of `factoryId`. */
export function learnRoutes(factoryId: string, options: LearnRouteOptions = {}): StoryRoutes {
  const base = `/v1/factory/factories/${factoryId}`;
  const kind = options.kind ?? "populated";
  const proposals = () => learnProposals({ afterSave: options.afterSave, scale: options.scale });
  const routes: StoryRoutes = {
    [`${base}/insights`]: options.insights ?? {
      body: insightsResponse(kind, { owner: options.owner }),
    },
    [`${base}/metrics`]: (path) => {
      const q = query(path);
      const group = q.get("group_by");
      if (group === "day") return { body: metricsPage(metricsByDay(kind), "day") };
      if (group === "project" && q.get("names") === "human_wait") {
        return { body: metricsPage({ human_wait: waitByProject(kind, options.scale) }, "project") };
      }
      if (group === "role") return { body: metricsPage({ cost_usd: costByRole(kind) }, "role") };
      if (group) return { body: metricsPage(kpisByProject(), group) };
      return { body: metricsPage(allMetrics(kind), null) };
    },
    [`${base}/versions`]: { body: versionsResponse() },
    [`${base}/bundles`]: { body: bundlesResponse(options.bundlesDrift ?? true) },
    [`${base}/definition`]: options.definition ?? {
      body: definitionResponse({ afterSave: options.afterSave, tier2On: options.tier2On }),
    },
    [`${base}/definition/versions`]: { body: definitionVersions(options.afterSave) },
    [`${base}/definition/diff`]: (path) => {
      const q = query(path);
      return { body: definitionDiff(Number(q.get("from")), Number(q.get("to"))) };
    },
    [`${base}/proposals`]:
      options.proposals ??
      ((path) => {
        const q = query(path);
        const wantKind = q.get("kind");
        const statuses = (q.get("status") ?? "").split(",").filter(Boolean);
        return {
          body: {
            data: proposals().filter(
              (p) =>
                (!wantKind || p.kind === wantKind) &&
                (statuses.length === 0 || statuses.includes(p.status)),
            ),
          },
        };
      }),
  };
  for (const p of proposals()) {
    routes[`${base}/proposals/${p.ref}`] = { body: { proposal: p } };
    routes[`${base}/proposals/${p.ref}/scorecard`] = {
      body: { proposal: p.ref, scorecard: scorecard(p.ref) },
    };
  }
  return routes;
}
