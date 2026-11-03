import { describe, expect, it } from "vitest";
import {
  previewFocus,
  rubricTrial,
  steerAllProposals,
  steerDefinition,
  steerProposals,
  steerVersions,
} from "./__fixtures__/steerFixtures";
import { STARTER_SOURCES, stageStarter } from "./discoveryStarter";
import {
  EMPTY_STEER,
  UNDO_WINDOW_S,
  addItemEdit,
  agentProposals,
  dropEdit,
  isStale,
  movesRank,
  outlineValue,
  proposalIntact,
  rebaseStaging,
  removeItemEdit,
  replaceEdit,
  rerankRows,
  sectionOfPath,
  stageEdit,
  stageProposal,
  stageRemoveItem,
  stageRestore,
  stagedChanges,
  stagedCount,
  stagedList,
  stagedSections,
  trialLocks,
  trialRule,
  lockedBy,
  undoLabel,
  undoLeft,
  versionRows,
  visibleGroups,
  visibleSections,
  withBase,
} from "./steerModel";

const definition = steerDefinition();
const doc = definition.effective;

describe("sections", () => {
  it("hides the discovery sections while discovery is off, and keeps strategy and the profile", () => {
    expect(visibleSections(true)).toContain("sources");
    expect(visibleSections(true)).toContain("rubric");
    const off = visibleSections(false);
    expect(off).not.toContain("sources");
    expect(off).not.toContain("rubric");
    expect(off).toEqual(expect.arrayContaining(["strategy", "product", "overview", "versions"]));
    expect(visibleGroups(false).map((g) => g.title)).not.toContain("Discovery method");
  });

  it("puts every definition path in the section that edits it", () => {
    expect(sectionOfPath("strategy.focus_segments")).toBe("strategy");
    expect(sectionOfPath("product.delivery.merge_policy")).toBe("product");
    expect(sectionOfPath("discovery.sources.forum.state")).toBe("sources");
    expect(sectionOfPath("discovery.rubric.bands.now")).toBe("rubric");
    expect(sectionOfPath("discovery.budgets.usd_per_day")).toBe("budgets");
    expect(sectionOfPath("briefs.scout")).toBe("roles");
    expect(sectionOfPath("models.ic")).toBe("roles");
    expect(sectionOfPath("team.templates.standard.roles")).toBe("templates");
    expect(sectionOfPath("rules/R-3")).toBe("alerts");
    expect(sectionOfPath("cadence.lead")).toBe("cadence");
    expect(sectionOfPath("loops.implement.attempts")).toBe("budgets");
  });

  it("states each section's value in one line", () => {
    const facts = {
      definition,
      discoveryOn: true,
      sourcesWaiting: 1,
      templates: 4,
      trialsRunning: 1,
    };
    expect(outlineValue("strategy", facts)).toBe("1 goal · focus New users");
    expect(outlineValue("product", facts)).toBe("Sample app (sample) · 3 segments");
    expect(outlineValue("sources", facts)).toBe("4 on · 1 waits for you");
    expect(outlineValue("rubric", facts)).toBe("off-focus × 0.5 · Now ≥ .20 · on trial");
    expect(outlineValue("versions", facts)).toBe("v12 · 1 trial running");
    expect(
      outlineValue("strategy", { ...facts, definition: steerDefinition({ noGoal: true }) }),
    ).toBe("no goal yet");
  });
});

describe("staging", () => {
  const focus = (after: string[]) =>
    replaceEdit("strategy", "strategy.focus_segments", "Focus segments", ["new-users"], after);

  it("stages per section and compiles everything into one change list (one version)", () => {
    let staging = stageEdit(EMPTY_STEER, focus(["new-users", "active-builders"]));
    staging = stageEdit(
      staging,
      replaceEdit("rubric", "discovery.rubric.off_focus_factor", "Fit outside focus", 0.5, 0.8),
    );
    staging = stageEdit(
      staging,
      replaceEdit("budgets", "budgets.factory_usd_per_day", "Factory per day", 240, 300),
    );
    expect([...stagedSections(staging)].sort()).toEqual(["budgets", "rubric", "strategy"]);
    expect(stagedChanges(staging)).toEqual([
      { op: "replace", path: "strategy.focus_segments", value: ["new-users", "active-builders"] },
      { op: "replace", path: "discovery.rubric.off_focus_factor", value: 0.8 },
      { op: "replace", path: "budgets.factory_usd_per_day", value: 300 },
    ]);
    expect(stagedCount(staging)).toBe(3);
  });

  it("keeps the first before, and an edit back to the value in force unstages it", () => {
    let staging = stageEdit(EMPTY_STEER, focus(["new-users", "active-builders"]));
    staging = stageEdit(staging, focus(["new-users", "active-builders", "teams"]));
    expect(staging.edits).toHaveLength(1);
    expect(staging.edits[0]!.before).toEqual(["new-users"]);
    staging = stageEdit(staging, focus(["new-users"]));
    expect(staging.edits).toHaveLength(0);
  });

  it("adds and removes list items by id; removing a staged add drops it", () => {
    let staging = stageEdit(
      EMPTY_STEER,
      addItemEdit(EMPTY_STEER, "product", "product.segments", "Segment", {
        id: "pros",
        label: "Pros",
      }),
    );
    staging = stageRemoveItem(
      staging,
      "product",
      "product.segments",
      "teams",
      "Segment: Teams",
      doc.product!.segments[2],
    );
    staging = stageEdit(
      staging,
      replaceEdit(
        "product",
        "product.segments.new-users.rule",
        "Rule",
        "account age 14 days or less",
        "7 days",
      ),
    );
    const list = stagedList(staging, "product.segments", doc.product!.segments);
    expect(list.map((s) => [s.id, s.staged ?? null])).toEqual([
      ["new-users", "changed"],
      ["active-builders", null],
      ["teams", "removed"],
      ["pros", "added"],
    ]);
    expect(list[0]!.rule).toBe("7 days");
    expect(stagedChanges(staging)).toEqual([
      { op: "add", path: "product.segments", value: { id: "pros", label: "Pros" } },
      { op: "remove", path: "product.segments.teams" },
      { op: "replace", path: "product.segments.new-users.rule", value: "7 days" },
    ]);
    staging = stageRemoveItem(
      staging,
      "product",
      "product.segments",
      "pros",
      "Segment: Pros",
      null,
    );
    expect(staging.edits.map((e) => e.key)).toEqual([
      "product.segments.teams",
      "product.segments.new-users.rule",
    ]);
  });

  it("numbers goals the server numbers: a second new goal gets its own key", () => {
    const first = addItemEdit(EMPTY_STEER, "strategy", "strategy.goals", "New goal", {
      statement: "a",
    });
    const one = stageEdit(EMPTY_STEER, first);
    const second = addItemEdit(one, "strategy", "strategy.goals", "New goal", { statement: "b" });
    expect([first.key, second.key]).toEqual(["strategy.goals+1", "strategy.goals+2"]);
    expect(stagedChanges(stageEdit(one, second))).toHaveLength(2);
    expect(dropEdit(stageEdit(one, second), first.key).edits.map((e) => e.key)).toEqual([
      second.key,
    ]);
  });

  it("stages a restore alone, and nothing else stages on top of it", () => {
    const restore = stageRestore(10);
    expect(stagedCount(restore)).toBe(1);
    expect(stagedChanges(restore)).toEqual([]);
    expect(stagedSections(restore)).toEqual(new Set(["versions"]));
    expect(stageEdit(restore, focus(["teams"]))).toBe(restore);
  });
});

describe("trials", () => {
  it("never offers a trial for strategy or the product profile: they are judged on outcomes", () => {
    const rule = trialRule(["strategy.focus_segments"], definition);
    expect(rule).toMatchObject({
      code: "not_trialable",
      ok: false,
      head: "No trial for strategy.",
    });
    expect(rule.text).toContain("Trials judge delivery metrics");
    expect(trialRule(["product.name"], definition).head).toBe("No trial for the product profile.");
  });

  it("refuses a second trial on a block a running trial holds, and says which and why", () => {
    const def = { ...definition, not_trialable: [] };
    const rule = trialRule(["discovery.rubric.off_focus_factor"], def, {
      nowS: rubricTrial().applied_at!,
    });
    expect(rule).toMatchObject({ code: "trial_overlap", ok: false, trial: "C-33" });
    expect(rule.head).toBe("No second trial on discovery.");
    expect(rule.text).toContain("C-33 is already trialing it");
    expect(rule.text).toContain("Apply directly, or wait for C-33 to end.");
  });

  it("offers a trial for today's knobs, never for a restore", () => {
    expect(trialRule(["cadence.lead"], definition).ok).toBe(true);
    expect(trialRule(["cadence.lead"], definition, { restore: 10 }).code).toBe("restore");
  });

  it("locks the field a running trial changed", () => {
    const locks = trialLocks(definition.on_trial);
    expect(lockedBy(locks, "discovery.rubric.decay_half_life_days")).toBe("C-33");
    expect(lockedBy(locks, "discovery.rubric.off_focus_factor")).toBeNull();
  });
});

describe("the re-rank", () => {
  it("reads band moves and position moves from the preview", () => {
    const rows = rerankRows(previewFocus().rerank);
    expect(rows.map((r) => [r.ref, r.move])).toEqual([
      ["O-7", "up"],
      ["O-24", "up"],
      ["O-15", null],
      ["O-18", "up"],
    ]);
    expect(rerankRows([{ nope: true }, null, { opportunity: "O-1" }])).toEqual([]);
  });

  it("shows the ranking for strategy, rubric and segment changes only", () => {
    expect(movesRank(["strategy.focus_segments"])).toBe(true);
    expect(movesRank(["discovery.rubric.bands.now"])).toBe(true);
    expect(movesRank(["budgets.factory_usd_per_day"])).toBe(false);
  });
});

describe("agents' proposals", () => {
  it("lists pending definition proposals from agents, and stages one as proposed", () => {
    const pending = agentProposals(steerAllProposals());
    expect(pending.map((p) => p.ref)).toEqual(["C-32", "C-35", "C-36"]);
    const c35 = steerProposals()[1]!;
    const staged = stageProposal(c35, doc);
    expect(staged.proposal?.ref).toBe("C-35");
    expect(proposalIntact(staged)).toBe(true);
    expect(staged.edits[0]).toMatchObject({ section: "roles", before: doc.briefs!.scout });
    const changed = stageEdit(
      staged,
      replaceEdit("roles", "briefs.scout", "Scout's brief", doc.briefs!.scout, "Something else."),
    );
    expect(proposalIntact(changed)).toBe(false);
  });
});

describe("versions", () => {
  it("joins each version with its change: trial, undone, restores", () => {
    const rows = versionRows(steerVersions().data, steerAllProposals());
    expect(rows.map((r) => r.version)).toEqual([12, 11, 10, 9, 8]);
    expect(rows[0]).toMatchObject({ pinned: true, ref: "C-33", trial: "on trial", by: "Sentinel" });
    expect(rows[1]).toMatchObject({ undone: "mia" });
    expect(rows[2]).toMatchObject({ via: "restore", restoredFrom: 8 });
    expect(rows[4]).toMatchObject({ via: "seeded" });
  });
});

describe("the version a staging was staged on", () => {
  const focus = (after: string[]) =>
    replaceEdit("strategy", "strategy.focus_segments", "Focus segments", ["new-users"], after);

  it("records the version in force when the first thing is staged, and forgets it when emptied", () => {
    const staged = withBase(stageEdit(EMPTY_STEER, focus(["new-users", "teams"])), 12);
    expect(staged.base).toBe(12);
    expect(withBase(stageEdit(staged, focus(["teams"])), 13).base).toBe(12);
    expect(withBase(dropEdit(staged, "strategy.focus_segments"), 13)).toEqual(EMPTY_STEER);
    expect(withBase(stageRestore(10), 12).base).toBe(12);
    expect(isStale(staged, 12)).toBe(false);
    expect(isStale(staged, 13)).toBe(true);
    expect(isStale(EMPTY_STEER, 13)).toBe(false);
  });

  it("rebases on the version in force: befores read again, edits it already made dropped", () => {
    const moved = structuredClone(doc);
    moved.strategy!.focus_segments = ["new-users", "teams"];
    moved.strategy!.non_goals = ["Enterprise billing (sample)"];
    let staged = withBase(stageEdit(EMPTY_STEER, focus(["new-users", "active-builders"])), 12);
    staged = stageEdit(
      staged,
      replaceEdit(
        "strategy",
        "strategy.non_goals",
        "Non-goals",
        [],
        ["Enterprise billing (sample)"],
      ),
    );
    staged = stageEdit(
      staged,
      removeItemEdit("product", "product.segments", "gone", "Segment gone", { id: "gone" }),
    );
    const rebased = rebaseStaging(staged, moved, 13);
    expect(rebased.base).toBe(13);
    expect(rebased.edits.map((e) => e.key)).toEqual(["strategy.focus_segments"]);
    expect(rebased.edits[0]).toMatchObject({
      before: ["new-users", "teams"],
      after: ["new-users", "active-builders"],
    });
    expect(isStale(rebased, 13)).toBe(false);
    // Nothing left to stage: the staging empties.
    expect(
      rebaseStaging(withBase(stageEdit(EMPTY_STEER, focus(["new-users", "teams"])), 12), moved, 13),
    ).toEqual(EMPTY_STEER);
  });
});

describe("undo of the version in force", () => {
  const rows = () => {
    const [latest] = versionRows(steerVersions().data);
    return { ...latest!, version: 13, parent: 12, createdBy: "mia@example.com", undone: null };
  };
  const at = 1_000_000;

  it("lasts 10 minutes, for owners and for the editor who made it", () => {
    const row = { ...rows(), at };
    const mia = { userId: "mia@example.com", owner: false };
    expect(undoLeft(row, at + 120, mia)).toBe(UNDO_WINDOW_S - 120);
    expect(undoLabel(UNDO_WINDOW_S - 120)).toBe("Undo (8 min left)");
    expect(undoLeft(row, at + UNDO_WINDOW_S, mia)).toBeNull();
    expect(undoLeft(row, at + 60, { userId: "lena@example.com", owner: false })).toBeNull();
    expect(undoLeft(row, at + 60, { userId: "lena@example.com", owner: true })).toBe(540);
  });

  it("is not offered for a version undone, not in force, or with nothing before it", () => {
    const row = { ...rows(), at };
    const owner = { userId: "lena@example.com", owner: true };
    expect(undoLeft({ ...row, undone: "mia" }, at + 1, owner)).toBeNull();
    expect(undoLeft({ ...row, pinned: false }, at + 1, owner)).toBeNull();
    expect(undoLeft({ ...row, parent: null }, at + 1, owner)).toBeNull();
  });
});

describe("the discovery starter", () => {
  it("turns discovery on with the three sources that need no secret", () => {
    const off = steerDefinition({ discovery: false }).effective;
    const staged = stageStarter(EMPTY_STEER, off);
    const changes = stagedChanges(staged);
    expect(changes[0]).toEqual({ op: "replace", path: "discovery.enabled", value: true });
    expect(
      changes.filter((c) => c.op === "add").map((c) => (c.value as { kind: string }).kind),
    ).toEqual(["repo", "web", "team"]);
    // Nothing names a product: the repos are the factory's own.
    expect(JSON.stringify(STARTER_SOURCES)).not.toMatch(/example|sample-org/);
  });

  it("leaves a source the factory already has alone", () => {
    const withRepo = steerDefinition({ discovery: false }).effective;
    withRepo.discovery!.sources = [{ ...STARTER_SOURCES[0]! } as never];
    const kinds = stagedChanges(stageStarter(EMPTY_STEER, withRepo))
      .filter((c) => c.op === "add")
      .map((c) => (c.value as { id: string }).id);
    expect(kinds).toEqual(["web", "team"]);
  });
});
