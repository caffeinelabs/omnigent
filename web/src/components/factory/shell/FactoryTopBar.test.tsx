import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { round3Snapshot } from "./__fixtures__/shellFixtures";
import { VerdictPill, fallbackVerdict, initialsOf, verdictRate } from "./FactoryTopBar";

describe("the top bar's pieces", () => {
  it("names a person by two letters", () => {
    expect(initialsOf("lena.roth@example.com")).toBe("LR");
    expect(initialsOf("ana@example.com")).toBe("AN");
    expect(initialsOf("ops-team")).toBe("OT");
    expect(initialsOf(null)).toBe("?");
  });

  it("says the rate against the alert, or the rate alone", () => {
    const verdict = { dot: "green" as const, sentence: "Running smoothly", reasons: [] };
    expect(verdictRate({ ...verdict, usd_per_h: 27.4, usd_per_h_alert: 40 })).toBe(
      "$27/h of $40/h",
    );
    expect(verdictRate({ ...verdict, usd_per_h: 3.14 })).toBe("$3.1/h");
    expect(verdictRate({ ...verdict, usd_per_h: 2 })).toBe("$2/h");
    expect(verdictRate(verdict)).toBeNull();
  });

  it("never says green before the server's verdict: red from the snapshot's facts, else grey", () => {
    const snapshot = round3Snapshot();
    // Round 3 has a stopped project (cache-warm): the snapshot alone shows it.
    const broken = fallbackVerdict(snapshot);
    expect(broken.dot).toBe("red");
    expect(broken.sentence).toMatch(/^Running, 1 project stopped/);
    expect(broken.reasons[0]).toMatchObject({ kind: "project" });
    expect(broken.reasons[0]!.text).toMatch(/^cache-warm: /);

    const calm: FactorySnapshot = {
      ...snapshot,
      projects: snapshot.projects.map((p) => ({ ...p, overlay: null })),
      members: snapshot.members.map((m) => ({ ...m, health: null, status: "idle" })),
    };
    expect(fallbackVerdict(calm)).toMatchObject({ dot: "grey", sentence: "Checking health…" });
    expect(fallbackVerdict(calm, "unknown")).toMatchObject({
      dot: "grey",
      sentence: "Health unknown",
    });
    const paused = { ...calm, factory: { ...calm.factory, state: "paused" as const } };
    expect(fallbackVerdict(paused)).toMatchObject({ dot: "grey", sentence: "Paused" });
    const github = {
      ...calm,
      health: { ...calm.health, github: { ...calm.health.github, state: "disconnected" as const } },
    };
    expect(fallbackVerdict(github)).toMatchObject({
      dot: "red",
      sentence: "Running, GitHub can't be reached",
    });
    const supervisor = {
      ...calm,
      health: { ...calm.health, supervisor: { last_tick_at: null, ok: false } },
    };
    expect(fallbackVerdict(supervisor, "unknown").dot).toBe("red");
  });

  it("reads the whole verdict to a screen reader and opens the Health panel", () => {
    const onOpen = vi.fn();
    render(
      <VerdictPill
        verdict={{
          dot: "red",
          sentence: "Running, 1 project stopped",
          reasons: [],
          usd_per_h: 27,
          usd_per_h_alert: 40,
        }}
        onOpen={onOpen}
      />,
    );
    const pill = screen.getByRole("button", {
      name: /Factory health: Running, 1 project stopped\. \$27\/h of \$40\/h/,
    });
    expect(pill).toHaveAttribute("data-dot", "red");
    fireEvent.click(pill);
    expect(onOpen).toHaveBeenCalledOnce();
  });
});
