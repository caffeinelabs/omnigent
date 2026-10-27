import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DecisionView } from "@/lib/factoryApi";
import { LIVE_RUN_FACTORY_ID, LIVE_RUN_NOW_S, liveRunSnapshot } from "../__fixtures__/liveRun";
import { FactoryNowContext } from "../factoryClock";
import { answeredDecision, lateQuestion } from "./__fixtures__/decisionFixtures";
import { DecisionsRail } from "./DecisionsRail";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));

const NOW = new Date(LIVE_RUN_NOW_S * 1000);

function renderRail(
  decisions: DecisionView[],
  highlightId: string | null = null,
  showClosed = false,
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui = (list: DecisionView[]) => (
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <FactoryNowContext.Provider value={NOW}>
          <DecisionsRail
            factoryId={LIVE_RUN_FACTORY_ID}
            decisions={list}
            highlightId={highlightId}
            showClosed={showClosed}
          />
        </FactoryNowContext.Provider>
      </MemoryRouter>
    </QueryClientProvider>
  );
  const view = render(ui(decisions));
  return {
    rail: screen.getByTestId("decisions-rail"),
    rerender: (l: DecisionView[]) => view.rerender(ui(l)),
  };
}

/** The live run's decisions plus an expired D-5 and a withdrawn D-6. */
function withClosed(): DecisionView[] {
  return [
    ...liveRunSnapshot().decisions,
    { ...lateQuestion(), status: "expired", updated_at: LIVE_RUN_NOW_S - 7200 },
    {
      ...answeredDecision(),
      id: "d6d6d6d6d6d6d6d6d6d6d6d6d6d6d6d6",
      ref: "D-6",
      number: 6,
      status: "withdrawn",
      answer_option_id: null,
      answer_text: null,
      answered_at: null,
      updated_at: LIVE_RUN_NOW_S - 600,
    },
  ];
}

beforeEach(() => {
  authenticatedFetch.mockReset();
  Element.prototype.scrollIntoView = vi.fn();
});

describe("DecisionsRail", () => {
  it("says nothing needs the human when nothing is open", () => {
    const { rail } = renderRail([]);
    expect(within(rail).getByTestId("decisions-empty")).toHaveTextContent(
      "Nothing needs you. Agents ask here instead of in chat.",
    );
    expect(rail).toHaveTextContent("Decisions · 0 open");
  });

  it("puts blocking decisions first, then the longest waiting", () => {
    const decisions = [lateQuestion(), ...liveRunSnapshot().decisions];
    const { rail } = renderRail(decisions);
    const cards = within(rail).getAllByTestId(/^decision-card-/);
    expect(cards.map((c) => c.dataset.testid)).toEqual([
      "decision-card-D-2",
      "decision-card-D-3",
      "decision-card-D-5",
    ]);
    expect(rail).toHaveTextContent("Decisions · 3 open");
  });

  it("folds earlier answers under Answered (n) with their receipts", () => {
    const { rail } = renderRail(liveRunSnapshot().decisions);
    const toggle = within(rail).getByTestId("decisions-answered-toggle");
    expect(toggle).toHaveTextContent("Answered (1)");
    fireEvent.click(toggle);
    const answered = within(rail).getByTestId("decisions-answered");
    expect(answered).toHaveTextContent(
      "D-1 “Settings → About · Settings, next to the build number.”",
    );
    expect(within(answered).getByTestId("decision-receipt-D-1")).toHaveAttribute(
      "data-state",
      "delivered",
    );
  });

  it("keeps a card answered here in place, with its receipt, after the snapshot moves on", async () => {
    authenticatedFetch.mockResolvedValue(
      new Response(JSON.stringify({ decision: answeredDecision() }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const decisions = liveRunSnapshot().decisions;
    const { rail, rerender } = renderRail(decisions);
    fireEvent.click(within(rail).getByTestId("decision-option-D-2-0"));
    await waitFor(() =>
      expect(within(rail).getByTestId("decision-card-D-2")).toHaveAttribute(
        "data-status",
        "answered",
      ),
    );
    // The next snapshot lists D-2 as answered and delivered.
    rerender(
      decisions.map((d) =>
        d.ref === "D-2" ? { ...answeredDecision(), delivered_at: LIVE_RUN_NOW_S + 4 } : d,
      ),
    );
    const card = within(rail).getByTestId("decision-card-D-2");
    expect(card).toHaveAttribute("data-status", "delivered");
    expect(within(card).getByTestId("decision-receipt-D-2")).toHaveTextContent(
      "Delivered to PL · db-version",
    );
    expect(within(rail).getByTestId("decisions-answered-toggle")).toHaveTextContent("Answered (1)");
    expect(rail).toHaveTextContent("Decisions · 1 open");
  });

  it("highlights ?decision and scrolls it into view", () => {
    const { rail } = renderRail(liveRunSnapshot().decisions, "D-3");
    const card = within(rail).getByTestId("decision-card-D-3");
    expect(card).toHaveAttribute("data-highlighted", "true");
    expect(card.scrollIntoView).toHaveBeenCalled();
    expect(within(rail).getByTestId("decision-card-D-2")).not.toHaveAttribute("data-highlighted");
  });

  it("leaves withdrawn and expired decisions out unless asked to show them", () => {
    const { rail } = renderRail(withClosed());
    expect(within(rail).queryByTestId("decisions-closed-toggle")).toBeNull();
    expect(rail).not.toHaveTextContent("D-6");
  });

  it("folds withdrawn and expired decisions, newest first, under their own toggle", () => {
    const { rail } = renderRail(withClosed(), null, true);
    const toggle = within(rail).getByTestId("decisions-closed-toggle");
    expect(toggle).toHaveTextContent("Withdrawn / expired (2)");
    expect(within(rail).queryByTestId("decisions-closed")).toBeNull();
    fireEvent.click(toggle);
    const rows = within(within(rail).getByTestId("decisions-closed")).getAllByTestId(
      /^decision-closed-/,
    );
    expect(rows.map((r) => [r.dataset.testid, r.dataset.status])).toEqual([
      ["decision-closed-D-6", "withdrawn"],
      ["decision-closed-D-5", "expired"],
    ]);
    expect(rows[0]).toHaveTextContent("D-6withdrawn · 10m ago");
    expect(rows[1]).toHaveTextContent("D-5expired · 2h agoShould /db/version be public");
    // Open and answered ones stay where they were.
    expect(rail).toHaveTextContent("Decisions · 2 open");
    expect(within(rail).getByTestId("decisions-answered-toggle")).toHaveTextContent("Answered (1)");
  });

  it("opens the closed fold on a highlighted withdrawn decision", () => {
    const { rail } = renderRail([{ ...lateQuestion(), status: "withdrawn" }], "D-5", true);
    expect(within(rail).getByTestId("decision-closed-D-5")).toHaveAttribute(
      "data-highlighted",
      "true",
    );
  });
});
