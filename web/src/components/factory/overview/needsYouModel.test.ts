import { describe, expect, it } from "vitest";
import type { DecisionView } from "@/lib/factoryApi";
import { liveRunSnapshot } from "../__fixtures__/liveRun";
import { round3Snapshot, viewerOf } from "../shell/__fixtures__/shellFixtures";
import { PL_CACHE, issuesResponse } from "../agents/__fixtures__/opsFixtures";
import {
  decisionScope,
  defaultNeedsTab,
  issueScope,
  myDecisionCount,
  splitNeedsYou,
} from "./needsYouModel";

function decision(patch: Partial<DecisionView>): DecisionView {
  const base = round3Snapshot().decisions[0]!;
  return { ...base, assignee: null, claimed_by: null, ...patch };
}

describe("decisionScope", () => {
  const mia = viewerOf("operator");
  const luka = viewerOf("creator");
  const ops = viewerOf("viewer");

  it("is mine when assigned to me or claimed by me", () => {
    expect(decisionScope(decision({ assignee: "mia@caffeine.ai" }), mia)).toBe("mine");
    expect(decisionScope(decision({ claimed_by: "mia@caffeine.ai" }), mia)).toBe("mine");
  });

  it("is someone else's when assigned to or claimed by another person", () => {
    expect(decisionScope(decision({ assignee: "mia@caffeine.ai" }), luka)).toBe("other");
    expect(
      decisionScope(decision({ assignee: "operators", claimed_by: "luka@caffeine.ai" }), mia),
    ).toBe("other");
  });

  it("is my role's when for operators (the default) and I am one", () => {
    expect(decisionScope(decision({ assignee: "operators" }), mia)).toBe("role");
    expect(decisionScope(decision({ assignee: null }), mia)).toBe("role");
    expect(decisionScope(decision({ assignee: "operators" }), ops)).toBe("other");
  });

  it("is my role's when for owners only if I own the factory", () => {
    expect(decisionScope(decision({ assignee: "owners" }), luka)).toBe("role");
    expect(decisionScope(decision({ assignee: "owners" }), mia)).toBe("other");
  });

  it("treats a server without viewer blocks as one person running everything", () => {
    expect(decisionScope(decision({ assignee: "owners" }), null)).toBe("role");
  });
});

describe("splitNeedsYou", () => {
  it("splits the round-3 factory for Mia (operator): D-2 is hers, the rest her role's or others'", () => {
    const snapshot = round3Snapshot("operator");
    const split = splitNeedsYou(snapshot);
    expect(split.mine.map((i) => i.key)).toEqual([`decision:${snapshot.decisions[0]!.id}`]);
    const roleRefs = split.role.flatMap((i) => (i.kind === "decision" ? [i.decision.ref] : []));
    expect(roleRefs).toEqual(expect.arrayContaining(["D-31", "D-33"]));
    expect(roleRefs).not.toContain("D-3"); // for owners
    expect(split.role.some((i) => i.kind === "pr" && i.pr.number === 9630)).toBe(true);
    // All lists everything open, Mia's and her role's first.
    expect(split.all.slice(0, split.mine.length + split.role.length)).toEqual([
      ...split.mine,
      ...split.role,
    ]);
    expect(split.all.filter((i) => i.scope === "other").map((i) => i.kind)).toContain("decision");
  });

  it("gives a viewer nothing of their own", () => {
    const split = splitNeedsYou(round3Snapshot("viewer"));
    expect(split.mine).toEqual([]);
    expect(split.role).toEqual([]);
    expect(split.all.length).toBeGreaterThan(0);
    expect(defaultNeedsTab(split)).toBe("all");
  });

  it("orders alerts, then blocking decisions, ready PRs, other decisions and claims", () => {
    const split = splitNeedsYou(round3Snapshot("creator"));
    const ranks = split.role.map((i) =>
      i.kind === "alert"
        ? 0
        : i.kind === "decision"
          ? i.decision.blocking
            ? 1
            : 3
          : i.kind === "pr"
            ? 2
            : 4,
    );
    expect([...ranks].sort((a, b) => a - b)).toEqual(ranks);
  });

  it("counts open decisions that are mine or my role's", () => {
    const split = splitNeedsYou(round3Snapshot("creator"));
    expect(myDecisionCount(split)).toBe(
      [...split.mine, ...split.role].filter((i) => i.kind === "decision").length,
    );
    expect(defaultNeedsTab(split)).toBe(split.mine.length > 0 ? "mine" : "role");
  });

  it("works on a round-2 snapshot without assignees or a viewer block", () => {
    const split = splitNeedsYou(liveRunSnapshot());
    expect(split.mine).toEqual([]);
    expect(split.role.map((i) => i.kind)).toEqual(expect.arrayContaining(["decision", "pr"]));
  });
});

describe("incidents in Needs you", () => {
  const issues = () => issuesResponse("?class=incident&status=open,acked");

  it("lists open incidents first, critical on top, and drops the alert they stand for", () => {
    const snapshot = round3Snapshot();
    const without = splitNeedsYou(snapshot);
    expect(without.all.some((i) => i.key === `alert:dead-${PL_CACHE}`)).toBe(true);
    const split = splitNeedsYou(snapshot, snapshot.viewer ?? null, issues());
    expect(split.role.slice(0, 2).map((i) => (i.kind === "issue" ? i.issue.ref : i.kind))).toEqual([
      "INC-12",
      "INC-13",
    ]);
    expect(split.all.some((i) => i.key === `alert:dead-${PL_CACHE}`)).toBe(false);
  });

  it("follows the server's mine: assigned to me is mine, my role's is role, a viewer's is other", () => {
    const mia = viewerOf("operator");
    const inc13 = issues().data.find((i) => i.ref === "INC-13")!;
    expect(issueScope({ ...inc13, assignee: "mia@caffeine.ai" }, mia, new Set())).toBe("mine");
    expect(issueScope(inc13, mia, new Set([inc13.id]))).toBe("role");
    expect(issueScope(inc13, viewerOf("viewer"), new Set())).toBe("other");
  });
});
