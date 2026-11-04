// Folding Room noise without hiding it: a run of "base moved" lines reads as
// one line (each move one click away), and an incident shows one live card per
// thread; its older cards (posted again before the server kept one live card)
// read as one quiet line just above it. Tests pin it.

import type { BaseMoveView, ChatMessageView, FactorySnapshot } from "@/lib/factoryApi";
import type { RoomItem } from "./roomModel";

/** The moves of one base that a Room row (or a run of rows) shows. */
export interface BaseMoves {
  base: string;
  /** `owner/name` of a batched line; null for an older server's one-move line. */
  repo: string | null;
  moves: BaseMoveView[];
}

/** A run of base lines takes no line whose last move is this long after the run's first. */
export const BASE_RUN_SPAN_S = 12 * 3600;

/** What servers before batching wrote per move: `base main moved 7f3e2a1 → 0c8a4e1 (#9630)`. */
const ONE_MOVE = /^base (\S+) moved ([0-9a-f]{7,40}) → ([0-9a-f]{7,40})(?: \(#(\d+)\))?$/;

/** A system row's base moves: a batched line's meta, or an older one-move line. */
export function baseMovesOf(message: ChatMessageView): BaseMoves | null {
  if (message.author_kind !== "system") return null;
  const meta = message.card?.meta;
  if (meta?.kind === "base.moved") {
    return meta.moves.length > 0
      ? { base: meta.base, repo: meta.repo || null, moves: meta.moves }
      : null;
  }
  if (message.card && message.card.type !== "system") return null;
  const match = message.body.trim().match(ONE_MOVE);
  if (!match) return null;
  return {
    base: match[1]!,
    repo: null,
    moves: [
      {
        at: message.created_at,
        from_sha: match[2]!,
        to_sha: match[3]!,
        pr: match[4] ? Number(match[4]) : null,
      },
    ],
  };
}

function issueKey(message: ChatMessageView): string | null {
  if (message.author_kind !== "system" || message.card?.type !== "issue") return null;
  return message.card.id ?? message.card.ref;
}

/** Issue cards a newer card of the same incident in `messages` replaces (their ids). */
export function supersededIssueCards(messages: readonly ChatMessageView[]): Set<string> {
  const newest = new Map<string, ChatMessageView>();
  for (const message of messages) {
    const key = issueKey(message);
    const seen = key ? newest.get(key) : undefined;
    if (key && (!seen || seen.seq < message.seq)) newest.set(key, message);
  }
  const out = new Set<string>();
  for (const message of messages) {
    const key = issueKey(message);
    if (key && newest.get(key)?.id !== message.id) out.add(message.id);
  }
  return out;
}

/**
 * System cards of `types` that a newer card of the same object in `messages`
 * repeats (their ids): a proposal filed and then applied is one live card.
 */
export function supersededCards(
  messages: readonly ChatMessageView[],
  types: readonly string[],
): Set<string> {
  const keyOf = (m: ChatMessageView) =>
    m.author_kind === "system" && m.card && types.includes(m.card.type)
      ? `${m.card.type}:${m.card.id ?? m.card.ref ?? ""}`
      : null;
  const newest = new Map<string, ChatMessageView>();
  for (const message of messages) {
    const key = keyOf(message);
    const seen = key ? newest.get(key) : undefined;
    if (key && (!seen || seen.seq < message.seq)) newest.set(key, message);
  }
  const out = new Set<string>();
  for (const message of messages) {
    const key = keyOf(message);
    if (key && newest.get(key)?.id !== message.id) out.add(message.id);
  }
  return out;
}

interface BaseItem {
  type: "base";
  key: string;
  at: number;
  base: string;
  repo: string | null;
  moves: BaseMoveView[];
  messages: ChatMessageView[];
}

interface RepeatsItem {
  type: "repeats";
  key: string;
  at: number;
  /** `INC-12`, or the issue id when the card has no ref. */
  ref: string;
  messages: ChatMessageView[];
}

export type FoldedItem = RoomItem | BaseItem | RepeatsItem;

function runKey(moves: BaseMoves): string {
  return `${moves.base}\u0000${moves.repo?.toLowerCase() ?? ""}`;
}

/** Whether `next` may join `run`: its last move within {@link BASE_RUN_SPAN_S} of the run's first. */
function inSpan(run: BaseItem, next: BaseMoves): boolean {
  const first = run.moves[0]?.at ?? 0;
  const last = next.moves[next.moves.length - 1]?.at ?? first;
  return last - first < BASE_RUN_SPAN_S;
}

function repeatsOf(copies: ChatMessageView[]): RepeatsItem {
  const first = copies[0]!;
  return {
    type: "repeats",
    key: `repeats:${first.id}`,
    at: first.created_at * 1000,
    ref: first.card?.ref ?? first.card?.id ?? "",
    messages: copies,
  };
}

/**
 * The thread's items with the noise folded.
 *
 * - An incident's older cards (`superseded`), wherever they sit in the loaded
 *   thread, read as one `repeats` item just above its live card.
 * - Base lines of one base and repo merge into one `base` item (a batched
 *   line is one already) at the place of the run's first line. A run goes on
 *   across cards and other system lines and other bases' lines; a person's or
 *   an agent's message or a bus line ends it, and so does a move more than
 *   {@link BASE_RUN_SPAN_S} after the run's first.
 */
export function foldRoomItems(
  items: readonly RoomItem[],
  superseded: ReadonlySet<string>,
): FoldedItem[] {
  const copies = new Map<string, ChatMessageView[]>();
  const live = new Set<string>();
  for (const item of items) {
    if (item.type !== "message") continue;
    const key = issueKey(item.message);
    if (!key) continue;
    if (!superseded.has(item.message.id)) live.add(key);
    else copies.set(key, [...(copies.get(key) ?? []), item.message]);
  }
  const out: FoldedItem[] = [];
  const runs = new Map<string, BaseItem>();
  for (const item of items) {
    if (item.type !== "message") {
      runs.clear();
      out.push(item);
      continue;
    }
    const message = item.message;
    const key = issueKey(message);
    if (key && superseded.has(message.id)) {
      // Shown above the live card; without one loaded, where the last copy was.
      const older = copies.get(key)!;
      if (!live.has(key) && older[older.length - 1] === message) out.push(repeatsOf(older));
      continue;
    }
    const moves = baseMovesOf(message);
    if (moves) {
      const run = runs.get(runKey(moves));
      if (run && inSpan(run, moves)) {
        run.moves.push(...moves.moves);
        run.messages.push(message);
      } else {
        const fresh: BaseItem = {
          type: "base",
          key: `base:${message.id}`,
          at: item.at,
          base: moves.base,
          repo: moves.repo,
          moves: [...moves.moves],
          messages: [message],
        };
        out.push(fresh);
        runs.set(runKey(moves), fresh);
      }
      continue;
    }
    if (message.author_kind !== "system") runs.clear();
    const older = key ? copies.get(key) : undefined;
    if (older) out.push(repeatsOf(older));
    out.push(item);
  }
  return out;
}

/**
 * Whether base lines name their repo: the factory watches (or the thread
 * shows) more than one.
 */
export function namesRepos(snapshot: FactorySnapshot, items: readonly FoldedItem[]): boolean {
  const repos = new Set<string>();
  const add = (repo: string | null | undefined) => {
    if (repo) repos.add(repo.toLowerCase());
  };
  add(snapshot.factory.base?.repo);
  for (const project of snapshot.projects) add(project.repo);
  for (const item of items) if (item.type === "base") add(item.repo);
  return repos.size > 1;
}

/** `sample-infra` for `sample-org/sample-infra`. */
export function repoName(repo: string): string {
  return repo.slice(repo.lastIndexOf("/") + 1);
}
