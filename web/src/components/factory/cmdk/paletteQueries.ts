// The new palette's server search: `GET …/search?q=` for what is typed, once
// typing pauses. Its key sits outside the factory's live keys, so a busy
// factory does not re-run every search on each event; a hit goes stale after
// a few seconds instead. A server without the route answers 404, and the
// palette then searches the snapshot it already holds.

import { useQuery } from "@tanstack/react-query";
import { type SearchResponse, fetchFactorySearch } from "@/lib/factoryPaletteApi";
import { ApiError } from "@/lib/sessionsApi";
import { useDebounced } from "../team/teamQueries";

/** Typing pauses this long before the server searches. */
export const SEARCH_DEBOUNCE_MS = 150;
/** A search answer stays fresh this long (reopening the palette reuses it). */
export const SEARCH_STALE_MS = 10_000;

export const factorySearchKey = (factoryId: string, q: string) =>
  ["factory-search", factoryId, q] as const;

/** Whether the search failed only because this server has no such route. */
export function searchMissing(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

/**
 * The server's hits for `query` (debounced). `current` is true when the
 * answer is for what is typed now; until then the palette shows the
 * snapshot's matches.
 */
export function useFactorySearch(
  factoryId: string,
  query: string,
  enabled = true,
): { data: SearchResponse | undefined; current: boolean; missing: boolean; loading: boolean } {
  const debounced = useDebounced(query.trim(), SEARCH_DEBOUNCE_MS);
  const result = useQuery<SearchResponse>({
    queryKey: factorySearchKey(factoryId, debounced),
    queryFn: ({ signal }) => fetchFactorySearch(factoryId, debounced, signal),
    enabled: enabled && Boolean(factoryId) && debounced.length > 0,
    staleTime: SEARCH_STALE_MS,
    retry: false,
  });
  const settled = debounced === query.trim();
  return {
    data: result.data,
    current: settled && result.data != null,
    missing: searchMissing(result.error),
    loading: enabled && query.trim().length > 0 && (!settled || result.isFetching),
  };
}
