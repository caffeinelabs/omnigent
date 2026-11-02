// Folding Room noise without hiding it: a run of "base moved" lines reads as
// one line (each move one click away), and an incident shows one live card per
// thread; its older cards (posted again before the server kept one live card)
// read as one quiet line. Tests pin it.

import type { BaseMoveView, ChatMessageView } from "@/lib/factoryApi";
import type { RoomItem } from "./roomModel";

/** The moves of one base that a Room row (or a run of rows) shows. */
export interface BaseMoves {
  base: string;
  moves: BaseMoveView[];
}

/** What servers before batching wrote per move: `base main moved 7f3e2a1 → 0c8a4e1 (#9630)`. */
const ONE_MOVE = /^base (\S+) moved ([0-9a-f]{7,40}) → ([0-9a-f]{7,40})(?: \(#(\d+)\))?$/;

/** A system row's base moves: a batched line's meta, or an older one-move line. */
export function baseMovesOf(message: ChatMessageView): BaseMoves | null {
  if (message.author_kind !== "system") return null;
  const meta = message.card?.meta;
  if (meta?.kind === "base.moved") {
    return meta.moves.length > 0 ? { base: meta.base, moves: meta.moves } : null;
  }
  if (message.card && message.card.type !== "system") return null;
  const match = message.body.trim().match(ONE_MOVE);
  if (!match) return null;
  return {
    base: match[1]!,
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

export type FoldedItem =
  | RoomItem
  | {
      type: "base";
      key: string;
      at: number;
      base: string;
      moves: BaseMoveView[];
      messages: ChatMessageView[];
    }
  | {
      type: "repeats";
      key: string;
      at: number;
      /** `INC-12`, or the issue id when the card has no ref. */
      ref: string;
      messages: ChatMessageView[];
    };

/**
 * The thread's items with the noise folded: consecutive base lines of one
 * base merge into one `base` item (a batched line is one already), and
 * consecutive older cards of one incident into one `repeats` item. Anything
 * else between them (a message, a bus line) keeps them apart.
 */
export function foldRoomItems(
  items: readonly RoomItem[],
  superseded: ReadonlySet<string>,
): FoldedItem[] {
  const out: FoldedItem[] = [];
  for (const item of items) {
    const prev = out[out.length - 1];
    if (item.type !== "message") {
      out.push(item);
      continue;
    }
    const message = item.message;
    const moves = baseMovesOf(message);
    if (moves) {
      if (prev?.type === "base" && prev.base === moves.base) {
        prev.moves.push(...moves.moves);
        prev.messages.push(message);
      } else {
        out.push({
          type: "base",
          key: `base:${message.id}`,
          at: item.at,
          base: moves.base,
          moves: [...moves.moves],
          messages: [message],
        });
      }
      continue;
    }
    if (superseded.has(message.id)) {
      const ref = message.card?.ref ?? message.card?.id ?? "";
      if (prev?.type === "repeats" && prev.ref === ref) {
        prev.messages.push(message);
      } else {
        out.push({
          type: "repeats",
          key: `repeats:${message.id}`,
          at: item.at,
          ref,
          messages: [message],
        });
      }
      continue;
    }
    out.push(item);
  }
  return out;
}
