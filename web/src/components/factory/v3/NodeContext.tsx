// What every part of a node page reads: the factory, the decision the header
// asks (so children say "the ask above" instead of repeating it), and the
// page's actions (answer, open an ask, use a verb, prefetch a node).

import { type ReactNode, createContext, useContext, useMemo } from "react";
import type { DecisionAction, DecisionRef, VerbAvail } from "./nodeTypes";

export interface NodeActions {
  factoryId: string;
  /** The decision the page's header asks, if any. */
  aboveId?: string | null;
  /** Read a node's page ahead (hover or focus on its link). */
  prefetch?: (ref: string) => void;
  /** A click on an answer (never Enter). */
  onAnswer?: (decision: DecisionRef, action: DecisionAction) => void;
  /** "Open C-16": the ask with its full text. */
  onOpenDecision?: (decision: DecisionRef) => void;
  /** A verb button. */
  onVerb?: (verb: VerbAvail) => void;
}

const NodeContext = createContext<NodeActions>({ factoryId: "" });

export function NodeContextProvider({
  value,
  children,
}: {
  value: NodeActions;
  children: ReactNode;
}) {
  return <NodeContext.Provider value={value}>{children}</NodeContext.Provider>;
}

/** The same actions with the header's decision set. */
export function AboveProvider({
  aboveId,
  children,
}: {
  aboveId: string | null;
  children: ReactNode;
}) {
  const outer = useContext(NodeContext);
  const value = useMemo(() => ({ ...outer, aboveId }), [outer, aboveId]);
  return <NodeContext.Provider value={value}>{children}</NodeContext.Provider>;
}

export function useNodeActions(): NodeActions {
  return useContext(NodeContext);
}
