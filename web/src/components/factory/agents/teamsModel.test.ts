import { describe, expect, it } from "vitest";
import type { AgentRowView } from "@/lib/factoryApi";
import { teamsAgentRows, teamsSnapshot } from "./__fixtures__/teamsFixtures";
import {
  currentTeam,
  filterOf,
  groupTeams,
  handleName,
  matchesTeamFilter,
  modelLabel,
  roleGlyph,
  stateCounts,
  stateWord,
  teamLine,
  teamMember,
  teamRows,
  teamState,
  vendorModel,
} from "./teamsModel";

const snapshot = teamsSnapshot();
const ROOT = snapshot.factory.root_session_id;
const members = () => teamsAgentRows(snapshot).map((row) => teamMember(row, ROOT));
const byHandle = (handle: string) => members().find((m) => m.row.handle === handle)!;
const matching = (query: string) =>
  members()
    .filter((m) =>
      matchesTeamFilter(
        m,
        query,
        m.row.project === "csv-export" ? "(sample) People can export a report as CSV" : null,
      ),
    )
    .map((m) => m.row.handle)
    .sort();

describe("an agent's state", () => {
  it("reads stopped, stuck, waiting, working, done and idle from its row", () => {
    expect(byHandle("mira").state).toBe("stopped");
    expect(byHandle("dot").state).toBe("stuck");
    expect(byHandle("ivy").state).toBe("working");
    expect(byHandle("pip").state).toBe("done");
    expect(byHandle("rex").state).toBe("idle");
    const row = byHandle("rex").row;
    expect(teamState({ ...row, status: "waiting" })).toBe("waiting");
    expect(teamState({ ...row, status: "failed" })).toBe("stopped");
    expect(filterOf("done")).toBe("idle");
  });

  it("ranks waiting on a person above stuck, and stuck above working", () => {
    const dot = byHandle("dot").row;
    expect(teamState(dot)).toBe("stuck");
    expect(teamState(dot, true)).toBe("waiting");
    expect(teamState({ ...dot, status: "waiting" })).toBe("waiting");
    expect(teamState({ ...dot, status: "running" })).toBe("stuck");
    // The fleet's flags no longer carry "stuck"; only the field counts.
    expect(teamState({ ...byHandle("ivy").row, flags: ["stuck"] })).toBe("working");
  });

  it("says waiting on you only when the viewer answers it", () => {
    const row = byHandle("nova").row;
    const mine = teamMember(row, ROOT, new Set([row.session_id.replace(/-/g, "").toLowerCase()]));
    expect(mine.state).toBe("waiting");
    expect(stateWord(mine)).toBe("Waiting on you");
    expect(stateWord(teamMember({ ...row, status: "waiting" }, ROOT))).toBe("Waiting on a person");
  });
});

describe("names, roles, vendors and models", () => {
  it("names agents by handle and the lead seats by what they own", () => {
    expect(handleName("dot")).toBe("Dot");
    expect(handleName("builder-2")).toBe("Builder 2");
    const atlas = byHandle("atlas");
    expect([atlas.name, atlas.seat, atlas.role]).toEqual(["Atlas", "portfolio", "Portfolio lead"]);
    expect(byHandle("sentinel").seat).toBe("reliability");
    expect(byHandle("ivy").role).toBe("Builder");
    expect(byHandle("kip").role).toBe("Env keeper");
  });

  it("writes vendor · model, falling back to the harness and a short model name", () => {
    expect(byHandle("rex").vendorModel).toBe("codex · gpt-5-codex");
    const bare: Pick<AgentRowView, "vendor" | "harness" | "model" | "model_label"> = {
      vendor: null,
      harness: "claude-native",
      model: "anthropic/claude-sonnet-4-5-20250929",
      model_label: null,
    };
    expect(vendorModel(bare)).toBe("claude · sonnet");
    expect(vendorModel({ ...bare, model: null })).toBe("claude · default");
    expect(modelLabel("gpt-5-codex")).toBe("gpt-5-codex");
    expect(modelLabel("claude-opus-4-1[1m]")).toBe("opus");
  });

  it("draws each role's glyph", () => {
    expect(roleGlyph(byHandle("atlas"))).toBe("compass");
    expect(roleGlyph(byHandle("sentinel"))).toBe("shield");
    expect(roleGlyph(byHandle("nova"))).toBe("hexagon");
    expect(roleGlyph(byHandle("quill"))).toBe("eye");
    expect(roleGlyph(byHandle("dot"))).toBe("play");
    expect(roleGlyph(byHandle("kip"))).toBe("box");
  });
});

describe("the filter", () => {
  it("matches name, role, vendor, model, state and project", () => {
    expect(matching("codex")).toEqual(["oro", "quill", "rex"]);
    expect(matching("stuck")).toEqual(["dot"]);
    expect(matching("reviewer sonnet")).toEqual([]);
    expect(matching("reviewer gpt-5")).toEqual(["oro", "quill"]);
    expect(matching("builder claude")).toEqual(["ivy", "pip"]);
    expect(matching("dark-mode env")).toEqual(["bo"]);
    expect(matching("export report")).toContain("ivy");
    expect(matching("")).toHaveLength(members().length);
  });

  it("counts every state, done as idle", () => {
    expect(stateCounts(members())).toEqual({
      working: 4,
      waiting: 0,
      stuck: 1,
      stopped: 1,
      idle: 7,
    });
  });
});

describe("teams", () => {
  it("puts the lead seats apart and one team per project, lead first", () => {
    const { leads, teams } = groupTeams(members(), snapshot.projects);
    expect(leads.map((m) => m.row.handle)).toEqual(["atlas", "sentinel"]);
    expect(teams.map((t) => t.slug)).toEqual(["csv-export", "dark-mode", "search-filters"]);
    const csv = teams[0]!;
    expect(csv.members.map((m) => m.row.handle)).toEqual([
      "nova",
      "ivy",
      "rex",
      "quill",
      "dot",
      "kip",
    ]);
    expect(csv.title).toBe("(sample) People can export a report as CSV");
    expect(csv.costUsd).toBeCloseTo(15.4);
    expect(teamLine(csv.members)).toBe(
      "Project lead opus · Builder sonnet, gpt-5-codex · Reviewer gpt-5-codex · Demo sonnet · Env keeper sonnet",
    );
  });

  it("folds older idle sessions of a role into a count", () => {
    const rows = teamsAgentRows(snapshot);
    const rex = rows.find((r) => r.handle === "rex")!;
    const older = {
      ...rex,
      session_id: "old-rex",
      handle: "fen",
      last_activity_at: (rex.last_activity_at ?? 0) - 3600,
    };
    const team = [...rows, older]
      .filter((r) => r.project === "csv-export")
      .map((r) => teamMember(r, ROOT));
    const { shown, earlier } = currentTeam(team);
    expect(earlier).toBe(1);
    expect(shown.map((m) => m.row.handle)).not.toContain("fen");
    // Busy members always stay, however many share a role.
    const busy = { ...older, status: "running" };
    expect(currentTeam([...team.slice(0, -1), teamMember(busy, ROOT)]).earlier).toBe(0);
  });

  it("puts the leads' helpers and shared-project members in others", () => {
    const rows = teamsAgentRows(snapshot);
    const atlas = rows.find((r) => r.handle === "atlas")!;
    const helper = {
      ...rows.find((r) => r.handle === "ivy")!,
      session_id: "helper",
      handle: "lark",
      project: null,
      parent_session_id: atlas.session_id,
      stuck: true,
    };
    const shared = {
      ...rows.find((r) => r.handle === "kip")!,
      session_id: "env",
      handle: "wren",
      project: "base-env",
    };
    const all = [...rows, helper, shared].map((r) => teamMember(r, ROOT));
    const { teams, others } = groupTeams(all, snapshot.projects, new Set(["base-env"]));
    expect(others.members.map((m) => m.row.handle).sort()).toEqual(["lark", "wren"]);
    expect(teams.some((t) => t.slug === "base-env")).toBe(false);
    const shown = new Set([
      ...groupTeams(all, snapshot.projects, new Set(["base-env"])).leads,
      ...teams.flatMap((t) => t.members),
      ...others.members,
    ]);
    // Every stuck agent the counts include is on a card.
    expect(stateCounts(all).stuck).toBe(
      all.filter((m) => m.state === "stuck" && shown.has(m)).length,
    );
  });

  it("falls back to the snapshot's members with their handles", () => {
    const rows = teamRows(snapshot, undefined);
    expect(rows.find((r) => r.handle === "dot")?.stuck).toBe(true);
    expect(teamRows(snapshot, [])).toEqual([]);
  });
});
