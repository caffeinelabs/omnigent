import { describe, expect, it } from "vitest";
import type { DecisionRef, NeedsYouList, NodePage, NodeSummary, StatusLine } from "../nodeTypes";
import { normalizeNeedsYou, normalizeYourMove } from "../nodeWire";
import { sampleEngine } from "../fixture/sampleNodeSource";
import {
  childPhrase,
  deriveMoves,
  fullTitle,
  moveView,
  movedAsks,
  nodeName,
  shortTitle,
  words,
} from "./calmModel";

describe("calm · short names", () => {
  it("keeps a short title and names a long or machine one in six words or fewer", () => {
    expect(shortTitle("Agent assistant")).toBe("Agent assistant");
    expect(
      shortTitle(
        "From C-11: create assistant-core · create assistant-panel-ux · create connectors-core · create skill-discovery",
      ),
    ).toBe("Create assistant-core +3");
    expect(shortTitle("assistant-core")).toBe("Assistant core");
    expect(
      shortTitle(
        "i want to build a bot / dots like feature. those agents can not only help with building, but also discovery",
      ),
    ).toBe("I want to build a bot");
    expect(
      shortTitle("@Atlas build-tier-drafts-2 hit the same 300 s sandbox launch timeout again"),
    ).toBe("Build-tier-drafts-2 hit the same 300…");
    for (const title of [
      "Agents that also do discovery, sales and promotion",
      "Please start 2, 3, 4 and 6 as four projects on the app (previews and demos on)",
    ]) {
      expect(words(shortTitle(title)).length).toBeLessThanOrEqual(6);
    }
  });

  it("keeps the name the server gave; the full text is the details'", () => {
    expect(nodeName({ title: "build-tier-drafts-3", named: "person" })).toBe("build-tier-drafts-3");
    expect(nodeName({ title: "build-tier-drafts-3", named: "words" })).toBe("Build tier drafts 3");
    expect(
      fullTitle({ title: "Agent assistant", full_title: "From C-11: create a", named: "factory" }),
    ).toBe("From C-11: create a");
    expect(fullTitle({ title: "Agent assistant" })).toBeNull();
  });

  it("counts words, not separators", () => {
    expect(words("Apply C-16 · unblocks Agent assistant › T-16")).toEqual([
      "Apply",
      "C-16",
      "unblocks",
      "Agent",
      "assistant",
      "T-16",
    ]);
  });
});

function child(state: NodeSummary["state"], line: Partial<StatusLine> = {}): NodeSummary {
  return {
    ref: "x",
    level: "project",
    kind: "project",
    title: "x",
    state,
    needs: { open: 0, blocking: 0 },
    status_compact: { kind: "waiting", label: "", segments: [], ends: "other", ...line },
  };
}

describe("calm · one phrase per child", () => {
  it("says nothing while a child moves or is done", () => {
    expect(childPhrase(child("moving", { kind: "moving", label: "Moving:" }))).toBeNull();
    expect(childPhrase(child("done", { kind: "done", label: "Done" }))).toBeNull();
  });

  it("names what holds it, or that it needs you, in a few words", () => {
    expect(childPhrase(child("needs", { kind: "needs", label: "Needs you:" }))).toEqual({
      text: "Needs you",
      tone: "you",
    });
    const waits = child("waiting", {
      label: "Waits on",
      segments: [
        { sep: "→", label: "", ref: "assistant-core", title: "assistant-core" },
        { sep: "→", label: "held for" },
      ],
    });
    expect(childPhrase(waits)).toEqual({ text: "Waits on Assistant core" });
    expect(childPhrase({ ...child("later"), held_for: "wave 2" })).toEqual({
      text: "Held for wave 2",
    });
    expect(childPhrase(child("notstarted", { label: "Not started:" }))).toEqual({
      text: "Not started",
    });
  });
});

function ask(id: string, verb: string, extra: Partial<DecisionRef> = {}): DecisionRef {
  return {
    id,
    verb,
    actions: [{ id: "apply", label: "Apply" }],
    asker: "Atlas",
    age: "1h",
    ...extra,
  };
}

describe("calm · Your move", () => {
  it("derives the root's moves from Needs you: ranked, named at the level Home lists, quiet asks last", () => {
    const engine = sampleEngine();
    const page = engine.node("product") as NodePage;
    const needs = normalizeNeedsYou(engine.needsYou("product") as NeedsYouList);
    const rows = deriveMoves(page, needs, "feature");
    expect(rows[0]).toMatchObject({ id: "D-45", text: "Apply C-16" });
    expect(rows[0]!.unblocks).toBe("Agent assistant (sample)");
    expect(rows.slice(-1)[0]!.unblocks).toBeNull();
    const view = moveView(page, rows);
    expect(view.rows).toHaveLength(3);
    expect(view.more).toBe(rows.length - 3);
    expect(movedAsks(view).has("C-16")).toBe(true);
  });

  it("puts the ask a node waits on first, also when it sits outside its subtree", () => {
    const engine = sampleEngine();
    const page = engine.node("assistant-panel-ux") as NodePage;
    const rows = deriveMoves(
      page,
      normalizeNeedsYou(engine.needsYou("product") as NeedsYouList),
      "task",
    );
    expect(rows[0]).toMatchObject({ id: "D-45" });
  });

  it("reads the server's Your move, with the derived rows behind “+n more”", () => {
    const status: StatusLine = { kind: "needs", label: "Needs you:", segments: [], ends: "person" };
    const given = normalizeYourMove(
      {
        items: [
          {
            id: "D-45",
            verb: "Apply",
            object: "C-16",
            unblocks: "Agent assistant",
            unblocks_ref: "I-1",
            action: { label: "Apply", option: "apply" },
            decision: ask("D-45", "Apply C-16"),
          },
          {
            id: "merge:main",
            verb: "Merge",
            object: "#9856 and #9857",
            action: { label: "Review and merge", url: "https://example.com/pulls" },
          },
        ],
        more: 1,
      },
      status,
    )!;
    expect(given.items[1]!.primary).toEqual({
      label: "Review and merge",
      url: "https://example.com/pulls",
    });
    const page = { your_move: given } as NodePage;
    const view = moveView(page, [
      {
        id: "D-45",
        decision: ask("D-45", "Apply C-16"),
        text: "Apply C-16",
        unblocks: "x",
        primary: null,
      },
      {
        id: "D-49",
        decision: ask("D-49", "Apply C-19"),
        text: "Apply C-19",
        unblocks: "y",
        primary: null,
      },
    ]);
    expect(view.rows.map((r) => r.text)).toEqual(["Apply C-16", "Merge #9856 and #9857"]);
    expect(view.rows[0]!.unblocks).toBe("Agent assistant");
    expect(view.rows[0]!.primary?.action).toEqual({ id: "apply", label: "Apply" });
    expect(view.rows[1]!.primary?.url).toBe("https://example.com/pulls");
    expect(view.more).toBe(1);
    expect(view.rest.map((r) => r.id)).toEqual(["D-49"]);
  });

  it("is empty when nothing needs the viewer", () => {
    const engine = sampleEngine();
    const page = engine.node("connectors-core") as NodePage;
    const view = moveView(
      page,
      deriveMoves(page, { items: [], blocks_nothing: [], closed_alarms: [], other_tree: 0 }, null),
    );
    expect(view.rows).toEqual([]);
    expect(view.more).toBe(0);
  });
});
