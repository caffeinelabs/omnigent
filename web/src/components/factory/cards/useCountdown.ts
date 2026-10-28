// A second-by-second countdown on the server's clock (an Undo window). It
// ticks only while time is left; a pinned clock (stories) stays still.

import { useContext, useEffect, useState } from "react";
import { FactoryNowContext } from "../factoryClock";

function secondsLeft(untilS: number | null | undefined, nowS: number): number | null {
  if (untilS == null) return null;
  const left = untilS - nowS;
  return left > 0 ? left : null;
}

/**
 * Seconds left until `untilS` (server epoch seconds), or null when there is
 * no deadline or it has passed.
 *
 * @param offsetS - Seconds the server clock runs ahead of the browser's.
 */
export function useCountdown(untilS: number | null | undefined, offsetS = 0): number | null {
  const pinned = useContext(FactoryNowContext);
  const pinnedS = pinned ? pinned.getTime() / 1000 : null;
  const [liveS, setLiveS] = useState(() => Date.now() / 1000 + offsetS);
  useEffect(() => {
    if (pinnedS != null || untilS == null) return;
    setLiveS(Date.now() / 1000 + offsetS);
    const timer = window.setInterval(() => {
      const now = Date.now() / 1000 + offsetS;
      setLiveS(now);
      if (now >= untilS) window.clearInterval(timer);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [untilS, offsetS, pinnedS]);
  return secondsLeft(untilS, pinnedS ?? liveS);
}
