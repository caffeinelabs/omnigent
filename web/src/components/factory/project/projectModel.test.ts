import { describe, expect, it } from "vitest";
import type { DemoListResponse, ProjectDemoView, ProjectView } from "@/lib/factoryApi";
import { projectPrList } from "../board/boardModel";
import { projectPageHref, projectSection } from "../factoryRoutes";
import {
  EARLIER_9630_DEMO_ID,
  UX_DEMO_ID,
  projectDemosOf,
  projectScenario,
} from "./__fixtures__/projectFixtures";
import {
  EVIDENCE_SLUG,
  IDEA_SLUG,
  REQUESTED_SLUG,
  SAMPLE_OPPORTUNITY,
  SHIPPED_SLUG,
  STOPPED_SLUG,
  projectsV2Snapshot,
} from "./__fixtures__/projectV2Fixtures";
import {
  demoGroupsFrom,
  demoRunCount,
  demosSummary,
  gateWords,
  groupDemosByPr,
  healthChip,
  measurementPlan,
  opportunityFacts,
  projectDecisions,
  projectWhy,
  projectWhyChain,
  spendByMember,
  stepTally,
  tabOfSection,
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

describe("demoGroupsFrom (the server's per-PR groups)", () => {
  it("keeps the groups' order, the newest run on the card and older runs under it", () => {
    const { demos, prs, snapshot } = scenario();
    const response = projectDemosOf(demos, "api-version", snapshot.prs);
    const groups = demoGroupsFrom(response.data, prs);
    expect(groups.map((g) => [g.prNumber, g.latest.id, g.earlier.map((r) => r.id)])).toEqual([
      [9634, UX_DEMO_ID, []],
      [9630, "df2503ab72c051c6bdfa1e15f1570e31", [EARLIER_9630_DEMO_ID]],
    ]);
    expect(demoRunCount(groups)).toBe(response.count);
    expect(groups[1]!.pr?.number).toBe(9630);
  });

  it("re-sorts a group's runs newest first and drops an empty group", () => {
    const { demos, prs, snapshot } = scenario();
    const response = projectDemosOf(demos, "api-version", snapshot.prs);
    const g9630 = response.data.find((g) => g.pr_number === 9630)!;
    g9630.runs.reverse();
    const groups = demoGroupsFrom([...response.data, { ...g9630, pr_number: 1, runs: [] }], prs);
    expect(groups).toHaveLength(2);
    expect(groups[1]!.latest.id).toBe("df2503ab72c051c6bdfa1e15f1570e31");
  });

  it("finds the PR by id first, else by repo and number; an untracked PR has none", () => {
    const { demos, prs, snapshot } = scenario();
    const [first] = projectDemosOf(demos, "api-version", snapshot.prs).data;
    expect(demoGroupsFrom([{ ...first!, pr_id: null }], prs)[0]!.pr?.number).toBe(9634);
    expect(demoGroupsFrom([{ ...first!, pr_id: null, pr_number: 1 }], prs)[0]!.pr).toBeNull();
  });
});

describe("project page links", () => {
  it("encodes the factory and slug; a section is an anchor, Overview is the top", () => {
    expect(projectPageHref("f 1", "a/b")).toBe("/factory/f%201/projects/a%2Fb");
    expect(projectPageHref("f1", "api", { section: "overview" })).toBe("/factory/f1/projects/api");
    expect(projectPageHref("f1", "api", { section: "demos", params: { decision: "D-2" } })).toBe(
      "/factory/f1/projects/api?decision=D-2#demos",
    );
    expect(projectSection("demos")).toBe("demos");
    expect(projectSection("#prs")).toBe("prs");
    expect(projectSection("board")).toBeNull();
    expect(projectSection(null)).toBeNull();
  });
});

// ── The new cockpit ────────────────────────────────────────────

describe("projectWhy", () => {
  const sample = () => projectsV2Snapshot();
  const of = (slug: string, patch: Partial<ProjectView> = {}) => ({
    ...sample().projects.find((p) => p.slug === slug)!,
    ...patch,
  });

  it("names the four kinds: from evidence, requested, an agent's idea, stopped", () => {
    expect(projectWhy(of(EVIDENCE_SLUG))).toMatchObject({
      kind: "evidence",
      text: "from O-3 · measured by weekly_exports",
      opportunity: "O-3",
      measured: true,
      noEvidence: false,
    });
    expect(projectWhy(of(REQUESTED_SLUG))).toMatchObject({
      kind: "requested",
      text: "No user evidence · requested by mia · not measured",
      by: "mia",
      noEvidence: true,
    });
    expect(projectWhy(of(IDEA_SLUG))).toMatchObject({
      kind: "idea",
      text: "No user evidence · Atlas's idea, counted as a hunch · not measured",
      by: "Atlas",
    });
    expect(projectWhy(of(STOPPED_SLUG))).toMatchObject({
      kind: "stopped",
      text: "Stopped: (sample) its lead stopped and could not restart",
    });
  });

  it("falls back to the owner, then to no name, on older servers", () => {
    const owner = of(REQUESTED_SLUG, { requested_by: null, human_owner: "lee@example.com" });
    const nobody = of(REQUESTED_SLUG, { requested_by: null, human_owner: null });
    const hunch = of(IDEA_SLUG, { requested_by: null, why: { kind: "hunch" } });
    expect(projectWhy(owner).text).toBe("No user evidence · requested by lee · not measured");
    expect(projectWhy(nobody).text).toBe("No user evidence · requested · not measured");
    expect(projectWhy(hunch)).toMatchObject({
      kind: "idea",
      text: "No user evidence · a hunch, counted as one · not measured",
    });
  });
});

describe("projectWhyChain", () => {
  it("fills evidence and opportunity from the ledger, the bet's range, the readout days", () => {
    const project = projectsV2Snapshot().projects.find((p) => p.slug === EVIDENCE_SLUG)!;
    const links = projectWhyChain(project, {
      opportunity: opportunityFacts(SAMPLE_OPPORTUNITY),
      opportunityHref: "/o",
    });
    expect(links.map((l) => [l.kind, l.value, l.detail ?? null, Boolean(l.missing)])).toEqual([
      ["evidence", "41 accounts", "forum 22 · trk 12 · web 7 · 1 hunch", false],
      ["opportunity", "O-3 · moderate", "score .50", false],
      ["bet", "C-29", "expects +1 to +4 pp", false],
      ["project", EVIDENCE_SLUG, "Verify", false],
      ["readout", "not yet", "day 7 and 28 after release", true],
    ]);
    expect(links[1]!.to).toBe("/o");
  });

  it("hatches every missing link of requested work and says it is not measured", () => {
    const project = projectsV2Snapshot().projects.find((p) => p.slug === REQUESTED_SLUG)!;
    const links = projectWhyChain(project);
    expect(links.filter((l) => l.missing).map((l) => [l.kind, l.detail])).toEqual([
      ["evidence", "no user evidence"],
      ["opportunity", "not linked"],
      ["bet", "no metric or range"],
      ["readout", "not measured"],
    ]);
  });
});

describe("healthChip", () => {
  const snapshot = () => projectsV2Snapshot();
  const project = (slug: string) => snapshot().projects.find((p) => p.slug === slug)!;

  it("says stopped, your merge, the facts' health, or stale", () => {
    const nowS = snapshot().server_time;
    expect(healthChip(project(STOPPED_SLUG), { nowS })).toMatchObject({
      tone: "red",
      label: "Stopped",
    });
    expect(healthChip(project(REQUESTED_SLUG), { nowS, mergeForYou: true })).toMatchObject({
      tone: "amber",
      label: "Needs your merge",
    });
    expect(healthChip(project(REQUESTED_SLUG), { nowS })).toMatchObject({
      tone: "green",
      label: "On track",
    });
    const overridden = healthChip(project(EVIDENCE_SLUG), { nowS })!;
    expect(overridden).toMatchObject({ tone: "neutral", label: "At risk" });
    expect(overridden.title).toBe(
      "At risk from the facts: 1 member stuck (the lead said on track)",
    );
    const stale = projectsV2Snapshot({ staleUpdate: true }).projects[0]!;
    expect(healthChip(stale, { nowS })).toMatchObject({
      tone: "grey",
      label: "At risk · stale 4h",
    });
    expect(healthChip(project(SHIPPED_SLUG), { nowS })).toMatchObject({ label: "Shipped" });
  });

  it("shows nothing from a server without health", () => {
    expect(healthChip({ ...project(IDEA_SLUG), health: null }, { nowS: 0 })).toBeNull();
  });
});

describe("gates, measurement and tabs", () => {
  it("writes a PR's gates in words, skipping what does not apply", () => {
    const [evidencePr, readyPr] = projectsV2Snapshot().prs;
    expect(gateWords(evidencePr!)).toEqual([
      "Checks passed",
      "Review passed",
      "Verify waiting",
      "Demo waiting",
    ]);
    expect(
      gateWords({ ...readyPr!, gates: { ...readyPr!.gates, checks: "pending", demo: null } }),
    ).toEqual(["Checks running", "Review passed", "Verify passed"]);
  });

  it("prints the frozen measurement plan, or nothing for requested work", () => {
    const [evidence, requested] = projectsV2Snapshot().projects;
    expect(measurementPlan(evidence!)).toEqual([
      { label: "Metric", value: "weekly_exports" },
      { label: "Method", value: "before and after release" },
      { label: "Expects", value: "+1 to +4 pp" },
      { label: "Readouts", value: "day 7 and 28 after release" },
      { label: "Guardrails", value: "(sample) support load stays flat" },
      { label: "Kill if", value: "(sample) lift under 0.5 pp by 28 days after release" },
    ]);
    expect(measurementPlan(requested!)).toBeNull();
  });

  it("sends each classic section to the tab that holds it", () => {
    expect(["#prs", "#demos", "#tasks", "#loops", "#previews"].map(tabOfSection)).toEqual([
      "work",
      "work",
      "work",
      "work",
      "work",
    ]);
    expect([tabOfSection("#activity"), tabOfSection("#decisions"), tabOfSection("#team")]).toEqual([
      "history",
      "overview",
      null,
    ]);
  });

  it("lists spend by member, largest first", () => {
    const snapshot = projectsV2Snapshot();
    const project = snapshot.projects.find((p) => p.slug === EVIDENCE_SLUG)!;
    const rows = spendByMember(snapshot, project);
    expect(rows[0]!.usd).toBeGreaterThanOrEqual(rows.at(-1)!.usd);
    expect(rows.map((r) => r.name)).toContain("nova");
  });
});
