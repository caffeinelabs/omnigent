// The factory cockpit's presence heartbeat. While a factory page is open it
// tells the server where the viewer is (`overview`, `project:<slug>`,
// `pr:<id>`, …) every 20 s (the server forgets them 60 s after the last
// beat), at once when they move, and whether the tab is idle (hidden). It
// leaves on unmount. Presence is best effort: a failed beat is retried on the
// next tick, and a 403/404 (no access, no such factory) stops the beats.

import { useEffect, useRef } from "react";
import { postFactoryPresence } from "@/lib/factoryApi";
import { ApiError } from "@/lib/sessionsApi";

/** How often the cockpit beats (the server's `heartbeat_s`). */
export const PRESENCE_HEARTBEAT_MS = 20_000;

function hidden(): boolean {
  return typeof document !== "undefined" && document.visibilityState === "hidden";
}

/**
 * Beat presence for `factoryId` at `location` while mounted.
 *
 * @param factoryId - The factory; null or empty sends nothing.
 * @param location - Where the viewer is; a change beats at once.
 */
export function useFactoryPresence(
  factoryId: string | null | undefined,
  location: string | null,
  options: { enabled?: boolean } = {},
): void {
  const enabled = (options.enabled ?? true) && Boolean(factoryId);
  const locationRef = useRef(location);
  locationRef.current = location;
  const stopped = useRef(false);
  const beatRef = useRef<(() => void) | null>(null);

  // One heartbeat loop per factory; it leaves when the page unmounts.
  useEffect(() => {
    if (!enabled || !factoryId) return;
    stopped.current = false;
    const beat = () => {
      if (stopped.current) return;
      postFactoryPresence(factoryId, { location: locationRef.current, idle: hidden() }).catch(
        (error: unknown) => {
          if (error instanceof ApiError && (error.status === 403 || error.status === 404)) {
            stopped.current = true;
          }
        },
      );
    };
    beatRef.current = beat;
    beat();
    const timer = window.setInterval(beat, PRESENCE_HEARTBEAT_MS);
    document.addEventListener("visibilitychange", beat);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", beat);
      beatRef.current = null;
      if (!stopped.current) {
        void postFactoryPresence(factoryId, { location: null, idle: false, leave: true }).catch(
          () => undefined,
        );
      }
    };
  }, [enabled, factoryId]);

  // A move beats at once (the loop above keeps beating from the new place).
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    beatRef.current?.();
  }, [location]);
}
