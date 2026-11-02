import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { round3Snapshot } from "./__fixtures__/shellFixtures";
import { VerdictPill, fallbackVerdict, initialsOf, verdictRate } from "./FactoryTopBar";

describe("the top bar's pieces", () => {
  it("names a person by two letters", () => {
    expect(initialsOf("luka.rumora@example.com")).toBe("LR");
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

  it("falls back to the factory's state until the server's verdict is in", () => {
    const snapshot = round3Snapshot();
    expect(fallbackVerdict(snapshot)).toMatchObject({ dot: "green", sentence: "Running" });
    const paused = { ...snapshot, factory: { ...snapshot.factory, state: "paused" as const } };
    expect(fallbackVerdict(paused)).toMatchObject({ dot: "grey", sentence: "Paused" });
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
