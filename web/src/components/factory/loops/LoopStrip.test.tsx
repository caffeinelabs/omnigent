import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { LoopView } from "@/lib/factoryApi";
import {
  PR_9631,
  round3IssueList,
  round3LoopList,
  timelineSpans,
} from "../agents/__fixtures__/opsFixtures";
import { round3Snapshot, viewerOf } from "../shell/__fixtures__/shellFixtures";
import { FactoryViewerProvider } from "../shell/viewerCan";
import { LoopStrip } from "./LoopStrip";
import { budgetShare, loopLines, prLoops, stateText, stripState } from "./loopStripModel";

const snapshot = round3Snapshot();
const pr9631 = snapshot.prs.find((p) => p.id === PR_9631)!;

function loop(kind: LoopView["kind"], patch: Partial<LoopView>): LoopView {
  return { ...round3LoopList().find((l) => l.kind === kind)!, ...patch };
}

/** One loop in each strip state the PR page shows. */
function stateLoops(): LoopView[] {
  return [
    loop("implement", {}),
    loop("review", {
      id: "rv-thrash",
      state: "thrashing",
      last_reason: "finding 3fa2c1d0 came back in round 3",
      iteration: 3,
    }),
    loop("review", {
      id: "rv-dry",
      subject: "pr:other",
      state: "stalled",
      last_reason: "review dry: round 2 adds no new blocking finding and fixes none",
    }),
    loop("verify", { state: "running" }),
    loop("demo", { state: "converged", last_reason: "PASS at 5e1c2d4" }),
  ];
}

describe("loop strip states", () => {
  it("classifies running, exhausted, thrashing, dry and converged", () => {
    expect(stateLoops().map((l) => stripState(l))).toEqual([
      "exhausted",
      "thrashing",
      "dry",
      "running",
      "converged",
    ]);
  });

  it("names each state with its budget", () => {
    const [impl, thrash, dry, verify, demo] = loopLines(stateLoops(), timelineSpans());
    expect(stateText(impl!)).toBe("⛔ exhausted 3/3");
    expect(stateText(thrash!)).toBe("↻ thrashing");
    expect(stateText(dry!)).toBe("dry: no new findings");
    expect(stateText(verify!)).toBe("running 1/2");
    expect(stateText(demo!)).toBe("✓ converged 1/2");
  });

  it("builds implement attempts r0..r2 from the loop's spans, with verdicts", () => {
    const [impl] = loopLines([loop("implement", {})], timelineSpans());
    expect(impl!.attempts.map((a) => `${a.label}:${a.outcome}:${a.verdict}`)).toEqual([
      "r0:fail:CI fail",
      "r1:fail:CI fail",
      "r2:fail:CI fail",
    ]);
    expect(impl!.usd).toEqual({ spent: 11.8, limit: 15 });
    expect(budgetShare(impl!)).toBeCloseTo(11.8 / 15);
  });

  it("falls back to the iteration count without spans, and reads blocking counts", () => {
    const review = loop("review", {
      state: "running",
      iteration: 2,
      last_reason: "3 blocking at a1b2c3d",
    });
    const [line] = loopLines([review], []);
    expect(line!.attempts.map((a) => a.label)).toEqual(["#1", "#2"]);
    expect(line!.attempts[1]!.blocking).toBe(3);
    expect(line!.attempts[1]!.outcome).toBe("open");
  });

  it("picks the PR's own loops and its task's implement loop, implement first", () => {
    const lines = loopLines(prLoops(round3LoopList(), pr9631), timelineSpans(), round3IssueList());
    expect(lines.map((l) => l.loop.kind)).toEqual(["implement", "review", "verify", "demo", "ci"]);
    expect(lines.find((l) => l.loop.kind === "verify")!.headNote).toBe("1/2 this head");
    expect(lines.find((l) => l.loop.kind === "ci")!.flake?.ref).toBe("F-7");
  });
});

function renderStrip(role: "creator" | "viewer" = "creator") {
  const client = new QueryClient();
  const lines = loopLines(stateLoops(), timelineSpans(), round3IssueList());
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <FactoryViewerProvider viewer={viewerOf(role)}>
          <LoopStrip snapshot={snapshot} lines={lines} />
        </FactoryViewerProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("LoopStrip", () => {
  it("draws each state, the attempts and the loop decision's remedies", () => {
    renderStrip();
    const impl = screen.getAllByTestId("loop-line-implement")[0]!;
    expect(impl).toHaveAttribute("data-state", "exhausted");
    expect(
      within(impl)
        .getAllByTestId(/^loop-attempt-r/)
        .map((a) => a.getAttribute("data-outcome")),
    ).toEqual(["fail", "fail", "fail"]);
    expect(impl).toHaveTextContent("$11.80/$15.00");
    expect(impl).toHaveTextContent("D-31");
    expect(within(impl).getByRole("meter")).toHaveAttribute("aria-valuenow", "79");
    expect(within(impl).getByTestId("loop-remedy-raise")).toHaveTextContent("+1 attempt ★");
    const states = screen.getAllByTestId(/^loop-line-/).map((l) => l.getAttribute("data-state"));
    expect(states).toEqual(["exhausted", "thrashing", "dry", "running", "converged"]);
  });

  it("disables the remedies for a viewer", () => {
    renderStrip("viewer");
    expect(screen.getByTestId("loop-remedy-raise")).toBeDisabled();
  });
});
