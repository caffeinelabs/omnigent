import { describe, expect, it } from "vitest";
import { liveRunSnapshot } from "../__fixtures__/liveRun";
import {
  type CockpitMode,
  cockpitRedirect,
  factoryLocation,
  findPr,
  legacyFactoryRedirect,
  presenceLocation,
  presenceWhere,
} from "./shellRoutes";

const FID = "f4f4ba9c77725331b12f0fa7072dc4d3";
const PR_9630 = "f74d5967c53455139e85ba1b402a5bd4";
const prs = liveRunSnapshot().prs;

function redirect(rest: string, query: string, withPrs = true) {
  return legacyFactoryRedirect(FID, rest, new URLSearchParams(query), withPrs ? prs : undefined);
}

describe("factoryLocation", () => {
  it.each([
    ["", { page: "overview", id: null }],
    ["projects", { page: "projects", id: null }],
    ["projects/db-version", { page: "project", id: "db-version" }],
    [`prs/${PR_9630}`, { page: "pr", id: PR_9630 }],
    ["prs", { page: "other", id: null }],
    ["activity", { page: "activity", id: null }],
    ["settings", { page: "settings", id: "people" }],
    ["settings/people", { page: "settings", id: "people" }],
    ["room", { page: "room", id: null }],
    ["room/project%3Adb-version", { page: "room", id: "project:db-version" }],
    ["graph", { page: "graph", id: null }],
    ["decisions", { page: "decisions", id: null }],
    ["timeline", { page: "timeline", id: null }],
    ["insights", { page: "insights", id: null }],
    ["improvements", { page: "improvements", id: null }],
    ["improvements/C-14", { page: "proposal", id: "C-14" }],
    ["settings/budgets", { page: "settings", id: "budgets" }],
    ["discovery", { page: "discovery", id: null }],
    ["discovery/flow", { page: "discovery", id: "flow" }],
    ["teams/bench", { page: "teams", id: "bench" }],
    ["steer/budgets", { page: "steer", id: "budgets" }],
    ["debug", { page: "debug", id: null }],
    ["debug/timeline", { page: "debug", id: "timeline" }],
    ["debug/incidents/12", { page: "debug", id: "incidents", sub: "12" }],
    ["debug/agent/abc", { page: "debug", id: "agent", sub: "abc" }],
    ["nowhere", { page: "other", id: "nowhere" }],
  ])("reads %j", (rest, expected) => {
    expect(factoryLocation(rest)).toEqual(expected);
  });
});

describe("presence location", () => {
  it("names the page the server shows next to a person", () => {
    expect(presenceLocation(factoryLocation(""))).toBe("overview");
    expect(presenceLocation(factoryLocation("projects/db-version"))).toBe("project:db-version");
    expect(presenceLocation(factoryLocation(`prs/${PR_9630}`))).toBe(`pr:${PR_9630}`);
    expect(presenceLocation(factoryLocation("room/factory"))).toBe("room:factory");
    expect(presenceLocation(factoryLocation("nowhere"))).toBeNull();
    expect(presenceLocation(factoryLocation("insights"))).toBe("insights");
    expect(presenceLocation(factoryLocation("improvements/C-14"))).toBe("proposal:C-14");
    expect(presenceWhere("improvements")).toBe("Improvements");
    // Every location fits the server's pattern.
    const pattern = /^[a-z][a-z_]{0,31}(?::[A-Za-z0-9_.:#!@/+-]{1,96})?$/;
    for (const rest of ["", "projects", "projects/a-b", `prs/${PR_9630}`, "activity", "settings"]) {
      expect(presenceLocation(factoryLocation(rest))).toMatch(pattern);
    }
  });

  it("says where someone is in words", () => {
    expect(presenceWhere("overview")).toBe("Overview");
    expect(presenceWhere("project:db-version")).toBe("db-version page");
    expect(presenceWhere("pr:abc")).toBe("a PR page");
    expect(presenceWhere(null)).toBe("here");
  });
});

describe("legacyFactoryRedirect", () => {
  // The server still writes these: PR bodies and demo comments link
  // `/factory/<fid>?review=<n>`; the stable preview link redirects to
  // `/factory/<fid>?review=<n>&pr=<id>&preview=unavailable` (api/pr_preview.py).
  it("lands a PR body's ?review=<n> on the PR page by its id", () => {
    expect(redirect("", "review=9630")).toEqual({ to: `/factory/${FID}/prs/${PR_9630}` });
  });

  it("lands the stable preview link on the PR page with preview=unavailable", () => {
    expect(redirect("", `review=9630&pr=${PR_9630}&preview=unavailable`)).toEqual({
      to: `/factory/${FID}/prs/${PR_9630}?preview=unavailable`,
    });
    // The pr id alone is enough, even before the snapshot is in.
    expect(redirect("", `pr=${PR_9630}&preview=unavailable`, false)).toEqual({
      to: `/factory/${FID}/prs/${PR_9630}?preview=unavailable`,
    });
  });

  it("waits for the PR list to resolve a number", () => {
    expect(redirect("", "review=9630", false)).toBe("wait");
  });

  it("sends an unknown PR number to its page, which says it isn't tracked", () => {
    expect(redirect("", "review=9999")).toEqual({ to: `/factory/${FID}/prs/9999` });
  });

  it("sends the old drawer's ?project= to the project page, keeping decision and preview", () => {
    expect(redirect("", "project=api-version")).toEqual({
      to: `/factory/${FID}/n/api-version`,
    });
    expect(redirect("", "project=db-version&decision=D-2")).toEqual({
      to: `/factory/${FID}/n/db-version?decision=D-2`,
    });
    // A review stacked on the project drawer wins: the PR has its own page.
    expect(redirect("", "project=api-version&review=9630")).toEqual({
      to: `/factory/${FID}/prs/${PR_9630}`,
    });
  });

  it("sends ?tab=activity to Activity and ?tab=board to the Overview", () => {
    expect(redirect("", "tab=activity")).toEqual({ to: `/factory/${FID}/activity` });
    expect(redirect("", "tab=board")).toEqual({ to: `/factory/${FID}` });
    expect(redirect("", "tab=board&decision=D-2")).toEqual({
      to: `/factory/${FID}?decision=D-2`,
    });
  });

  it("turns a project page's ?tab= into its section anchor", () => {
    expect(redirect("projects/api-version", "tab=demos")).toEqual({
      to: `/factory/${FID}/n/api-version`,
    });
    expect(redirect("projects/api-version", "tab=overview&decision=D-4")).toEqual({
      to: `/factory/${FID}/n/api-version?decision=D-4`,
    });
    expect(redirect("projects/api-version", "review=9630")).toEqual({
      to: `/factory/${FID}/prs/${PR_9630}`,
    });
  });

  it("leaves current links alone", () => {
    expect(redirect("", "")).toBeNull();
    expect(redirect("", "decision=D-2")).toBeNull();
    expect(redirect(`prs/${PR_9630}`, "preview=unavailable")).toBeNull();
    expect(redirect("projects", "view=cards")).toBeNull();
  });
});

describe("findPr", () => {
  it("finds a PR by id or number", () => {
    expect(findPr(prs, PR_9630)?.number).toBe(9630);
    expect(findPr(prs, "9631")?.number).toBe(9631);
    expect(findPr(prs, "nope")).toBeNull();
  });
});

describe("cockpitRedirect", () => {
  const BASE = `/factory/${FID}`;
  function land(rest: string, query: string, mode: CockpitMode, from: string | null = null) {
    return cockpitRedirect(FID, rest, new URLSearchParams(query), mode, from);
  }

  // Spec §2.2: every address of the classic cockpit lands somewhere in the new one.
  it.each([
    ["overview", "", `${BASE}`],
    ["agents", "", `${BASE}/teams/agents`],
    [
      "agents",
      "view=looping&q=state%3Arunning",
      `${BASE}/teams/agents?view=looping&q=state%3Arunning`,
    ],
    ["graph", "kind=org", `${BASE}/teams`],
    ["graph", "", `${BASE}/debug/graph`],
    ["graph", "kind=bus&scope=db-version", `${BASE}/debug/graph?kind=bus&scope=db-version`],
    [
      "timeline",
      "focus=db-version&at=1700000000000",
      `${BASE}/debug/timeline?focus=db-version&at=1700000000000`,
    ],
    ["activity", "", `${BASE}/debug/activity`],
    ["insights", "window=7d", `${BASE}/debug/insights?window=7d`],
    ["alerts", "class=finding", `${BASE}/debug/incidents?class=finding`],
    ["alerts/12", "", `${BASE}/debug/incidents/12`],
    ["alerts/F-7", "", `${BASE}/debug/incidents/F-7`],
    ["decisions", "", `${BASE}?peek=needs`],
    ["decisions", "decision=D-41", `${BASE}?peek=needs&decision=D-41`],
    ["improvements", "", `${BASE}/steer/versions`],
    ["improvements", "tab=history", `${BASE}/steer/versions?tab=history`],
    ["improvements", "tab=proposed", `${BASE}/steer/versions?tab=proposed`],
    ["improvements", "tab=definition", `${BASE}/steer/versions?tab=definition`],
    ["improvements", "tab=rules", `${BASE}/steer/alerts`],
    ["improvements", "tab=findings", `${BASE}/debug/incidents?class=finding`],
    ["improvements/C-14", "", `${BASE}/steer/versions?peek=C-14`],
    ["settings/autonomy", "", `${BASE}/steer/budgets`],
    ["settings/budgets", "", `${BASE}/steer/budgets`],
    ["settings/alerts", "", `${BASE}/steer/alerts`],
    ["settings/rules", "", `${BASE}/steer/alerts`],
    ["settings/definition", "", `${BASE}/steer/versions`],
    ["settings/notify", "", `${BASE}/settings/notifications`],
    ["room", "", `${BASE}?room=`],
    ["room/project%3Adb-version", "", `${BASE}?room=project%3Adb-version`],
    ["steer", "", `${BASE}/steer/overview`],
    ["steer/nowhere", "", `${BASE}/steer/overview`],
    // An earlier cockpit's "Cadence, previews and models".
    ["steer/definition", "", `${BASE}/steer/cadence`],
    ["debug", "", `${BASE}/debug/fleet`],
  ])("lands /%s?%s in the new cockpit", (rest, query, expected) => {
    expect(land(rest, query, "v2")).toBe(expected);
  });

  it("never carries a ?cockpit= link on: the switch reads it once", () => {
    expect(land("agents", "cockpit=v2&q=x", "v2")).toBe(`${BASE}/teams/agents?q=x`);
    expect(land("teams", "cockpit=v2", "classic")).toBe(`${BASE}/agents`);
    expect(land("room/factory", "cockpit=v2", "v2", `${BASE}/projects`)).toBe(
      `${BASE}/projects?room=factory`,
    );
  });

  it("opens a Room link over the page the viewer was on", () => {
    expect(land("room/factory", "", "v2", `${BASE}/projects?view=board`)).toBe(
      `${BASE}/projects?view=board&room=factory`,
    );
    expect(land("room", "", "v2", `${BASE}/n/db-version?room=old`)).toBe(
      `${BASE}/n/db-version?room=`,
    );
    expect(land("room/factory", "", "v2", `${BASE}?view=board`)).toBe(
      `${BASE}?view=board&room=factory`,
    );
  });

  it("opens a Room link of another factory over that factory's Home, not this page", () => {
    const other = "/factory/0123456789abcdef0123456789abcdef";
    expect(land("room/factory", "", "v2", `${other}/projects?view=board`)).toBe(
      `${BASE}?room=factory`,
    );
    // A factory id that only starts like this one is another factory too.
    expect(land("room/factory", "", "v2", `${BASE}0/projects`)).toBe(`${BASE}?room=factory`);
  });

  it.each([
    "",
    "projects",
    "projects/db-version",
    `prs/${PR_9630}`,
    "agents/abc",
    "discovery",
    "discovery/flow",
    "teams",
    "teams/agents",
    "steer/overview",
    "steer/strategy",
    "steer/product",
    "steer/sources",
    "steer/rubric",
    "steer/roles",
    "steer/templates",
    "steer/gates",
    "steer/versions",
    "steer/budgets",
    "steer/alerts",
    "steer/cadence",
    "debug/fleet",
    "debug/timeline",
    "debug/incidents/12",
    "debug/agent/abc",
    "settings/people",
    "settings/notifications",
    "settings/integrations",
  ])("keeps the new cockpit's own /%s", (rest) => {
    expect(land(rest, "", "v2")).toBeNull();
  });

  // A link someone copied from the new cockpit opens for a person on the classic one.
  it.each([
    ["discovery/flow", "", `${BASE}`],
    ["teams", "", `${BASE}/agents`],
    ["teams/agents", "q=sonnet", `${BASE}/agents?q=sonnet`],
    ["steer", "", `${BASE}/improvements`],
    ["steer/versions", "tab=history", `${BASE}/improvements?tab=history`],
    ["steer/versions", "peek=C-14", `${BASE}/improvements/C-14`],
    ["steer/versions", "peek=F-3", `${BASE}/improvements/F-3`],
    // The Room drawer over any page opens the classic Room page on that thread.
    ["projects/db-version", "room=project%3Adb-version", `${BASE}/chats/project%3Adb-version`],
    ["", "room=factory", `${BASE}/chats/factory`],
    ["projects", "room=", `${BASE}/chats`],
    ["teams", "room=factory&q=x", `${BASE}/chats/factory?q=x`],
    ["steer/budgets", "", `${BASE}/settings/budgets`],
    ["steer/alerts", "", `${BASE}/settings/alerts`],
    ["steer/definition", "", `${BASE}/settings/definition`],
    ["steer/cadence", "", `${BASE}/settings/definition`],
    ["steer/roles", "role=ic", `${BASE}/settings/definition?role=ic`],
    ["steer/strategy", "", `${BASE}/improvements`],
    ["steer/gates", "", `${BASE}/improvements`],
    ["debug", "", `${BASE}/agents`],
    ["debug/fleet", "", `${BASE}/agents`],
    ["debug/timeline", "focus=x", `${BASE}/timeline?focus=x`],
    ["debug/activity", "", `${BASE}/activity`],
    ["debug/graph", "kind=bus", `${BASE}/graph?kind=bus`],
    ["debug/incidents", "", `${BASE}/alerts`],
    ["debug/incidents/12", "", `${BASE}/alerts/12`],
    ["debug/insights", "", `${BASE}/insights`],
    ["debug/agent/abc", "tab=mail", `${BASE}/agents/abc?tab=mail`],
    ["settings/integrations", "", `${BASE}/settings/alerts`],
    ["settings/notify", "", `${BASE}/settings/notifications`],
    ["", "peek=needs&decision=D-4", `${BASE}/decisions?decision=D-4`],
    ["overview", "", `${BASE}`],
  ])("lands /%s?%s in the classic cockpit", (rest, query, expected) => {
    expect(land(rest, query, "classic")).toBe(expected);
  });

  it.each([
    "",
    "projects",
    "agents",
    "agents/abc",
    "timeline",
    "activity",
    "graph",
    "alerts/12",
    "decisions",
    "improvements",
    "improvements/C-14",
    "insights",
    "room/factory",
    "settings/budgets",
  ])("keeps the classic cockpit's own /%s", (rest) => {
    expect(land(rest, "", "classic")).toBeNull();
  });

  it("names the new places for presence, in the server's pattern", () => {
    const pattern = /^[a-z][a-z_]{0,31}(?::[A-Za-z0-9_.:#!@/+-]{1,96})?$/;
    const cases: [string, string][] = [
      ["discovery", "discovery"],
      ["teams/bench", "teams"],
      ["steer/budgets", "steer:budgets"],
      ["debug/timeline", "debug:timeline"],
      ["debug/agent/abc", "agent:abc"],
      ["debug/incidents/12", "incident:12"],
    ];
    for (const [rest, expected] of cases) {
      const where = presenceLocation(factoryLocation(rest));
      expect(where).toBe(expected);
      expect(where).toMatch(pattern);
    }
    expect(presenceWhere("steer:budgets")).toBe("Steer");
    expect(presenceWhere("debug:timeline")).toBe("Debug");
  });
});
