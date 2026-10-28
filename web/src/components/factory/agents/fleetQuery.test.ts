import { describe, expect, it } from "vitest";
import { agentRows } from "./__fixtures__/opsFixtures";
import { filterFleet, fleetAgent, fleetCounts, groupFleet, waitingOnViewer } from "./fleetModel";
import {
  globMatch,
  parseFleetQuery,
  queryFieldValue,
  setQueryField,
  tokenize,
  toggleQueryTerm,
} from "./fleetQuery";
import { ROUND3_NOW_S, round3Snapshot, viewerOf } from "../shell/__fixtures__/shellFixtures";

const fleet = () => agentRows().map((row) => fleetAgent(row, ROUND3_NOW_S));
const titles = (query: string) =>
  filterFleet(fleet(), parseFleetQuery(query)).map((a) => a.row.title);

describe("fleet query parsing", () => {
  it("parses the spec's example into four terms", () => {
    expect(parseFleetQuery("state:running tool:Bash cost>2 project:db-*")).toEqual({
      terms: [
        { kind: "match", field: "state", value: "running", negate: false },
        { kind: "match", field: "tool", value: "bash", negate: false },
        { kind: "compare", field: "cost", op: ">", value: 2, negate: false },
        { kind: "match", field: "project", value: "db-*", negate: false },
      ],
      errors: [],
    });
  });

  it("reads negation, quotes, aliases, comparators and idle units", () => {
    const { terms, errors } = parseFleetQuery(
      '-role:PL tool:"pnpm test" $/h>=1.5 turn<=10 idle>2h status:waiting',
    );
    expect(errors).toEqual([]);
    expect(terms).toEqual([
      { kind: "match", field: "role", value: "pl", negate: true },
      { kind: "match", field: "tool", value: "pnpm test", negate: false },
      { kind: "compare", field: "rate", op: ">=", value: 1.5, negate: false },
      { kind: "compare", field: "turn", op: "<=", value: 10, negate: false },
      { kind: "compare", field: "idle", op: ">", value: 120, negate: false },
      { kind: "match", field: "state", value: "waiting", negate: false },
    ]);
  });

  it("keeps bare words as text and reports what it can't read", () => {
    const { terms, errors } = parseFleetQuery("route colour:red is:busy >3 cost>2h");
    expect(terms).toEqual([{ kind: "match", field: "text", value: "route", negate: false }]);
    expect(errors).toEqual(["colour:red", "is:busy", ">3", "cost>2h"]);
  });

  it("tokenizes quoted phrases as one token", () => {
    expect(tokenize('a "b c" d:"e f"  ')).toEqual(["a", "b c", "d:e f"]);
  });

  it("globs with * and matches substrings without", () => {
    expect(globMatch("db-*", "db-version")).toBe(true);
    expect(globMatch("db-*", "api-version")).toBe(false);
    expect(globMatch("ver", "api-version")).toBe(true);
    expect(globMatch("*-r2", "ic:route-r2")).toBe(true);
  });

  it("edits field terms and toggles saved views", () => {
    expect(setQueryField("state:idle tool:Bash", "state", "running")).toBe(
      "tool:Bash state:running",
    );
    expect(setQueryField("state:idle tool:Bash", "state", null)).toBe("tool:Bash");
    expect(queryFieldValue("x project:db-*", "project")).toBe("db-*");
    expect(toggleQueryTerm("cost>2", "is:looping")).toBe("cost>2 is:looping");
    expect(toggleQueryTerm("cost>2 is:looping", "is:looping")).toBe("cost>2");
  });
});

describe("fleet query on the round-3 fleet", () => {
  it("finds the repeating implementer by state, tool and cost", () => {
    expect(titles("state:running tool:Bash cost>2 project:db-*")).toEqual(["ic:route-r2"]);
  });

  it("narrows by role short name and negation", () => {
    expect(titles("role:PL").sort()).toEqual(
      [
        "PL · api-version",
        "PL · base-env",
        "PL · cache-warm",
        "PL · db-version",
        "PL · docs-factory-envs",
        "PL · ux-badge",
      ].sort(),
    );
    expect(titles("role:PL -project:*-version").length).toBe(4);
  });

  it("evaluates the saved views", () => {
    expect(titles("is:looping")).toEqual(expect.arrayContaining(["ic:route-r2"]));
    expect(titles("is:waiting")).toContain("PL · db-version");
    expect(titles("is:expensive")).toEqual(
      expect.arrayContaining(["Atlas", "ic:route-r1", "ic:route-r2"]),
    );
  });

  it("waits on me: the decisions claimed by, assigned to or routed to the viewer", () => {
    const { decisions } = round3Snapshot();
    const waitingFor = (role: "creator" | "operator" | "viewer") => {
      const mine = waitingOnViewer(decisions, viewerOf(role));
      return filterFleet(
        agentRows().map((row) => fleetAgent(row, ROUND3_NOW_S, mine)),
        parseFleetQuery("is:waiting"),
      )
        .map((a) => a.row.title)
        .sort();
    };
    // D-2 (the db-version PL's) is claimed by Mia; D-33 (Atlas's) goes to operators.
    expect(waitingFor("operator")).toEqual(["Atlas", "PL · db-version"]);
    expect(waitingFor("creator")).not.toContain("PL · db-version");
    expect(waitingFor("creator")).toContain("Atlas");
    expect(waitingFor("viewer")).toEqual([]);
    expect(waitingOnViewer(decisions, null)).toBeNull();
  });

  it("matches flags, tasks and loops", () => {
    expect(titles("flag:repeat")).toEqual(["ic:route-r2"]);
    expect(titles("flag:dead")).toEqual(["PL · cache-warm"]);
    expect(titles("task:T-4").sort()).toEqual(["ic:route", "ic:route-r1", "ic:route-r2"]);
    expect(titles("loop:exhausted").length).toBe(3);
  });

  it("groups by project (leads last) and counts the fleet", () => {
    const groups = groupFleet(fleet(), "project", ["api-version", "db-version"]);
    expect(groups[0]!.label).toBe("api-version");
    expect(groups[1]!.label).toBe("db-version");
    expect(groups[1]!.agents[0]!.row.title).toBe("PL · db-version");
    expect(groups.at(-1)!.label).toBe("Leads");
    expect(fleetCounts(fleet())).toMatchObject({ total: 41, dead: 1 });
  });
});
