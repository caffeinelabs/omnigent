// Discovery's reads. The summary, the ranked opportunities and one opportunity
// sit under the Now key, so a factory change refreshes them with Home's. The
// costlier reads (the signal queues, readouts, a source's access log, the open
// proposals and the bets) sit under the discovery key, which only discovery,
// proposal and definition changes refresh. Every route answers `404
// discovery_off` while discovery is off; the readouts route is newer than the
// rest, so a 404 there means "no readouts yet" and the page goes on without.

import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo } from "react";
import { factoryDiscoveryQueryKey, factoryNowQueryKey } from "@/hooks/useFactory";
import type { ProposalView } from "@/lib/factoryApi";
import { fetchDiscovery } from "@/lib/factoryDiscoveryApi";
import { fetchProposals } from "@/lib/factoryRoomApi";
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

/** Reads a factory change refreshes only when it touches discovery, proposals or the definition. */
export const discoveryDeepKey = (factoryId: string, ...rest: unknown[]) =>
  [...factoryDiscoveryQueryKey(factoryId), ...rest] as const;

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
    queryKey: discoveryDeepKey(factoryId, "signals", view),
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
    queryKey: discoveryDeepKey(factoryId, "readouts"),
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
    queryKey: discoveryDeepKey(factoryId, "source-log", id),
    queryFn: ({ signal }) =>
      fetchDiscovery(factoryId, `/sources/${encodeURIComponent(id!)}/log?limit=20`, signal),
    enabled: Boolean(factoryId && id) && enabled,
    staleTime: 30_000,
    retry: retryTransient,
  });
}

/** Statuses of a proposal that still waits for a person or the other lead. */
const OPEN = ["pending", "awaiting_cosign"] as const;
/** The server's page size for proposals. */
const PROPOSALS_MAX = 200;

/**
 * The proposals Discovery draws: every open one (pending sources and bets
 * waiting for a person), and the applied or kept bets for Flow. Asked for by
 * status, so a factory with many other proposals never pushes them out.
 */
export function useDiscoveryProposals(factoryId: string, enabled: boolean) {
  const open = useQuery<ProposalView[]>({
    queryKey: discoveryDeepKey(factoryId, "proposals", "open"),
    queryFn: async ({ signal }) =>
      (await fetchProposals(factoryId, { status: [...OPEN], limit: PROPOSALS_MAX }, signal)).data,
    enabled: Boolean(factoryId) && enabled,
    ...READ,
  });
  const bets = useQuery<ProposalView[]>({
    queryKey: discoveryDeepKey(factoryId, "proposals", "bets"),
    queryFn: async ({ signal }) =>
      (
        await fetchProposals(
          factoryId,
          { kind: "projects", status: ["applied", "kept"], limit: PROPOSALS_MAX },
          signal,
        )
      ).data.filter((p) => p.bet != null),
    enabled: Boolean(factoryId) && enabled,
    ...READ,
  });
  const openData = open.data;
  const betData = bets.data;
  return useMemo(
    () => ({
      /** Open proposals: pending sources and bets that wait. */
      open: openData ?? [],
      /** Open proposals, then the applied or kept bets. */
      all: [...(openData ?? []), ...(betData ?? [])],
    }),
    [openData, betData],
  );
}

/** Refresh everything a discovery lever may have changed (Home's loop and asks too). */
export function useRefreshDiscovery(factoryId: string): () => void {
  const queryClient = useQueryClient();
  return useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: factoryNowQueryKey(factoryId) });
    void queryClient.invalidateQueries({ queryKey: factoryDiscoveryQueryKey(factoryId) });
    void queryClient.invalidateQueries({ queryKey: learnKey(factoryId, "definition") });
  }, [queryClient, factoryId]);
}
