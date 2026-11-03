// TanStack Query bindings for Steer: the server's dry run of what is staged
// (`POST …/definition/preview`, debounced, keeping the last answer while a
// newer one loads), the gates, the factory's team catalog (templates and the
// roles' vendor · models), a source's access log and test, and the discovery
// summary's per-source counts. Costly reads sit under the team key, which only
// definition frames refresh; Apply, Undo, restore and trial decisions go
// through the learning hooks, which refresh everything a definition write moves.

import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import {
  type CatalogResponse,
  type DefinitionPreviewRequest,
  type DefinitionPreviewResponse,
  type DefinitionRestoreRequest,
  fetchFactoryCatalog,
  previewFactoryDefinition,
  restoreFactoryDefinition,
} from "@/lib/factoryApi";
import { setSourcePaused } from "@/lib/factoryPaletteApi";
import {
  type GatesResponse,
  type SourceCounts,
  type SourceLogResponse,
  fetchFactoryGates,
  fetchSourceCounts,
  fetchSourceLog,
  testSource,
} from "@/lib/factorySteerApi";
import { ApiError } from "@/lib/sessionsApi";
import { useInvalidateLearn } from "../insights/learnQueries";
import { PREVIEW_DEBOUNCE_MS, teamKey, useDebounced } from "../team/teamQueries";
import { type SteerStaging, stagedChanges } from "./steerModel";

function retryTransient(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < 1;
}

/** The preview body for what is staged: the changes, or the version to restore. */
export function previewBody(
  staging: SteerStaging,
  baseVersion: number | null,
): DefinitionPreviewRequest | null {
  if (staging.restore != null) return { restore: staging.restore };
  const changes = stagedChanges(staging);
  if (!changes.length) return null;
  return { changes, ...(baseVersion != null ? { base_version: baseVersion } : {}) };
}

/**
 * The server's dry run of what is staged (nothing stored): diff, class, who
 * applies it, effects, the re-rank and the trial verdict. `settled` is false
 * while a newer staging waits out the debounce.
 */
export function useSteerPreview(
  factoryId: string,
  staging: SteerStaging,
  baseVersion: number | null,
) {
  const now = JSON.stringify(previewBody(staging, baseVersion));
  const body = useDebounced(now, PREVIEW_DEBOUNCE_MS);
  const query = useQuery<DefinitionPreviewResponse>({
    queryKey: teamKey(factoryId, "steer-preview", body),
    queryFn: () =>
      previewFactoryDefinition(factoryId, JSON.parse(body) as DefinitionPreviewRequest),
    enabled: Boolean(factoryId) && body !== "null",
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    retry: retryTransient,
  });
  return { ...query, settled: body === now };
}

/** The gates (read only); null on a server without the route. */
export function useFactoryGates(factoryId: string, enabled = true) {
  return useQuery<GatesResponse | null>({
    queryKey: teamKey(factoryId, "gates"),
    queryFn: async ({ signal }) => {
      try {
        return await fetchFactoryGates(factoryId, signal);
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
    enabled: Boolean(factoryId) && enabled,
    staleTime: 10 * 60_000,
    retry: retryTransient,
  });
}

/** The factory's team catalog (templates, roles, vendor · models); null without team specs. */
export function useSteerCatalog(factoryId: string, enabled = true) {
  return useQuery<CatalogResponse | null>({
    queryKey: teamKey(factoryId, "catalog", ""),
    queryFn: async ({ signal }) => {
      try {
        return await fetchFactoryCatalog(factoryId, undefined, signal);
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
    enabled: Boolean(factoryId) && enabled,
    staleTime: 5 * 60_000,
    retry: retryTransient,
  });
}

/** Per-source counts from the discovery summary (null while discovery is off here). */
export function useSourceCounts(factoryId: string, enabled: boolean) {
  return useQuery<SourceCounts[] | null>({
    queryKey: teamKey(factoryId, "source-counts"),
    queryFn: ({ signal }) => fetchSourceCounts(factoryId, signal),
    enabled: Boolean(factoryId) && enabled,
    staleTime: 60_000,
    retry: retryTransient,
  });
}

/** A source's access log (every read, sync, pause and resume). */
export function useSourceLog(factoryId: string, sourceId: string | null) {
  return useQuery<SourceLogResponse>({
    queryKey: teamKey(factoryId, "source-log", sourceId),
    queryFn: ({ signal }) => fetchSourceLog(factoryId, sourceId!, signal),
    enabled: Boolean(factoryId && sourceId),
    staleTime: 15_000,
    retry: retryTransient,
  });
}

/** Test a source (one call; nothing stored). */
export function useTestSource(factoryId: string) {
  return useMutation({ mutationFn: (sourceId: string) => testSource(factoryId, sourceId) });
}

/** Pause a source at once (Undo pins the version before) or resume it (owners). */
export function usePauseSource(factoryId: string) {
  const invalidate = useInvalidateLearn(factoryId);
  return useMutation({
    mutationFn: ({ source, paused }: { source: string; paused: boolean }) =>
      setSourcePaused(factoryId, source, paused),
    onSettled: invalidate,
  });
}

/** Store version K's document as the next version (owners). */
export function useRestoreDefinition(factoryId: string) {
  const invalidate = useInvalidateLearn(factoryId);
  return useMutation({
    mutationFn: (request: DefinitionRestoreRequest) => restoreFactoryDefinition(factoryId, request),
    onSettled: invalidate,
  });
}
