// Web paths of the factory pages. Every primary object has its own page under
// `/factory/<fid>`: the Overview, Projects, a project, a PR, Activity and
// Settings. The project page's sections are anchors (`#demos`), not tabs.

/** The project page's sections, in page order; Overview is the top of the page. */
export const PROJECT_SECTIONS = [
  "overview",
  "tasks",
  "prs",
  "demos",
  "previews",
  "decisions",
  "team",
  "loops",
  "activity",
] as const;
export type ProjectSection = (typeof PROJECT_SECTIONS)[number];

/** The section a `#hash` or an old `?tab=` value names, else null. */
export function projectSection(value: string | null | undefined): ProjectSection | null {
  const key = (value ?? "").replace(/^#/, "");
  return PROJECT_SECTIONS.find((section) => section === key) ?? null;
}

/** DOM id of a project page section (the `#hash` target). */
export function projectSectionId(section: ProjectSection): string {
  return section;
}

/** Settings sections under `/factory/<fid>/settings/<section>`. */
export const SETTINGS_SECTIONS = ["people"] as const;
export type SettingsSection = (typeof SETTINGS_SECTIONS)[number];

const enc = encodeURIComponent;

function withQuery(path: string, params?: Record<string, string | number>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params ?? {})) query.set(key, String(value));
  const qs = query.toString();
  return qs ? `${path}?${qs}` : path;
}

/** `/factory/<fid>`, the Overview. */
export function factoryPageHref(factoryId: string): string {
  return `/factory/${enc(factoryId)}`;
}

/** `/factory/<fid>/projects`. */
export function projectsPageHref(factoryId: string): string {
  return `${factoryPageHref(factoryId)}/projects`;
}

/**
 * `/factory/<fid>/projects/<slug>`, scrolled to `section` (the top when
 * omitted), with any extra query parameters (e.g. `decision`).
 */
export function projectPageHref(
  factoryId: string,
  slug: string,
  options: { section?: ProjectSection; params?: Record<string, string | number> } = {},
): string {
  const path = withQuery(`${projectsPageHref(factoryId)}/${enc(slug)}`, options.params);
  return options.section && options.section !== "overview" ? `${path}#${options.section}` : path;
}

/** `/factory/<fid>/prs/<pr id>` (a PR number also resolves there). */
export function prPageHref(
  factoryId: string,
  prId: string | number,
  params?: Record<string, string | number>,
): string {
  return withQuery(`${factoryPageHref(factoryId)}/prs/${enc(String(prId))}`, params);
}

/** `/factory/<fid>/activity`. */
export function activityPageHref(factoryId: string): string {
  return `${factoryPageHref(factoryId)}/activity`;
}

/** `/factory/<fid>/settings/<section>`. */
export function settingsPageHref(factoryId: string, section: SettingsSection = "people"): string {
  return `${factoryPageHref(factoryId)}/settings/${section}`;
}

/** `/factory/<fid>/room/<thread>` (the Room page; the thread is optional). */
export function roomPageHref(factoryId: string, thread?: string | null): string {
  return `${factoryPageHref(factoryId)}/room${thread ? `/${enc(thread)}` : ""}`;
}

/** `/factory/<fid>/decisions` (open first; `?decision=D-7` highlights one). */
export function decisionsPageHref(
  factoryId: string,
  params?: Record<string, string | number>,
): string {
  return withQuery(`${factoryPageHref(factoryId)}/decisions`, params);
}

export type GraphPageKind = "work" | "bus" | "flow" | "org";

/**
 * `/factory/<fid>/graph?kind=work` (Work when omitted), optionally one
 * project (`scope=`: a factory URL's `?project=` opens the project page) or task.
 */
export function graphPageHref(
  factoryId: string,
  options: { kind?: GraphPageKind; project?: string | null; task?: string | null } = {},
): string {
  const params: Record<string, string> = {};
  if (options.kind && options.kind !== "work") params.kind = options.kind;
  if (options.project) params.scope = options.project;
  if (options.task) params.task = options.task;
  return withQuery(`${factoryPageHref(factoryId)}/graph`, params);
}

/** `/factory/<fid>/alerts/<n>`: an incident's page. */
export function incidentPageHref(factoryId: string, number: string | number): string {
  return `${factoryPageHref(factoryId)}/alerts/${enc(String(number))}`;
}

/** `/factory/<fid>/improvements/<n>`: a proposal's or finding's page. */
export function improvementPageHref(factoryId: string, ref: string): string {
  return `${factoryPageHref(factoryId)}/improvements/${enc(ref)}`;
}
