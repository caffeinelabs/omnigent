// TanStack Query bindings for the team chart and editor: the catalog a team
// is built from (`GET …/catalog?project=`) and the server's dry run of the
// staged changes (`POST …/definition/preview`), which says the cost, class,
// approvers, effects and what Tier 0 refuses. Both sit under the Room's key,
// so a definition change anywhere refreshes them. Apply, Undo and Propose use
// the learning hooks (`useDefinitionEdit`, `usePinDefinition`, `useProposeChange`).

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  type CatalogResponse,
  type DefinitionChange,
  type DefinitionPreviewResponse,
  type TeamChangeWhen,
  fetchFactoryCatalog,
  previewFactoryDefinition,
} from "@/lib/factoryApi";
import { ApiError } from "@/lib/sessionsApi";
import { learnKey } from "../insights/learnQueries";

/** How long the catalog's rates stay fresh (the server reads 30 days of spans). */
export const CATALOG_STALE_MS = 5 * 60_000;
/** Typing in the budget field previews once it pauses. */
export const PREVIEW_DEBOUNCE_MS = 300;

function retryTransient(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < 1;
}

export const teamCatalogKey = (factoryId: string, slug: string) =>
  learnKey(factoryId, "team-catalog", slug);

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
  const query = useQuery<DefinitionPreviewResponse>({
    queryKey: learnKey(factoryId, "team-preview", body),
    queryFn: () => previewFactoryDefinition(factoryId, JSON.parse(body)),
    enabled: Boolean(factoryId) && changes.length > 0,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    retry: retryTransient,
  });
  return { ...query, settled };
}
