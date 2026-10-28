// TanStack Query bindings for the Room, proposals, the work graph and the
// cards' loops and issues. Every key starts with `factory-room` and the
// factory id, so a `factory_changed` frame (scope `chat`, `proposals`,
// `tasks`, …) refreshes them all. Read marks also sync between this
// browser's tabs over a BroadcastChannel, and across devices through the server.

import { useEffect, useMemo } from "react";
import {
  type InfiniteData,
  type QueryClient,
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { FACTORY_FALLBACK_POLL_MS, FACTORY_DECISIONS_QUERY_KEY } from "@/hooks/useFactory";
import { useSessionUpdatesConnected } from "@/hooks/useSessionUpdatesConnected";
import { ApiError } from "@/lib/sessionsApi";
import type { DecisionStatus, IssueView, LoopView } from "@/lib/factoryApi";
import {
  type ChatPage,
  type GraphKind,
  type GraphResponse,
  type ProposalsResponse,
  type ThreadsResponse,
  fetchDecisionsByStatus,
  fetchFactoryGraph,
  fetchProposals,
  fetchRoomIssues,
  fetchRoomLoops,
  fetchRoomMessages,
  fetchRoomThreads,
  putRoomRead,
} from "@/lib/factoryRoomApi";

export const roomQueryKey = (factoryId: string) => ["factory-room", factoryId] as const;
export const threadsQueryKey = (factoryId: string) =>
  [...roomQueryKey(factoryId), "threads"] as const;
export const messagesQueryKey = (factoryId: string, thread: string) =>
  [...roomQueryKey(factoryId), "chat", thread] as const;
export const proposalsQueryKey = (factoryId: string) =>
  [...roomQueryKey(factoryId), "proposals"] as const;
export const graphQueryKey = (
  factoryId: string,
  kind: GraphKind,
  project: string | null,
  window: string | null,
) => [...roomQueryKey(factoryId), "graph", kind, project, window] as const;
export const loopsQueryKey = (factoryId: string) => [...roomQueryKey(factoryId), "loops"] as const;
export const issuesQueryKey = (factoryId: string) =>
  [...roomQueryKey(factoryId), "issues"] as const;

/** Messages one page of a thread holds. */
export const ROOM_PAGE_SIZE = 50;

function retryTransient(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < 1;
}

function useFallbackInterval(): number | false {
  return useSessionUpdatesConnected() ? false : FACTORY_FALLBACK_POLL_MS;
}

/** The Room's threads with the viewer's unread counts. */
export function useRoomThreads(factoryId: string | null | undefined) {
  const refetchInterval = useFallbackInterval();
  return useQuery<ThreadsResponse>({
    queryKey: threadsQueryKey(factoryId ?? ""),
    queryFn: ({ signal }) => fetchRoomThreads(factoryId as string, signal),
    enabled: Boolean(factoryId),
    refetchInterval,
    retry: retryTransient,
  });
}

/**
 * One thread, newest page first; `fetchNextPage` reads the page before the
 * oldest message on screen (`before_seq`).
 */
export function useRoomMessages(factoryId: string | null | undefined, thread: string | null) {
  const refetchInterval = useFallbackInterval();
  return useInfiniteQuery({
    queryKey: messagesQueryKey(factoryId ?? "", thread ?? ""),
    queryFn: ({ pageParam, signal }) =>
      fetchRoomMessages(
        factoryId as string,
        thread as string,
        { beforeSeq: pageParam, limit: ROOM_PAGE_SIZE },
        signal,
      ),
    initialPageParam: null as number | null,
    getNextPageParam: (page: ChatPage) =>
      page.has_more && page.data.length > 0 ? page.data[0]!.seq : undefined,
    enabled: Boolean(factoryId && thread),
    refetchInterval,
    retry: retryTransient,
  });
}

/** Every loaded message of a thread, oldest first, without repeats. */
export function flattenPages(data: InfiniteData<ChatPage> | undefined): ChatPage["data"] {
  if (!data) return [];
  const seen = new Set<string>();
  const out: ChatPage["data"] = [];
  for (const page of [...data.pages].reverse()) {
    for (const message of page.data) {
      if (seen.has(message.id)) continue;
      seen.add(message.id);
      out.push(message);
    }
  }
  return out.sort((a, b) => a.seq - b.seq);
}

/** The factory's proposals (cards look theirs up by id or ref). */
export function useProposals(factoryId: string | null | undefined, enabled = true) {
  const refetchInterval = useFallbackInterval();
  return useQuery<ProposalsResponse>({
    queryKey: proposalsQueryKey(factoryId ?? ""),
    queryFn: ({ signal }) => fetchProposals(factoryId as string, {}, signal),
    enabled: Boolean(factoryId) && enabled,
    refetchInterval,
    retry: retryTransient,
  });
}

/** One of the four graphs. */
export function useFactoryGraph<K extends GraphKind>(
  factoryId: string | null | undefined,
  kind: K,
  options: { project?: string | null; window?: string | null } = {},
) {
  const refetchInterval = useFallbackInterval();
  const project = options.project ?? null;
  const window = options.window ?? null;
  return useQuery<Extract<GraphResponse, { kind: K }>>({
    queryKey: graphQueryKey(factoryId ?? "", kind, project, window),
    queryFn: ({ signal }) =>
      fetchFactoryGraph(factoryId as string, kind, { project, window }, signal),
    enabled: Boolean(factoryId),
    refetchInterval,
    retry: retryTransient,
  });
}

/** The factory's loops, or null on a server without the loop engine (404). */
export function useRoomLoops(factoryId: string | null | undefined, enabled = true) {
  return useQuery<LoopView[] | null>({
    queryKey: loopsQueryKey(factoryId ?? ""),
    queryFn: async ({ signal }) => {
      try {
        return (await fetchRoomLoops(factoryId as string, signal)).data;
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
    enabled: Boolean(factoryId) && enabled,
    retry: retryTransient,
  });
}

/** The factory's incidents and findings, or null on a server without them. */
export function useRoomIssues(factoryId: string | null | undefined, enabled = true) {
  return useQuery<IssueView[] | null>({
    queryKey: issuesQueryKey(factoryId ?? ""),
    queryFn: async ({ signal }) => {
      try {
        return (await fetchRoomIssues(factoryId as string, signal)).data;
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
    enabled: Boolean(factoryId) && enabled,
    retry: retryTransient,
  });
}

/** Decisions of every factory the viewer reads in `statuses` (the Decisions page's history). */
export function useDecisionsByStatus(statuses: DecisionStatus[], enabled = true) {
  const refetchInterval = useFallbackInterval();
  return useQuery({
    queryKey: [...FACTORY_DECISIONS_QUERY_KEY, "status", statuses.join(",")],
    queryFn: ({ signal }) => fetchDecisionsByStatus(statuses, {}, signal),
    enabled,
    refetchInterval,
    retry: retryTransient,
  });
}

// ── Read marks ─────────────────────────────────────────────────

/** A read mark another tab of this browser moved. */
export interface ReadMark {
  factoryId: string;
  thread: string;
  lastSeq: number;
}

const READS_CHANNEL = "omnigent.factory.room-reads";

/** What the sync needs of a channel (BroadcastChannel, or a test double). */
export interface ReadChannel {
  postMessage: (message: ReadMark) => void;
  addEventListener: (type: "message", listener: (event: MessageEvent<ReadMark>) => void) => void;
  removeEventListener: (type: "message", listener: (event: MessageEvent<ReadMark>) => void) => void;
  close?: () => void;
}

let openChannel: (() => ReadChannel | null) | null = null;

/** Tests swap the channel (two "tabs" share one fake). */
export function setReadChannelFactory(factory: (() => ReadChannel | null) | null): void {
  openChannel = factory;
}

function newChannel(): ReadChannel | null {
  if (openChannel) return openChannel();
  if (typeof BroadcastChannel === "undefined") return null;
  return new BroadcastChannel(READS_CHANNEL) as unknown as ReadChannel;
}

/** Zero a thread's unread count in this client's cache (and anything read up to `lastSeq`). */
export function applyReadMark(queryClient: QueryClient, mark: ReadMark): void {
  queryClient.setQueryData<ThreadsResponse>(threadsQueryKey(mark.factoryId), (prev) =>
    prev
      ? {
          data: prev.data.map((t) =>
            t.key === mark.thread && t.last_seq <= mark.lastSeq ? { ...t, unread: 0 } : t,
          ),
        }
      : prev,
  );
}

/**
 * Mark a thread read up to `lastSeq`: zero its badge here at once, tell this
 * browser's other tabs, and move the server's mark (other devices see it on
 * their next read).
 */
export async function markThreadRead(
  queryClient: QueryClient,
  mark: ReadMark,
  channel: ReadChannel | null = null,
): Promise<void> {
  applyReadMark(queryClient, mark);
  channel?.postMessage(mark);
  try {
    await putRoomRead(mark.factoryId, mark.thread, mark.lastSeq);
  } catch {
    // The badge comes back on the next read; nothing else depends on the mark.
  }
}

/**
 * Keep this tab's badges in step with marks other tabs move; returns the
 * channel to post this tab's marks on.
 */
export function useReadMarkSync(): ReadChannel | null {
  const queryClient = useQueryClient();
  const channel = useMemo(() => newChannel(), []);
  useEffect(() => {
    if (!channel) return;
    const onMessage = (event: MessageEvent<ReadMark>) => {
      if (event.data?.factoryId && event.data.thread) applyReadMark(queryClient, event.data);
    };
    channel.addEventListener("message", onMessage);
    return () => {
      channel.removeEventListener("message", onMessage);
      channel.close?.();
    };
  }, [channel, queryClient]);
  return channel;
}

/**
 * While `active` (the thread is on screen and the tab visible), mark it read
 * up to its newest message whenever one arrives.
 */
export function useMarkThreadRead(
  factoryId: string,
  thread: string | null,
  newestSeq: number,
  active: boolean,
  channel: ReadChannel | null,
): void {
  const queryClient = useQueryClient();
  const threads = queryClient.getQueryData<ThreadsResponse>(threadsQueryKey(factoryId));
  const unread = threads?.data.find((t) => t.key === thread)?.unread ?? 0;
  useEffect(() => {
    if (!active || !thread || newestSeq <= 0) return;
    if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
    void markThreadRead(queryClient, { factoryId, thread, lastSeq: newestSeq }, channel);
    // `unread` re-runs the mark when a refetch brings the badge back.
  }, [active, thread, newestSeq, factoryId, queryClient, channel, unread]);
}
