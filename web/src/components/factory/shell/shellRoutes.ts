// Pure routing logic of the factory shell: which page a sub-path is, where the
// viewer is for presence, where an old cockpit link (the board's
// `?project=`, `?review=`, `?tab=activity`, the stable preview link's
// `?pr=…&preview=unavailable`) lands now, and how the classic and the new
// cockpit's addresses map onto each other (`cockpitRedirect`). Tests pin
// every legacy shape.

import type { PrView } from "@/lib/factoryApi";
import {
  DEBUG_VIEWS,
  type DebugView,
  STEER_SECTIONS,
  type SteerSection,
  activityPageHref,
  debugPageHref,
  factoryPageHref,
  prPageHref,
  projectPageHref,
  projectSection,
  steerPageHref,
} from "../factoryRoutes";

/** The pages the shell routes to, keyed by their first path segment. */
export type FactoryPageKey =
  | "overview"
  | "projects"
  | "project"
  | "pr"
  | "activity"
  | "settings"
  | "room"
  | "decisions"
  | "graph"
  | "agents"
  | "agent"
  | "timeline"
  | "alerts"
  | "incident"
  | "insights"
  | "improvements"
  | "proposal"
  | "discovery"
  | "teams"
  | "steer"
  | "debug"
  | "other";

export interface FactoryLocation {
  page: FactoryPageKey;
  /**
   * The project slug, PR id, settings section, Room thread, Discovery view,
   * Teams tab, Steer section or Debug view the page is about.
   */
  id: string | null;
  /** Under a Debug view: the incident ref or the agent's session id. */
  sub?: string | null;
}

/**
 * Which page `rest` (the path after `/factory/<fid>/`) is.
 *
 * @example factoryLocation("projects/db-version") // {page: "project", id: "db-version"}
 */
export function factoryLocation(rest: string | undefined): FactoryLocation {
  const [first = "", second, third] = (rest ?? "")
    .split("/")
    .filter(Boolean)
    .map(decodeURIComponent);
  switch (first) {
    case "discovery":
      return { page: "discovery", id: second ?? null };
    case "teams":
      return { page: "teams", id: second ?? null };
    case "steer":
      return { page: "steer", id: second ?? null };
    case "debug":
      return third
        ? { page: "debug", id: second ?? null, sub: third }
        : { page: "debug", id: second ?? null };
    case "":
      return { page: "overview", id: null };
    case "projects":
      return second ? { page: "project", id: second } : { page: "projects", id: null };
    case "prs":
      return second ? { page: "pr", id: second } : { page: "other", id: null };
    case "activity":
      return { page: "activity", id: null };
    case "settings":
      return { page: "settings", id: second ?? "people" };
    case "room":
      return { page: "room", id: second ?? null };
    case "decisions":
      return { page: "decisions", id: null };
    case "graph":
      return { page: "graph", id: null };
    case "agents":
      return second ? { page: "agent", id: second } : { page: "agents", id: null };
    case "timeline":
      return { page: "timeline", id: null };
    case "alerts":
      return second ? { page: "incident", id: second } : { page: "alerts", id: null };
    case "insights":
      return { page: "insights", id: null };
    case "improvements":
      return second ? { page: "proposal", id: second } : { page: "improvements", id: null };
    default:
      return { page: "other", id: first };
  }
}

/** The presence `location` the server shows next to a person (`overview`, `project:<slug>`, …). */
export function presenceLocation(location: FactoryLocation): string | null {
  switch (location.page) {
    case "overview":
      return "overview";
    case "projects":
      return "projects";
    case "project":
      return `project:${location.id}`;
    case "pr":
      return `pr:${location.id}`;
    case "activity":
      return "activity";
    case "settings":
      return "settings";
    case "room":
      return location.id ? `room:${location.id}` : "room";
    case "decisions":
      return "decisions";
    case "graph":
      return "graph";
    case "agents":
    case "timeline":
    case "alerts":
    case "insights":
    case "improvements":
      return location.page;
    case "proposal":
      return `proposal:${location.id}`;
    case "agent":
      return `agent:${location.id}`;
    case "incident":
      return `incident:${location.id}`;
    case "discovery":
    case "teams":
      return location.page;
    case "steer":
      return location.id ? `steer:${location.id}` : "steer";
    case "debug":
      if (location.id === "agent" && location.sub) return `agent:${location.sub}`;
      if (location.id === "incidents" && location.sub) return `incident:${location.sub}`;
      return location.id ? `debug:${location.id}` : "debug";
    default:
      return null;
  }
}

/** `"db-version page"`, `"Overview"`, `"PR !9631"`: where someone is, in words. */
export function presenceWhere(location: string | null | undefined): string {
  if (!location) return "here";
  const [kind, id] = location.split(/:(.*)/s);
  switch (kind) {
    case "overview":
      return "Overview";
    case "projects":
      return "Projects";
    case "project":
      return `${id} page`;
    case "pr":
      return "a PR page";
    case "activity":
      return "Activity";
    case "settings":
      return "Settings";
    case "room":
      return id ? `Room ${id}` : "the Room";
    case "decisions":
      return "Decisions";
    case "graph":
      return "Graph";
    case "agent":
      return "an agent page";
    case "agents":
      return "Agents";
    case "timeline":
      return "the Timeline";
    case "alerts":
      return "Alerts";
    case "incident":
      return "an incident";
    case "insights":
      return "Insights";
    case "improvements":
      return "Improvements";
    case "proposal":
      return id ? `${id}` : "a proposal";
    case "discovery":
      return "Discovery";
    case "teams":
      return "Teams";
    case "steer":
      return "Steer";
    case "debug":
      return "Debug";
    default:
      return kind ?? "here";
  }
}

/** A PR named by id or number in the snapshot's list. */
export function findPr(prs: readonly PrView[], ref: string): PrView | null {
  return prs.find((pr) => pr.id === ref) ?? prs.find((pr) => String(pr.number) === ref) ?? null;
}

const KEPT_PARAMS = ["decision", "preview"] as const;

function kept(params: URLSearchParams): Record<string, string> {
  const out: Record<string, string> = {};
  for (const key of KEPT_PARAMS) {
    const value = params.get(key);
    if (value) out[key] = value;
  }
  return out;
}

/** Where an old link goes: a path, "wait for the PR list", or nowhere (no legacy params). */
export type LegacyTarget = { to: string } | "wait" | null;

/**
 * Where an old cockpit link lands now.
 *
 * - `?review=<n>` (PR bodies, demo comments) and `?pr=<id>` (the stable preview
 *   link, with `&preview=unavailable`) → `/prs/<id>`; a number is resolved
 *   against `prs`, so the answer is "wait" until the snapshot is in.
 * - `?project=<slug>` (the old drawer) → `/projects/<slug>`.
 * - `?tab=activity` → `/activity`; `?tab=board` → the Overview.
 * - On a project page, `?tab=<section>` → `#<section>`.
 *
 * `decision` and `preview` carry over.
 *
 * @param rest - The path after `/factory/<fid>/` (the page the link opened).
 * @param prs - The snapshot's PRs, or undefined while it loads.
 */
export function legacyFactoryRedirect(
  factoryId: string,
  rest: string | undefined,
  params: URLSearchParams,
  prs: readonly PrView[] | undefined,
): LegacyTarget {
  const location = factoryLocation(rest);
  const review = params.get("review");
  const prId = params.get("pr");
  if (review || (prId && location.page !== "pr")) {
    if (prId) return { to: prPageHref(factoryId, prId, kept(params)) };
    if (!prs) return "wait";
    const pr = findPr(prs, review!);
    return { to: prPageHref(factoryId, pr?.id ?? review!, kept(params)) };
  }
  const project = params.get("project");
  if (project) return { to: projectPageHref(factoryId, project, { params: kept(params) }) };
  const tab = params.get("tab");
  if (tab && location.page === "overview") {
    const remaining = new URLSearchParams(params);
    remaining.delete("tab");
    const qs = remaining.toString();
    const base = tab === "activity" ? activityPageHref(factoryId) : factoryPageHref(factoryId);
    return { to: qs ? `${base}?${qs}` : base };
  }
  if (tab && location.page === "project" && location.id) {
    const section = projectSection(tab);
    return {
      to: projectPageHref(factoryId, location.id, {
        ...(section ? { section } : {}),
        params: kept(params),
      }),
    };
  }
  return null;
}

// ── Classic and new cockpit addresses ──────────────────────────

/** Which cockpit the viewer sees. */
export type CockpitMode = "classic" | "v2";

/** The link parameter that carries a cockpit choice (read by `useCockpitV2`, never carried on). */
const COCKPIT_LINK_PARAM = "cockpit";

function hrefWith(path: string, params: URLSearchParams, drop: readonly string[] = []): string {
  const rest = new URLSearchParams(params);
  for (const key of [...drop, COCKPIT_LINK_PARAM]) rest.delete(key);
  const qs = rest.toString();
  if (!qs) return path;
  const [base, hash] = path.split("#");
  const sep = base!.includes("?") ? "&" : "?";
  return `${base}${sep}${qs}${hash != null ? `#${hash}` : ""}`;
}

/** Settings sections that moved to Steer, and where. */
const SETTINGS_TO_STEER: Readonly<Record<string, SteerSection>> = {
  autonomy: "budgets",
  budgets: "budgets",
  alerts: "alerts",
  rules: "alerts",
  definition: "definition",
};

/** Steer's sections back to where the classic cockpit keeps them. */
const STEER_TO_SETTINGS: Readonly<Record<Exclude<SteerSection, "versions">, string>> = {
  budgets: "budgets",
  alerts: "alerts",
  definition: "definition",
};

/** Debug's views back to the classic pages. */
const DEBUG_TO_CLASSIC: Readonly<Record<DebugView, string>> = {
  fleet: "agents",
  timeline: "timeline",
  activity: "activity",
  graph: "graph",
  incidents: "alerts",
  insights: "insights",
  agent: "agents",
};

/**
 * Where an address of the other cockpit lands in this one, or null when it
 * belongs here. Query parameters carry over (a consumed `tab` is dropped).
 *
 * The new cockpit (spec §2.2): `/overview` → `/`, `/agents` → `/teams/agents`,
 * `/graph?kind=org` → `/teams`, `/graph` → `/debug/graph`, `/timeline`,
 * `/activity`, `/insights` → `/debug/<page>`, `/alerts[/<ref>]` →
 * `/debug/incidents[/<ref>]`, `/decisions` → `/?peek=needs`,
 * `/improvements?tab=` → `/steer/versions` (Rules → `/steer/alerts`, Findings
 * → Debug › Incidents and findings), `/improvements/<ref>` →
 * `/steer/versions?peek=<ref>`, `/settings/{autonomy,budgets}` →
 * `/steer/budgets`, `/settings/{alerts,rules}` → `/steer/alerts`,
 * `/settings/definition` → `/steer/definition`, `/room[/<thread>]` → the page
 * the viewer was on (`from`, else Home) with `?room=<thread>`.
 *
 * The classic cockpit maps the new places back to its pages, so a link one
 * person copied from the new cockpit opens for someone on the classic one.
 *
 * @param from - The factory page (path and query) the viewer was on before a Room link.
 */
export function cockpitRedirect(
  factoryId: string,
  rest: string | undefined,
  params: URLSearchParams,
  mode: CockpitMode,
  from: string | null = null,
): string | null {
  const base = factoryPageHref(factoryId);
  const location = factoryLocation(rest);
  // Both cockpits.
  if (location.page === "other" && location.id === "overview") return hrefWith(base, params);
  if (location.page === "settings" && location.id === "notify") {
    return hrefWith(`${base}/settings/notifications`, params);
  }
  return mode === "v2"
    ? toNewCockpit(factoryId, location, params, from)
    : toClassicCockpit(factoryId, location, params);
}

function toNewCockpit(
  factoryId: string,
  location: FactoryLocation,
  params: URLSearchParams,
  from: string | null,
): string | null {
  const base = factoryPageHref(factoryId);
  switch (location.page) {
    case "agents":
      return hrefWith(`${base}/teams/agents`, params);
    case "graph":
      return params.get("kind") === "org"
        ? hrefWith(`${base}/teams`, params, ["kind"])
        : hrefWith(debugPageHref(factoryId, "graph"), params);
    case "timeline":
    case "activity":
    case "insights":
      return hrefWith(debugPageHref(factoryId, location.page), params);
    case "alerts":
      return hrefWith(debugPageHref(factoryId, "incidents"), params);
    case "incident":
      return hrefWith(debugPageHref(factoryId, "incidents", { ref: location.id }), params);
    case "decisions":
      return hrefWith(`${base}?peek=needs`, params, ["peek"]);
    case "improvements": {
      const tab = params.get("tab");
      if (tab === "rules") return hrefWith(steerPageHref(factoryId, "alerts"), params, ["tab"]);
      if (tab === "findings") {
        return hrefWith(
          debugPageHref(factoryId, "incidents", { params: { class: "finding" } }),
          params,
          ["tab", "class"],
        );
      }
      return hrefWith(steerPageHref(factoryId, "versions"), params);
    }
    case "proposal":
      return hrefWith(steerPageHref(factoryId, "versions", { peek: location.id ?? "" }), params, [
        "peek",
      ]);
    case "settings": {
      const section = SETTINGS_TO_STEER[location.id ?? ""];
      return section ? hrefWith(steerPageHref(factoryId, section), params) : null;
    }
    case "room": {
      const target = from ?? base;
      const [path, query = ""] = target.split("?");
      const merged = new URLSearchParams(query);
      for (const [key, value] of params) merged.set(key, value);
      merged.delete(COCKPIT_LINK_PARAM);
      merged.set("room", location.id ?? "");
      return `${path}?${merged.toString()}`;
    }
    case "steer":
      return location.id && (STEER_SECTIONS as readonly string[]).includes(location.id)
        ? null
        : hrefWith(steerPageHref(factoryId, "versions"), params);
    case "debug":
      return location.id && (DEBUG_VIEWS as readonly string[]).includes(location.id)
        ? null
        : hrefWith(debugPageHref(factoryId, "fleet"), params);
    default:
      return null;
  }
}

function toClassicCockpit(
  factoryId: string,
  location: FactoryLocation,
  params: URLSearchParams,
): string | null {
  const base = factoryPageHref(factoryId);
  switch (location.page) {
    case "overview":
      return params.get("peek") === "needs"
        ? hrefWith(`${base}/decisions`, params, ["peek"])
        : null;
    case "discovery":
      return hrefWith(base, params);
    case "teams":
      return hrefWith(`${base}/agents`, params);
    case "steer": {
      const section = location.id as SteerSection | null;
      if (section && section !== "versions" && section in STEER_TO_SETTINGS) {
        return hrefWith(`${base}/settings/${STEER_TO_SETTINGS[section]}`, params);
      }
      const peek = params.get("peek");
      if (peek && /^C-\d+$/.test(peek)) {
        return hrefWith(`${base}/improvements/${encodeURIComponent(peek)}`, params, ["peek"]);
      }
      return hrefWith(`${base}/improvements`, params);
    }
    case "debug": {
      const view = (DEBUG_VIEWS as readonly string[]).includes(location.id ?? "")
        ? (location.id as DebugView)
        : "fleet";
      const page = DEBUG_TO_CLASSIC[view];
      const tail =
        location.sub && (view === "incidents" || view === "agent")
          ? `/${encodeURIComponent(location.sub)}`
          : "";
      return hrefWith(`${base}/${page}${tail}`, params);
    }
    case "settings":
      return location.id === "integrations" ? hrefWith(`${base}/settings/alerts`, params) : null;
    default:
      return null;
  }
}
