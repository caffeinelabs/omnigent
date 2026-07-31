import { useQuery } from "@tanstack/react-query";
import { fetchUsageSummary, type UsageSummary, type UsageSummaryParams } from "@/lib/usageApi";

/**
 * Aggregated usage + cost for the given window / dimension / scope, from
 * `GET /v1/usage/summary`. Cached briefly and re-keyed on every param so
 * switching period / group-by / user swaps to the right series without a
 * stale flash. Server enforces the admin scope; `user` is chrome-gated.
 */
export function useUsageSummary(params: UsageSummaryParams, options: { enabled?: boolean } = {}) {
  return useQuery<UsageSummary>({
    queryKey: ["usage-summary", params],
    queryFn: () => fetchUsageSummary(params),
    enabled: options.enabled ?? true,
    staleTime: 30_000,
  });
}
