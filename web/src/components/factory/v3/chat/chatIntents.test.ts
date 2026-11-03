import { describe, expect, it } from "vitest";
import { matchingNodeVerbs, nodeVerbCommands } from "../../cmdk/slashCommands";
import { sampleEngine } from "../fixture/sampleNodeSource";
import { isBareWhy, namedChild, pageDecisions, parseIntent } from "./chatIntents";

const engine = sampleEngine();

function page(ref: string) {
  const p = engine.node(ref);
  if (!p) throw new Error(`no node ${ref}`);
  return p;
}

describe("cockpit v3 · plain words in a node's chat", () => {
  it("reads “why” in its usual spellings; only a bare “why” stays off the thread", () => {
    for (const text of ["why", "Why?", "/why"]) {
      expect(parseIntent(text, page("assistant-panel-ux"))).toEqual({ kind: "why", bare: true });
      expect(isBareWhy(text)).toBe(true);
    }
    for (const text of ["why is it not moving", "why hasn't it started", "what is it waiting on"]) {
      expect(parseIntent(text, page("assistant-panel-ux"))).toEqual({ kind: "why", bare: false });
      expect(isBareWhy(text)).toBe(false);
    }
  });

  it("finds the ask “apply c-16” names, to show its card (never to answer it)", () => {
    for (const text of ["apply c-16", "Apply C16", "approve D-45"]) {
      const intent = parseIntent(text, page("assistant-core"));
      expect(intent?.kind).toBe("answer");
      expect(intent?.kind === "answer" && intent.decision.id).toBe("D-45");
    }
    // The root's path ends at the same ask.
    expect(parseIntent("apply c-16", page("product"))?.kind).toBe("answer");
    // An ask the page does not name is a message for the responder.
    expect(parseIntent("apply c-99", page("assistant-core"))).toBeNull();
  });

  it("opens Start now here, or on the child the words name", () => {
    expect(parseIntent("start it now", page("assistant-panel-ux"))).toEqual({
      kind: "verb",
      verb: "start",
      at: "assistant-panel-ux",
    });
    expect(parseIntent("start the panel now", page("I-1"))).toEqual({
      kind: "verb",
      verb: "start",
      at: "assistant-panel-ux",
    });
    // Nothing to start here and no child named: a message.
    expect(parseIntent("start it now", page("assistant-core"))).toBeNull();
  });

  it("opens Add child with the title the words give", () => {
    expect(parseIntent("also plan Ads network integration", page("I-1"))).toEqual({
      kind: "verb",
      verb: "add",
      at: "I-1",
      args: { title: "Ads network integration" },
    });
  });

  it("opens Team, Autonomy & merge and Pause where the node has them", () => {
    expect(parseIntent("swap luz to codex", page("assistant-core"))).toEqual({
      kind: "verb",
      verb: "team",
      at: "assistant-core",
      args: { member: "luz", to: "codex" },
    });
    expect(parseIntent("auto-merge this", page("I-1"))).toMatchObject({ verb: "auto" });
    expect(parseIntent("pause", page("assistant-core"))).toMatchObject({ verb: "pause" });
    // The product has no Team: a message.
    expect(parseIntent("swap luz to codex", page("product"))).toBeNull();
  });

  it("opens research where a study is offered", () => {
    expect(parseIntent("research what comparable products do", page("R-1"))).toMatchObject({
      verb: "research",
      at: "R-1",
    });
  });

  it("leaves anything else as a message", () => {
    for (const text of ["what does wave 2 need?", "thanks", "ship it", ""]) {
      expect(parseIntent(text, page("I-1"))).toBeNull();
    }
  });

  it("opens nothing for a sentence to the agent, even one that names a verb", () => {
    // Long, negated, a question, or swapping to something that is not a vendor or model.
    const sentences: [string, string][] = [
      ["Please don't auto-merge this, I want to review the migration first", "I-1"],
      ["add logging to the payment handler before you open the PR", "assistant-core"],
      ["hold on, I think the spec for T-16 is wrong", "assistant-core"],
      ["can you change it to use the v2 billing endpoint", "assistant-core"],
      ["why does the debit test fail on staging? please fix it", "assistant-panel-ux"],
      ["plan the rollout carefully and tell me the risks", "assistant-core"],
      ["this study looks too expensive, what else could we do?", "R-1"],
      ["don't pause", "assistant-core"],
      ["hold on", "assistant-core"],
      ["swap luz to the other one", "assistant-core"],
      ["start it now?", "assistant-panel-ux"],
    ];
    for (const [text, ref] of sentences) {
      expect({ text, intent: parseIntent(text, page(ref)) }).toEqual({ text, intent: null });
    }
  });

  it("lists every ask a page names once", () => {
    const ids = pageDecisions(page("I-1")).map((d) => d.id);
    expect(ids).toContain("D-45");
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("finds the child the words name", () => {
    const kids = page("I-1").children.items;
    expect(namedChild("the panel", kids)?.ref).toBe("assistant-panel-ux");
    expect(namedChild("it now", kids)).toBeNull();
  });
});

describe("cockpit v3 · the / menu of a node", () => {
  const core = page("assistant-core");
  const rows = nodeVerbCommands({
    verbs: core.verbs,
    why: Boolean(core.status.why?.length),
    decision: core.status.decision,
  });

  it("lists the ask first, then why, then the verbs that open previews (no Talk)", () => {
    expect(rows.map((r) => r.id)).toEqual([
      "answer:D-45",
      "why",
      "verb:add",
      "verb:team",
      "verb:auto",
      "verb:pause",
      "verb:move",
    ]);
    expect(rows[0]!.detail).toBe("opens D-45: answer with a click");
  });

  it("filters by what follows the /", () => {
    expect(matchingNodeVerbs("/", rows)).toHaveLength(rows.length);
    expect(matchingNodeVerbs("/tea", rows).map((r) => r.label)).toEqual(["Team"]);
    expect(matchingNodeVerbs("why", rows).map((r) => r.id)).toEqual(["why"]);
    expect(matchingNodeVerbs("/zzz", rows)).toEqual([]);
  });

  it("leaves out an ask answered elsewhere and verbs the viewer may not use", () => {
    const out = nodeVerbCommands({
      verbs: [
        { verb: "talk", label: "Talk", enabled: true },
        { verb: "pause", label: "Pause", enabled: false },
      ],
      why: false,
      decision: { id: "D-9", verb: "Merge #1", external_url: "https://example.com" },
    });
    expect(out).toEqual([]);
  });
});
