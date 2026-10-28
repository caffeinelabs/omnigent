// TanStack Query bindings for the learning pages: Insights (KPIs, metrics,
// version markers), Improvements (proposals, scorecards, the definition, its
// versions and diffs, bundle drift) and Settings (definition edits, the Slack
// webhook). Keys sit under the Room's `["factory-room", fid]`, so full
// `factory_changed` frames (proposals, definition, insights) refresh them and
// `now` ticks don't.

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSessionUpdatesConnected } from "@/hooks/useSessionUpdatesConnected";
import { FACTORY_FALLBACK_POLL_MS, factoryInvalidationKeys } from "@/hooks/useFactory";
import {
  type BundlesResponse,
  type DefinitionDiffResponse,
  type DefinitionEditRequest,
  type DefinitionResponse,
  type DefinitionVersionsResponse,
  type HumanChangeRequest,
  type InsightsFilters,
  type InsightsResponse,
  type MetricGroupBy,
  type MetricsResponse,
  type ProposalStatus,
  type ProposalView,
  type ScorecardResponse,
  type VersionsResponse,
  decideTrial,
  fetchDefinitionDiff,
  fetchDefinitionVersions,
  fetchFactoryBundles,
  fetchFactoryDefinition,
  fetchFactoryInsights,
  fetchFactoryMetrics,
  fetchFactoryProposal,
  fetchFactoryVersions,
  fetchProposalScorecard,
  pinFactoryDefinition,
  proposeDefinitionChange,
  putFactoryDefinition,
  putSlackWebhook,
  requestFactoryRetro,
} from "@/lib/factoryApi";
import { applyProposal, fetchProposals, rejectProposal } from "@/lib/factoryRoomApi";
import { ApiError } from "@/lib/sessionsApi";

/** Under the Room's key: full frames refresh it. */
export const learnKey = (factoryId: string, ...rest: unknown[]) =>
  ["factory-room", factoryId, "learn", ...rest] as const;

function retryTransient(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < 1;
}

function useFallbackInterval(): number | false {
  return useSessionUpdatesConnected() ? false : FACTORY_FALLBACK_POLL_MS;
}

/** The KPI block with deltas, findings, retros and experiments. */
export function useFactoryInsights(factoryId: string, filters: InsightsFilters) {
  const refetchInterval = useFallbackInterval();
  return useQuery<InsightsResponse>({
    queryKey: learnKey(factoryId, "insights", filters),
    queryFn: ({ signal }) => fetchFactoryInsights(factoryId, filters, signal),
    enabled: Boolean(factoryId),
    placeholderData: keepPreviousData,
    refetchInterval,
    retry: retryTransient,
  });
}

/** Catalog metrics (all when `names` is empty), optionally grouped. */
export function useFactoryMetrics(
  factoryId: string,
  options: InsightsFilters & { names?: readonly string[]; groupBy?: MetricGroupBy | null },
  enabled = true,
) {
  const refetchInterval = useFallbackInterval();
  return useQuery<MetricsResponse>({
    queryKey: learnKey(factoryId, "metrics", options),
    queryFn: ({ signal }) => fetchFactoryMetrics(factoryId, options, signal),
    enabled: Boolean(factoryId) && enabled,
    placeholderData: keepPreviousData,
    refetchInterval,
    retry: retryTransient,
  });
}

export function useFactoryVersions(factoryId: string) {
  return useQuery<VersionsResponse>({
    queryKey: learnKey(factoryId, "versions"),
    queryFn: ({ signal }) => fetchFactoryVersions(factoryId, signal),
    enabled: Boolean(factoryId),
    retry: retryTransient,
  });
}

export function useFactoryBundles(factoryId: string, enabled = true) {
  return useQuery<BundlesResponse>({
    queryKey: learnKey(factoryId, "bundles"),
    queryFn: ({ signal }) => fetchFactoryBundles(factoryId, signal),
    enabled: Boolean(factoryId) && enabled,
    retry: retryTransient,
  });
}

export function useFactoryDefinition(factoryId: string) {
  const refetchInterval = useFallbackInterval();
  return useQuery<DefinitionResponse>({
    queryKey: learnKey(factoryId, "definition"),
    queryFn: ({ signal }) => fetchFactoryDefinition(factoryId, signal),
    enabled: Boolean(factoryId),
    refetchInterval,
    retry: retryTransient,
  });
}

export function useDefinitionVersions(factoryId: string, enabled = true) {
  return useQuery<DefinitionVersionsResponse>({
    queryKey: learnKey(factoryId, "definition-versions"),
    queryFn: ({ signal }) => fetchDefinitionVersions(factoryId, signal),
    enabled: Boolean(factoryId) && enabled,
    retry: retryTransient,
  });
}

export function useDefinitionDiff(factoryId: string, from: number | null, to: number | null) {
  return useQuery<DefinitionDiffResponse>({
    queryKey: learnKey(factoryId, "definition-diff", from, to),
    queryFn: ({ signal }) => fetchDefinitionDiff(factoryId, from!, to!, signal),
    enabled: Boolean(factoryId) && from != null && to != null && from !== to,
    staleTime: Infinity,
    retry: retryTransient,
  });
}

/** Every Tier-1 proposal (tuning, rules, code), newest first. */
export function useLearnProposals(factoryId: string, filters: { status?: ProposalStatus[] } = {}) {
  const refetchInterval = useFallbackInterval();
  return useQuery<ProposalView[]>({
    queryKey: learnKey(factoryId, "proposals", filters),
    queryFn: async ({ signal }) => {
      const kinds = ["tuning", "rules", "code"] as const;
      const pages = await Promise.all(
        kinds.map((kind) =>
          fetchProposals(factoryId, { kind, status: filters.status, limit: 200 }, signal),
        ),
      );
      return pages.flatMap((p) => p.data).sort((a, b) => b.number - a.number);
    },
    enabled: Boolean(factoryId),
    placeholderData: keepPreviousData,
    refetchInterval,
    retry: retryTransient,
  });
}

export function useFactoryProposal(factoryId: string, ref: string) {
  return useQuery<{ proposal: ProposalView }>({
    queryKey: learnKey(factoryId, "proposal", ref),
    queryFn: ({ signal }) => fetchFactoryProposal(factoryId, ref, signal),
    enabled: Boolean(factoryId && ref),
    retry: retryTransient,
  });
}

/** A trial's scorecard (live while on trial; polled slowly since metrics cache 5 min). */
export function useProposalScorecard(factoryId: string, ref: string, enabled = true) {
  return useQuery<ScorecardResponse>({
    queryKey: learnKey(factoryId, "scorecard", ref),
    queryFn: ({ signal }) => fetchProposalScorecard(factoryId, ref, signal),
    enabled: Boolean(factoryId && ref) && enabled,
    refetchInterval: 5 * 60_000,
    retry: retryTransient,
  });
}

/** Invalidate everything a learning write can move (the snapshot's counts included). */
export function useInvalidateLearn(factoryId: string) {
  const queryClient = useQueryClient();
  return () => {
    for (const key of factoryInvalidationKeys(factoryId)) {
      void queryClient.invalidateQueries({ queryKey: key });
    }
  };
}

export function useDefinitionEdit(factoryId: string) {
  const invalidate = useInvalidateLearn(factoryId);
  return useMutation({
    mutationFn: (edit: DefinitionEditRequest) => putFactoryDefinition(factoryId, edit),
    onSettled: invalidate,
  });
}

export function usePinDefinition(factoryId: string) {
  const invalidate = useInvalidateLearn(factoryId);
  return useMutation({
    mutationFn: (version: number) => pinFactoryDefinition(factoryId, version),
    onSettled: invalidate,
  });
}

export function useProposeChange(factoryId: string) {
  const invalidate = useInvalidateLearn(factoryId);
  return useMutation({
    mutationFn: (request: HumanChangeRequest) => proposeDefinitionChange(factoryId, request),
    onSettled: invalidate,
  });
}

export function useDecideTrial(factoryId: string) {
  const invalidate = useInvalidateLearn(factoryId);
  return useMutation({
    mutationFn: ({
      ref,
      action,
      note,
    }: {
      ref: string;
      action: "keep" | "revert";
      note?: string;
    }) => decideTrial(factoryId, ref, action, note),
    onSettled: invalidate,
  });
}

/** Approve (apply as a trial) or reject a pending proposal through its decision. */
export function useAnswerProposal(factoryId: string) {
  const invalidate = useInvalidateLearn(factoryId);
  return useMutation({
    mutationFn: ({ ref, verb, note }: { ref: string; verb: "apply" | "reject"; note?: string }) =>
      verb === "apply" ? applyProposal(factoryId, ref) : rejectProposal(factoryId, ref, note),
    onSettled: invalidate,
  });
}

export function useSlackWebhook(factoryId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (url: string | null) => putSlackWebhook(factoryId, url),
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: ["factory-alert-routing", factoryId] }),
  });
}

export function useRequestRetro(factoryId: string) {
  return useMutation({ mutationFn: () => requestFactoryRetro(factoryId) });
}
