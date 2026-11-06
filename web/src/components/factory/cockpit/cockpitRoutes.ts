// The cockpit's places and where an older address lands in them. The cockpit
// is one thing for everyone (no `?cockpit=` switch): Home, Demos, Chats,
// Decisions and the node pages (`/n/<ref>`). Addresses of the earlier
// cockpits (a project page, a PR page, an initiative, the Room, Needs you)
// open the place that shows the same thing now; the earlier pages that have
// no place here stay reachable from Debug.

import type { PrView } from "@/lib/factoryApi";
import { factoryPageHref } from "../factoryRoutes";
import { factoryLocation, findPr } from "../shell/shellRoutes";
import { nodeHref } from "../v3/nodeRoutes";

/** The four places the sidebar (and a phone's tab bar) lists, Search aside. */
export type CockpitPlace = "home" | "demos" | "chats" | "decisions";

export function demosHref(factoryId: string, params?: Record<string, string>): string {
  const qs = params ? new URLSearchParams(params).toString() : "";
  return `${factoryPageHref(factoryId)}/demos${qs ? `?${qs}` : ""}`;
}

export function chatsHref(factoryId: string, thread?: string | null): string {
  const base = `${factoryPageHref(factoryId)}/chats`;
  return thread ? `${base}/${encodeURIComponent(thread)}` : base;
}

export function decisionsHref(factoryId: string): string {
  return `${factoryPageHref(factoryId)}/decisions`;
}

/** Which place an address is in (a node page is in none: the outline marks it). */
export function placeOf(rest: string | undefined): CockpitPlace | null {
  const first = (rest ?? "").split("/").find(Boolean) ?? "";
  if (first === "") return "home";
  if (first === "demos" || first === "chats" || first === "decisions") return first;
  return null;
}

/** Parameters an older cockpit used that mean nothing here (dropped on the way). */
const DROPPED = ["cockpit", "room", "peek", "tab", "section"] as const;

function carry(path: string, params: URLSearchParams, drop: readonly string[] = []): string {
  const rest = new URLSearchParams(params);
  for (const key of [...DROPPED, ...drop]) rest.delete(key);
  const qs = rest.toString();
  return qs ? `${path}?${qs}` : path;
}

/** `PR-<n>`: the change node of a PR, by the number the snapshot knows it by. */
function prNode(factoryId: string, prs: readonly PrView[], id: string): string {
  const pr = findPr(prs, id);
  return nodeHref(factoryId, pr ? `PR-${pr.number}` : /^\d+$/.test(id) ? `PR-${id}` : id);
}

/** Where to go: a path, "wait for the PR list", or nowhere (the address belongs here). */
export type CockpitTarget = { to: string } | "wait" | null;

/**
 * Where an address of an earlier cockpit opens now.
 *
 * - `/projects/<slug>[/<tab>]` → the project's node page (its team editor,
 *   `/projects/<slug>/team`, stays); `/projects` → Home.
 * - `/prs/<id>`, `?review=<n>`, `?pr=<id>` → the PR's change node (`/n/PR-<n>`);
 *   a PR id is resolved against `prs`, so the answer is "wait" until they are in.
 * - `/initiatives/<ref>` → the feature's node page.
 * - `/room[/<thread>]` and `?room=<thread>` → Chats on that thread.
 * - `?peek=needs` → Decisions; `?project=<slug>` → the project's node page.
 * - `?cockpit=` is dropped wherever it shows.
 */
export function cockpitTarget(
  factoryId: string,
  rest: string | undefined,
  params: URLSearchParams,
  prs: readonly PrView[] | undefined,
): CockpitTarget {
  const location = factoryLocation(rest);
  const review = params.get("review");
  const prParam = location.page === "pr" ? null : params.get("pr");
  if (review || prParam || location.page === "pr") {
    const id = review ?? prParam ?? location.id ?? "";
    if (!prs) return "wait";
    return { to: carry(prNode(factoryId, prs, id), params, ["review", "pr", "preview"]) };
  }
  const project = params.get("project");
  if (project) return { to: carry(nodeHref(factoryId, project), params, ["project"]) };
  const room = params.get("room");
  if (room != null && location.page !== "room") {
    return { to: carry(chatsHref(factoryId, room || null), params) };
  }
  if (params.get("peek") === "needs") return { to: carry(decisionsHref(factoryId), params) };
  switch (location.page) {
    case "project":
      // The team editor keeps its address (the Team verb opens it).
      if ((rest ?? "").split("/").filter(Boolean)[2] === "team") break;
      return { to: carry(nodeHref(factoryId, location.id ?? ""), params) };
    case "projects":
      return { to: carry(factoryPageHref(factoryId), params) };
    case "room":
      return { to: carry(chatsHref(factoryId, location.id), params) };
    case "other":
      if (location.id === "initiatives") {
        const ref = (rest ?? "").split("/").filter(Boolean)[1];
        return {
          to: carry(
            ref ? nodeHref(factoryId, decodeURIComponent(ref)) : factoryPageHref(factoryId),
            params,
          ),
        };
      }
      if (location.id === "overview") return { to: carry(factoryPageHref(factoryId), params) };
      break;
    default:
      break;
  }
  if (params.has("cockpit"))
    return { to: carry(`${factoryPageHref(factoryId)}${rest ? `/${rest}` : ""}`, params) };
  return null;
}
