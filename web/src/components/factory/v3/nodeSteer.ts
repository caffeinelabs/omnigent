// Steering at the node through TanStack Query: a verb's preview (read again as
// its arguments change, the last answer kept meanwhile), its apply (every node
// page of the factory reads again, the toast offers Undo; a refused one reads
// them again too, so the preview is the server's current one), the latest
// Undo per node for its whole window (the `steer/UndoLatest` pattern: "Undo
// (8 min left)" outlives the toast; a definition version's only while it is
// in force), Needs you, Find, and the last-look mark sent after three seconds
// on a page.

import {
  type QueryClient,
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import {
  type DefinitionVersionsResponse,
  type DefinitionView,
  fetchDefinitionVersions,
} from "@/lib/factoryApi";
import { learnKey, useDefinitionVersions, usePinDefinition } from "../insights/learnQueries";
import { type FactoryShellValue, useOptionalFactoryShell } from "../shell/FactoryShellContext";
import { UNDO_WINDOW_S, undoLeft, versionRows } from "../steer/steerModel";
import { useDebounced } from "../team/teamQueries";
import { useFactoryToast } from "../ui/Toast";
import { useNodeActions } from "./NodeContext";
import { NODE_STALE_MS, nodeQueryKeys, useAnswerDecision } from "./nodeQueries";
import { answerProposal, useNodeSource } from "./nodeSource";
import type {
  DecisionAction,
  DecisionRef,
  NodePage,
  NodeSummary,
  Rollup,
  VerbPreview,
  VerbRequest,
} from "./nodeTypes";

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

/**
 * The server's dry run of `verb` on this page's node; nothing changes. Off
 * (`enabled: false`) while the form cannot be previewed yet (no title, no
 * pick), so a blocked form never asks the server.
 */
export function useVerbPreview(
  page: NodePage,
  verb: string,
  args: Args,
  { enabled = true, factoryId }: { enabled?: boolean; factoryId?: string } = {},
) {
  const fid = useFid(factoryId);
  const source = useNodeSource();
  // A blocked form wants no preview (null); what is asked is what settled, so a
  // form unblocked by its first keystroke never sends the blocked arguments.
  const wanted = enabled ? JSON.stringify(args) : null;
  const settled = useDebounced(wanted, PREVIEW_DEBOUNCE_MS);
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
        { verb, args: JSON.parse(settled!) as Args, based_on: page.version },
        signal,
      ),
    enabled: Boolean(wanted != null && settled != null && source?.preview && fid),
    placeholderData: keepPreviousData,
    staleTime: 0,
    retry: false,
  });
  return { ...query, settled: settled === wanted, supported: Boolean(source?.preview) };
}

// ── The latest Undo per node, for its whole window ─────────────

/** A definition version an Undo takes back by pinning the one it was made from. */
export interface VersionUndo {
  /** The version the apply made. */
  version: number;
  /** The version it was made from: Undo pins it again. */
  parent: number;
}

/** An applied verb that can still be undone. */
export interface NodeUndo {
  /** "Start now", "Team". */
  label: string;
  /** Epoch ms after which it cannot be undone (a version's own time wins, see `version`). */
  expiresAt: number;
  run: () => Promise<unknown>;
  /** The server's Undo token (`POST …/undo/{token}`), when it has one: its card in the thread carries it too. */
  token?: string;
  /**
   * It re-pins a definition version: offered only while `version.version` is
   * the version in force and was made from `version.parent`, for the window
   * counted from that version's time (the server's rule, `undoLeft`).
   */
  version?: VersionUndo;
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

/** Forget the Undo whose server token is `token` (it ran from its card in the thread). */
export function forgetUndoToken(fid: string, token: string): void {
  for (const [key, undo] of undos) {
    if (key.startsWith(`${fid}/`) && undo.token === token) undos.delete(key);
  }
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

/** "Now" on the server's clock, in epoch seconds (the shell's offset; none without a shell). */
export function serverNowS(shell: Pick<FactoryShellValue, "clockOffsetS"> | null): number {
  return Math.floor(Date.now() / 1000 + (shell?.clockOffsetS ?? 0));
}

/** Who undoes, as the server's Undo rule reads them (no viewer block: every lever open). */
export function undoViewer(shell: Pick<FactoryShellValue, "snapshot"> | null): {
  userId: string | null | undefined;
  owner: boolean;
} {
  const viewer = shell?.snapshot.viewer ?? null;
  return { userId: viewer?.user_id, owner: viewer ? viewer.can.settings !== false : true };
}

/**
 * Seconds left to undo `undo` (re-pin its parent) from the versions as the
 * server lists them: null once it is no longer the version in force, was
 * made from another version, was undone, or its window closed; undefined
 * while it is not listed (yet).
 */
export function versionUndoLeft(
  versions: readonly DefinitionView[] | undefined,
  undo: VersionUndo,
  nowS: number,
  viewer: { userId: string | null | undefined; owner: boolean },
): number | null | undefined {
  const row = versionRows(versions ?? []).find((r) => r.version === undo.version);
  if (!row) return undefined;
  if (row.parent !== undo.parent) return null;
  return undoLeft(row, nowS, viewer);
}

/** The definition's versions, read now (an Undo checks its version is still in force). */
export function readDefinitionVersions(
  client: QueryClient,
  factoryId: string,
): Promise<DefinitionVersionsResponse> {
  return client.fetchQuery({
    queryKey: learnKey(factoryId, "definition-versions"),
    queryFn: ({ signal }) => fetchDefinitionVersions(factoryId, signal),
    staleTime: 0,
  });
}

/** The Undo still open on this node, with the seconds left (ticks every 15 s). */
export function useNodeUndo(ref: string, factoryId?: string) {
  const fid = useFid(factoryId);
  const shell = useOptionalFactoryShell();
  const undo = useSyncExternalStore(
    subscribe,
    () => undos.get(undoKey(fid, ref)) ?? null,
    () => null,
  );
  const pinned = undo?.version ?? null;
  const versions = useDefinitionVersions(fid, Boolean(pinned));
  // Ticks while one is open, so "n min left" counts down and the button goes at 0.
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!undo) return;
    const timer = setInterval(() => setTick((t) => t + 1), 15_000);
    return () => clearInterval(timer);
  }, [undo]);
  const own = undo ? Math.floor((undo.expiresAt - Date.now()) / 1000) : 0;
  // A version's Undo goes by the versions: once they are read, only the version
  // in force has one (its window from its own time); while they are read again
  // after the apply, or cannot be read, its own window stands in (Undo itself
  // reads them again first).
  const listed = pinned
    ? versionUndoLeft(versions.data?.data, pinned, serverNowS(shell), undoViewer(shell))
    : undefined;
  const known = versions.data != null && !versions.isFetching;
  const left = !pinned ? own : listed !== undefined ? listed : known ? null : own;
  const gone = Boolean(undo && pinned && known && (left == null || left <= 0));
  useEffect(() => {
    if (gone && undo) forgetUndo(fid, ref, undo);
  }, [fid, gone, ref, undo]);
  return undo && left != null && left > 0 ? { undo, left } : null;
}

/**
 * The Undo of a definition version an apply made on `ref` (Team, Rename
 * levels): it re-pins `parent` only while `version` is still the version in
 * force (read again first), so a version someone landed after it is never
 * dropped with it. `undone` is the toast after it ran.
 */
export function useVersionUndo(ref: string, factoryId?: string) {
  const fid = useFid(factoryId);
  const client = useQueryClient();
  const shell = useOptionalFactoryShell();
  const toast = useFactoryToast();
  const { mutateAsync: pin } = usePinDefinition(fid);
  const refresh = useRefreshNodes(fid);
  return useCallback(
    (label: string, version: number, parent: number, undone: string): NodeUndo => {
      const entry: NodeUndo = {
        label,
        expiresAt: Date.now() + UNDO_WINDOW_S * 1000,
        version: { version, parent },
        run: async () => {
          const versions = await readDefinitionVersions(client, fid).catch(() => null);
          if (!versions) {
            toast.show({
              message: "Could not undo: the definition's versions could not be read.",
            });
            return;
          }
          const left = versionUndoLeft(
            versions.data,
            entry.version!,
            serverNowS(shell),
            undoViewer(shell),
          );
          if (left == null) {
            forgetUndo(fid, ref, entry);
            toast.show({ message: `v${version} is no longer in force; undo from Versions.` });
            return;
          }
          try {
            await pin(parent);
          } catch (error) {
            toast.show({
              message: `Could not undo: ${error instanceof Error ? error.message : String(error)}`,
            });
            return;
          }
          forgetUndo(fid, ref, entry);
          void refresh();
          toast.show({ message: undone });
        },
      };
      return entry;
    },
    [client, fid, pin, ref, refresh, shell, toast],
  );
}

/** Words for why something failed. */
function errorWords(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
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
      const said = (applied.message ?? `${label}: applied`).trim();
      const filed =
        applied.filed && !said.includes(applied.filed) ? ` Filed ${applied.filed}.` : "";
      const message = `${/[.!?]$/.test(said) ? said : `${said}.`}${filed}`;
      if (!token || !undoFn) {
        toast.show({ message });
        return;
      }
      const expires = Date.parse(applied.undo!.expires_at);
      const entry: NodeUndo = {
        label,
        token,
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
    onError: (error) => {
      toast.show({ message: refusal(error) });
      // A refused write (409 stale, 422, 403) reads the page and its preview
      // again: the panel shows what applies now, and Apply works once it settles.
      void refresh();
    },
  });
}

/**
 * Undo by the server's token, from a verb's card in its node's thread (the
 * same Undo as the toast's and "Undo (n min left)", which it also closes).
 */
export function useUndoToken(factoryId?: string) {
  const fid = useFid(factoryId);
  const source = useNodeSource();
  const toast = useFactoryToast();
  const refresh = useRefreshNodes(fid);
  const undo = source?.undo;
  return useMemo(
    () =>
      undo
        ? async (token: string, label: string) => {
            try {
              await undo(fid, token);
            } catch (error) {
              void refresh();
              toast.show({ message: `Could not undo: ${errorWords(error)}` });
              return;
            }
            forgetUndoToken(fid, token);
            void refresh();
            toast.show({ message: `Undone: ${label}` });
          }
        : null,
    [fid, refresh, toast, undo],
  );
}

// ── Answers, with Undo when they applied a proposal ────────────

/**
 * Answer an ask (always a click). An answer that applied a proposal ("Apply
 * C-16") can be taken back with the proposal's own Undo for 10 minutes: the
 * toast offers it, and the node it was answered on (`at`) keeps "Apply C-16:
 * Undo (n min left)" beside its verbs. Every node page reads again after
 * either. `sample`: the toast says only the sample changed.
 */
export function useAnswerAsk(
  at: string | null,
  { sample = false, factoryId }: { sample?: boolean; factoryId?: string } = {},
) {
  const fid = useFid(factoryId);
  const source = useNodeSource();
  const toast = useFactoryToast();
  const refresh = useRefreshNodes(fid);
  const { mutate } = useAnswerDecision(fid);
  return useCallback(
    (decision: DecisionRef, action: DecisionAction) =>
      mutate(
        { decision, action },
        {
          onSuccess: () => {
            const message = sample
              ? `Sample data: nothing was answered. The sample shows what ${decision.id}: ${action.label} would do.`
              : `${decision.id}: ${action.label}`;
            const proposal = answerProposal(decision, action);
            const back = source?.undoAnswer;
            if (!proposal || !back) {
              toast.show({ message });
              return;
            }
            const entry: NodeUndo = {
              label: `${action.label} ${proposal}`,
              expiresAt: Date.now() + UNDO_WINDOW_S * 1000,
              run: () =>
                back(fid, decision, action).then(
                  () => {
                    if (at) forgetUndo(fid, at, entry);
                    void refresh();
                    toast.show({
                      message: `Undone: ${action.label} ${proposal} (${decision.id}).`,
                    });
                  },
                  (error: unknown) => {
                    // Its window closed, or what it changed moved on: it stays applied.
                    if (at) forgetUndo(fid, at, entry);
                    void refresh();
                    toast.show({ message: `Could not undo: ${errorWords(error)}` });
                  },
                ),
            };
            if (at) rememberUndo(fid, at, entry);
            toast.show({ message, undo: () => void entry.run() });
          },
          onError: (error) =>
            toast.show({ message: `${decision.id} was not answered: ${errorWords(error)}` }),
        },
      ),
    [at, fid, mutate, refresh, sample, source?.undoAnswer, toast],
  );
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

/** What "since your last look" said when this visit to a node began. */
interface VisitSince {
  ref: string;
  since?: Rollup["since"];
  /** Each child's changes, by ref. */
  changed: ReadonlyMap<string, string[]>;
}

function visitOf(page: NodePage): VisitSince {
  const changed = new Map<string, string[]>();
  const kids = page.children;
  for (const k of [...kids.items, ...kids.later, ...kids.ghosts]) {
    if (k.changed?.length) changed.set(k.ref, k.changed);
  }
  return { ref: page.node.ref, since: page.rollup.since, changed };
}

function union(a: readonly string[], b: readonly string[] | undefined): string[] {
  return [...new Set([...a, ...(b ?? [])])];
}

/** `page` with what this visit first showed "since" kept (a later read counts from the new mark). */
export function withVisitSince(page: NodePage, visit: VisitSince): NodePage {
  if (visit.ref !== page.node.ref || (!visit.since && !visit.changed.size)) return page;
  const now = page.rollup.since;
  const since = visit.since
    ? {
        at: visit.since.at,
        items: union(visit.since.items, now?.items),
        more: Math.max(visit.since.more, now?.more ?? 0),
      }
    : now;
  const keep = (k: NodeSummary): NodeSummary => {
    const was = visit.changed.get(k.ref);
    return was ? { ...k, changed: union(was, k.changed) } : k;
  };
  const kids = page.children;
  return {
    ...page,
    rollup: { ...page.rollup, ...(since ? { since } : {}) },
    children: {
      ...kids,
      items: kids.items.map(keep),
      later: kids.later.map(keep),
      ghosts: kids.ghosts.map(keep),
    },
  };
}

/**
 * The page as this visit shows it: the "since" line and each child's changes
 * from its first read stay for the whole visit, though the mark moved after
 * three seconds and an apply, an answer or an Undo reads the page again. A
 * visit to another node starts over.
 */
export function useVisitSince(page: NodePage): NodePage {
  const [visit, setVisit] = useState(() => visitOf(page));
  let shown = visit;
  if (visit.ref !== page.node.ref) {
    shown = visitOf(page);
    setVisit(shown);
  }
  return useMemo(() => withVisitSince(page, shown), [page, shown]);
}

/**
 * Marks this person's last look at `ref` once the page has been in view for
 * three seconds; the "since" lines it shows stay for this visit
 * (`useVisitSince`), and the next visit starts from the new mark.
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
