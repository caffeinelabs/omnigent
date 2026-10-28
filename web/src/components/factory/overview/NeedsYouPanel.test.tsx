import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeAll, describe, expect, it } from "vitest";
import { FactoryNowContext } from "../factoryClock";
import { ROUND3_NOW_S, round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import { FactoryViewerProvider } from "../shell/viewerCan";
import { splitNeedsYou } from "./needsYouModel";
import { NeedsYouPanel } from "./NeedsYouPanel";

beforeAll(() => {
  Element.prototype.scrollIntoView ??= () => undefined;
});

const NOW = new Date(ROUND3_NOW_S * 1000);

function renderPanel(highlightDecision: string | null) {
  const snapshot = round3Snapshot("operator");
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <FactoryNowContext.Provider value={NOW}>
          <FactoryViewerProvider viewer={snapshot.viewer}>
            <NeedsYouPanel
              snapshot={snapshot}
              split={splitNeedsYou(snapshot)}
              nowS={ROUND3_NOW_S}
              clockOffsetS={0}
              highlightDecision={highlightDecision}
            />
          </FactoryViewerProvider>
        </FactoryNowContext.Provider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("NeedsYouPanel ?decision= (no dock)", () => {
  it("opens the named decision's card, in a tab that lists it", () => {
    // D-3 is for owners: Mia (operator) finds it under All, not Mine.
    renderPanel("D-3");
    expect(screen.getByTestId("needs-tab-all")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("decision-card-D-3")).toBeInTheDocument();
  });

  it("opens no card without a highlight", () => {
    renderPanel(null);
    expect(screen.getByTestId("needs-tab-mine")).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByTestId("decision-card-D-3")).toBeNull();
  });
});
