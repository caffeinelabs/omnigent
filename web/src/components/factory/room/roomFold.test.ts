import { describe, expect, it } from "vitest";
import type { ChatMessageView } from "@/lib/factoryApi";
import { round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import {
  afterDeployThread,
  longIncidentThread,
  manyBaseMovesThread,
  roomMessages,
} from "./__fixtures__/roomFixtures";
import {
  BASE_RUN_SPAN_S,
  type FoldedItem,
  baseMovesOf,
  foldRoomItems,
  namesRepos,
  supersededIssueCards,
} from "./roomFold";
import { interleave, type BusLine } from "./roomModel";

function fold(messages: ChatMessageView[], lines: BusLine[] = []) {
  return foldRoomItems(
    interleave(messages, lines, lines.length > 0),
    supersededIssueCards(messages),
  );
}

/** Each item as a short label: `base main@app ×2`, `repeats 4001,4003`, `msg 4013`. */
function shape(items: FoldedItem[]): string[] {
  return items.map((i) => {
    if (i.type === "base") return `base ${i.base}@${i.repo ?? "-"} ×${i.moves.length}`;
    if (i.type === "repeats") return `repeats ${i.messages.map((m) => m.seq).join(",")}`;
    if (i.type === "bus") return "bus";
    return `msg ${i.message.seq}`;
  });
}

function baseLine(seq: number, atS: number, repo: string | null, to = "6666666"): ChatMessageView {
  const row = manyBaseMovesThread().find((m) => m.seq === 3001)!;
  const moves = [{ at: atS, from_sha: "1111111", to_sha: to, pr: null }];
  return {
    ...row,
    id: `line${String(seq).padStart(28, "0")}`,
    seq,
    created_at: atS,
    body: `base main moved 1111111 → ${to}`,
    card: repo
      ? {
          type: "system",
          ref: null,
          id: null,
          meta: { kind: "base.moved", repo, base: "main", moves },
        }
      : { type: "system", ref: null, id: null },
  };
}

describe("base moves", () => {
  it("reads a batched line's meta and an older server's one-move line", () => {
    const legacy = manyBaseMovesThread().find((m) => m.seq === 3001)!;
    const [, , , from, , to] = legacy.body.split(" ");
    expect(baseMovesOf(legacy)).toEqual({
      base: "main",
      repo: null,
      moves: [{ at: legacy.created_at, from_sha: from, to_sha: to, pr: 9820 }],
    });
    expect(from).toMatch(/^[0-9a-f]{7}$/);
    const batched = manyBaseMovesThread().find((m) => m.seq === 3103)!;
    expect(baseMovesOf(batched)?.moves).toHaveLength(7);
    expect(baseMovesOf(batched)?.repo).toBe("caffeinelabs/app");
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

  it("carries a run across cards and system lines; a person or an agent ends it", () => {
    const thread = longIncidentThread();
    const live = thread.find((m) => m.seq === 2009)!;
    const agent = thread.find((m) => m.seq === 2010)!;
    const at = live.created_at;
    const messages = [
      baseLine(1, at - 60, null),
      { ...live, seq: 2 },
      baseLine(3, at + 60, null),
      { ...agent, seq: 4, created_at: at + 120 },
      baseLine(5, at + 180, null),
    ];
    expect(shape(fold(messages))).toEqual(["base main@- ×2", "msg 2", "msg 4", "base main@- ×1"]);
  });

  it("keeps each repo's main its own line", () => {
    const at = manyBaseMovesThread()[0]!.created_at;
    const items = fold([
      baseLine(1, at, "caffeinelabs/app", "5555555"),
      baseLine(2, at + 60, "caffeinelabs/app", "6666666"),
      baseLine(3, at + 120, "caffeinelabs/infra", "4444444"),
      baseLine(4, at + 180, "CaffeineLabs/App", "7777777"),
      baseLine(5, at + 240, null, "8888888"),
    ]);
    expect(shape(items)).toEqual([
      "base main@caffeinelabs/app ×3",
      "base main@caffeinelabs/infra ×1",
      "base main@- ×1",
    ]);
    const app = items[0]!;
    if (app.type !== "base") throw new Error("no app line");
    expect(app.moves.map((m) => m.to_sha)).toEqual(["5555555", "6666666", "7777777"]);
  });

  it("starts a new run once a move is far from the run's first", () => {
    const at = manyBaseMovesThread()[0]!.created_at;
    const items = fold([
      baseLine(1, at, null),
      baseLine(2, at + BASE_RUN_SPAN_S - 1, null),
      baseLine(3, at + BASE_RUN_SPAN_S, null),
    ]);
    expect(shape(items)).toEqual(["base main@- ×2", "base main@- ×1"]);
  });

  it("names the repo only when the factory or the thread has more than one", () => {
    const snapshot = round3Snapshot();
    expect(namesRepos(snapshot, fold(manyBaseMovesThread()))).toBe(false);
    expect(namesRepos(snapshot, fold(afterDeployThread()))).toBe(true);
    const infra = { ...snapshot.projects[0]!, repo: "caffeinelabs/infra" };
    expect(namesRepos({ ...snapshot, projects: [...snapshot.projects, infra] }, [])).toBe(true);
  });
});

describe("an incident's cards", () => {
  it("keeps the newest card of each incident live and the older ones as repeats", () => {
    const messages = longIncidentThread();
    const superseded = supersededIssueCards(messages);
    expect([...superseded].map((id) => messages.find((m) => m.id === id)!.seq)).toEqual([
      2001, 2004, 2005, 2006, 2008,
    ]);
    // Every older card in one line just above the live one; people's words stay put.
    expect(shape(fold(messages)).slice(2)).toEqual([
      "msg 2002",
      "msg 2003",
      "msg 2007",
      "repeats 2001,2004,2005,2006,2008",
      "msg 2009",
      "msg 2010",
    ]);
  });

  it("cleans up a thread where old cards and base lines alternate", () => {
    const messages = afterDeployThread();
    expect(shape(fold(messages)).slice(2)).toEqual([
      "base main@- ×6",
      "repeats 4001,4003,4005,4007,4009,4011",
      "msg 4013",
      "msg 4014",
      "base main@caffeinelabs/infra ×2",
      "base main@caffeinelabs/app ×2",
    ]);
  });

  it("keeps older cards where the last one was when the live card is not loaded", () => {
    const messages = longIncidentThread().filter((m) => m.seq !== 2009);
    const all = longIncidentThread();
    const items = foldRoomItems(interleave(messages, [], false), supersededIssueCards(all));
    expect(shape(items).slice(2)).toEqual([
      "msg 2002",
      "msg 2003",
      "msg 2007",
      "repeats 2001,2004,2005,2006,2008",
      "msg 2010",
    ]);
  });

  it("keeps one card per incident: a card of another incident is untouched", () => {
    const messages = roomMessages();
    expect(supersededIssueCards(messages).size).toBe(0);
    expect(fold(messages).every((i) => i.type === "message")).toBe(true);
  });
});
