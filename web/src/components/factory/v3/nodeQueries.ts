// Node pages and level views through TanStack Query, keyed by factory and
// ref (a ref is an address, so a moved node keeps its cache). Pages are read
// ahead on hover or focus; an answer refreshes every node of the factory.

import { useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useOptionalFactoryShell } from "../shell/FactoryShellContext";
import { type NodeSource, useNodeSource } from "./nodeSource";
import type { DecisionAction, DecisionRef } from "./nodeTypes";

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
