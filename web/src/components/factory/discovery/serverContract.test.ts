// The cockpit's readers against what the server really answers. The fixture
// `__fixtures__/discoveryContract.json` holds the discovery routes' answers
// from the server's eval-mode smoke loop
// (`tests/server/factory/test_discovery_smoke_e2e.py`, which fails when the
// answers change shape until the fixture is rewritten), so a field one side
// renames shows up here instead of as a blank on a page. Every word in it is
// sample data.

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { Ask } from "@/lib/factoryAsksApi";
import type { DefinitionDoc, ProjectView } from "@/lib/factoryApi";
import { parseDiscoveryFacts, parseOpportunities } from "../home/homeModel";
import { opportunityFacts, projectWhy, projectWhyChain } from "../project/projectModel";
import { rerankRows } from "../steer/steerModel";
import contract from "./__fixtures__/discoveryContract.json";
import starterFixture from "./__fixtures__/discoveryStarter.json";
import {
  ledgerFacts,
  parseOpportunityDetail,
  parseOpportunityList,
  parseReadouts,
  parseSignals,
  parseSummary,
  starterChanges,
  starterWaiting,
} from "./discoveryModel";

/** The server's default definition (its golden test keeps it current). */
const SERVER_DEFAULTS = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../../../../tests/server/factory/golden/definition_doc_defaults.json",
);

const out = contract.read_out as Record<string, unknown>;

describe("GET …/discovery as the Discovery page and Home read it", () => {
  it("the Discovery header reads the chain, sources, readouts and the ledger", () => {
    const summary = parseSummary(out.discovery)!;
    expect(summary.scout.state).toBe("active");
    expect(summary.sources.map((s) => s.id)).toEqual(["repo", "web", "team"]);
    expect(summary.chain.signalsWeek).toBeGreaterThan(50);
    expect(summary.chain.opportunities).toBe(3);
    expect(summary.ledger).toMatchObject({ readouts: 1, inside: 1 });
    expect(summary.readoutsToDecide).toBe(0);
    expect(summary.nextSynthesisAt).toEqual(expect.any(Number));
  });

  it("Home's loop strip and Learned this week read the same answer", () => {
    const facts = parseDiscoveryFacts(out.discovery)!;
    expect(facts.signalsWeek).toBe(parseSummary(out.discovery)!.chain.signalsWeek);
    expect(facts.opportunities).toBe(3);
    expect(facts.opportunitiesNow).toEqual(expect.any(Number));
    expect(facts.readouts).toBe(1);
    expect(facts.readoutsWon).toBe(1);
    expect(facts.nextSynthesisAt).toEqual(expect.any(Number));
    expect(facts.learned.length).toBeGreaterThanOrEqual(3);
    expect(facts.learned[0]).toEqual({
      text: expect.stringContaining("day 7 readout: won"),
      ref: "O-1",
    });
    const rows = parseOpportunities(out.opportunities)!;
    expect(rows.map((r) => r.id)).toEqual(["O-1", "O-2", "O-3"]);
    expect(rows.every((r) => r.level === "moderate" && r.accounts != null)).toBe(true);
  });
});

describe("opportunities, signals and readouts", () => {
  it("the table and the panel read every opportunity's evidence, score, bet and quotes", () => {
    const list = parseOpportunityList(out.opportunities)!;
    expect(list.rows).toHaveLength(3);
    const first = list.rows.find((r) => r.id === "O-1")!;
    expect(first).toMatchObject({ status: "measured", bet: { proposal: "C-4" } });
    expect(first.parts.map((p) => p.name)).toEqual(["reach", "intensity", "fit", "confidence"]);
    const detail = parseOpportunityDetail(out.opportunity)!;
    expect(detail.quotes.said).toHaveLength(3);
    expect(detail.betNote?.cheapest).toMatch(/fake door/);
    expect(detail.why.map((l) => l.kind)).toEqual([
      "evidence",
      "opportunity",
      "bet",
      "project",
      "readout",
    ]);
    expect(detail.why.every((l) => !l.missing)).toBe(true);
  });

  it("the hunch column and the flagged queue read the signal views", () => {
    const hunches = parseSignals(out.hunches)!;
    expect(hunches.length).toBeGreaterThanOrEqual(3);
    expect(hunches.every((s) => s.hunch)).toBe(true);
    const [flagged] = parseSignals(out.flagged)!;
    expect(flagged).toMatchObject({ injection: true, actionable: false });
  });

  it("Impact reads a written readout with its range, effect and options", () => {
    const readouts = parseReadouts(out.readouts)!;
    expect(readouts.map((r) => [r.day, r.status])).toEqual([
      [7, "written"],
      [28, "scheduled"],
    ]);
    const [day7] = readouts;
    expect(day7).toMatchObject({
      key: "C-4-d7",
      status: "written",
      proposal: "C-4",
      opportunity: "O-1",
      day: 7,
      verdict: "won",
      recommendation: "keep",
      low: -100,
      high: -20,
      unit: "%",
      inside: true,
      nextDay: 28,
    });
    expect(day7!.actual).toEqual(expect.any(Number));
    expect(day7!.options[0]).toMatchObject({ decision: "keep", recommended: true });
    const ledger = parseSummary(out.discovery)!.ledger;
    const facts = ledgerFacts(ledger, readouts, [out.project as ProjectView]);
    expect(facts.readouts).toBe(1);
  });
});

describe("the project's why-chain", () => {
  it("shows the readout the ledger wrote for the project's own bet", () => {
    const project = out.project as ProjectView;
    expect(projectWhy(project)).toMatchObject({ kind: "evidence", opportunity: "O-1" });
    const links = projectWhyChain(project, { opportunity: opportunityFacts(out.opportunity) });
    expect(links.map((l) => l.kind)).toEqual([
      "evidence",
      "opportunity",
      "bet",
      "project",
      "readout",
    ]);
    expect(links[4]).toEqual({ kind: "readout", value: "day 7 · won", detail: "C-4-d7" });
    expect(links.some((l) => l.missing)).toBe(false);
  });

  it("leaves another bet's readout out", () => {
    const project = {
      ...(out.project as ProjectView),
      why: { ...(out.project as ProjectView).why!, proposal: "C-9" },
    } as ProjectView;
    const links = projectWhyChain(project, { opportunity: opportunityFacts(out.opportunity) });
    expect(links[4]).toMatchObject({ kind: "readout", value: "not yet", missing: true });
  });
});

describe("Steer and Needs you", () => {
  it("the re-rank table reads the preview's rows", () => {
    const rows = rerankRows((out.preview_rerank as { rerank: unknown[] }).rerank);
    expect(rows).toHaveLength(3);
    expect(rows[0]).toMatchObject({ ref: "O-1", move: "up" });
  });

  it("a bet waiting for a person is a bet row that names its opportunity", () => {
    const asks = contract.bet_waits.asks as unknown as Ask[];
    const [bet] = asks.filter((a) => a.kind === "bet");
    expect(bet).toMatchObject({ opportunity: "O-1", primary_action: { kind: "open" } });
    expect(bet!.title).toBe("Approve a bet: Share a private draft link (sample)");
  });
});

describe("what the palette's verbs and Steer's source buttons read back", () => {
  const verbs = contract.verbs as Record<string, Record<string, unknown>>;

  it("a hunch gives its signal id for Undo; research its budget and Undo; plan its Room post", () => {
    // `addHunch` returns `signal.id`; `withdrawHunch` takes it back.
    expect((verbs.hunch!.signal as { id: string }).id).toMatch(/^S-\d+$/);
    const research = verbs.research as {
      research: { budget_usd: number; state: string };
      action: { id: string } | null;
    };
    expect(research.research.budget_usd).toEqual(expect.any(Number));
    expect(research.research.state).toEqual(expect.any(String));
    expect(research.action?.id).toEqual(expect.any(String));
    expect(verbs.plan!.chat).toMatch(/^chat_/);
  });

  it("a pause gives the version its Undo pins; a test gives its checks", () => {
    const pause = verbs.pause as { changed: boolean; undo: { pin: number } | null };
    expect(pause.changed).toBe(true);
    expect(pause.undo?.pin).toEqual(expect.any(Number));
    const test = verbs.test as {
      ok: boolean;
      checks: { name: string; ok: boolean; detail: string }[];
    };
    expect(test.ok).toBe(true);
    expect(test.checks.every((c) => c.name && typeof c.ok === "boolean" && c.detail)).toBe(true);
  });
});

describe("the discovery starter", () => {
  const defaults = JSON.parse(readFileSync(SERVER_DEFAULTS, "utf8")) as DefinitionDoc;

  it("is what the server's smoke test applies, against the server's default definition", () => {
    const all = new Set(starterFixture.approval_needed);
    expect(starterChanges(defaults, all)).toEqual(starterFixture.changes);
  });

  it("adds an outside source the owner did not tick as pending, which waits for an owner", () => {
    const changes = starterChanges(defaults, new Set(["repo"]));
    const states = Object.fromEntries(
      changes
        .filter((c) => c.path === "discovery.sources")
        .map((c) => [(c.value as { id: string }).id, (c.value as { state: string }).state]),
    );
    expect(states).toEqual({ repo: "trial", web: "pending", team: "active" });
    expect(starterWaiting(defaults, new Set(["repo"])).map((s) => s.id)).toEqual(["web"]);
  });
});
