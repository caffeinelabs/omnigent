// A project's team as data, for the team chart and the team editor (spec
// §6.1–6.2): the groups a chart draws (the project lead on top, then Build,
// Check and Run), the staged changes a person makes in the editor and the
// definition changes they compile to (`PUT …/definition` and its preview), the
// team those changes would leave (what each tile shows: STAGED, NEW, REMOVE),
// which removals a gate locks, the vendor · model options of a role with their
// bench rates, the live sessions behind each member, and the team's history
// across definition versions (with the change that restores an earlier team).
//
// Pure: no queries and no store. The server stays the judge of cost, class and
// Tier 0: the staged panel reads them from the preview; what this module works
// out (tags, locks, options) only draws the chart before the preview answers.

import type {
  CatalogResponse,
  CatalogRole,
  CatalogVendor,
  DefinitionChange,
  DefinitionView,
  TeamChangeWhen,
  TeamMemberSpecView,
  TeamSpecView,
  TeamTemplateView,
} from "@/lib/factoryApi";
import type { BenchResponse, BenchRowView } from "@/lib/factoryTeamsApi";
import { modelLabel, type TeamMember } from "../agents/teamsModel";

// ── Roles and groups ───────────────────────────────────────────

/** The roles a project team is made of (the PL bundle's), lead first. */
export const TEAM_ROLES = ["project_lead", "ic", "reviewer", "env_keeper", "demo"] as const;
export type TeamRole = (typeof TEAM_ROLES)[number];

export type TeamGroupKey = "build" | "check" | "run";

export interface TeamGroupInfo {
  key: TeamGroupKey;
  label: string;
  roles: readonly TeamRole[];
  /** The ghost tile's words in the editor. */
  add: string;
}

/** The chart's groups under the lead: who builds, who checks, who runs. */
export const TEAM_GROUPS: readonly TeamGroupInfo[] = [
  { key: "build", label: "Build", roles: ["ic"], add: "Add a builder" },
  { key: "check", label: "Check", roles: ["reviewer", "demo"], add: "Add a reviewer or demo" },
  { key: "run", label: "Run", roles: ["env_keeper"], add: "Add an env keeper" },
];

/** Tile labels (v2 words: a builder, not an "ic"). */
export const TEAM_ROLE_LABEL: Record<TeamRole, string> = {
  project_lead: "Project lead",
  ic: "Builder",
  reviewer: "Reviewer",
  env_keeper: "Env keeper",
  demo: "Demo",
};

/** "a builder", "an env keeper": a role after "add" or "the last". */
export function roleNoun(role: string): string {
  const label = (TEAM_ROLE_LABEL[role as TeamRole] ?? role).toLowerCase();
  return /^[aeiou]/.test(label) ? `an ${label}` : `a ${label}`;
}

export function isTeamRole(role: string | null | undefined): role is TeamRole {
  return (TEAM_ROLES as readonly string[]).includes(role ?? "");
}

/** The group a role sits in, or `lead`. */
export function groupOf(role: string): TeamGroupKey | "lead" {
  if (role === "project_lead") return "lead";
  return TEAM_GROUPS.find((g) => (g.roles as readonly string[]).includes(role))?.key ?? "build";
}

const ROLE_RANK = new Map<string, number>(TEAM_ROLES.map((r, i) => [r, i]));

/** `M-3` → 3 (0 for anything else). */
export function memberNumber(id: string | null | undefined): number {
  const match = /^M-(\d+)$/.exec(id ?? "");
  return match ? Number(match[1]) : 0;
}

// ── The team a change starts from ──────────────────────────────

/** One member of the team in force. */
export interface BaseMember {
  id: string;
  role: string;
  handle: string | null;
  vendor: string;
  model: string | null;
  /** The model it runs: its own, else its bundle's default. */
  modelLabel: string | null;
  brief: string | null;
  removeBlocked: string | null;
}

/** The team in force, as the editor starts from it. */
export interface TeamBase {
  slug: string;
  /** The project has its own team (else it follows today's bundle). */
  own: boolean;
  template: string | null;
  builders: number;
  budget: number;
  members: BaseMember[];
}

/**
 * @param briefs - The members' brief texts (the definition's
 *   `team.projects.<slug>.briefs`; the snapshot says only whether one exists).
 */
export function teamBase(
  slug: string,
  spec: TeamSpecView,
  briefs: Record<string, string> = {},
): TeamBase {
  return {
    slug,
    own: spec.own,
    template: spec.template,
    builders: spec.builders_at_once,
    budget: spec.budget_usd,
    members: spec.members.map((m) => ({
      id: m.id,
      role: m.role,
      handle: m.handle,
      vendor: m.vendor,
      model: m.model,
      modelLabel: m.model_label ?? m.model,
      brief: briefs[m.id] ?? (m.brief ? "" : null),
      removeBlocked: m.remove_blocked,
    })),
  };
}

// ── Catalog lookups ────────────────────────────────────────────

export function catalogRole(
  catalog: CatalogResponse | null | undefined,
  role: string,
): CatalogRole | null {
  return catalog?.roles.find((r) => r.role === role) ?? null;
}

export function catalogVendor(
  catalog: CatalogResponse | null | undefined,
  role: string,
  vendor: string,
): CatalogVendor | null {
  return catalogRole(catalog, role)?.vendors.find((v) => v.vendor === vendor) ?? null;
}

/** The model a member on `vendor` runs when it names none (its bundle's default). */
export function defaultModel(
  catalog: CatalogResponse | null | undefined,
  role: string,
  vendor: string,
): string | null {
  return catalogVendor(catalog, role, vendor)?.default_model ?? null;
}

/** The role's first vendor it can run on (its default). */
export function defaultVendor(catalog: CatalogResponse | null | undefined, role: string): string {
  const found = catalogRole(catalog, role)?.vendors.find((v) => v.available);
  if (found) return found.vendor;
  return role === "reviewer" ? "codex" : "claude";
}

/** `null` and the bundle's default name the same model. */
export function sameModel(
  catalog: CatalogResponse | null | undefined,
  role: string,
  a: { vendor: string; model: string | null },
  b: { vendor: string; model: string | null },
): boolean {
  if (a.vendor !== b.vendor) return false;
  const fallback = defaultModel(catalog, role, a.vendor);
  return (a.model ?? fallback) === (b.model ?? fallback);
}

// ── Staged changes ─────────────────────────────────────────────

/** A team member's earlier team, as a restore puts it back (`team.projects.<slug>` of vK). */
export interface RestoredTeam {
  template?: string | null;
  builders_at_once?: number | null;
  budget_usd?: number | null;
  members: {
    id: string;
    role: string;
    handle?: string | null;
    vendor?: string | null;
    agent?: string | null;
    model?: string | null;
  }[];
  briefs?: Record<string, string>;
}

/**
 * One change a person staged. `member` is a member id (`M-3`); an `add`
 * carries its own `key` until the server numbers it on Apply.
 */
export type StagedTeamChange =
  | { kind: "model"; member: string; vendor: string; model: string | null }
  | { kind: "add"; key: string; role: string; vendor: string; model: string | null }
  | { kind: "remove"; member: string }
  | { kind: "builders"; value: number }
  | { kind: "budget"; value: number }
  | { kind: "template"; value: string }
  | { kind: "brief"; member: string; text: string }
  | {
      kind: "restore";
      version: number;
      /** The team as vK had it; `null`: vK's project followed today's bundle. */
      team: RestoredTeam | null;
    };

export type TeamScope = "project" | "template";

/** Everything staged on one project's team, and how it would apply. */
export interface TeamStaging {
  changes: StagedTeamChange[];
  when: TeamChangeWhen;
  /** Also write the team's shape into its template (owners; new projects only). */
  scope: TeamScope;
}

export const EMPTY_STAGING: TeamStaging = { changes: [], when: "after_current", scope: "project" };

export const BUILDERS_MIN = 1;
export const BUILDERS_MAX = 10;
export const BUDGET_MIN = 1;
export const BUDGET_MAX = 1000;
export const BRIEF_MAX = 1000;

/** A new member's key: unique among the staged adds. */
export function nextAddKey(staging: TeamStaging): string {
  const used = staging.changes
    .filter((c): c is Extract<StagedTeamChange, { kind: "add" }> => c.kind === "add")
    .map((c) => Number(/^new-(\d+)$/.exec(c.key)?.[1] ?? 0));
  return `new-${Math.max(0, ...used) + 1}`;
}

function sameKind(a: StagedTeamChange, b: StagedTeamChange): boolean {
  if (a.kind !== b.kind) return false;
  switch (a.kind) {
    case "model":
    case "brief":
    case "remove":
      return a.member === (b as typeof a).member;
    case "add":
      return a.key === (b as typeof a).key;
    default:
      return true;
  }
}

/** Put `change` where an earlier change of the same kind and subject sat, or at the end. */
function upsert(list: StagedTeamChange[], change: StagedTeamChange): StagedTeamChange[] {
  const at = list.findIndex((c) => sameKind(c, change));
  if (at < 0) return [...list, change];
  return list.map((c, i) => (i === at ? change : c));
}

function without(list: StagedTeamChange[], change: StagedTeamChange): StagedTeamChange[] {
  return list.filter((c) => !sameKind(c, change));
}

/** Whether a restore is staged: it replaces the whole team, so nothing else stages with it. */
export function hasRestore(staging: TeamStaging): boolean {
  return staging.changes.some((c) => c.kind === "restore");
}

/**
 * Stage `change` on top of `staging`. A change that puts a value back to what
 * the team in force has drops out; a second change to the same thing replaces
 * the first; removing a member drops its other changes; removing a new member
 * drops it. A restore replaces everything staged, and nothing stages beside it.
 */
export function stageChange(
  staging: TeamStaging,
  change: StagedTeamChange,
  base: TeamBase,
  catalog?: CatalogResponse | null,
): TeamStaging {
  if (change.kind === "restore") return { ...staging, changes: [change] };
  if (hasRestore(staging)) return staging;
  let list = staging.changes;
  const member = "member" in change ? base.members.find((m) => m.id === change.member) : null;
  switch (change.kind) {
    case "model": {
      const added = list.find(
        (c): c is Extract<StagedTeamChange, { kind: "add" }> =>
          c.kind === "add" && c.key === change.member,
      );
      if (added) {
        list = upsert(list, { ...added, vendor: change.vendor, model: change.model });
        break;
      }
      if (member && sameModel(catalog, member.role, member, change)) list = without(list, change);
      else list = upsert(list, change);
      break;
    }
    case "add":
      list = [...list, change];
      break;
    case "remove": {
      const added = list.find((c) => c.kind === "add" && c.key === change.member);
      if (added) {
        list = list.filter((c) => c !== added);
        break;
      }
      if (list.some((c) => sameKind(c, change))) {
        list = without(list, change);
        break;
      }
      list = [
        ...list.filter(
          (c) => !((c.kind === "model" || c.kind === "brief") && c.member === change.member),
        ),
        change,
      ];
      break;
    }
    case "builders":
      list = change.value === base.builders ? without(list, change) : upsert(list, change);
      break;
    case "budget":
      list = change.value === base.budget ? without(list, change) : upsert(list, change);
      break;
    case "template":
      list = change.value === base.template ? without(list, change) : upsert(list, change);
      break;
    case "brief": {
      const was = (member?.brief ?? "").trim();
      list = change.text.trim() === was ? without(list, change) : upsert(list, change);
      break;
    }
  }
  return { ...staging, changes: list };
}

/**
 * Fold changes staged from outside the editor (the palette appends them raw)
 * into one consistent list, as if each had been staged in the editor; adds
 * get keys when they have none.
 */
export function normalizeStaging(
  staging: TeamStaging,
  base: TeamBase,
  catalog?: CatalogResponse | null,
): TeamStaging {
  let out: TeamStaging = { ...staging, changes: [] };
  for (const change of staging.changes) {
    const keyed =
      change.kind === "add" &&
      (!change.key || out.changes.some((c) => c.kind === "add" && c.key === change.key))
        ? { ...change, key: nextAddKey(out) }
        : change;
    out = stageChange(out, keyed, base, catalog);
  }
  return out;
}

/** Drop the staged change at `index` (the panel's ×). */
export function dropChange(staging: TeamStaging, index: number): TeamStaging {
  return { ...staging, changes: staging.changes.filter((_, i) => i !== index) };
}

/** What Apply and Undo leave: nothing staged (Undo never puts changes back as staged). */
export function clearedStaging(staging: TeamStaging): TeamStaging {
  return { ...staging, changes: [] };
}

// ── The team after the staged changes ──────────────────────────

/** What the editor draws for one seat. */
export interface EditTile {
  /** The member id, or a new member's key. */
  key: string;
  /** `M-n` of a member in force (`null` for a new one). */
  id: string | null;
  role: string;
  handle: string | null;
  vendor: string;
  model: string | null;
  /** NEW (added), STAGED (vendor, model or brief changed), REMOVE, or as it is. */
  tag: "new" | "staged" | "remove" | null;
  /** The vendor · model it runs today, when the staged team changes it. */
  before: { vendor: string; model: string | null } | null;
  /** A brief is staged for it. */
  briefStaged: boolean;
  /** Added by a template switch or a restore: no change of its own until Apply. */
  locked: "template" | "restore" | null;
  /** The staged change that made it new or removed it, if one did. */
  changeIndex: number | null;
}

export interface StagedTeam {
  tiles: EditTile[];
  template: string | null;
  builders: number;
  budget: number;
}

interface Working extends EditTile {
  /** It sits on the team after the changes (else REMOVE). */
  on: boolean;
  /** Order of joining: members by number, new ones after. */
  order: number;
}

function vendorOf(
  catalog: CatalogResponse | null | undefined,
  role: string,
  vendor: string | null | undefined,
): string {
  return vendor ?? defaultVendor(catalog, role);
}

/**
 * The team the staged changes leave, change by change in staging order (as the
 * server applies them): a template switch keeps the members that fit a seat
 * of the template (by role and vendor, oldest first), adds the missing seats
 * and removes the rest, like the server's `switch_template`.
 */
export function stagedTeam(
  base: TeamBase,
  staging: TeamStaging,
  catalog?: CatalogResponse | null,
): StagedTeam {
  let working: Working[] = base.members.map((m) => ({
    key: m.id,
    id: m.id,
    role: m.role,
    handle: m.handle,
    vendor: m.vendor,
    model: m.model,
    tag: null,
    before: null,
    briefStaged: false,
    locked: null,
    changeIndex: null,
    on: true,
    order: memberNumber(m.id),
  }));
  let template = base.template;
  let builders = base.builders;
  let budget = base.budget;
  let order = 1_000;
  staging.changes.forEach((change, index) => {
    switch (change.kind) {
      case "model": {
        const tile = working.find((w) => w.key === change.member && w.on);
        if (tile) {
          tile.vendor = change.vendor;
          tile.model = change.model;
        }
        break;
      }
      case "add":
        working.push({
          key: change.key,
          id: null,
          role: change.role,
          handle: null,
          vendor: change.vendor,
          model: change.model,
          tag: "new",
          before: null,
          briefStaged: false,
          locked: null,
          changeIndex: index,
          on: true,
          order: (order += 1),
        });
        break;
      case "remove": {
        const tile = working.find((w) => w.key === change.member && w.on);
        if (tile) {
          tile.on = false;
          tile.changeIndex = index;
        }
        break;
      }
      case "builders":
        builders = change.value;
        break;
      case "budget":
        budget = change.value;
        break;
      case "brief": {
        const tile = working.find((w) => w.key === change.member && w.on);
        if (tile) tile.briefStaged = true;
        break;
      }
      case "template": {
        const shape = catalog?.templates.find((t) => t.id === change.value);
        template = change.value;
        if (!shape) break;
        const live = working.filter((w) => w.on).sort((a, b) => a.order - b.order);
        const kept = new Set<string>();
        for (const entry of shape.roles) {
          const vendor = vendorOf(catalog, entry.role, entry.vendor);
          const fits = live
            .filter((w) => !kept.has(w.key) && w.role === entry.role && w.vendor === vendor)
            .slice(0, entry.count);
          for (const w of fits) kept.add(w.key);
          for (let n = fits.length; n < entry.count; n += 1) {
            const key = `tpl-${index}-${entry.role}-${vendor}-${n}`;
            working.push({
              key,
              id: null,
              role: entry.role,
              handle: null,
              vendor,
              model: entry.model ?? null,
              tag: "new",
              before: null,
              briefStaged: false,
              locked: "template",
              changeIndex: index,
              on: true,
              order: (order += 1),
            });
            kept.add(key);
          }
        }
        for (const w of live) {
          if (!kept.has(w.key)) {
            w.on = false;
            w.changeIndex = index;
          }
        }
        if (shape.builders_at_once != null) builders = shape.builders_at_once;
        break;
      }
      case "restore": {
        const team = change.team;
        const restored = team?.members ?? implicitMembers(catalog);
        const current = new Map(working.filter((w) => w.on).map((w) => [w.id, w]));
        const next: Working[] = [];
        for (const m of restored) {
          const vendor = vendorOf(catalog, m.role, m.vendor);
          const held = m.id ? current.get(m.id) : undefined;
          if (held && held.role === m.role) {
            held.vendor = vendor;
            held.model = m.model ?? null;
            held.handle = m.handle ?? null;
            next.push(held);
            current.delete(m.id);
          } else {
            next.push({
              key: `restore-${index}-${next.length}`,
              id: null,
              role: m.role,
              handle: m.handle ?? null,
              vendor,
              model: m.model ?? null,
              tag: "new",
              before: null,
              briefStaged: false,
              locked: "restore",
              changeIndex: index,
              on: true,
              order: (order += 1),
            });
          }
        }
        for (const gone of current.values()) {
          gone.on = false;
          gone.changeIndex = index;
          next.push(gone);
        }
        working = [...next, ...working.filter((w) => !w.on && !next.includes(w))];
        template = team ? (team.template ?? null) : null;
        if (team?.builders_at_once != null) builders = team.builders_at_once;
        if (team?.budget_usd != null) budget = team.budget_usd;
        break;
      }
    }
  });
  const byId = new Map(base.members.map((m) => [m.id, m]));
  const tiles = working.map((w): EditTile => {
    const was = w.id ? byId.get(w.id) : undefined;
    const moved = was != null && !sameModel(catalog, w.role, was, w);
    const tag = !w.on ? "remove" : w.id == null ? "new" : moved || w.briefStaged ? "staged" : null;
    const { on: _on, order: _order, ...tile } = w;
    return {
      ...tile,
      tag,
      before: moved && was ? { vendor: was.vendor, model: was.modelLabel } : null,
    };
  });
  tiles.sort((a, b) => (ROLE_RANK.get(a.role) ?? 9) - (ROLE_RANK.get(b.role) ?? 9));
  return { tiles, template, builders, budget };
}

/** Today's bundle as members (what a project without its own team runs). */
function implicitMembers(catalog: CatalogResponse | null | undefined): RestoredTeam["members"] {
  const standard =
    catalog?.templates.find((t) => t.id === "standard") ??
    catalog?.templates.find((t) => t.default);
  const roles: { role: string; vendor: string | null; model: string | null; count: number }[] =
    standard?.roles ?? [
      { role: "project_lead", vendor: null, model: null, count: 1 },
      { role: "ic", vendor: "claude", model: null, count: 1 },
      { role: "ic", vendor: "codex", model: null, count: 1 },
      { role: "reviewer", vendor: "codex", model: null, count: 1 },
      { role: "env_keeper", vendor: null, model: null, count: 1 },
      { role: "demo", vendor: null, model: null, count: 1 },
    ];
  const out: RestoredTeam["members"] = [];
  for (const entry of roles) {
    for (let n = 0; n < entry.count; n += 1) {
      out.push({
        id: `M-${out.length + 1}`,
        role: entry.role,
        vendor: entry.vendor,
        model: entry.model,
      });
    }
  }
  return out;
}

/** The tiles on the team after the changes (REMOVE ones left out). */
export function seated(team: StagedTeam): EditTile[] {
  return team.tiles.filter((t) => t.tag !== "remove");
}

// ── Gates: removals a gate locks ───────────────────────────────

/** Whether the project's charter needs an env and has previews on (strict when unknown). */
export interface TeamFacts {
  envNeeded: boolean;
  previewsOn: boolean;
}

export function teamFacts(catalog: CatalogResponse | null | undefined): TeamFacts {
  return {
    envNeeded: catalog?.project?.env_needed ?? true,
    previewsOn: catalog?.project?.previews_on ?? true,
  };
}

const GATE_FALLBACK: CatalogResponse["gates"] = [
  { gate: "review", role: "reviewer", text: "the review gate needs a reviewer", when: "always" },
  { gate: "verify", role: "env_keeper", text: "the verify gate needs an env keeper", when: "env" },
  {
    gate: "demo",
    role: "demo",
    text: "the demo gate needs one while previews are on",
    when: "previews",
  },
  { gate: "builder", role: "ic", text: "a project needs at least one builder", when: "always" },
];

function gateApplies(when: string, facts: TeamFacts): boolean {
  if (when === "env") return facts.envNeeded;
  if (when === "previews") return facts.previewsOn;
  return true;
}

/**
 * Why `tile` cannot be removed from the staged team, or null. The project
 * lead always stays; the last member a gate needs is locked (Tier 0), so a
 * second reviewer unlocks removing the first.
 */
export function removeLock(
  tile: EditTile,
  team: StagedTeam,
  facts: TeamFacts,
  catalog?: CatalogResponse | null,
): string | null {
  if (tile.role === "project_lead") return "The project lead stays: every project has one.";
  if (tile.tag === "remove" || tile.tag === "new") return null;
  const need = (catalog?.gates ?? GATE_FALLBACK).find(
    (g) => g.role === tile.role && gateApplies(g.when, facts),
  );
  if (!need) return null;
  const left = seated(team).filter((t) => t.role === tile.role).length;
  return left <= 1 ? `Can't remove: ${need.text} (Tier 0)` : null;
}

// ── Vendor · model options ─────────────────────────────────────

/** `opus`, `gpt-5-codex`: a bench row's or member's model in the bench's words. */
function benchModelKey(model: string | null | undefined): string | null {
  return modelLabel(model) ?? null;
}

/** The bench row of `role` on `vendor` · `model`, if the bench has one. */
export function benchFor(
  bench: BenchResponse | null | undefined,
  role: string,
  vendor: string,
  model: string | null,
): BenchRowView | null {
  const key = benchModelKey(model);
  return (
    bench?.rows.find(
      (r) => r.role === role && r.vendor === vendor && benchModelKey(r.model) === key,
    ) ?? null
  );
}

/** `75 %`, `75 % (low n)`; null without a quality figure. */
export function benchRate(row: BenchRowView | null): string | null {
  if (!row?.quality) return null;
  return `${Math.round(row.quality.value * 100)} %${row.low_n ? " (low n)" : ""}`;
}

/** "Bench: 75 % first-pass review, n 12", or null. */
export function benchLine(row: BenchRowView | null): string | null {
  if (!row?.quality) return null;
  return `Bench: ${Math.round(row.quality.value * 100)} % ${row.quality.label}, n ${row.quality.n}${
    row.low_n ? " (low n)" : ""
  }`;
}

/** One option of a tile's vendor · model select. */
export interface ModelOption {
  /** `claude|sonnet`; `claude|` is the bundle's default. */
  value: string;
  vendor: string;
  model: string | null;
  label: string;
  disabled: boolean;
}

export function optionValue(vendor: string, model: string | null): string {
  return `${vendor}|${model ?? ""}`;
}

export function parseOption(value: string): { vendor: string; model: string | null } {
  const [vendor = "", model = ""] = value.split("|");
  return { vendor, model: model || null };
}

/**
 * What `role` can run, each with its bench rate ("codex · gpt-5-codex · 75 %");
 * the bundle's default model is `null` (it follows the bundle), and a vendor
 * the role cannot run is listed disabled.
 */
export function modelOptions(
  catalog: CatalogResponse | null | undefined,
  bench: BenchResponse | null | undefined,
  role: string,
): ModelOption[] {
  const entry = catalogRole(catalog, role);
  if (!entry) return [];
  const out: ModelOption[] = [];
  for (const vendor of entry.vendors) {
    if (!vendor.available) {
      out.push({
        value: optionValue(vendor.vendor, null),
        vendor: vendor.vendor,
        model: null,
        label: `${vendor.vendor} · not available for this role`,
        disabled: true,
      });
      continue;
    }
    const models = vendor.models ?? [];
    const fallback = vendor.default_model ?? null;
    const named = [fallback, ...models.filter((m) => m !== fallback)];
    for (const model of named) {
      const value = model === fallback ? null : model;
      const rate = benchRate(benchFor(bench, role, vendor.vendor, model));
      const name = model ?? "default";
      out.push({
        value: optionValue(vendor.vendor, value),
        vendor: vendor.vendor,
        model: value,
        label: `${vendor.vendor} · ${name}${rate ? ` · ${rate}` : ""}`,
        disabled: false,
      });
    }
  }
  return out;
}

/** The option a tile shows as selected (its model, or the default it runs). */
export function selectedOption(
  catalog: CatalogResponse | null | undefined,
  role: string,
  vendor: string,
  model: string | null,
): string {
  const fallback = defaultModel(catalog, role, vendor);
  return optionValue(vendor, model === fallback ? null : model);
}

/** The words a tile says when the role cannot run a vendor ("Codex is not available…"). */
export function vendorLocks(catalog: CatalogResponse | null | undefined, role: string): string[] {
  return (catalogRole(catalog, role)?.vendors ?? [])
    .filter((v) => !v.available && v.reason)
    .map((v) => sentence(v.reason!));
}

function sentence(text: string): string {
  const trimmed = text.trim();
  const capital = trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
  return /[.!?]$/.test(capital) ? capital : `${capital}.`;
}

/** `claude · sonnet` (`default` when the bundle's model is not known). */
export function runsOn(
  catalog: CatalogResponse | null | undefined,
  role: string,
  vendor: string,
  model: string | null,
): string {
  return `${vendor} · ${model ?? defaultModel(catalog, role, vendor) ?? "default"}`;
}

// ── Compiling to definition changes ────────────────────────────

/** `team.projects.<slug>` */
export function teamPath(slug: string): string {
  return `team.projects.${slug}`;
}

/**
 * The template roles of a staged team: one entry per role, vendor and model,
 * with a count (a model that is its bundle's default counts as the default).
 */
export function templateRoles(
  team: StagedTeam,
  catalog?: CatalogResponse | null,
): TeamTemplateView["roles"] {
  const out: TeamTemplateView["roles"] = [];
  for (const tile of seated(team)) {
    const model = tile.model === defaultModel(catalog, tile.role, tile.vendor) ? null : tile.model;
    const found = out.find(
      (r) => r.role === tile.role && r.vendor === tile.vendor && r.model === model,
    );
    if (found) found.count += 1;
    else out.push({ role: tile.role, vendor: tile.vendor, model, count: 1 });
  }
  return out;
}

/**
 * The `changes[]` of `PUT …/definition` (and its preview) for what is staged,
 * in staging order. With `scope: "template"` the team's shape is also written
 * into its template (`team.templates.<id>.roles`), which reaches new projects only.
 */
export function definitionChanges(
  slug: string,
  staging: TeamStaging,
  team?: StagedTeam,
  catalog?: CatalogResponse | null,
): DefinitionChange[] {
  const base = teamPath(slug);
  const out: DefinitionChange[] = staging.changes.map((change): DefinitionChange => {
    switch (change.kind) {
      case "model":
        return {
          op: "replace",
          path: `${base}.members.${change.member}`,
          value: { vendor: change.vendor, model: change.model },
        };
      case "add":
        return {
          op: "add",
          path: `${base}.members`,
          value: { role: change.role, vendor: change.vendor, model: change.model },
        };
      case "remove":
        return { op: "remove", path: `${base}.members.${change.member}` };
      case "builders":
        return { op: "replace", path: `${base}.builders_at_once`, value: change.value };
      case "budget":
        return { op: "replace", path: `${base}.budget_usd`, value: change.value };
      case "template":
        return { op: "replace", path: `${base}.template`, value: change.value };
      case "brief":
        return change.text.trim()
          ? { op: "replace", path: `${base}.briefs.${change.member}`, value: change.text.trim() }
          : { op: "remove", path: `${base}.briefs.${change.member}` };
      case "restore":
        return change.team
          ? { op: "replace", path: base, value: change.team }
          : { op: "remove", path: base };
    }
  });
  if (staging.scope === "template" && team?.template && out.length) {
    out.push({
      op: "replace",
      path: `team.templates.${team.template}.roles`,
      value: templateRoles(team, catalog),
    });
  }
  return out;
}

// ── Words for the staged panel ─────────────────────────────────

/** What to call a member: its handle, else its role and number ("Builder M-3"). */
export function memberName(
  member: Pick<BaseMember, "id" | "role" | "handle"> | null | undefined,
  names?: ReadonlyMap<string, string>,
): string {
  if (!member) return "The new member";
  const known = names?.get(member.id);
  if (member.handle) return member.handle;
  if (known) return known;
  return `${TEAM_ROLE_LABEL[member.role as TeamRole] ?? member.role} ${member.id}`;
}

/** One staged change in words: title, before → after, and what it does. */
export interface ChangeWords {
  title: string;
  before: string | null;
  after: string | null;
  effect: string;
  /** The diff path the preview classes it by (adds have none until numbered). */
  path: string | null;
}

/**
 * @param names - Member id → the name the chart shows (handles from live sessions).
 */
export function changeWords(
  change: StagedTeamChange,
  context: {
    slug: string;
    base: TeamBase;
    catalog?: CatalogResponse | null;
    bench?: BenchResponse | null;
    when: TeamChangeWhen;
    names?: ReadonlyMap<string, string>;
  },
): ChangeWords {
  const { slug, base, catalog, bench, when, names } = context;
  const path = teamPath(slug);
  const member =
    "member" in change ? (base.members.find((m) => m.id === change.member) ?? null) : null;
  const name = memberName(member, names);
  const role = member ? (TEAM_ROLE_LABEL[member.role as TeamRole] ?? member.role) : "";
  const money = (n: number) => `$${Math.round(n)}`;
  switch (change.kind) {
    case "model": {
      const was = member ? runsOn(catalog, member.role, member.vendor, member.model) : null;
      const now = runsOn(catalog, member?.role ?? "", change.vendor, change.model);
      const lead =
        when === "now" ? `${name} is interrupted now and` : `${name} finishes its current task,`;
      const route =
        member?.role === "ic" && member.vendor !== change.vendor
          ? ` Its PRs then get a ${change.vendor === "codex" ? "Claude" : "Codex"} review (cross-vendor rule).`
          : "";
      const oldRow = member ? benchFor(bench, member.role, member.vendor, member.modelLabel) : null;
      const newRow = benchFor(
        bench,
        member?.role ?? "",
        change.vendor,
        change.model ?? defaultModel(catalog, member?.role ?? "", change.vendor),
      );
      const record =
        oldRow?.quality && newRow?.quality
          ? ` Bench: ${Math.round(newRow.quality.value * 100)} % on ${
              now.split(" · ")[1]
            } vs ${Math.round(oldRow.quality.value * 100)} % on ${was?.split(" · ")[1]} (n ${
              newRow.quality.n
            } vs ${oldRow.quality.n}).`
          : "";
      return {
        title: `${name} (${role}): vendor and model`,
        before: was,
        after: now,
        effect: `${lead} then restarts on ${now}.${route}${record}`,
        path: member ? `${path}.members.${member.id}` : null,
      };
    }
    case "add": {
      const noun = roleNoun(change.role);
      const does = catalogRole(catalog, change.role)?.does;
      return {
        title: `Add ${noun}`,
        before: null,
        after: runsOn(catalog, change.role, change.vendor, change.model),
        effect:
          change.role === "reviewer"
            ? "Every PR gets a second, independent review before it can be ready."
            : change.role === "ic"
              ? "One more builder can take a ready task."
              : (does ?? "Joins after you apply."),
        path: null,
      };
    }
    case "remove":
      return {
        title: `Remove ${name} (${role})`,
        before: member ? runsOn(catalog, member.role, member.vendor, member.model) : null,
        after: null,
        effect:
          when === "now"
            ? `${name} is interrupted now and leaves the team.`
            : `${name} finishes its current task, then leaves the team.`,
        path: member ? `${path}.members.${member.id}` : null,
      };
    case "builders":
      return {
        title: "Builders at once",
        before: String(base.builders),
        after: String(change.value),
        effect:
          change.value > base.builders
            ? "More tasks run in parallel when they are ready."
            : "Fewer tasks run in parallel.",
        path: `${path}.builders_at_once`,
      };
    case "budget":
      return {
        title: "Project budget",
        before: money(base.budget),
        after: money(change.value),
        effect:
          change.value > base.budget
            ? "The project may spend more before it stops for a person."
            : "The project stops for a person sooner.",
        path: `${path}.budget_usd`,
      };
    case "template": {
      const shape = catalog?.templates.find((t) => t.id === change.value);
      const was = catalog?.templates.find((t) => t.id === base.template);
      return {
        title: "Template",
        before: was?.label ?? base.template ?? "Today's bundle",
        after: shape?.label ?? change.value,
        effect: `${shape?.summary ? `${shape.summary} ` : ""}Members that fit a seat stay; the rest are removed and missing seats are added.`,
        path: `${path}.template`,
      };
    }
    case "brief":
      return {
        title: `Instructions for ${name} on this project`,
        before: null,
        after: change.text.trim() ? `"${clip(change.text.trim(), 120)}"` : "cleared",
        effect:
          "Added to the role's brief for this project only. It cannot replace guards or protocol.",
        path: member ? `${path}.briefs.${member.id}` : null,
      };
    case "restore":
      return {
        title: `Restore the team of v${change.version}`,
        before: null,
        after: change.team ? `v${change.version}'s team` : "today's bundle",
        effect:
          "Puts this project's team back as it was in that version; nothing else in the definition changes. It applies as a new version you can undo.",
        path: path,
      };
  }
}

function clip(text: string, n: number): string {
  return text.length > n ? `${text.slice(0, n - 1)}…` : text;
}

// ── Live sessions behind each member ───────────────────────────

/** The role a live session plays on the team (a Claude review runs as a builder agent). */
export function liveRole(member: Pick<TeamMember, "row">): string {
  const { role, title } = member.row;
  if (role === "ic" && /^review-/i.test(title)) return "reviewer";
  if (role === "ic" && /^demo-/i.test(title)) return "demo";
  return role ?? "agent";
}

const ACTIVE_STATES = new Set(["working", "waiting", "stuck", "stopped"]);

function relevance(a: TeamMember, b: TeamMember): number {
  const active = Number(ACTIVE_STATES.has(b.state)) - Number(ACTIVE_STATES.has(a.state));
  return active || (b.row.last_activity_at ?? 0) - (a.row.last_activity_at ?? 0);
}

/** One member of the chart with what runs it now. */
export interface ChartMember {
  /** The member in the team spec (null on a server without team specs). */
  spec: TeamMemberSpecView | null;
  role: string;
  /** The session doing its work now (the most relevant one), if any. */
  live: TeamMember | null;
  /** Every session of the project matched to it. */
  sessions: TeamMember[];
  /** Spend of those sessions. */
  costUsd: number | null;
}

/**
 * Match the project's live sessions to its team spec. The snapshot does not
 * link sessions to members, so sessions are matched by role and vendor: the
 * most relevant session (busy first, then the newest) goes to the lowest
 * member number, and so on round the members of that role and vendor.
 * Sessions no member fits are returned in `outside`.
 */
export function chartMembers(
  spec: TeamSpecView | null | undefined,
  live: readonly TeamMember[],
): { members: ChartMember[]; outside: TeamMember[] } {
  if (!spec) {
    return {
      members: live.map((m) => ({
        spec: null,
        role: liveRole(m),
        live: m,
        sessions: [m],
        costUsd: m.row.cost_usd,
      })),
      outside: [],
    };
  }
  const key = (role: string, vendor: string | null | undefined) => `${role}|${vendor ?? ""}`;
  const pools = new Map<string, TeamMember[]>();
  for (const m of [...live].sort(relevance)) {
    const role = liveRole(m);
    const k = key(role, role === "project_lead" ? "claude" : (m.row.vendor ?? vendorGuess(m)));
    pools.set(k, [...(pools.get(k) ?? []), m]);
  }
  const groups = new Map<string, TeamMemberSpecView[]>();
  for (const m of [...spec.members].sort((a, b) => memberNumber(a.id) - memberNumber(b.id))) {
    const k = key(m.role, m.vendor);
    groups.set(k, [...(groups.get(k) ?? []), m]);
  }
  const assigned = new Map<string, TeamMember[]>();
  const outside: TeamMember[] = [];
  for (const [k, sessions] of pools) {
    const owners = groups.get(k);
    if (!owners?.length) {
      outside.push(...sessions);
      continue;
    }
    sessions.forEach((session, i) => {
      const owner = owners[i % owners.length]!;
      assigned.set(owner.id, [...(assigned.get(owner.id) ?? []), session]);
    });
  }
  const members = spec.members.map((m): ChartMember => {
    const sessions = assigned.get(m.id) ?? [];
    const costs = sessions.map((s) => s.row.cost_usd).filter((c): c is number => c != null);
    return {
      spec: m,
      role: m.role,
      live: sessions[0] ?? null,
      sessions,
      costUsd: costs.length ? costs.reduce((a, b) => a + b, 0) : null,
    };
  });
  return { members, outside };
}

function vendorGuess(member: TeamMember): string | null {
  const harness = (member.row.harness ?? "").toLowerCase();
  if (harness.includes("codex")) return "codex";
  if (harness.includes("claude")) return "claude";
  return null;
}

/**
 * The member a name in a command means ("swap rex to claude"): a member's own
 * handle, else the handle of the session doing its work, else its id; any case.
 */
export function memberByName(
  spec: TeamSpecView | null | undefined,
  live: readonly TeamMember[],
  name: string,
): TeamMemberSpecView | null {
  const want = name.trim().toLowerCase();
  if (!spec || !want) return null;
  const own = spec.members.find(
    (m) => m.handle?.toLowerCase() === want || m.id.toLowerCase() === want,
  );
  if (own) return own;
  const { members } = chartMembers(spec, live);
  const found = members.find((m) =>
    m.sessions.some((s) => s.row.handle?.toLowerCase() === want || s.name.toLowerCase() === want),
  );
  return found?.spec ?? null;
}

/** Members by chart place: the lead, then each group (in group order). */
export function chartGroups<T extends { role: string }>(
  members: readonly T[],
): { lead: T[]; groups: { info: TeamGroupInfo; members: T[] }[] } {
  const lead = members.filter((m) => m.role === "project_lead");
  const groups = TEAM_GROUPS.map((info) => ({
    info,
    members: members
      .filter((m) => (info.roles as readonly string[]).includes(m.role))
      .sort((a, b) => (ROLE_RANK.get(a.role) ?? 9) - (ROLE_RANK.get(b.role) ?? 9)),
  }));
  return { lead, groups };
}

/**
 * $/h of the team while it works, from the catalog's estimates (the server's
 * formula: every member but the builders, plus `builders` builders at their
 * average rate). Null without a catalog.
 */
export function teamRate(
  catalog: CatalogResponse | null | undefined,
  members: readonly { role: string; vendor: string; model: string | null }[],
  builders: number,
): number | null {
  if (!catalog) return null;
  let others = 0;
  const builderRates: number[] = [];
  for (const m of members) {
    const vendor = catalogVendor(catalog, m.role, m.vendor);
    const model = m.model ?? vendor?.default_model ?? null;
    const rate = (model ? vendor?.model_rates?.[model] : undefined) ?? vendor?.usd_per_h ?? 0;
    if (m.role === "ic") builderRates.push(rate);
    else others += rate;
  }
  const average = builderRates.length
    ? builderRates.reduce((a, b) => a + b, 0) / builderRates.length
    : 0;
  return Math.round((others + average * Math.max(0, builders)) * 100) / 100;
}

// ── History across definition versions ─────────────────────────

/** A project's own team as a version's document stores it (`null`: it followed the bundle). */
export function teamInDoc(
  doc: Record<string, unknown> | null | undefined,
  slug: string,
): RestoredTeam | null {
  const team = (doc?.team ?? null) as { projects?: Record<string, unknown> } | null;
  const own = team?.projects?.[slug];
  if (!own || typeof own !== "object") return null;
  const { seq: _seq, ...rest } = own as Record<string, unknown> & { seq?: unknown };
  return rest as unknown as RestoredTeam;
}

function canonical(team: RestoredTeam | null): string {
  if (!team) return "null";
  const members = [...(team.members ?? [])]
    .map((m) => ({
      id: m.id,
      role: m.role,
      handle: m.handle ?? null,
      vendor: m.vendor ?? null,
      model: m.model ?? null,
    }))
    .sort((a, b) => memberNumber(a.id) - memberNumber(b.id));
  const briefs = Object.entries(team.briefs ?? {}).sort(([a], [b]) => a.localeCompare(b));
  return JSON.stringify({
    template: team.template ?? null,
    builders: team.builders_at_once ?? null,
    budget: team.budget_usd ?? null,
    members,
    briefs,
  });
}

function memberWords(m: RestoredTeam["members"][number]): string {
  const role = (TEAM_ROLE_LABEL[m.role as TeamRole] ?? m.role).toLowerCase();
  return `${m.handle ? `${m.handle}, ` : ""}${role} · ${m.vendor ?? "default"} · ${m.model ?? "default"}`;
}

/** What changed between two teams of one project, in short lines. */
export function teamDiffLines(before: RestoredTeam | null, after: RestoredTeam | null): string[] {
  if (canonical(before) === canonical(after)) return [];
  if (!after) return ["Back to today's bundle"];
  const value = (v: unknown, unit = "") =>
    v == null ? "the charter's" : `${unit}${typeof v === "number" ? Math.round(v) : String(v)}`;
  if (!before) {
    const parts = [
      after.template ? `${after.template} template` : null,
      `${after.members.length} members`,
      after.builders_at_once != null
        ? `${after.builders_at_once} ${after.builders_at_once === 1 ? "builder" : "builders"} at once`
        : null,
      after.budget_usd != null ? `budget ${value(after.budget_usd, "$")}` : null,
    ].filter(Boolean);
    return [`Got its own team: ${parts.join(", ")}`];
  }
  const lines: string[] = [];
  if ((before.template ?? null) !== (after.template ?? null)) {
    lines.push(`Template ${before.template ?? "none"} → ${after.template ?? "none"}`);
  }
  if ((before.builders_at_once ?? null) !== (after.builders_at_once ?? null)) {
    lines.push(
      `Builders at once ${value(before.builders_at_once)} → ${value(after.builders_at_once)}`,
    );
  }
  if ((before.budget_usd ?? null) !== (after.budget_usd ?? null)) {
    lines.push(`Budget ${value(before.budget_usd, "$")} → ${value(after.budget_usd, "$")}`);
  }
  const old = new Map(before.members.map((m) => [m.id, m]));
  const now = new Map(after.members.map((m) => [m.id, m]));
  for (const m of after.members) {
    const was = old.get(m.id);
    if (!was) lines.push(`Added ${m.id} (${memberWords(m)})`);
    else if (
      (was.vendor ?? null) !== (m.vendor ?? null) ||
      (was.model ?? null) !== (m.model ?? null)
    ) {
      lines.push(
        `${m.handle ?? m.id}: ${was.vendor ?? "default"} · ${was.model ?? "default"} → ${
          m.vendor ?? "default"
        } · ${m.model ?? "default"}`,
      );
    } else if ((was.handle ?? null) !== (m.handle ?? null)) {
      lines.push(`${m.id} renamed ${was.handle ?? "—"} → ${m.handle ?? "—"}`);
    }
  }
  for (const m of before.members) {
    if (!now.has(m.id)) lines.push(`Removed ${m.id} (${memberWords(m)})`);
  }
  const oldBriefs = before.briefs ?? {};
  const newBriefs = after.briefs ?? {};
  for (const id of new Set([...Object.keys(oldBriefs), ...Object.keys(newBriefs)])) {
    if (!now.has(id) || oldBriefs[id] === newBriefs[id]) continue;
    lines.push(newBriefs[id] ? `Instructions for ${id} set` : `Instructions for ${id} cleared`);
  }
  return lines.length ? lines : ["Team changed"];
}

/** One team change in the project's history: a definition version. */
export interface TeamHistoryRow {
  version: number;
  parent: number | null;
  createdAt: number;
  /** Who made it, in words (an agent's title, else the person). */
  by: string;
  /** Who made it: a person's user id or an agent's session id. */
  createdBy: string;
  proposalRef: string | null;
  lines: string[];
  /** The version in force. */
  current: boolean;
  undoneBy: string | null;
  restoredFrom: number | null;
  /** The team as this version has it (what Restore puts back). */
  team: RestoredTeam | null;
}

/**
 * The versions that changed project `slug`'s team, newest first (each against
 * its parent version). A version whose parent is not in the list counts when
 * its project already had its own team.
 */
export function teamHistory(
  versions: readonly DefinitionView[],
  slug: string,
  pinned?: number | null,
): TeamHistoryRow[] {
  const byVersion = new Map(versions.map((v) => [v.version, v]));
  const rows: TeamHistoryRow[] = [];
  for (const v of [...versions].sort((a, b) => b.version - a.version)) {
    const after = teamInDoc(v.doc, slug);
    const parent = v.parent_version != null ? byVersion.get(v.parent_version) : undefined;
    if (!parent && v.parent_version != null) continue;
    const before = parent ? teamInDoc(parent.doc, slug) : null;
    const lines = teamDiffLines(before, after);
    if (!lines.length) continue;
    rows.push({
      version: v.version,
      parent: v.parent_version,
      createdAt: v.created_at,
      by: v.created_by_title ?? v.created_by,
      createdBy: v.created_by,
      proposalRef: v.proposal_ref,
      lines,
      current: pinned != null ? v.version === pinned : v.pinned,
      undoneBy: v.undone_by ?? null,
      restoredFrom: v.restored_from ?? null,
      team: after,
    });
  }
  return rows;
}

/** The change that puts back the team of history row `row` (null when it is the team in force). */
export function restoreChange(
  row: TeamHistoryRow,
  current: RestoredTeam | null,
): Extract<StagedTeamChange, { kind: "restore" }> | null {
  if (canonical(row.team) === canonical(current)) return null;
  return { kind: "restore", version: row.version, team: row.team };
}

/** The 10-minute window in which an applied team change can be undone (seconds). */
export const UNDO_WINDOW_S = 600;

/**
 * Whether `row` (the newest team change) can still be undone by re-pinning its
 * parent: it is in force, not undone, and within the window. Owners undo any
 * team change; an editor only their own.
 *
 * @returns Seconds left, or null.
 */
export function undoLeft(
  row: TeamHistoryRow | null | undefined,
  nowS: number,
  viewer: { userId: string | null | undefined; owner: boolean },
): number | null {
  if (!row || !row.current || row.undoneBy || row.parent == null) return null;
  if (!viewer.owner && (!viewer.userId || row.createdBy !== viewer.userId)) return null;
  const left = row.createdAt + UNDO_WINDOW_S - nowS;
  return left > 0 ? left : null;
}
