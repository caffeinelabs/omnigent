import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DecisionView } from "@/lib/factoryApi";
import { LIVE_RUN_NOW_S } from "../__fixtures__/liveRun";
import { agentDecision } from "../decisions/__fixtures__/decisionFixtures";
import { DecisionCard } from "../decisions/DecisionCard";
import { FactoryNowContext } from "../factoryClock";
import { closureReasons } from "./ClosureReasonPicker";
import { CLOSURE_REASONS, PR_RATING_TAGS, ratingReceipt, tagsFor } from "./feedbackModel";
import { StarRating } from "./FeedbackWidgets";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const NOW = new Date(LIVE_RUN_NOW_S * 1000);

function Frame({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <FactoryNowContext.Provider value={NOW}>{children}</FactoryNowContext.Provider>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

/** A closed PR's decision: both options carry the closure-reason picker. */
function closedPr(): DecisionView {
  const option = {
    description: null,
    action: null,
    href: null,
    target: null,
    reasons: [...CLOSURE_REASONS],
  };
  return {
    ...agentDecision(),
    ref: "D-21",
    source: "pr_closed",
    asked_by_session_id: null,
    question: "!9653 was closed without merge. Rework or abandon?",
    options: [
      { ...option, id: "rework", label: "Rework", recommended: true },
      { ...option, id: "abandon", label: "Abandon", recommended: false },
    ],
  };
}

beforeEach(() => authenticatedFetch.mockReset());

describe("feedback model", () => {
  it("offers the good tags from 4★ and the bad ones below", () => {
    expect(tagsFor(null, PR_RATING_TAGS)).toEqual([]);
    expect(tagsFor(5, PR_RATING_TAGS)).toContain("clear_demo");
    expect(tagsFor(2, PR_RATING_TAGS)).toContain("missing_tests");
    expect(ratingReceipt(4, ["clear_demo", "good_tests"])).toBe(
      "Rated 4★ · clear demo, good tests",
    );
  });

  it("asks for a closure reason only on a closed PR's decision", () => {
    expect(closureReasons(closedPr())).toEqual([...CLOSURE_REASONS]);
    expect(closureReasons(agentDecision())).toEqual([]);
  });
});

describe("StarRating", () => {
  it("is a radio group of five stars", () => {
    const onChange = vi.fn();
    render(<StarRating value={3} onChange={onChange} testId="stars" />);

    expect(screen.getByRole("radiogroup", { name: "Rating" })).toHaveAttribute("data-value", "3");
    expect(screen.getByTestId("stars-3")).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByTestId("stars-5"));
    expect(onChange).toHaveBeenCalledWith(5);
  });
});

describe("closure reason on a closed PR's decision", () => {
  it("holds the options until a reason is picked, then sends it with the answer", async () => {
    authenticatedFetch.mockImplementation(async () =>
      json({ decision: { ...closedPr(), status: "answered", answer_option_id: "rework" } }),
    );
    render(
      <Frame>
        <DecisionCard decision={closedPr()} />
      </Frame>,
    );

    expect(screen.getByTestId("decision-option-D-21-0")).toBeDisabled();
    const trigger = screen.getByTestId("decision-closure-D-21");
    fireEvent.pointerDown(trigger, new MouseEvent("pointerdown", { bubbles: true, button: 0 }));
    fireEvent.click(trigger);
    fireEvent.click(await screen.findByRole("option", { name: "wrong scope" }));
    fireEvent.click(screen.getByTestId("decision-option-D-21-0"));

    await waitFor(() => expect(authenticatedFetch).toHaveBeenCalled());
    const [path, init] = authenticatedFetch.mock.calls[0]!;
    expect(String(path)).toMatch(/\/answer$/);
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      option_id: "rework",
      reason: "wrong_scope",
    });
  });
});
