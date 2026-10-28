import { describe, expect, it } from "vitest";
import type { EventView } from "@/lib/factoryApi";
import { round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import { factoryLocation } from "../shell/shellRoutes";
import { busyThread, roomMessages, roomThreads } from "./__fixtures__/roomFixtures";
import {
  DOCK_DEFAULT_PX,
  DOCK_MAX_PX,
  DOCK_MIN_PX,
  busLines,
  clampDockWidth,
  continuesRun,
  groupThreads,
  interleave,
  pageProjectSlug,
  pageThread,
  peopleHere,
  responderLine,
  responderStatus,
  threadFromParam,
  threadTitle,
  unreadTotal,
} from "./roomModel";

const snapshot = round3Snapshot();
const PR_9631 = snapshot.prs.find((p) => p.number === 9631)!;

describe("the dock follows the route", () => {
  it.each([
    ["projects/db-version", "project:db-version"],
    [`prs/${PR_9631.id}`, `pr:${PR_9631.id}`],
    ["prs/9631", `pr:${PR_9631.id}`],
    ["", "factory"],
    ["projects", "factory"],
    ["activity", "factory"],
  ])("%j binds %j", (rest, thread) => {
    expect(pageThread(snapshot, factoryLocation(rest), null)).toBe(thread);
  });

  it("keeps the last thread used off project and PR pages", () => {
    expect(pageThread(snapshot, factoryLocation("activity"), "improvements")).toBe("improvements");
    expect(pageThread(snapshot, factoryLocation("projects/ux-badge"), "improvements")).toBe(
      "project:ux-badge",
    );
  });

  it("pins the page's project in the Decisions tab", () => {
    expect(pageProjectSlug(snapshot, factoryLocation("projects/db-version"))).toBe("db-version");
    expect(pageProjectSlug(snapshot, factoryLocation(`prs/${PR_9631.id}`))).toBe("db-version");
    expect(pageProjectSlug(snapshot, factoryLocation(""))).toBeNull();
  });
});

describe("threads", () => {
  it.each([
    [null, "factory"],
    ["factory", "factory"],
    ["#improvements", "improvements"],
    ["project:db-version", "project:db-version"],
    ["#db-version", "project:db-version"],
    ["db-version", "project:db-version"],
    ["!9631", `pr:${PR_9631.id}`],
    [`pr:9631`, `pr:${PR_9631.id}`],
  ])("reads the URL segment %j as %j", (param, key) => {
    expect(threadFromParam(snapshot, param)).toBe(key);
  });

  it("titles threads as people write them", () => {
    expect(threadTitle(snapshot, "factory")).toBe("#factory");
    expect(threadTitle(snapshot, "project:db-version")).toBe("#db-version");
    expect(threadTitle(snapshot, `pr:${PR_9631.id}`)).toBe("!9631");
  });

  it("groups fixed threads, projects in board order, then PRs", () => {
    const groups = groupThreads(roomThreads(), snapshot);
    expect(groups.fixed.map((t) => t.key)).toEqual(["factory", "improvements"]);
    // Every project has a thread; a closed one sinks.
    expect(groups.projects.map((t) => t.key)).toEqual([
      "project:api-version",
      "project:db-version",
      "project:ux-badge",
      "project:cache-warm",
      "project:settings-row",
      "project:base-env",
      "project:docs-factory-envs",
    ]);
    expect(groups.prs.map((t) => t.title)).toEqual(["!9631"]);
  });

  it("totals unread across threads, capped", () => {
    expect(unreadTotal(roomThreads())).toBe(6);
    expect(unreadTotal(roomThreads().map((t) => ({ ...t, unread: 60 })))).toBe(99);
    expect(unreadTotal(undefined)).toBe(0);
  });
});

describe("who answers", () => {
  const factory = roomThreads().find((t) => t.key === "factory")!;

  it("says Atlas is working and what waits for it", () => {
    const working = {
      ...snapshot,
      members: snapshot.members.map((m) =>
        m.session_id === factory.responder_session_id ? { ...m, running: true } : m,
      ),
    };
    const status = responderStatus(working, factory, roomMessages("factory"));
    expect(status.state).toBe("working");
    expect(status.queued).toBe(1);
    expect(responderLine(status)).toBe("Atlas is working… · 1 queued");
  });

  it("says when the responder is paused", () => {
    const thread = roomThreads().find((t) => t.key === "project:db-version")!;
    const paused = {
      ...snapshot,
      members: snapshot.members.map((m) =>
        m.session_id === thread.responder_session_id ? { ...m, state: "paused" as const } : m,
      ),
    };
    expect(responderLine(responderStatus(paused, thread, []))).toBe(
      "PL · db-version is paused; Atlas answers meanwhile",
    );
  });

  it("is quiet when idle with nothing queued", () => {
    const idle = {
      ...snapshot,
      members: snapshot.members.map((m) => ({ ...m, running: false })),
    };
    expect(responderLine(responderStatus(idle, factory, []))).toBeNull();
  });

  it("finds the people on a thread's page", () => {
    expect(
      peopleHere(snapshot.presence, snapshot, "project:db-version").map((p) => p.name),
    ).toEqual(["Mia"]);
    expect(peopleHere(snapshot.presence, snapshot, "factory").map((p) => p.name)).toEqual(["Luka"]);
  });
});

describe("messages and the bus view", () => {
  it("folds one author's run within five minutes", () => {
    const [a, b, c] = busyThread("factory", 3);
    expect(continuesRun(undefined, a!)).toBe(false);
    expect(
      continuesRun(a, { ...a!, id: "x", seq: a!.seq + 1, created_at: a!.created_at + 60 }),
    ).toBe(true);
    expect(continuesRun(b, c!)).toBe(false);
  });

  it("turns message events into one-liners and interleaves them by time", () => {
    const messages = roomMessages("factory");
    const event = (id: string, ts: number, kind: string): EventView => ({
      id,
      ts,
      kind,
      severity: "info",
      project_slug: null,
      from_id: "a",
      to_id: "b",
      from_title: "PL · db-version",
      to_title: "Lead B · Sentinel",
      subject: null,
      summary: "verify !9631",
      data: { kind: "request", excerpt: "verify !9631" },
    });
    const lines = busLines([
      event("e2", (messages[1]!.created_at + 1) * 1000, "message.queued"),
      event("e1", (messages[0]!.created_at - 600) * 1000, "message.queued"),
      event("e3", (messages[1]!.created_at + 2) * 1000, "message.delivered"),
    ]);
    expect(lines.map((l) => [l.id, l.from, l.to, l.kind, l.text])).toEqual([
      ["e1", "PL · db-version", "Lead B · Sentinel", "request", "verify !9631"],
      ["e2", "PL · db-version", "Lead B · Sentinel", "request", "verify !9631"],
    ]);
    const items = interleave(messages, lines, true);
    expect(items.map((i) => i.type).slice(0, 3)).toEqual(["message", "message", "bus"]);
    expect(interleave(messages, lines, false).every((i) => i.type === "message")).toBe(true);
  });
});

describe("dock width", () => {
  it("stays within its bounds", () => {
    expect(clampDockWidth(10)).toBe(DOCK_MIN_PX);
    expect(clampDockWidth(9999)).toBe(DOCK_MAX_PX);
    expect(clampDockWidth(400.4)).toBe(400);
    expect(clampDockWidth(Number.NaN)).toBe(DOCK_DEFAULT_PX);
  });
});
