// The Agent page's live tail: it holds the session's SSE stream open (which
// also shows the viewer as watching it) and, on any frame, refreshes the
// agent's page data at most every 3 s. The server's `now` poller stays
// the source of what the agent runs; the stream only makes it prompt.

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { openSessionStream } from "@/lib/sessionsApi";
import { parseSseStream } from "@/lib/sse";
import { agentDetailKey } from "./opsQueries";

export type StreamState = "connecting" | "live" | "closed";

/** Refreshes after a frame wait this long, so a burst of tokens costs one read. */
export const STREAM_REFRESH_MS = 3000;
const RETRY_MS = 5000;

export function useAgentStream(
  factoryId: string,
  sessionId: string,
  options: { enabled?: boolean } = {},
): StreamState {
  const queryClient = useQueryClient();
  const [state, setState] = useState<StreamState>("connecting");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const enabled = options.enabled ?? true;
  useEffect(() => {
    if (!enabled || !sessionId) return;
    const controller = new AbortController();
    let retry: ReturnType<typeof setTimeout> | null = null;
    const refresh = () => {
      if (timer.current) return;
      timer.current = setTimeout(() => {
        timer.current = null;
        void queryClient.invalidateQueries({ queryKey: agentDetailKey(factoryId, sessionId) });
      }, STREAM_REFRESH_MS);
    };
    const connect = async () => {
      setState("connecting");
      try {
        const res = await openSessionStream(sessionId, controller.signal);
        if (!res.ok || !res.body) throw new Error(`stream ${res.status}`);
        setState("live");
        for await (const _ of parseSseStream(res.body)) refresh();
      } catch {
        // Aborted on unmount, or the stream failed: retry below unless aborted.
      }
      if (controller.signal.aborted) return;
      setState("closed");
      retry = setTimeout(() => void connect(), RETRY_MS);
    };
    void connect();
    return () => {
      controller.abort();
      if (retry) clearTimeout(retry);
      if (timer.current) clearTimeout(timer.current);
      timer.current = null;
    };
  }, [factoryId, sessionId, enabled, queryClient]);
  return state;
}
