import { describe, expect, it } from "vitest";
import type { ChatMessageView } from "@/lib/factoryApi";
import { longIncidentThread, manyBaseMovesThread, roomMessages } from "./__fixtures__/roomFixtures";
import { baseMovesOf, foldRoomItems, supersededIssueCards } from "./roomFold";
import { interleave, type BusLine } from "./roomModel";

function fold(messages: ChatMessageView[], lines: BusLine[] = []) {
  return foldRoomItems(
    interleave(messages, lines, lines.length > 0),
    supersededIssueCards(messages),
  );
}

describe("base moves", () => {
  it("reads a batched line's meta and an older server's one-move line", () => {
    const legacy = manyBaseMovesThread().find((m) => m.seq === 3001)!;
    const [, , , from, , to] = legacy.body.split(" ");
    expect(baseMovesOf(legacy)).toEqual({
      base: "main",
      moves: [{ at: legacy.created_at, from_sha: from, to_sha: to, pr: 9820 }],
    });
    expect(from).toMatch(/^[0-9a-f]{7}$/);
    const batched = manyBaseMovesThread().find((m) => m.seq === 3103)!;
    expect(baseMovesOf(batched)?.moves).toHaveLength(7);
  });

  it("leaves people, agents, cards and other lines alone", () => {
    const said = { ...manyBaseMovesThread().find((m) => m.seq === 3001)!, author_kind: "human" };
    expect(baseMovesOf(said as ChatMessageView)).toBeNull();
    for (const message of roomMessages()) expect(baseMovesOf(message)).toBeNull();
  });

  it("folds a run of one base into one line; other messages and bases keep runs apart", () => {
    const items = fold(manyBaseMovesThread());
    const bases = items.flatMap((i) => (i.type === "base" ? [[i.base, i.moves.length]] : []));
    expect(bases).toEqual([
      ["main", 12],
      ["main", 7],
      ["temp-main", 3],
      ["main", 1],
    ]);
    // The twelve old lines are one item; nothing else is dropped.
    expect(items).toHaveLength(2 + 1 + 2 + 1 + 1 + 1 + 1);
    const people = items.filter((i) => i.type === "message").map((i) => i.message.seq);
    expect(people).toEqual([1, 2, 3101, 3102, 3104]);
  });

  it("lets a bus line between two moves keep them apart", () => {
    const [a, b] = manyBaseMovesThread().filter((m) => m.seq === 3001 || m.seq === 3002);
    const line: BusLine = {
      id: "bus-1",
      ts: (a!.created_at + 1) * 1000,
      from: "Atlas",
      to: "PL",
      kind: "info",
      text: "rebase",
      bounced: false,
    };
    expect(fold([a!, b!], [line]).map((i) => i.type)).toEqual(["base", "bus", "base"]);
    expect(fold([a!, b!]).map((i) => i.type)).toEqual(["base"]);
  });
});

describe("an incident's cards", () => {
  it("keeps the newest card of each incident live and the older ones as repeats", () => {
    const messages = longIncidentThread();
    const superseded = supersededIssueCards(messages);
    expect([...superseded].map((id) => messages.find((m) => m.id === id)!.seq)).toEqual([
      2001, 2004, 2005, 2006, 2008,
    ]);
    const items = fold(messages);
    const repeats = items.flatMap((i) =>
      i.type === "repeats" ? [i.messages.map((m) => m.seq)] : [],
    );
    expect(repeats).toEqual([[2001], [2004, 2005, 2006], [2008]]);
    const live = items.filter((i) => i.type === "message" && i.message.card?.type === "issue");
    expect(live.map((i) => (i.type === "message" ? i.message.seq : 0))).toEqual([2009]);
  });

  it("keeps one card per incident: a card of another incident is untouched", () => {
    const messages = roomMessages();
    expect(supersededIssueCards(messages).size).toBe(0);
    expect(fold(messages).every((i) => i.type === "message")).toBe(true);
  });
});
