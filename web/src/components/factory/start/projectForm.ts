// The start form's model: one draft per project section, its validation
// (limits mirror `omnigent/server/factory/start.py`), the request it becomes,
// the "Load example" projects (spec §12) and the localStorage draft.

import type {
  CharterPatchRequest,
  CharterView,
  FactoryCadence,
  FactoryProjectInput,
  ProjectView,
} from "@/lib/factoryApi";

// ── Limits (the server's `ProjectIn` / `StartIn`) ──────────────

export const MAX_PROJECTS = 10;
export const NAME_MAX = 80;
export const TITLE_MAX = 256;
export const GOAL_MAX = 4000;
export const DOD_MAX_ITEMS = 20;
export const DOD_ITEM_MAX = 300;
export const SCOPE_MAX_ITEMS = 50;
export const SCOPE_ITEM_MAX = 200;
export const CONSTRAINTS_MAX = 2000;
export const DEMO_MAX = 1000;
export const MIN_SLOTS = 1;
export const MAX_SLOTS = 3;
export const DEFAULT_SLOTS = 2;
export const MIN_BUDGET = 1;
export const MAX_BUDGET = 10;
export const DEFAULT_BUDGET = 3;
/** Live previews a whole factory may hold (`OMNIGENT_FACTORY_MAX_PREVIEWS`). */
export const FACTORY_PREVIEW_CAP = 8;
/** Pods a factory may use (`factory_max_pods` of the lead bundle). */
export const FACTORY_MAX_PODS = 12;

export const SLUG_RE = /^[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?$/;
const REPO_RE = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const BRANCH_RE = /^(?!\/)(?!.*\.\.)(?!.*\/\/)[A-Za-z0-9._/-]{1,255}$/;
const URL_PATH_RE = /^\/[A-Za-z0-9._~/-]{0,199}$/;

/** The definition-of-done row every project has; it is never stored. */
export const FIXED_DOD = "PR ready for human merge";

export type Priority = 0 | 1 | 2;
export const PRIORITIES: readonly Priority[] = [0, 1, 2];

/** One project section of the form. */
export interface ProjectDraft {
  /** Stable React key; never sent. */
  key: string;
  title: string;
  slug: string;
  /** The slug was typed by hand, so it no longer follows the title. */
  slugEdited: boolean;
  repo: string;
  branch: string;
  priority: Priority;
  goal: string;
  /** Editable DoD checks (the fixed row is not in here). */
  dod: string[];
  scopePaths: string[];
  /** A dev env for the PL's ICs (`env_needed`). */
  envNeeded: boolean;
  /** A live factory preview per runnable PR plus a recorded demo. */
  previewEnabled: boolean;
  previewSlots: number;
  demoFocus: string;
  versionPath: string;
  budgetIcs: number;
  constraints: string;
}

export interface FactoryDraft {
  name: string;
  cadence: FactoryCadence;
  projects: ProjectDraft[];
}

/** A form field a validation message attaches to. */
export type ProjectField =
  | "title"
  | "slug"
  | "repo"
  | "branch"
  | "priority"
  | "goal"
  | "dod"
  | "scope"
  | "env"
  | "slots"
  | "demo"
  | "version"
  | "budget"
  | "constraints";

export type ProjectErrors = Partial<Record<ProjectField, string>>;

export interface FormErrors {
  name?: string;
  /** Errors about the project list as a whole (e.g. too many). */
  projects?: string;
  byProject: ProjectErrors[];
}

let keySeq = 0;
function nextKey(): string {
  keySeq += 1;
  return `p${Date.now().toString(36)}${keySeq}`;
}

/** `"API status endpoint"` → `"api-status-endpoint"` (cut to the server's 48 chars). */
export function slugify(title: string): string {
  return title
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48)
    .replace(/-+$/g, "");
}

export function emptyProject(overrides: Partial<ProjectDraft> = {}): ProjectDraft {
  return {
    key: nextKey(),
    title: "",
    slug: "",
    slugEdited: false,
    repo: "",
    branch: "main",
    priority: 1,
    goal: "",
    dod: [""],
    scopePaths: [],
    envNeeded: true,
    previewEnabled: true,
    previewSlots: DEFAULT_SLOTS,
    demoFocus: "",
    versionPath: "",
    budgetIcs: DEFAULT_BUDGET,
    constraints: "",
    ...overrides,
  };
}

export function emptyFactoryDraft(): FactoryDraft {
  return { name: "Atlas factory", cadence: "demo", projects: [emptyProject()] };
}

/** A copy of `project` with a fresh key and a slug that doesn't clash with `taken`. */
export function duplicateProject(project: ProjectDraft, taken: readonly string[]): ProjectDraft {
  const base = (project.slug || slugify(project.title) || "project").slice(0, 44);
  let n = 2;
  let slug = `${base}-${n}`;
  while (taken.includes(slug)) {
    n += 1;
    slug = `${base}-${n}`;
  }
  return {
    ...structuredClone(project),
    key: nextKey(),
    title: project.title ? `${project.title} (copy)` : "",
    slug,
    slugEdited: true,
  };
}

/** The title changed: follow it with the slug unless the slug was edited. */
export function withTitle(project: ProjectDraft, title: string): ProjectDraft {
  return project.slugEdited ? { ...project, title } : { ...project, title, slug: slugify(title) };
}

/** The slug was typed; clearing it hands it back to the title. */
export function withSlug(project: ProjectDraft, slug: string): ProjectDraft {
  const clean = slug.toLowerCase().replace(/\s+/g, "-");
  if (clean === "") return { ...project, slug: slugify(project.title), slugEdited: false };
  return { ...project, slug: clean, slugEdited: true };
}

const lines = (items: readonly string[]) => items.map((i) => i.trim()).filter(Boolean);

function itemError(items: readonly string[], what: string, max: number): string | undefined {
  for (const item of lines(items)) {
    if (item.includes("\n")) return `Each ${what} is one line`;
    if (item.length > max) return `Each ${what} is at most ${max} characters`;
  }
  return undefined;
}

export interface ValidateOptions {
  /** Slugs already used by other projects of the factory. */
  takenSlugs?: readonly string[];
  /** Charter-only edits skip the fields a PATCH cannot change. */
  charterOnly?: boolean;
}

/** Inline errors for one project section (empty when it can be sent). */
export function validateProject(
  project: ProjectDraft,
  options: ValidateOptions = {},
): ProjectErrors {
  const errors: ProjectErrors = {};
  if (!options.charterOnly) {
    if (project.title.length > TITLE_MAX) errors.title = `At most ${TITLE_MAX} characters`;
    else if (project.title.includes("\n")) errors.title = "One line";
    if (!project.slug) errors.slug = "A slug is required";
    else if (!SLUG_RE.test(project.slug)) {
      errors.slug = "Lowercase letters, digits and inner dashes; 1–48 characters";
    } else if (options.takenSlugs?.includes(project.slug)) {
      errors.slug = `“${project.slug}” is already used`;
    }
    if (!project.repo.trim()) errors.repo = "Pick a repository";
    else if (!REPO_RE.test(project.repo.trim())) errors.repo = "Use owner/name";
    if (project.branch.trim() && !BRANCH_RE.test(project.branch.trim())) {
      errors.branch = "Not a valid branch name";
    }
  }
  const goal = project.goal.trim();
  if (!goal) errors.goal = "Say what the project should achieve";
  else if (goal.length > GOAL_MAX) errors.goal = `At most ${GOAL_MAX} characters`;
  const dod = lines(project.dod);
  if (dod.length === 0) errors.dod = "Add at least one check besides the fixed one";
  else if (dod.length > DOD_MAX_ITEMS) errors.dod = `At most ${DOD_MAX_ITEMS} checks`;
  else {
    const bad = itemError(project.dod, "check", DOD_ITEM_MAX);
    if (bad) errors.dod = bad;
  }
  if (lines(project.scopePaths).length > SCOPE_MAX_ITEMS) {
    errors.scope = `At most ${SCOPE_MAX_ITEMS} paths`;
  } else {
    const bad = itemError(project.scopePaths, "path", SCOPE_ITEM_MAX);
    if (bad) errors.scope = bad;
  }
  if (project.demoFocus.trim().length > DEMO_MAX) errors.demo = `At most ${DEMO_MAX} characters`;
  if (project.constraints.trim().length > CONSTRAINTS_MAX) {
    errors.constraints = `At most ${CONSTRAINTS_MAX} characters`;
  }
  const version = project.versionPath.trim();
  if (version && !URL_PATH_RE.test(version)) errors.version = "A URL path like /version";
  return errors;
}

/** Inline errors for the whole start form. */
export function validateFactory(draft: FactoryDraft): FormErrors {
  const errors: FormErrors = { byProject: [] };
  const name = draft.name.trim();
  if (!name) errors.name = "Name the factory";
  else if (name.length > NAME_MAX) errors.name = `At most ${NAME_MAX} characters`;
  else if (name.includes("\n")) errors.name = "One line";
  if (draft.projects.length === 0) errors.projects = "Add at least one project";
  else if (draft.projects.length > MAX_PROJECTS) {
    errors.projects = `At most ${MAX_PROJECTS} projects (a factory has ${FACTORY_MAX_PODS} pods: 2 leads plus one PL per project)`;
  }
  errors.byProject = draft.projects.map((project, i) =>
    validateProject(project, {
      takenSlugs: draft.projects.filter((_, j) => j !== i).map((p) => p.slug),
    }),
  );
  return errors;
}

export function hasErrors(errors: FormErrors): boolean {
  return (
    errors.name != null ||
    errors.projects != null ||
    errors.byProject.some((p) => Object.keys(p).length > 0)
  );
}

/** Previews are on only with an env. */
export function previewsOn(project: Pick<ProjectDraft, "envNeeded" | "previewEnabled">): boolean {
  return project.envNeeded && project.previewEnabled;
}

/** The request body for one project (start form and Add project). */
export function toProjectInput(project: ProjectDraft): FactoryProjectInput {
  const on = previewsOn(project);
  return {
    title: project.title.trim() || project.slug,
    slug: project.slug,
    repo: project.repo.trim(),
    base_branch: project.branch.trim(),
    goal: project.goal.trim(),
    dod: lines(project.dod),
    scope_paths: lines(project.scopePaths),
    constraints: project.constraints.trim() || null,
    env_needed: project.envNeeded,
    preview: { enabled: on, slots: project.previewSlots },
    demo_focus: on ? project.demoFocus.trim() || null : null,
    version_path: project.versionPath.trim() || null,
    priority: project.priority,
    budget_ics: project.budgetIcs,
  };
}

/** A stored project as an editable draft (Edit charter). */
export function draftFromProject(project: ProjectView): ProjectDraft {
  const charter = project.charter;
  return emptyProject({
    title: project.title,
    slug: project.slug,
    slugEdited: true,
    repo: project.repo ?? "",
    branch: project.base_branch ?? "",
    priority: Math.min(2, Math.max(0, project.priority)) as Priority,
    goal: charter?.goal ?? "",
    dod: charter?.dod.length ? [...charter.dod] : [""],
    scopePaths: [...(charter?.scope_paths ?? [])],
    envNeeded: charter?.env_needed ?? true,
    previewEnabled: charter?.preview.enabled ?? false,
    previewSlots: charter?.preview.slots ?? DEFAULT_SLOTS,
    demoFocus: charter?.demo_focus ?? charter?.demo ?? "",
    versionPath: charter?.version_path ?? "",
    budgetIcs: charter?.budget_ics ?? DEFAULT_BUDGET,
    constraints: charter?.constraints ?? "",
  });
}

// ── Charter diff (Edit charter) ────────────────────────────────

/** One changed charter field: the before and after lines a diff shows. */
export interface CharterChange {
  field: string;
  label: string;
  before: string[];
  after: string[];
}

function previewWords(enabled: boolean, slots: number): string {
  return enabled ? `on · ${slots} ${slots === 1 ? "slot" : "slots"}` : "off";
}

/**
 * What a charter edit changes, and the PATCH that sends only those fields.
 *
 * @param before - The stored project.
 * @param after - The edited draft.
 */
export function charterChanges(
  before: ProjectView,
  after: ProjectDraft,
): { changes: CharterChange[]; patch: CharterPatchRequest } {
  const charter: CharterView | null = before.charter;
  const changes: CharterChange[] = [];
  const patch: CharterPatchRequest = {};
  const text = (value: string | null | undefined) => (value ?? "").trim();
  const list = (value: readonly string[] | null | undefined) => lines(value ?? []);
  const sameList = (a: string[], b: string[]) =>
    a.length === b.length && a.every((v, i) => v === b[i]);

  const goal = after.goal.trim();
  if (goal !== text(charter?.goal)) {
    patch.goal = goal;
    changes.push({ field: "goal", label: "Goal", before: [text(charter?.goal)], after: [goal] });
  }
  const dod = lines(after.dod);
  if (!sameList(dod, list(charter?.dod))) {
    patch.dod = dod;
    changes.push({
      field: "dod",
      label: "Definition of done",
      before: list(charter?.dod),
      after: dod,
    });
  }
  const scope = lines(after.scopePaths);
  if (!sameList(scope, list(charter?.scope_paths))) {
    patch.scope_paths = scope;
    changes.push({
      field: "scope_paths",
      label: "Scope paths",
      before: list(charter?.scope_paths),
      after: scope,
    });
  }
  const constraints = after.constraints.trim();
  if (constraints !== text(charter?.constraints)) {
    patch.constraints = constraints || null;
    changes.push({
      field: "constraints",
      label: "Constraints",
      before: text(charter?.constraints) ? [text(charter?.constraints)] : [],
      after: constraints ? [constraints] : [],
    });
  }
  const wasOn = charter?.preview.enabled ?? false;
  const wasSlots = charter?.preview.slots ?? DEFAULT_SLOTS;
  const isOn = previewsOn(after);
  if (isOn !== wasOn || (isOn && after.previewSlots !== wasSlots)) {
    patch.preview = { enabled: isOn, slots: after.previewSlots };
    changes.push({
      field: "preview",
      label: "Preview & demo",
      before: [previewWords(wasOn, wasSlots)],
      after: [previewWords(isOn, after.previewSlots)],
    });
  }
  const focus = isOn ? after.demoFocus.trim() : text(charter?.demo_focus ?? charter?.demo);
  const wasFocus = text(charter?.demo_focus ?? charter?.demo);
  if (focus !== wasFocus) {
    patch.demo_focus = focus || null;
    changes.push({
      field: "demo_focus",
      label: "Demo should show",
      before: wasFocus ? [wasFocus] : [],
      after: focus ? [focus] : [],
    });
  }
  if (after.budgetIcs !== (charter?.budget_ics ?? DEFAULT_BUDGET)) {
    patch.budget_ics = after.budgetIcs;
    changes.push({
      field: "budget_ics",
      label: "IC budget",
      before: [`up to ${charter?.budget_ics ?? DEFAULT_BUDGET} ICs`],
      after: [`up to ${after.budgetIcs} ICs`],
    });
  }
  if (after.priority !== before.priority) {
    patch.priority = after.priority;
    changes.push({
      field: "priority",
      label: "Priority",
      before: [`P${before.priority}`],
      after: [`P${after.priority}`],
    });
  }
  return { changes, patch };
}

// ── Live summary ───────────────────────────────────────────────

/** Heartbeat seconds per top-level role and cadence (`CADENCE_HEARTBEATS`). */
export const CADENCE_BEATS: Record<FactoryCadence, { lead: number; pl: number }> = {
  demo: { lead: 300, pl: 600 },
  normal: { lead: 3600, pl: 7200 },
};

/** `"5m / 10m"`: the lead and PL heartbeat intervals of a cadence. */
export function cadenceBeatsText(cadence: FactoryCadence): string {
  const fmt = (s: number) => (s >= 3600 ? `${s / 3600}h` : `${s / 60}m`);
  const beats = CADENCE_BEATS[cadence];
  return `${fmt(beats.lead)} / ${fmt(beats.pl)}`;
}

/**
 * The most heartbeat turns an hour can cost: every lead and PL beating on
 * schedule (quiet beats skip some, so it's a ceiling).
 */
export function heartbeatsPerHour(cadence: FactoryCadence, leads: number, pls: number): number {
  const beats = CADENCE_BEATS[cadence];
  return Math.round((leads * 3600) / beats.lead + (pls * 3600) / beats.pl);
}

export interface StartSummary {
  sandboxes: number;
  maxPreviews: number;
  /** The previews the projects ask for before the factory cap. */
  requestedPreviews: number;
  heartbeats: number;
  repos: string[];
}

export function startSummary(draft: FactoryDraft): StartSummary {
  const requested = draft.projects.reduce(
    (sum, p) => sum + (previewsOn(p) ? p.previewSlots : 0),
    0,
  );
  const repos = [...new Set(draft.projects.map((p) => p.repo.trim()).filter(Boolean))];
  return {
    sandboxes: 2 + draft.projects.length,
    maxPreviews: Math.min(requested, FACTORY_PREVIEW_CAP),
    requestedPreviews: requested,
    heartbeats: heartbeatsPerHour(draft.cadence, 2, draft.projects.length),
    repos,
  };
}

// ── Load example (spec §12) ────────────────────────────────────

/** The three-project demo scenario. */
export function exampleProjects(): ProjectDraft[] {
  const repo = "caffeinelabs/app";
  return [
    emptyProject({
      title: "API status endpoint",
      slug: "api-status",
      repo,
      branch: "main",
      priority: 1,
      goal: 'Add GET /status to caffeine-api returning {service, uptimeS, db: "ok" | "down"}.',
      dod: ['curl -fsS {api}/status → 200 with "db":"ok"', "pnpm -F @caffeine/api test"],
      scopePaths: ["services/caffeine-api/**"],
      envNeeded: true,
      previewEnabled: true,
      previewSlots: 2,
      demoFocus: 'Call GET /status and show "db":"ok" in the response.',
    }),
    emptyProject({
      title: "ENV badge in the UX header",
      slug: "ux-env-badge",
      repo,
      branch: "main",
      priority: 1,
      goal: "Show an “ENV <id>” badge in the caffeine-ux header when served from *.envs.sandbox.caffeinelabs.io.",
      dod: [
        "The badge shows on *.envs.sandbox.caffeinelabs.io and not elsewhere",
        "pnpm -F @caffeine/ux test",
      ],
      scopePaths: ["services/caffeine-ux/**"],
      envNeeded: true,
      previewEnabled: true,
      previewSlots: 2,
      demoFocus: "Open the app in the preview, show the ENV badge in the header and hover it.",
      constraints:
        "Ask the human (sys_factory_ask) whether the badge goes in the header or the footer, and keep building meanwhile.",
    }),
    emptyProject({
      title: "Docs: factory envs",
      slug: "docs-factory-envs",
      repo,
      branch: "main",
      priority: 2,
      goal: "Add docs/factory-envs.md: how factory envs and previews work and how to review them.",
      dod: ["docs/factory-envs.md renders on GitHub"],
      scopePaths: ["docs/**"],
      envNeeded: false,
      previewEnabled: false,
      demoFocus: "",
    }),
  ];
}

// ── Draft (localStorage) ───────────────────────────────────────

export const DRAFT_KEY = "omnigent.factory.newDraft";

export interface StoredDraft {
  savedAt: number;
  draft: FactoryDraft;
}

function isDraft(value: unknown): value is StoredDraft {
  if (typeof value !== "object" || value === null) return false;
  const { savedAt, draft } = value as Partial<StoredDraft>;
  return (
    typeof savedAt === "number" &&
    typeof draft === "object" &&
    draft !== null &&
    typeof draft.name === "string" &&
    Array.isArray(draft.projects)
  );
}

/** The saved draft, or null when there is none (or storage is unavailable). */
export function loadDraft(): StoredDraft | null {
  try {
    const raw = window.localStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!isDraft(parsed)) return null;
    // Fill fields added since the draft was saved.
    const projects = parsed.draft.projects.map((p) => ({ ...emptyProject(), ...p }));
    return { savedAt: parsed.savedAt, draft: { ...parsed.draft, projects } };
  } catch {
    return null;
  }
}

export function saveDraft(draft: FactoryDraft, now = Date.now()): void {
  try {
    window.localStorage.setItem(DRAFT_KEY, JSON.stringify({ savedAt: now, draft }));
  } catch {
    // Storage full or blocked: the form still works, it just isn't kept.
  }
}

export function clearDraft(): void {
  try {
    window.localStorage.removeItem(DRAFT_KEY);
  } catch {
    // Nothing to clear when storage is unavailable.
  }
}

/** Whether a draft holds anything worth restoring. */
export function draftHasContent(draft: FactoryDraft): boolean {
  return draft.projects.some(
    (p) => p.title.trim() || p.goal.trim() || p.repo.trim() || lines(p.dod).length > 0,
  );
}

// ── Server field errors ────────────────────────────────────────

/** A server message as a sentence: `"slug is …"` → `"Slug is …"`. */
export function sentence(message: string): string {
  return message ? message.charAt(0).toUpperCase() + message.slice(1) : message;
}

const SERVER_FIELDS: Record<string, ProjectField> = {
  title: "title",
  slug: "slug",
  repo: "repo",
  base_branch: "branch",
  goal: "goal",
  dod: "dod",
  scope_paths: "scope",
  constraints: "constraints",
  env_needed: "env",
  preview: "slots",
  preview_slots: "slots",
  demo: "demo",
  demo_focus: "demo",
  version_path: "version",
  budget_ics: "budget",
  priority: "priority",
};

/**
 * Where a server 400's `field` lands on the form: `projects[1].slug` →
 * project 1's slug, `name` → the name. A bare project field (Add project,
 * Edit charter) lands on project 0.
 */
export function mapServerField(
  field: string | null | undefined,
): { project: number; field: ProjectField } | { name: true } | null {
  if (!field) return null;
  if (field === "name") return { name: true };
  const match = /^(?:projects\[(\d+)\]\.)?([a-z_]+)/.exec(field);
  if (!match) return null;
  const mapped = SERVER_FIELDS[match[2] ?? ""];
  if (!mapped) return null;
  return { project: match[1] ? Number(match[1]) : 0, field: mapped };
}
