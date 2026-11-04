import { describe, expect, it } from "vitest";
import type { ChatMessageView, DecisionView, ProposalView } from "@/lib/factoryApi";
import { activityWords } from "./ActivityLine";
import { type LaneContext, itemLane, messageLane, repeatsAsk } from "./chatLanes";

let seq = 0;
function msg(fields: Partial<ChatMessageView>): ChatMessageView {
  seq += 1;
  return {
    id: `m${seq}`,
    factory_id: "f",
    thread: "factory",
    seq,
    author_kind: "system",
    author: "server",
    author_title: null,
    author_role: null,
    body: "",
    mentions: [],
    refs: [],
    reply_to: null,
    card: null,
    routed: [],
    auto_reply: false,
    created_at: 1_000,
    ...fields,
  };
}

const ctx: LaneContext = {
  asked: new Set(["D-45", "C-16"]),
  decisions: [
    { id: "d49", ref: "D-49", status: "open" } as DecisionView,
    { id: "d45", ref: "D-45", status: "open" } as DecisionView,
    { id: "d40", ref: "D-40", status: "answered" } as DecisionView,
  ],
  proposals: [
    { id: "p14", ref: "C-14", status: "expired", decision_id: null } as unknown as ProposalView,
    { id: "p15", ref: "C-15", status: "pending", decision_id: "d50" } as unknown as ProposalView,
    { id: "p16", ref: "C-16", status: "pending", decision_id: "d45" } as unknown as ProposalView,
  ],
  nowS: 2_000,
};

describe("calm chat · conversation and activity", () => {
  it("keeps people's and agents' messages in the conversation", () => {
    expect(messageLane(msg({ author_kind: "human", body: "hi" }), ctx)).toBe("conversation");
    expect(messageLane(msg({ author_kind: "agent", body: "on it" }), ctx)).toBe("conversation");
  });

  it("folds system events into activity: base moves, spawns, incidents, closed items", () => {
    const system = (type: string, ref: string | null = null, id: string | null = null) =>
      msg({ card: { type, ref, id } as ChatMessageView["card"] });
    expect(messageLane(msg({ body: "the server spawned PL · x" }), ctx)).toBe("activity");
    expect(messageLane(system("system"), ctx)).toBe("activity");
    expect(messageLane(system("issue", "INC-1", "i1"), ctx)).toBe("activity");
    expect(messageLane(system("closed_alarm", "D-46"), ctx)).toBe("activity");
    expect(messageLane(system("pr", "!9876", "pr1"), ctx)).toBe("activity");
    expect(messageLane(system("proposal", "C-14", "p14"), ctx)).toBe("activity");
    expect(messageLane(system("decision", "D-40", "d40"), ctx)).toBe("activity");
    expect(
      itemLane(
        { type: "base", key: "b", at: 0, base: "main", repo: null, moves: [], messages: [] },
        ctx,
      ),
    ).toBe("activity");
  });

  it("keeps live cards that need the viewer, and never repeats an ask Your move shows", () => {
    const card = (type: string, ref: string, id: string) =>
      msg({ card: { type, ref, id } as ChatMessageView["card"] });
    expect(messageLane(card("decision", "D-49", "d49"), ctx)).toBe("conversation");
    expect(messageLane(card("proposal", "C-15", "p15"), ctx)).toBe("conversation");
    expect(messageLane(card("decision", "D-45", "d45"), ctx)).toBe("hidden");
    expect(messageLane(card("proposal", "C-16", "p16"), ctx)).toBe("hidden");
    // Under an agent's message, the card of an ask Your move shows is left out; the words stay.
    const agent = msg({ author_kind: "agent", card: { type: "decision", ref: "D-45", id: "d45" } });
    expect(messageLane(agent, ctx)).toBe("conversation");
    expect(repeatsAsk(agent, ctx)).toBe(true);
  });

  it("keeps a verb's card while its Undo is open, then folds it", () => {
    const applied = (until: number) =>
      msg({
        card: {
          type: "preview_applied",
          ref: "assistant-panel-ux",
          id: null,
          meta: { kind: "preview_applied", verb: "start", undo_token: "t", undo_until: until },
        },
      });
    expect(messageLane(applied(3_000), ctx)).toBe("conversation");
    expect(messageLane(applied(1_500), ctx)).toBe("activity");
  });

  it("reads the server's lane when it sends one", () => {
    const told = msg({ author_kind: "agent", lane: "activity" });
    expect(messageLane(told, ctx)).toBe("activity");
  });

  it("says how much is new since the viewer looked", () => {
    const items = [10, 11, 12].map((n) => ({
      type: "message" as const,
      key: `k${n}`,
      at: 0,
      message: msg({ seq: n }),
    }));
    expect(activityWords(items, { seq: 10, fresh: null })).toBe("Activity · 2 since you looked");
    expect(activityWords(items, { seq: 12, fresh: null })).toBe("Activity · 3");
    expect(activityWords(items, { seq: null, fresh: null })).toBe("Activity · 3");
    // The server's count after the reader's read mark, when it sends one.
    expect(activityWords(items, { seq: null, fresh: 1 })).toBe("Activity · 1 since you looked");
  });
});
