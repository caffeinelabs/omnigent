// Proof (calm rule 10): a node's recorded demos as the page shows them. The
// server picks the one the page shows (a change's current head, a project's
// newest, a feature's acceptance run) and counts the subtree; the page renders
// that row, the "▶" chip on Home's feature rows and the Demos sheet, and plays
// each demo in place with the PR page's player (`DemoMedia`).

import { useCallback } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import type { DemoFileView, DemoView } from "@/lib/factoryApi";
import { useOptionalFactoryShell } from "../../shell/FactoryShellContext";
import { NODE_STALE_MS, nodeQueryKeys } from "../nodeQueries";
import { useNodeSource } from "../nodeSource";
import type { DemoChip, DemoRef, DemoVerdict, NodeDemos, NodePage } from "../nodeTypes";

/** How many demos "n more demos" names: the subtree's, less the one the row shows. */
export function moreDemos(page: Pick<NodePage, "proof">): number {
  const proof = page.proof;
  if (!proof) return 0;
  return Math.max(0, proof.count - (proof.latest ? 1 : 0));
}

/** "n more demos", "1 more demo". */
export function moreWords(n: number): string {
  return `${n} more demo${n === 1 ? "" : "s"}`;
}

/** A verdict that did not pass (labelled with what did not). */
export function notPassed(verdict: DemoVerdict): boolean {
  return verdict !== "PASS";
}

/** What a play button says: "Play the PASS demo (0:14)". */
export function playLabel(demo: Pick<DemoRef, "verdict" | "duration">, what = "demo"): string {
  return `Play the ${demo.verdict} ${what}${demo.duration ? ` (${demo.duration})` : ""}`;
}

/** What a "▶" chip on a feature row says to a screen reader. */
export function chipLabel(chip: DemoChip, name: string): string {
  return `${playLabel(chip, "demo")} of ${name}`;
}

function file(id: string, kind: "poster" | "video", url: string): DemoFileView {
  return {
    id,
    name: kind === "video" ? "demo.webm" : "poster.png",
    kind,
    caption: null,
    content_type: kind === "video" ? "video/webm" : "image/png",
    bytes: 0,
    size: 0,
    url,
    step: null,
  };
}

/** A demo row as the PR page's player reads it: its poster and its recording, by URL. */
export function asDemoView(demo: DemoRef): DemoView {
  const files = [
    ...(demo.poster_url ? [file("poster", "poster", demo.poster_url)] : []),
    ...(demo.video_url ? [file("video", "video", demo.video_url)] : []),
  ];
  return {
    id: demo.id,
    status: demo.verdict,
    head_sha: "",
    is_current_head: !demo.stale,
    title: demo.full_title ?? demo.title,
    summary: "",
    plan_source: null,
    duration_s: demo.duration_s ?? null,
    console_errors: null,
    steps: [],
    files,
    video_file_id: demo.video_url ? "video" : null,
    poster_file_id: demo.poster_url ? "poster" : null,
    preview_url: null,
    created_at: Math.floor(Date.parse(demo.at) / 1000) || 0,
    by: null,
  };
}

/** The Demos sheet's list: the subtree's demos, newest first, a page at a time. */
export function useNodeDemos(ref: string, open: boolean, factoryId?: string) {
  const shell = useOptionalFactoryShell();
  const fid = factoryId ?? shell?.factoryId ?? "";
  const source = useNodeSource();
  const query = useInfiniteQuery({
    queryKey: [...nodeQueryKeys.node(fid, source?.kind ?? "pending", ref), "demos"],
    queryFn: ({ pageParam, signal }) =>
      source?.demos ? source.demos(fid, ref, signal, pageParam || undefined) : null,
    initialPageParam: "",
    getNextPageParam: (last: NodeDemos | null) => last?.next || undefined,
    enabled: Boolean(open && source?.demos && fid && ref),
    staleTime: NODE_STALE_MS,
  });
  const pages = query.data?.pages ?? [];
  const first = pages[0] ?? null;
  const { fetchNextPage } = query;
  const more = useCallback(() => void fetchNextPage(), [fetchNextPage]);
  return {
    items: pages.flatMap((p) => p?.items ?? []),
    total: first?.total ?? 0,
    latest: first?.latest,
    missing: query.isSuccess && first === null,
    pending: query.isPending && open,
    error: query.error,
    more: query.hasNextPage ? more : undefined,
    loading: query.isFetchingNextPage,
  };
}
