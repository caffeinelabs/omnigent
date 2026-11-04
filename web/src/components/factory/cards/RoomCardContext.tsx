/**
 * What the Room's cards read besides their message: the factory, its
 * snapshot, the proposals, loops and issues lists (each read once per
 * thread view), whether they render compact (the dock), and the cards a
 * cockpit draws itself (cockpit v3's node cards).
 */

import { type ReactNode, createContext, useContext } from "react";
import type {
  ChatMessageView,
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
  /**
   * Cards the Room does not draw (cockpit v3's `why`, `closed_alarm`, `offer`
   * and `preview_applied`). A card it cannot draw renders `fallback` (the message's own
   * line, null under a message), so it never renders empty. Absent elsewhere.
   */
  nodeCard?:
    ((message: ChatMessageView, attached: boolean, fallback: ReactNode) => ReactNode) | null;
  /** Messages whose card a newer one of the same object repeats: their line, not the card (v3). */
  repeated?: ReadonlySet<string> | null;
  /** A card under a message whose ask the page shows elsewhere: left out (v3). */
  hidden?: ((message: ChatMessageView) => boolean) | null;
}

export const RoomCardContext = createContext<RoomCardData | null>(null);

/** The cards' data; throws outside a thread view. */
export function useRoomCardData(): RoomCardData {
  const value = useContext(RoomCardContext);
  if (!value) throw new Error("useRoomCardData must be used inside a Room thread");
  return value;
}
