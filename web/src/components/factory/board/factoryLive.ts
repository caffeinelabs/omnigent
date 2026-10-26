import { createContext, useContext } from "react";
import { useSessionUpdatesConnected } from "@/hooks/useSessionUpdatesConnected";

/** Pins the cockpit's live/reconnecting state (stories); `null` follows the socket. */
export const FactoryLiveContext = createContext<boolean | null>(null);

/** Whether factory updates are pushed live right now. */
export function useFactoryLive(): boolean {
  const pinned = useContext(FactoryLiveContext);
  const connected = useSessionUpdatesConnected();
  return pinned ?? connected;
}
