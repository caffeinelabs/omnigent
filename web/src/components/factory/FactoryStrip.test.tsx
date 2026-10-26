import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FALLBACK_SERVER_INFO } from "@/lib/capabilities";
import { CapabilitiesProvider } from "@/lib/CapabilitiesContext";
import type { Session } from "@/lib/types";
import {
  LIVE_RUN_FACTORY_ID,
  LIVE_RUN_NOW_S,
  atlasSession,
  icSession,
  liveRunSnapshot,
  pendingForever,
  plSession,
} from "./__fixtures__/liveRun";
import { FactoryStrip } from "./FactoryStrip";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({ useSessionUpdatesConnected: () => true }));
vi.mock("@/hooks/useNow", () => ({ useNow: () => new Date(LIVE_RUN_NOW_S * 1000) }));

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
}

function renderStrip(
  sessionId: string,
  sessions: Session[],
  { factory = true }: { factory?: boolean } = {},
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  for (const session of sessions) client.setQueryData(["session", session.id], session);
  return render(
    <QueryClientProvider client={client}>
      <CapabilitiesProvider
        info={{ ...FALLBACK_SERVER_INFO, features: factory ? { factory: true } : {} }}
      >
        <MemoryRouter>
          <FactoryStrip sessionId={sessionId} />
        </MemoryRouter>
      </CapabilitiesProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  authenticatedFetch.mockReset();
  authenticatedFetch.mockImplementation(async (path: string) => {
    if (path === `/v1/factory/factories/${LIVE_RUN_FACTORY_ID}`) return json(liveRunSnapshot());
    throw new Error(`unexpected ${path}`);
  });
});

describe("FactoryStrip", () => {
  it("shows a PL's project, stage, PR gates, preview and cost, linking to its project", async () => {
    const pl = plSession();
    renderStrip(pl.id, [pl]);

    const strip = await screen.findByTestId("factory-strip");
    await waitFor(() => expect(strip).toHaveAttribute("data-live", "true"));
    expect(strip).toHaveAttribute("href", `/factory/${LIVE_RUN_FACTORY_ID}?project=api-version`);
    expect(strip).toHaveTextContent("Atlas factory");
    expect(strip).toHaveTextContent("api-version");
    // The stage sits in the never-wrapping head, so a narrow header keeps it.
    const stage = within(within(strip).getByTestId("factory-strip-place")).getByTestId(
      "factory-strip-stage",
    );
    expect(stage).toHaveAttribute("data-stage", "ready");
    expect(stage).toHaveTextContent("Ready");
    // Ready since 760 s before the fixture's now.
    expect(strip).toHaveTextContent("12m");
    expect(strip).toHaveTextContent("PR #9630");
    const dots = within(strip).getByTestId("factory-gate-dots");
    expect(dots.querySelectorAll("[data-gate]")).toHaveLength(7);
    expect(dots).toHaveAccessibleName(expect.stringContaining("CI pass"));
    expect(within(strip).getByTestId("factory-strip-preview")).toHaveAttribute("data-tone", "pass");
    expect(strip).toHaveTextContent("$7.10");
    expect(strip).toHaveTextContent("Open cockpit");
    expect(screen.getByTestId("factory-strip-mobile")).toHaveAttribute(
      "href",
      `/factory/${LIVE_RUN_FACTORY_ID}?project=api-version`,
    );
  });

  it("reads an IC sub-agent's factory from its root PL and shows the IC's PR", async () => {
    const pl = plSession();
    const ic = icSession();
    renderStrip(ic.id, [pl, ic]);

    const strip = await screen.findByTestId("factory-strip");
    await waitFor(() => expect(strip).toHaveAttribute("data-live", "true"));
    expect(strip).toHaveAttribute("href", `/factory/${LIVE_RUN_FACTORY_ID}?project=api-version`);
    expect(strip).toHaveTextContent("api-version");
    expect(strip).toHaveTextContent("ic_codex:version-endpoint");
    expect(strip).toHaveTextContent("PR #9630");
    // The stage, preview and cost belong to the PL's strip, not a sub-agent's.
    expect(within(strip).queryByTestId("factory-strip-stage")).toBeNull();
    expect(within(strip).queryByTestId("factory-strip-preview")).toBeNull();
  });

  it("flags a preview that runs behind the PR head", async () => {
    const pl = {
      ...plSession(),
      labels: { ...plSession().labels, "omnigent.factory.project": "db-version" },
    };
    renderStrip(pl.id, [pl]);

    const strip = await screen.findByTestId("factory-strip");
    await waitFor(() => expect(strip).toHaveAttribute("data-live", "true"));
    expect(within(strip).getByTestId("factory-strip-stage")).toHaveTextContent(
      "Building · Blocked",
    );
    expect(within(strip).getByTestId("factory-strip-preview")).toHaveAttribute(
      "data-tone",
      "warning",
    );
  });

  it("summarises the whole factory for a lead", async () => {
    const atlas = atlasSession();
    renderStrip(atlas.id, [atlas]);

    const strip = await screen.findByTestId("factory-strip");
    await waitFor(() => expect(strip).toHaveAttribute("data-live", "true"));
    expect(strip).toHaveAttribute("href", `/factory/${LIVE_RUN_FACTORY_ID}`);
    expect(strip).toHaveTextContent("3 projects");
    expect(strip).toHaveTextContent("1 ready for review");
    expect(strip).toHaveTextContent("2 decisions");
  });

  it("falls back to its labels while the snapshot is unavailable", async () => {
    authenticatedFetch.mockImplementation(() => pendingForever());
    const pl = plSession();
    renderStrip(pl.id, [pl]);

    const strip = await screen.findByTestId("factory-strip");
    expect(strip).toHaveAttribute("data-live", "false");
    expect(strip).toHaveTextContent("Factory");
    expect(strip).toHaveTextContent("api-version");
    expect(strip).toHaveTextContent("Open cockpit");
  });

  it("renders nothing for a session outside any factory", () => {
    const plain = { ...plSession(), labels: {} };
    const { container } = renderStrip(plain.id, [plain]);
    expect(container).toBeEmptyDOMElement();
    expect(authenticatedFetch).not.toHaveBeenCalled();
  });

  it("renders nothing while the factory feature is off", () => {
    const pl = plSession();
    const { container } = renderStrip(pl.id, [pl], { factory: false });
    expect(container).toBeEmptyDOMElement();
    expect(authenticatedFetch).not.toHaveBeenCalled();
  });
});
