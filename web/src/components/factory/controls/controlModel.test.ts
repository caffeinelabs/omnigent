import { describe, expect, it } from "vitest";
import { type EventView, FactoryApiError } from "@/lib/factoryApi";
import { ApiError } from "@/lib/sessionsApi";
import { liveRunSnapshot } from "../__fixtures__/liveRun";
import {
  ACK_LATE_MS,
  type LocalControl,
  REFETCH_GRACE_MS,
  cadenceEstimateText,
  controlErrorMessage,
  localControlLive,
  pendingControls,
  pendingForProject,
  pendingText,
  relaunchableDrift,
} from "./controlModel";

const NOW = 1_800_000_000_000;
const PL = "b408c5f09ff659e6b907fd9f688829e9";

function control(over: Partial<EventView> = {}, data: Record<string, unknown> = {}): EventView {
  return {
    id: "ev-pause",
    ts: NOW - 60_000,
    kind: "control.pause",
    severity: "notice",
    project_slug: "api-version",
    from_id: null,
    to_id: PL,
    from_title: null,
    to_title: null,
    subject: "project:api-version",
    summary: "luka paused project api-version",
    data: { action: "pause", target: "project:api-version", recipients: [PL], ...data },
    ...over,
  };
}

function ack(controlId: string, from: string, ts: number): EventView {
  return {
    id: `ack-${controlId}-${from}`,
    ts,
    kind: "control.ack",
    severity: "info",
    project_slug: "api-version",
    from_id: from,
    to_id: null,
    from_title: null,
    to_title: null,
    subject: null,
    summary: "pause acknowledged",
    data: { control_event_id: controlId, action: "pause" },
  };
}

function local(over: Partial<LocalControl> = {}): LocalControl {
  return {
    localId: "c1",
    action: "pause",
    target: "project:api-version",
    project: "api-version",
    at: NOW,
    sent: false,
    sentAt: null,
    eventId: null,
    recipients: null,
    error: null,
    label: null,
    ...over,
  };
}

describe("pendingControls", () => {
  it("shows a click at once, then waits for the recipient's ack", () => {
    expect(pendingControls([local()], [], NOW)[0]?.state).toBe("sending");
    const sent = local({ sent: true, sentAt: NOW - 1000, eventId: "ev-pause", at: NOW - 60_000 });
    expect(pendingControls([sent], [], NOW)[0]?.state).toBe("waiting");
    const [fromServer] = pendingControls([sent], [control()], NOW);
    expect(fromServer).toMatchObject({ state: "waiting", localId: "c1", recipients: [PL] });
  });

  it("turns done when every recipient acked, and disappears a minute later", () => {
    const events = [ack("ev-pause", PL, NOW - 10_000), control()];
    expect(pendingControls([], events, NOW)[0]?.state).toBe("done");
    expect(pendingControls([], events, NOW + 120_000)).toEqual([]);
  });

  it("goes amber after 15 min without a reply", () => {
    const old = control({ ts: NOW - ACK_LATE_MS - 1000 });
    expect(pendingControls([], [old], NOW)[0]?.state).toBe("late");
  });

  it("counts replies of a factory-wide control", () => {
    const wide = control(
      { id: "ev-all", project_slug: null, subject: "factory" },
      { target: "factory", recipients: ["a", "b", PL] },
    );
    const [pending] = pendingControls([], [ack("ev-all", "a", NOW - 1000), wide], NOW);
    expect(pending).toMatchObject({ target: "factory", acked: ["a"], state: "waiting" });
    expect(pendingText(pending!, null)).toBe("Pausing… 1/3 replied");
  });

  it("keeps only the newest control per target, a click in flight beating the server", () => {
    const older = control({ id: "ev-old", ts: NOW - 120_000 });
    const resume = control({ id: "ev-resume", kind: "control.resume", ts: NOW - 30_000 });
    expect(pendingControls([], [resume, older], NOW).map((p) => p.eventId)).toEqual(["ev-resume"]);
    const click = local({ action: "stop", at: NOW - 200_000 });
    expect(pendingControls([click], [resume], NOW)[0]).toMatchObject({
      action: "stop",
      state: "sending",
    });
  });

  it("shows a refused click as an error for a minute", () => {
    const failed = local({ error: "You need edit/owner access", at: NOW - 1000 });
    const [shown] = pendingControls([failed], [], NOW);
    expect(pendingText(shown!, "PL")).toBe("Pause: You need edit/owner access");
    expect(pendingControls([failed], [], NOW + 120_000)).toEqual([]);
  });

  it("reads a charter update by its project and matches a sent click without an id", () => {
    const charter = control(
      { id: "ev-charter", kind: "control.charter_update", subject: "project:api-version" },
      { target: undefined },
    );
    const click = local({
      action: "charter_update",
      sent: true,
      sentAt: NOW - 1000,
      at: NOW - 1000,
    });
    const pending = pendingControls([click], [charter], NOW);
    expect(pending).toHaveLength(1);
    expect(pending[0]).toMatchObject({ eventId: "ev-charter", target: "project:api-version" });
    expect(pendingText(pending[0]!, "PL")).toBe("Sending the charter… waiting for PL");
  });

  it("reads a preview control with mode down as a take-down of that PR", () => {
    const down = control(
      { id: "ev-down", kind: "control.preview" },
      { options: { mode: "down", pr_id: "pr-1", pr: 9630 } },
    );
    const [shown] = pendingControls([], [down], NOW);
    expect(shown).toMatchObject({ action: "preview_down", label: "#9630", state: "waiting" });
    expect(pendingText(shown!, "PL")).toBe("Taking down preview #9630… waiting for PL");
    const up = control({ id: "ev-up", kind: "control.preview" }, { options: { pr: 9631 } });
    expect(pendingText(pendingControls([], [up], NOW)[0]!, "PL")).toBe(
      "Deploying preview #9631… waiting for PL",
    );
  });

  describe("a sent click once the server's events speak for it", () => {
    const T = NOW - 7 * 3600_000;
    const pause = control({ id: "e1", ts: T });
    const acked = ack("e1", PL, T + 60_000);
    const click = local({ sent: true, sentAt: T, eventId: "e1", at: T, recipients: [PL] });

    it("confirms, then goes away, and stays away once the control ages out", () => {
      expect(pendingText(pendingControls([click], [acked, pause], T + 120_000)[0]!, "PL")).toBe(
        "PL paused",
      );
      expect(pendingControls([click], [acked, pause], T + 30 * 60_000)).toEqual([]);
      expect(pendingControls([click], [acked, pause], NOW)).toEqual([]);
      expect(pendingControls([click], [], NOW)).toEqual([]);
    });

    it("drops a click whose event fell off the page once the refetch had time", () => {
      const off = T + 30 * 60_000;
      expect(pendingControls([click], [], T + 1000)[0]?.state).toBe("waiting");
      expect(pendingControls([click], [], off)).toEqual([]);
      expect(localControlLive(click, new Set(), T + REFETCH_GRACE_MS + 1)).toBe(false);
      // Still loading: nothing to go by yet, so the click stays.
      expect(pendingControls([click], null, T + 1000)[0]?.state).toBe("waiting");
      expect(localControlLive(click, null, T + 1000)).toBe(true);
    });

    it("drops a tracked charter update after its event ages out or leaves the page", () => {
      const charter = local({ action: "charter_update", sent: true, sentAt: T, at: T });
      const event = control({ id: "e-ch", kind: "control.charter_update", ts: T + 1000 });
      expect(pendingControls([charter], [ack("e-ch", PL, T + 5000), event], NOW)).toEqual([]);
      expect(pendingControls([charter], [], T + 30 * 60_000)).toEqual([]);
    });

    it("keeps a click whose event is on the page as the source of its chip", () => {
      const recent = local({
        sent: true,
        sentAt: NOW - 1000,
        eventId: "ev-pause",
        at: NOW - 60_000,
      });
      expect(localControlLive(recent, new Set(["ev-pause"]), NOW + 10 * 60_000)).toBe(true);
    });
  });

  it("lets an expired refusal give way to the server's control", () => {
    const failed = local({
      action: "stop",
      error: "You need edit/owner access",
      at: NOW - 120_000,
    });
    const [shown] = pendingControls([failed], [control({ ts: NOW - 1000 })], NOW);
    expect(shown).toMatchObject({ action: "pause", state: "waiting" });
  });

  it("picks a project's control, including one on its member", () => {
    const member = control(
      { id: "ev-m", kind: "control.relaunch", ts: NOW - 5000 },
      { target: `member:${PL}` },
    );
    const pending = pendingControls([], [member, control()], NOW);
    expect(pendingForProject(pending, "api-version")?.eventId).toBe("ev-m");
    expect(pendingForProject(pending, "db-version")).toBeNull();
  });
});

describe("texts", () => {
  it("words each state", () => {
    const [p] = pendingControls([], [control({ ts: NOW - 1000 })], NOW);
    expect(pendingText(p!, "PL")).toBe("Pausing… waiting for PL");
    expect(pendingText({ ...p!, state: "late" }, "PL")).toBe("No reply from PL in 15 min");
    expect(pendingText({ ...p!, state: "done" }, "PL")).toBe("PL paused");
    expect(pendingText({ ...p!, action: "preview", label: "#9631" }, "PL")).toBe(
      "Deploying preview #9631… waiting for PL",
    );
  });

  it("explains a 403 as missing access", () => {
    expect(controlErrorMessage(new ApiError("Forbidden", 403, null))).toBe(
      "You need edit/owner access",
    );
    expect(controlErrorMessage(new Error("boom"))).toBe("Boom");
  });

  it("words the server's known refusals without ids", () => {
    const refused = (status: number, code: string) =>
      new FactoryApiError(`'f4f4ba9c' is not a factory`, status, code, null, null);
    expect(controlErrorMessage(refused(404, "not_a_factory"))).toBe(
      "This factory is no longer on the server",
    );
    expect(controlErrorMessage(refused(409, "no_project_lead"))).toBe("The project has no PL yet");
    expect(controlErrorMessage(refused(404, "something_else"))).toBe(
      "Not found on the server; reload the cockpit",
    );
    expect(controlErrorMessage(refused(409, "pr_not_open"))).toBe("'f4f4ba9c' is not a factory");
  });

  it("estimates both cadences for the live factory", () => {
    // Atlas, Sentinel and two active PLs (the docs PL is done).
    expect(cadenceEstimateText(liveRunSnapshot().members)).toBe("≈36 vs ≈3 heartbeat turns/h");
  });

  it("offers relaunch only for drifted top-level members", () => {
    const members = liveRunSnapshot().members;
    const atlas = members.find((m) => m.role === "lead" && m.launch_drift)!;
    expect(relaunchableDrift(members, atlas.session_id)?.fields).toEqual(
      atlas.launch_drift!.fields,
    );
    expect(relaunchableDrift(members, PL)).toBeNull();
    expect(relaunchableDrift(members, null)).toBeNull();
  });
});
