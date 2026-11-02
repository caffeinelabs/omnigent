import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeAll, describe, expect, it } from "vitest";
import { FactoryNowContext } from "../factoryClock";
import { ROUND3_NOW_S, round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import { FactoryViewerProvider } from "../shell/viewerCan";
import { round3IssueList } from "../agents/__fixtures__/opsFixtures";
import { statusSummaryOf } from "./__fixtures__/nowFixtures";
import { nowShownKeys } from "./nowCardModel";
import { splitNeedsYou } from "./needsYouModel";
import { NeedsYouPanel } from "./NeedsYouPanel";

beforeAll(() => {
  Element.prototype.scrollIntoView ??= () => undefined;
});

const NOW = new Date(ROUND3_NOW_S * 1000);

function renderPanel(highlightDecision: string | null, shownInNow: Set<string> | null = null) {
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
              shownInNow={shownInNow}
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

describe("NeedsYouPanel beside the Now card", () => {
  it("leaves out what the Now card lists and says so", () => {
    const snapshot = round3Snapshot("operator");
    const now = nowShownKeys(statusSummaryOf(snapshot, { incidents: round3IssueList() }));
    const all = splitNeedsYou(snapshot).all;
    renderPanel(null, now);

    const listed = screen.queryAllByTestId(/^needs-item-/).map((li) => li.dataset.testid);
    expect(listed.length).toBeGreaterThan(0);
    for (const key of now) expect(listed).not.toContain(`needs-item-${key}`);
    expect(screen.getByTestId("needs-you-in-now")).toHaveTextContent(
      `${all.filter((i) => now.has(i.key)).length} listed in Now above`,
    );
  });

  it("says when everything that waits is in Now above", () => {
    const snapshot = round3Snapshot("operator");
    renderPanel(null, new Set(splitNeedsYou(snapshot).all.map((i) => i.key)));

    expect(screen.getByTestId("needs-you-empty")).toHaveTextContent(
      "Nothing else: everything that waits is in Now above.",
    );
  });
});
