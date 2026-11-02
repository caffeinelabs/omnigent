import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { ProposalView } from "@/lib/factoryApi";
import { FactoryNowContext } from "../factoryClock";
import { round3Snapshot, viewerOf } from "../shell/__fixtures__/shellFixtures";
import { FactoryViewerProvider } from "../shell/viewerCan";
import { betProposal, requestedFromOpportunity, sampleBet } from "./__fixtures__/betFixtures";
import { ProposalCard } from "./ProposalCard";

vi.mock("@/lib/identity", () => ({ authenticatedFetch: vi.fn() }));

const NOW = 1_793_017_800;
const PINNED = new Date(NOW * 1000);
const FID = round3Snapshot().factory.id;

function renderCard(proposal: ProposalView) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <FactoryNowContext.Provider value={PINNED}>
          <FactoryViewerProvider viewer={viewerOf("creator")}>
            <ProposalCard factoryId={FID} proposal={proposal} snapshot={round3Snapshot()} />
          </FactoryViewerProvider>
        </FactoryNowContext.Provider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("a proposal with a bet", () => {
  it("shows the bet beside the case against", () => {
    renderCard(betProposal("waiting", NOW));

    const block = screen.getByTestId("proposal-bet-C-4");
    expect(within(block).getByText(/^An outcome bet/)).toBeTruthy();
    expect(within(block).getByText("· from O-12 · under G-1")).toBeTruthy();
    expect(
      within(block).getByText("Users can share a draft privately before publishing (sample)"),
    ).toBeTruthy();
    expect(
      within(block).getByRole("img", { name: "Predicted +1 to +4 pp, not measured yet" }),
    ).toBeTruthy();
    expect(within(block).getByText("the signal trend, days 7 and 28 after release")).toBeTruthy();
    const against = screen.getByTestId("proposal-case-C-4");
    expect(against.dataset.state).toBe("written");
    expect(within(against).getByText("Case against · Lead B · Sentinel")).toBeTruthy();
    expect(within(against).getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getByTestId("proposal-apply-C-4")).toBeTruthy();
  });

  it("cannot be applied while the other lead writes the case against", () => {
    renderCard(betProposal("cosigning", NOW));

    expect(screen.getByTestId("proposal-cosigning-C-4").textContent).toBe(
      "Waits for the other lead's case against",
    );
    expect(screen.queryByTestId("proposal-apply-C-4")).toBeNull();
    expect(screen.queryByTestId("proposal-edit-C-4")).toBeNull();
    expect(screen.getByTestId("proposal-reject-C-4")).toBeTruthy();
    expect(screen.getByTestId("proposal-case-C-4").dataset.state).toBe("waiting");
    // Not amber: it waits for the other lead, not for a person.
    expect(screen.getByTestId("proposal-card-C-4").className).not.toMatch(/border-warning/);
    expect(screen.getByTestId("proposal-state-C-4").className).not.toMatch(/warning/);
    expect(screen.queryByText(/^If you approve/)).toBeNull();
  });

  it("a requested bet does not wait for a case against", () => {
    renderCard(betProposal("requested", NOW));

    const against = screen.getByTestId("proposal-case-C-4");
    expect(against.dataset.state).toBe("later");
    expect(within(against).getByText(/It does not block approval/)).toBeTruthy();
    expect(screen.getByTestId("proposal-apply-C-4")).toBeTruthy();
    expect(screen.getByTestId("proposal-card-C-4").className).toMatch(/border-warning/);
    expect(screen.getByText(/^If you approve/)).toBeTruthy();
  });

  it("draws a predicted drop below zero", () => {
    const drop = {
      ...betProposal("waiting", NOW),
      bet: sampleBet({ expected: { direction: "down", low: -4, high: -1, unit: "pp" } }),
    };
    renderCard(drop);

    const block = screen.getByTestId("proposal-bet-C-4");
    expect(
      within(block).getByRole("img", { name: "Predicted -4 to -1 pp, not measured yet" }),
    ).toBeTruthy();
    expect(within(block).getByText(/^expects down -4 to -1 pp/)).toBeTruthy();
  });

  it("says when nobody wrote a case against", () => {
    renderCard(betProposal("alone", NOW));

    const against = screen.getByTestId("proposal-case-C-4");
    expect(against.dataset.state).toBe("missing");
    expect(within(against).getByText(/Read the evidence before you approve/)).toBeTruthy();
  });

  it("marks requested work from an opportunity as not measured", () => {
    renderCard(requestedFromOpportunity());

    expect(screen.getByTestId("proposal-why-C-4").textContent).toBe(
      "From O-7 · requested, no bet: not measured",
    );
    expect(screen.queryByTestId("proposal-bet-C-4")).toBeNull();
  });

  it("keeps a proposal without a bet as it was", () => {
    const plain = { ...betProposal("waiting", NOW), bet: null, opportunity: null };
    renderCard(plain);

    expect(screen.queryByTestId("proposal-bet-C-4")).toBeNull();
    expect(screen.queryByTestId("proposal-why-C-4")).toBeNull();
  });

  it.each(["cosigning", "waiting", "requested", "alone", "applied"] as const)(
    "%s has no axe violations",
    async (state) => {
      const { container } = renderCard(betProposal(state, NOW));
      const results = await axe.run(container, {
        rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
      });
      expect(results.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
    },
  );
});
