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
