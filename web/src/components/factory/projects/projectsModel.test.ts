import { describe, expect, it } from "vitest";
import { liveRunSnapshot } from "../__fixtures__/liveRun";
import {
  changesRequestedSnapshot,
  round3Snapshot,
  scaleSnapshot,
} from "../shell/__fixtures__/shellFixtures";
import { isClosedProject } from "../board/boardModel";
import { matchesFilter, projectRowFacts, projectSections, sectionsSummary } from "./projectsModel";

describe("projectSections", () => {
  it("splits work in flight, the base env and closed projects", () => {
    const sections = projectSections(round3Snapshot().projects);
    expect(sections.envs.map((p) => p.slug)).toEqual(["base-env"]);
    expect(sections.closed.map((p) => p.slug)).toEqual(["docs-factory-envs"]);
    expect(sections.work.map((p) => p.slug)).not.toContain("base-env");
    expect(sections.work[0]!.slug).toBe("api-version"); // ready first
    expect(sectionsSummary(sections)).toBe("5 active · 1 closed");
  });

  it("treats cancelled and merged projects as closed", () => {
    const project = { ...round3Snapshot().projects[0]!, status: "cancelled" as const };
    expect(isClosedProject(project)).toBe(true);
  });
});

describe("matchesFilter", () => {
  it("finds what needs attention: blocked, open decisions, exhausted loops", () => {
    const snapshot = round3Snapshot();
    const attention = snapshot.projects
      .filter((p) => matchesFilter(snapshot, p, "attention"))
      .map((p) => p.slug);
    expect(attention).toEqual(expect.arrayContaining(["db-version", "cache-warm"]));
    expect(attention).not.toContain("ux-badge");
  });

  it("filters by stage", () => {
    const snapshot = round3Snapshot();
    const pick = (filter: Parameters<typeof matchesFilter>[2]) =>
      snapshot.projects.filter((p) => matchesFilter(snapshot, p, filter)).map((p) => p.slug);
    expect(pick("ready")).toEqual(["api-version"]);
    expect(pick("waiting")).toEqual(["settings-row"]);
    expect(pick("building")).toEqual(expect.arrayContaining(["db-version", "ux-badge"]));
  });
});

describe("projectRowFacts", () => {
  it("reads tasks, the main PR and its gates, loops, agents, flags and dependencies", () => {
    const snapshot = round3Snapshot();
    const db = snapshot.projects.find((p) => p.slug === "db-version")!;
    const facts = projectRowFacts(snapshot, db);
    expect(facts.tasks).toBe("2/5");
    expect(facts.pr?.number).toBe(9631);
    expect(facts.gates.length).toBeGreaterThan(4);
    expect(facts.loops).toBe("3▶ 1⛔");
    expect(facts.loopsTone).toBe("warning");
    expect(facts.flags.map((f) => f.label)).toEqual(expect.arrayContaining(["D-2"]));
    expect(facts.agents).toBe(1 + db.team.length);
    const row = snapshot.projects.find((p) => p.slug === "settings-row")!;
    expect(projectRowFacts(snapshot, row).dependsOn).toEqual(["api-version", "db-version"]);
    expect(projectRowFacts(snapshot, row).pr).toBeNull();
  });

  it("flags a project whose PR a human asked changes on, until a new head", () => {
    const snapshot = changesRequestedSnapshot();
    const api = snapshot.projects.find((p) => p.slug === "api-version")!;
    expect(api.stage).toBe("building");
    const facts = projectRowFacts(snapshot, api);
    expect(facts.pr?.number).toBe(9630);
    expect(facts.flags[0]).toMatchObject({
      key: "changes",
      label: "changes requested",
      tone: "warning",
      title:
        "luka@caffeine.ai asked for changes on !9630 @7767f0d; " +
        "ready again once a new head passes the gates",
    });
    expect(matchesFilter(snapshot, api, "ready")).toBe(false);
    snapshot.prs.find((p) => p.number === 9630)!.head_sha = "9".repeat(40);
    expect(projectRowFacts(snapshot, api).flags.some((f) => f.key === "changes")).toBe(false);
  });

  it("leaves tasks and loops out on a round-2 server", () => {
    const snapshot = liveRunSnapshot();
    const facts = projectRowFacts(snapshot, snapshot.projects[0]!);
    expect(facts.tasks).toBeNull();
    expect(facts.loops).toBeNull();
  });

  it("scales to 12 projects and 60 agents", () => {
    const snapshot = scaleSnapshot(12, 60);
    expect(snapshot.members).toHaveLength(60);
    const sections = projectSections(snapshot.projects);
    expect(sections.work.length + sections.envs.length + sections.closed.length).toBe(12);
    for (const project of sections.work)
      expect(() => projectRowFacts(snapshot, project)).not.toThrow();
  });
});
