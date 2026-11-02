// TanStack Query bindings for Teams and the agent panel: one agent's strip
// over a window, and the bench. Both are slow-moving reads (the strip changes
// at turn edges, the bench is cached on the server for five minutes).

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  type AgentTimelineResponse,
  type BenchResponse,
  type TimelineWindow,
  fetchAgentTimeline,
  fetchFactoryBench,
} from "@/lib/factoryTeamsApi";
import { ApiError } from "@/lib/sessionsApi";

export const TIMELINE_POLL_MS = 60_000;
export const BENCH_STALE_MS = 5 * 60_000;

function retryTransient(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < 1;
}

export const agentTimelineKey = (factoryId: string, agent: string, window: TimelineWindow) =>
  ["factory-agent", factoryId, agent, "timeline", window] as const;

export const benchKey = (factoryId: string, window: string) =>
  ["factory-bench", factoryId, window] as const;

/** One agent's state strip, totals and sparklines (`agent`: a session id or handle). */
export function useAgentTimeline(
  factoryId: string,
  agent: string | null,
  window: TimelineWindow,
  options: { enabled?: boolean } = {},
) {
  return useQuery<AgentTimelineResponse>({
    queryKey: agentTimelineKey(factoryId, agent ?? "", window),
    queryFn: ({ signal }) => fetchAgentTimeline(factoryId, agent as string, window, signal),
    enabled: Boolean(factoryId && agent) && (options.enabled ?? true),
    placeholderData: keepPreviousData,
    refetchInterval: TIMELINE_POLL_MS,
    retry: retryTransient,
  });
}

/** The bench over `window` (30 days by default). */
export function useFactoryBench(
  factoryId: string,
  window = "30d",
  options: { enabled?: boolean } = {},
) {
  return useQuery<BenchResponse>({
    queryKey: benchKey(factoryId, window),
    queryFn: ({ signal }) => fetchFactoryBench(factoryId, window, signal),
    enabled: Boolean(factoryId) && (options.enabled ?? true),
    staleTime: BENCH_STALE_MS,
    retry: retryTransient,
  });
}
