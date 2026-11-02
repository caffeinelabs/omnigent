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
import { LoopChips } from "./LoopSections";
import { LoopStrip, verdictWord } from "./LoopStrip";
import {
  attemptsOf,
  budgetShare,
  flakeOnLoop,
  heldText,
  isBadState,
  loopLines,
  prLoops,
  stateText,
  stripState,
} from "./loopStripModel";

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
    // A demo that passed at its first run (no spans: the strip reads its count and verdict).
    loop("demo", {
      id: "demo-pass",
      state: "converged",
      iteration: 1,
      last_verdict: "PASS",
      last_reason: "PASS at 5e1c2d4",
    }),
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

  it("marks the last round by its verdict: an approved round of a thrashing review is ✓", () => {
    const approved = loop("review", { state: "thrashing", iteration: 3, last_verdict: "APPROVE" });
    expect(attemptsOf(approved, []).map((a) => a.outcome)).toEqual(["fail", "fail", "ok"]);
    const changes = loop("review", {
      state: "thrashing",
      iteration: 3,
      last_verdict: "CHANGES",
      last_reason: "3 blocking · finding 3fa2c1d0 came back",
    });
    const last = attemptsOf(changes, []).at(-1)!;
    expect(last.outcome).toBe("fail");
    expect(verdictWord(last, "review")).toBe("CHANGES · 3 blocking");
    // Without a verdict the loop's state decides.
    const bare = loop("review", { state: "exhausted", iteration: 2, last_verdict: null });
    expect(attemptsOf(bare, []).at(-1)!.outcome).toBe("fail");
  });

  it("marks CI flake from the server's `pr:<id>@<sha7>` target, on that head only", () => {
    const ci = round3LoopList().find((l) => l.kind === "ci")!;
    const f7 = round3IssueList().find((i) => i.ref === "F-7")!;
    expect(f7.target).toBe(`pr:${PR_9631}@5e1c2d4`);
    expect(flakeOnLoop(f7, ci)).toBe(true);
    const dashed = `${PR_9631.slice(0, 8)}-${PR_9631.slice(8, 12)}-${PR_9631.slice(12)}`;
    expect(flakeOnLoop({ ...f7, target: `pr:${dashed}@5e1c2d4` }, ci)).toBe(true);
    expect(flakeOnLoop({ ...f7, target: `pr:${PR_9631}` }, ci)).toBe(true);
    expect(flakeOnLoop({ ...f7, target: `pr:${PR_9631}@a1b2c3d` }, ci)).toBe(false);
    expect(flakeOnLoop({ ...f7, status: "resolved" }, ci)).toBe(false);
    const [line] = loopLines([ci], [], [{ ...f7, target: `pr:${PR_9631}@a1b2c3d` }]);
    expect(line!.flake).toBeNull();
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

  it("disables the remedies of a decision routed to owners for an operator", () => {
    const owners = round3Snapshot("operator");
    owners.decisions = owners.decisions.map((d) =>
      d.ref === "D-31" ? { ...d, assignee: "owners" } : d,
    );
    const client = new QueryClient();
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <FactoryViewerProvider viewer={owners.viewer}>
            <LoopStrip snapshot={owners} lines={loopLines(stateLoops(), timelineSpans())} />
          </FactoryViewerProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByTestId("loop-remedy-raise")).toBeDisabled();
  });
});

/** T-5's IC was dispatched; its lead waits on two questions a person has not answered. */
function heldLoop(): LoopView {
  return loop("implement", {
    id: "impl-held",
    subject: "task:held",
    task_ref: "T-5",
    state: "running",
    iteration: 1,
    last_verdict: "DISPATCHED",
    last_reason: "attempt 1 (dispatch)",
    decision_id: null,
    decision_ref: null,
    held_by: { decision_id: "d22", decision_ref: "D-22", refs: ["D-22", "D-23"], since: 100 },
  });
}

describe("a task waiting on a person's answer", () => {
  it("reads `held`, not running or stalled, and names the answers it waits on", () => {
    const [line] = loopLines([heldLoop()], []);
    expect(line!.state).toBe("held");
    expect(isBadState(line!.state)).toBe(false);
    expect(stateText(line!)).toBe("waiting on D-22 +1");
    expect(heldText({ decision_id: null, decision_ref: "D-7", refs: ["D-7"], since: null })).toBe(
      "waiting on D-7",
    );
    // `refs` lists at most five; `count` says how many there are.
    const many = ["D-1", "D-2", "D-3", "D-4", "D-5"];
    expect(
      heldText({ decision_id: null, decision_ref: "D-1", refs: many, since: null, count: 8 }),
    ).toBe("waiting on D-1 +7");
    // Only a running loop is held: a stall the tick already raised stays a stall.
    expect(stripState({ ...heldLoop(), state: "stalled" })).toBe("stalled");
    expect(loopLines([{ ...heldLoop(), state: "stalled" }], [])[0]!.heldBy).toBeNull();
    // An older server sends no hold.
    const { held_by: _drop, ...older } = heldLoop();
    expect(stripState(older)).toBe("running");
  });

  it("draws the state chip and links each answer it waits on", () => {
    const client = new QueryClient();
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <FactoryViewerProvider viewer={viewerOf("creator")}>
            <LoopStrip snapshot={snapshot} lines={loopLines([heldLoop()], [])} showSubject />
          </FactoryViewerProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const line = screen.getByTestId("loop-line-implement");
    expect(line).toHaveAttribute("data-state", "held");
    expect(within(line).getByTestId("loop-state-implement")).toHaveTextContent(
      "waiting on D-22 +1",
    );
    const held = within(line).getByTestId("loop-held-implement");
    expect(held).toHaveTextContent(
      "Waits for a person to answer D-22, D-23; not counted as stalled until then.",
    );
    expect(within(held).getByRole("link", { name: "D-23" })).toHaveAttribute(
      "href",
      `/factory/${snapshot.factory.id}/decisions?decision=D-23`,
    );
    // No remedies: nothing is wrong with the task.
    expect(within(line).queryByTestId("loop-remedies-implement")).toBeNull();
  });

  it("says so on the snapshot's loop chips too", () => {
    render(
      <LoopChips
        chips={[
          {
            id: "c1",
            kind: "implement",
            state: "running",
            iteration: 1,
            limit: 3,
            held_by: heldLoop().held_by,
          },
        ]}
      />,
    );
    const chip = screen.getByTestId("pr-loop-implement");
    expect(chip).toHaveAttribute("data-state", "held");
    expect(chip).toHaveTextContent("implement 1/3 · waiting on D-22 +1");
  });

  it("links the oldest answer and the rest from the chips", () => {
    render(
      <MemoryRouter>
        <LoopChips
          factoryId={snapshot.factory.id}
          chips={[
            {
              id: "c1",
              kind: "implement",
              state: "running",
              iteration: 1,
              limit: 3,
              held_by: { ...heldLoop().held_by!, count: 7 },
            },
          ]}
        />
      </MemoryRouter>,
    );
    const chip = screen.getByTestId("pr-loop-implement");
    expect(chip).toHaveTextContent("implement 1/3 · waiting on D-22 +6");
    expect(within(chip).getByRole("link", { name: "D-22" })).toHaveAttribute(
      "href",
      `/factory/${snapshot.factory.id}/decisions?decision=D-22`,
    );
    expect(within(chip).getByRole("link", { name: "+6" })).toHaveAttribute(
      "href",
      `/factory/${snapshot.factory.id}/decisions`,
    );
  });
});
