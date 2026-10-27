import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { PrView } from "@/lib/factoryApi";
import { LIVE_RUN_NOW_S, liveRunSnapshot } from "../__fixtures__/liveRun";
import { GATE_STATES, gateScenario, prByNumber, storySnapshot } from "./__fixtures__/boardFixtures";
import { renderBoard } from "./__fixtures__/renderBoard";
import { GATE_ORDER, GateChip, type GateKey, gateState, shownGates } from "./GateChip";

function Row({ pr }: { pr: PrView }) {
  return (
    <div>
      {shownGates(pr).map((gate) => (
        <GateChip key={gate} pr={pr} gate={gate} nowS={LIVE_RUN_NOW_S} />
      ))}
    </div>
  );
}

async function evidence(pr: PrView, gate: GateKey) {
  fireEvent.focus(screen.getByTestId(`gate-${gate}-${pr.number}`));
  return screen.findByTestId(`gate-evidence-${gate}-${pr.number}`);
}

describe("GateChip", () => {
  it.each(GATE_STATES)("renders every gate in the %s state", (state) => {
    const pr = gateScenario(state);
    renderBoard(<Row pr={pr} />);
    const gates = shownGates(pr);
    // The pending scenario is #9631 as recorded: it targets its base and has no conflicts.
    const settled = new Set<GateKey>(["base", "mergeable", "protected_paths"]);
    for (const gate of gates.filter((g) => state !== "pending" || !settled.has(g))) {
      expect(screen.getByTestId(`gate-${gate}-${pr.number}`)).toHaveAttribute("data-state", state);
    }
  });

  it("shows the CI count and the demo gate only when the project records demos", () => {
    const snapshot = storySnapshot();
    const api = prByNumber(snapshot, 9630);
    const docs = prByNumber(snapshot, 9632);
    expect(shownGates(api)).toEqual([...GATE_ORDER]);
    expect(shownGates(docs)).not.toContain("demo");
    renderBoard(<Row pr={api} />);
    expect(screen.getByTestId("gate-checks-9630")).toHaveTextContent("CI 14/14");
    expect(screen.getByTestId("gate-checks-9630")).toHaveAccessibleName("CI passed");
  });

  it("hides the Base chip for a server that sends no base gate", () => {
    const old = liveRunSnapshot().prs.find((p) => p.number === 9630)!;
    expect(shownGates(old)).not.toContain("base");
    expect(shownGates(old)).toContain("checks");
  });

  it("shows which branch a PR targets and the project's base", async () => {
    const ok = prByNumber(storySnapshot(), 9630);
    const { unmount } = renderBoard(<Row pr={ok} />);
    expect(screen.getByTestId("gate-base-9630")).toHaveAccessibleName("Base passed");
    expect(await evidence(ok, "base")).toHaveTextContent("Targets main (the project’s base).");
    unmount();

    const wrong = gateScenario("fail");
    renderBoard(<Row pr={wrong} />);
    expect(screen.getByTestId("gate-base-9631")).toHaveAttribute("data-state", "fail");
    const card = await evidence(wrong, "base");
    expect(card).toHaveTextContent("Targets main; the project’s base is temp-main.");
    expect(card).toHaveTextContent("gh pr edit https://github.com/caffeinelabs/app/pull/9631");
  });

  it("reads unknown while GitHub can't be polled, but keeps a skip", () => {
    const pr = { ...prByNumber(storySnapshot(), 9632), poll_error: "rate-limited" };
    expect(gateState(pr, "checks")).toBe("unknown");
    expect(gateState(pr, "verify")).toBe("skip");
    expect(gateState(pr, "demo")).toBeNull();
  });

  it("shows CI evidence with failing job names and the checks link", async () => {
    const pr = gateScenario("fail");
    renderBoard(<Row pr={pr} />);
    const card = await evidence(pr, "checks");
    expect(card).toHaveTextContent("12 passing · 2 failing · 0 pending of 14");
    expect(card).toHaveTextContent("e2e / smoke");
    expect(card).toHaveTextContent("checks failing: 2 of 14");
    expect(within(card).getByRole("link", { name: /Checks on GitHub/ })).toHaveAttribute(
      "href",
      "https://github.com/caffeinelabs/app/pull/9631/checks",
    );
  });

  it("shows the review marker's verdict, sha, reviewer and comment link", async () => {
    const pr = prByNumber(storySnapshot(), 9630);
    renderBoard(<Row pr={pr} />);
    const card = await evidence(pr, "review");
    expect(card).toHaveTextContent("APPROVE @7767f0d · reviewer (codex)");
    expect(within(card).getByRole("link", { name: /Review comment/ })).toHaveAttribute(
      "href",
      "https://github.com/caffeinelabs/app/pull/9630#issuecomment-3448812201",
    );
  });

  it("names the env a verify ran in and links the demo in-app", async () => {
    const pr = prByNumber(storySnapshot(), 9630);
    renderBoard(<Row pr={pr} />);
    expect(await evidence(pr, "verify")).toHaveTextContent(
      "PASS @7767f0d in b693ef0a-2c6683-a38d-p9630",
    );
    const demo = await evidence(pr, "demo");
    expect(within(demo).getByRole("link", { name: /Watch the demo/ })).toHaveAttribute(
      "href",
      "/factory/f4f4ba9c77725331b12f0fa7072dc4d3?review=9630",
    );
  });

  it("lists touched files with protected ones called out", async () => {
    const pr = gateScenario("fail");
    renderBoard(<Row pr={pr} />);
    const card = await evidence(pr, "protected_paths");
    expect(card).toHaveTextContent("4 files · 1 protected");
    expect(within(card).getByText("services/caffeine-api/src/app.ts")).toHaveClass(
      "text-destructive",
    );
  });

  it("explains a pending gate with the server's reason", async () => {
    const pr = prByNumber(storySnapshot(), 9631);
    renderBoard(<Row pr={pr} />);
    const card = await evidence(pr, "review");
    expect(card).toHaveTextContent("No review marker for 5e1c2d4 yet.");
    expect(card).toHaveTextContent("no review marker for 5e1c2d4");
  });

  it("pulses a ring only when a gate changes after the first paint", () => {
    const pr = prByNumber(storySnapshot(), 9631);
    const { rerender } = renderBoard(<GateChip pr={pr} gate="checks" nowS={LIVE_RUN_NOW_S} />);
    const chip = () => screen.getByTestId("gate-checks-9631");
    expect(chip().querySelector(".animate-\\[user-msg-flash_600ms_ease-out\\]")).toBeNull();
    const passed = { ...pr, gates: { ...pr.gates, checks: "pass" as const } };
    rerender(<GateChip pr={passed} gate="checks" nowS={LIVE_RUN_NOW_S} />);
    expect(chip()).toHaveAttribute("data-state", "pass");
    expect(chip().querySelector(".animate-\\[user-msg-flash_600ms_ease-out\\]")).not.toBeNull();
  });
});
