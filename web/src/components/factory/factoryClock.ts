import { createContext, useContext } from "react";
import { useNow } from "@/hooks/useNow";

/** A pinned "now" for factory views; stories set it so captures compare run to run. */
export const FactoryNowContext = createContext<Date | null>(null);

/** The clock factory views measure elapsed and relative times against. */
export function useFactoryNow(): Date {
  const pinned = useContext(FactoryNowContext);
  const live = useNow();
  return pinned ?? live;
}

/**
 * "Now" in epoch seconds on the server's clock: the browser clock shifted by
 * `offsetS` (server minus browser), so a skewed laptop clock doesn't age the
 * server's stamps. A pinned clock is used as is.
 */
export function useFactoryServerNowS(offsetS: number): number {
  const pinned = useContext(FactoryNowContext);
  const now = useFactoryNow();
  return Math.floor(now.getTime() / 1000 + (pinned ? 0 : offsetS));
}
