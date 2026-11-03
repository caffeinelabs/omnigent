// Sample (fictional, product-agnostic) data for the new cockpit's Projects
// page and project page, on the Teams sample factory: a project from evidence
// (an opportunity and a bet, its lead's update overridden by a stuck member),
// one a person requested (ready for a merge), an agent's idea, a stopped one,
// and a shipped one. Every visible string is marked "(sample)" or is neutral;
// repos are sample-org/sample-app and links point at example.com.

import type {
  FactorySnapshot,
  GatesView,
  ProjectView,
  PrView,
  DecisionView,
  EventPage,
  EventView,
  ViewerRole,
} from "@/lib/factoryApi";
import type { CharterVersionsResponse } from "@/lib/factoryProjectsApi";
import { SAMPLE_AGENTS } from "../../agents/__fixtures__/teamsFixtures";
import { type StoryRoutes, filterEventPage } from "../../factoryStoryFixtures";
import { round3Snapshot } from "../../shell/__fixtures__/shellFixtures";
import {
  TEAM_SLUG,
  teamDefinition,
  teamRoutes,
  teamSnapshot,
} from "../../team/__fixtures__/teamFixtures";

const H = 3600;
const REPO = "sample-org/sample-app";

export const EVIDENCE_SLUG = TEAM_SLUG; // csv-export
export const REQUESTED_SLUG = "dark-mode";
export const STOPPED_SLUG = "search-filters";
export const IDEA_SLUG = "bulk-archive";
export const SHIPPED_SLUG = "saved-views";

function gates(partial: Partial<GatesView>): GatesView {
  return {
    draft: "pass",
    base: "pass",
    checks: "pass",
    review: "pending",
    verify: "pending",
    mergeable: "pass",
    protected_paths: "pass",
    demo: "pending",
    reasons: [],
    notes: [],
    evidence: {
      checks: null,
      review: null,
      verify: null,
      demo: null,
      base: null,
      mergeable: null,
      files: [],
      protected: [],
    },
    ...partial,
  };
}

function pr(
  template: PrView,
  number: number,
  slug: string,
  title: string,
  extra: Partial<PrView>,
  nowS: number,
): PrView {
  return {
    ...template,
    id: (0xd000 + number).toString(16).padStart(32, "0"),
    project_slug: slug,
    repo: REPO,
    number,
    url: `https://example.com/${REPO}/pull/${number}`,
    title,
    task: null,
    head_ref: `factory/${slug}/t-${number}`,
    base_ref: "main",
    state: "open",
    is_draft: false,
    ready: false,
    ready_since: null,
    sentinel: null,
    human_review: null,
    preview: null,
    demo: null,
    older_demos: 0,
    merged: null,
    closed_at: null,
    loops: null,
    task_ref: null,
    task_id: null,
    opened_at: nowS - 5 * H,
    first_ready_at: null,
    gates: gates({}),
    ...extra,
  };
}

/** (sample) A demo of the CSV export's PR: four steps passed, one failed. */
function sampleDemo(nowS: number): NonNullable<PrView["demo"]> {
  const step = (n: number, title: string, status: "pass" | "fail") => ({
    n,
    title,
    kind: "ui" as const,
    status,
    screenshot_file_id: null,
    duration_ms: 1800,
    note: null,
    api_calls: null,
  });
  return {
    id: "d".repeat(32),
    status: "PARTIAL",
    head_sha: "a".repeat(40),
    is_current_head: true,
    title: "(sample) Export a report as CSV",
    summary: "(sample) The export downloads; one column header is misspelled.",
    plan_source: null,
    duration_s: 42,
    console_errors: 0,
    steps: [
      step(1, "(sample) Open a report", "pass"),
      step(2, "(sample) Choose Export", "pass"),
      step(3, "(sample) Pick CSV", "pass"),
      step(4, "(sample) The file downloads", "pass"),
      step(5, "(sample) Headers match the table", "fail"),
    ],
    files: [],
    video_file_id: null,
    poster_file_id: null,
    preview_url: null,
    created_at: nowS - H,
    by: "demo:dot",
  };
}

export interface ProjectV2Options {
  /** The csv-export lead's update is older than three hours. */
  staleUpdate?: boolean;
  /** An open question on the requested project, waiting on the viewer. */
  question?: boolean;
  /** Ten more requested projects (for a busy page). */
  more?: number;
  /** Who looks (an owner by default). */
  viewer?: ViewerRole;
  /** Keep only these projects (the shared environment stays). */
  only?: string[];
}

/**
 * (sample) The Teams sample factory with each kind of project: from evidence,
 * requested (ready for a merge), an agent's idea, stopped and shipped.
 */
export function projectsV2Snapshot(options: ProjectV2Options = {}): FactorySnapshot {
  const snapshot = teamSnapshot("standard", { viewer: options.viewer });
  const nowS = snapshot.server_time;
  const template = round3Snapshot().prs[0]!;
  // Nothing of the round-3 template a project row was built from shows through.
  const neutral = (p: ProjectView): ProjectView => ({
    ...p,
    human_owner: null,
    active_task: null,
    tasks_summary: {
      total: 5,
      done: 2,
      running: 1,
      ready: 1,
      blocked: 1,
      failed: 0,
      critical_path_min: 90,
    },
    loops_summary: null,
    stage_note: null,
    start: null,
    provisioning: null,
    depends_on: null,
    depends_mode: null,
    paused_by: null,
    parent_slug: null,
  });
  const bySlug = new Map(snapshot.projects.map((p) => [p.slug, neutral(p)]));
  const base = bySlug.get(EVIDENCE_SLUG)!;
  const charter = (outcome: string, extra: Partial<NonNullable<ProjectView["charter"]>> = {}) => ({
    kind: "work" as const,
    goal: `${outcome}, behind a flag`,
    dod: ["(sample) the change is behind a flag, off by default"],
    scope_paths: ["src/reports/**"],
    constraints: null,
    env_needed: true,
    preview: { enabled: true, slots: 1 },
    preview_slots: 1,
    demo_focus: null,
    demo: null,
    budget_ics: 2,
    outcome,
    ...extra,
  });
  const updateAt = options.staleUpdate ? nowS - 4 * H : nowS - 25 * 60;
  const evidence: ProjectView = {
    ...base,
    repo: REPO,
    base_branch: "main",
    elapsed_s: 2 * 24 * H,
    stage: "verify",
    stage_since: nowS - 18 * 60,
    cost_usd: 25.35,
    charter: charter("(sample) People can export a report as CSV"),
    why: {
      proposal: "C-29",
      opportunity: "O-3",
      kind: "outcome",
      goal: "G-1",
      outcome: "(sample) People can export a report as CSV",
      hypothesis: "(sample) People who export reports come back weekly",
      metric: "weekly_exports",
      expected: { direction: "up", low: 1, high: 4, unit: "pp" },
      guardrails: ["(sample) support load stays flat"],
      flag: { key: "csv-export", convention: "kebab-case, off by default" },
      measurement: { method: "before_after", readout_days: [7, 28] },
      kill: { state: "(sample) lift under 0.5 pp", date: "28 days after release" },
      frozen_at: nowS - 3 * 24 * H,
      frozen_by: "owner",
    },
    requested_by: { kind: "agent", name: "Atlas", proposal: "C-29" },
    update: {
      health: "on_track",
      done: "(sample) T-3 merged; the export endpoint streams rows",
      next: "(sample) T-4 in review",
      blocker: null,
      at: updateAt,
      by: "nova",
    },
    health: {
      health: "at_risk",
      source: "facts",
      computed: "at_risk",
      facts: ["checks green", "budget at 42 %", "1 member stuck"],
      reasons: ["1 member stuck"],
      agrees: false,
      stale: Boolean(options.staleUpdate),
      budget_usd: 60,
    },
  };
  const requested: ProjectView = {
    ...bySlug.get(REQUESTED_SLUG)!,
    repo: REPO,
    base_branch: "main",
    elapsed_s: 26 * H,
    stage: "ready",
    stage_since: nowS - 5 * H - 50 * 60,
    cost_usd: 11.2,
    charter: charter("(sample) Settings remember the dark theme", { scope_paths: [] }),
    requested_by: { kind: "human", name: "mia@example.com" },
    human_owner: "mia@example.com",
    update: {
      health: "on_track",
      done: "(sample) Ready for merge; demo passed",
      next: "(sample) Waits for a person's merge",
      blocker: null,
      at: nowS - 40 * 60,
      by: "orin",
    },
    health: {
      health: "on_track",
      source: "lead",
      computed: "on_track",
      facts: ["checks green", "budget at 19 %"],
      reasons: [],
      agrees: true,
      stale: false,
      // The charter's project budget: this project's team spec sets none.
      budget_usd: 60,
    },
  };
  const stopped: ProjectView = {
    ...bySlug.get(STOPPED_SLUG)!,
    repo: REPO,
    base_branch: "main",
    elapsed_s: 4 * 24 * H,
    stage_since: nowS - 7 * H,
    charter: charter("(sample) Search results can be filtered by date"),
    overlay: "failed",
    overlay_reason: "(sample) its lead stopped and could not restart",
    requested_by: { kind: "human", name: "owner@example.test" },
    health: {
      health: "off_track",
      source: "facts",
      computed: "off_track",
      facts: [],
      reasons: ["the project stopped"],
      agrees: null,
      stale: false,
    },
  };
  const idea: ProjectView = {
    ...base,
    slug: IDEA_SLUG,
    title: "(sample) Bulk archive",
    repo: REPO,
    base_branch: "main",
    elapsed_s: 6 * H,
    stage: "building",
    stage_since: nowS - 2 * H,
    cost_usd: 3.4,
    pl: null,
    team: [],
    team_spec: null,
    pr_ids: [],
    open_decision_ids: [],
    charter: charter("(sample) People can archive many items at once", { scope_paths: [] }),
    why: null,
    requested_by: { kind: "agent", name: "Atlas", proposal: "C-33" },
    update: null,
    health: {
      health: "on_track",
      source: "facts",
      computed: "on_track",
      facts: ["budget at 9 %"],
      reasons: [],
      agrees: null,
      stale: false,
      budget_usd: 38,
    },
  };
  const shipped: ProjectView = {
    ...idea,
    slug: SHIPPED_SLUG,
    title: "(sample) Saved views",
    stage: "merged",
    stage_since: nowS - 3 * 24 * H,
    charter: charter("(sample) People can save a filtered view"),
    requested_by: { kind: "human", name: "mia@example.com" },
    update: null,
    health: null,
  };
  const prs: PrView[] = [
    pr(
      template,
      41,
      EVIDENCE_SLUG,
      "(sample) Stream the CSV export",
      {
        gates: gates({ checks: "pass", review: "pass", verify: "pending", demo: "pending" }),
        demo: sampleDemo(nowS),
      },
      nowS,
    ),
    pr(
      template,
      38,
      REQUESTED_SLUG,
      "(sample) Remember the theme in settings",
      {
        ready: true,
        ready_since: nowS - 5 * H - 50 * 60,
        first_ready_at: nowS - 5 * H - 50 * 60,
        gates: gates({ checks: "pass", review: "pass", verify: "pass", demo: "pass" }),
      },
      nowS,
    ),
  ];
  evidence.pr_ids = [prs[0]!.id];
  requested.pr_ids = [prs[1]!.id];
  const decisions: DecisionView[] = [];
  if (options.question) {
    const d = round3Snapshot().decisions.find((x) => x.status === "open")!;
    const question: DecisionView = {
      ...d,
      id: "e".repeat(32),
      ref: "D-41",
      number: 41,
      project_slug: REQUESTED_SLUG,
      question: "(sample) Keep the old theme toggle for a week?",
      context: "(sample) Some people use the old toggle; removing it now may confuse them.",
      options: d.options.slice(0, 2).map((o, i) => ({
        ...o,
        label: i === 0 ? "(sample) Keep it a week" : "(sample) Remove it now",
        description: null,
        recommended: i === 0,
        action: null,
        href: null,
        target: null,
      })),
      asked_by_title: "PL · dark-mode",
      blocking: true,
    };
    decisions.push(question);
    requested.open_decision_ids = [question.id];
  }
  const extra: ProjectView[] = Array.from({ length: options.more ?? 0 }, (_, i) => ({
    ...idea,
    slug: `sample-${i + 1}`,
    title: `(sample) Project ${i + 1}`,
    charter: charter(`(sample) People get sample change ${i + 1}`),
    requested_by: { kind: "human", name: "owner@example.test" },
    stage: (["planning", "building", "review", "verify", "ready"] as const)[i % 5]!,
  }));
  // Each member's vendor and model, as servers with handles report them.
  const sample = new Map(SAMPLE_AGENTS.map((a) => [a.handle, a]));
  const members = snapshot.members.map((m) => {
    const agent = m.handle ? sample.get(m.handle) : undefined;
    if (!agent) return m;
    const vendor = agent.harness.startsWith("codex") ? "codex" : "claude";
    return { ...m, vendor, model: agent.model };
  });
  return {
    ...snapshot,
    members,
    factory: {
      ...snapshot.factory,
      product: {
        name: "Sample app (sample)",
        kind: null,
        summary: null,
        links: {},
        segments: [],
        areas: [],
        competitors: [],
        delivery: {
          merge_policy: "A person merges every pull request in this repo (sample)",
          protected_repos: [],
          demo_paths: [],
          preview_domain: null,
          release: "merged",
          flags: { provider: null, convention: "kebab-case keys, off by default (sample)" },
        },
      },
    },
    projects: [evidence, requested, stopped, idea, shipped, ...extra]
      .filter((p) => !options.only || options.only.includes(p.slug))
      .concat(snapshot.projects.filter((p) => p.kind === "integration")),
    prs,
    decisions: [...snapshot.decisions, ...decisions],
  };
}

/** (sample) The CSV export's charter history: a person's edit (Undo open) and an agent's. */
export function charterVersions(nowS: number, options: { justEdited?: boolean } = {}) {
  const versions: CharterVersionsResponse["versions"] = [
    {
      ref: "C-34",
      status: "applied",
      op: "update",
      title: "update csv-export (dod)",
      by: "owner",
      by_kind: "human",
      at: options.justEdited ? nowS - 120 : nowS - 2 * H,
      undo_until: options.justEdited ? nowS + 480 : null,
      revert_of: null,
      rationale: "Charter edited in the cockpit",
      changes: [
        {
          field: "dod",
          before: ["(sample) the export works"],
          after: ["(sample) the change is behind a flag, off by default"],
        },
      ],
      revertible: true,
    },
    {
      ref: "C-31",
      status: "applied",
      op: "update",
      title: "update csv-export (scope_paths)",
      by: "Nova",
      by_kind: "agent",
      at: nowS - 26 * H,
      undo_until: null,
      revert_of: null,
      rationale: "(sample) narrow the scope to the reports module",
      changes: [{ field: "scope_paths", before: [], after: ["src/reports/**"] }],
      revertible: true,
    },
    {
      ref: "C-29",
      status: "applied",
      op: "create",
      title: "create csv-export",
      by: "Atlas",
      by_kind: "agent",
      at: nowS - 3 * 24 * H,
      undo_until: null,
      revert_of: null,
      rationale: null,
      changes: [],
      revertible: false,
    },
  ];
  return { slug: EVIDENCE_SLUG, versions, started: { at: nowS - 3 * 24 * H } };
}

/** (sample) `GET …/opportunities/O-3`: the evidence behind the CSV export. */
export const SAMPLE_OPPORTUNITY = {
  id: "O-3",
  title: "(sample) Reports cannot leave the app",
  rank: 0.5,
  evidence: {
    level: "moderate",
    accounts: 41,
    hunches: 1,
    by_source: { forum: 22, trk: 12, web: 7 },
  },
};

/** (sample) A day of the CSV export, in the words the activity list shows. */
export function sampleEvents(snapshot: FactorySnapshot): EventPage {
  const nowS = snapshot.server_time;
  const event = (n: number, ago: number, kind: string, summary: string): EventView => ({
    id: (0xe000 + n).toString(16).padStart(32, "0"),
    ts: (nowS - ago) * 1000,
    kind,
    severity: "info",
    project_slug: EVIDENCE_SLUG,
    from_id: null,
    to_id: null,
    from_title: null,
    to_title: null,
    subject: `project:${EVIDENCE_SLUG}`,
    summary,
    data: null,
  });
  return {
    data: [
      event(1, 25 * 60, "project.update", "(sample) nova posted an update on csv-export: on track"),
      event(2, 2 * H, "pr.ready", "(sample) #38 is ready for a person's merge"),
      event(3, 5 * H, "pr.discovered", "(sample) #41 opened: Stream the CSV export"),
      event(4, 26 * H, "proposal.applied", "(sample) C-31 applied by owner"),
    ],
    has_more: false,
  };
}

export interface ProjectV2RouteOptions {
  versions?: CharterVersionsResponse | "missing";
  /** Discovery runs (the definition and this server both say so). */
  discovery?: boolean;
}

/** `/v1/factory/*` for the new cockpit's project pages over `snapshot`. */
export function projectV2Routes(
  snapshot: FactorySnapshot,
  options: ProjectV2RouteOptions = {},
): StoryRoutes {
  const base = `/v1/factory/factories/${snapshot.factory.id}`;
  const definition = teamDefinition();
  const routes: StoryRoutes = {
    ...teamRoutes(snapshot),
    [`${base}/notify/me/ui`]: { body: { cockpit: "v2" } },
    // Discovery runs only with this server's switch and the definition's.
    [`${base}/definition`]: {
      body: options.discovery
        ? {
            ...definition,
            discovery_server: true,
            effective: {
              ...definition.effective,
              discovery: { ...(definition.effective.discovery ?? {}), enabled: true },
            },
          }
        : definition,
    },
    [`${base}/opportunities/O-3`]: options.discovery
      ? { body: SAMPLE_OPPORTUNITY }
      : { error: 404, message: "discovery_off" },
    [`${base}/tasks`]: {
      body: { tasks: [], projects: [] },
    },
    // Neutral ops reads: no loops or incidents of another sample, and this sample's events.
    [`${base}/loops`]: { body: { data: [], engine_mode: "assist" } },
    [`${base}/issues`]: { body: { data: [], count: 0, mine: [] } },
    [`${base}/events`]: (path) => ({ body: filterEventPage(sampleEvents(snapshot), path) }),
  };
  for (const project of snapshot.projects) {
    routes[`${base}/projects/${project.slug}/charter/versions`] =
      options.versions === "missing"
        ? { error: 404, message: "not found" }
        : {
            body:
              project.slug === EVIDENCE_SLUG
                ? (options.versions ?? charterVersions(snapshot.server_time))
                : { slug: project.slug, versions: [], started: { at: snapshot.server_time } },
          };
    routes[`${base}/projects/${project.slug}/demos`] = { body: { data: [], count: 0 } };
  }
  return routes;
}
