/**
 * What the Room's cards read besides their message: the factory, its
 * snapshot, the proposals, loops and issues lists (each read once per
 * thread view), and whether they render compact (the dock).
 */

import { createContext, useContext } from "react";
import type {
  FactorySnapshot,
  IssueView,
  LoopView,
  PersonView,
  ProposalView,
} from "@/lib/factoryApi";

export interface RoomCardData {
  factoryId: string;
  snapshot: FactorySnapshot;
  clockOffsetS: number;
  proposals: readonly ProposalView[] | null;
  /** Null: loading, or a server without the loop engine. */
  loops: readonly LoopView[] | null;
  issues: readonly IssueView[] | null;
  people: readonly PersonView[] | null;
  /** The dock's narrow column: cards fold their long parts. */
  compact: boolean;
}

export const RoomCardContext = createContext<RoomCardData | null>(null);

/** The cards' data; throws outside a thread view. */
export function useRoomCardData(): RoomCardData {
  const value = useContext(RoomCardContext);
  if (!value) throw new Error("useRoomCardData must be used inside a Room thread");
  return value;
}
