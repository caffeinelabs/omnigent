import { beforeEach, describe, expect, it } from "vitest";
import { teamMember, teamRows } from "../agents/teamsModel";
import {
  TEAM_SLUG,
  teamCatalog,
  teamSnapshot,
  teamSpec,
  teamVersions,
} from "./__fixtures__/teamFixtures";
import {
  EMPTY_STAGING,
  type StagedTeamChange,
  type TeamStaging,
  benchRate,
  changeWords,
  chartMembers,
  clearedStaging,
  definitionChanges,
  dropChange,
  memberByName,
  modelOptions,
  normalizeStaging,
  removeLock,
  restoreChange,
  seated,
  stageChange,
  stagedTeam,
  teamBase,
  teamChangedIn,
  teamDiffLines,
  teamFacts,
  teamHistory,
  teamRate,
  undoLeft,
} from "./teamModel";
import {
  readTeamStaging,
  resetTeamStaging,
  stageTeamChanges,
  writeTeamStaging,
} from "./teamStaging";
import { benchResponse } from "../agents/__fixtures__/teamsFixtures";

const catalog = teamCatalog();
const base = teamBase(TEAM_SLUG, teamSpec("standard"), { "M-2": "(sample) brief" });

function stageAll(...changes: StagedTeamChange[]): TeamStaging {
  return changes.reduce((s, c) => stageChange(s, c, base, catalog), EMPTY_STAGING);
}

const rexToClaude: StagedTeamChange = {
  kind: "model",
  member: "M-3",
  vendor: "claude",
  model: "sonnet",
};

beforeEach(() => resetTeamStaging());

describe("staging", () => {
  it("stages, replaces and drops a model change put back to today's", () => {
    let s = stageAll(rexToClaude);
    expect(s.changes).toEqual([rexToClaude]);
    s = stageChange(s, { ...rexToClaude, model: "opus" }, base, catalog);
    expect(s.changes).toHaveLength(1);
    expect(s.changes[0]).toMatchObject({ model: "opus" });
    // Back to codex · default (the bundle's gpt-5-codex): nothing staged.
    s = stageChange(
      s,
      { kind: "model", member: "M-3", vendor: "codex", model: null },
      base,
      catalog,
    );
    expect(s.changes).toEqual([]);
    // Naming the bundle's default explicitly is the same as following it.
    s = stageChange(
      s,
      { kind: "model", member: "M-3", vendor: "codex", model: "gpt-5-codex" },
      base,
      catalog,
    );
    expect(s.changes).toEqual([]);
  });

  it("drops a change by index, and Apply or Undo leave nothing staged", () => {
    const s = stageAll(rexToClaude, { kind: "budget", value: 80 }, { kind: "builders", value: 3 });
    expect(dropChange(s, 1).changes.map((c) => c.kind)).toEqual(["model", "builders"]);
    // When and Scope go back to their defaults too: the next change starts from them.
    expect(clearedStaging()).toEqual({ changes: [], when: "after_current", scope: "project" });
  });

  it("removing a member drops its other changes; Keep (remove again) puts it back", () => {
    let s = stageAll(rexToClaude, { kind: "brief", member: "M-3", text: "(sample) do x" });
    s = stageChange(s, { kind: "remove", member: "M-3" }, base, catalog);
    expect(s.changes).toEqual([{ kind: "remove", member: "M-3" }]);
    s = stageChange(s, { kind: "remove", member: "M-3" }, base, catalog);
    expect(s.changes).toEqual([]);
  });

  it("a new member's model edits its add, and removing it drops the add", () => {
    let s = stageAll({ kind: "add", key: "new-1", role: "reviewer", vendor: "codex", model: null });
    s = stageChange(
      s,
      { kind: "model", member: "new-1", vendor: "claude", model: "opus" },
      base,
      catalog,
    );
    expect(s.changes).toEqual([
      { kind: "add", key: "new-1", role: "reviewer", vendor: "claude", model: "opus" },
    ]);
    s = stageChange(s, { kind: "remove", member: "new-1" }, base, catalog);
    expect(s.changes).toEqual([]);
  });

  it("a brief back to its text drops out; builders and budget back to today's too", () => {
    expect(stageAll({ kind: "brief", member: "M-2", text: " (sample) brief " }).changes).toEqual(
      [],
    );
    expect(
      stageAll({ kind: "builders", value: 3 }, { kind: "builders", value: 2 }).changes,
    ).toEqual([]);
    expect(stageAll({ kind: "budget", value: 60 }).changes).toEqual([]);
  });

  it("a restore replaces everything staged and nothing stages beside it", () => {
    const restore: StagedTeamChange = { kind: "restore", version: 10, team: null };
    let s = stageAll(rexToClaude, restore);
    expect(s.changes).toEqual([restore]);
    s = stageChange(s, { kind: "budget", value: 90 }, base, catalog);
    expect(s.changes).toEqual([restore]);
  });

  it("folds changes staged from outside (the palette) and keys their adds", () => {
    const raw: TeamStaging = {
      ...EMPTY_STAGING,
      changes: [
        rexToClaude,
        { ...rexToClaude, model: "opus" },
        { kind: "add", key: "", role: "reviewer", vendor: "claude", model: null },
        { kind: "add", key: "", role: "ic", vendor: "claude", model: null },
      ],
    };
    const s = normalizeStaging(raw, base, catalog);
    expect(s.changes.map((c) => c.kind)).toEqual(["model", "add", "add"]);
    expect(s.changes[0]).toMatchObject({ model: "opus" });
    expect(s.changes.slice(1).map((c) => (c.kind === "add" ? c.key : ""))).toEqual([
      "new-1",
      "new-2",
    ]);
  });

  it("keeps staged changes per project across readers, and in session storage", () => {
    stageTeamChanges("f1", TEAM_SLUG, [rexToClaude], base);
    expect(readTeamStaging("f1", TEAM_SLUG).changes).toEqual([rexToClaude]);
    expect(readTeamStaging("f1", "other").changes).toEqual([]);
    expect(window.sessionStorage.getItem(`omnigent.factory.team-staged.f1/${TEAM_SLUG}`)).toContain(
      "M-3",
    );
    writeTeamStaging("f1", TEAM_SLUG, clearedStaging());
    expect(
      window.sessionStorage.getItem(`omnigent.factory.team-staged.f1/${TEAM_SLUG}`),
    ).toBeNull();
  });
});

describe("the staged team", () => {
  it("tags staged, new and removed members", () => {
    const team = stagedTeam(
      base,
      stageAll(
        rexToClaude,
        { kind: "add", key: "new-1", role: "reviewer", vendor: "claude", model: "opus" },
        { kind: "remove", member: "M-2" },
        { kind: "budget", value: 80 },
      ),
      catalog,
    );
    const tag = (key: string) => team.tiles.find((t) => t.key === key)?.tag;
    expect(tag("M-3")).toBe("staged");
    expect(team.tiles.find((t) => t.key === "M-3")?.before).toEqual({
      vendor: "codex",
      model: "gpt-5-codex",
    });
    expect(tag("new-1")).toBe("new");
    expect(tag("M-2")).toBe("remove");
    expect(tag("M-4")).toBeNull();
    expect(team.budget).toBe(80);
    expect(seated(team)).toHaveLength(6);
  });

  it("switches template like the server: fitting members stay, seats are added, the rest go", () => {
    const team = stagedTeam(base, stageAll({ kind: "template", value: "pair" }), catalog);
    expect(team.template).toBe("pair");
    expect(team.tiles.find((t) => t.key === "M-3")?.tag).toBe("remove");
    const added = team.tiles.filter((t) => t.tag === "new");
    expect(added).toHaveLength(1);
    expect(added[0]).toMatchObject({ role: "ic", vendor: "claude", locked: "template" });
    expect(seated(team).filter((t) => t.role === "ic")).toHaveLength(2);
  });

  it("restores a version's team: kept ids change in place, the rest come back new", () => {
    const old = {
      template: "standard",
      builders_at_once: 1,
      budget_usd: null,
      members: [
        { id: "M-1", role: "project_lead", vendor: "claude", model: null },
        { id: "M-2", role: "ic", vendor: "claude", model: "opus" },
        { id: "M-4", role: "reviewer", vendor: "codex", model: null },
        { id: "M-9", role: "ic", vendor: "codex", model: null },
      ],
    };
    const team = stagedTeam(base, stageAll({ kind: "restore", version: 10, team: old }), catalog);
    expect(team.builders).toBe(1);
    expect(team.tiles.find((t) => t.key === "M-2")?.tag).toBe("staged");
    expect(
      team.tiles
        .filter((t) => t.tag === "remove")
        .map((t) => t.key)
        .sort(),
    ).toEqual(["M-3", "M-5", "M-6"]);
    expect(team.tiles.filter((t) => t.tag === "new")).toHaveLength(1);
  });
});

describe("locks", () => {
  const facts = teamFacts(catalog);
  it("locks the last member a gate needs, and a second reviewer unlocks the first", () => {
    const now = stagedTeam(base, EMPTY_STAGING, catalog);
    const quill = now.tiles.find((t) => t.key === "M-4")!;
    expect(removeLock(quill, now, facts, catalog)).toBe(
      "Can't remove: the review gate needs a reviewer (Tier 0)",
    );
    expect(
      removeLock(
        now.tiles.find((t) => t.key === "M-5")!,
        now,
        facts,
        catalog,
      ),
    ).toMatch(/verify gate needs an env keeper/);
    expect(
      removeLock(
        now.tiles.find((t) => t.key === "M-2")!,
        now,
        facts,
        catalog,
      ),
    ).toBeNull();
    expect(
      removeLock(
        now.tiles.find((t) => t.key === "M-1")!,
        now,
        facts,
        catalog,
      ),
    ).toMatch(/project lead stays/);
    const two = stagedTeam(
      base,
      stageAll({ kind: "add", key: "new-1", role: "reviewer", vendor: "claude", model: "opus" }),
      catalog,
    );
    expect(
      removeLock(
        two.tiles.find((t) => t.key === "M-4")!,
        two,
        facts,
        catalog,
      ),
    ).toBeNull();
  });

  it("the demo and env gates only lock while the charter needs them", () => {
    const team = stagedTeam(base, EMPTY_STAGING, catalog);
    const off = { envNeeded: false, previewsOn: false };
    expect(
      removeLock(
        team.tiles.find((t) => t.key === "M-5")!,
        team,
        off,
        catalog,
      ),
    ).toBeNull();
    expect(
      removeLock(
        team.tiles.find((t) => t.key === "M-6")!,
        team,
        off,
        catalog,
      ),
    ).toBeNull();
  });
});

describe("options", () => {
  it("lists only what a role can run, the default first, with bench rates", () => {
    const options = modelOptions(catalog, benchResponse(), "ic");
    expect(options.map((o) => o.label)).toEqual([
      "claude · sonnet · 61 %",
      "claude · opus · 67 % (low n)",
      "claude · haiku",
      "codex · gpt-5-codex · 75 %",
    ]);
    expect(options[0]).toMatchObject({ value: "claude|", model: null });
  });

  it("a project lead has no Codex: listed disabled", () => {
    const options = modelOptions(catalog, null, "project_lead");
    expect(options.find((o) => o.vendor === "codex")).toMatchObject({ disabled: true });
    expect(options.filter((o) => !o.disabled).every((o) => o.vendor === "claude")).toBe(true);
  });

  it("says a bench rate with its low n", () => {
    expect(benchRate(null)).toBeNull();
    const low = benchResponse().rows.find((r) => r.low_n && r.quality)!;
    expect(benchRate(low)).toMatch(/\(low n\)$/);
  });
});

describe("words", () => {
  it("says a builder's review route in the preview's words, about that builder only", () => {
    const effect = (reviewRoutes?: string[]) =>
      changeWords(rexToClaude, {
        slug: TEAM_SLUG,
        base,
        catalog,
        when: "after_current",
        names: new Map([["M-3", "Rex"]]),
        reviewRoutes,
      }).effect;
    expect(effect()).not.toContain("PRs then get");
    expect(effect(["M-3's PRs then get a Codex review (cross-vendor rule)."])).toContain(
      "Rex's PRs then get a Codex review (cross-vendor rule).",
    );
    expect(effect(["M-2's PRs then get a Codex review (cross-vendor rule)."])).not.toContain(
      "PRs then get",
    );
  });
});

describe("definition changes", () => {
  it("compiles each staged change to the team paths, in staging order", () => {
    const s = stageAll(
      rexToClaude,
      { kind: "add", key: "new-1", role: "reviewer", vendor: "claude", model: "opus" },
      { kind: "remove", member: "M-6" },
      { kind: "builders", value: 3 },
      { kind: "budget", value: 80 },
      { kind: "brief", member: "M-2", text: "" },
    );
    expect(definitionChanges(TEAM_SLUG, s)).toEqual([
      {
        op: "replace",
        path: "team.projects.csv-export.members.M-3",
        value: { vendor: "claude", model: "sonnet" },
      },
      {
        op: "add",
        path: "team.projects.csv-export.members",
        value: { role: "reviewer", vendor: "claude", model: "opus" },
      },
      { op: "remove", path: "team.projects.csv-export.members.M-6" },
      { op: "replace", path: "team.projects.csv-export.builders_at_once", value: 3 },
      { op: "replace", path: "team.projects.csv-export.budget_usd", value: 80 },
      { op: "remove", path: "team.projects.csv-export.briefs.M-2" },
    ]);
  });

  it("restores a team with one change (or back to the bundle), and writes the template on request", () => {
    const old = { template: "standard", members: [] };
    expect(
      definitionChanges(TEAM_SLUG, stageAll({ kind: "restore", version: 10, team: old })),
    ).toEqual([{ op: "replace", path: "team.projects.csv-export", value: old }]);
    expect(
      definitionChanges(TEAM_SLUG, stageAll({ kind: "restore", version: 9, team: null })),
    ).toEqual([{ op: "remove", path: "team.projects.csv-export" }]);
    const s = { ...stageAll(rexToClaude), scope: "template" as const };
    const out = definitionChanges(TEAM_SLUG, s, stagedTeam(base, s, catalog), catalog);
    expect(out.at(-1)).toMatchObject({ op: "replace", path: "team.templates.standard.roles" });
    expect(
      (out.at(-1)!.value as { role: string; count: number }[]).find((r) => r.role === "ic"),
    ).toMatchObject({ vendor: "claude", count: 2 });
  });
});

describe("live sessions", () => {
  it("matches sessions to members by role and vendor", () => {
    const snapshot = teamSnapshot();
    const rows = teamRows(snapshot, undefined).filter((r) => r.project === TEAM_SLUG);
    const live = rows.map((r) => teamMember(r, snapshot.factory.root_session_id));
    const { members, outside } = chartMembers(teamSpec(), live);
    const handle = (id: string) => members.find((m) => m.spec?.id === id)?.live?.row.handle;
    expect(handle("M-1")).toBe("nova");
    expect(handle("M-2")).toBe("ivy");
    expect(handle("M-3")).toBe("rex");
    expect(handle("M-4")).toBe("quill");
    expect(handle("M-5")).toBe("kip");
    expect(handle("M-6")).toBe("dot");
    expect(outside).toEqual([]);
  });

  it("finds the member a command names, by its handle or its session's", () => {
    const snapshot = teamSnapshot();
    const live = teamRows(snapshot, undefined)
      .filter((r) => r.project === TEAM_SLUG)
      .map((r) => teamMember(r, snapshot.factory.root_session_id));
    expect(memberByName(teamSpec(), live, "Rex")?.id).toBe("M-3");
    expect(memberByName(teamSpec(), live, "m-4")?.id).toBe("M-4");
    expect(memberByName(teamSpec(), live, "nobody")).toBeNull();
  });

  it("keeps each session on its member as activity changes, and asks when the name is shared", () => {
    const snapshot = teamSnapshot("pair");
    const spec = teamSpec("pair");
    const live = teamRows(snapshot, undefined)
      .filter((r) => r.project === TEAM_SLUG)
      .map((r) => teamMember(r, snapshot.factory.root_session_id));
    const seats = (sessions: typeof live) => {
      const { members } = chartMembers(spec, sessions);
      const seat = (handle: string) =>
        members.find((m) => m.sessions.some((s) => s.row.handle === handle))?.spec?.id;
      return { ivy: seat("ivy"), rex: seat("rex") };
    };
    const before = seats(live);
    expect(new Set(Object.values(before))).toEqual(new Set(["M-2", "M-7"]));
    // Rex gets busy and Ivy goes quiet: neither moves to the other's seat.
    const busy = live.map((m) =>
      m.row.handle === "rex"
        ? { ...m, state: "working" as const, row: { ...m.row, last_activity_at: 4e9 } }
        : m.row.handle === "ivy"
          ? { ...m, state: "idle" as const, row: { ...m.row, last_activity_at: 1 } }
          : m,
    );
    expect(seats(busy)).toEqual(before);
    // Two Claude builders: "rex" could be either seat's session, so the palette asks.
    expect(memberByName(spec, live, "rex")).toBeNull();
    expect(memberByName(spec, live, "M-7")?.id).toBe("M-7");
  });

  it("estimates the team's rate the server's way", () => {
    const spec = teamSpec();
    // others 1.2 + 0.6 + 0.4 + 0.5; builders (1.6 + 1.3) / 2 × 2.
    expect(teamRate(catalog, spec.members, spec.builders_at_once)).toBe(5.6);
    expect(teamRate(null, spec.members, 2)).toBeNull();
  });
});

describe("history", () => {
  it("lists the versions that changed the team, newest first, with what changed", () => {
    const { data, pinned } = teamVersions();
    const rows = teamHistory(data, TEAM_SLUG, pinned);
    expect(rows.map((r) => r.version)).toEqual([12, 11, 10]);
    expect(rows[0]!.lines).toEqual(["Builders at once 1 → 2"]);
    expect(rows[0]!.current).toBe(true);
    expect(rows[2]!.lines).toEqual([
      "Got its own team: standard template, 6 members, 1 builder at once, budget $60",
    ]);
    expect(rows[1]!.lines).toContain("Instructions for M-2 set");
  });

  it("names the version that changed the team in force, not an undone or unpinned one", () => {
    const applied = teamVersions({ justApplied: true });
    expect(teamChangedIn(teamHistory(applied.data, TEAM_SLUG, 13), applied.data, 13)).toBe(13);
    // Undo re-pins v12: v13 stays listed first, but v12's team is in force.
    const { data } = teamVersions({ undone: true });
    const rows = teamHistory(data, TEAM_SLUG, 12);
    expect(rows[0]).toMatchObject({ version: 13, undoneBy: "owner@example.test" });
    expect(teamChangedIn(rows, data, 12)).toBe(12);
    // An owner pins v10 from Steer: v10 made that team.
    expect(teamChangedIn(teamHistory(data, TEAM_SLUG, 10), data, 10)).toBe(10);
    // v9 had none and its parents are not listed: unknown.
    expect(teamChangedIn(rows, data, 9)).toBeNull();
    expect(teamChangedIn(rows, data, null)).toBeNull();
  });

  it("restores an earlier team, never the one in force", () => {
    const { data, pinned } = teamVersions();
    const rows = teamHistory(data, TEAM_SLUG, pinned);
    expect(restoreChange(rows[0]!, rows[0]!.team)).toBeNull();
    expect(restoreChange(rows[2]!, rows[0]!.team)).toMatchObject({ kind: "restore", version: 10 });
  });

  it("offers Undo for 10 minutes to the owner or whoever made it", () => {
    const { data, pinned } = teamVersions({ justApplied: true });
    const [latest] = teamHistory(data, TEAM_SLUG, pinned);
    const now = latest!.createdAt + 120;
    expect(undoLeft(latest, now, { userId: "owner@example.test", owner: false })).toBe(480);
    expect(undoLeft(latest, now, { userId: "someone@example.test", owner: false })).toBeNull();
    expect(undoLeft(latest, now, { userId: null, owner: true })).toBe(480);
    expect(undoLeft(latest, latest!.createdAt + 601, { userId: null, owner: true })).toBeNull();
    expect(undoLeft({ ...latest!, undoneBy: "x" }, now, { userId: null, owner: true })).toBeNull();
  });

  it("says a move back to the bundle and member changes in words", () => {
    const before = {
      members: [
        { id: "M-1", role: "project_lead", vendor: "claude", model: null },
        { id: "M-3", role: "ic", vendor: "codex", model: null },
      ],
    };
    const after = {
      members: [
        { id: "M-1", role: "project_lead", vendor: "claude", model: null },
        { id: "M-3", role: "ic", vendor: "claude", model: "sonnet" },
        { id: "M-7", role: "reviewer", vendor: "claude", model: "opus" },
      ],
    };
    expect(teamDiffLines(before, after)).toEqual([
      "M-3: codex · default → claude · sonnet",
      "Added M-7 (reviewer · claude · opus)",
    ]);
    expect(teamDiffLines(before, null)).toEqual(["Back to today's bundle"]);
    expect(teamDiffLines(before, before)).toEqual([]);
  });
});
