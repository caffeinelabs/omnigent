// Where v3 pages read nodes from: the server's node API
// (`GET /v1/factory/factories/{fid}/n/{ref}` and `…/n/{ref}/level/{level}`).
// Stories and tests pass their own source (the sample factory) through
// `NodeSourceProvider`; the sample never ships in the live path.

import { type ReactNode, createContext, createElement, useContext } from "react";
import { answerDecision, factoryErrorFromResponse } from "@/lib/factoryApi";
import { authenticatedFetch } from "@/lib/identity";
import type { DecisionAction, DecisionRef, LevelView, NodePage } from "./nodeTypes";
import { normalizeLevelView, normalizeNodePage } from "./nodeWire";

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

/** The server's node API. */
export const liveNodeSource: NodeSource = {
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
