import { describe, expect, it } from "vitest";
import { round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import { filterJumpItems, jumpItems } from "./paletteModel";
import { matchingCommands, parseSlashCommand } from "./slashCommands";

const FID = "f4f4ba9c77725331b12f0fa7072dc4d3";

describe("jumpItems", () => {
  it("lists pages, projects, PRs, tasks, open decisions and agents from the snapshot", () => {
    const snapshot = round3Snapshot();
    const items = jumpItems(snapshot);
    const kinds = new Set(items.map((i) => i.kind));
    expect([...kinds]).toEqual(["page", "project", "pr", "task", "decision", "agent"]);
    expect(items.find((i) => i.id === "project:db-version")!.href).toBe(
      `/factory/${FID}/projects/db-version`,
    );
    const pr = items.find((i) => i.ref === "!9631")!;
    expect(pr.href).toBe(`/factory/${FID}/prs/${snapshot.prs.find((p) => p.number === 9631)!.id}`);
    expect(items.some((i) => i.kind === "task" && i.ref?.startsWith("T-"))).toBe(true);
    const decision = items.find((i) => i.ref === "D-2")!;
    expect(decision.href).toBe(`/factory/${FID}/projects/db-version?decision=D-2`);
    expect(items.filter((i) => i.kind === "decision").every((i) => i.ref !== "D-1")).toBe(true);
    expect(items.filter((i) => i.kind === "agent")).toHaveLength(snapshot.members.length);
  });

  it("filters by every typed word across label, ref, detail and keywords", () => {
    const items = jumpItems(round3Snapshot());
    expect(filterJumpItems(items, "9631").map((i) => i.ref)).toContain("!9631");
    expect(filterJumpItems(items, "db version").some((i) => i.id === "project:db-version")).toBe(
      true,
    );
    expect(filterJumpItems(items, "zzz-nothing")).toEqual([]);
    expect(filterJumpItems(items, "")).toHaveLength(items.length);
  });
});

describe("parseSlashCommand", () => {
  const snapshot = round3Snapshot();

  it("pauses and resumes the factory or one project", () => {
    expect(parseSlashCommand("/pause", snapshot)).toEqual({
      ok: true,
      command: { kind: "pause", target: "factory", label: "Atlas factory" },
    });
    expect(parseSlashCommand("/resume #db-version", snapshot)).toEqual({
      ok: true,
      command: { kind: "resume", target: "project:db-version", label: "#db-version" },
    });
    expect(parseSlashCommand("/pause nope", snapshot)).toEqual({
      ok: false,
      error: "No project nope in this factory",
    });
  });

  it("opens Add project, Share and People", () => {
    expect(parseSlashCommand("/project new", snapshot)).toEqual({
      ok: true,
      command: { kind: "project_new" },
    });
    expect(parseSlashCommand("/project", snapshot).ok).toBe(false);
    expect(parseSlashCommand("/share", snapshot)).toEqual({ ok: true, command: { kind: "share" } });
    expect(parseSlashCommand("/invite", snapshot)).toEqual({
      ok: true,
      command: { kind: "share" },
    });
    expect(parseSlashCommand("/people", snapshot)).toEqual({
      ok: true,
      command: { kind: "people" },
    });
  });

  it("refreshes a PR by number", () => {
    const parsed = parseSlashCommand("/refresh !9630", snapshot);
    expect(parsed).toMatchObject({ ok: true, command: { kind: "refresh", number: 9630 } });
    expect(parseSlashCommand("/refresh", snapshot)).toEqual({
      ok: false,
      error: "Name a PR: /refresh !9630",
    });
  });

  it("refuses unknown commands and plain text", () => {
    expect(parseSlashCommand("/dance", snapshot)).toEqual({
      ok: false,
      error: "Unknown command /dance",
    });
    expect(parseSlashCommand("pause", snapshot).ok).toBe(false);
  });

  it("suggests commands by prefix", () => {
    expect(matchingCommands("/p").map((c) => c.name)).toEqual(["pause", "project", "people"]);
    expect(matchingCommands("/").length).toBeGreaterThan(4);
  });
});
