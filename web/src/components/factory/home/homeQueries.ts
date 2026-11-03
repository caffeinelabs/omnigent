// Home's reads: the one Needs you list (`GET …/asks`, refreshed with the Now
// card: its key sits under `factoryNowQueryKey`, so every factory change that
// refreshes Now refreshes it too), and the discovery summary and top
// opportunities, which a server without the discovery ledger (or with
// discovery off) answers 404: Home then shows the loop without them.

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  COCKPIT_MIN_REFRESH_MS,
  FACTORY_FALLBACK_POLL_MS,
  factoryNowQueryKey,
} from "@/hooks/useFactory";
import { useSessionUpdatesConnected } from "@/hooks/useSessionUpdatesConnected";
import { type AsksResponse, fetchFactoryAsks } from "@/lib/factoryAsksApi";
import { factoryErrorFromResponse } from "@/lib/factoryApi";
import { authenticatedFetch } from "@/lib/identity";
import { ApiError } from "@/lib/sessionsApi";
import {
  type DiscoveryHomeFacts,
  type OpportunityRow,
  parseDiscoveryFacts,
  parseOpportunities,
} from "./homeModel";

export const factoryAsksKey = (factoryId: string) =>
  [...factoryNowQueryKey(factoryId), "asks"] as const;

function retryTransient(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < 1;
}

/** Whether a read failed only because this server has no such route (an older server). */
export function isMissing(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

/** The viewer's Needs you, one row per subject. */
export function useFactoryAsks(factoryId: string) {
  const connected = useSessionUpdatesConnected();
  return useQuery<AsksResponse>({
    queryKey: factoryAsksKey(factoryId),
    queryFn: ({ signal }) => fetchFactoryAsks(factoryId, signal),
    enabled: Boolean(factoryId),
    placeholderData: keepPreviousData,
    refetchInterval: connected ? COCKPIT_MIN_REFRESH_MS : FACTORY_FALLBACK_POLL_MS,
    refetchOnWindowFocus: true,
    retry: retryTransient,
  });
}

/**
 * The Needs you list as Home last read it, from the cache only: it never
 * fetches (the shell's count uses it while Home holds answers).
 */
export function useCachedFactoryAsks(factoryId: string): AsksResponse | undefined {
  return useQuery<AsksResponse>({
    queryKey: factoryAsksKey(factoryId),
    queryFn: ({ signal }) => fetchFactoryAsks(factoryId, signal),
    enabled: false,
  }).data;
}

async function readOptional(path: string, signal?: AbortSignal): Promise<unknown> {
  const resp = await authenticatedFetch(path, { signal });
  if (resp.status === 404) return null;
  if (!resp.ok) throw await factoryErrorFromResponse(resp);
  return resp.json();
}

const base = (factoryId: string) => `/v1/factory/factories/${encodeURIComponent(factoryId)}`;

/** `GET …/discovery`: the loop's counts and what was learned; null without the ledger. */
export function useDiscoveryHome(factoryId: string, enabled: boolean) {
  return useQuery<DiscoveryHomeFacts | null>({
    queryKey: [...factoryNowQueryKey(factoryId), "discovery"],
    queryFn: async ({ signal }) =>
      parseDiscoveryFacts(await readOptional(`${base(factoryId)}/discovery`, signal)),
    enabled: Boolean(factoryId) && enabled,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    retry: retryTransient,
  });
}

/** `GET …/opportunities`: the ranked list (Home shows the top of it); null without the ledger. */
export function useTopOpportunities(factoryId: string, enabled: boolean) {
  return useQuery<OpportunityRow[] | null>({
    queryKey: [...factoryNowQueryKey(factoryId), "opportunities"],
    queryFn: async ({ signal }) =>
      parseOpportunities(await readOptional(`${base(factoryId)}/opportunities`, signal)),
    enabled: Boolean(factoryId) && enabled,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    retry: retryTransient,
  });
}

/** `POST …/hunches`: a person's "I think … because …" (a hunch signal, weight 0.1). */
export async function postHunch(factoryId: string, text: string): Promise<void> {
  const resp = await authenticatedFetch(`${base(factoryId)}/hunches`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!resp.ok) throw await factoryErrorFromResponse(resp);
}
