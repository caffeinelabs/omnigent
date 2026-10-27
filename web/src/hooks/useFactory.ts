// TanStack Query bindings for the factory cockpit.
//
// Freshness is push-driven: a `factory_changed` frame on the session-updates
// socket invalidates these keys (see SessionUpdatesProvider). While that socket
// is down the factory reads fall back to a 15 s poll, and a failed refresh
// keeps the last good data on screen with its age instead of blanking it.
// Quiet ticks push nothing, so a page that ages them (the cockpit's health
// strip) also asks for a minimum refresh.

import {
  type QueryClient,
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useSessionUpdatesConnected } from "@/hooks/useSessionUpdatesConnected";
import { ApiError } from "@/lib/sessionsApi";
import {
  type DemoFilters,
  type DemoListResponse,
  type EventCursor,
  type EventFilters,
  type EventPage,
  type FactoryListResponse,
  type FactorySnapshot,
  type OpenDecisionsResponse,
  type Preflight,
  fetchFactories,
  fetchFactoryDemos,
  fetchFactoryEvents,
  fetchFactoryPreflight,
  fetchFactorySnapshot,
  fetchOpenDecisionCount,
  fetchOpenDecisions,
} from "@/lib/factoryApi";

/** Poll cadence while the session-updates socket is disconnected. */
export const FACTORY_FALLBACK_POLL_MS = 15_000;

/**
 * Factory pages re-read their snapshot at least this often while visible:
 * quiet supervisor ticks, polls and probes push no frame, and the health strip
 * ages their stamps against the clock (the ETag keeps an unchanged read cheap).
 */
export const COCKPIT_MIN_REFRESH_MS = 60_000;

export const factoryQueryKey = (factoryId: string) => ["factory", factoryId] as const;
export const factoryEventsQueryKey = (factoryId: string, filters: EventFilters = {}) =>
  ["factory-events", factoryId, filters] as const;
export const factoryDemosQueryKey = (factoryId: string, filters: DemoFilters = {}) =>
  ["factory-demos", factoryId, filters] as const;
export const FACTORIES_QUERY_KEY = ["factories"] as const;
export const FACTORY_DECISIONS_QUERY_KEY = ["factory-decisions"] as const;
export const FACTORY_PREFLIGHT_QUERY_KEY = ["factory-preflight"] as const;

/** Every query a `factory_changed` frame for `factoryId` makes stale. */
export function factoryInvalidationKeys(factoryId: string): readonly (readonly unknown[])[] {
  return [
    factoryQueryKey(factoryId),
    ["factory-events", factoryId],
    ["factory-demos", factoryId],
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

// Last ETag and server clock offset per factory, per QueryClient so separate
// clients (tests, embeds) never share each other's validators.
interface SnapshotMeta {
  etags: Map<string, string>;
  /** Seconds the server clock ran ahead of this browser's when a body arrived. */
  clockOffsets: Map<string, number>;
}
const snapshotMeta = new WeakMap<QueryClient, SnapshotMeta>();

function metaFor(queryClient: QueryClient): SnapshotMeta {
  let meta = snapshotMeta.get(queryClient);
  if (!meta) {
    meta = { etags: new Map(), clockOffsets: new Map() };
    snapshotMeta.set(queryClient, meta);
  }
  return meta;
}

/** Seconds the server clock ran ahead of this browser's at the factory's last snapshot (0 before one). */
export function factoryClockOffsetS(queryClient: QueryClient, factoryId: string): number {
  return metaFor(queryClient).clockOffsets.get(factoryId) ?? 0;
}

async function readSnapshot(
  queryClient: QueryClient,
  factoryId: string,
  signal: AbortSignal,
): Promise<FactorySnapshot> {
  const { etags, clockOffsets } = metaFor(queryClient);
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
  clockOffsets.set(factoryId, result.data.server_time - Date.now() / 1000);
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
  /**
   * Seconds the server clock runs ahead of this browser's, measured when the
   * snapshot body arrived (0 until a body is fetched).
   */
  clockOffsetS: number;
  refetch: () => void;
}

export interface UseFactoryOptions {
  /**
   * Re-read at least this often while the tab is visible, and on return to
   * it, even with the socket live. Quiet supervisor ticks and polls push no
   * frame, so a page that ages them against the clock needs this.
   */
  minRefreshMs?: number;
}

/**
 * One factory's live snapshot, ETag-aware (a 304 keeps the cached object).
 *
 * @param factoryId - The factory (root session) id; `null` disables the read.
 */
export function useFactory(
  factoryId: string | null | undefined,
  options: UseFactoryOptions = {},
): UseFactoryResult {
  const queryClient = useQueryClient();
  const fallback = useFallbackInterval();
  const { minRefreshMs } = options;
  const refetchInterval =
    minRefreshMs == null
      ? fallback
      : fallback === false
        ? minRefreshMs
        : Math.min(fallback, minRefreshMs);
  const query = useQuery<FactorySnapshot>({
    queryKey: factoryQueryKey(factoryId ?? ""),
    queryFn: ({ signal }) => readSnapshot(queryClient, factoryId as string, signal),
    enabled: Boolean(factoryId),
    refetchInterval,
    // Timer refetches pause while the tab is hidden; catch up on return.
    ...(minRefreshMs != null ? { refetchOnWindowFocus: true } : {}),
    retry: retryTransient,
  });
  const error = (query.error as Error | null) ?? null;
  return {
    data: query.data,
    error,
    isStale: query.data !== undefined && error !== null,
    isLoading: query.isLoading,
    updatedAt: query.dataUpdatedAt,
    clockOffsetS: factoryId ? factoryClockOffsetS(queryClient, factoryId) : 0,
    refetch: () => void query.refetch(),
  };
}

/**
 * The cursor of the page after `page`: the server's, else its oldest event.
 * Time plus id, so events sharing a millisecond are never skipped.
 */
export function nextEventCursor(page: EventPage): EventCursor | undefined {
  if (!page.has_more) return undefined;
  if (page.next_before != null) {
    return { before: page.next_before, before_id: page.next_before_id ?? null };
  }
  const oldest = page.data.at(-1);
  return oldest ? { before: oldest.ts, before_id: oldest.id } : undefined;
}

/** A factory's activity, newest first, paged backwards by event time and id. */
export function useFactoryEvents(factoryId: string | null | undefined, filters: EventFilters = {}) {
  const refetchInterval = useFallbackInterval();
  return useInfiniteQuery({
    queryKey: factoryEventsQueryKey(factoryId ?? "", filters),
    queryFn: ({ pageParam }) =>
      fetchFactoryEvents(factoryId as string, { ...filters, cursor: pageParam }),
    initialPageParam: null as EventCursor | null,
    getNextPageParam: nextEventCursor,
    enabled: Boolean(factoryId),
    refetchInterval,
    retry: retryTransient,
  });
}

/** A factory's recorded demos, newest first (a new demo's push refreshes them). */
export function useFactoryDemos(factoryId: string | null | undefined, filters: DemoFilters = {}) {
  const refetchInterval = useFallbackInterval();
  return useQuery<DemoListResponse>({
    queryKey: factoryDemosQueryKey(factoryId ?? "", filters),
    queryFn: ({ signal }) => fetchFactoryDemos(factoryId as string, filters, signal),
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
