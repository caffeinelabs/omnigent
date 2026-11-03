import { describe, expect, it } from "vitest";
import type { DefinitionDoc, ProjectView, ProposalView } from "@/lib/factoryApi";
import { discoveryDefinition } from "../home/__fixtures__/homeFixtures";
import { definitionResponse } from "../insights/__fixtures__/learnFixtures";
import {
  discoveryProposals,
  discoverySnapshot,
  sampleFlagged,
  sampleHunches,
  sampleOpportunities,
  sampleOpportunityDetail,
  sampleReadouts,
  sampleSummary,
  sampleUnsorted,
} from "./__fixtures__/discoveryFixtures";
import {
  bandSections,
  chainCells,
  chainOf,
  filterRows,
  flowModel,
  hunchOutcome,
  latestReadouts,
  ledgerFacts,
  leversFor,
  mapDividers,
  mapPoints,
  parseOpportunityDetail,
  parseOpportunityList,
  parseReadouts,
  parseSignals,
  parseSummary,
  peekTarget,
  pendingBets,
  pendingSources,
  quietSegments,
  rankText,
  sourceStateWord,
  starterChanges,
  starterSummary,
  statusWords,
  triageAction,
} from "./discoveryModel";

const NOW = 1_793_017_800;

function rows() {
  return parseOpportunityList(sampleOpportunities(NOW))!.rows;
}

describe("readers", () => {
  it("reads the summary field by field, absent counts staying unknown", () => {
    const summary = parseSummary(sampleSummary(NOW))!;
    expect(summary.chain).toEqual({
      signalsWeek: 214,
      opportunities: 7,
      bets: 2,
      building: 1,
      shipped: 0,
      measured: 1,
    });
    expect(summary.cutoffs).toEqual({ now: 0.2, next: 0.1 });
    expect(summary.sources.map((s) => s.id)).toEqual(["repo", "trk", "web", "team"]);
    expect(summary.goals[0]).toMatchObject({ id: "G-1", measured: false });
    expect(summary.ledger).toBeNull();
    expect(parseSummary({})!.chain.signalsWeek).toBeNull();
    expect(parseSummary(null)).toBeNull();
    expect(parseSummary([])).toBeNull();
  });

  it("reads the prediction ledger once the summary carries one (A7)", () => {
    const summary = parseSummary({
      ...sampleSummary(NOW),
      ledger: {
        readouts: 6,
        inside: 4,
        by_level: [{ level: "moderate", n: 5, won: 3 }],
        discovered: { won: 4, total: 6 },
        requested: { shipped: 9, measured: 1 },
      },
    })!;
    expect(summary.ledger).toEqual({
      readouts: 6,
      inside: 4,
      byLevel: [{ level: "moderate", n: 5, won: 3 }],
      minPerLevel: 5,
      discovered: { won: 4, total: 6 },
      requested: { shipped: 9, measured: 1 },
    });
  });

  it("reads opportunities, skipping rows without an id or a title", () => {
    const list = parseOpportunityList({
      ...sampleOpportunities(NOW),
      data: [...sampleOpportunities(NOW).data, { id: "O-1" }, 7],
    })!;
    expect(list.rows).toHaveLength(8);
    expect(list.cutoffs).toEqual({ now: 0.2, next: 0.1 });
    const o15 = list.rows.find((r) => r.id === "O-15")!;
    expect(o15).toMatchObject({ band: "now", rank: 0.23, quadrant: "bet", pinned: true });
    expect(o15.evidence).toMatchObject({ level: "moderate", accounts: 23, hunches: 1 });
    expect(o15.bet).toEqual({ proposal: "C-31", status: "pending", kind: "outcome" });
    expect(o15.parts.map((p) => p.name)).toEqual(["reach", "intensity", "fit", "confidence"]);
    expect(parseOpportunityList({ nope: 1 })).toBeNull();
  });

  it("reads one opportunity's panel: quotes, why-chain, lifecycle, bet note", () => {
    const detail = parseOpportunityDetail(sampleOpportunityDetail("O-15", NOW))!;
    expect(detail.quotes.said).toHaveLength(3);
    expect(detail.quotes.said.every((q) => q.verified && !q.hunch)).toBe(true);
    expect(detail.quotes.hunches[0]).toMatchObject({ hunch: true, author: "ana" });
    expect(detail.why.map((w) => [w.kind, w.missing])).toEqual([
      ["evidence", false],
      ["opportunity", false],
      ["bet", false],
      ["project", true],
      ["readout", true],
    ]);
    expect(detail.lifecycle.find((s) => s.current)?.step).toBe("proposed");
    const idea = parseOpportunityDetail(sampleOpportunityDetail("O-24", NOW))!;
    expect(idea.betNote?.ideas).toHaveLength(3);
    expect(idea.betNote?.cheapest).toContain("(sample)");
  });

  it("never makes an injection suspect actionable, whatever the server says", () => {
    const flagged = parseSignals({ data: sampleFlagged(NOW) })!;
    expect(flagged[0]).toMatchObject({ injection: true, actionable: false });
    const lying = parseSignals({
      data: [{ ...sampleFlagged(NOW)[0], actionable: true, excluded: null }],
    })!;
    expect(lying[0]!.actionable).toBe(false);
    const flagOnly = parseSignals({
      data: [{ id: "S-9", source: "web", flags: ["injection_suspect"], actionable: true }],
    })!;
    expect(flagOnly[0]!.actionable).toBe(false);
    expect(parseSignals({ data: sampleUnsorted(NOW) })!.every((s) => s.actionable)).toBe(true);
  });

  it("reads readouts in the plan's shape and in flatter ones", () => {
    const [won] = parseReadouts(sampleReadouts())!;
    expect(won).toMatchObject({
      id: "R-1",
      proposal: "C-20",
      verdict: "won",
      low: -40,
      high: -20,
      unit: "%",
      actual: -31,
      inside: true,
    });
    const [flat] = parseReadouts([{ ref: "R-2", low: 1, high: 4, actual: 0.2, verdict: "flat" }])!;
    expect(flat).toMatchObject({ id: "R-2", inside: false, verdict: "flat" });
    expect(parseReadouts({})).toBeNull();
  });
});

describe("bands, status and filters", () => {
  it("groups by band in table order with the rubric's cut-offs in each header", () => {
    const sections = bandSections(rows(), { now: 0.2, next: 0.1 });
    expect(sections.map((s) => [s.band, s.rows.map((r) => r.id)])).toEqual([
      ["now", ["O-3", "O-15"]],
      ["next", ["O-12", "O-7", "O-24"]],
      ["later", ["O-18"]],
      ["parked", ["O-21"]],
      ["closed", ["O-9"]],
    ]);
    expect(sections[0]!.note).toBe("score ≥ .20");
    expect(sections[1]!.note).toBe("score ≥ .10");
    expect(bandSections(rows(), { now: null, next: null })[0]!.note).toBe("");
  });

  it("says what happens next, amber only where a person is waited on", () => {
    const byId = new Map(rows().map((r) => [r.id, r]));
    expect(statusWords(byId.get("O-15")!, NOW)).toMatchObject({
      text: "Bet waits for you",
      waits: true,
    });
    expect(statusWords(byId.get("O-12")!, NOW)).toMatchObject({
      text: "Test: the other lead is co-signing",
      waits: false,
    });
    expect(statusWords(byId.get("O-3")!, NOW)).toMatchObject({ text: "Building", waits: false });
    expect(statusWords(byId.get("O-24")!, NOW)).toEqual({
      text: "Idea · test first",
      waits: false,
      detail: "Scout researching · due in 2 days · $2 ask",
    });
    expect(statusWords(byId.get("O-21")!, NOW)).toMatchObject({
      text: "Parked under a non-goal",
      detail: "Reporting for large teams (sample)",
    });
    expect(rankText(0.23)).toBe(".23");
    expect(rankText(null)).toBe("—");
  });

  it("filters by chain count, segment and source", () => {
    const all = rows();
    expect(filterRows(all, { chain: "bets" }).map((r) => r.id)).toEqual(["O-15", "O-12"]);
    expect(filterRows(all, { chain: "building" }).map((r) => r.id)).toEqual(["O-3"]);
    expect(filterRows(all, { chain: "measured" }).map((r) => r.id)).toEqual(["O-9"]);
    expect(filterRows(all, { chain: "opportunities" })).toHaveLength(7);
    expect(filterRows(all, { segment: "teams" }).map((r) => r.id)).toEqual(["O-21"]);
    expect(filterRows(all, { source: "trk" }).map((r) => r.id)).toEqual([
      "O-3",
      "O-15",
      "O-7",
      "O-21",
      "O-9",
    ]);
  });

  it("offers only the levers a status allows", () => {
    const byId = new Map(rows().map((r) => [r.id, r]));
    expect(leversFor(byId.get("O-7")!)).toEqual({
      vote: true,
      plan: true,
      park: true,
      kill: true,
      research: true,
    });
    expect(leversFor(byId.get("O-15")!).plan).toBe(false);
    expect(leversFor(byId.get("O-21")!)).toMatchObject({ plan: false, park: false });
    expect(leversFor(byId.get("O-9")!)).toMatchObject({ vote: false, plan: false, kill: true });
  });

  it("counts the chain, grey zeros and amber bets", () => {
    const cells = chainCells(parseSummary(sampleSummary(NOW))!.chain, { betsWaiting: true });
    expect(cells.map((c) => `${c.count} ${c.label}`)).toEqual([
      "214 signals this week",
      "7 opportunities",
      "2 bets",
      "1 building",
      "0 shipped",
      "1 measured",
    ]);
    expect(cells.find((c) => c.key === "bets")!.waiting).toBe(true);
    expect(chainCells(parseSummary({})!.chain).every((c) => c.count === 0)).toBe(true);
  });

  it("names a source's state and the segments heard least", () => {
    expect(sourceStateWord({ state: "trial", health: "ok" })).toBe("trial");
    expect(sourceStateWord({ state: "active", health: "failing" })).toBe("on hold after failures");
    expect(sourceStateWord({ state: "paused", health: "ok" })).toBe("paused");
    expect(sourceStateWord({ state: "active", health: "ok" })).toBe("on");
    const quiet = quietSegments(parseSummary(sampleSummary(NOW))!.coverage);
    expect(quiet.map((c) => [c.segment, c.signals30d])).toEqual([
      ["left", 0],
      ["teams", 2],
    ]);
  });
});

describe("triage keys", () => {
  it("maps j k Enter v n p x, and ignores modifiers and fields", () => {
    expect(["j", "k", "Enter", "v", "n", "p", "x"].map((key) => triageAction({ key }))).toEqual([
      "down",
      "up",
      "open",
      "vote",
      "plan",
      "park",
      "kill",
    ]);
    expect(triageAction({ key: "v", metaKey: true })).toBeNull();
    expect(triageAction({ key: "q" })).toBeNull();
    const input = document.createElement("input");
    expect(triageAction({ key: "v", target: input })).toBeNull();
  });
});

describe("flow", () => {
  function model() {
    const snapshot = discoverySnapshot();
    return flowModel({
      sources: parseSummary(sampleSummary(NOW))!.sources,
      rows: rows(),
      proposals: discoveryProposals(snapshot),
      snapshot,
      readouts: parseReadouts(sampleReadouts())!,
      pendingSources: pendingSources(discoveryProposals(snapshot)),
    });
  }

  it("draws fixed lanes: sources, Now and Next with Later folded, bets, projects, readouts", () => {
    const m = model();
    const lane = (n: number) => m.nodes.filter((x) => x.lane === n).map((x) => x.id);
    expect(lane(0)).toEqual([
      "source:repo",
      "source:trk",
      "source:web",
      "source:team",
      "source:ana",
    ]);
    expect(lane(1)).toEqual(["O-3", "O-15", "O-12", "O-7", "O-24", "later"]);
    expect(lane(2)).toEqual(["C-31", "C-34", "requested"]);
    expect(m.nodes.find((n) => n.id === "later")!.title).toBe("Later and parked: 2");
    expect(m.nodes.find((n) => n.id === "source:ana")!.look).toBe("amber");
    expect(m.nodes.find((n) => n.id === "C-31")!.look).toBe("amber");
    expect(lane(4)).toContain("readout:R-1");
    expect(lane(4)).toContain("not-measured");
  });

  it("weights evidence lines by signals, dots hunches and requested work, dashes what is not measured", () => {
    const m = model();
    expect(m.edges).toContainEqual({ from: "source:repo", to: "O-3", weight: 22, style: "solid" });
    expect(m.edges).toContainEqual({ from: "source:team", to: "O-15", weight: 1, style: "dotted" });
    expect(m.edges).toContainEqual({
      from: "requested",
      to: "project:api-version",
      weight: 1,
      style: "dotted",
    });
    expect(m.edges).toContainEqual({
      from: "project:api-version",
      to: "not-measured",
      weight: 1,
      style: "dashed",
    });
    expect(m.edges).toContainEqual({
      from: "project:ux-badge",
      to: "pending:C-24",
      weight: 2,
      style: "dashed",
    });
    expect(m.edges.find((e) => e.back)).toBeUndefined();
  });

  it("walks a selection's chain upstream and downstream, never through a back curve", () => {
    const m = model();
    const lit = chainOf("O-15", m);
    expect([...lit].sort()).toEqual(
      ["C-31", "O-15", "source:repo", "source:team", "source:trk", "source:web"].sort(),
    );
    expect(lit.has("O-3")).toBe(false);
    const fromProject = chainOf("project:ux-badge", m);
    expect(fromProject.has("O-3")).toBe(true);
    expect(fromProject.has("source:repo")).toBe(true);
    expect(fromProject.has("pending:C-24")).toBe(true);
    expect(fromProject.has("O-15")).toBe(false);
  });

  it("links a readout back to its opportunity when that opportunity is drawn", () => {
    const snapshot = discoverySnapshot();
    const m = flowModel({
      sources: [],
      rows: rows().map((r) => (r.id === "O-9" ? { ...r, band: "now" as const } : r)),
      proposals: [],
      snapshot,
      readouts: parseReadouts(sampleReadouts())!,
    });
    expect(m.edges).toContainEqual({
      from: "readout:R-1",
      to: "O-9",
      weight: 1.5,
      style: "dashed",
      back: true,
    });
    expect(m.edges).toContainEqual({
      from: "project:docs-factory-envs",
      to: "readout:R-1",
      weight: 2,
      style: "solid",
    });
    // The back curve is drawn but walks nothing: O-3's chain never reaches this readout.
    expect(chainOf("O-3", m).has("readout:R-1")).toBe(false);
  });
});

describe("map, impact and the ledger", () => {
  it("plots open opportunities with the rubric's dividers", () => {
    const points = mapPoints(rows());
    expect(points.map((p) => p.id)).toEqual(["O-3", "O-15", "O-12", "O-7", "O-24", "O-18"]);
    expect(points.find((p) => p.id === "O-3")!.tone).toBe("building");
    expect(points.find((p) => p.id === "O-15")!.tone).toBe("waits");
    expect(points.find((p) => p.id === "O-7")).toMatchObject({ tone: "data", confidence: 0.5 });
    const effective = {
      discovery: { rubric: { map: { value_high: 0.4, confidence_high: 0.5 } } },
    } as unknown as DefinitionDoc;
    expect(mapDividers(effective)).toEqual({ value: 0.4, confidence: 0.5 });
    expect(mapDividers(null)).toBeNull();
  });

  it("lists bets waiting for their readout, from proposals and from frozen charters", () => {
    const snapshot = discoverySnapshot();
    const bets = pendingBets(
      discoveryProposals(snapshot),
      snapshot.projects,
      parseReadouts(sampleReadouts())!,
    );
    expect(bets.map((b) => [b.proposal, b.state, b.waits])).toEqual([
      ["C-31", "waits for you", true],
      ["C-34", "co-signing", false],
      ["C-24", "building", false],
    ]);
  });

  it("counts the ledger from readouts until the server keeps one, requested work never a win", () => {
    const snapshot = discoverySnapshot();
    const facts = ledgerFacts(null, parseReadouts(sampleReadouts())!, snapshot.projects);
    expect(facts).toMatchObject({
      readouts: 1,
      inside: 1,
      discovered: { won: 1, total: 1 },
      requested: { measured: 0 },
    });
    expect(facts.levelLine).toBe("not enough data (needs 5 readouts per level)");
    const enough = ledgerFacts(
      {
        readouts: 10,
        inside: 7,
        byLevel: [{ level: "moderate", n: 6, won: 4 }],
        minPerLevel: 5,
        discovered: { won: 6, total: 10 },
        requested: null,
      },
      [],
      [] as ProjectView[],
    );
    expect(enough.levelLine).toBe("moderate 4 of 6 won");
    expect(enough.discovered).toEqual({ won: 6, total: 10 });
  });

  it("keeps the latest readout of each bet", () => {
    const readouts = parseReadouts([
      { id: "R-1", proposal: "C-20", day: 7 },
      { id: "R-2", proposal: "C-20", day: 28 },
      { id: "R-3", proposal: "C-21", day: 7 },
    ])!;
    expect(latestReadouts(readouts).map((r) => r.id)).toEqual(["R-2", "R-3"]);
  });

  it("says what came of a hunch", () => {
    const [ana, atlas, mia] = parseSignals({ data: sampleHunches(NOW) })!;
    expect(hunchOutcome(ana!, rows())).toBe("Evidence found: 23 accounts. Bet waits for you.");
    expect(hunchOutcome(atlas!, rows())).toBe(
      "Evidence found: 30 accounts. Test: the other lead is co-signing.",
    );
    expect(hunchOutcome(mia!, rows())).toMatch(/^No opportunity yet/);
  });
});

describe("side panels and pending sources", () => {
  it("reads the panel from the address", () => {
    expect(peekTarget("O-15")).toEqual({ kind: "opportunity", id: "O-15" });
    expect(peekTarget("c-31")).toEqual({ kind: "bet", id: "C-31" });
    expect(peekTarget("source:repo")).toEqual({ kind: "source", id: "repo" });
    expect(peekTarget("readout:R-1")).toEqual({ kind: "readout", id: "R-1" });
    expect(peekTarget("merge")).toBeNull();
    expect(peekTarget(null)).toBeNull();
  });

  it("finds sources an open proposal would add", () => {
    const pending = pendingSources(discoveryProposals(discoverySnapshot()));
    expect(pending).toEqual([
      expect.objectContaining({
        id: "ana",
        label: "Product analytics (sample)",
        proposal: "C-36",
        by: "Scout",
      }),
    ]);
    const applied = {
      ...discoveryProposals(discoverySnapshot()).find((p) => p.ref === "C-36")!,
      status: "applied",
    } as ProposalView;
    expect(pendingSources([applied])).toEqual([]);
  });
});

describe("the discovery starter", () => {
  it("turns discovery on with three sources that need no secret", () => {
    const changes = starterChanges(definitionResponse().effective);
    expect(changes[0]).toEqual({ op: "replace", path: "discovery.enabled", value: true });
    expect(changes.slice(1).map((c) => (c.value as { kind: string }).kind)).toEqual([
      "repo",
      "web",
      "team",
    ]);
    expect(changes.every((c) => !JSON.stringify(c).match(/key|token|secret/i))).toBe(true);
  });

  it("adds only the starter sources a factory does not have yet", () => {
    const on = discoveryDefinition().effective;
    expect(starterChanges(on)).toEqual([]);
    const summary = starterSummary(definitionResponse().effective);
    expect(summary.reads.map((r) => r.label)).toEqual([
      "The factory's repositories",
      "The open web",
      "The factory's people",
    ]);
  });
});
