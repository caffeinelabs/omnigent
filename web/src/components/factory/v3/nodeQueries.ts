// Node pages and level views through TanStack Query, keyed by factory and
// ref (a ref is an address, so a moved node keeps its cache). Pages are read
// ahead on hover or focus; an answer refreshes every node of the factory.
// Children and level groups past their first 60 are read when asked.

import { useCallback } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useOptionalFactoryShell } from "../shell/FactoryShellContext";
import { type NodeSource, useNodeSource } from "./nodeSource";
import type { ChildPage, DecisionAction, DecisionRef, LevelGroup, NodeSummary } from "./nodeTypes";

/** Node pages are fresh for 5 s, as long as the server memoizes its facts. */
export const NODE_STALE_MS = 5_000;

export const nodeQueryKeys = {
  all: (fid: string) => ["factory-node", fid] as const,
  node: (fid: string, kind: NodeSource["kind"] | "pending", ref: string) =>
    ["factory-node", fid, kind, "node", ref] as const,
  level: (fid: string, kind: NodeSource["kind"] | "pending", ref: string, level: string) =>
    ["factory-node", fid, kind, "level", ref, level] as const,
};

function useFid(factoryId?: string): string {
  const shell = useOptionalFactoryShell();
  return factoryId ?? shell?.factoryId ?? "";
}

/** One node's page (null data: no node has that ref). */
export function useNode(ref: string, factoryId?: string) {
  const fid = useFid(factoryId);
  const source = useNodeSource();
  return useQuery({
    queryKey: nodeQueryKeys.node(fid, source?.kind ?? "pending", ref),
    queryFn: ({ signal }) => source!.node(fid, ref, signal),
    enabled: Boolean(source && fid && ref),
    staleTime: NODE_STALE_MS,
  });
}

/** One level under a node, grouped under parents (off when `level` is null). */
export function useLevel(ref: string, level: string | null, factoryId?: string) {
  const fid = useFid(factoryId);
  const source = useNodeSource();
  return useQuery({
    queryKey: nodeQueryKeys.level(fid, source?.kind ?? "pending", ref, level ?? ""),
    queryFn: ({ signal }) => source!.level(fid, ref, level!, signal),
    enabled: Boolean(source && fid && ref && level),
    staleTime: NODE_STALE_MS,
  });
}

/** The next pages of a list, read one at a time when the person asks. */
export interface MorePages {
  /** Items read so far past the first page. */
  items: NodeSummary[];
  /** Read the next 60; absent when there is nothing more. */
  more?: () => void;
  loading: boolean;
}

/** A node's children past the first 60 (`children.next`), appended as they are read. */
export function useMoreChildren(ref: string, first: ChildPage, factoryId?: string): MorePages {
  const fid = useFid(factoryId);
  const source = useNodeSource();
  const query = useInfiniteQuery({
    queryKey: [
      ...nodeQueryKeys.node(fid, source?.kind ?? "pending", ref),
      "more",
      first.next ?? "",
    ],
    queryFn: ({ pageParam, signal }) => source!.node(fid, ref, signal, pageParam),
    initialPageParam: first.next ?? "",
    getNextPageParam: (last) => last?.children.next || undefined,
    enabled: false,
    staleTime: NODE_STALE_MS,
  });
  const pages = query.data?.pages ?? [];
  const next = pages.length ? pages.at(-1)?.children.next : first.next;
  const { fetchNextPage } = query;
  const more = useCallback(() => void fetchNextPage(), [fetchNextPage]);
  return {
    items: pages.flatMap((p) => p?.children.items ?? []),
    more: source && next ? more : undefined,
    loading: query.isFetching,
  };
}

/** One group of a level view past its first 60 (`group.next`), appended as they are read. */
export function useMoreInGroup(
  scope: string,
  level: string,
  group: LevelGroup,
  factoryId?: string,
): MorePages {
  const fid = useFid(factoryId);
  const source = useNodeSource();
  const parent = group.parent.ref;
  const query = useInfiniteQuery({
    queryKey: [
      ...nodeQueryKeys.level(fid, source?.kind ?? "pending", scope, level),
      "more",
      parent,
      group.next ?? "",
    ],
    queryFn: ({ pageParam, signal }) => source!.level(fid, scope, level, signal, pageParam),
    initialPageParam: group.next ?? "",
    getNextPageParam: (last) =>
      last?.groups.find((g) => g.parent.ref === parent)?.next || undefined,
    enabled: false,
    staleTime: NODE_STALE_MS,
  });
  const pages = (query.data?.pages ?? []).map((p) =>
    p?.groups.find((g) => g.parent.ref === parent),
  );
  const next = pages.length ? pages.at(-1)?.next : group.next;
  const { fetchNextPage } = query;
  const more = useCallback(() => void fetchNextPage(), [fetchNextPage]);
  return {
    items: pages.flatMap((g) => g?.items ?? []),
    more: source && next ? more : undefined,
    loading: query.isFetching,
  };
}

/** Read a node's page ahead (a link under the pointer or the keyboard). */
export function usePrefetchNode(factoryId?: string): (ref: string) => void {
  const fid = useFid(factoryId);
  const source = useNodeSource();
  const client = useQueryClient();
  return useCallback(
    (ref: string) => {
      if (!source || !fid) return;
      void client.prefetchQuery({
        queryKey: nodeQueryKeys.node(fid, source.kind, ref),
        queryFn: ({ signal }) => source.node(fid, ref, signal),
        staleTime: NODE_STALE_MS,
      });
    },
    [client, fid, source],
  );
}

/** Answer a decision; every node page of the factory reads again. */
export function useAnswerDecision(factoryId?: string) {
  const fid = useFid(factoryId);
  const source = useNodeSource();
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ decision, action }: { decision: DecisionRef; action: DecisionAction }) =>
      source ? source.answer(fid, decision, action) : Promise.resolve(),
    onSettled: () => client.invalidateQueries({ queryKey: nodeQueryKeys.all(fid) }),
  });
}
