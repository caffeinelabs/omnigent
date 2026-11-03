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
  findReadout,
  flowModel,
  hunchOutcome,
  hunchWeight,
  latestReadouts,
  ledgerFacts,
  leversFor,
  mapDividers,
  mapPoints,
  mayWithdrawHunch,
  parseOpportunityDetail,
  parseLedger,
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
  starterReads,
  starterSummary,
  starterWaiting,
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

  it("reads the prediction ledger in the shape A7 serves it", () => {
    const ledger = parseLedger({
      readouts: 6,
      inside: { n: 4, of: 6, share: 0.6667 },
      win_rate_by_level: {
        moderate: { value: 0.6, n: 5, enough: true },
        weak: { value: 1, n: 1, enough: false },
      },
      min_per_level: 5,
      by_origin: { discovered: { value: 0.5, n: 4 } },
      requested: { shipped: 3, measured: 0 },
    });
    expect(ledger).toEqual({
      readouts: 6,
      inside: 4,
      byLevel: [
        { level: "moderate", n: 5, won: 3 },
        { level: "weak", n: 1, won: 1 },
      ],
      minPerLevel: 5,
      discovered: { won: 2, total: 4 },
      requested: { shipped: 3, measured: 0 },
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
    expect(detail.quotes.hunches[0]).toMatchObject({ hunch: true, author: "ana@example.com" });
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

  it("reads what A7 serves: status, options, why, waiting and unmeasured guardrails", () => {
    const [kept, toDecide, toAnswer, waiting] = parseReadouts(sampleReadouts({ more: true }))!;
    expect(kept).toMatchObject({ key: "C-20-d7", status: "decided", options: [] });
    expect(kept!.guardrails).toEqual([
      { name: "support tickets per 1,000 accounts (sample)", held: true, measured: true },
    ]);
    expect(toDecide).toMatchObject({
      status: "written",
      recommendation: "iterate",
      actual: -8,
      inside: false,
      why: expect.stringContaining("(guardrails not measured)"),
    });
    expect(toDecide!.options.map((o) => [o.decision, o.recommended, o.against])).toEqual([
      ["iterate", true, false],
      ["drop", false, false],
      ["keep", false, true],
      ["extend", false, true],
    ]);
    expect(toDecide!.guardrails[0]).toMatchObject({ measured: false });
    expect(toAnswer).toMatchObject({ status: "awaiting_answer", method: "human_verdict" });
    expect(waiting!.waiting).toBe(
      "3 signals from the window are not sorted into an opportunity yet",
    );
    const all = parseReadouts(sampleReadouts({ more: true }))!;
    expect(findReadout(all, "C-21-d28")?.id).toBe("R-2");
    expect(findReadout(all, "R-3")?.key).toBe("C-22-d7");
    expect(findReadout(all, "C-99-d7")).toBeNull();
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
    expect(sourceStateWord({ state: "pending", health: "new" })).toBe("waits for an owner");
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

  it("leaves keys to a dialog, a menu or a list that has focus", () => {
    for (const role of ["dialog", "menu", "listbox"]) {
      const owner = document.createElement("div");
      owner.setAttribute("role", role);
      const button = document.createElement("button");
      owner.append(button);
      expect(triageAction({ key: "p", target: button })).toBeNull();
    }
    expect(triageAction({ key: "p", target: document.createElement("button") })).toBe("park");
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

  it("draws a source that waits for an owner's approval like a pending one", () => {
    const summary = parseSummary(sampleSummary(0, { pendingWeb: true }))!;
    const m = flowModel({
      sources: summary.sources,
      rows: rows(),
      proposals: [],
      snapshot: discoverySnapshot(),
      readouts: [],
    });
    const web = m.nodes.find((n) => n.id === "source:web")!;
    expect(web).toMatchObject({ look: "amber", detail: "web · waits for an owner" });
    expect(m.nodes.find((n) => n.id === "source:repo")!.look).toBe("plain");
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

  it("lets a person's hunch be withdrawn by its author or an owner, never an agent's idea", () => {
    const [ana, atlas, mia] = parseSignals({ data: sampleHunches(NOW) })!;
    const operator = { userId: "mia@example.com", owner: false };
    const owner = { userId: "lena@example.com", owner: true };
    expect(mayWithdrawHunch(mia!, operator)).toBe(true);
    expect(mayWithdrawHunch(ana!, operator)).toBe(false);
    expect(mayWithdrawHunch(ana!, owner)).toBe(true);
    expect(mayWithdrawHunch(atlas!, owner)).toBe(false);
    // A repo post by a team author (no author) and an agent's hunch stay too.
    expect(mayWithdrawHunch({ ...ana!, author: null }, owner)).toBe(false);
    expect(mayWithdrawHunch({ ...ana!, authorKind: "agent" }, owner)).toBe(false);
    expect(mayWithdrawHunch({ ...mia!, gone: "withdrawn" }, operator)).toBe(false);
  });

  it("reads the hunch weight from the rubric, 0.1 when it sets none", () => {
    expect(hunchWeight(null)).toBe(0.1);
    const doc = {
      discovery: { rubric: { ladder: { hunch: 0.25 } } },
    } as unknown as DefinitionDoc;
    expect(hunchWeight(doc)).toBe(0.25);
    expect(starterReads("team", 0.25)).toContain("weight 0.25");
    expect(starterReads("calendar")).toBeNull();
  });

  it("counts requested work as measured only once a readout exists", () => {
    const snapshot = discoverySnapshot();
    const requested = snapshot.projects.filter(
      (p) => p.kind !== "integration" && p.status !== "cancelled" && !p.why?.opportunity,
    );
    const withMetric: ProjectView[] = snapshot.projects.map((p) =>
      p.slug === requested[0]!.slug
        ? ({
            ...p,
            why: { ...p.why, proposal: "C-90", metric: "time to first result" },
          } as ProjectView)
        : p,
    );
    expect(ledgerFacts(null, [], withMetric).requested.measured).toBe(0);
    const readout = parseReadouts([{ id: "R-9", proposal: "C-90", day: 7 }])!;
    expect(ledgerFacts(null, readout, withMetric).requested.measured).toBe(1);
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
    const changes = starterChanges(definitionResponse().effective, new Set(["repo", "web"]));
    expect(changes[0]).toEqual({ op: "replace", path: "discovery.enabled", value: true });
    const sources = changes.filter((c) => c.path === "discovery.sources");
    expect(sources.map((c) => (c.value as { kind: string }).kind)).toEqual(["repo", "web", "team"]);
    expect(sources.every((c) => (c.value as { state: string }).state !== "pending")).toBe(true);
    expect(changes.every((c) => !JSON.stringify(c).match(/key|token|secret/i))).toBe(true);
  });

  it("leaves an outside source the owner did not tick waiting for an owner", () => {
    const off = definitionResponse().effective;
    const states = starterChanges(off)
      .filter((c) => c.path === "discovery.sources")
      .map((c) => (c.value as { state: string }).state);
    expect(states).toEqual(["pending", "pending", "active"]);
    expect(starterWaiting(off, new Set(["web"])).map((s) => s.label)).toEqual([
      "The factory's repos",
    ]);
  });

  it("keeps a budget and cadence an owner set, and says what applying it costs", () => {
    const off = structuredClone(definitionResponse().effective);
    off.discovery = {
      ...off.discovery!,
      enabled: false,
      budgets: { ...off.discovery!.budgets, usd_per_day: 3 },
      cadence: { ...off.discovery!.cadence, sweep_h: 12 },
    };
    const paths = starterChanges(off).map((c) => c.path);
    expect(paths).not.toContain("discovery.budgets.usd_per_day");
    expect(paths).not.toContain("discovery.cadence.sweep_h");
    expect(starterSummary(off)).toMatchObject({ usdPerDay: 3, sweepH: 12 });

    const unset = structuredClone(off);
    delete (unset.discovery!.budgets as { usd_per_day?: number }).usd_per_day;
    expect(starterChanges(unset)).toContainEqual({
      op: "replace",
      path: "discovery.budgets.usd_per_day",
      value: 10,
    });
    expect(starterSummary(unset).usdPerDay).toBe(10);
  });

  it("adds only the starter sources a factory does not have yet", () => {
    const on = discoveryDefinition().effective;
    expect(starterChanges(on).filter((c) => c.path === "discovery.sources")).toEqual([]);
    const summary = starterSummary(definitionResponse().effective);
    expect(summary.reads.map((r) => [r.label, r.added])).toEqual([
      ["The factory's repos", true],
      ["The open web", true],
      ["The factory's people", true],
    ]);
  });
});
