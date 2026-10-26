// TanStack Query bindings for the factory cockpit.
//
// Freshness is push-driven: a `factory_changed` frame on the session-updates
// socket invalidates these keys (see SessionUpdatesProvider). While that socket
// is down the factory reads fall back to a 15 s poll, and a failed refresh
// keeps the last good data on screen with its age instead of blanking it.

import {
  type QueryClient,
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useSessionUpdatesConnected } from "@/hooks/useSessionUpdatesConnected";
import { ApiError } from "@/lib/sessionsApi";
import {
  type EventFilters,
  type FactoryListResponse,
  type FactorySnapshot,
  type OpenDecisionsResponse,
  type Preflight,
  fetchFactories,
  fetchFactoryEvents,
  fetchFactoryPreflight,
  fetchFactorySnapshot,
  fetchOpenDecisionCount,
  fetchOpenDecisions,
} from "@/lib/factoryApi";

/** Poll cadence while the session-updates socket is disconnected. */
export const FACTORY_FALLBACK_POLL_MS = 15_000;

export const factoryQueryKey = (factoryId: string) => ["factory", factoryId] as const;
export const factoryEventsQueryKey = (factoryId: string, filters: EventFilters = {}) =>
  ["factory-events", factoryId, filters] as const;
export const FACTORIES_QUERY_KEY = ["factories"] as const;
export const FACTORY_DECISIONS_QUERY_KEY = ["factory-decisions"] as const;
export const FACTORY_PREFLIGHT_QUERY_KEY = ["factory-preflight"] as const;

/** Every query a `factory_changed` frame for `factoryId` makes stale. */
export function factoryInvalidationKeys(factoryId: string): readonly (readonly unknown[])[] {
  return [
    factoryQueryKey(factoryId),
    ["factory-events", factoryId],
    FACTORIES_QUERY_KEY,
    FACTORY_DECISIONS_QUERY_KEY,
  ];
}

// One quick retry for a blip; a 4xx (no access, no such factory, routes not
// deployed) won't heal, and the fallback poll or next frame retries anyway.
function retryTransient(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < 1;
}

/** `refetchInterval` for factory reads: poll only while push is unavailable. */
function useFallbackInterval(): number | false {
  return useSessionUpdatesConnected() ? false : FACTORY_FALLBACK_POLL_MS;
}

/** The factories the viewer can see (index cards, sidebar badge). */
export function useFactories(options: { enabled?: boolean } = {}) {
  const refetchInterval = useFallbackInterval();
  return useQuery<FactoryListResponse>({
    queryKey: FACTORIES_QUERY_KEY,
    queryFn: fetchFactories,
    enabled: options.enabled ?? true,
    refetchInterval,
    retry: retryTransient,
  });
}

// Last ETag per factory, per QueryClient so separate clients (tests, embeds)
// never send each other's validators.
const snapshotEtags = new WeakMap<QueryClient, Map<string, string>>();

function etagsFor(queryClient: QueryClient): Map<string, string> {
  let etags = snapshotEtags.get(queryClient);
  if (!etags) {
    etags = new Map();
    snapshotEtags.set(queryClient, etags);
  }
  return etags;
}

async function readSnapshot(
  queryClient: QueryClient,
  factoryId: string,
  signal: AbortSignal,
): Promise<FactorySnapshot> {
  const etags = etagsFor(queryClient);
  const cached = queryClient.getQueryData<FactorySnapshot>(factoryQueryKey(factoryId));
  const etag = cached ? etags.get(factoryId) : undefined;
  const result = await fetchFactorySnapshot(factoryId, { etag, signal });
  if (result.notModified) {
    if (cached) return cached;
    // A 304 without a cached body (evicted meanwhile): ask again unconditionally.
    etags.delete(factoryId);
    return readSnapshot(queryClient, factoryId, signal);
  }
  if (result.etag) etags.set(factoryId, result.etag);
  else etags.delete(factoryId);
  return result.data;
}

export interface UseFactoryResult {
  data: FactorySnapshot | undefined;
  error: Error | null;
  /** The data on screen is from before a failed refresh. */
  isStale: boolean;
  isLoading: boolean;
  /** When `data` was fetched (epoch ms), 0 before the first success. */
  updatedAt: number;
  refetch: () => void;
}

/**
 * One factory's live snapshot, ETag-aware (a 304 keeps the cached object).
 *
 * @param factoryId - The factory (root session) id; `null` disables the read.
 */
export function useFactory(factoryId: string | null | undefined): UseFactoryResult {
  const queryClient = useQueryClient();
  const refetchInterval = useFallbackInterval();
  const query = useQuery<FactorySnapshot>({
    queryKey: factoryQueryKey(factoryId ?? ""),
    queryFn: ({ signal }) => readSnapshot(queryClient, factoryId as string, signal),
    enabled: Boolean(factoryId),
    refetchInterval,
    retry: retryTransient,
  });
  const error = (query.error as Error | null) ?? null;
  return {
    data: query.data,
    error,
    isStale: query.data !== undefined && error !== null,
    isLoading: query.isLoading,
    updatedAt: query.dataUpdatedAt,
    refetch: () => void query.refetch(),
  };
}

/** A factory's activity, newest first, paged backwards by event time. */
export function useFactoryEvents(factoryId: string | null | undefined, filters: EventFilters = {}) {
  const refetchInterval = useFallbackInterval();
  return useInfiniteQuery({
    queryKey: factoryEventsQueryKey(factoryId ?? "", filters),
    queryFn: ({ pageParam }) =>
      fetchFactoryEvents(factoryId as string, { ...filters, before: pageParam }),
    initialPageParam: null as number | null,
    getNextPageParam: (lastPage) =>
      lastPage.has_more && lastPage.data.length > 0 ? lastPage.data.at(-1)!.ts : undefined,
    enabled: Boolean(factoryId),
    refetchInterval,
    retry: retryTransient,
  });
}

/** Open decisions across every factory the viewer can see (Inbox mirror). */
export function useOpenFactoryDecisions(options: { enabled?: boolean } = {}) {
  const refetchInterval = useFallbackInterval();
  return useQuery<OpenDecisionsResponse>({
    queryKey: [...FACTORY_DECISIONS_QUERY_KEY, "open"],
    queryFn: fetchOpenDecisions,
    enabled: options.enabled ?? true,
    refetchInterval,
    retry: retryTransient,
  });
}

/** How many decisions wait on the viewer (badge counts); 0 until known. */
export function useFactoryDecisionCount(options: { enabled?: boolean } = {}): number {
  const refetchInterval = useFallbackInterval();
  const { data } = useQuery<number>({
    queryKey: [...FACTORY_DECISIONS_QUERY_KEY, "count"],
    queryFn: fetchOpenDecisionCount,
    enabled: options.enabled ?? true,
    refetchInterval,
    retry: retryTransient,
  });
  return data ?? 0;
}

/** Can this viewer start a factory here (GitHub, agents, sandbox host, flag)? */
export function usePreflight(options: { enabled?: boolean } = {}) {
  return useQuery<Preflight>({
    queryKey: FACTORY_PREFLIGHT_QUERY_KEY,
    queryFn: fetchFactoryPreflight,
    enabled: options.enabled ?? true,
    staleTime: 30_000,
    retry: retryTransient,
  });
}
