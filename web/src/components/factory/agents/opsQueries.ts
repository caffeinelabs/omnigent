// TanStack Query bindings for the ops pages: the fleet, one agent, loops,
// issues and the cost ledger. Keys sit under the snapshot's `["factory", fid]`
// so every `factory_changed` frame (the throttled `now` scope included)
// refreshes them; the session-updates socket being down falls back to a poll.

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSessionUpdatesConnected } from "@/hooks/useSessionUpdatesConnected";
import { factoryQueryKey, FACTORY_FALLBACK_POLL_MS } from "@/hooks/useFactory";
import {
  type AgentDetailResponse,
  type AlertRoutingView,
  type AgentsResponse,
  type CostResponse,
  type IssueFilters,
  type IssueLifecycle,
  type IssuePageResponse,
  type IssuesResponse,
  type IssueView,
  type LoopsResponse,
  type SpanKind,
  type SpanPage,
  fetchAlertRouting,
  fetchFactoryAgent,
  fetchFactoryAgents,
  fetchFactoryCost,
  fetchFactoryIssue,
  fetchFactoryIssues,
  fetchFactoryLoops,
  fetchFactorySpans,
  updateFactoryIssue,
} from "@/lib/factoryApi";
import { ApiError } from "@/lib/sessionsApi";

/** A key under the snapshot's, so its invalidation reaches every ops read. */
export const opsKey = (factoryId: string, ...rest: unknown[]) =>
  [...factoryQueryKey(factoryId), "ops", ...rest] as const;

/**
 * One agent's page data: off the snapshot's key so `now` ticks don't re-read
 * its items and mail; its stream and a poll refresh it instead.
 */
export const agentDetailKey = (factoryId: string, sessionId: string) =>
  ["factory-agent", factoryId, sessionId] as const;

/** An incident's page: under the spans' key, refreshed by full frames and a slow poll. */
export const issuePageKey = (factoryId: string, ref: string) =>
  ["factory-spans", factoryId, "issue", ref] as const;

/** The incident page re-reads its steps this often (its status comes from the issues list). */
export const ISSUE_PAGE_POLL_MS = 60_000;

function retryTransient(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < 1;
}

function useFallbackInterval(): number | false {
  return useSessionUpdatesConnected() ? false : FACTORY_FALLBACK_POLL_MS;
}

/** Whether a read failed because the server has no such route yet (an older build). */
export function isMissingRoute(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.status === 405);
}

/** The fleet with its live "now"; refreshed by every `now` frame. */
export function useFactoryAgents(factoryId: string, options: { enabled?: boolean } = {}) {
  const refetchInterval = useFallbackInterval();
  return useQuery<AgentsResponse>({
    queryKey: opsKey(factoryId, "agents"),
    queryFn: ({ signal }) => fetchFactoryAgents(factoryId, {}, signal),
    enabled: Boolean(factoryId) && (options.enabled ?? true),
    placeholderData: keepPreviousData,
    refetchInterval,
    retry: retryTransient,
  });
}

/** One agent's page data; `liveMs` polls while the page is open (the stream nudges it too). */
export function useFactoryAgent(
  factoryId: string,
  sessionId: string,
  options: { liveMs?: number | false } = {},
) {
  const fallback = useFallbackInterval();
  return useQuery<AgentDetailResponse>({
    queryKey: agentDetailKey(factoryId, sessionId),
    queryFn: ({ signal }) => fetchFactoryAgent(factoryId, sessionId, signal),
    enabled: Boolean(factoryId && sessionId),
    placeholderData: keepPreviousData,
    refetchInterval: options.liveMs ?? fallback,
    retry: retryTransient,
  });
}

/** A project's loops (the PR strip filters them to its PR and task). */
export function useFactoryLoops(
  factoryId: string,
  filters: { project?: string | null } = {},
  options: { enabled?: boolean } = {},
) {
  const refetchInterval = useFallbackInterval();
  const project = filters.project ?? undefined;
  return useQuery<LoopsResponse>({
    queryKey: opsKey(factoryId, "loops", project ?? null),
    queryFn: ({ signal }) => fetchFactoryLoops(factoryId, { project }, signal),
    enabled: Boolean(factoryId) && (options.enabled ?? true),
    refetchInterval,
    retry: retryTransient,
  });
}

/** Loop iterations and PR rounds of a project (attempts `r0..rk`, review rounds). */
export function useLoopSpans(
  factoryId: string,
  project: string | null,
  sinceMs: number,
  options: { enabled?: boolean } = {},
) {
  const refetchInterval = useFallbackInterval();
  const kinds: SpanKind[] = ["loop", "loop_iter", "pr_round"];
  return useQuery<SpanPage>({
    queryKey: ["factory-spans", factoryId, "loops", project, sinceMs],
    queryFn: ({ signal }) =>
      fetchFactorySpans(
        factoryId,
        { since: sinceMs, kinds, limit: 2000, ...(project ? { project } : {}) },
        signal,
      ),
    enabled: Boolean(factoryId) && (options.enabled ?? true),
    placeholderData: keepPreviousData,
    refetchInterval,
    retry: retryTransient,
  });
}

/** Incidents and findings, newest first; `issues` frames refresh them. */
export function useFactoryIssues(
  factoryId: string,
  filters: IssueFilters = {},
  options: { enabled?: boolean } = {},
) {
  const refetchInterval = useFallbackInterval();
  return useQuery<IssuesResponse>({
    queryKey: opsKey(factoryId, "issues", filters),
    queryFn: ({ signal }) => fetchFactoryIssues(factoryId, filters, signal),
    enabled: Boolean(factoryId) && (options.enabled ?? true),
    placeholderData: keepPreviousData,
    refetchInterval,
    retry: retryTransient,
  });
}

/** One incident's page: the issue, the steps before it, the people it is for. */
export function useFactoryIssue(factoryId: string, ref: string) {
  const fallback = useFallbackInterval();
  return useQuery<IssuePageResponse>({
    queryKey: issuePageKey(factoryId, ref),
    queryFn: ({ signal }) => fetchFactoryIssue(factoryId, ref, signal),
    enabled: Boolean(factoryId && ref),
    refetchInterval: fallback === false ? ISSUE_PAGE_POLL_MS : fallback,
    retry: retryTransient,
  });
}

/** Ack, assign, snooze or resolve; the lists refresh from the answer and the next frame. */
export function useIssueLifecycle(factoryId: string) {
  const queryClient = useQueryClient();
  return useMutation<{ issue: IssueView }, Error, { ref: string; change: IssueLifecycle }>({
    mutationFn: ({ ref, change }) => updateFactoryIssue(factoryId, ref, change),
    onSettled: (_data, _error, { ref }) => {
      void queryClient.invalidateQueries({ queryKey: factoryQueryKey(factoryId) });
      void queryClient.invalidateQueries({ queryKey: ["factory-events", factoryId] });
      void queryClient.invalidateQueries({ queryKey: issuePageKey(factoryId, ref) });
    },
  });
}

/** The cost ledger grouped by `groupBy` (display cost). */
export function useFactoryCost(
  factoryId: string,
  options: { groupBy: string[]; project?: string | null; enabled?: boolean },
) {
  return useQuery<CostResponse>({
    queryKey: opsKey(factoryId, "cost", options.groupBy, options.project ?? null),
    queryFn: ({ signal }) =>
      fetchFactoryCost(
        factoryId,
        { groupBy: options.groupBy, ...(options.project ? { project: options.project } : {}) },
        signal,
      ),
    enabled: Boolean(factoryId) && (options.enabled ?? true),
    staleTime: 60_000,
    retry: retryTransient,
  });
}

/** Where incidents go: the cockpit, browser notifications, Slack (whether set). */
export function useAlertRouting(factoryId: string) {
  return useQuery<AlertRoutingView>({
    queryKey: opsKey(factoryId, "alert-routing"),
    queryFn: ({ signal }) => fetchAlertRouting(factoryId, signal),
    enabled: Boolean(factoryId),
    staleTime: 5 * 60_000,
    retry: retryTransient,
  });
}
