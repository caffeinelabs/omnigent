// TanStack Query bindings for the team chart and editor: the catalog a team
// is built from (`GET …/catalog?project=`), the definition versions behind its
// history, and the server's dry run of the staged changes (`POST
// …/definition/preview`), which says the cost, class, approvers, effects and
// what Tier 0 refuses. They sit under `["factory-team", fid]`, which only
// definition frames (and local writes) refresh, so a busy factory does not
// re-read them on every event. Apply, Undo and Propose use the learning hooks
// (`useDefinitionEdit`, `usePinDefinition`, `useProposeChange`).

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { factoryTeamQueryKey } from "@/hooks/useFactory";
import {
  type CatalogResponse,
  type DefinitionChange,
  type DefinitionPreviewResponse,
  type DefinitionVersionsResponse,
  type TeamChangeWhen,
  fetchDefinitionVersions,
  fetchFactoryCatalog,
  previewFactoryDefinition,
} from "@/lib/factoryApi";
import { ApiError } from "@/lib/sessionsApi";

/** How long the catalog's rates stay fresh (the server reads 30 days of spans). */
export const CATALOG_STALE_MS = 5 * 60_000;
/** The versions change only with the definition, whose frames refresh them anyway. */
export const VERSIONS_STALE_MS = 5 * 60_000;
/** Typing in the budget field previews once it pauses. */
export const PREVIEW_DEBOUNCE_MS = 300;

function retryTransient(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < 1;
}

/** Under `["factory-team", fid]`: only definition frames refresh it. */
export const teamKey = (factoryId: string, ...rest: unknown[]) =>
  [...factoryTeamQueryKey(factoryId), ...rest] as const;

export const teamCatalogKey = (factoryId: string, slug: string) =>
  teamKey(factoryId, "catalog", slug);

/** What project `slug`'s team can be made of, with its locks. */
export function useTeamCatalog(factoryId: string, slug: string, enabled = true) {
  return useQuery<CatalogResponse>({
    queryKey: teamCatalogKey(factoryId, slug),
    queryFn: ({ signal }) => fetchFactoryCatalog(factoryId, slug, signal),
    enabled: Boolean(factoryId && slug) && enabled,
    staleTime: CATALOG_STALE_MS,
    retry: retryTransient,
  });
}

/** The definition versions (team history, "changed in vN", Undo). */
export function useTeamVersions(factoryId: string, enabled = true) {
  return useQuery<DefinitionVersionsResponse>({
    queryKey: teamKey(factoryId, "definition-versions"),
    queryFn: ({ signal }) => fetchDefinitionVersions(factoryId, signal),
    enabled: Boolean(factoryId) && enabled,
    staleTime: VERSIONS_STALE_MS,
    retry: retryTransient,
  });
}

/** `value`, once it has stopped changing for `ms`. */
export function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return settled;
}

/**
 * The server's dry run of `changes` (nothing stored). Keeps the last answer
 * while a newer one loads, so the panel does not flicker as people edit.
 */
export function useTeamPreview(
  factoryId: string,
  changes: readonly DefinitionChange[],
  when: TeamChangeWhen,
  baseVersion: number | null,
) {
  const body = useDebounced(
    JSON.stringify({ changes, when, base_version: baseVersion ?? undefined }),
    PREVIEW_DEBOUNCE_MS,
  );
  const settled =
    body === JSON.stringify({ changes, when, base_version: baseVersion ?? undefined });
  // What is sent is the settled body: the first pick waits for it, never sending no changes.
  const sends = (JSON.parse(body) as { changes: unknown[] }).changes.length > 0;
  const query = useQuery<DefinitionPreviewResponse>({
    queryKey: teamKey(factoryId, "preview", body),
    queryFn: () => previewFactoryDefinition(factoryId, JSON.parse(body)),
    enabled: Boolean(factoryId) && changes.length > 0 && sends,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    retry: retryTransient,
  });
  return { ...query, settled };
}
