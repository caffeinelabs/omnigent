// The lenses' reads through TanStack Query (lineage, timeline, money) and the
// definition's levels. They sit under the factory's node key, so an answer or
// an applied verb reads them again with the pages.

import { useQuery } from "@tanstack/react-query";
import { useOptionalFactoryShell } from "../../shell/FactoryShellContext";
import { useNodeActions } from "../NodeContext";
import { nodeQueryKeys } from "../nodeQueries";
import { useNodeSource } from "../nodeSource";

/** A lens is fresh for 15 s (the timeline reads spans, so it is read less often than a page). */
export const LENS_STALE_MS = 15_000;

export type LensRead = "lineage" | "timeline" | "money";

export const lensQueryKeys = {
  lens: (fid: string, kind: string, lens: LensRead, ref: string) =>
    [...nodeQueryKeys.all(fid), kind, "lens", lens, ref] as const,
  levels: (fid: string, kind: string) => [...nodeQueryKeys.all(fid), kind, "levels"] as const,
};

/** The factory: the one named, else the page's, else the shell's. */
function useFid(factoryId?: string): string {
  const shell = useOptionalFactoryShell();
  const page = useNodeActions().factoryId;
  return factoryId || page || shell?.factoryId || "";
}

/** Where the node came from (null data: no node has that ref). */
export function useLineage(ref: string, enabled = true, factoryId?: string) {
  const fid = useFid(factoryId);
  const source = useNodeSource();
  return useQuery({
    queryKey: lensQueryKeys.lens(fid, source?.kind ?? "pending", "lineage", ref),
    queryFn: ({ signal }) => source!.lineage!(fid, ref, signal),
    enabled: Boolean(enabled && source?.lineage && fid && ref),
    staleTime: LENS_STALE_MS,
    retry: false,
  });
}

/** One lane per child over the last day. */
export function useTimeline(ref: string, enabled = true, factoryId?: string) {
  const fid = useFid(factoryId);
  const source = useNodeSource();
  return useQuery({
    queryKey: lensQueryKeys.lens(fid, source?.kind ?? "pending", "timeline", ref),
    queryFn: ({ signal }) => source!.timeline!(fid, ref, signal),
    enabled: Boolean(enabled && source?.timeline && fid && ref),
    staleTime: LENS_STALE_MS,
    retry: false,
  });
}

/** The children's cost against their budgets. */
export function useMoney(ref: string, enabled = true, factoryId?: string) {
  const fid = useFid(factoryId);
  const source = useNodeSource();
  return useQuery({
    queryKey: lensQueryKeys.lens(fid, source?.kind ?? "pending", "money", ref),
    queryFn: ({ signal }) => source!.money!(fid, ref, signal),
    enabled: Boolean(enabled && source?.money && fid && ref),
    staleTime: LENS_STALE_MS,
    retry: false,
  });
}

/** Both trees' levels as the definition names them, with its version. */
export function useLevelsInfo(enabled = true, factoryId?: string) {
  const fid = useFid(factoryId);
  const source = useNodeSource();
  return useQuery({
    queryKey: lensQueryKeys.levels(fid, source?.kind ?? "pending"),
    queryFn: ({ signal }) => source!.levels!(fid, signal),
    enabled: Boolean(enabled && source?.levels && fid),
    staleTime: LENS_STALE_MS,
    retry: false,
  });
}

/** Whether this source reads lenses at all (a source without them offers only Map and List). */
export function useLensSupport(): { lineage: boolean; timeline: boolean; money: boolean } {
  const source = useNodeSource();
  return {
    lineage: Boolean(source?.lineage),
    timeline: Boolean(source?.timeline),
    money: Boolean(source?.money),
  };
}
