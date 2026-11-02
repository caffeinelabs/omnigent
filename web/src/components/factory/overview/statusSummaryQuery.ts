// The Now card's read. Its own key (`factoryNowQueryKey`) is made stale by a
// `factory_changed` frame that changes more than who is running (the light
// "now" and "presence" ticks, every few seconds, would re-read the incidents,
// the ledger and the pause for nothing); it also refreshes at least once a
// minute, and every 15 s while the push socket is down.

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useSessionUpdatesConnected } from "@/hooks/useSessionUpdatesConnected";
import {
  COCKPIT_MIN_REFRESH_MS,
  FACTORY_FALLBACK_POLL_MS,
  factoryNowQueryKey,
} from "@/hooks/useFactory";
import { type StatusSummary, fetchFactoryStatusSummary } from "@/lib/factoryStatusApi";
import { ApiError } from "@/lib/sessionsApi";

export const statusSummaryKey = factoryNowQueryKey;

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
