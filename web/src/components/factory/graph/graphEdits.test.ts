import { describe, expect, it } from "vitest";
import { roomTasks } from "../room/__fixtures__/roomFixtures";
import { round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import {
  checkTaskEdit,
  findCycle,
  globsOverlap,
  keysToRefs,
  overlapWarnings,
  uniqueKey,
} from "./graphEdits";
import { dependsRows } from "./ProjectDependsDialog";

const db = roomTasks().filter((t) => t.project_slug === "db-version");
const task = (ref: string) => db.find((t) => t.ref === ref)!;
const known = { projects: ["db-version", "api-version"], decisions: ["D-2", "D-31"] };

describe("globsOverlap (the server's prefix heuristic)", () => {
  it.each([
    ["services/api/**", "services/api/src/x.ts", true],
    ["services/**", "services/api/**", true],
    ["services/api", "services/api/x.ts", true],
    ["services/api", "services/api-gw/**", false],
    ["services/api*", "services/api-gw/**", true],
    ["./web/**", "/web/src", true],
    ["docs/**", "web/**", false],
    ["", "web/**", false],
  ])("%s ~ %s → %s", (a, b, expected) => {
    expect(globsOverlap(a, b)).toBe(expected);
  });
});

describe("task edit checks", () => {
  it("finds a cycle as a path", () => {
    const tasks = [
      { key: "a", deps: ["b"], number: 1 },
      { key: "b", deps: ["c"], number: 2 },
      { key: "c", deps: ["a"], number: 3 },
    ];
    expect(findCycle(tasks)).toEqual(["a", "b", "c", "a"]);
    expect(findCycle([{ key: "a", deps: ["project:x"], number: 1 }])).toBeNull();
  });

  it("refuses a dependency that closes a cycle", () => {
    const schema = task("T-1");
    const check = checkTaskEdit(db, { ...schema, deps: ["settings-row"] }, known);
    expect(check.errors).toEqual(["That makes a cycle: schema → settings-row → route → schema"]);
  });

  it("refuses unknown tasks, projects and decisions, and a task on itself", () => {
    const route = task("T-4");
    const check = checkTaskEdit(
      db,
      { ...route, deps: [...route.deps, "nope", "project:ghost", "decision:D-99", "route"] },
      known,
    );
    expect(check.errors).toEqual([
      "No task nope in this project",
      "No project ghost",
      "No decision D-99",
      "route can't depend on itself",
      expect.stringMatching(/^That makes a cycle/),
    ]);
  });

  it("warns, without blocking, when unordered tasks touch the same paths", () => {
    const docs = task("T-2");
    const check = checkTaskEdit(
      db,
      { ...docs, state: "planned", scope: ["services/caffeine-api/src/modules/db/**"] },
      known,
    );
    expect(check.errors).toEqual([]);
    expect(check.warnings).toEqual([
      "route and docs both touch services/caffeine-api/src/modules/db/**; they run one after the other",
      "tests and docs both touch services/caffeine-api/src/modules/db/**; they run one after the other",
    ]);
    // route and tests already overlap: the edit doesn't repeat what was there.
    expect(overlapWarnings(db)).toEqual([
      "route and tests both touch services/caffeine-api/src/modules/db/**; they run one after the other",
    ]);
  });

  it("an added dependency that orders two overlapping tasks clears their warning", () => {
    const tests = task("T-5");
    const check = checkTaskEdit(db, { ...tests, deps: [...tests.deps, "route"] }, known);
    expect(check).toEqual({ errors: [], warnings: [] });
  });

  it("keys new tasks uniquely", () => {
    expect(uniqueKey("Route tests", ["route-tests"])).toBe("route-tests-2");
    expect(uniqueKey("!!!", [])).toBe("task");
  });
});

describe("project dependency rows", () => {
  it("lists current dependencies that are no candidates, so they can be unchecked", () => {
    const snapshot = round3Snapshot();
    const integration = snapshot.projects.find((p) => p.kind === "integration")!;
    const done = snapshot.projects.find((p) => p.status === "done")!;
    const target = snapshot.projects.find(
      (p) => p.slug !== integration.slug && p.slug !== done.slug && p.kind !== "integration",
    )!;
    target.depends_on = [integration.slug, done.slug, "gone-project"];
    const rows = dependsRows(snapshot, target.slug);
    const note = (slug: string) => rows.find((r) => r.slug === slug)?.note;
    expect(note(integration.slug)).toBe("integration");
    expect(note(done.slug)).toBe("done");
    expect(note("gone-project")).toBe("not found");
    expect(rows.some((r) => r.slug === target.slug)).toBe(false);
    const others = snapshot.projects.filter(
      (p) => p.kind === "integration" && p.slug !== integration.slug,
    );
    for (const p of others) expect(note(p.slug)).toBeUndefined();
  });
});

describe("keysToRefs", () => {
  it("names tasks by ref in check messages, leaving paths and words alone", () => {
    expect(keysToRefs(db, "That makes a cycle: schema → settings-row → route → schema")).toBe(
      "That makes a cycle: T-1 → T-6 → T-4 → T-1",
    );
    expect(
      keysToRefs(
        db,
        "tests and route both touch services/caffeine-api/src/modules/db/**; they run",
      ),
    ).toBe("T-5 and T-4 both touch services/caffeine-api/src/modules/db/**; they run");
  });
});
