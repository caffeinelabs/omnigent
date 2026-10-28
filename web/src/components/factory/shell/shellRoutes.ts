// Pure routing logic of the factory shell: which page a sub-path is, where the
// viewer is for presence, and where an old cockpit link (the board's
// `?project=`, `?review=`, `?tab=activity`, the stable preview link's
// `?pr=…&preview=unavailable`) lands now. Tests pin every legacy shape.

import type { PrView } from "@/lib/factoryApi";
import {
  activityPageHref,
  factoryPageHref,
  prPageHref,
  projectPageHref,
  projectSection,
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
  | "other";

export interface FactoryLocation {
  page: FactoryPageKey;
  /** The project slug, PR id, settings section or Room thread the page is about. */
  id: string | null;
}

/**
 * Which page `rest` (the path after `/factory/<fid>/`) is.
 *
 * @example factoryLocation("projects/db-version") // {page: "project", id: "db-version"}
 */
export function factoryLocation(rest: string | undefined): FactoryLocation {
  const [first = "", second] = (rest ?? "").split("/").filter(Boolean).map(decodeURIComponent);
  switch (first) {
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
