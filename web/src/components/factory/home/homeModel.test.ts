import { describe, expect, it } from "vitest";
import type { Ask } from "@/lib/factoryAsksApi";
import type { DefinitionDoc, FactorySnapshot, ProjectView } from "@/lib/factoryApi";
import { teamMember } from "../agents/teamsModel";
import { round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import {
  NEEDS_ROWS_MAX,
  askMeta,
  discoveryChecklist,
  discoveryRuns,
  homeAgentRows,
  homeFacts,
  homeHeadline,
  homeLoop,
  homeProjects,
  needsView,
  parseDiscoveryFacts,
  parseOpportunities,
  shellNeedsCount,
  usersNeed,
  whyLine,
  yourMergeCount,
} from "./homeModel";

const NOW = 1_793_600_000;

function ask(id: string, extra: Partial<Ask> = {}): Ask {
  return {
    id,
    kind: "decision",
    subject: `decision:${id}`,
    ref: id,
    title: `Question ${id} (sample)`,
    context: null,
    meta: [],
    since: NOW - 3600,
    age_s: 3600,
    cost_of_waiting: { score: 30, level: "low", text: "the factory goes on without it", holds: 0 },
    primary_action: { kind: "open", label: "Answer", href: "/x" },
    options: null,
    allow_text: false,
    items: [],
    more_count: 0,
    assignee: "operators",
    mine: false,
    folded: [],
    covers: [],
    critical: false,
    href: "/x",
    ...extra,
  };
}

function project(slug: string, extra: Partial<ProjectView> = {}): ProjectView {
  const base = round3Snapshot().projects[0]!;
  return {
    ...base,
    slug,
    title: `${slug} (sample)`,
    stage: "building",
    stage_since: NOW - 600,
    overlay: null,
    overlay_reason: null,
    status: "active",
    kind: "work",
    why: null,
    charter: base.charter ? { ...base.charter, outcome: null } : null,
    pr_ids: [],
    human_owner: null,
    ...extra,
  };
}

const DISCOVERY_ON = {
  discovery: { enabled: true, sources: [{ id: "web", label: "Web (sample)", state: "trial" }] },
} as unknown as DefinitionDoc;

describe("the headline", () => {
  it("says how many things need you, from the one list", () => {
    expect(homeHeadline({ needs: 5, moving: 2, state: "active" })).toBe(
      "5 things need you. Everything else is moving.",
    );
    expect(homeHeadline({ needs: 1, moving: 2, state: "active" })).toBe(
      "1 thing needs you. Everything else is moving.",
    );
    expect(homeHeadline({ needs: 0, moving: 2, state: "active" })).toBe(
      "Nothing needs you. Everything is moving.",
    );
  });

  it("never says moving when nothing moves, and names a paused factory", () => {
    expect(homeHeadline({ needs: 0, moving: 0, state: "active" })).toBe(
      "Nothing needs you. Nothing is in progress.",
    );
    expect(homeHeadline({ needs: 2, moving: 3, state: "paused" })).toBe(
      "2 things need you. The factory is paused.",
    );
    // While the list loads it claims no count.
    expect(homeHeadline({ needs: null, moving: 3, state: "active" })).toBe(
      "3 projects are moving.",
    );
  });

  it("states facts and skips zeros", () => {
    expect(
      homeFacts({
        building: 1,
        waitingMerge: 4,
        yourMerge: true,
        todayUsd: 128.4,
        dayBudgetUsd: 240,
        usdPerH: 27,
      }),
    ).toEqual(["1 building", "4 waiting for your merge", "$128 spent today of $240"]);
    // Without the asks route it can only count ready projects.
    expect(
      homeFacts({ building: 0, waitingMerge: 2, todayUsd: 0, dayBudgetUsd: null, usdPerH: 0 }),
    ).toEqual(["2 waiting for a merge"]);
    expect(
      homeFacts({ building: 0, waitingMerge: 0, todayUsd: null, dayBudgetUsd: null, usdPerH: 3.5 }),
    ).toEqual(["$3.50/h now"]);
  });
});

describe("your merges", () => {
  it("count the ready PRs of Needs you's merge rows, listed or not", () => {
    const items = [1, 2, 3, 4, 5].map((n) => ({
      ref: `PR #${n}`,
      title: `Change ${n} (sample)`,
      href: `/pr/${n}`,
    }));
    expect(
      yourMergeCount([ask("merge", { kind: "merge", items, more_count: 3 }), ask("D-1")]),
    ).toBe(8);
    expect(yourMergeCount([ask("D-1")])).toBe(0);
  });
});

describe("project rows", () => {
  it("say why each project exists, honestly", () => {
    expect(
      whyLine(project("a", { overlay: "failed", overlay_reason: "PL sandbox failed twice" })),
    ).toMatchObject({ kind: "stopped", text: "Stopped: PL sandbox failed twice" });
    expect(
      whyLine(project("b", { why: { opportunity: "O-3", metric: "weekly publishes" } as never })),
    ).toEqual({
      kind: "evidence",
      text: "from O-3 · measured by weekly publishes",
      ref: "O-3",
      noEvidence: false,
    });
    expect(whyLine(project("c", { human_owner: "mia@example.com" }))).toEqual({
      kind: "requested",
      text: "No user evidence · requested by mia · not measured",
      noEvidence: true,
    });
    expect(whyLine(project("d", { why: { kind: "hunch" } as never })).kind).toBe("hunch");
  });

  it("list working first, then waiting for a merge, then stopped; shipped work folds", () => {
    const base = round3Snapshot();
    const readyPr = { ...base.prs.find((pr) => pr.ready)!, id: "prready", project_slug: "ready" };
    const snapshot: FactorySnapshot = {
      ...base,
      prs: [readyPr],
      projects: [
        project("stopped", { overlay: "failed", stage: "building" }),
        project("ready", { stage: "ready", stage_since: NOW - 7200, pr_ids: ["prready"] }),
        project("ready-elsewhere", { stage: "ready", stage_since: NOW - 3600 }),
        project("verify", { stage: "verify" }),
        project("plan", { stage: "planning" }),
        project("shipped", { stage: "merged", charter: null }),
        project("env", { kind: "integration" }),
        project("closed", { status: "cancelled" }),
      ],
      server_time: NOW,
    };

    const { rows, shipped } = homeProjects(snapshot, [], NOW);

    expect(rows.map((r) => [r.slug, r.stripe, r.stageLabel])).toEqual([
      ["verify", "none", "Verify"],
      ["plan", "none", "Plan"],
      ["ready", "amber", "Ready for merge"],
      // Ready, but no PR of it waits on a person here: no amber.
      ["ready-elsewhere", "none", "Ready for merge"],
      ["stopped", "red", "Stopped"],
    ]);
    expect(rows[0]!.stageAge).toBe("10m in stage");
    expect(rows[2]!.stageAge).toBe("2h");
    expect(shipped).toEqual([{ slug: "shipped", title: "shipped (sample)", measured: false }]);
  });

  it("put each project's team on its row, models in words", () => {
    const snapshot = round3Snapshot();
    const members = homeAgentRows(snapshot).map((r) =>
      teamMember(r, snapshot.factory.root_session_id),
    );
    const { rows } = homeProjects(snapshot, members, snapshot.server_time);
    const api = rows.find((r) => r.slug === "api-version")!;
    expect(api.team.length).toBeGreaterThan(0);
    expect(api.team.every((m) => m.row.project === "api-version")).toBe(true);
  });
});

describe("needs you", () => {
  it("caps the rows at five and says how many more", () => {
    const asks = Array.from({ length: 8 }, (_, i) => ask(`D-${i + 1}`));

    expect(needsView(asks, "all", false)).toMatchObject({ more: 3, total: 8 });
    expect(needsView(asks, "all", false).shown).toHaveLength(NEEDS_ROWS_MAX);
    expect(needsView(asks, "all", true).shown).toHaveLength(8);
  });

  it("narrows to what is assigned by name, and hides rows answered with Undo pending", () => {
    const asks = [ask("D-1", { mine: true }), ask("D-2"), ask("D-3", { mine: true })];

    expect(needsView(asks, "mine", false).shown.map((a) => a.id)).toEqual(["D-1", "D-3"]);
    expect(needsView(asks, "all", false, new Set(["D-2"])).shown.map((a) => a.id)).toEqual([
      "D-1",
      "D-3",
    ]);
  });

  it("gives the top bar Home's count while answers are held, else the server's", () => {
    const asks = [ask("D-1"), ask("D-2"), ask("D-3")];

    // Nothing held: the server's count stands, even when the list says otherwise.
    expect(shellNeedsCount(4, new Set(), asks)).toBe(4);
    // Held (or sent and still listed): Home's list without them.
    expect(shellNeedsCount(3, new Set(["D-2"]), asks)).toBe(2);
    // Sent and gone from a newer list, while the server's count is older: still Home's.
    expect(shellNeedsCount(3, new Set(["D-2"]), [ask("D-1"), ask("D-3")])).toBe(2);
    // Before the list is read, the held rows come off the server's count.
    expect(shellNeedsCount(3, new Set(["D-2"]), null)).toBe(2);
    expect(shellNeedsCount(0, new Set(["D-2"]), null)).toBe(0);
  });

  it("states the row's facts: who, what waiting costs, age and deadline", () => {
    const row = ask("D-41", {
      meta: ["search", "Nova"],
      since: NOW - 720,
      deadline_at: NOW + 4 * 3600,
      cost_of_waiting: { score: 70, level: "high", text: "blocks the search team", holds: 1 },
    });

    expect(askMeta(row, NOW)).toEqual([
      "search",
      "Nova",
      "blocks the search team",
      "12m",
      "due in 4h",
    ]);
  });
});

describe("the loop strip", () => {
  it("shows Build and Measure from the factory's own projects while discovery is off", () => {
    const snapshot = {
      ...round3Snapshot(),
      projects: [
        project("a", { why: { opportunity: "O-1" } as never }),
        project("b"),
        project("c", { stage: "done" }),
      ],
    };

    const loop = homeLoop({
      snapshot,
      definition: {} as DefinitionDoc,
      discoveryOn: false,
      asks: [],
      discovery: null,
      leads: {},
      hrefs: { signals: "/s", opportunities: "/o", flow: "/f", projects: "/p", impact: "/i" },
    });

    expect(loop.discoveryOn).toBe(false);
    expect(loop.goal).toBeNull();
    const build = loop.stages.find((s) => s.key === "build")!;
    expect([build.count, build.detail]).toEqual([2, "1 from evidence · 1 requested"]);
    expect(loop.stages.find((s) => s.key === "measure")!.detail).toBe(
      "nothing shipped from a bet yet",
    );
  });

  it("counts what it knows and says what waits for you once discovery is on", () => {
    const loop = homeLoop({
      snapshot: round3Snapshot(),
      definition: DISCOVERY_ON,
      discoveryOn: true,
      asks: [ask("C-9", { kind: "source" }), ask("C-31", { kind: "bet" })],
      discovery: parseDiscoveryFacts({ chain: { signals: 64, opportunities: 0 } }),
      leads: {},
      hrefs: { signals: "/s", opportunities: "/o", flow: "/f", projects: "/p", impact: "/i" },
    });

    expect(loop.discoveryOn).toBe(true);
    expect(loop.goal).toEqual({
      statement: null,
      measured: false,
      note: "Opportunities are ranked against goals.",
    });
    const [sense, make, decide] = loop.stages;
    expect([sense!.count, sense!.unit, sense!.waiting]).toEqual([
      64,
      "signals this week",
      "1 waits for you",
    ]);
    expect([make!.count, make!.detail]).toEqual([0, "first synthesis not run yet"]);
    expect([decide!.count, decide!.waiting]).toEqual([1, "1 waits for you"]);
  });

  it("stays dark while this server has discovery off, whatever the definition says", () => {
    const definition = { discovery_server: false, effective: DISCOVERY_ON };
    expect(discoveryRuns(definition)).toBe(false);
    expect(discoveryRuns({ ...definition, discovery_server: true })).toBe(true);
    expect(discoveryRuns({ discovery_server: true, effective: {} as DefinitionDoc })).toBe(false);
    expect(discoveryRuns(undefined)).toBe(false);

    const loop = homeLoop({
      snapshot: round3Snapshot(),
      definition: DISCOVERY_ON,
      discoveryOn: discoveryRuns(definition),
      asks: [ask("C-9", { kind: "source" })],
      discovery: null,
      leads: {},
      hrefs: { signals: "/s", opportunities: "/o", flow: "/f", projects: "/p", impact: "/i" },
    });

    // Only Build and Measure (the strip draws the rest as "turn on"), no goal, no checklist.
    expect(loop.discoveryOn).toBe(false);
    expect(loop.goal).toBeNull();
    expect(discoveryChecklist(DISCOVERY_ON, [], null, discoveryRuns(definition))).toBeNull();
  });

  it("lists the cold-start steps until each is done", () => {
    expect(discoveryChecklist({} as DefinitionDoc, [], null, true)).toBeNull();
    const steps = discoveryChecklist(DISCOVERY_ON, [ask("C-2", { kind: "profile" })], null, true)!;
    expect(steps.map((s) => [s.label, s.done])).toEqual([
      ["Turn on discovery", true],
      ["Approve the starter sources", true],
      ["Review the product profile", false],
      ["Set a first goal", false],
      ["First weekly synthesis", false],
    ]);
  });
});

describe("the discovery readers", () => {
  it("read the summary field by field, and nothing when there is none", () => {
    expect(parseDiscoveryFacts(null)).toBeNull();
    expect(
      parseDiscoveryFacts({
        signals: { week: 214, unsorted: 37 },
        opportunities: { open: 8, now: 2 },
        learned: [{ text: "Two new opportunities (sample)", ref: "O-24" }, "A line (sample)"],
      }),
    ).toMatchObject({
      signalsWeek: 214,
      signalsUnsorted: 37,
      opportunities: 8,
      opportunitiesNow: 2,
      bets: null,
      learned: [
        { text: "Two new opportunities (sample)", ref: "O-24" },
        { text: "A line (sample)", ref: null },
      ],
    });
  });

  it("read ranked opportunities and show the top of Now and Next", () => {
    const rows = parseOpportunities({
      data: [
        {
          ref: "O-15",
          title: "Share a draft (sample)",
          band: "now",
          score: 0.23,
          level: "moderate",
        },
        { ref: "O-7", title: "Custom names (sample)", band: "next", level: "telepathy" },
        { id: "O-9", title: "Parked one (sample)", band: "parked" },
        { title: "No id" },
        { ref: "O-2", title: "Later one (sample)", band: "later" },
      ],
    })!;

    expect(rows.map((r) => [r.id, r.band, r.level])).toEqual([
      ["O-15", "now", "moderate"],
      ["O-7", "next", null],
      ["O-9", "parked", null],
      ["O-2", "later", null],
    ]);
    expect(usersNeed(rows).map((r) => r.id)).toEqual(["O-15", "O-7"]);
    expect(parseOpportunities({ error: "x" })).toBeNull();
  });
});
