// The Now card's read. It sits under the snapshot's key, so it refreshes on the
// Overview's cadence: every `factory_changed` frame, at least once a minute,
// and every 15 s while the push socket is down.

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useSessionUpdatesConnected } from "@/hooks/useSessionUpdatesConnected";
import {
  COCKPIT_MIN_REFRESH_MS,
  FACTORY_FALLBACK_POLL_MS,
  factoryQueryKey,
} from "@/hooks/useFactory";
import { type StatusSummary, fetchFactoryStatusSummary } from "@/lib/factoryStatusApi";
import { ApiError } from "@/lib/sessionsApi";

export const statusSummaryKey = (factoryId: string) =>
  [...factoryQueryKey(factoryId), "status-summary"] as const;

function retryTransient(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < 1;
}

/** The factory's "Now" for this viewer. */
export function useFactoryStatusSummary(factoryId: string) {
  const connected = useSessionUpdatesConnected();
  return useQuery<StatusSummary>({
    queryKey: statusSummaryKey(factoryId),
    queryFn: ({ signal }) => fetchFactoryStatusSummary(factoryId, signal),
    enabled: Boolean(factoryId),
    placeholderData: keepPreviousData,
    refetchInterval: connected ? COCKPIT_MIN_REFRESH_MS : FACTORY_FALLBACK_POLL_MS,
    refetchOnWindowFocus: true,
    retry: retryTransient,
  });
}
