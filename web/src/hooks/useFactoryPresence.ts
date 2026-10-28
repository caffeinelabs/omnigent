// The factory cockpit's presence heartbeat. While a factory page is open it
// tells the server where the viewer is (`overview`, `project:<slug>`,
// `pr:<id>`, …) every 20 s (the server forgets them 60 s after the last
// beat), at once when they move, and whether the tab is idle (hidden). It
// leaves on unmount. Presence is best effort: a failed beat is retried on the
// next tick, and a 403/404 (no access, no such factory) stops the beats.
//
// The server keeps one entry per person, so tabs of the same factory agree
// over a BroadcastChannel: a hidden tab leaves the location to a visible one
// (no flip-flopping between pages), and closing a tab while another is open
// sends no leave.

import { useEffect, useRef } from "react";
import { postFactoryPresence } from "@/lib/factoryApi";
import { ApiError } from "@/lib/sessionsApi";

/** How often the cockpit beats (the server's `heartbeat_s`). */
export const PRESENCE_HEARTBEAT_MS = 20_000;

/** Another tab counts as open this long after its last note. */
const PEER_FRESH_MS = 2 * PRESENCE_HEARTBEAT_MS;

function hidden(): boolean {
  return typeof document !== "undefined" && document.visibilityState === "hidden";
}

interface PeerNote {
  tab: string;
  visible: boolean;
  gone?: boolean;
}

/** This tab's view of the viewer's other tabs on the same factory (null without BroadcastChannel). */
function openPeers(factoryId: string, onPeerGone: () => void) {
  if (typeof BroadcastChannel === "undefined") return null;
  const channel = new BroadcastChannel(`omnigent-factory-presence:${factoryId}`);
  const tab = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  const seen = new Map<string, { visible: boolean; at: number }>();
  channel.onmessage = (event: MessageEvent<PeerNote>) => {
    const note = event.data;
    if (!note || note.tab === tab) return;
    if (note.gone) {
      seen.delete(note.tab);
      onPeerGone();
    } else {
      seen.set(note.tab, { visible: note.visible, at: Date.now() });
    }
  };
  const fresh = () => [...seen.values()].filter((p) => Date.now() - p.at < PEER_FRESH_MS);
  return {
    announce: (visible: boolean) => channel.postMessage({ tab, visible } satisfies PeerNote),
    others: () => fresh().length > 0,
    visibleOther: () => fresh().some((p) => p.visible),
    close: () => {
      channel.postMessage({ tab, visible: false, gone: true } satisfies PeerNote);
      channel.close();
    },
  };
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
    let peers: ReturnType<typeof openPeers> = null;
    const beat = () => {
      if (stopped.current) return;
      const idle = hidden();
      peers?.announce(!idle);
      // A visible tab says where the viewer is; this hidden one keeps quiet.
      if (idle && peers?.visibleOther()) return;
      postFactoryPresence(factoryId, { location: locationRef.current, idle }).catch(
        (error: unknown) => {
          if (error instanceof ApiError && (error.status === 403 || error.status === 404)) {
            stopped.current = true;
          }
        },
      );
    };
    // A closed sibling tab may have been the one the server showed: say where this one is.
    peers = openPeers(factoryId, () => beat());
    beatRef.current = beat;
    beat();
    const timer = window.setInterval(beat, PRESENCE_HEARTBEAT_MS);
    document.addEventListener("visibilitychange", beat);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", beat);
      beatRef.current = null;
      const othersOpen = peers?.others() ?? false;
      peers?.close();
      if (!stopped.current && !othersOpen) {
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
