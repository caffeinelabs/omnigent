// Where v3 pages read nodes from: the server's node API once it serves one
// (`GET /v1/factory/factories/{fid}/n/{ref}`, advertised by preflight
// `node_api`), else the sample factory, labelled as sample. Stories and tests
// pass their own source through `NodeSourceProvider`.

import { type ReactNode, createContext, createElement, useContext } from "react";
import { usePreflight } from "@/hooks/useFactory";
import { answerDecision, factoryErrorFromResponse } from "@/lib/factoryApi";
import { authenticatedFetch } from "@/lib/identity";
import type { DecisionAction, DecisionRef, LevelView, NodePage } from "./nodeTypes";
import { sampleNodeSource } from "./fixture/sampleNodeSource";

export interface NodeSource {
  /** `live` reads the server; `sample` is the built-in sample factory. */
  kind: "live" | "sample";
  /** Says the data is not this factory's ("Sample data"). */
  label?: string;
  /** The node page; null when no node has that ref. */
  node: (factoryId: string, ref: string, signal?: AbortSignal) => Promise<NodePage | null>;
  /** One level under a node, grouped under parents; null when the level is not below it. */
  level: (
    factoryId: string,
    ref: string,
    level: string,
    signal?: AbortSignal,
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

/** The server's node API. */
export const liveNodeSource: NodeSource = {
  kind: "live",
  node: async (fid, ref, signal) =>
    readOrNull<NodePage>(await authenticatedFetch(nodePath(fid, ref), { signal })),
  level: async (fid, ref, level, signal) =>
    readOrNull<LevelView>(
      await authenticatedFetch(`${nodePath(fid, ref)}/level/${enc(level)}`, { signal }),
    ),
  answer: async (_fid, decision, action) => {
    await answerDecision(decision.id, { option_id: action.id });
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

let builtIn: NodeSource | null = null;

/**
 * The source this page reads: a provider's, else the server's node API when
 * preflight says it serves one, else the sample factory. Null while preflight loads.
 */
export function useNodeSource(): NodeSource | null {
  const given = useContext(NodeSourceContext);
  const preflight = usePreflight({ enabled: given == null });
  if (given) return given;
  if (preflight.isPending) return null;
  if (preflight.data?.node_api === true) return liveNodeSource;
  builtIn ??= sampleNodeSource();
  return builtIn;
}
