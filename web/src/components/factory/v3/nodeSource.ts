// Where v3 pages read nodes from: the server's node API
// (`GET /v1/factory/factories/{fid}/n/{ref}` and `…/n/{ref}/level/{level}`).
// Stories and tests pass their own source (the sample factory) through
// `NodeSourceProvider`; the sample never ships in the live path.
//
// Steering (verb previews with apply and Undo, Needs you, Find, last-look
// marks) is optional on a source: a panel that needs a route the source lacks
// falls back (a toast, the older Needs you list, the snapshot's search). The
// live source gains each route once its endpoint is on the server
// (`SERVED_STEER`).

import { type ReactNode, createContext, createElement, useContext } from "react";
import { answerDecision, factoryErrorFromResponse } from "@/lib/factoryApi";
import { authenticatedFetch } from "@/lib/identity";
import type {
  DecisionAction,
  DecisionRef,
  FindHit,
  LevelView,
  NeedsYouList,
  NodePage,
  TeamSheet,
  VerbApplied,
  VerbPreview,
  VerbRequest,
} from "./nodeTypes";
import {
  normalizeFind,
  normalizeLevelView,
  normalizeNeedsYou,
  normalizeNodePage,
  normalizePreview,
} from "./nodeWire";

export interface NodeSource {
  /** `live` reads the server; `sample` is the built-in sample factory. */
  kind: "live" | "sample";
  /** Says the data is not this factory's ("Sample data"). */
  label?: string;
  /** The node page (`cursor`: its children from `children.next`); null when no node has that ref. */
  node: (
    factoryId: string,
    ref: string,
    signal?: AbortSignal,
    cursor?: string,
  ) => Promise<NodePage | null>;
  /** One level under a node, grouped under parents (`cursor`: a group's `next`); null when the level is not below it. */
  level: (
    factoryId: string,
    ref: string,
    level: string,
    signal?: AbortSignal,
    cursor?: string,
  ) => Promise<LevelView | null>;
  /** Answer a decision with one of its actions. */
  answer: (factoryId: string, decision: DecisionRef, action: DecisionAction) => Promise<void>;
  /** A verb's dry run: nothing changes (`POST …/n/{ref}/preview`). */
  preview?: (
    factoryId: string,
    ref: string,
    request: VerbRequest,
    signal?: AbortSignal,
  ) => Promise<VerbPreview>;
  /** Apply what a preview showed; returns the Undo (`POST …/n/{ref}/apply`). */
  apply?: (
    factoryId: string,
    ref: string,
    request: VerbRequest & { preview_id: string },
  ) => Promise<VerbApplied>;
  /** Undo an applied verb within its window (`POST …/undo/{token}`). */
  undo?: (factoryId: string, token: string) => Promise<void>;
  /** Every open ask of a tree, ranked by what it holds (`GET …/needs-you?tree=`). */
  needsYou?: (factoryId: string, tree: string, signal?: AbortSignal) => Promise<NeedsYouList>;
  /** Nodes by ref or title, with breadcrumbs (`GET …/find?q=`). */
  find?: (factoryId: string, q: string, signal?: AbortSignal) => Promise<FindHit[]>;
  /** This person looked at this node now (`PUT …/n/{ref}/seen`). */
  seen?: (factoryId: string, ref: string) => Promise<void>;
  /**
   * A node's team for the Team panel (the sample's; the live panel reads the
   * team editor's routes: the project's team spec, the catalog, the definition).
   */
  team?: (factoryId: string, ref: string, signal?: AbortSignal) => Promise<TeamSheet | null>;
}

const enc = encodeURIComponent;

async function readOrNull<T>(res: Response): Promise<T | null> {
  if (res.status === 404) return null;
  if (!res.ok) throw await factoryErrorFromResponse(res);
  return (await res.json()) as T;
}

function nodePath(fid: string, ref: string): string {
  return `/v1/factory/factories/${enc(fid)}/n/${enc(ref)}`;
}

function query(name: string, value: string | undefined): string {
  return value ? `?${name}=${enc(value)}` : "";
}

async function sendJson(path: string, method: string, body?: unknown): Promise<Response> {
  const res = await authenticatedFetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!res.ok) throw await factoryErrorFromResponse(res);
  return res;
}

function factoryPath(fid: string): string {
  return `/v1/factory/factories/${enc(fid)}`;
}

type SteerRoutes = Required<
  Pick<NodeSource, "preview" | "apply" | "undo" | "needsYou" | "find" | "seen">
>;

/** The steering routes, as api.md §3.8 and §4 name them. */
export const steerRoutes: SteerRoutes = {
  preview: async (fid, ref, request, signal) => {
    const res = await authenticatedFetch(`${nodePath(fid, ref)}/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
      signal,
    });
    if (!res.ok) throw await factoryErrorFromResponse(res);
    return normalizePreview((await res.json()) as VerbPreview);
  },
  apply: async (fid, ref, request) => {
    const res = await sendJson(`${nodePath(fid, ref)}/apply`, "POST", request);
    const applied = (await res.json()) as VerbApplied;
    return applied.result ? { ...applied, result: normalizeNodePage(applied.result) } : applied;
  },
  undo: async (fid, token) => {
    await sendJson(`${factoryPath(fid)}/undo/${enc(token)}`, "POST", {});
  },
  needsYou: async (fid, tree, signal) => {
    const res = await authenticatedFetch(`${factoryPath(fid)}/needs-you${query("tree", tree)}`, {
      signal,
    });
    if (!res.ok) throw await factoryErrorFromResponse(res);
    return normalizeNeedsYou((await res.json()) as NeedsYouList);
  },
  find: async (fid, q, signal) => {
    const res = await authenticatedFetch(`${factoryPath(fid)}/find${query("q", q)}`, { signal });
    if (!res.ok) throw await factoryErrorFromResponse(res);
    return normalizeFind((await res.json()) as FindHit[]);
  },
  seen: async (fid, ref) => {
    await sendJson(`${nodePath(fid, ref)}/seen`, "PUT");
  },
};

/**
 * The steering routes this server has. Each joins the live source once its
 * endpoint is on staging; until then the panels fall back.
 *
 * TODO(S12): `preview`, `apply`, `undo` once `POST …/n/{ref}/preview|apply` and
 * `POST …/undo/{token}` land.
 * TODO(S9): `needsYou`, `find` once `GET …/needs-you` and `GET …/find` land.
 * TODO(S10): `seen` once `PUT …/n/{ref}/seen` lands.
 */
export const SERVED_STEER: readonly (keyof SteerRoutes)[] = [];

function served(): Partial<NodeSource> {
  return Object.fromEntries(
    SERVED_STEER.map((name) => [name, steerRoutes[name]]),
  ) as Partial<NodeSource>;
}

/** The server's node API. */
export const liveNodeSource: NodeSource = {
  ...served(),
  kind: "live",
  node: async (fid, ref, signal, cursor) => {
    const url = `${nodePath(fid, ref)}${query("children_page", cursor)}`;
    const page = await readOrNull<NodePage>(await authenticatedFetch(url, { signal }));
    return page && normalizeNodePage(page);
  },
  level: async (fid, ref, level, signal, cursor) => {
    const url = `${nodePath(fid, ref)}/level/${enc(level)}${query("cursor", cursor)}`;
    const view = await readOrNull<LevelView>(await authenticatedFetch(url, { signal }));
    return view && normalizeLevelView(view);
  },
  answer: async (_fid, decision, action) => {
    // The answer route takes the decision's row id; `id` is its ref ("D-45").
    await answerDecision(decision.row_id ?? decision.id, { option_id: action.id });
  },
};

const NodeSourceContext = createContext<NodeSource | null>(null);

export function NodeSourceProvider({
  source,
  children,
}: {
  source: NodeSource;
  children: ReactNode;
}) {
  return createElement(NodeSourceContext.Provider, { value: source }, children);
}

/**
 * The source this page reads: a provider's (stories and tests), else the
 * server's node API. Null when `enabled` is false (the shell of another
 * cockpit: nothing is read).
 */
export function useNodeSource(enabled = true): NodeSource | null {
  const given = useContext(NodeSourceContext);
  if (!enabled) return null;
  return given ?? liveNodeSource;
}
