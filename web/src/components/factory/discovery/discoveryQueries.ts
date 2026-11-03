// Discovery's reads, under the Now key so a factory change refreshes them with
// Home's: the summary, the ranked opportunities, one opportunity, the signal
// queues, a source's access log and, once the server measures bets, the
// readouts. Every route answers `404 discovery_off` while discovery is off;
// the readouts route is newer than the rest, so a 404 there means "no
// readouts yet" and the page keeps going without them.

import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { factoryNowQueryKey } from "@/hooks/useFactory";
import { fetchDiscovery } from "@/lib/factoryDiscoveryApi";
import { ApiError } from "@/lib/sessionsApi";
import { learnKey } from "../insights/learnQueries";
import {
  type DiscoverySummary,
  type OpportunityDetail,
  type OpportunityList,
  type ReadoutView,
  type SignalView,
  parseOpportunityDetail,
  parseOpportunityList,
  parseReadouts,
  parseSignals,
  parseSummary,
} from "./discoveryModel";

export const discoveryKey = (factoryId: string, ...rest: unknown[]) =>
  [...factoryNowQueryKey(factoryId), "discovery-page", ...rest] as const;

function retryTransient(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < 1;
}

/** A 404 for a route this server lacks, or for discovery being off. */
export function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

/** The server says discovery is off for this factory (`404 discovery_off`). */
export function isDiscoveryOff(error: unknown): boolean {
  return isNotFound(error) && (error as ApiError).code === "discovery_off";
}

const READ = { staleTime: 30_000, placeholderData: keepPreviousData, retry: retryTransient };

/** `GET …/discovery`: the header's facts. */
export function useDiscoverySummary(factoryId: string, enabled: boolean) {
  return useQuery<DiscoverySummary | null>({
    queryKey: discoveryKey(factoryId, "summary"),
    queryFn: async ({ signal }) =>
      parseSummary(await fetchDiscovery(factoryId, "/discovery", signal)),
    enabled: Boolean(factoryId) && enabled,
    ...READ,
  });
}

/** `GET …/opportunities`: every band, closed included (the table folds those). */
export function useOpportunityList(factoryId: string, enabled: boolean) {
  return useQuery<OpportunityList | null>({
    queryKey: discoveryKey(factoryId, "opportunities"),
    queryFn: async ({ signal }) =>
      parseOpportunityList(
        await fetchDiscovery(factoryId, "/opportunities?band=all&limit=500", signal),
      ),
    enabled: Boolean(factoryId) && enabled,
    ...READ,
  });
}

/** `GET …/opportunities/{O-n}`: one opportunity as its panel shows it. */
export function useOpportunity(factoryId: string, ref: string | null, enabled: boolean) {
  return useQuery<OpportunityDetail | null>({
    queryKey: discoveryKey(factoryId, "opportunity", ref),
    queryFn: async ({ signal }) =>
      parseOpportunityDetail(
        await fetchDiscovery(factoryId, `/opportunities/${encodeURIComponent(ref!)}`, signal),
      ),
    enabled: Boolean(factoryId && ref) && enabled,
    staleTime: 30_000,
    retry: retryTransient,
  });
}

export type SignalQueue = "unsorted" | "flagged" | "hunches" | "all";

/** `GET …/signals?view=`: the unsorted queue, flagged items, hunches. */
export function useSignals(factoryId: string, view: SignalQueue, enabled: boolean) {
  return useQuery<SignalView[] | null>({
    queryKey: discoveryKey(factoryId, "signals", view),
    queryFn: async ({ signal }) =>
      parseSignals(await fetchDiscovery(factoryId, `/signals?view=${view}&limit=100`, signal)),
    enabled: Boolean(factoryId) && enabled,
    ...READ,
  });
}

/**
 * `GET …/readouts`: every readout the server wrote. Null when the server has
 * no readouts route yet (404), so Impact shows bets as "not measured yet".
 */
export function useReadouts(factoryId: string, enabled: boolean) {
  return useQuery<ReadoutView[] | null>({
    queryKey: discoveryKey(factoryId, "readouts"),
    queryFn: async ({ signal }) => {
      try {
        return parseReadouts(await fetchDiscovery(factoryId, "/readouts", signal)) ?? [];
      } catch (error) {
        if (isNotFound(error)) return null;
        throw error;
      }
    },
    enabled: Boolean(factoryId) && enabled,
    ...READ,
  });
}

/** `GET …/sources/{id}/log`: the source's status and who read it, newest first. */
export function useSourceLog(factoryId: string, id: string | null, enabled: boolean) {
  return useQuery<unknown>({
    queryKey: discoveryKey(factoryId, "source-log", id),
    queryFn: ({ signal }) =>
      fetchDiscovery(factoryId, `/sources/${encodeURIComponent(id!)}/log?limit=20`, signal),
    enabled: Boolean(factoryId && id) && enabled,
    staleTime: 30_000,
    retry: retryTransient,
  });
}

/** Refresh everything a discovery lever may have changed (Home's loop and asks too). */
export function useRefreshDiscovery(factoryId: string): () => void {
  const queryClient = useQueryClient();
  return useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: factoryNowQueryKey(factoryId) });
    void queryClient.invalidateQueries({ queryKey: learnKey(factoryId, "definition") });
  }, [queryClient, factoryId]);
}
