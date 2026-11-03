import { describe, expect, it } from "vitest";
import { teamMember } from "../agents/teamsModel";
import { homeAgentRows } from "../home/homeModel";
import {
  EVIDENCE_SLUG,
  IDEA_SLUG,
  REQUESTED_SLUG,
  SHIPPED_SLUG,
  STOPPED_SLUG,
  projectsV2Snapshot,
} from "../project/__fixtures__/projectV2Fixtures";
import {
  BOTTLENECK_AT,
  boardColumns,
  filterCards,
  filterCounts,
  filterOf,
  projectCards,
} from "./projectCards";

function cardsOf(options: Parameters<typeof projectsV2Snapshot>[0] = {}, merge?: Set<string>) {
  const snapshot = projectsV2Snapshot(options);
  const members = homeAgentRows(snapshot).map((row) =>
    teamMember(row, snapshot.factory.root_session_id, new Set()),
  );
  return projectCards(snapshot, members, snapshot.server_time, merge ?? null);
}

describe("projectCards", () => {
  it("gives each live project one outcome-first card, working before merges before stopped", () => {
    const { cards, shipped } = cardsOf({ question: true });
    expect(cards.map((c) => [c.slug, c.origin.kind, c.stripe])).toEqual([
      [EVIDENCE_SLUG, "evidence", "none"],
      [IDEA_SLUG, "idea", "none"],
      [REQUESTED_SLUG, "requested", "amber"],
      [STOPPED_SLUG, "stopped", "red"],
    ]);
    const evidence = cards[0]!;
    expect(evidence.title).toBe("(sample) People can export a report as CSV");
    expect(evidence.update).toEqual({
      by: "nova",
      text: "(sample) T-3 merged; the export endpoint streams rows",
    });
    expect(evidence.health).toMatchObject({ label: "At risk" });
    expect(evidence.spend).toEqual({ spent: 25.35, budget: 60 });
    expect(evidence.team.length).toBeGreaterThan(3);
    expect(cards[2]!.health).toMatchObject({ tone: "amber", label: "Needs your merge" });
    expect(shipped).toEqual([
      {
        slug: SHIPPED_SLUG,
        title: "(sample) People can save a filtered view",
        readout: "not measured",
        measured: false,
      },
    ]);
  });

  it("keeps the amber stripe for merges the viewer makes, when the server says whose", () => {
    const { cards } = cardsOf({}, new Set([EVIDENCE_SLUG]));
    const requested = cards.find((c) => c.slug === REQUESTED_SLUG)!;
    expect(requested.stripe).toBe("none");
    expect(requested.needsYou).toBe(false);
  });

  it("filters: from evidence, requested (ideas too), needs you, stopped", () => {
    const { cards } = cardsOf({ question: true });
    const slugs = (f: Parameters<typeof filterCards>[1]) =>
      filterCards(cards, f).map((c) => c.slug);
    expect(slugs("evidence")).toEqual([EVIDENCE_SLUG]);
    expect(slugs("requested")).toEqual([IDEA_SLUG, REQUESTED_SLUG]);
    expect(slugs("needs")).toEqual([REQUESTED_SLUG]);
    expect(slugs("stopped")).toEqual([STOPPED_SLUG]);
    expect(filterCounts(cards)).toEqual({
      all: 4,
      evidence: 1,
      requested: 2,
      needs: 1,
      stopped: 1,
    });
    expect([filterOf("needs"), filterOf("bogus"), filterOf(null)]).toEqual(["needs", "all", "all"]);
  });

  it("lays the board out by stage, leaves stopped work off it, and flags a merge bottleneck", () => {
    const { cards, shipped } = cardsOf({ more: 10 });
    const columns = boardColumns(cards, shipped);
    expect(columns.map((c) => c.label)).toEqual([
      "Plan",
      "Build",
      "Review",
      "Verify",
      "Ready for merge",
      "Shipped",
    ]);
    const all = columns.flatMap((c) => c.cards.map((x) => x.slug));
    expect(all).not.toContain(STOPPED_SLUG);
    const ready = columns.find((c) => c.step === "ready")!;
    expect(ready.cards.length).toBeGreaterThanOrEqual(BOTTLENECK_AT);
    expect(ready.bottleneck).toBe(true);
    expect(columns.at(-1)!.shipped.map((s) => s.slug)).toEqual([SHIPPED_SLUG]);
    const calm = boardColumns(cardsOf().cards, []);
    expect(calm.some((c) => c.bottleneck)).toBe(false);
  });
});
