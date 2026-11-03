// Sample (fictional, product-agnostic) data for the team chart and editor, on
// the Teams sample factory: the "(sample)" CSV export project with its own
// team spec (standard, pair or investigation), the team catalog, the server's
// preview of a few staged change sets, and a short team history across
// definition versions. Rates and bench figures are made up.

import type {
  CatalogResponse,
  CatalogRole,
  DefinitionEffect,
  DefinitionPreviewResponse,
  DefinitionResponse,
  DefinitionVersionsResponse,
  DefinitionView,
  FactorySnapshot,
  TeamMemberSpecView,
  TeamSpecView,
  ViewerRole,
} from "@/lib/factoryApi";
import {
  SAMPLE_AGENTS,
  TEAMS_NOW_S,
  teamsRoutes,
  teamsSnapshot,
} from "../../agents/__fixtures__/teamsFixtures";
import type { StoryRoutes } from "../../factoryStoryFixtures";
import { definitionResponse } from "../../insights/__fixtures__/learnFixtures";
import { viewerOf } from "../../shell/__fixtures__/shellFixtures";
import type { RestoredTeam } from "../teamModel";

export const TEAM_SLUG = "csv-export";
export const TEAM_NOW_S = TEAMS_NOW_S;
const H = 3600;
const OWNER = "owner@example.test";

export type TeamShape = "standard" | "pair" | "investigation";

type Seat = [id: string, role: string, vendor: string, model: string | null, label: string];

const SHAPES: Record<TeamShape, Seat[]> = {
  standard: [
    ["M-1", "project_lead", "claude", null, "opus"],
    ["M-2", "ic", "claude", null, "sonnet"],
    ["M-3", "ic", "codex", null, "gpt-5-codex"],
    ["M-4", "reviewer", "codex", null, "gpt-5-codex"],
    ["M-5", "env_keeper", "claude", null, "sonnet"],
    ["M-6", "demo", "claude", null, "sonnet"],
  ],
  pair: [
    ["M-1", "project_lead", "claude", null, "opus"],
    ["M-2", "ic", "claude", null, "sonnet"],
    ["M-7", "ic", "claude", "opus", "opus"],
    ["M-4", "reviewer", "codex", null, "gpt-5-codex"],
    ["M-5", "env_keeper", "claude", null, "sonnet"],
    ["M-6", "demo", "claude", null, "sonnet"],
  ],
  investigation: [
    ["M-1", "project_lead", "claude", null, "opus"],
    ["M-2", "ic", "claude", null, "sonnet"],
    ["M-7", "ic", "claude", null, "sonnet"],
    ["M-4", "reviewer", "codex", null, "gpt-5-codex"],
  ],
};

const AGENTS: Record<string, Record<string, string>> = {
  project_lead: { claude: "factory-pl" },
  ic: { claude: "ic", codex: "ic_codex" },
  reviewer: { codex: "reviewer", claude: "ic" },
  env_keeper: { claude: "env_keeper" },
  demo: { claude: "demo" },
};

const GATE_TEXT: Record<string, string> = {
  reviewer: "Can't remove: the review gate needs a reviewer (Tier 0)",
  env_keeper: "Can't remove: the verify gate needs an env keeper (Tier 0)",
  demo: "Can't remove: the demo gate needs one while previews are on (Tier 0)",
  ic: "Can't remove: a project needs at least one builder (Tier 0)",
};

function blockedOf(seats: Seat[], role: string, shape: TeamShape): string | null {
  if (role === "project_lead") return "The project lead stays: every project has one.";
  if (shape === "investigation" && (role === "env_keeper" || role === "demo")) return null;
  return seats.filter((s) => s[1] === role).length <= 1 ? (GATE_TEXT[role] ?? null) : null;
}

/** The sample project's team spec, as the snapshot carries it. */
export function teamSpec(
  shape: TeamShape = "standard",
  options: { mode?: "warn" | "enforce"; own?: boolean; defVersion?: number } = {},
): TeamSpecView {
  const seats = SHAPES[shape];
  const own = options.own ?? true;
  const members: TeamMemberSpecView[] = seats.map(([id, role, vendor, model, label]) => ({
    id,
    role,
    handle: null,
    vendor,
    agent: AGENTS[role]?.[vendor] ?? role,
    model,
    model_label: label,
    brief: id === "M-2",
    remove_blocked: blockedOf(seats, role, shape),
  }));
  return {
    own,
    follows: own ? "own" : "bundle",
    summary: own
      ? null
      : "Follows today's bundle: every agent of the PL bundle on its default model, with the charter's builders at once and budget.",
    template: own ? shape : null,
    builders_at_once: shape === "standard" ? 2 : 2,
    builders_from: "team",
    budget_usd: 60,
    budget_from: "team",
    members,
    drift: [],
    drifted: false,
    mode: options.mode ?? "enforce",
    def_version: options.defVersion ?? 12,
  };
}

/** The sample agents of a shape: who runs the work now (a pair has two Claude builders). */
function agentsFor(shape: TeamShape) {
  return SAMPLE_AGENTS.filter((a) => {
    if (a.project !== TEAM_SLUG) return true;
    if (shape === "investigation") return a.role !== "env_keeper" && a.role !== "demo";
    return true;
  }).map((a) =>
    a.project === TEAM_SLUG && a.handle === "rex" && shape !== "standard"
      ? { ...a, harness: "claude-native", model: "claude-opus-4-1", label: "opus" }
      : a,
  );
}

/** The Teams sample factory with the CSV export project's team spec (and `viewer`). */
export function teamSnapshot(
  shape: TeamShape = "standard",
  options: { mode?: "warn" | "enforce"; own?: boolean; viewer?: ViewerRole } = {},
): FactorySnapshot {
  const snapshot = teamsSnapshot(agentsFor(shape));
  snapshot.def_version = 12;
  snapshot.projects = snapshot.projects.map((p) =>
    p.slug === TEAM_SLUG
      ? {
          ...p,
          repo: "sample/repo",
          base_branch: "main",
          team_spec: teamSpec(shape, options),
        }
      : p,
  );
  if (options.viewer && options.viewer !== "owner") {
    snapshot.viewer = viewerOf(options.viewer, `${options.viewer}@example.test`);
  } else {
    snapshot.viewer = viewerOf("owner", OWNER);
  }
  return snapshot;
}

// ── Catalog ────────────────────────────────────────────────────

const RATES: Record<string, Record<string, number>> = {
  project_lead: { opus: 1.2, sonnet: 0.5, haiku: 0.2 },
  ic: { opus: 2.6, sonnet: 1.6, haiku: 0.5, "gpt-5-codex": 1.3 },
  reviewer: { opus: 2.4, sonnet: 0.9, haiku: 0.3, "gpt-5-codex": 0.6 },
  env_keeper: { opus: 1.1, sonnet: 0.4, haiku: 0.2 },
  demo: { opus: 1.3, sonnet: 0.5, haiku: 0.2 },
};

const DOES: Record<string, string> = {
  project_lead: "Plans the project, dispatches its team, reviews and hands PRs over.",
  ic: "Implements one task card per dispatch in its own worktree and PR.",
  reviewer: "Reviews a PR against its acceptance contract; never edits.",
  env_keeper: "Keeps the dev env and one preview per PR head.",
  demo: "Records a demo of a PR head in its preview.",
};

const LABEL: Record<string, string> = {
  project_lead: "project lead",
  ic: "builder",
  reviewer: "reviewer",
  env_keeper: "env keeper",
  demo: "demo agent",
};

function claude(role: string, fallback: string): CatalogRole["vendors"][number] {
  const rates = RATES[role]!;
  return {
    vendor: "claude",
    available: true,
    agent: AGENTS[role]?.claude ?? role,
    default_model: fallback,
    models: ["opus", "sonnet", "haiku"],
    usd_per_h: rates[fallback],
    rate_source: "ledger",
    rate_n: 14,
    model_rates: { opus: rates.opus!, sonnet: rates.sonnet!, haiku: rates.haiku! },
    bench: null,
  };
}

function codex(role: string): CatalogRole["vendors"][number] {
  const rate = RATES[role]?.["gpt-5-codex"];
  if (rate == null) {
    const runtime = "Claude Code";
    return {
      vendor: "codex",
      available: false,
      reason: `Codex is not available for ${LABEL[role]}s yet: they run on ${runtime} today`,
    };
  }
  return {
    vendor: "codex",
    available: true,
    agent: AGENTS[role]?.codex ?? role,
    default_model: "gpt-5-codex",
    models: ["gpt-5-codex"],
    usd_per_h: rate,
    rate_source: "ledger",
    rate_n: 11,
    model_rates: { "gpt-5-codex": rate },
    bench: null,
  };
}

/** `GET …/catalog?project=csv-export`. */
export function teamCatalog(
  options: { mode?: "warn" | "enforce"; shape?: TeamShape } = {},
): CatalogResponse {
  const shape = options.shape ?? "standard";
  const investigation = shape === "investigation";
  const role = (r: string, fallback: string, codexFirst = false): CatalogRole => ({
    role: r,
    label: LABEL[r]!,
    does: DOES[r]!,
    needed_by:
      r === "reviewer"
        ? { gate: "review", text: "the review gate needs a reviewer", when: "always" }
        : r === "env_keeper"
          ? { gate: "verify", text: "the verify gate needs an env keeper", when: "env" }
          : r === "demo"
            ? {
                gate: "demo",
                text: "the demo gate needs one while previews are on",
                when: "previews",
              }
            : r === "ic"
              ? { gate: "builder", text: "a project needs at least one builder", when: "always" }
              : null,
    addable: r !== "project_lead",
    vendors: codexFirst ? [codex(r), claude(r, fallback)] : [claude(r, fallback), codex(r)],
  });
  const spec = teamSpec(shape);
  return {
    def_version: 12,
    roles: [
      role("project_lead", "opus"),
      role("ic", "sonnet"),
      role("reviewer", "sonnet", true),
      role("env_keeper", "sonnet"),
      role("demo", "sonnet"),
    ],
    missing: [
      {
        role: "designer",
        label: "Designer",
        reason:
          "Not in the catalog. A new kind of role needs a built-in bundle, which is a code change.",
      },
      {
        role: "qa",
        label: "QA tester",
        reason:
          "Not in the catalog. A new kind of role needs a built-in bundle, which is a code change.",
      },
    ],
    templates: [
      {
        id: "standard",
        label: "Standard",
        summary:
          "A Claude and a Codex builder, a cross-vendor reviewer, an env keeper and a demo agent.",
        roles: [
          { role: "project_lead", vendor: null, model: null, count: 1 },
          { role: "ic", vendor: "claude", model: null, count: 1 },
          { role: "ic", vendor: "codex", model: null, count: 1 },
          { role: "reviewer", vendor: "codex", model: null, count: 1 },
          { role: "env_keeper", vendor: null, model: null, count: 1 },
          { role: "demo", vendor: null, model: null, count: 1 },
        ],
        builders_at_once: null,
        default: true,
        members: 6,
        locked: null,
      },
      {
        id: "pair",
        label: "Pair",
        summary: "Two Claude builders, a Codex reviewer, an env keeper and a demo agent.",
        roles: [
          { role: "project_lead", vendor: null, model: null, count: 1 },
          { role: "ic", vendor: "claude", model: null, count: 2 },
          { role: "reviewer", vendor: "codex", model: null, count: 1 },
          { role: "env_keeper", vendor: null, model: null, count: 1 },
          { role: "demo", vendor: null, model: null, count: 1 },
        ],
        builders_at_once: null,
        default: false,
        members: 6,
        locked: null,
      },
      {
        id: "lean",
        label: "Lean",
        summary: "One builder, a reviewer and an env keeper; no demo (previews off only).",
        roles: [
          { role: "project_lead", vendor: null, model: null, count: 1 },
          { role: "ic", vendor: "claude", model: null, count: 1 },
          { role: "reviewer", vendor: "codex", model: null, count: 1 },
          { role: "env_keeper", vendor: null, model: null, count: 1 },
        ],
        builders_at_once: 1,
        default: false,
        members: 4,
        locked: investigation ? null : "Locked: the demo gate needs one while previews are on.",
      },
      {
        id: "investigation",
        label: "Investigation",
        summary: "Two Claude builders and a reviewer, no env (for work that needs none).",
        roles: [
          { role: "project_lead", vendor: null, model: null, count: 1 },
          { role: "ic", vendor: "claude", model: null, count: 2 },
          { role: "reviewer", vendor: "codex", model: null, count: 1 },
        ],
        builders_at_once: null,
        default: false,
        members: 4,
        locked: investigation ? null : "Locked: the verify gate needs an env keeper.",
      },
    ],
    gates: [
      {
        gate: "review",
        role: "reviewer",
        text: "the review gate needs a reviewer",
        when: "always",
      },
      {
        gate: "verify",
        role: "env_keeper",
        text: "the verify gate needs an env keeper",
        when: "env",
      },
      {
        gate: "demo",
        role: "demo",
        text: "the demo gate needs one while previews are on",
        when: "previews",
      },
      { gate: "builder", role: "ic", text: "a project needs at least one builder", when: "always" },
    ],
    vendors: { claude: "Claude Code", codex: "Codex" },
    mode: options.mode ?? "enforce",
    rates_window_days: 30,
    project: {
      slug: TEAM_SLUG,
      own: true,
      env_needed: !investigation,
      previews_on: !investigation,
      remove_blocked: Object.fromEntries(
        spec.members.filter((m) => m.remove_blocked).map((m) => [m.id, m.remove_blocked!]),
      ),
    },
  };
}

// ── Previews ───────────────────────────────────────────────────

const TRIAL = {
  ok: false,
  code: "not_trialable" as const,
  reason:
    "a project's team changes would rarely reach the 3 merged PRs a trial needs (one team merges about one a day): they apply as a version",
  why: "too few merged PRs per team",
};

function member(id: string, role: string, vendor: string, model: string | null) {
  return { id, role, handle: null, vendor, agent: AGENTS[role]?.[vendor] ?? role, model };
}

function teamEffects(
  before: number,
  after: number,
  extra: DefinitionEffect[] = [],
  finish: [number, number, number] = [41, 41, 60],
): DefinitionEffect[] {
  return [
    {
      kind: "rate",
      project: TEAM_SLUG,
      before_usd_h: before,
      after_usd_h: after,
      text: `While working: $${before.toFixed(2)}/h → $${after.toFixed(2)}/h (estimate from the cost ledger).`,
    },
    {
      kind: "finish",
      project: TEAM_SLUG,
      spent_usd: 15.4,
      before_usd: finish[0],
      after_usd: finish[1],
      budget_usd: finish[2],
      text: `To finish: $${finish[0]} → $${finish[1]} of $${finish[2]}.`,
    },
    ...extra,
    {
      kind: "team_update",
      project: TEAM_SLUG,
      to: "5a000000000000000000000000000000",
      text: "Nova gets one TEAM UPDATE message with v13.",
    },
  ];
}

const REX_RESTART: DefinitionEffect = {
  kind: "restart",
  project: TEAM_SLUG,
  when: "after_current",
  members: ["M-3"],
  sessions: [],
  text: "M-3 finishes its current task, then restarts on claude · sonnet.",
};

const REX_ROUTE: DefinitionEffect = {
  kind: "review_route",
  project: TEAM_SLUG,
  text: "M-3's PRs then get a Codex review (cross-vendor rule).",
};

function preview(
  diff: DefinitionPreviewResponse["diff"],
  effects: DefinitionEffect[],
  extra: Partial<DefinitionPreviewResponse> = {},
): DefinitionPreviewResponse {
  const klass = diff.some((d) => d.class === "B") ? "B" : diff.length ? "A" : null;
  return {
    def_version: 12,
    version_next: 13,
    diff,
    lines: diff.map((d) => d.line),
    class: klass,
    approvers: klass === "A" ? ["owners", "editors"] : ["owners"],
    who:
      klass === "A"
        ? "Owners and editors (class A: it only narrows a team). You are an owner, so it applies when you press Apply."
        : klass === "B"
          ? "Owners (class B). You are an owner, so it applies when you press Apply."
          : "Owners. You are an owner, so it applies when you press Apply.",
    owner_only: false,
    you_can_apply: true,
    you_can_propose: true,
    blocked: [],
    effects,
    rerank: [],
    trialable: TRIAL,
    restore: null,
    ...extra,
  };
}

const PATH = `team.projects.${TEAM_SLUG}`;

const REX_DIFF = {
  path: `${PATH}.members.M-3`,
  before: member("M-3", "ic", "codex", null),
  after: member("M-3", "ic", "claude", "sonnet"),
  class: "B" as const,
  owner_only: false,
  block: "team.projects",
  line: `~ ${PATH}.members.M-3 · vendor codex → claude; model default → sonnet; agent ic_codex → ic`,
};

const REVIEWER_DIFF = {
  path: `${PATH}.members.M-7`,
  before: null,
  after: member("M-7", "reviewer", "claude", "opus"),
  class: "B" as const,
  owner_only: false,
  block: "team.projects",
  line: `+ ${PATH}.members.M-7 · reviewer · claude · opus`,
};

const BUDGET_DIFF = {
  path: `${PATH}.budget_usd`,
  before: 60,
  after: 80,
  class: "B" as const,
  owner_only: false,
  block: "team.projects",
  line: `${PATH}.budget_usd: 60 → 80`,
};

/** Rex moves from Codex to Claude: one owner change. */
export function previewOneChange(): DefinitionPreviewResponse {
  return preview([REX_DIFF], teamEffects(5.6, 5.9, [REX_RESTART, REX_ROUTE], [41, 43, 60]));
}

/** Rex to Claude, a second (Claude) reviewer, a higher budget. */
export function previewThreeChanges(): DefinitionPreviewResponse {
  return preview(
    [REX_DIFF, REVIEWER_DIFF, BUDGET_DIFF],
    teamEffects(5.6, 8.3, [REX_RESTART, REX_ROUTE], [41, 52, 80]),
  );
}

/** A second reviewer, alone. */
export function previewNewMember(): DefinitionPreviewResponse {
  return preview([REVIEWER_DIFF], teamEffects(5.6, 8.0, [], [41, 50, 60]));
}

/** The pair template: Rex (Codex) goes, a second Claude builder comes. */
export function previewTemplateSwitch(): DefinitionPreviewResponse {
  return preview(
    [
      {
        path: `${PATH}.template`,
        before: "standard",
        after: "pair",
        class: "A",
        owner_only: false,
        block: "team.projects",
        line: `${PATH}.template: standard → pair`,
      },
      {
        path: `${PATH}.members.M-3`,
        before: member("M-3", "ic", "codex", null),
        after: null,
        class: "A",
        owner_only: false,
        block: "team.projects",
        line: `- ${PATH}.members.M-3 · builder · codex · default`,
      },
      {
        path: `${PATH}.members.M-7`,
        before: null,
        after: member("M-7", "ic", "claude", null),
        class: "B",
        owner_only: false,
        block: "team.projects",
        line: `+ ${PATH}.members.M-7 · builder · claude · default`,
      },
    ],
    teamEffects(5.6, 5.9, [
      {
        kind: "restart",
        project: TEAM_SLUG,
        when: "after_current",
        members: ["M-3"],
        sessions: [],
        text: "M-3 finishes its current task, then leaves the team.",
      },
    ]),
  );
}

/** Removing the only env keeper (staged from elsewhere): Tier 0 refuses it. */
export function previewBlockedRemoval(): DefinitionPreviewResponse {
  return preview([], [], {
    class: null,
    you_can_apply: false,
    blocked: [
      {
        index: null,
        path: PATH,
        code: "gate_requires_role",
        message: `${TEAM_SLUG}: can't remove the last env keeper: the verify gate needs an env keeper (Tier 0)`,
        gate: "verify",
        role: "env_keeper",
      },
    ],
    trialable: { ok: false, code: "no_change", reason: "Nothing to try." },
  });
}

/** What an operator sees for owner changes: it waits for an owner. */
export function previewOperator(): DefinitionPreviewResponse {
  return {
    ...previewOneChange(),
    you_can_apply: false,
    you_can_propose: true,
    who: "Owners (class B). It waits for an owner: propose it to them.",
  };
}

// ── History ────────────────────────────────────────────────────

function ownTeam(
  seats: Seat[],
  extra: Partial<RestoredTeam> & { seq?: number } = {},
): RestoredTeam & { seq: number } {
  return {
    template: "standard",
    builders_at_once: 2,
    budget_usd: 60,
    members: seats.map(([id, role, vendor, model]) => member(id, role, vendor, model)),
    briefs: { "M-2": "(sample) Run the export tests before every push." },
    seq: 6,
    ...extra,
  };
}

function version(
  n: number,
  team: RestoredTeam | null,
  options: { at: number; by?: string; ref?: string; undone?: boolean; pinned?: boolean },
): DefinitionView {
  return {
    factory_id: "f",
    version: n,
    parent_version: n - 1,
    hash: `${n}`.repeat(64).slice(0, 64),
    created_by: options.by ?? OWNER,
    created_by_title: null,
    created_via: "human",
    proposal_id: null,
    proposal_ref: options.ref ?? null,
    created_at: options.at,
    pinned: options.pinned ?? false,
    doc: team ? { team: { projects: { [TEAM_SLUG]: team } } } : {},
    undone_by: options.undone ? OWNER : null,
    undone_at: options.undone ? options.at + 120 : null,
  };
}

/**
 * v9 (no team of its own) → v10 its own standard team → v11 one builder at
 * once → v12 two builders at once, in force; with `justApplied`, v13 (Rex to
 * Claude) is in force and made two minutes ago, so it can still be undone.
 */
export function teamVersions(options: { justApplied?: boolean } = {}): DefinitionVersionsResponse {
  const standard = SHAPES.standard;
  const rows = [
    version(9, null, { at: TEAM_NOW_S - 30 * H, ref: "C-2" }),
    version(10, ownTeam(standard, { builders_at_once: 1, briefs: {} }), {
      at: TEAM_NOW_S - 26 * H,
      ref: "C-3",
    }),
    version(11, ownTeam(standard, { builders_at_once: 1 }), {
      at: TEAM_NOW_S - 20 * H,
      ref: "C-4",
    }),
    version(12, ownTeam(standard), {
      at: TEAM_NOW_S - 3 * H,
      ref: "C-5",
      pinned: !options.justApplied,
    }),
  ];
  if (options.justApplied) {
    const moved = standard.map((s): Seat =>
      s[0] === "M-3" ? ["M-3", "ic", "claude", "sonnet", "sonnet"] : s,
    );
    rows.push(version(13, ownTeam(moved), { at: TEAM_NOW_S - 120, ref: "C-6", pinned: true }));
  }
  return { data: rows.reverse(), pinned: options.justApplied ? 13 : 12 };
}

/** `GET …/definition` with the sample project's own team in its effective document. */
export function teamDefinition(shape: TeamShape = "standard"): DefinitionResponse {
  const response = definitionResponse();
  const seats = SHAPES[shape];
  return {
    ...response,
    def_version: 12,
    effective: {
      ...response.effective,
      team: { projects: { [TEAM_SLUG]: ownTeam(seats, { template: shape }) } },
    } as unknown as DefinitionResponse["effective"],
  };
}

// ── Routes ─────────────────────────────────────────────────────

export interface TeamRouteOptions {
  shape?: TeamShape;
  mode?: "warn" | "enforce";
  preview?: DefinitionPreviewResponse;
  justApplied?: boolean;
}

/** `/v1/factory/*` for the team chart and editor over `snapshot` (Teams' routes, plus the team's). */
export function teamRoutes(snapshot: FactorySnapshot, options: TeamRouteOptions = {}): StoryRoutes {
  const base = `/v1/factory/factories/${snapshot.factory.id}`;
  return {
    ...teamsRoutes(snapshot),
    [`${base}/catalog`]: { body: teamCatalog({ mode: options.mode, shape: options.shape }) },
    [`${base}/definition`]: { body: teamDefinition(options.shape) },
    [`${base}/definition/versions`]: { body: teamVersions({ justApplied: options.justApplied }) },
    [`${base}/definition/preview`]: { body: options.preview ?? previewOneChange() },
    [`${base}/projects/${TEAM_SLUG}/demos`]: { body: { data: [], count: 0 } },
  };
}
