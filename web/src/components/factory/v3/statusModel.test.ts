import { describe, expect, it } from "vitest";
import type { DecisionRef, StatusLine } from "./nodeTypes";
import { statusText } from "./statusModel";

const D45: DecisionRef = {
  id: "D-45",
  verb: "Apply C-16",
  actions: [{ id: "apply", label: "Apply" }],
  asker: "Atlas",
  age: "6h",
};
const MERGE: DecisionRef = {
  ...D45,
  id: "merge:main",
  verb: "Merge #9856 and #9857",
  grouped: true,
};

const person = (decision: DecisionRef) => ({ sep: "→" as const, person: true as const, decision });

describe("cockpit v3 · how a status line reads", () => {
  it("a full line is the whole path, the person named once", () => {
    const line: StatusLine = {
      kind: "needs",
      label: "Not moving:",
      segments: [
        { sep: "›", ref: "T-16", title: "T-16" },
        { sep: "→", label: "held for C-16" },
        person(D45),
      ],
      ends: "person",
      decision: D45,
      also_holds: [{ ref: "assistant-panel-ux", title: "assistant-panel-ux" }],
    };
    expect(statusText(line)).toBe(
      "Not moving: T-16 → held for C-16 → needs you: Apply C-16 (D-45) · also holds assistant-panel-ux",
    );
  });

  it("an ask that ends the line alone reads as the verb (a group of merges has no id)", () => {
    const line: StatusLine = {
      kind: "needs",
      label: "Needs you:",
      segments: [person(MERGE)],
      ends: "person",
      decision: MERGE,
      detail: "main is merged by people",
    };
    expect(statusText(line)).toBe("Needs you: Merge #9856 and #9857 · main is merged by people");
  });

  it("a compact line ending at the header's ask says “the ask above”", () => {
    const via: StatusLine = {
      kind: "needs",
      label: "Needs you:",
      segments: [{ sep: "›", ref: "T-16", title: "T-16" }, person(D45)],
      ends: "person",
      decision: D45,
    };
    expect(statusText(via, { compact: true })).toBe("Needs you: Apply C-16 · via T-16");
    expect(statusText(via, { compact: true, aboveId: "D-45" })).toBe(
      "Needs you: Apply C-16 · via T-16 · the ask above",
    );
    const held: StatusLine = {
      kind: "waiting",
      label: "Held for C-16:",
      segments: [person(D45)],
      ends: "person",
      decision: D45,
    };
    expect(statusText(held, { compact: true })).toBe("Held for C-16: needs you: Apply C-16 (D-45)");
    expect(statusText(held, { compact: true, aboveId: "D-45" })).toBe(
      "Held for C-16: the ask above",
    );
  });
});
