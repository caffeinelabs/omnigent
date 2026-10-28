import { describe, expect, it } from "vitest";
import { liveRunSnapshot } from "../__fixtures__/liveRun";
import {
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
    ["timeline", { page: "other", id: "timeline" }],
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
      to: `/factory/${FID}/projects/api-version`,
    });
    expect(redirect("", "project=db-version&decision=D-2")).toEqual({
      to: `/factory/${FID}/projects/db-version?decision=D-2`,
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
      to: `/factory/${FID}/projects/api-version#demos`,
    });
    expect(redirect("projects/api-version", "tab=overview&decision=D-4")).toEqual({
      to: `/factory/${FID}/projects/api-version?decision=D-4`,
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
