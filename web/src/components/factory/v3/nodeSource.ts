// Where v3 pages read nodes from: the server's node API once it serves one
// (`GET /v1/factory/factories/{fid}/n/{ref}`, advertised by preflight
// `node_api`), else the sample factory, labelled as sample and loaded only
// then. Stories and tests pass their own source through `NodeSourceProvider`.

import {
  type ReactNode,
  createContext,
  createElement,
  useContext,
  useEffect,
  useState,
} from "react";
import { usePreflight } from "@/hooks/useFactory";
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

// The sample (its data and engine) is its own chunk, read only when a v3 page
// has no node API to read; one copy per tab, so answers carry across pages.
let sample: NodeSource | null = null;
let sampleLoad: Promise<NodeSource> | null = null;

function loadSample(): Promise<NodeSource> {
  sampleLoad ??= import("./fixture/sampleNodeSource").then((m) => {
    sample = m.sampleNodeSource();
    return sample;
  });
  return sampleLoad;
}

/**
 * The source this page reads: a provider's, else the server's node API when
 * preflight says it serves one, else the sample factory. Null while preflight
 * or the sample loads.
 */
export function useNodeSource(): NodeSource | null {
  const given = useContext(NodeSourceContext);
  const preflight = usePreflight({ enabled: given == null });
  const wantSample = !given && !preflight.isPending && preflight.data?.node_api !== true;
  const [loaded, setLoaded] = useState(sample);
  useEffect(() => {
    if (!wantSample || loaded) return;
    let live = true;
    void loadSample().then((s) => {
      if (live) setLoaded(s);
    });
    return () => {
      live = false;
    };
  }, [wantSample, loaded]);
  if (given) return given;
  if (preflight.isPending) return null;
  if (preflight.data?.node_api === true) return liveNodeSource;
  return loaded ?? sample;
}
