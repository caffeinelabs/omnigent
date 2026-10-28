import { describe, expect, it } from "vitest";
import {
  type RoomCandidate,
  applyRoomCandidate,
  detectRoomTokenAt,
  findRoomTokens,
  roomCandidates,
  roomMentions,
} from "./composerMentions";

const tokens = (text: string) => findRoomTokens(text).map((t) => [t.kind, t.text, t.value]);

describe("findRoomTokens", () => {
  it.each([
    ["@atlas please split it", [["agent", "@atlas", "atlas"]]],
    ["@Sentinel look", [["agent", "@sentinel", "sentinel"]]],
    ["@PL:db-version use knex", [["agent", "@pl:db-version", "pl:db-version"]]],
    ["ping @mia@caffeine.ai", [["person", "@mia@caffeine.ai", "mia@caffeine.ai"]]],
    ["see #db-version", [["project", "#db-version", "db-version"]]],
    [
      "post in #factory or #improvements",
      [
        ["thread", "#factory", "factory"],
        ["thread", "#improvements", "improvements"],
      ],
    ],
    ["!9630 is ready", [["pr", "!9630", "9630"]]],
    [
      "answer D-7, apply C-4",
      [
        ["decision", "D-7", "7"],
        ["proposal", "C-4", "4"],
      ],
    ],
    [
      "T-12 blocks INC-13 (F-9)",
      [
        ["task", "T-12", "12"],
        ["incident", "INC-13", "13"],
        ["finding", "F-9", "9"],
      ],
    ],
    [
      "@pl:db-version T-4 waits on D-31 for !9631",
      [
        ["agent", "@pl:db-version", "pl:db-version"],
        ["task", "T-4", "4"],
        ["decision", "D-31", "31"],
        ["pr", "!9631", "9631"],
      ],
    ],
  ])("reads %j", (text, expected) => {
    expect(tokens(text)).toEqual(expected);
  });

  it.each([
    "mail mia@caffeine.ai directly",
    "a t-shirt and d-7 in lower case",
    "url https://x.dev/#anchor and path a/#b",
    "D-0 and !0 are not refs",
    "AD-7 and xD-7 are words",
    "issue#12 and foo!12",
    "@unknown agent",
  ])("finds nothing in %j", (text) => {
    expect(findRoomTokens(text)).toEqual([]);
  });

  it("keeps positions so chips replace the right text", () => {
    const [token] = findRoomTokens("go #db-version now");
    expect(token).toMatchObject({ start: 3, end: 14 });
  });
});

describe("roomMentions", () => {
  it("sends agents and people once, normalized", () => {
    expect(
      roomMentions("@Atlas and @atlas, @PL:db-version, @mia@caffeine.ai #db-version D-7"),
    ).toEqual(["@atlas", "@pl:db-version", "@mia@caffeine.ai"]);
  });
});

describe("detectRoomTokenAt", () => {
  it.each([
    ["@at", { trigger: "@", query: "at", start: 0 }],
    ["hi @pl:db", { trigger: "@", query: "pl:db", start: 3 }],
    ["see #db", { trigger: "#", query: "db", start: 4 }],
    ["(#", { trigger: "#", query: "", start: 1 }],
    ["!96", { trigger: "!", query: "96", start: 0 }],
    ["answer D-", { trigger: "D-", query: "", start: 7 }],
    ["C-1", { trigger: "C-", query: "1", start: 0 }],
    ["T-4", { trigger: "T-", query: "4", start: 0 }],
    ["ack INC-1", { trigger: "INC-", query: "1", start: 4 }],
    ["F-", { trigger: "F-", query: "", start: 0 }],
  ])("detects %j", (text, expected) => {
    expect(detectRoomTokenAt(text, text.length)).toEqual({ ...expected, end: text.length });
  });

  it.each(["plain text", "@atlas ", "d-7", "mail@x", "a#b", "#db done"])(
    "is quiet for %j",
    (text) => {
      expect(detectRoomTokenAt(text, text.length)).toBeNull();
    },
  );
});

describe("roomCandidates and applyRoomCandidate", () => {
  const all: RoomCandidate[] = [
    { kind: "agent", insert: "@atlas", label: "Atlas" },
    { kind: "agent", insert: "@pl:db-version", label: "PL · db-version", keywords: ["db-version"] },
    { kind: "person", insert: "@mia@caffeine.ai", label: "Mia" },
    { kind: "project", insert: "#db-version", label: "DB version endpoint" },
    { kind: "decision", insert: "D-7", label: "Which table?" },
    { kind: "pr", insert: "!9630", label: "API version" },
  ];

  it("offers the trigger's kinds, prefix matches first", () => {
    const active = detectRoomTokenAt("@db", 3)!;
    expect(roomCandidates(active, all).map((c) => c.insert)).toEqual(["@pl:db-version"]);
    const people = detectRoomTokenAt("@mi", 3)!;
    expect(roomCandidates(people, all).map((c) => c.insert)).toEqual(["@mia@caffeine.ai"]);
    const any = detectRoomTokenAt("@", 1)!;
    expect(roomCandidates(any, all)).toHaveLength(3);
    expect(roomCandidates(detectRoomTokenAt("D-", 2)!, all).map((c) => c.insert)).toEqual(["D-7"]);
  });

  it("replaces the token and leaves the caret after it", () => {
    const text = "ask @pl:d about it";
    const active = detectRoomTokenAt(text, 9)!;
    const result = applyRoomCandidate(text, active, all[1]!);
    expect(result.text).toBe("ask @pl:db-version about it");
    expect(result.caret).toBe("ask @pl:db-version ".length);
  });
});
