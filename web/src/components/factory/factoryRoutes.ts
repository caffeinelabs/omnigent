// Web paths of the factory pages. Every primary object has its own page under
// `/factory/<fid>`: the Overview, Projects, a project, a PR, Activity and
// Settings. The project page's sections are anchors (`#demos`), not tabs.
// The new cockpit adds its places (Discovery, Teams, Steer) and Debug.

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

/** The new cockpit's project tabs (`/projects/<slug>/<tab>`; Overview when omitted). */
export const PROJECT_TABS = ["overview", "work", "history", "charter"] as const;
export type ProjectTab = (typeof PROJECT_TABS)[number];

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
export const SETTINGS_SECTIONS = [
  "people",
  "notifications",
  "autonomy",
  "budgets",
  "alerts",
  "rules",
  "definition",
] as const;

/** The new cockpit's Settings: personal and access only (the rest moved to Steer). */
export const SETTINGS_V2_SECTIONS = ["people", "notifications", "integrations"] as const;
export type SettingsSection =
  (typeof SETTINGS_SECTIONS)[number] | (typeof SETTINGS_V2_SECTIONS)[number];

/** The new cockpit's places, in tab order (`/` is Home). */
export const COCKPIT_PLACES = ["home", "discovery", "projects", "teams", "steer"] as const;
export type CockpitPlace = (typeof COCKPIT_PLACES)[number];

/** Discovery's views (`/discovery/<view>`; Opportunities when omitted). */
export const DISCOVERY_VIEWS = [
  "opportunities",
  "flow",
  "map",
  "impact",
  "signals",
  "hunches",
] as const;
export type DiscoveryView = (typeof DISCOVERY_VIEWS)[number];

/**
 * Steer's sections the cockpit has today (`/steer/<section>`): how the
 * factory runs, its budgets, alerts and rules, and its versions and trials.
 */
export const STEER_SECTIONS = ["versions", "budgets", "alerts", "definition"] as const;
export type SteerSection = (typeof STEER_SECTIONS)[number];

/** Debug's views (`/debug/<view>`): today's operator pages, one level down. */
export const DEBUG_VIEWS = [
  "fleet",
  "projects",
  "timeline",
  "activity",
  "graph",
  "incidents",
  "insights",
  "agent",
] as const;
export type DebugView = (typeof DEBUG_VIEWS)[number];

/** The Improvements page's tabs (`?tab=`), On trial first. */
export const IMPROVEMENT_TABS = [
  "trial",
  "proposed",
  "history",
  "rules",
  "definition",
  "findings",
] as const;
export type ImprovementTab = (typeof IMPROVEMENT_TABS)[number];

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

/** `/factory/<fid>/projects/<slug>[/<tab>]`: a tab of the new cockpit's project page. */
export function projectTabHref(
  factoryId: string,
  slug: string,
  tab: ProjectTab = "overview",
  params?: Record<string, string | number>,
): string {
  const tail = tab === "overview" ? "" : `/${tab}`;
  return withQuery(`${projectsPageHref(factoryId)}/${enc(slug)}${tail}`, params);
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

/** `/factory/<fid>/agents`, optionally with a query (`?q=…`, `?view=looping`, `?select=…`). */
export function agentsPageHref(factoryId: string, params?: Record<string, string>): string {
  return withQuery(`${factoryPageHref(factoryId)}/agents`, params);
}

/** Teams' tabs (`/teams/<tab>`; Teams when omitted). */
export const TEAMS_TABS = ["teams", "bench", "templates", "agents"] as const;
export type TeamsTab = (typeof TEAMS_TABS)[number];

/** `/factory/<fid>/teams[/<tab>]` with an optional query (`?q=`, `?state=`, `?peek=agent:<handle>`). */
export function teamsPageHref(
  factoryId: string,
  tab: TeamsTab = "teams",
  params?: Record<string, string>,
): string {
  const path = `${factoryPageHref(factoryId)}/teams${tab === "teams" ? "" : `/${tab}`}`;
  return withQuery(path, params);
}

/** The Agent page's tabs. */
export const AGENT_TABS = [
  "live",
  "tools",
  "transcript",
  "terminal",
  "diff",
  "mail",
  "task",
  "cost",
] as const;
export type AgentTab = (typeof AGENT_TABS)[number];

/**
 * `/factory/<fid>/agents/<sid>`, on `tab` (Live when omitted), at a moment
 * (`at`, epoch ms). Naming a tab, Live included, opens the classic page's tab
 * even with the new cockpit.
 */
export function agentPageHref(
  factoryId: string,
  sessionId: string,
  options: { tab?: AgentTab; at?: number } = {},
): string {
  const params: Record<string, string | number> = {};
  if (options.tab) params.tab = options.tab;
  if (options.at != null) params.at = Math.round(options.at);
  return withQuery(`${agentsPageHref(factoryId)}/${enc(sessionId)}`, params);
}

/**
 * `/factory/<fid>/timeline`, narrowed to a project (`?focus=`; `?project=` is
 * the old drawer link) or agents, replayed `at` a moment (ms).
 */
export function timelinePageHref(
  factoryId: string,
  options: { project?: string | null; agents?: string[]; at?: number; window?: string } = {},
): string {
  const params: Record<string, string | number> = {};
  if (options.project) params.focus = options.project;
  if (options.agents?.length) params.agents = options.agents.join(",");
  if (options.window) params.window = options.window;
  if (options.at != null) params.at = Math.round(options.at);
  return withQuery(`${factoryPageHref(factoryId)}/timeline`, params);
}

/** `/factory/<fid>/alerts`. */
export function alertsPageHref(factoryId: string): string {
  return `${factoryPageHref(factoryId)}/alerts`;
}

/** An incident's or finding's page: `INC-12` → `/alerts/12`, `F-7` → `/alerts/F-7`. */
export function alertPageHref(factoryId: string, ref: string): string {
  const incident = /^INC-(\d+)$/i.exec(ref);
  return `${alertsPageHref(factoryId)}/${enc(incident ? incident[1]! : ref)}`;
}

/** `/factory/<fid>/settings/<section>`. */
export function settingsPageHref(factoryId: string, section: SettingsSection = "people"): string {
  return `${factoryPageHref(factoryId)}/settings/${section}`;
}

/** `/factory/<fid>/discovery[/<view>]`. */
export function discoveryPageHref(factoryId: string, view?: DiscoveryView): string {
  const tail = view && view !== "opportunities" ? `/${view}` : "";
  return `${factoryPageHref(factoryId)}/discovery${tail}`;
}

/** `/factory/<fid>/steer/<section>` with an optional query (`?tab=`, `?peek=C-4`). */
export function steerPageHref(
  factoryId: string,
  section: SteerSection = "versions",
  params?: Record<string, string>,
): string {
  return withQuery(`${factoryPageHref(factoryId)}/steer/${section}`, params);
}

/**
 * `/factory/<fid>/debug/<view>[/<ref>]`: an incident (`incidents/12`) or an
 * agent's depth (`agent/<sid>`) by `ref`, with the view's own query.
 */
export function debugPageHref(
  factoryId: string,
  view: DebugView = "fleet",
  options: { ref?: string | null; params?: Record<string, string> } = {},
): string {
  const tail = options.ref ? `/${enc(options.ref)}` : "";
  return withQuery(`${factoryPageHref(factoryId)}/debug/${view}${tail}`, options.params);
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

/** `/factory/<fid>/insights`, with its window, grouping and filters as a query. */
export function insightsPageHref(factoryId: string, params?: Record<string, string>): string {
  return withQuery(`${factoryPageHref(factoryId)}/insights`, params);
}

/** `/factory/<fid>/improvements` on `tab` (On trial when omitted). */
export function improvementsPageHref(
  factoryId: string,
  tab?: ImprovementTab,
  params?: Record<string, string>,
): string {
  return withQuery(`${factoryPageHref(factoryId)}/improvements`, {
    ...(tab && tab !== "trial" ? { tab } : {}),
    ...params,
  });
}
