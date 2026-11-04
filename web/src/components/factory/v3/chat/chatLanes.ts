// The node chat is a conversation (calm rule 5): people's and agents'
// messages, plus live cards that need this viewer. System events (base moved,
// a member spawned, incidents, resolved or closed items, proposal diffs) fold
// into one "Activity" line. An ask the page's Your move already shows is not
// repeated here at all. The server marks each message's lane when it can
// (`lane`); older servers' messages are sorted here.

import type {
  ChatMessageView,
  DecisionView,
  PreviewAppliedMeta,
  ProposalView,
} from "@/lib/factoryApi";
import type { FoldedItem } from "../../room/roomFold";

export type Lane = "conversation" | "activity" | "hidden";

export interface LaneContext {
  /** Keys of the asks Your move shows (`D-45`, a row id, `C-16`). */
  asked: ReadonlySet<string>;
  decisions: readonly DecisionView[];
  proposals: readonly ProposalView[] | null;
  /** The server's clock, epoch seconds (an Undo's window). */
  nowS: number;
  /** Undo tokens a later card says were used. */
  undone?: ReadonlySet<string>;
}

/** The Undo tokens a thread's cards say were used ("Undone by …"). */
export function undoneTokens(messages: readonly ChatMessageView[]): Set<string> {
  const out = new Set<string>();
  for (const m of messages) {
    const meta = m.card?.type === "preview_applied" ? (m.card.meta as PreviewAppliedMeta) : null;
    if (meta?.undone && meta.undo_token) out.add(meta.undo_token);
  }
  return out;
}

const OPEN_PROPOSAL: ReadonlySet<string> = new Set(["pending", "awaiting_cosign"]);

function decisionOf(ctx: LaneContext, key: string | null): DecisionView | undefined {
  return key ? ctx.decisions.find((d) => d.id === key || d.ref === key) : undefined;
}

function proposalOf(ctx: LaneContext, id: string | null, ref: string | null) {
  return ctx.proposals?.find((p) => (id && p.id === id) || (ref && p.ref === ref));
}

/** The keys a card goes by: its ref, its id, and the decision or proposal behind it. */
function cardKeys(message: ChatMessageView, ctx: LaneContext): string[] {
  const card = message.card;
  if (!card) return [];
  const keys = [card.ref, card.id];
  if (card.type === "proposal") {
    const p = proposalOf(ctx, card.id, card.ref);
    keys.push(p?.ref ?? null, p?.decision_id ?? null, p?.decision_ref ?? null);
  }
  if (card.type === "decision" || card.type === "loop") {
    const d = decisionOf(ctx, card.id) ?? decisionOf(ctx, card.ref);
    keys.push(d?.id ?? null, d?.ref ?? null);
  }
  return keys.filter((k): k is string => Boolean(k));
}

/** A card shows an ask Your move already shows (never repeated in the chat). */
export function repeatsAsk(message: ChatMessageView, ctx: LaneContext): boolean {
  return cardKeys(message, ctx).some((k) => ctx.asked.has(k));
}

/** Whether a system card is live and needs this viewer: an open ask, an offer, an Undo still in reach. */
function liveCard(message: ChatMessageView, ctx: LaneContext): boolean {
  const card = message.card!;
  switch (card.type) {
    case "why":
    case "offer":
      return true;
    case "preview_applied": {
      const meta = card.meta as PreviewAppliedMeta | null | undefined;
      return Boolean(
        meta?.undo_token &&
        !meta.undone &&
        !ctx.undone?.has(meta.undo_token) &&
        meta.undo_until &&
        meta.undo_until > ctx.nowS,
      );
    }
    case "decision": {
      const d = decisionOf(ctx, card.id) ?? decisionOf(ctx, card.ref);
      return d?.status === "open";
    }
    case "loop": {
      const d = decisionOf(ctx, card.ref);
      return d?.status === "open";
    }
    case "proposal": {
      const p = proposalOf(ctx, card.id, card.ref);
      return Boolean(p && OPEN_PROPOSAL.has(p.status));
    }
    default:
      return false;
  }
}

/** Where one message goes: the conversation, the Activity list, or nowhere (its ask is Your move). */
export function messageLane(message: ChatMessageView, ctx: LaneContext): Lane {
  if (message.card && repeatsAsk(message, ctx) && message.author_kind === "system") {
    return "hidden";
  }
  const told = (message as { lane?: string }).lane;
  if (told === "activity" || told === "conversation") return told;
  if (message.author_kind !== "system") return "conversation";
  if (!message.card) return "activity";
  return liveCard(message, ctx) ? "conversation" : "activity";
}

/** Where a folded thread item goes: runs of base moves and an incident's repeats are activity. */
export function itemLane(item: FoldedItem, ctx: LaneContext): Lane {
  if (item.type === "message") return messageLane(item.message, ctx);
  if (item.type === "bus") return "hidden";
  return "activity";
}

/** The newest seq among `items` (an Activity line's "since you looked" mark). */
export function itemSeq(item: FoldedItem): number {
  if (item.type === "message") return item.message.seq;
  if (item.type === "bus") return 0;
  return Math.max(...item.messages.map((m) => m.seq));
}
