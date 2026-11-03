// Pure model of Steer: its sections (and which hide while discovery is off),
// the one staged change for the next definition version (each edit with its
// path, before → after and section; a restore stands alone), the trial rule
// the change column states before the server's preview answers, the fields a
// running trial locks, the re-rank rows of a preview, the outline's one-line
// values and the version list. Apply sends everything staged as one
// `PUT …/definition`, so one apply is one version.

import type {
  DefinitionChange,
  DefinitionDoc,
  DefinitionResponse,
  DefinitionView,
  GoalDef,
  MixDef,
  ProposalView,
  SegmentDef,
} from "@/lib/factoryApi";
import { STEER_SECTIONS, type SteerSection } from "../factoryRoutes";
import { OTHER_GROUPS } from "../settings/settingsModel";

// ── Sections ───────────────────────────────────────────────────

export interface SectionInfo {
  label: string;
  /** Shown under the label on desktop when no value applies. */
  hint: string;
}

export const SECTION_INFO: Record<SteerSection, SectionInfo> = {
  overview: { label: "Overview", hint: "the loop, who owns each stage" },
  cadence: { label: "Cadence and previews", hint: "heartbeats, supervisor, mail, previews" },
  strategy: { label: "Strategy and goals", hint: "goals, focus, non-goals, mix" },
  product: { label: "Product profile", hint: "what it is, who uses it, delivery, environment" },
  sources: { label: "Sources", hint: "where evidence comes from" },
  rubric: { label: "Scoring rubric", hint: "how opportunities rank" },
  roles: { label: "Roles and briefs", hint: "models, briefs, rules per role" },
  templates: { label: "Team templates", hint: "named team shapes" },
  gates: { label: "Gates", hint: "locked (Tier 0)" },
  budgets: { label: "Budgets and autonomy", hint: "money, loops, who decides" },
  alerts: { label: "Alerts and rules", hint: "thresholds and rules per role" },
  versions: { label: "Versions and trials", hint: "every version, restore" },
};

/** The outline's groups, in order. */
export const STEER_GROUPS: readonly { title: string; sections: readonly SteerSection[] }[] = [
  { title: "How it works", sections: ["overview"] },
  { title: "Product and strategy", sections: ["strategy", "product"] },
  { title: "Discovery method", sections: ["sources", "rubric"] },
  { title: "Teams and roles", sections: ["roles", "templates", "gates"] },
  { title: "Guardrails", sections: ["budgets", "alerts", "cadence"] },
  { title: "History", sections: ["versions"] },
];

/** Sections that only mean something while discovery runs (Strategy and Product stay). */
export const DISCOVERY_SECTIONS: ReadonlySet<SteerSection> = new Set(["sources", "rubric"]);

/** Roles shown in Roles and briefs only while discovery runs. */
export const DISCOVERY_ROLES: ReadonlySet<string> = new Set(["scout"]);

/** The sections shown: discovery's hide while it is off. */
export function visibleSections(discoveryOn: boolean): SteerSection[] {
  return STEER_SECTIONS.filter((s) => discoveryOn || !DISCOVERY_SECTIONS.has(s));
}

/** The groups with the sections shown, empty groups left out. */
export function visibleGroups(discoveryOn: boolean) {
  const shown = new Set(visibleSections(discoveryOn));
  return STEER_GROUPS.map((g) => ({
    title: g.title,
    sections: g.sections.filter((s) => shown.has(s)),
  })).filter((g) => g.sections.length > 0);
}

const PREFIX_SECTION: readonly [string, SteerSection][] = [
  ["strategy", "strategy"],
  ["product", "product"],
  ["environment", "product"],
  ["discovery.sources", "sources"],
  ["discovery.rubric", "rubric"],
  ["discovery.thresholds", "rubric"],
  ["discovery.budgets", "budgets"],
  ["discovery.autonomy", "budgets"],
  ["discovery.seat", "roles"],
  ["discovery.cadence", "roles"],
  ["discovery.enabled", "overview"],
  ["discovery", "overview"],
  ["briefs", "roles"],
  ["role_defaults", "roles"],
  ["models", "roles"],
  ["team.templates", "templates"],
  ["team", "templates"],
  ["budgets", "budgets"],
  ["loops", "budgets"],
  ["autonomy", "budgets"],
  ["engine", "budgets"],
  ["alerts", "alerts"],
  ["decisions", "alerts"],
  ["rules", "alerts"],
  ["cadence", "cadence"],
  ["supervisor", "cadence"],
  ["mail", "cadence"],
  ["previews", "cadence"],
  ["caps", "cadence"],
];

/** Today's run knobs in Cadence and previews (models moved to Roles and briefs). */
export const CADENCE_GROUPS = OTHER_GROUPS.filter(
  (g) => !g.fields.some((f) => f.path.startsWith("role_defaults.")),
);

/** Dotted form of a change path (`rules/R-3` → `rules.R-3`). */
export function dotted(path: string): string {
  return path.replace(/^\//, "").replaceAll("/", ".");
}

/** Whether dotted `path` is `prefix` or lies below it. */
export function under(path: string, prefix: string): boolean {
  return path === prefix || path.startsWith(`${prefix}.`);
}

/** The Steer section that edits a definition path (Overview when nothing else does). */
export function sectionOfPath(path: string): SteerSection {
  const p = dotted(path);
  return PREFIX_SECTION.find(([prefix]) => under(p, prefix))?.[1] ?? "overview";
}

// ── Values ─────────────────────────────────────────────────────

/** The value at a dotted path of the doc (list items by id: `strategy.goals.G-1.statement`). */
export function valueAt(doc: unknown, path: string): unknown {
  let node: unknown = doc;
  for (const part of dotted(path).split(".")) {
    if (node == null || typeof node !== "object") return undefined;
    if (Array.isArray(node)) {
      node = node.find((item) => (item as { id?: unknown })?.id === part);
    } else {
      node = (node as Record<string, unknown>)[part];
    }
  }
  return node;
}

/** Structural equality of JSON values (key order ignored). */
export function same(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if ((a ?? null) === null && (b ?? null) === null) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a)) {
    const bb = b as unknown[];
    return a.length === bb.length && a.every((x, i) => same(x, bb[i]));
  }
  const ka = Object.keys(a as object).filter(
    (k) => (a as Record<string, unknown>)[k] !== undefined,
  );
  const kb = Object.keys(b as object).filter(
    (k) => (b as Record<string, unknown>)[k] !== undefined,
  );
  return (
    ka.length === kb.length &&
    ka.every((k) => same((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]))
  );
}

/** Team roles as people say them. */
const ROLE_WORDS: Readonly<Record<string, string>> = {
  project_lead: "project lead",
  ic: "builder",
  reviewer: "reviewer",
  env_keeper: "env keeper",
  demo: "demo agent",
};

/** A value in words for the change column: lists joined, nothing as "(none)". */
export function showValue(value: unknown, names?: ReadonlyMap<string, string>): string {
  if (value == null || value === "") return "(none)";
  if (Array.isArray(value)) {
    if (value.length === 0) return "(none)";
    return value
      .map((v) =>
        typeof v === "object" && v !== null
          ? showValue(v, names)
          : (names?.get(String(v)) ?? String(v)),
      )
      .join(", ");
  }
  if (typeof value === "object") {
    const item = value as Record<string, unknown>;
    if (typeof item.label === "string") return item.label;
    if (typeof item.statement === "string") return item.statement;
    if (typeof item.text === "string") return item.text;
    if (typeof item.role === "string") {
      const who = [item.vendor, item.model].filter((x) => typeof x === "string").join(" · ");
      const n = typeof item.count === "number" && item.count > 1 ? `${item.count} × ` : "";
      const role = ROLE_WORDS[item.role] ?? item.role.replaceAll("_", " ");
      return `${n}${role}${who ? ` (${who})` : ""}`;
    }
    if (typeof item.id === "string") return item.id;
    return Object.entries(item)
      .filter(([, v]) => v != null && v !== "")
      .map(([k, v]) => `${k} ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
      .join(", ");
  }
  if (typeof value === "boolean") return value ? "on" : "off";
  return String(value);
}

// ── Staging ────────────────────────────────────────────────────

/** One staged edit: what changes, in force before, and after Apply. */
export interface StagedEdit {
  /** One per edit of a path: the path, or `<list>+<n>` for an item added. */
  key: string;
  section: SteerSection;
  /** What people call it ("Focus segments"). */
  label: string;
  change: DefinitionChange;
  /** In force now (an added item: null). */
  before: unknown;
  /** After Apply (a removed item: null). */
  after: unknown;
  /** What it does, in plain words. */
  effect?: string;
}

/** An agent's proposal staged with Stage it: Apply answers it while it stays as proposed. */
export interface StagedProposal {
  ref: string;
  by: string;
  /** Its edits as staged, to tell whether someone changed them since. */
  signature: string;
}

/** Everything staged for the next version: edits, or one version to restore. */
export interface SteerStaging {
  edits: StagedEdit[];
  restore: number | null;
  proposal: StagedProposal | null;
  /** The version in force when the first edit (or the restore) was staged; Apply sends it. */
  base: number | null;
}

export const EMPTY_STEER: SteerStaging = { edits: [], restore: null, proposal: null, base: null };

/** Whether nothing is staged. */
export function stagingEmpty(staging: SteerStaging): boolean {
  return staging.edits.length === 0 && staging.restore == null;
}

/**
 * `staging` with its base: an empty staging resets to nothing, and the first
 * thing staged records `version`, the version in force, so Apply cannot
 * overwrite a version applied since.
 */
export function withBase(staging: SteerStaging, version: number): SteerStaging {
  if (stagingEmpty(staging)) return EMPTY_STEER;
  return staging.base == null ? { ...staging, base: version } : staging;
}

/** Whether another version came into force since `staging` was staged. */
export function isStale(staging: SteerStaging, version: number): boolean {
  return !stagingEmpty(staging) && staging.base != null && staging.base !== version;
}

/**
 * Stage again on the version in force: each edit's "before" is read anew, an
 * edit that now equals the value in force (or removes an item already gone)
 * drops, and the base moves to `version`.
 */
export function rebaseStaging(
  staging: SteerStaging,
  effective: DefinitionDoc,
  version: number,
): SteerStaging {
  if (staging.restore != null) return { ...staging, base: version };
  const edits: StagedEdit[] = [];
  for (const edit of staging.edits) {
    if (edit.change.op === "add") {
      edits.push(edit);
      continue;
    }
    const now = valueAt(effective, edit.change.path) ?? null;
    if (edit.change.op === "remove" ? now == null : same(now, edit.after)) continue;
    edits.push({ ...edit, before: now });
  }
  return withBase({ ...staging, edits, base: version }, version);
}

function signature(edits: readonly StagedEdit[]): string {
  return JSON.stringify(edits.map((e) => e.change));
}

/** How many things are staged (a restore counts as one). */
export function stagedCount(staging: SteerStaging): number {
  return staging.restore != null ? 1 : staging.edits.length;
}

/**
 * Stage `edit`: a later edit of the same key keeps the first one's "before",
 * and an edit back to the value in force drops it. Nothing stages while a
 * restore is staged (drop the restore first).
 */
export function stageEdit(staging: SteerStaging, edit: StagedEdit): SteerStaging {
  if (staging.restore != null) return staging;
  const i = staging.edits.findIndex((e) => e.key === edit.key);
  const prior = i >= 0 ? staging.edits[i]! : null;
  const merged: StagedEdit = prior ? { ...edit, before: prior.before } : edit;
  const edits = [...staging.edits];
  const backToNow = merged.change.op === "replace" && same(merged.before, merged.after);
  if (backToNow) {
    if (i >= 0) edits.splice(i, 1);
  } else if (i >= 0) {
    edits[i] = merged;
  } else {
    edits.push(merged);
  }
  return { ...staging, edits };
}

/** Drop one staged edit (an added item's other edits go with it). */
export function dropEdit(staging: SteerStaging, key: string): SteerStaging {
  const gone = staging.edits.find((e) => e.key === key);
  if (!gone) return staging;
  const added = gone.change.op === "add" ? addedId(gone) : null;
  const edits = staging.edits.filter(
    (e) =>
      e.key !== key &&
      !(added && dotted(e.change.path).startsWith(`${dotted(gone.change.path)}.${added}.`)),
  );
  return { ...staging, edits };
}

/** Stage a restore of version `version` (it replaces whatever was staged). */
export function stageRestore(version: number): SteerStaging {
  return { edits: [], restore: version, proposal: null, base: null };
}

/** The `changes[]` of `PUT …/definition` for what is staged, in staging order. */
export function stagedChanges(staging: SteerStaging): DefinitionChange[] {
  return staging.edits.map((e) => e.change);
}

/** The sections something is staged in. */
export function stagedSections(staging: SteerStaging): Set<SteerSection> {
  const out = new Set(staging.edits.map((e) => e.section));
  if (staging.restore != null) out.add("versions");
  return out;
}

/** The staged edit of `path`, if any. */
export function stagedEditOf(staging: SteerStaging, path: string): StagedEdit | undefined {
  const p = dotted(path);
  return staging.edits.find((e) => e.key === p);
}

/** The value a control shows: staged when staged, else the value in force. */
export function stagedValue(staging: SteerStaging, path: string, inForce: unknown): unknown {
  const edit = stagedEditOf(staging, path);
  return edit ? edit.after : inForce;
}

/** A replace of the value at `path` (scalars, whole lists such as `strategy.non_goals`). */
export function replaceEdit(
  section: SteerSection,
  path: string,
  label: string,
  before: unknown,
  after: unknown,
  effect?: string,
): StagedEdit {
  const p = dotted(path);
  return {
    key: p,
    section,
    label,
    change: { op: "replace", path: p, value: after },
    before: before ?? null,
    after: after ?? null,
    effect,
  };
}

/** The id an added item will have (`G-n` items are numbered by the server: none yet). */
function addedId(edit: StagedEdit): string | null {
  const value = edit.change.value as { id?: unknown } | undefined;
  return typeof value?.id === "string" ? value.id : null;
}

/**
 * The key of an item added to `listPath`: `<list>+<id>`, or `<list>+<n>` for
 * an item the server numbers (a goal), one past the highest staged.
 */
export function nextAddKey(staging: SteerStaging, listPath: string, id?: string | null): string {
  const p = dotted(listPath);
  if (id) return `${p}+${id}`;
  const taken = staging.edits
    .map((e) => /\+(\d+)$/.exec(e.key))
    .filter((m): m is RegExpExecArray => m != null && m.input.startsWith(`${p}+`))
    .map((m) => Number(m[1]));
  return `${p}+${Math.max(0, ...taken) + 1}`;
}

/** An item added to a list with ids (`product.segments`, `strategy.goals`, `discovery.sources`…). */
export function addItemEdit(
  staging: SteerStaging,
  section: SteerSection,
  listPath: string,
  label: string,
  item: Record<string, unknown>,
  effect?: string,
  key?: string,
): StagedEdit {
  const p = dotted(listPath);
  return {
    key: key ?? nextAddKey(staging, p, typeof item.id === "string" ? item.id : null),
    section,
    label,
    change: { op: "add", path: p, value: item },
    before: null,
    after: item,
    effect,
  };
}

/** One item removed from a list with ids. */
export function removeItemEdit(
  section: SteerSection,
  listPath: string,
  id: string,
  label: string,
  before: unknown,
  effect?: string,
): StagedEdit {
  const p = `${dotted(listPath)}.${id}`;
  return { key: p, section, label, change: { op: "remove", path: p }, before, after: null, effect };
}

/** Stage removing an item: a staged add of it is dropped instead. */
export function stageRemoveItem(
  staging: SteerStaging,
  section: SteerSection,
  listPath: string,
  id: string,
  label: string,
  before: unknown,
): SteerStaging {
  const p = dotted(listPath);
  const added = staging.edits.find((e) => e.key === `${p}+${id}`);
  if (added) return dropEdit(staging, added.key);
  // Edits of its fields go too: the item is gone.
  const rest = {
    ...staging,
    edits: staging.edits.filter((e) => !dotted(e.change.path).startsWith(`${p}.${id}.`)),
  };
  return stageEdit(rest, removeItemEdit(section, p, id, label, before));
}

/** Items of a list as staged: in force (minus removals, with field edits) plus additions. */
export function stagedList<T extends { id?: string }>(
  staging: SteerStaging,
  listPath: string,
  inForce: readonly T[],
): (T & { staged?: "added" | "changed" | "removed" })[] {
  const p = dotted(listPath);
  const out: (T & { staged?: "added" | "changed" | "removed" })[] = inForce.map((item) => {
    const id = item.id ?? "";
    if (stagedEditOf(staging, `${p}.${id}`)?.change.op === "remove") {
      return { ...item, staged: "removed" as const };
    }
    let next: T = item;
    let changed = false;
    for (const e of staging.edits) {
      const path = dotted(e.change.path);
      if (e.change.op !== "replace") continue;
      if (path === `${p}.${id}`) {
        next = { ...(e.after as T), id } as T;
        changed = true;
      } else if (path.startsWith(`${p}.${id}.`)) {
        const field = path.slice(p.length + id.length + 2);
        next = { ...next, [field]: e.after };
        changed = true;
      }
    }
    return changed ? { ...next, staged: "changed" as const } : next;
  });
  for (const e of staging.edits) {
    if (e.change.op === "add" && dotted(e.change.path) === p) {
      out.push({ ...(e.after as T), staged: "added" as const });
    }
  }
  return out;
}

// ── Words ──────────────────────────────────────────────────────

const ROLE_NAMES: Readonly<Record<string, string>> = {
  scout: "Scout",
  atlas: "Atlas",
  sentinel: "Sentinel",
  project_lead: "Project lead",
  ic: "Builder",
  reviewer: "Reviewer",
  env_keeper: "Env keeper",
  demo: "Demo",
  planner: "Planner",
};

const PATH_LABELS: readonly [RegExp, (m: RegExpExecArray) => string][] = [
  [/^briefs\.([a-z_]+)$/, (m) => `${ROLE_NAMES[m[1]!] ?? m[1]}'s brief`],
  [/^role_defaults\.([a-z_]+)\.(vendor|model)$/, (m) => `${ROLE_NAMES[m[1]!] ?? m[1]}: ${m[2]}`],
  [/^strategy\.goals(\..*)?$/, () => "Goal"],
  [/^strategy\.focus_segments$/, () => "Focus segments"],
  [/^strategy\.non_goals$/, () => "Non-goals"],
  [/^strategy\.guardrails$/, () => "Guardrails"],
  [/^strategy\.mix\.([a-z]+)$/, (m) => `Capacity mix: ${m[1]}`],
  [/^product\.segments(\..*)?$/, () => "Segment"],
  [/^product\.areas(\..*)?$/, () => "Area"],
  [/^discovery\.sources(\..*)?$/, () => "Source"],
  [/^discovery\.seat\.model$/, () => "Scout's model"],
  [/^discovery\.enabled$/, () => "Discovery"],
  [/^rules(\..*)?$/, () => "Rule"],
  [/^team\.templates\.([a-z0-9_-]+)/, (m) => `Template ${m[1]}`],
];

/** What people call a definition path ("Scout's brief", "Segment", "Off focus factor"). */
export function labelOfPath(path: string): string {
  const p = dotted(path);
  for (const [re, word] of PATH_LABELS) {
    const m = re.exec(p);
    if (m) return word(m);
  }
  const last = p.split(".").pop() ?? p;
  const words = last.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

// ── Agent proposals ────────────────────────────────────────────

/** A definition patch of a proposal (`{op, path, value}`), or null for another kind of op. */
function patchOf(op: Record<string, unknown>): DefinitionChange | null {
  const kind = op.op;
  const path = op.path;
  if ((kind !== "replace" && kind !== "add" && kind !== "remove") || typeof path !== "string") {
    return null;
  }
  return kind === "remove"
    ? { op: kind, path: dotted(path) }
    : { op: kind, path: dotted(path), value: op.value };
}

/** Whether a proposal changes the definition (tuning and rules), so Stage it applies to it. */
export function stageable(proposal: ProposalView): boolean {
  return (
    (proposal.kind === "tuning" || proposal.kind === "rules") &&
    proposal.ops.some((op) => patchOf(op) != null)
  );
}

/** Who filed a proposal, in words ("Scout", "Sentinel", a person's short name). */
export function proposerName(proposal: ProposalView): string {
  const title = proposal.author_title ?? proposal.author;
  if (title.includes("@")) return title.split("@")[0]!;
  return title;
}

/**
 * Stage an agent's proposal as it stands: its patches become the staged edits
 * (whatever was staged is replaced), with the ref kept so Apply answers it.
 */
export function stageProposal(proposal: ProposalView, effective: DefinitionDoc): SteerStaging {
  let staging: SteerStaging = { ...EMPTY_STEER };
  for (const op of proposal.ops) {
    const change = patchOf(op);
    if (!change) continue;
    const section = sectionOfPath(change.path);
    const label = labelOfPath(change.path);
    if (change.op === "add") {
      staging = stageEdit(
        staging,
        addItemEdit(
          staging,
          section,
          change.path,
          label,
          (change.value ?? {}) as Record<string, unknown>,
        ),
      );
    } else if (change.op === "remove") {
      staging = stageEdit(staging, {
        key: change.path,
        section,
        label,
        change,
        before: valueAt(effective, change.path) ?? null,
        after: null,
      });
    } else {
      staging = stageEdit(staging, {
        ...replaceEdit(section, change.path, label, valueAt(effective, change.path), change.value),
        key: change.path,
      });
    }
  }
  return {
    ...staging,
    proposal: {
      ref: proposal.ref,
      by: proposerName(proposal),
      signature: signature(staging.edits),
    },
  };
}

/** Whether the staged edits are still exactly the staged proposal's (Apply then answers it). */
export function proposalIntact(staging: SteerStaging): boolean {
  return staging.proposal != null && staging.proposal.signature === signature(staging.edits);
}

/** Agents' pending proposals to change how the factory works, oldest first. */
export function agentProposals(proposals: readonly ProposalView[]): ProposalView[] {
  return proposals
    .filter(
      (p) =>
        p.author_kind !== "human" &&
        (p.status === "pending" || p.status === "awaiting_cosign") &&
        stageable(p),
    )
    .sort((a, b) => a.number - b.number);
}

/** Agent proposals expire after this long unanswered. */
export const PROPOSAL_TTL_S = 72 * 3600;

/** Seconds until a pending proposal expires (its deadline, else 72 h after filing). */
export function expiresIn(proposal: ProposalView, nowS: number): number {
  const result = proposal.result ?? {};
  const at =
    typeof result.expires_at === "number"
      ? result.expires_at
      : proposal.created_at + PROPOSAL_TTL_S;
  return Math.max(0, at - nowS);
}

/** The paths a proposal changes (its patches). */
export function proposalPaths(proposal: ProposalView): string[] {
  return proposal.ops.map(patchOf).flatMap((c) => (c ? [c.path] : []));
}

// ── Trials ─────────────────────────────────────────────────────

/** The definition blocks a trial holds (the server's `trial_block`): new blocks and teams. */
export const TRIAL_BLOCKS = ["product", "strategy", "discovery", "briefs", "role_defaults", "team"];

/** The block a trial on `path` holds, or null (today's settings trial side by side). */
export function trialBlock(path: string): string | null {
  const head = dotted(path)
    .replace(/^models\./, "role_defaults.")
    .split(".", 1)[0]!;
  return TRIAL_BLOCKS.includes(head) ? head : null;
}

export type TrialCode = "ok" | "no_change" | "restore" | "not_trialable" | "trial_overlap";

/** Whether the staged change may run as a trial, and why not, in words. */
export interface TrialRule {
  code: TrialCode;
  ok: boolean;
  /** A short bold head ("No trial for strategy."). */
  head: string;
  text: string;
  /** The running trial that holds the block (`trial_overlap`). */
  trial?: string;
}

const NAMES: Record<string, string> = {
  product: "the product profile",
  strategy: "strategy",
  discovery: "discovery",
  "briefs.scout": "Scout's brief",
  "team.projects": "a project's team",
  "team.templates": "team templates",
};

/**
 * The trial rule of a change to `paths`, from what `GET …/definition` says
 * (paths that never trial and the blocks running trials hold). The server's
 * preview says the same once it answers; this is what shows before that.
 */
export function trialRule(
  paths: readonly string[],
  definition: Pick<DefinitionResponse, "not_trialable" | "not_trialable_why" | "trial_blocks">,
  options: { restore?: number | null; nowS?: number } = {},
): TrialRule {
  if (options.restore != null) {
    return {
      code: "restore",
      ok: false,
      head: "No trial for a restore.",
      text: "A restore applies as a version; undo it, or restore again.",
    };
  }
  if (paths.length === 0) return { code: "no_change", ok: false, head: "", text: "" };
  const never = definition.not_trialable ?? [];
  const why = definition.not_trialable_why ?? {};
  const hit = never.filter((prefix) => paths.some((p) => under(dotted(p), prefix)));
  if (hit.length) {
    const first = hit[0]!;
    const reason = why[first] ?? "judged on outcomes";
    const name = NAMES[first] ?? first;
    return {
      code: "not_trialable",
      ok: false,
      head: `No trial for ${name}.`,
      text:
        reason === "judged on outcomes"
          ? "Trials judge delivery metrics, which would judge the wrong thing here. It applies as a new version; restore the old one any time."
          : reason === "needs discovery guards"
            ? "It would be judged on discovery guards (quote failures, privacy events, accepted bets), which this server does not measure yet. It applies as a new version; restore the old one any time."
            : `A trial would measure the wrong thing (${reason}). It applies as a new version; restore the old one any time.`,
    };
  }
  const blocks = new Set(paths.map(trialBlock).filter((b): b is string => b != null));
  for (const held of definition.trial_blocks ?? []) {
    const clash = held.blocks.find((b) => blocks.has(b));
    if (!clash) continue;
    const left =
      held.trial_until != null && options.nowS != null
        ? ` (ends in ${Math.max(1, Math.round((held.trial_until - options.nowS) / 86400))} d)`
        : "";
    return {
      code: "trial_overlap",
      ok: false,
      head: `No second trial on ${clash}.`,
      text: `${held.proposal} is already trialing it${left}. Two trials on one block confound each other's guards. Apply directly, or wait for ${held.proposal} to end.`,
      trial: held.proposal,
    };
  }
  return {
    code: "ok",
    ok: true,
    head: "It can run as a trial.",
    text: "A trial applies it now and judges it against the guards; it is kept or reverted when it ends.",
  };
}

/** Field path → the running trial whose change set it (that field is locked in its section). */
export function trialLocks(onTrial: readonly ProposalView[]): Map<string, string> {
  const out = new Map<string, string>();
  for (const trial of onTrial) {
    const after = trial.after;
    const paths = [
      ...proposalPaths(trial),
      ...(after && typeof after === "object" && !Array.isArray(after) ? Object.keys(after) : []),
    ];
    for (const path of paths) out.set(dotted(path), trial.ref);
  }
  return out;
}

/** The running trial that locks `path` (or anything below it), if any. */
export function lockedBy(locks: ReadonlyMap<string, string>, path: string): string | null {
  const p = dotted(path);
  for (const [locked, ref] of locks) if (under(locked, p) || under(p, locked)) return ref;
  return null;
}

// ── The re-rank ────────────────────────────────────────────────

export type Band = "now" | "next" | "later" | "parked";
const BAND_ORDER: readonly Band[] = ["parked", "later", "next", "now"];

export const BAND_LABEL: Record<Band, string> = {
  now: "Now",
  next: "Next",
  later: "Later",
  parked: "Parked",
};

export interface RankPlace {
  position: number;
  rank: number;
  band: Band | null;
}

export interface RerankRow {
  ref: string;
  title: string;
  before: RankPlace;
  after: RankPlace;
  /** Up or down a band; null when the band holds. */
  move: "up" | "down" | null;
}

function place(raw: unknown): RankPlace | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const position = typeof r.position === "number" ? r.position : null;
  const rank = typeof r.rank === "number" ? r.rank : null;
  if (position == null || rank == null) return null;
  const band = BAND_ORDER.includes(r.band as Band) ? (r.band as Band) : null;
  return { position, rank, band };
}

/** The preview's `rerank[]`, read field by field (rows it cannot read are left out). */
export function rerankRows(raw: readonly unknown[] | null | undefined): RerankRow[] {
  const out: RerankRow[] = [];
  for (const item of raw ?? []) {
    if (!item || typeof item !== "object") continue;
    const r = item as Record<string, unknown>;
    const before = place(r.before);
    const after = place(r.after);
    if (typeof r.opportunity !== "string" || !before || !after) continue;
    const move =
      before.band && after.band && before.band !== after.band
        ? BAND_ORDER.indexOf(after.band) > BAND_ORDER.indexOf(before.band)
          ? "up"
          : "down"
        : null;
    out.push({
      ref: r.opportunity,
      title: typeof r.title === "string" ? r.title : r.opportunity,
      before,
      after,
      move,
    });
  }
  return out;
}

/** Whether a change to `paths` can move the ranking (strategy and rubric numbers). */
export function movesRank(paths: readonly string[]): boolean {
  return paths.some((p) => {
    const d = dotted(p);
    return under(d, "strategy") || under(d, "discovery.rubric") || under(d, "product.segments");
  });
}

/** `.23` for a rank. */
export function rankText(rank: number): string {
  return rank.toFixed(2).replace(/^0(?=\.)/, "");
}

// ── Strategy ───────────────────────────────────────────────────

export const MIX_KEYS = ["outcome", "tests", "enablers", "hunches"] as const;
export type MixKey = (typeof MIX_KEYS)[number];

export const MIX_LABEL: Record<MixKey, string> = {
  outcome: "Outcome bets",
  tests: "Tests",
  enablers: "Enablers",
  hunches: "Hunches",
};

export function mixTotal(mix: Partial<MixDef> | null | undefined): number {
  return MIX_KEYS.reduce((sum, k) => sum + (Number(mix?.[k]) || 0), 0);
}

/** Active projects by what they are (a bet's kind; requested work counts as a hunch). */
export function mixNow(
  projects: readonly { why?: { kind?: string | null; opportunity?: string | null } | null }[],
): Record<MixKey, number> {
  const out: Record<MixKey, number> = { outcome: 0, tests: 0, enablers: 0, hunches: 0 };
  for (const p of projects) {
    const kind = p.why?.kind;
    if (kind === "test") out.tests += 1;
    else if (kind === "enabler") out.enablers += 1;
    else if (kind === "outcome" || (kind == null && p.why?.opportunity)) out.outcome += 1;
    else out.hunches += 1;
  }
  return out;
}

/** Segment id → label (for focus chips and values in words). */
export function segmentNames(segments: readonly SegmentDef[] | null | undefined) {
  return new Map((segments ?? []).map((s) => [s.id, s.label]));
}

/** A goal's date and weight as one line ("by 2027-03-31 · weight 1"). */
export function goalMeta(goal: Pick<GoalDef, "by" | "weight">): string {
  return [goal.by ? `by ${goal.by}` : null, `weight ${goal.weight}`].filter(Boolean).join(" · ");
}

/** A slug for an id typed as a label ("New users" → `new-users`). */
export function slugOf(label: string, taken: Iterable<string> = []): string {
  const base =
    label
      .toLowerCase()
      .normalize("NFKD")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 28)
      .replace(/^[^a-z]+/, "") || "item";
  const used = new Set(taken);
  if (!used.has(base)) return base;
  for (let i = 2; i < 100; i += 1) if (!used.has(`${base}-${i}`)) return `${base}-${i}`;
  return `${base}-${Date.now() % 1000}`;
}

/** A comma or line separated list as items. */
export function splitList(text: string): string[] {
  return text
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

// ── Outline ────────────────────────────────────────────────────

export interface OutlineFacts {
  definition: DefinitionResponse | null | undefined;
  discoveryOn: boolean;
  /** Sources waiting for an owner (Needs you `source` rows). */
  sourcesWaiting: number;
  templates: number | null;
  trialsRunning: number;
}

/** The one line under a section's name: its current value. */
export function outlineValue(section: SteerSection, facts: OutlineFacts): string {
  const doc = facts.definition?.effective;
  if (!doc) return SECTION_INFO[section].hint;
  const names = segmentNames(doc.product?.segments);
  switch (section) {
    case "overview":
      return facts.discoveryOn ? "the loop, who owns each stage" : "the loop · discovery off";
    case "strategy": {
      const goals = doc.strategy?.goals ?? [];
      const focus = (doc.strategy?.focus_segments ?? []).map((s) => names.get(s) ?? s);
      if (!goals.length) return "no goal yet";
      return `${goals.length} ${goals.length === 1 ? "goal" : "goals"}${
        focus.length ? ` · focus ${focus.join(", ")}` : ""
      }`;
    }
    case "product": {
      const name = doc.product?.name;
      const n = doc.product?.segments?.length ?? 0;
      return `${name ?? "no name yet"} · ${n} ${n === 1 ? "segment" : "segments"}`;
    }
    case "sources": {
      const all = doc.discovery?.sources ?? [];
      const on = all.filter((s) => s.state !== "paused").length;
      return `${on} on${facts.sourcesWaiting ? ` · ${facts.sourcesWaiting} ${facts.sourcesWaiting === 1 ? "waits" : "wait"} for you` : ""}`;
    }
    case "rubric": {
      const r = doc.discovery?.rubric;
      if (!r) return SECTION_INFO.rubric.hint;
      const trial = (facts.definition?.trial_blocks ?? []).some((t) =>
        t.blocks.includes("discovery"),
      );
      return `off-focus × ${r.off_focus_factor} · Now ≥ ${rankText(r.bands.now)}${trial ? " · on trial" : ""}`;
    }
    case "roles": {
      const rules = doc.rules?.length ?? 0;
      const scout = doc.discovery?.seat?.model;
      return `${facts.discoveryOn ? `Scout on ${scout ?? "its default"} · ` : ""}${rules} ${rules === 1 ? "rule" : "rules"}`;
    }
    case "templates":
      return facts.templates != null ? `${facts.templates} templates` : SECTION_INFO.templates.hint;
    case "gates":
      return "locked (Tier 0)";
    case "budgets": {
      const b = doc.budgets as Record<string, unknown> | undefined;
      const day = b?.factory_usd_per_day;
      const alert = b?.usd_per_hour_alert;
      return typeof day === "number"
        ? `$${day} a day${typeof alert === "number" ? ` · alert $${alert}/h` : ""}`
        : SECTION_INFO.budgets.hint;
    }
    case "alerts": {
      const rules = doc.rules?.length ?? 0;
      return `thresholds · ${rules} ${rules === 1 ? "rule" : "rules"}`;
    }
    case "cadence":
      return SECTION_INFO.cadence.hint;
    case "versions":
      return `v${facts.definition?.def_version ?? "?"}${
        facts.trialsRunning
          ? ` · ${facts.trialsRunning} ${facts.trialsRunning === 1 ? "trial" : "trials"} running`
          : ""
      }`;
  }
}

// ── Versions ───────────────────────────────────────────────────

export interface VersionRow {
  version: number;
  /** seeded, edited, proposal, revert, restore. */
  via: string;
  by: string;
  ref: string | null;
  at: number;
  /** A, B or human, from the version's change. */
  klass: string | null;
  /** Its change ran as a trial: on trial, kept, reverted. */
  trial: "on trial" | "kept" | "reverted" | null;
  undone: string | null;
  restoredFrom: number | null;
  pinned: boolean;
  /** Who made it (their id: an email, or an agent's session). */
  createdBy: string;
  /** The version it was made from (its Undo pins this one). */
  parent: number | null;
}

const VIA: Record<DefinitionView["created_via"], string> = {
  seed: "seeded",
  human: "edited",
  proposal: "proposal",
  revert: "revert",
};

function shortName(id: string | null | undefined): string {
  if (!id) return "someone";
  return id.includes("@") ? id.split("@")[0]! : id;
}

/** Every version, newest first, joined with the change that made it. */
export function versionRows(
  versions: readonly DefinitionView[],
  proposals: readonly ProposalView[] = [],
): VersionRow[] {
  const byRef = new Map(proposals.map((p) => [p.ref, p]));
  return [...versions]
    .sort((a, b) => b.version - a.version)
    .map((v) => {
      const change = v.proposal_ref ? byRef.get(v.proposal_ref) : undefined;
      const trial =
        change?.status === "trial"
          ? "on trial"
          : change?.status === "kept"
            ? "kept"
            : change?.status === "reverted" && change.trial_until != null
              ? "reverted"
              : null;
      return {
        version: v.version,
        via: v.restored_from != null ? "restore" : VIA[v.created_via],
        by: v.created_by_title ?? shortName(v.created_by),
        ref: v.proposal_ref,
        at: v.created_at,
        klass: change?.class ?? null,
        trial,
        undone: v.undone_by ? shortName(v.undone_by) : null,
        restoredFrom: v.restored_from ?? null,
        pinned: v.pinned,
        createdBy: v.created_by,
        parent: v.parent_version,
      };
    });
}

/** Undo after Apply works this long (seconds): it re-pins the version before. */
export const UNDO_WINDOW_S = 600;

/**
 * Seconds left to undo `row` (re-pin its parent), or null: it is the version
 * in force, not undone, made from another version within the window, and the
 * viewer is an owner or made it themselves (the server's rule for Undo).
 */
export function undoLeft(
  row: VersionRow | null | undefined,
  nowS: number,
  viewer: { userId: string | null | undefined; owner: boolean },
): number | null {
  if (!row || !row.pinned || row.undone || row.parent == null) return null;
  if (!viewer.owner && (!viewer.userId || row.createdBy !== viewer.userId)) return null;
  const left = row.at + UNDO_WINDOW_S - nowS;
  return left > 0 ? left : null;
}

/** "Undo (8 min left)". */
export function undoLabel(left: number): string {
  return `Undo (${Math.max(1, Math.ceil(left / 60))} min left)`;
}
