import { describe, expect, it } from "vitest";
import {
  round3People,
  round3Snapshot,
} from "@/components/factory/shell/__fixtures__/shellFixtures";
import {
  isRoomCommand,
  matchingRoomCommands,
  parseRoomCommand,
  resolveAgent,
  roomCommandLever,
  taskKeyOf,
} from "./roomCommands";

const snapshot = round3Snapshot();
const people = round3People().data;
const PR_9631 = snapshot.prs.find((p) => p.number === 9631)!;
const PL_DB = snapshot.members.find((m) => m.title === "PL · db-version")!;
const IC_ROUTE = snapshot.members.find((m) => m.title === "ic:route-r2")!;

const parse = (input: string) => parseRoomCommand(input, { snapshot, people });

describe("parseRoomCommand", () => {
  it.each([
    ["/project new", { kind: "project_new", title: null }],
    [
      "/project new Health badge on settings",
      { kind: "project_new", title: "Health badge on settings" },
    ],
    ["/charter #db-version", { kind: "charter", slug: "db-version" }],
    ["/charter db-version", { kind: "charter", slug: "db-version" }],
    [
      "/pause db-version",
      {
        kind: "control",
        request: {
          action: "pause",
          target: "project:db-version",
          options: { keep_previews: true },
        },
        label: "Pause #db-version · keep previews",
      },
    ],
    [
      "/pause #db-version drop previews",
      {
        kind: "control",
        request: {
          action: "pause",
          target: "project:db-version",
          options: { keep_previews: false },
        },
        label: "Pause #db-version",
      },
    ],
    [
      "/resume #db-version",
      {
        kind: "control",
        request: { action: "resume", target: "project:db-version" },
        label: "Resume #db-version",
      },
    ],
    [
      "/cadence normal",
      {
        kind: "control",
        request: { action: "cadence", target: "factory", options: { cadence: "normal" } },
        label: "Cadence normal",
      },
    ],
    [
      "/preview !9631",
      {
        kind: "control",
        request: {
          action: "preview",
          target: "project:db-version",
          options: { pr_id: PR_9631.id, mode: "up" },
        },
        label: "Preview !9631",
      },
    ],
    [
      "/env #api-version",
      {
        kind: "control",
        request: { action: "env_status", target: "project:api-version" },
        label: "Env status #api-version",
      },
    ],
    [
      "/task #db-version Add a version table",
      {
        kind: "task",
        slug: "db-version",
        title: "Add a version table",
        key: "add-a-version-table",
      },
    ],
    [
      "/interrupt @pl:db-version",
      {
        kind: "intervene",
        action: "interrupt",
        target: `member:${PL_DB.session_id}`,
        agent: "PL · db-version",
        note: null,
      },
    ],
    [
      "/msg @ic:route-r2 re-run with the knex seed",
      {
        kind: "intervene",
        action: "message",
        target: `member:${IC_ROUTE.session_id}`,
        agent: "ic:route-r2",
        note: "re-run with the knex seed",
      },
    ],
    ["/claim D-2", { kind: "claim" }],
    ["/ack INC-13", { kind: "ack", ref: "INC-13" }],
    ["/ack inc-4", { kind: "ack", ref: "INC-4" }],
    [
      "/invite joe@caffeine.ai viewer",
      { kind: "invite", email: "joe@caffeine.ai", role: "viewer" },
    ],
    ["/invite joe@caffeine.ai", { kind: "invite", email: "joe@caffeine.ai", role: "operator" }],
    ["/status", { kind: "status", slug: null }],
    ["/status #db-version", { kind: "status", slug: "db-version" }],
  ])("parses %j", (input, expected) => {
    const result = parse(input);
    expect(result.ok, JSON.stringify(result)).toBe(true);
    if (result.ok) expect(result.command).toMatchObject(expected);
  });

  it("assigns a decision to a person by short name, email or role", () => {
    const mia = parse("/assign D-2 @mia");
    const email = parse("/assign D-2 mia@caffeine.ai");
    const owners = parse("/assign D-31 owners");
    expect(mia.ok && mia.command).toMatchObject({ kind: "assign", to: "mia@caffeine.ai" });
    expect(email.ok && email.command).toMatchObject({ kind: "assign", to: "mia@caffeine.ai" });
    expect(owners.ok && owners.command).toMatchObject({ kind: "assign", to: "owners" });
  });

  it.each([
    ["/pause", "Name a project"],
    ["/pause #nope", "No project #nope here"],
    ["/pause #db-version later", "Add “keep previews” or nothing"],
    ["/resume #db-version now", "/resume takes only a project"],
    ["/cadence fast", "Cadence is demo or normal"],
    ["/preview !1", "No PR !1 in this factory"],
    ["/task #db-version", "Give the task a title"],
    ["/interrupt atlas", "Name an agent: @atlas, @pl:<slug>, …"],
    ["/interrupt @env_keeper", "5 agents are called env_keeper; open the agent's page instead"],
    ["/msg @atlas", "Say what to send"],
    ["/claim D-999", "No open decision D-999"],
    ["/claim D-1", "D-1 is answered"],
    ["/assign D-2 @nobody", "No one called @nobody on the roster"],
    ["/ack 13", "Name an incident: /ack INC-4"],
    ["/invite not-an-email", "Invite by email"],
    ["/invite joe@caffeine.ai admin", "Role is viewer, operator or owner"],
    ["/project", "Try /project new [title]"],
    ["/frobnicate", "Unknown command /frobnicate"],
  ])("refuses %j", (input, error) => {
    const result = parse(input);
    expect(result).toMatchObject({ ok: false, error });
  });
});

describe("helpers", () => {
  it("tells commands from messages", () => {
    expect(isRoomCommand("/pause #x")).toBe(true);
    expect(isRoomCommand("  /status")).toBe(true);
    expect(isRoomCommand("/ not a command")).toBe(false);
    expect(isRoomCommand("pause /x")).toBe(false);
  });

  it("lists commands by the typed prefix, none once arguments start", () => {
    expect(matchingRoomCommands("/pa").map((c) => c.name)).toEqual(["pause"]);
    expect(matchingRoomCommands("/").length).toBe(15);
    expect(matchingRoomCommands("/pause #x")).toEqual([]);
  });

  it("resolves agents by name", () => {
    expect(resolveAgent(snapshot, "@atlas")).toMatchObject({ member: { title: "Atlas" } });
    expect(resolveAgent(snapshot, "@sentinel")).toMatchObject({
      member: { title: "Lead B · Sentinel" },
    });
    expect(resolveAgent(snapshot, "@pl:nope")).toEqual({ error: "No PL for #nope" });
  });

  it("keys a task from its title", () => {
    expect(taskKeyOf("Add GET /db/version (route)!")).toBe("add-get-db-version-route");
    expect(taskKeyOf("Ünïcode  title")).toBe("unicode-title");
  });

  it("names the lever each command needs", () => {
    const lever = (input: string) => {
      const r = parse(input);
      return r.ok ? roomCommandLever(r.command) : "error";
    };
    expect(lever("/status")).toBeNull();
    expect(lever("/pause #db-version")).toBe("control");
    expect(lever("/project new")).toBe("apply_projects");
    expect(lever("/msg @atlas hi")).toBe("intervene");
    expect(lever("/claim D-2")).toBe("answer");
    expect(lever("/invite a@b.co")).toBe("invite");
  });
});
