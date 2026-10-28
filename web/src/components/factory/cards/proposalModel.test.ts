import { describe, expect, it } from "vitest";
import { proposalInState, roomProposals } from "../room/__fixtures__/roomFixtures";
import { round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import { draftFromProposed, editedChanges, mapChangeField } from "./editApply";
import {
  analysisLine,
  changeRows,
  countdownText,
  isOpen,
  outcomeLine,
  proposalLever,
  proposalState,
  proposerLine,
  undoLeftS,
} from "./proposalModel";

const NOW = 1_793_017_800;

describe("proposalState", () => {
  it.each([
    ["pending", false, "pending"],
    ["pending", true, "stale"],
    ["applied", false, "applied"],
    ["auto_applied", false, "auto_applied"],
    ["refused", false, "refused"],
    ["rejected", false, "rejected"],
    ["expired", false, "expired"],
    ["withdrawn", false, "withdrawn"],
    ["undone", false, "undone"],
  ] as const)("%s (stale=%s) reads %s", (fixture, stale, expected) => {
    const state = proposalState(proposalInState(fixture, NOW), stale);
    expect(state).toBe(expected);
    expect(isOpen(state)).toBe(expected === "pending" || expected === "stale");
  });

  it("reads a grace-timer apply as auto-applied", () => {
    const c2 = roomProposals().find((p) => p.ref === "C-2")!;
    expect(proposalState(c2)).toBe("auto_applied");
  });
});

describe("changeRows", () => {
  it("renders creates as added projects with their charter", () => {
    const rows = changeRows(proposalInState("pending"), round3Snapshot());
    expect(rows.map((r) => r.title)).toEqual(["+ billing-schema", "+ billing-api", "+ billing-ui"]);
    expect(rows[1]!.meta).toBe("P1 · app#temp-main · after billing-schema");
    expect(rows[0]!.lines.map((l) => [l.sign, l.label])).toEqual([
      ["+", "Goal"],
      ["+", "DoD"],
      ["+", "Scope"],
    ]);
  });

  it("diffs an update against the project as it is", () => {
    const p = {
      ...proposalInState("pending"),
      ops: [
        { op: "update", slug: "db-version", patch: { priority: 0, depends_on: ["api-version"] } },
      ],
    };
    const [row] = changeRows(p, round3Snapshot());
    expect(row!.title).toBe("~ db-version");
    expect(row!.lines).toEqual([
      { sign: "~", label: "Priority", before: "P1", after: "P0" },
      { sign: "~", label: "Depends on", before: "", after: "api-version" },
    ]);
  });

  it("names splits, merges, cancels and definition patches", () => {
    const p = {
      ...proposalInState("pending"),
      ops: [
        {
          op: "split",
          slug: "billing",
          into: [{ slug: "a", priority: 1 }, { slug: "b" }],
          keep: "cancel",
        },
        { op: "merge", into: "a", from: ["b", "c"] },
        { op: "cancel", slug: "cache-warm" },
        { op: "replace", path: "cadence.project_lead", value: 10800 },
      ],
    };
    expect(changeRows(p, null).map((r) => [r.title, r.meta])).toEqual([
      ["split billing", "into a, b · cancel the original"],
      ["merge into a", "absorbs b, c"],
      ["− cache-warm", "cancel · previews torn down"],
      ["cadence.project_lead", null],
    ]);
  });
});

describe("lines and levers", () => {
  it("says who proposed it and for whom", () => {
    expect(proposerLine(proposalInState("pending"))).toBe("by Atlas for luka");
  });

  it("sums the planner's analysis", () => {
    expect(analysisLine(proposalInState("pending").analysis)).toBe(
      "planner: 2 hub files · est 5 PRs",
    );
    expect(analysisLine({ cohesion: { a: 0.3 } })).toBe("planner: low cohesion: a");
    expect(analysisLine(null)).toBeNull();
  });

  it("counts down the Undo window", () => {
    const applied = proposalInState("applied", NOW);
    expect(undoLeftS(applied, NOW)).toBe(580);
    expect(undoLeftS(applied, NOW + 600)).toBeNull();
    expect(undoLeftS(proposalInState("pending"), NOW)).toBeNull();
    expect(countdownText(580)).toBe("9:40");
    expect(countdownText(5.2)).toBe("0:06");
  });

  it("writes each closed state's outcome", () => {
    expect(outcomeLine(proposalInState("applied"), "applied")).toBe(
      "Applied by mia · spawning billing-schema",
    );
    expect(outcomeLine(proposalInState("rejected"), "rejected")).toBe(
      "Rejected by luka: “billing waits for Q1; keep the API contract stable”",
    );
    expect(outcomeLine(proposalInState("refused"), "refused")).toBe("Refused: over_caps");
    expect(outcomeLine(proposalInState("undone"), "undone")).toBe("Undone by mia");
  });

  it("needs an owner for tuning and owner-class proposals", () => {
    expect(proposalLever(proposalInState("pending"))).toBe("apply_projects");
    expect(proposalLever({ ...proposalInState("pending"), approval: "owner" })).toBe(
      "apply_tuning",
    );
    expect(proposalLever(roomProposals().find((p) => p.ref === "C-15")!)).toBe("apply_tuning");
  });
});

describe("Edit & apply", () => {
  it("turns a proposed project into a draft and back, keeping fields the form hides", () => {
    const proposal = proposalInState("pending");
    const drafts = proposal.ops.map((op) =>
      draftFromProposed(op.project as Record<string, unknown>),
    );
    drafts[1] = { ...drafts[1]!, slug: "billing-core", title: "Billing core", priority: 0 };
    const changes = editedChanges(proposal, drafts);
    const project = changes[1]!.project as Record<string, unknown>;
    expect(project).toMatchObject({
      slug: "billing-core",
      title: "Billing core",
      priority: 0,
      depends_on: ["billing-schema"],
      kind: "work",
      repo: "caffeinelabs/app",
      base_branch: "temp-main",
    });
    expect(project).not.toHaveProperty("constraints");
    expect(project).not.toHaveProperty("version_path");
    expect(changes[0]).toEqual({
      ...proposal.ops[0],
      project: expect.objectContaining({ slug: "billing-schema" }),
    });
  });

  it("lands a server field error on the draft it names", () => {
    const proposal = proposalInState("pending");
    expect(mapChangeField(proposal, "changes[2].project.slug")).toEqual({
      draft: 2,
      field: "slug",
    });
    expect(mapChangeField(proposal, "changes[0].project.base_branch")).toEqual({
      draft: 0,
      field: "branch",
    });
    expect(mapChangeField(proposal, "rationale")).toBeNull();
  });
});
