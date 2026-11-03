// Home's story and test fixtures (sample data only): the new cockpit's routes
// over a snapshot (preflight offers it, the person chose it, the definition,
// Now, the asks the server would build, and the discovery reads when on),
// the round-3 sample with handles and models on its members, a factory with
// many ready PRs, a cold start, and a factory of 12 projects and 60 agents.

import type {
  DefinitionResponse,
  FactorySnapshot,
  MemberView,
  ProjectView,
  ProposalView,
  PrView,
  DecisionView,
} from "@/lib/factoryApi";
import { preflightOk } from "../../__fixtures__/liveRun";
import { round3IssueList } from "../../agents/__fixtures__/opsFixtures";
import type { StoryRoutes } from "../../factoryStoryFixtures";
import { definitionResponse } from "../../insights/__fixtures__/learnFixtures";
import { notifyMe } from "../../notify/__fixtures__/notifyFixtures";
import { statusSummaryOf } from "../../overview/__fixtures__/nowFixtures";
import { roomProposals } from "../../room/__fixtures__/roomFixtures";
import { round3Snapshot } from "../../shell/__fixtures__/shellFixtures";
import { asksOf } from "./asksFixtures";

const HANDLES = [
  "nova",
  "rex",
  "quill",
  "dot",
  "mesa",
  "juno",
  "pike",
  "wren",
  "otto",
  "lark",
  "sable",
  "tilde",
];

const MODEL: Record<string, string> = {
  lead: "claude-opus-4-1",
  project_lead: "claude-opus-4-1",
  ic: "claude-sonnet-4-5",
  reviewer: "gpt-5-codex",
  demo: "claude-sonnet-4-5",
  env_keeper: "claude-sonnet-4-5",
  planner: "claude-sonnet-4-5",
};

/** (sample) Handles, vendors and models on every member, as newer servers report them. */
export function withTeamFacts(snapshot: FactorySnapshot): FactorySnapshot {
  let n = 0;
  const members = snapshot.members.map((m): MemberView => {
    const codex = m.role === "reviewer" || (m.role === "ic" && n % 3 === 1);
    const handle =
      m.is_top_level && m.role === "lead"
        ? null
        : `${HANDLES[n % HANDLES.length]}${n >= HANDLES.length ? `-${Math.floor(n / HANDLES.length)}` : ""}`;
    n += 1;
    return {
      ...m,
      handle: m.handle ?? handle,
      vendor: codex ? "codex" : "claude",
      model: codex ? "gpt-5-codex" : (MODEL[m.role ?? ""] ?? "claude-sonnet-4-5"),
      harness: codex ? "codex" : (m.harness ?? "claude-native"),
    };
  });
  return { ...snapshot, members };
}

/** (sample) The product profile's merge policy on the snapshot (no product named). */
function withPolicy(snapshot: FactorySnapshot): FactorySnapshot {
  return {
    ...snapshot,
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
          merge_policy: "A person merges every pull request in this repo",
          protected_repos: [],
          demo_paths: [],
          preview_domain: null,
          release: "merged",
          flags: { provider: null, convention: null },
        },
      },
    },
  };
}

/** The live sample: round 3 with handles, models and a merge policy. */
export function homeSnapshot(role: "creator" | "operator" | "viewer" = "creator"): FactorySnapshot {
  return withPolicy(withTeamFacts(round3Snapshot(role)));
}

/** (sample) `n` more ready PRs, each on a project of its own. */
export function withReadyPrs(snapshot: FactorySnapshot, n: number): FactorySnapshot {
  const prTemplate = snapshot.prs.find((p) => p.ready)!;
  const projectTemplate = snapshot.projects.find((p) => p.slug === prTemplate.project_slug)!;
  const prs: PrView[] = [];
  const projects: ProjectView[] = [];
  for (let i = 0; i < n; i++) {
    const slug = `sample-change-${i + 1}`;
    const id = (0xa000 + i).toString(16).padStart(32, "0");
    prs.push({
      ...prTemplate,
      id,
      number: 9700 + i,
      title: `Sample change ${i + 1} (sample)`,
      project_slug: slug,
      ready_since: snapshot.server_time - (n - i) * 900,
    });
    projects.push({
      ...projectTemplate,
      slug,
      title: `Sample change ${i + 1} (sample)`,
      charter: projectTemplate.charter
        ? { ...projectTemplate.charter, outcome: `People get sample change ${i + 1} (sample)` }
        : null,
      pr_ids: [id],
      pl: null,
      team: [],
      stage_since: snapshot.server_time - (n - i) * 900,
    });
  }
  return {
    ...snapshot,
    prs: [...snapshot.prs, ...prs],
    projects: [...snapshot.projects, ...projects],
  };
}

/** Nothing waits on anyone: no open decision, no ready PR, nothing broken. */
export function calmSnapshot(snapshot: FactorySnapshot): FactorySnapshot {
  return {
    ...snapshot,
    decisions: snapshot.decisions.filter((d) => d.status !== "open"),
    prs: snapshot.prs.map((pr) => ({ ...pr, ready: false })),
    projects: snapshot.projects.map((p) =>
      p.overlay === "failed" || p.overlay === "blocked"
        ? { ...p, overlay: null, overlay_reason: null }
        : p,
    ),
    members: snapshot.members.map((m) =>
      m.health === "dead" || m.status === "failed"
        ? { ...m, health: null, status: "idle", now: m.now ? { ...m.now, flags: [] } : m.now }
        : m,
    ),
  };
}

const ROLE_SEQUENCE = ["ic", "ic", "reviewer", "demo"] as const;

/** (sample) 12 projects with a lead and four members each (60 agents with the leads). */
export function manyProjectsSnapshot(): FactorySnapshot {
  const base = homeSnapshot();
  const template = base.projects.find((p) => p.slug === "ux-badge") ?? base.projects[0]!;
  const lead = base.members.find((m) => m.role === "project_lead")!;
  const sub = base.members.find((m) => m.role === "ic")!;
  const stages = ["planning", "building", "building", "review", "verify", "ready"] as const;
  const projects: ProjectView[] = [];
  const members: MemberView[] = base.members.filter((m) => m.role === "lead");
  for (let i = 0; i < 12; i++) {
    const slug = `sample-${i + 1}`;
    const leadId = (0xb000 + i).toString(16).padStart(32, "0");
    projects.push({
      ...template,
      slug,
      title: `Sample project ${i + 1} (sample)`,
      charter: template.charter
        ? { ...template.charter, outcome: `People can do sample thing ${i + 1} (sample)` }
        : null,
      stage: stages[i % stages.length]!,
      stage_since: base.server_time - (i + 1) * 1500,
      overlay: null,
      overlay_reason: null,
      pr_ids: [],
      human_owner: null,
      why: i % 4 === 0 ? ({ opportunity: `O-${i + 3}`, metric: null } as ProjectView["why"]) : null,
    });
    members.push({
      ...lead,
      session_id: leadId,
      title: `PL · ${slug}`,
      project: slug,
      handle: `lead-${i + 1}`,
      status: i % 3 === 0 ? "running" : "idle",
      running: i % 3 === 0,
    });
    ROLE_SEQUENCE.forEach((role, k) => {
      const codex = role === "reviewer" || (role === "ic" && k === 1);
      members.push({
        ...sub,
        session_id: (0xc000 + i * 10 + k).toString(16).padStart(32, "0"),
        parent_session_id: leadId,
        title: `${role}:${slug}`,
        role,
        project: slug,
        handle: `${HANDLES[(i + k) % HANDLES.length]}-${i + 1}`,
        vendor: codex ? "codex" : "claude",
        model: codex ? "gpt-5-codex" : "claude-sonnet-4-5",
        harness: codex ? "codex" : "claude-native",
        status: (i + k) % 5 === 0 ? "running" : "idle",
        running: (i + k) % 5 === 0,
        stuck: i === 4 && k === 3,
        stuck_reason: i === 4 && k === 3 ? "same failing browser step 3 times" : null,
      });
    });
  }
  return { ...base, projects, members, prs: [], decisions: [] };
}

/**
 * (sample) A definition with discovery on, its starter sources, and (or not)
 * a goal, on a server that runs discovery (`server: false`: one that does not).
 */
export function discoveryDefinition(
  options: { goal?: boolean; server?: boolean } = {},
): DefinitionResponse {
  const body = definitionResponse();
  const base = body.effective.discovery;
  return {
    ...body,
    discovery_server: options.server ?? true,
    effective: {
      ...body.effective,
      product: { name: "Sample app (sample)" } as DefinitionResponse["effective"]["product"],
      discovery: {
        ...(base ?? {}),
        enabled: true,
        sources: [
          { id: "repo", label: "The factory's repos (sample)", kind: "repo", state: "trial" },
          { id: "web", label: "The open web (sample)", kind: "web", state: "trial" },
          { id: "team", label: "The factory's people (sample)", kind: "team", state: "active" },
        ],
      } as unknown as NonNullable<DefinitionResponse["effective"]["discovery"]>,
      strategy: options.goal
        ? ({
            goals: [
              {
                id: "G-1",
                statement: "More new users get a working result on day one (sample)",
                metric: null,
                baseline: null,
                target: null,
                by: null,
                weight: 1,
              },
            ],
            guardrails: [],
            non_goals: [],
            focus_segments: [],
            mix: { outcome: 60, tests: 20, enablers: 10, hunches: 10 },
          } as NonNullable<DefinitionResponse["effective"]["strategy"]>)
        : undefined,
    },
  };
}

/** (sample) The discovery summary of a factory that has been learning for a while. */
export const SAMPLE_DISCOVERY = {
  chain: { signals: 214, opportunities: 8, bets: 3, building: 1, shipped: 1, measured: 1 },
  opportunities_now: 2,
  signals_unsorted: 37,
  readouts_won: 1,
  learned: [
    {
      text: "Stored 214 new signals from 3 sources and found 2 new opportunities (sample).",
      ref: "O-24",
    },
    {
      text: "“Templates for common work” moved Later → Next on 21 new accounts (sample).",
      ref: "O-12",
    },
  ],
};

/** (sample) The ranked opportunities. */
export const SAMPLE_OPPORTUNITIES = {
  data: [
    {
      ref: "O-15",
      title: "Can't show a draft before it goes live (sample)",
      band: "now",
      score: 0.23,
      level: "moderate",
      accounts: 23,
      status: "bet filed",
    },
    {
      ref: "O-3",
      title: "Builds fail before a first draft (sample)",
      band: "now",
      score: 0.19,
      level: "moderate",
      accounts: 41,
      status: "building",
    },
    {
      ref: "O-7",
      title: "Custom names for shared links (sample)",
      band: "next",
      score: 0.12,
      level: "weak",
      accounts: 9,
      status: "ready to plan",
    },
    {
      ref: "O-24",
      title: "Preview on a phone (sample)",
      band: "next",
      score: 0.1,
      level: "weak",
      accounts: 6,
      status: "research due",
    },
    {
      ref: "O-18",
      title: "Dark theme everywhere (sample)",
      band: "later",
      score: 0.04,
      level: "hunch",
      accounts: 2,
      status: "new",
    },
  ],
};

/** (sample) Cold start: the discovery lead drafted the profile and suggests a first goal. */
export function coldStartProposals(snapshot: FactorySnapshot): {
  snapshot: FactorySnapshot;
  proposals: ProposalView[];
} {
  const template = roomProposals().find((p) => p.kind === "rules")!;
  const decisionTemplate = round3Snapshot().decisions.find((d) => d.source === "proposal")!;
  const make = (number: number, title: string, before: object, after: object): ProposalView => ({
    ...template,
    id: `cold${number}`,
    ref: `C-${number}`,
    number,
    title,
    kind: "tuning",
    status: "pending",
    before,
    after,
    rationale: `${title}: drafted from the repository's own docs (sample).`,
    author: snapshot.factory.root_session_id,
    author_title: "Atlas",
    decision_id: `colddecision${number}`,
    decision_ref: `D-${number}`,
  });
  const proposals = [
    make(
      2,
      "Product profile v1 (sample)",
      { "product.summary": null },
      { "product.summary": "A sample tool (sample)" },
    ),
    make(
      3,
      "A first goal (sample)",
      { "strategy.goals/new": null },
      {
        "strategy.goals/new": {
          statement: "More first-time visitors finish setup in ten minutes (sample)",
        },
      },
    ),
  ];
  const decisions: DecisionView[] = proposals.map((p, i) => ({
    ...decisionTemplate,
    id: `colddecision${p.number}`,
    ref: `D-${p.number}`,
    number: p.number,
    subject: `proposal:${p.ref}`,
    question: `Atlas proposes ${p.ref} (tuning): ${p.title}`,
    blocking: false,
    assignee: "owners",
    created_at: snapshot.server_time - (2 - i) * 3600,
    deadline_at: snapshot.server_time + 60 * 3600,
  }));
  return { snapshot: { ...snapshot, decisions }, proposals };
}

/** (sample) A co-signed bet on O-15 that waits for a person's approval, mirrored as a decision. */
export function withBet(snapshot: FactorySnapshot): {
  snapshot: FactorySnapshot;
  proposals: ProposalView[];
} {
  const template = roomProposals().find((p) => p.kind === "projects" && p.status === "pending")!;
  const decisionTemplate = round3Snapshot().decisions.find((d) => d.source === "proposal")!;
  const sentinel = snapshot.members.find(
    (m) => m.role === "lead" && m.session_id !== snapshot.factory.root_session_id,
  );
  const bet: ProposalView = {
    ...template,
    id: "bet31",
    ref: "C-31",
    number: 31,
    title: "Share a private draft link (sample)",
    status: "pending",
    opportunity: "O-15",
    bet: {
      kind: "outcome",
      goal: "G-1",
      hypothesis: "People who can share a draft publish more (sample)",
      outcome: "People share a draft before it goes live (sample)",
      metric: "week-one publishing",
      expected: { direction: "up", low: 1, high: 4, unit: "pp" },
      guardrails: [],
      flag: { key: "draft_links" },
      measurement: { method: "signal_trend", readout_days: [7, 28] },
      kill: { state: "flat", date: "2026-12-15" },
    } as ProposalView["bet"],
    case_against: ["14 of 23 accounts come from one thread (sample)"],
    cosigned_by: sentinel?.session_id ?? null,
    decision_id: "betdecision31",
    decision_ref: "D-36",
  };
  const decision: DecisionView = {
    ...decisionTemplate,
    id: "betdecision31",
    ref: "D-36",
    number: 36,
    subject: "proposal:C-31",
    question: "Atlas proposes C-31: Share a private draft link (sample)",
    blocking: false,
    assignee: "owners",
    created_at: snapshot.server_time - 3 * 3600,
    deadline_at: snapshot.server_time + 69 * 3600,
  };
  return {
    snapshot: { ...snapshot, decisions: [...snapshot.decisions, decision] },
    proposals: [...roomProposals(), bet],
  };
}

export interface HomeRouteOptions {
  /** The definition read (discovery off by default). */
  definition?: DefinitionResponse;
  /** The discovery summary; 404 (no ledger) when omitted. */
  discovery?: unknown;
  /** The ranked opportunities; 404 when omitted. */
  opportunities?: unknown;
  proposals?: readonly ProposalView[];
  /** Incidents (round 3's by default). */
  incidents?: ReturnType<typeof round3IssueList>;
}

/** The new cockpit's routes for Home over `snapshot` (fixture-built Now and asks). */
export function homeRoutes(snapshot: FactorySnapshot, options: HomeRouteOptions = {}): StoryRoutes {
  const own = `/v1/factory/factories/${snapshot.factory.id}`;
  const incidents = options.incidents ?? round3IssueList();
  const missing = { error: 404, message: "Not found" } as const;
  return {
    "/v1/factory/preflight": { body: { ...preflightOk(), cockpit_v2: true } },
    [`${own}/notify/me/ui`]: { body: { cockpit: "v2" } },
    [`${own}/notify/me`]: { body: notifyMe() },
    [`${own}/definition`]: { body: options.definition ?? definitionResponse() },
    [`${own}/issues`]: { body: { data: incidents, count: incidents.length, mine: [] } },
    [`${own}/status-summary`]: { body: statusSummaryOf(snapshot, { incidents, usdPerHAlert: 40 }) },
    [`${own}/asks`]: {
      body: asksOf(snapshot, { incidents, proposals: options.proposals ?? roomProposals() }),
    },
    [`${own}/discovery`]: options.discovery ? { body: options.discovery } : missing,
    [`${own}/opportunities`]: options.opportunities ? { body: options.opportunities } : missing,
  };
}
