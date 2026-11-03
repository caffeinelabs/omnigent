// Fixtures for Steer over the round-3 sample factory: a product profile, a
// goal, focus segments and sources (all marked "(sample)", on example.com),
// a rubric trial C-33 holding the discovery block, agents' proposals (Scout
// adds a segment and a source, Sentinel edits Scout's brief), versions with an
// undone one and a restore, and the server's preview answers (a focus change
// with its re-rank, a rubric change refused a second trial, a restore, an
// operator's change that waits for owners). Every export is a fresh copy.

import type {
  DefinitionPreviewResponse,
  DefinitionResponse,
  DefinitionView,
  DefinitionVersionsResponse,
  ProposalView,
  SourceDef,
} from "@/lib/factoryApi";
import type { Ask, AsksResponse } from "@/lib/factoryAsksApi";
import type { GatesResponse } from "@/lib/factorySteerApi";
import type { StoryRoutes } from "../../factoryStoryFixtures";
import {
  bundlesResponse,
  definitionResponse,
  learnProposals,
} from "../../insights/__fixtures__/learnFixtures";
import { ROUND3_NOW_S } from "../../shell/__fixtures__/shellFixtures";
import { teamCatalog } from "../../team/__fixtures__/teamFixtures";

const clone = <T>(value: T): T => structuredClone(value);
const H = 3600;
const D = 86_400;
const NOW = ROUND3_NOW_S;

export const STEER_NOW_S = NOW;
export const STEER_VERSION = 12;

function source(patch: Partial<SourceDef> & Pick<SourceDef, "id" | "label" | "kind">): SourceDef {
  return {
    params: {},
    roles: ["scout"],
    privacy: {
      level: "public",
      redact: ["email", "phone", "token", "ip", "url_secret", "card"],
      quote_max: 280,
      keep_days: 365,
    },
    consent: "public",
    budget: { calls_per_day: 100 },
    interval_min: 60,
    state: "active",
    ...patch,
  };
}

export const SAMPLE_SOURCES: SourceDef[] = [
  source({
    id: "forum",
    label: "Public community forum (sample)",
    kind: "web",
    params: { domains: ["forum.example.com"] },
    budget: { calls_per_day: 200 },
  }),
  source({
    id: "repo",
    label: "The factory's repos (sample)",
    kind: "repo",
    params: { repos: ["sample-org/sample-app"], since_days: 90 },
    privacy: { level: "internal", redact: [], quote_max: 280, keep_days: 180 },
    consent: "internal",
    state: "trial",
    interval_min: 360,
  }),
  source({
    id: "web",
    label: "Web and market (sample)",
    kind: "web",
    params: { changelogs: true },
    budget: { calls_per_day: 30 },
    interval_min: 0,
  }),
  source({
    id: "team",
    label: "Factory people (sample)",
    kind: "team",
    privacy: { level: "internal", redact: [], quote_max: 280, keep_days: 180 },
    consent: "internal",
    interval_min: 0,
  }),
];

/** What a sample definition leaves out or moves (stories and tests). */
export interface SteerDefinitionOptions {
  discovery?: boolean;
  server?: boolean;
  noGoal?: boolean;
  noTrial?: boolean;
  /** No discovery sources yet. */
  noSources?: boolean;
  /** No rules and no briefs: a factory that never set them. */
  bare?: boolean;
  /** v13 is in force: someone else added "Teams" to the focus after v12. */
  moved?: boolean;
  /** v13 is in force: the viewer applied it two minutes ago (Undo still open). */
  undoable?: boolean;
}

/** The definition of a factory that steers: profile, goal, focus, sources, rubric. */
export function steerDefinition(options: SteerDefinitionOptions = {}): DefinitionResponse {
  const discovery = options.discovery ?? true;
  const base = definitionResponse();
  const effective = clone(base.effective);
  effective.product = {
    name: "Sample app (sample)",
    kind: "web app",
    summary: "A sample product people use to build and share small apps (sample).",
    links: { site: "https://www.example.com", docs: "https://docs.example.com" },
    segments: [
      { id: "new-users", label: "New users", rule: "account age 14 days or less" },
      { id: "active-builders", label: "Active builders", rule: "3 or more builds in 7 days" },
      { id: "teams", label: "Teams", rule: "2 or more members on one project" },
    ],
    areas: [
      { id: "builder", label: "Builder", paths: ["app/builder/**"], events: ["build_started"] },
      { id: "sharing", label: "Sharing", paths: ["app/share/**"], events: [] },
    ],
    competitors: [
      { name: "Other tool (sample)", changelog: "https://other.example.com/changelog" },
    ],
    delivery: {
      merge_policy: "A person merges every pull request after review (sample).",
      protected_repos: ["sample-org/sample-infra"],
      demo_paths: ["app/**"],
      preview_domain: "previews.example.com",
      release: "github_release",
      flags: { provider: "none", convention: null },
    },
  };
  effective.strategy = {
    goals: options.noGoal
      ? []
      : [
          {
            id: "G-1",
            statement: "More new users publish a working app in their first week (sample)",
            metric: null,
            baseline: null,
            target: 40,
            by: "2027-03-31",
            weight: 1,
          },
        ],
    guardrails: ["Support requests per 1,000 accounts (sample)"],
    non_goals: ["Enterprise single sign-on (sample)", "Pricing changes (sample)"],
    focus_segments: options.moved ? ["new-users", "teams"] : ["new-users"],
    mix: { outcome: 60, tests: 20, enablers: 15, hunches: 5 },
  };
  const disc = effective.discovery!;
  disc.enabled = discovery;
  disc.seat = { model: "sonnet" };
  disc.sources = discovery && !options.noSources ? clone(SAMPLE_SOURCES) : [];
  disc.rubric.decay_half_life_days = 45;
  effective.briefs = options.bare
    ? (Object.fromEntries(
        Object.keys(effective.briefs ?? {}).map((k) => [k, ""]),
      ) as unknown as typeof effective.briefs)
    : { ...effective.briefs!, scout: "Focus on people in their first two weeks (sample)." };
  if (options.bare) effective.rules = [];
  const trial = options.noTrial ? [] : [rubricTrial()];
  const inForce = options.moved || options.undoable ? STEER_VERSION + 1 : STEER_VERSION;
  return {
    ...base,
    def_version: inForce,
    version: base.version ? { ...base.version, version: inForce } : null,
    effective,
    bounds: {
      ...base.bounds,
      "discovery.budgets.usd_per_day": [0, 1000],
      "discovery.budgets.per_ask": [0, 100],
      "discovery.cadence.sweep_h": [0, 168],
      "discovery.cadence.synthesis_h": [0, 720],
      "discovery.thresholds.min_accounts": [1, 100],
      "discovery.thresholds.bets_per_week": [0, 50],
      "discovery.thresholds.max_open_bets": [0, 50],
    },
    on_trial: trial,
    trial_blocks: trial.map((t) => ({
      proposal: t.ref,
      blocks: ["discovery"],
      trial_until: t.trial_until,
    })),
    lists: {
      "product.segments": { numbered: null },
      "product.areas": { numbered: null },
      "strategy.goals": { numbered: "G" },
      "discovery.sources": { numbered: null },
      "team.templates": { numbered: null },
    },
    owner_only: [
      "engine.",
      "autonomy.",
      "budgets.",
      "models.",
      "product.",
      "strategy.",
      "discovery.",
      "briefs.",
      "role_defaults.",
    ],
    not_trialable: [
      "product",
      "strategy",
      "discovery",
      "briefs.scout",
      "team.projects",
      "team.templates",
    ],
    not_trialable_why: {
      product: "judged on outcomes",
      strategy: "judged on outcomes",
      discovery: "needs discovery guards",
      "briefs.scout": "needs discovery guards",
      "team.projects": "too few merged PRs per team",
      "team.templates": "no project follows a template",
    },
    agent_paths: {
      scout: ["product", "strategy", "discovery.sources"],
      sentinel: ["discovery.rubric", "discovery.thresholds", "briefs.scout", "team"],
      atlas: ["team"],
    },
    discovery_server: options.server ?? true,
  };
}

function baseProposal(): ProposalView {
  return clone(learnProposals()[0]!);
}

/** C-33: Sentinel's rubric trial (evidence half-life 60 → 45 days), day 4 of 14. */
export function rubricTrial(): ProposalView {
  const applied = NOW - 3 * D - 6 * H;
  return {
    ...baseProposal(),
    id: "c33c33c33c33c33c33c33c33c33c33c3",
    ref: "C-33",
    number: 33,
    kind: "tuning",
    status: "trial",
    class: "B",
    title: "Evidence half-life 60 → 45 days",
    ops: [{ op: "replace", path: "discovery.rubric.decay_half_life_days", value: 45 }],
    before: { "discovery.rubric.decay_half_life_days": 60 },
    after: { "discovery.rubric.decay_half_life_days": 45 },
    rationale: "Stale complaints keep opportunities in Now (sample).",
    evidence: ["F-14"],
    expected_effect: { metric: "merge_acceptance", direction: "up", delta: null },
    author_kind: "agent",
    author_title: "Sentinel",
    author_role: "lead",
    applied_at: applied,
    trial_until: applied + 14 * D,
    def_version_from: 11,
    def_version_to: 12,
    created_at: applied - 600,
    updated_at: applied,
  };
}

/** Agents' pending proposals: Scout adds a segment and a source, Sentinel edits Scout's brief. */
export function steerProposals(): ProposalView[] {
  const p = baseProposal();
  const pending = (patch: Partial<ProposalView>): ProposalView => ({
    ...p,
    kind: "tuning",
    status: "pending",
    class: "B",
    approval: "owner",
    author_kind: "agent",
    decision_id: null,
    decision_ref: null,
    applied_at: null,
    trial_until: null,
    def_version_from: null,
    def_version_to: null,
    expected_effect: null,
    ...patch,
  });
  return [
    pending({
      id: "c32c32c32c32c32c32c32c32c32c32c3",
      ref: "C-32",
      number: 32,
      title: 'Product profile: add the segment "Came back after a failed build" (sample)',
      ops: [
        {
          op: "add",
          path: "product.segments",
          value: {
            id: "came-back",
            label: "Came back after a failed build",
            rule: "a build failed, then a new one within 3 days",
          },
        },
      ],
      before: {},
      after: {},
      rationale:
        "14 signals mention coming back after a failure. No segment captures them today (sample).",
      evidence: ["S-1188", "S-1207"],
      author_title: "Scout",
      author_role: "scout",
      created_at: NOW - 11 * H,
    }),
    pending({
      id: "c35c35c35c35c35c35c35c35c35c35c3",
      ref: "C-35",
      number: 35,
      title: "Scout's brief: name the riskiest assumption in every bet note",
      ops: [
        {
          op: "replace",
          path: "briefs.scout",
          value:
            "Focus on people in their first two weeks (sample). Name the riskiest assumption in every bet note.",
        },
      ],
      rationale:
        "From the discovery retro: 2 of 3 rejected bets had no riskiest assumption (sample).",
      evidence: ["C-26", "C-28", "F-14"],
      author_title: "Sentinel",
      author_role: "lead",
      created_at: NOW - 2 * H,
    }),
    pending({
      id: "c36c36c36c36c36c36c36c36c36c36c3",
      ref: "C-36",
      number: 36,
      title: "Add a source: the support tracker (sample)",
      ops: [
        {
          op: "add",
          path: "discovery.sources",
          value: {
            id: "support",
            label: "Support questions (sample)",
            kind: "web",
            params: { domains: ["help.example.com"] },
            roles: ["scout"],
            budget: { calls_per_day: 50 },
            state: "trial",
          },
        },
      ],
      rationale: "Would measure the goal's first-week funnel from public help threads (sample).",
      evidence: ["S-1101"],
      author_title: "Scout",
      author_role: "scout",
      created_at: NOW - 5 * H,
    }),
  ];
}

/** C-37: Scout suggests a first goal (a factory with no goal yet). */
export function goalSuggestion(): ProposalView {
  return {
    ...steerProposals()[0]!,
    id: "c37c37c37c37c37c37c37c37c37c37c3",
    ref: "C-37",
    number: 37,
    title: "More new users publish a working app in their first week (sample)",
    ops: [
      {
        op: "add",
        path: "strategy.goals",
        value: {
          statement: "More new users publish a working app in their first week (sample)",
          metric: null,
          target: null,
          by: null,
          weight: 1,
        },
      },
    ],
    rationale: "41 signals this month are new users stuck before a first working app (sample).",
    evidence: ["S-1101", "S-1188"],
    created_at: NOW - 3 * H,
  };
}

/** Every proposal the Versions list joins with (the trial and the pending ones). */
export function steerAllProposals(): ProposalView[] {
  return [...steerProposals(), rubricTrial()];
}

/**
 * Versions v12 … v8: v11 was undone, v10 restores v8, v12 is C-33's. With
 * `latest`, v13 is in force, made from v12 by `latest.by` `latest.age_s` ago.
 */
export function steerVersions(
  latest: { by: string; age_s: number; title?: string } | null = null,
): DefinitionVersionsResponse {
  const row = (version: number, patch: Partial<DefinitionView> = {}): DefinitionView => ({
    factory_id: "f",
    version,
    parent_version: version - 1,
    hash: `${version}`.repeat(64).slice(0, 64),
    created_by: "lena@example.com",
    created_via: "human",
    proposal_id: null,
    proposal_ref: null,
    created_at: NOW - (12 - version + 1) * D,
    pinned: version === STEER_VERSION,
    doc: {},
    ...patch,
  });
  const newest = latest
    ? [
        row(13, {
          created_by: latest.by,
          created_by_title: latest.title ?? null,
          created_at: NOW - latest.age_s,
          pinned: true,
        }),
      ]
    : [];
  return {
    pinned: latest ? STEER_VERSION + 1 : STEER_VERSION,
    data: [
      ...newest,
      row(12, {
        created_via: "proposal",
        created_by: "s-sentinel",
        created_by_title: "Sentinel",
        proposal_ref: "C-33",
        pinned: !latest,
      }),
      row(11, { undone_by: "mia@example.com", undone_at: NOW - 2 * D }),
      row(10, { proposal_ref: "C-30", restored_from: 8 }),
      row(9, { proposal_ref: "C-29" }),
      row(8, { created_via: "seed", created_by: "server" }),
    ],
  };
}

const RERANK = [
  {
    opportunity: "O-7",
    title: "No way to connect a custom domain (sample)",
    before: { position: 4, rank: 0.12, band: "next" },
    after: { position: 2, rank: 0.25, band: "now" },
    line: "O-7 Next → Now",
  },
  {
    opportunity: "O-24",
    title: "Undo the last change (sample)",
    before: { position: 6, rank: 0.1, band: "next" },
    after: { position: 4, rank: 0.2, band: "now" },
    line: "O-24 Next → Now",
  },
  {
    opportunity: "O-15",
    title: "Can't show a draft before it goes live (sample)",
    before: { position: 2, rank: 0.23, band: "now" },
    after: { position: 3, rank: 0.23, band: "now" },
    line: "O-15 #2 → #3",
  },
  {
    opportunity: "O-18",
    title: "Lost work when a preview expired (sample)",
    before: { position: 9, rank: 0.05, band: "later" },
    after: { position: 7, rank: 0.1, band: "next" },
    line: "O-18 Later → Next",
  },
];

/** The preview of toggling "Active builders" into focus. */
/** The focus change staged on v12 after v13 (someone else's) came into force. */
export function previewStale(): DefinitionPreviewResponse {
  const preview = previewFocus();
  const before = ["new-users", "teams"];
  return {
    ...preview,
    def_version: STEER_VERSION + 1,
    version_next: STEER_VERSION + 2,
    diff: preview.diff.map((d) => ({
      ...d,
      before,
      line: "strategy.focus_segments: new-users, teams → new-users, active-builders",
    })),
    blocked: [
      {
        index: null,
        path: "",
        code: "stale_definition",
        message: `v${STEER_VERSION + 1} is in force, not v${STEER_VERSION}`,
      },
    ],
  };
}

export function previewFocus(): DefinitionPreviewResponse {
  return {
    def_version: STEER_VERSION,
    version_next: STEER_VERSION + 1,
    diff: [
      {
        path: "strategy.focus_segments",
        before: ["new-users"],
        after: ["new-users", "active-builders"],
        class: "B",
        owner_only: true,
        block: "strategy",
        line: "strategy.focus_segments: new-users → new-users, active-builders",
      },
    ],
    lines: ["strategy.focus_segments: new-users → new-users, active-builders"],
    class: "B",
    approvers: ["owners"],
    who: "Owners (class B). You are an owner, so it applies when you press Apply. Agents can only propose it.",
    owner_only: true,
    you_can_apply: true,
    you_can_propose: true,
    blocked: [],
    effects: [
      {
        kind: "data",
        text: "Product, strategy, discovery, brief, role-default and template changes are data the factory reads when it needs them; nobody is mailed.",
        paths: ["strategy.focus_segments"],
      },
    ],
    rerank: clone(RERANK),
    trialable: {
      ok: false,
      code: "not_trialable",
      reason: "strategy is judged on outcomes: they apply as a version",
      why: "judged on outcomes",
    },
    restore: null,
  };
}

/**
 * A preview for any staged change: its diff (owner-only, class B), no re-rank,
 * and the trial verdict the server gives those paths.
 */
export function previewWith(
  entries: readonly { path: string; before: unknown; after: unknown }[],
  trialable: DefinitionPreviewResponse["trialable"] = {
    ok: false,
    code: "not_trialable",
    reason: "it applies as a version",
  },
): DefinitionPreviewResponse {
  const line = (e: { path: string; before: unknown; after: unknown }) =>
    `${e.path}: ${JSON.stringify(e.before)} → ${JSON.stringify(e.after)}`;
  return {
    ...previewFocus(),
    diff: entries.map((e) => ({
      ...e,
      class: "B",
      owner_only: true,
      block: e.path.split(".")[0] ?? null,
      line: line(e),
    })),
    lines: entries.map(line),
    rerank: [],
    effects: [],
    trialable,
  };
}

/** The preview of a rubric number while C-33 trials the discovery block. */
export function previewRubric(): DefinitionPreviewResponse {
  return {
    ...previewFocus(),
    diff: [
      {
        path: "discovery.rubric.off_focus_factor",
        before: 0.5,
        after: 0.8,
        class: "B",
        owner_only: true,
        block: "discovery",
        line: "discovery.rubric.off_focus_factor: 0.5 → 0.8",
      },
    ],
    lines: ["discovery.rubric.off_focus_factor: 0.5 → 0.8"],
    rerank: clone(RERANK).slice(0, 3),
    trialable: {
      ok: false,
      code: "trial_overlap",
      reason:
        "C-33 is already trialing discovery (day 4 of 14). Two trials on one block confound each other's guards: apply it directly, or wait for C-33 to end.",
      trial: "C-33",
      block: "discovery",
    },
  };
}

/** The preview of restoring v10. */
export function previewRestore(): DefinitionPreviewResponse {
  return {
    ...previewFocus(),
    diff: [
      {
        path: "discovery.rubric.decay_half_life_days",
        before: 45,
        after: 60,
        class: "B",
        owner_only: true,
        block: "discovery",
        line: "discovery.rubric.decay_half_life_days: 45 → 60",
      },
    ],
    lines: ["discovery.rubric.decay_half_life_days: 45 → 60", "Ends trial C-33"],
    rerank: [],
    effects: [
      {
        kind: "restore",
        text: "Stores v10's settings as v13; v12 stays in the history and can be restored.",
      },
      { kind: "ends_trial", text: "Ends trial C-33: its change is taken out.", trial: "C-33" },
    ],
    trialable: {
      ok: false,
      code: "restore",
      reason: "A restore applies as a version; undo it, or restore again.",
    },
    restore: { version: 10, ends: ["C-33"] },
  };
}

/** An operator's change: it waits for the owners. */
export function previewOperator(): DefinitionPreviewResponse {
  return {
    ...previewFocus(),
    who: "Owners (class B). It waits for an owner: propose it to them. Agents can only propose it.",
    you_can_apply: false,
    you_can_propose: true,
  };
}

/** `GET …/gates`, as the server words it. */
export function steerGates(): GatesResponse {
  const row = (
    gate: string,
    label: string,
    checks: string,
    extra: Partial<GatesResponse["gates"][number]> = {},
  ) => ({
    gate,
    label,
    checks,
    required: "always",
    required_text: "every pull request",
    role: null,
    role_label: null,
    role_when: null,
    role_text: null,
    skipped_by: [],
    allowed_by: null,
    ...extra,
  });
  return {
    gates: [
      row("draft", "Ready for review", "The pull request is marked ready for review, not a draft."),
      row("base", "Right base branch", "It targets its project's base branch."),
      row(
        "checks",
        "Checks pass",
        "The checks the base branch requires have reported and none failed.",
      ),
      row("review", "Review", "A reviewer approved the newest commit.", {
        role: "reviewer",
        role_label: "reviewer",
        role_when: "always",
        role_text: "the review gate needs a reviewer",
      }),
      row("verify", "Verify", "The env keeper ran the change in an environment and it passed.", {
        role: "env_keeper",
        role_label: "env keeper",
        role_when: "env",
        role_text: "the verify gate needs an env keeper",
        skipped_by: ["factory:no-env", "factory:revert"],
      }),
      row("mergeable", "No conflicts", "The code host reports the branch mergeable."),
      row(
        "protected_paths",
        "Protected paths",
        "It touches no protected path unless a person allows it.",
        {
          allowed_by: "factory:allow-protected",
        },
      ),
      row("demo", "Demo", "The newest demo of the head passed (PASS or PARTIAL).", {
        required: "previews",
        required_text: "while the project has previews on",
        role: "demo",
        role_label: "demo agent",
        role_when: "previews",
        role_text: "the demo gate needs one while previews are on",
        skipped_by: ["factory:no-env"],
      }),
    ],
    team_needs: [
      {
        gate: "builder",
        role: "ic",
        role_label: "builder",
        when: "always",
        when_text: "on every team",
        text: "a project needs at least one builder",
        pr_gate: false,
      },
    ],
    protected_paths: [".github/workflows/**"],
    labels: {
      no_env: "factory:no-env",
      revert: "factory:revert",
      allow_protected: "factory:allow-protected",
    },
    tier: 0,
    note: "Gates are Tier 0: no definition change, trial or agent proposal changes them.",
  };
}

/** Needs you with what waits for an owner (a bet and a source). */
export function steerAsks(factoryId: string): AsksResponse {
  const ask = (id: string, kind: "bet" | "source", title: string, context: string): Ask => ({
    id,
    kind,
    subject: id,
    ref: id,
    title,
    context,
    meta: [],
    since: NOW - 3 * H,
    age_s: 3 * H,
    cost_of_waiting: { score: 5, level: "medium", text: "holds a bet", holds: 1 },
    primary_action: {
      kind: "open",
      label: "Review",
      href: `/factory/${factoryId}/steer/versions?peek=${id}`,
    },
    options: null,
    allow_text: false,
    items: [],
    more_count: 0,
    assignee: "owners",
    mine: true,
    folded: [],
    covers: [],
    critical: false,
    href: `/factory/${factoryId}/steer/versions?peek=${id}`,
  });
  return {
    factory_id: factoryId,
    asks: [
      ask(
        "C-31",
        "bet",
        "Approve a bet: share a private draft link (sample)",
        "From an opportunity with 23 accounts (sample).",
      ),
      ask(
        "C-36",
        "source",
        "Approve a new source: support questions (sample)",
        "Proposed by Scout (sample).",
      ),
    ],
    handled: [],
    counts: { total: 2, mine: 2, others: 0, in_steer: 0, folded: 0 },
    generated_at: NOW,
  };
}

/** The discovery summary's per-source counts. */
export function steerDiscoverySummary() {
  return {
    enabled: true,
    sources: [
      { id: "forum", calls_today: 41, calls_per_day: 200, signals_week: 40, health: "ok" },
      { id: "repo", calls_today: 58, calls_per_day: 100, signals_week: 24, health: "ok" },
      { id: "web", calls_today: 17, calls_per_day: 30, signals_week: 10, health: "ok" },
      { id: "team", calls_today: 0, calls_per_day: 100, signals_week: 3, health: "new" },
    ],
  };
}

export interface SteerRouteOptions extends SteerDefinitionOptions {
  preview?: DefinitionPreviewResponse;
  proposals?: ProposalView[];
}

/** `/v1/factory/*` routes Steer reads for `factoryId`. */
export function steerRoutes(factoryId: string, options: SteerRouteOptions = {}): StoryRoutes {
  const base = `/v1/factory/factories/${factoryId}`;
  const proposals = options.proposals ?? steerAllProposals();
  return {
    [`${base}/definition`]: { body: steerDefinition(options) },
    [`${base}/definition/versions`]: {
      body: steerVersions(
        options.undoable
          ? { by: "lena@example.com", age_s: 120 }
          : options.moved
            ? { by: "mia@example.com", age_s: 300 }
            : null,
      ),
    },
    [`${base}/definition/preview`]: { body: options.preview ?? previewFocus() },
    [`${base}/definition/diff`]: {
      body: {
        from: 10,
        to: 12,
        changes: [],
        lines: ["discovery.rubric.decay_half_life_days: 60 → 45"],
      },
    },
    [`${base}/proposals`]: (path) => {
      const q = new URL(path, "http://story").searchParams;
      const kind = q.get("kind");
      return { body: { data: proposals.filter((p) => !kind || p.kind === kind) } };
    },
    [`${base}/proposals/C-33/scorecard`]: {
      body: {
        proposal: "C-33",
        scorecard: {
          outcome: "inconclusive",
          reason: "n below 3",
          n: 1,
          min_n: 3,
          target: { metric: null, met: false },
          guards: [
            {
              metric: "merge_acceptance",
              baseline: 0.8,
              trial: 0.82,
              n_baseline: 9,
              n_trial: 4,
              allowed: "≥ baseline − 10 pp",
              status: "ok",
            },
            {
              metric: "incidents",
              baseline: 2,
              trial: 1,
              n_baseline: 9,
              n_trial: 4,
              allowed: "≤ baseline",
              status: "ok",
            },
          ],
          trial_window: [NOW - 4 * D, NOW],
          baseline_window: [NOW - 8 * D, NOW - 4 * D],
          evaluated_at: NOW,
          live: true,
        },
      },
    },
    [`${base}/gates`]: { body: steerGates() },
    [`${base}/bundles`]: { body: bundlesResponse(false) },
    [`${base}/catalog`]: { body: teamCatalog() },
    [`${base}/asks`]: { body: steerAsks(factoryId) },
    [`${base}/discovery`]:
      options.discovery === false ? { error: 404 } : { body: steerDiscoverySummary() },
  };
}
