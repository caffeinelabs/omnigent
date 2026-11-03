// The new cockpit's project page reads: the charter's versions (refetched
// whenever the snapshot's charter changes, and after the page's own writes),
// the opportunity a project came from (its evidence for the why-chain; null
// without the discovery ledger), and the project's tasks.

import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { type ProjectView, type TaskView, factoryErrorFromResponse } from "@/lib/factoryApi";
import { type CharterVersionsResponse, fetchCharterVersions } from "@/lib/factoryProjectsApi";
import { fetchFactoryTasks } from "@/lib/factoryRoomApi";
import { authenticatedFetch } from "@/lib/identity";
import { ApiError } from "@/lib/sessionsApi";
import { type OpportunityFacts, opportunityFacts } from "./projectModel";

function retryTransient(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < 1;
}

export const charterVersionsKey = (factoryId: string, slug: string) =>
  ["factory-charter", factoryId, slug] as const;

/** What the snapshot says of the charter: a new value refetches its versions. */
function charterStamp(project: ProjectView): string {
  return JSON.stringify([project.charter, project.priority]);
}

/** The project's charter history, newest first. */
export function useCharterVersions(factoryId: string, project: ProjectView, enabled = true) {
  return useQuery<CharterVersionsResponse>({
    queryKey: [...charterVersionsKey(factoryId, project.slug), charterStamp(project)],
    queryFn: ({ signal }) => fetchCharterVersions(factoryId, project.slug, signal),
    enabled: Boolean(factoryId) && enabled,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    retry: retryTransient,
  });
}

/** Refetch the charter's versions now (after an edit, an Undo or a revert). */
export function useRefreshCharterVersions(factoryId: string, slug: string) {
  const client = useQueryClient();
  return useCallback(
    () => void client.invalidateQueries({ queryKey: charterVersionsKey(factoryId, slug) }),
    [client, factoryId, slug],
  );
}

/** `GET …/opportunities/<O-n>`: its evidence; null without the ledger (discovery off). */
export function useOpportunityFacts(factoryId: string, ref: string | null, enabled: boolean) {
  return useQuery<OpportunityFacts | null>({
    queryKey: ["factory-opportunity", factoryId, ref],
    queryFn: async ({ signal }) => {
      const path = `/v1/factory/factories/${encodeURIComponent(factoryId)}/opportunities/${encodeURIComponent(ref ?? "")}`;
      const resp = await authenticatedFetch(path, { signal });
      if (resp.status === 404) return null;
      if (!resp.ok) throw await factoryErrorFromResponse(resp);
      return opportunityFacts(await resp.json());
    },
    enabled: Boolean(factoryId && ref) && enabled,
    staleTime: 5 * 60_000,
    retry: retryTransient,
  });
}

/** The project's tasks (the work graph's nodes), for the Work tab. */
export function useProjectTasks(factoryId: string, slug: string) {
  return useQuery<TaskView[]>({
    queryKey: ["factory-project-tasks", factoryId, slug],
    queryFn: async ({ signal }) => (await fetchFactoryTasks(factoryId, slug, signal)).tasks,
    enabled: Boolean(factoryId && slug),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    retry: retryTransient,
  });
}
