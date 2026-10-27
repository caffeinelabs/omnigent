import { describe, expect, it } from "vitest";
import type { DemoListResponse, ProjectDemoView } from "@/lib/factoryApi";
import { projectPrList } from "../board/boardModel";
import { projectPageHref, projectTab } from "../factoryRoutes";
import { EARLIER_9630_DEMO_ID, UX_DEMO_ID, projectScenario } from "./__fixtures__/projectFixtures";
import {
  demosSummary,
  groupDemosByPr,
  projectDecisions,
  stepTally,
  tallyText,
  undemoedPrs,
} from "./projectModel";

function scenario() {
  const { snapshot, demos } = projectScenario();
  const project = snapshot.projects.find((p) => p.slug === "api-version")!;
  return { snapshot, demos, project, prs: projectPrList(snapshot, project) };
}

describe("groupDemosByPr", () => {
  it("groups runs by PR, newest run first, groups by their newest run", () => {
    const { demos, prs } = scenario();
    // The server's order doesn't matter: the oldest run first still groups the same.
    const groups = groupDemosByPr([...demos.data].reverse(), prs);
    expect(groups.map((g) => [g.prNumber, g.latest.id, g.earlier.map((r) => r.id)])).toEqual([
      [9634, UX_DEMO_ID, []],
      [9630, "df2503ab72c051c6bdfa1e15f1570e31", [EARLIER_9630_DEMO_ID]],
    ]);
    expect(groups[0]!.pr?.title).toBe("feat(ux): API version row in Settings → About");
    expect(demosSummary(groups)).toBe("2 PRs · 3 runs");
  });

  it("keeps a PR the board doesn't track, without its PR view", () => {
    const { demos } = scenario();
    const stray: ProjectDemoView = { ...demos.data[0]!, id: "stray", pr_number: 1, repo: "o/r" };
    const [group] = groupDemosByPr([stray], []);
    expect(group).toMatchObject({ key: "o/r#1", prNumber: 1, pr: null });
  });
});

describe("demo tallies", () => {
  it("counts steps by outcome and words them", () => {
    const { demos } = scenario();
    const [ux, , earlier] = demos.data as DemoListResponse["data"];
    expect(stepTally(ux!)).toEqual({ passed: 7, failed: 0, skipped: 1 });
    expect(tallyText(stepTally(ux!))).toBe("7 passed · 1 skipped");
    expect(tallyText(stepTally(earlier!))).toBe("2 passed · 1 failed · 1 skipped");
    expect(tallyText({ passed: 0, failed: 0, skipped: 0 })).toBe("no steps");
  });
});

describe("project lists", () => {
  it("names open PRs without any demo", () => {
    const { snapshot } = scenario();
    const db = snapshot.projects.find((p) => p.slug === "db-version")!;
    const prs = projectPrList(snapshot, db);
    expect(undemoedPrs(prs, []).map((pr) => pr.number)).toEqual([9631]);
  });

  it("collects decisions filed under the project or asked by its PL", () => {
    const { snapshot } = scenario();
    const db = snapshot.projects.find((p) => p.slug === "db-version")!;
    expect(projectDecisions(snapshot, db).map((d) => d.ref)).toEqual(["D-2"]);
  });
});

describe("project page links", () => {
  it("encodes the factory and slug, and leaves Overview out of the query", () => {
    expect(projectPageHref("f 1", "a/b")).toBe("/factory/f%201/projects/a%2Fb");
    expect(projectPageHref("f1", "api", { tab: "overview" })).toBe("/factory/f1/projects/api");
    expect(projectPageHref("f1", "api", { tab: "demos", params: { review: 9630 } })).toBe(
      "/factory/f1/projects/api?tab=demos&review=9630",
    );
    expect(projectTab("demos")).toBe("demos");
    expect(projectTab("board")).toBe("overview");
    expect(projectTab(null)).toBe("overview");
  });
});
