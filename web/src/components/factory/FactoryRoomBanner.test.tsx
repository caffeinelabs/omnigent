import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { FALLBACK_SERVER_INFO } from "@/lib/capabilities";
import { CapabilitiesProvider } from "@/lib/CapabilitiesContext";
import type { Session } from "@/lib/types";
import { LIVE_RUN_FACTORY_ID, atlasSession, plSession } from "./__fixtures__/liveRun";
import { FactoryRoomBanner } from "./FactoryRoomBanner";

vi.mock("@/lib/identity", () => ({
  authenticatedFetch: () => Promise.reject(new Error("no network in this test")),
}));

function renderBanner(session: Session, { factory = true }: { factory?: boolean } = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  client.setQueryData(["session", session.id], session);
  return render(
    <QueryClientProvider client={client}>
      <CapabilitiesProvider
        info={{ ...FALLBACK_SERVER_INFO, features: factory ? { factory: true } : {} }}
      >
        <MemoryRouter>
          <FactoryRoomBanner sessionId={session.id} />
        </MemoryRouter>
      </CapabilitiesProvider>
    </QueryClientProvider>,
  );
}

describe("FactoryRoomBanner", () => {
  it("points a PL's chat at its project thread in the Room", () => {
    const pl = plSession();
    renderBanner(pl);
    const banner = screen.getByTestId("factory-room-banner");
    expect(banner).toHaveTextContent(`This is ${pl.title}’s machine channel.`);
    expect(screen.getByRole("link", { name: /Talk to the factory in the Room/ })).toHaveAttribute(
      "href",
      `/factory/${LIVE_RUN_FACTORY_ID}/room/project%3Aapi-version`,
    );
  });

  it("points a lead's chat at #factory", () => {
    renderBanner(atlasSession());
    expect(screen.getByRole("link", { name: /Talk to the factory/ })).toHaveAttribute(
      "href",
      `/factory/${LIVE_RUN_FACTORY_ID}/room/factory`,
    );
  });

  it("renders nothing without the factory or outside one", () => {
    const { container } = renderBanner(plSession(), { factory: false });
    expect(container).toBeEmptyDOMElement();
    const plain = { ...atlasSession(), labels: {} };
    const second = renderBanner(plain);
    expect(second.container).toBeEmptyDOMElement();
  });
});
