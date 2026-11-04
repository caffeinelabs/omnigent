import { describe, expect, it } from "vitest";
import type { DecisionRef, NeedsYouList, NodePage, NodeSummary, StatusLine } from "../nodeTypes";
import { normalizeNeedsYou, normalizeYourMove } from "../nodeWire";
import { sampleEngine } from "../fixture/sampleNodeSource";
import {
  askName,
  askerName,
  childPhrase,
  deriveMoves,
  fullTitle,
  holdsSlug,
  moveView,
  movedAsks,
  nameUnder,
  nodeName,
  shortTitle,
  words,
  youByLevel,
} from "./calmModel";

describe("calm · short names", () => {
  it("keeps a short title and names a long or machine one in six words or fewer", () => {
    expect(shortTitle("Agent assistant")).toBe("Agent assistant");
    expect(
      shortTitle(
        "From C-11: create assistant-core · create assistant-panel-ux · create connectors-core · create skill-discovery",
      ),
    ).toBe("Create assistant core +3");
    expect(shortTitle("assistant-core")).toBe("Assistant core");
    expect(
      shortTitle(
        "i want to build a bot / dots like feature. those agents can not only help with building, but also discovery",
      ),
    ).toBe("I want to build a bot");
    expect(
      shortTitle("@Atlas build-tier-drafts-2 hit the same 300 s sandbox launch timeout again"),
    ).toBe("Build tier drafts 2 hit…");
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

  it("never shows a slug inside a title, and a child named after its parent keeps the rest", () => {
    expect(holdsSlug("build-tier-drafts-3, part 1")).toBe(true);
    expect(holdsSlug("Start the live-search fix")).toBe(false);
    expect(shortTitle("build-tier-drafts-3, part 1")).toBe("Build tier drafts 3, part 1");
    expect(nameUnder({ title: "build-tier-drafts-3, part 2" }, "Build tier drafts 3")).toBe(
      "Part 2",
    );
    expect(nameUnder({ title: "Assistant core" }, "Agent assistant")).toBe("Assistant core");
    expect(nameUnder({ title: "Agent assistant" }, "Agent assistant")).toBe("Agent assistant");
  });

  it("a cut name never ends on a dangling word or a number and its unit", () => {
    expect(shortTitle("Please start the sandbox launch again after the 300 s timeout passes")).toBe(
      "Please start the sandbox launch…",
    );
    expect(shortTitle("The runner waits 300 s before it gives up on a sandbox")).toBe(
      "The runner waits…",
    );
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
        quiet: false,
        primary: null,
      },
      {
        id: "D-49",
        decision: ask("D-49", "Apply C-19"),
        text: "Apply C-19",
        unblocks: "y",
        quiet: false,
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

  it("names what an ask unblocks as the page's rows do: itself as “this task”, never a slug or an id", () => {
    const engine = sampleEngine();
    const task = engine.node("T-16") as NodePage;
    const needs = normalizeNeedsYou(engine.needsYou("product") as NeedsYouList);
    const status: StatusLine = { kind: "needs", label: "Needs you:", segments: [], ends: "person" };
    const given = normalizeYourMove(
      {
        items: [
          {
            id: "D-45",
            verb: "Apply",
            object: "C-16",
            unblocks: "T-16 and assistant-panel-ux",
            unblocks_ref: "T-16",
            unblocks_refs: ["assistant-panel-ux", "T-16"],
            action: { label: "Apply", option: "apply" },
            decision: ask("D-45", "Apply C-16", {
              title: "human proposes C-16: update assistant-core (constraints, dod)",
            }),
          },
        ],
        more: 0,
      },
      status,
    )!;
    const view = moveView({ ...task, your_move: given }, [], 3, needs);
    expect(view.rows[0]!.unblocks).toBe("this task and Assistant panel ux");
    // On the panel's page it is "this project"; a node the page knows only by its code is "+1".
    const panel = engine.node("assistant-panel-ux") as NodePage;
    const fromPanel = moveView({ ...panel, your_move: given }, [], 3, needs);
    expect(fromPanel.rows[0]!.unblocks).toMatch(/^this project( and Debit each turn| \+1)$/);
    expect(view.rows[0]!.name).toBe("update assistant core");
    expect(view.rows[0]!.quiet).toBe(false);
    // On the feature, its children by their names; a node it knows only by id is "+1".
    const feature = engine.node("I-1") as NodePage;
    const named = moveView({ ...feature, your_move: given }, [], 3, needs);
    expect(named.rows[0]!.unblocks).toMatch(/^Assistant panel ux \+1$/);
    const bare = normalizeYourMove(
      { items: [{ ...given.items[0]!, unblocks_refs: undefined, unblocks_ref: undefined }] },
      status,
    )!;
    const said = moveView({ ...feature, your_move: bare }, [], 3, needs).rows[0]!.unblocks;
    expect(said).not.toMatch(/T-\d+|[a-z]+-[a-z]+/);
  });

  it("says “blocks nothing” only when the server says so; a held ask with nothing named frees this node", () => {
    const engine = sampleEngine();
    const page = engine.node("build-tier-drafts-3") as NodePage;
    const status: StatusLine = { kind: "needs", label: "Needs you:", segments: [], ends: "person" };
    const merge = {
      id: "merge:main",
      verb: "Merge",
      object: "#9856 and #9857",
      action: { label: "Review and merge", url: "https://example.com/pulls" },
    };
    const held = normalizeYourMove({ items: [merge], more: 0 }, status)!;
    const row = moveView({ ...page, your_move: held }, []).rows[0]!;
    expect(row.quiet).toBe(false);
    expect(row.unblocks).toBe("this project");
    expect(row.name).toBeNull();
    const idle = normalizeYourMove(
      { items: [{ ...merge, blocks_nothing: true }], more: 0 },
      status,
    )!;
    const quiet = moveView({ ...page, your_move: idle }, []).rows[0]!;
    expect(quiet.quiet).toBe(true);
    expect(quiet.unblocks).toBeNull();
  });

  it("names an ask by its title in a few words, unless the row already says it", () => {
    expect(
      askName(
        ask("D-45", "Apply C-16", {
          title: "human proposes C-16: update assistant-core (constraints, dod)",
        }),
        "Apply C-16",
      ),
    ).toBe("update assistant core");
    expect(
      askName(ask("D-49", "Apply C-19", { title: "Start the live-search fix?" }), "Apply C-19"),
    ).toBe("start the live search fix");
    expect(
      askName(
        ask("m", "Merge #1 and #2", { title: "Merge #1 and #2 into main" }),
        "Merge #1 and #2",
      ),
    ).toBeNull();
    expect(askName(ask("D-1", "Apply C-1"), "Apply C-1")).toBeNull();
  });

  it("never names a factory id as who asked", () => {
    expect(askerName("090e6d1a022a440fa754264911e9980c", "f-1")).toBe("the factory");
    expect(askerName("f-1", "f-1")).toBe("the factory");
    expect(askerName("Atlas", "f-1")).toBe("Atlas");
    expect(askerName("", "f-1")).toBeNull();
  });

  it("counts the viewer's asks under each level below a page, as Your move ranks them", () => {
    const engine = sampleEngine();
    const page = engine.node("product") as NodePage;
    const needs = normalizeNeedsYou(engine.needsYou("product") as NeedsYouList);
    const you = youByLevel(page, needs)!;
    const asks = needs.items.length + needs.blocks_nothing.length;
    expect(you.get(page.home ?? "feature")).toBeGreaterThan(0);
    for (const n of you.values()) expect(n).toBeLessThanOrEqual(asks);
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
