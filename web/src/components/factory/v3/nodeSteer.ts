// Steering at the node through TanStack Query: a verb's preview (read again as
// its arguments change, the last answer kept meanwhile), its apply (every node
// page of the factory reads again, the toast offers Undo), the latest Undo
// per node for its whole window (the `steer/UndoLatest` pattern: "Undo (8 min
// left)" outlives the toast), Needs you, Find, and the last-look mark sent
// after three seconds on a page.

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useOptionalFactoryShell } from "../shell/FactoryShellContext";
import { UNDO_WINDOW_S } from "../steer/steerModel";
import { useDebounced } from "../team/teamQueries";
import { useFactoryToast } from "../ui/Toast";
import { useNodeActions } from "./NodeContext";
import { NODE_STALE_MS, nodeQueryKeys } from "./nodeQueries";
import { useNodeSource } from "./nodeSource";
import type { NodePage, VerbPreview, VerbRequest } from "./nodeTypes";

/** Typing in a preview's form reads the preview again once it pauses. */
export const PREVIEW_DEBOUNCE_MS = 250;

/** A page counts as looked at after this long in view. */
export const SEEN_AFTER_MS = 3_000;

/** The factory: the one named, else the page's, else the shell's. */
function useFid(factoryId?: string): string {
  const shell = useOptionalFactoryShell();
  const page = useNodeActions().factoryId;
  return factoryId || page || shell?.factoryId || "";
}

type Args = VerbRequest["args"];

/** The server's dry run of `verb` on this page's node; nothing changes. */
export function useVerbPreview(page: NodePage, verb: string, args: Args, factoryId?: string) {
  const fid = useFid(factoryId);
  const source = useNodeSource();
  const body = JSON.stringify(args);
  const settled = useDebounced(body, PREVIEW_DEBOUNCE_MS);
  const query = useQuery<VerbPreview>({
    queryKey: [
      ...nodeQueryKeys.node(fid, source?.kind ?? "pending", page.node.ref),
      "preview",
      verb,
      settled,
      page.version,
    ],
    queryFn: ({ signal }) =>
      source!.preview!(
        fid,
        page.node.ref,
        { verb, args: JSON.parse(settled) as Args, based_on: page.version },
        signal,
      ),
    enabled: Boolean(source?.preview && fid),
    placeholderData: keepPreviousData,
    staleTime: 0,
    retry: false,
  });
  return { ...query, settled: settled === body, supported: Boolean(source?.preview) };
}

// ── The latest Undo per node, for its whole window ─────────────

/** An applied verb that can still be undone. */
export interface NodeUndo {
  /** "Start now", "Team". */
  label: string;
  /** Epoch ms after which it cannot be undone. */
  expiresAt: number;
  run: () => Promise<unknown>;
}

const undos = new Map<string, NodeUndo>();
const listeners = new Set<() => void>();

function undoKey(fid: string, ref: string): string {
  return `${fid}/${ref}`;
}

function notify() {
  for (const listener of listeners) listener();
}

/** Remember the Undo of what was just applied on `ref` (it replaces an older one). */
export function rememberUndo(fid: string, ref: string, undo: NodeUndo): void {
  undos.set(undoKey(fid, ref), undo);
  notify();
}

/** Forget it (it ran, or its window closed). */
export function forgetUndo(fid: string, ref: string, undo?: NodeUndo): void {
  const key = undoKey(fid, ref);
  if (undo && undos.get(key) !== undo) return;
  undos.delete(key);
  notify();
}

/** Forget every Undo (tests). */
export function resetNodeUndos(): void {
  undos.clear();
  notify();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The Undo still open on this node, with the seconds left (ticks every 15 s). */
export function useNodeUndo(ref: string, factoryId?: string) {
  const fid = useFid(factoryId);
  const undo = useSyncExternalStore(
    subscribe,
    () => undos.get(undoKey(fid, ref)) ?? null,
    () => null,
  );
  // Ticks while one is open, so "n min left" counts down and the button goes at 0.
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!undo) return;
    const timer = setInterval(() => setTick((t) => t + 1), 15_000);
    return () => clearInterval(timer);
  }, [undo]);
  const left = undo ? Math.floor((undo.expiresAt - Date.now()) / 1000) : 0;
  return undo && left > 0 ? { undo, left } : null;
}

/** "Not applied: …", from a refused write (409 stale, 422 wrong level, 403 with who may). */
export function refusal(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  const status = (error as { status?: number } | null)?.status;
  if (status === 409) return `Not applied: someone changed this first. ${message}`;
  return `Not applied: ${message}`;
}

/** Every node page of the factory reads again (an apply or an Undo changed the facts). */
export function useRefreshNodes(factoryId?: string): () => Promise<void> {
  const fid = useFid(factoryId);
  const client = useQueryClient();
  return useCallback(
    () => client.invalidateQueries({ queryKey: nodeQueryKeys.all(fid) }),
    [client, fid],
  );
}

/**
 * Apply a preview. The toast says what happened and offers Undo (12 s, or `u`);
 * the node keeps "Undo (n min left)" for the rest of its window.
 */
export function useApplyVerb(page: NodePage, factoryId?: string) {
  const fid = useFid(factoryId);
  const source = useNodeSource();
  const toast = useFactoryToast();
  const refresh = useRefreshNodes(fid);
  const ref = page.node.ref;
  return useMutation({
    mutationFn: async ({
      verb,
      label,
      args,
      preview,
    }: {
      verb: string;
      label: string;
      args: Args;
      preview: VerbPreview;
    }) => {
      if (!source?.apply) throw new Error("This server cannot apply this verb yet.");
      const applied = await source.apply(fid, ref, {
        verb,
        args,
        based_on: page.version,
        preview_id: preview.preview_id,
      });
      return { applied, label };
    },
    onSuccess: ({ applied, label }) => {
      void refresh();
      const token = applied.undo?.token;
      const undoFn = source?.undo;
      const message = [
        applied.message ?? `${label}: applied`,
        applied.filed && `Filed ${applied.filed}.`,
      ]
        .filter(Boolean)
        .join(". ");
      if (!token || !undoFn) {
        toast.show({ message });
        return;
      }
      const expires = Date.parse(applied.undo!.expires_at);
      const entry: NodeUndo = {
        label,
        expiresAt: Number.isFinite(expires) ? expires : Date.now() + UNDO_WINDOW_S * 1000,
        run: () =>
          undoFn(fid, token).then(
            () => {
              forgetUndo(fid, ref, entry);
              void refresh();
              toast.show({ message: `Undone: ${label}.` });
            },
            (error: unknown) =>
              toast.show({
                message: `Could not undo: ${error instanceof Error ? error.message : String(error)}`,
              }),
          ),
      };
      rememberUndo(fid, ref, entry);
      toast.show({ message, undo: () => void entry.run() });
    },
    onError: (error) => toast.show({ message: refusal(error) }),
  });
}

// ── Needs you, Find, last look ─────────────────────────────────

/** Every open ask in a tree, ranked (null data: this source has no Needs you route). */
export function useNeedsYou(tree: string, enabled = true, factoryId?: string) {
  const fid = useFid(factoryId);
  const source = useNodeSource();
  return useQuery({
    queryKey: [...nodeQueryKeys.all(fid), source?.kind ?? "pending", "needs-you", tree],
    queryFn: ({ signal }) => source!.needsYou!(fid, tree, signal),
    enabled: Boolean(enabled && fid && source?.needsYou),
    staleTime: NODE_STALE_MS,
  });
}

/** Nodes whose ref or title holds `q`, once typing pauses (off without a Find route). */
export function useNodeFind(q: string, enabled = true, factoryId?: string) {
  const fid = useFid(factoryId);
  const source = useNodeSource();
  const settled = useDebounced(q.trim(), PREVIEW_DEBOUNCE_MS);
  const query = useQuery({
    queryKey: [...nodeQueryKeys.all(fid), source?.kind ?? "pending", "find", settled],
    queryFn: ({ signal }) => source!.find!(fid, settled, signal),
    enabled: Boolean(enabled && fid && settled && source?.find),
    staleTime: NODE_STALE_MS,
    placeholderData: keepPreviousData,
  });
  return { ...query, supported: Boolean(source?.find) };
}

/**
 * Marks this person's last look at `ref` once the page has been in view for
 * three seconds; the "since" lines it shows stay until the next visit.
 */
export function useMarkSeen(ref: string, factoryId?: string): void {
  const fid = useFid(factoryId);
  const source = useNodeSource();
  const seen = source?.seen;
  useEffect(() => {
    if (!seen || !fid || !ref) return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let done = false;
    const arm = () => {
      if (done || timer || document.visibilityState === "hidden") return;
      timer = setTimeout(() => {
        done = true;
        void seen(fid, ref).catch(() => undefined);
      }, SEEN_AFTER_MS);
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden" && timer) {
        clearTimeout(timer);
        timer = null;
      } else arm();
    };
    arm();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [fid, ref, seen]);
}
