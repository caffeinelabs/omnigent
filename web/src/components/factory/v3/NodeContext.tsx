// What every part of a node page reads: the factory, the decision the header
// asks (so children say "the ask above" instead of repeating it), and the
// page's actions (answer, open an ask, use a verb, prefetch a node).

import { type ReactNode, createContext, useContext, useMemo } from "react";
import type { DecisionAction, DecisionRef, VerbArgs, VerbAvail } from "./nodeTypes";
import type { Above } from "./statusModel";

/** A sheet a page part can open (`?peek=`): Needs you, the base env, Try it, the keys, a lineage. */
export type SheetKind = "needs" | "env" | "try" | "keys" | "lineage";

export interface NodeActions {
  factoryId: string;
  /** The asks the page's Your move shows: nothing else on the page repeats them. */
  aboveId?: Above;
  /** Read a node's page ahead (hover or focus on its link). */
  prefetch?: (ref: string) => void;
  /** A click on an answer (never Enter). */
  onAnswer?: (decision: DecisionRef, action: DecisionAction) => void;
  /** "Open C-16": the ask with its full text. */
  onOpenDecision?: (decision: DecisionRef) => void;
  /** A verb button, a quick verb or words in the chat (with what they named). */
  onVerb?: (verb: VerbAvail, args?: VerbArgs) => void;
  /** Open a sheet; `at` names the node (Try it). */
  onSheet?: (sheet: SheetKind, at?: string) => void;
  /** The verb whose preview is open on this page (its button reads as pressed). */
  openVerb?: string | null;
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
export function AboveProvider({ aboveId, children }: { aboveId: Above; children: ReactNode }) {
  const outer = useContext(NodeContext);
  const value = useMemo(() => ({ ...outer, aboveId }), [outer, aboveId]);
  return <NodeContext.Provider value={value}>{children}</NodeContext.Provider>;
}

export function useNodeActions(): NodeActions {
  return useContext(NodeContext);
}
