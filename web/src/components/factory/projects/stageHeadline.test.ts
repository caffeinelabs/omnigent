import { describe, expect, it } from "vitest";
import type { ProjectStartView, ProjectView } from "@/lib/factoryApi";
import { ROUND3_NOW_S, round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import { stageHeadline } from "./stageHeadline";

const NOW = ROUND3_NOW_S;

function queued(start: ProjectStartView | null, patch: Partial<ProjectView> = {}): ProjectView {
  return {
    ...round3Snapshot().projects[0]!,
    stage: "queued",
    stage_since: NOW - 300,
    overlay: null,
    overlay_reason: null,
    start,
    ...patch,
  };
}

describe("stageHeadline", () => {
  it("says the PL is starting, for how long and at which step", () => {
    const h = stageHeadline(
      queued({
        state: "starting",
        phase: "provisioning",
        label: "Provisioning sandbox",
        since: NOW - 120,
      }),
      NOW,
    );
    expect(h).toMatchObject({
      label: "Starting PL",
      detail: "Provisioning sandbox",
      ageS: 120,
      title: "Starting the project lead: Provisioning sandbox",
      tone: "active",
    });
  });

  it("drops a phase it has no words for", () => {
    const h = stageHeadline(
      queued({ state: "starting", phase: null, label: "Starting", since: NOW - 60 }),
      NOW,
    );
    expect([h.label, h.detail, h.title]).toEqual([
      "Starting PL",
      null,
      "Starting the project lead",
    ]);
  });

  it("says how many projects are ahead for a free pod", () => {
    const start: ProjectStartView = {
      state: "queued",
      phase: "pod_cap",
      label: "No free pod: 12 of 12 in use",
      since: NOW - 240,
      ahead: 3,
      pods_in_use: 12,
      pod_cap: 12,
    };
    expect(stageHeadline(queued(start), NOW)).toMatchObject({
      label: "Queued",
      detail: "3 projects ahead",
      explain: "No free pod: 12 of 12 in use · 3 projects ahead",
      ageS: 240,
    });
    expect(stageHeadline(queued({ ...start, ahead: 0 }), NOW).detail).toBe("next for a free pod");
  });

  it("names who starts the PL when nothing is in the way", () => {
    const h = stageHeadline(
      queued({
        state: "queued",
        phase: "lead",
        label: "Waiting for Atlas to start its PL",
        since: NOW - 60,
      }),
      NOW,
    );
    expect([h.label, h.detail]).toEqual(["Queued", "Waiting for Atlas to start its PL"]);
  });

  it("keeps a bare Queued when the server says nothing (older servers)", () => {
    const h = stageHeadline(queued(null), NOW);
    expect([h.label, h.detail, h.explain, h.ageS]).toEqual(["Queued", null, null, 300]);
  });

  it("lets an overlay speak over the start reason", () => {
    const start: ProjectStartView = { state: "queued", phase: "server", label: "x", since: null };
    const waiting = queued(start, { overlay: "waiting", depends_on: ["db-version"] });
    expect(stageHeadline(waiting, NOW)).toMatchObject({
      label: "Waiting",
      title: "Waiting on db-version",
    });
    const failed = queued(start, { overlay: "failed" });
    expect(stageHeadline(failed, NOW)).toMatchObject({ label: "Queued", detail: null });
  });

  it("carries why a merged project still works", () => {
    const project: ProjectView = {
      ...round3Snapshot().projects[0]!,
      stage: "verify",
      overlay: null,
      stage_note: "!9774 was merged before this project started",
    };
    expect(stageHeadline(project, NOW)).toMatchObject({
      label: "Verify",
      detail: null,
      explain: "!9774 was merged before this project started",
      title: "Verify · !9774 was merged before this project started",
    });
  });
});
