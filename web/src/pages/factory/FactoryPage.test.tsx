import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  LIVE_RUN_FACTORY_ID,
  liveRunSnapshot,
  pendingForever,
} from "@/components/factory/__fixtures__/liveRun";
import { boardActivityEvents } from "@/components/factory/board/__fixtures__/boardFixtures";
import { factoryQueryKey } from "@/hooks/useFactory";
import { FactoryPage } from "./FactoryPage";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
const connected = vi.hoisted(() => ({ value: true }));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({
  useSessionUpdatesConnected: () => connected.value,
}));

const SNAPSHOT = `/v1/factory/factories/${LIVE_RUN_FACTORY_ID}`;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function serve(snapshot: () => Response | Promise<Response> = () => json(liveRunSnapshot())) {
  authenticatedFetch.mockImplementation(async (path: string) => {
    if (path.startsWith(`${SNAPSHOT}/events`)) return json(boardActivityEvents());
    if (path === SNAPSHOT) return snapshot();
    throw new Error(`unexpected ${path}`);
  });
}

function setWide(wide: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: query.includes("min-width: 1280px") ? wide : false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

let location = "";
function LocationProbe() {
  const loc = useLocation();
  location = loc.pathname + loc.search;
  return null;
}

function renderPage(search = "", seed?: (client: QueryClient) => void) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  seed?.(client);
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/factory/${LIVE_RUN_FACTORY_ID}${search}`]}>
        <Routes>
          <Route
            path="/factory/:factoryId"
            element={
              <>
                <FactoryPage />
                <LocationProbe />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return client;
}

beforeEach(() => {
  authenticatedFetch.mockReset();
  connected.value = true;
  setWide(true);
});
afterEach(() => vi.unstubAllGlobals());

describe("FactoryPage", () => {
  it("shows skeletons, never an empty board, while the first read is pending", () => {
    serve(() => pendingForever());
    renderPage();
    expect(screen.getByTestId("factory-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("factory-board-empty")).toBeNull();
    expect(screen.queryByTestId("factory-missing")).toBeNull();
  });

  it("renders header, health, needs-you, stage strip, cards and the decisions rail", async () => {
    serve();
    renderPage();
    expect(await screen.findByTestId("factory-header")).toHaveTextContent("Atlas factory");
    expect(screen.getByTestId("factory-live-dot")).toHaveAttribute("data-state", "live");
    expect(screen.getByTestId("factory-health-strip")).toBeInTheDocument();
    expect(screen.getByTestId("factory-needs-you")).toBeInTheDocument();
    expect(screen.getByTestId("factory-stage-strip")).toBeInTheDocument();
    expect(screen.getByTestId("project-card-api-version")).toBeInTheDocument();
    const rail = screen.getByTestId("factory-rail");
    expect(within(rail).getByTestId("decision-card-D-2")).toBeInTheDocument();
  });

  it("says when updates are not live and it is polling", async () => {
    connected.value = false;
    serve();
    renderPage();
    const dot = await screen.findByTestId("factory-live-dot");
    expect(dot).toHaveAttribute("data-state", "reconnecting");
    expect(dot).toHaveTextContent("Reconnecting · polling 15 s");
  });

  it("keeps stale data under an error alert when a refresh fails, and retries", async () => {
    let fail = true;
    serve(() => (fail ? json({ error: { message: "down" } }, 503) : json(liveRunSnapshot())));
    const client = renderPage("", (c) =>
      c.setQueryData(factoryQueryKey(LIVE_RUN_FACTORY_ID), liveRunSnapshot(), {
        updatedAt: new Date(2026, 9, 26, 14, 2).getTime(),
      }),
    );
    await act(async () => {
      await client.invalidateQueries({ queryKey: factoryQueryKey(LIVE_RUN_FACTORY_ID) });
    });
    const alert = await screen.findByTestId("factory-load-error");
    expect(alert).toHaveTextContent("Couldn’t refresh this factory");
    expect(alert).toHaveTextContent(/HTTP 503 · showing data from \d{1,2}:02/);
    expect(screen.getByTestId("project-card-api-version")).toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByTestId("factory-load-error-retry"));
    await waitFor(() => expect(screen.queryByTestId("factory-load-error")).toBeNull());
  });

  it("switches to the Activity tab through ?tab", async () => {
    serve();
    renderPage();
    const tab = await screen.findByTestId("factory-tab-activity");
    fireEvent.mouseDown(tab);
    await waitFor(() => expect(location).toContain("tab=activity"));
    expect(await screen.findByTestId("activity-list")).toBeInTheDocument();
    expect(screen.queryByTestId("factory-board")).toBeNull();
  });

  it("opens the project drawer from a card and closes it", async () => {
    serve();
    renderPage();
    fireEvent.click(await screen.findByTestId("project-open-api-version"));
    expect(await screen.findByTestId("project-drawer-api-version")).toBeInTheDocument();
    expect(location).toContain("project=api-version");
    fireEvent.click(
      within(screen.getByTestId("project-drawer-api-version")).getByRole("button", {
        name: "Close",
      }),
    );
    await waitFor(() => expect(location).not.toContain("project="));
  });

  it("opens the review drawer from ?review and from a PR row", async () => {
    serve();
    renderPage("?review=9630");
    const drawer = await screen.findByTestId("review-drawer");
    expect(drawer).toHaveTextContent("Review · PR #9630");
    expect(drawer).toHaveTextContent("Ready for human merge");
    fireEvent.click(within(drawer).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByTestId("review-drawer")).toBeNull());
    fireEvent.click(screen.getByTestId("review-open-9631"));
    expect(await screen.findByTestId("review-drawer")).toHaveTextContent("Review · PR #9631");
    expect(location).toContain("review=9631");
  });

  it("highlights ?decision in the rail and points the blocked card at it", async () => {
    serve();
    renderPage("?decision=D-2");
    expect(await screen.findByTestId("decision-card-D-2")).toHaveAttribute(
      "data-highlighted",
      "true",
    );
    expect(screen.getByTestId("decision-card-D-3")).not.toHaveAttribute("data-highlighted");
    fireEvent.click(screen.getByTestId("needs-you-decisions"));
    expect(location).toContain("decision=D-2");
  });

  it("folds the rail into the Needs-you strip below 1280 px", async () => {
    setWide(false);
    serve();
    renderPage();
    await screen.findByTestId("factory-board");
    expect(screen.queryByTestId("factory-rail")).toBeNull();
    expect(screen.queryByTestId("decisions-rail")).toBeNull();
    fireEvent.click(screen.getByTestId("needs-you-decisions"));
    const panel = screen.getByTestId("needs-you-decisions-panel");
    expect(within(panel).getByTestId("decision-card-D-2")).toBeInTheDocument();
  });

  it("scrolls to and flashes what a Needs-you chip points at", async () => {
    serve();
    renderPage();
    const row = await screen.findByTestId("pr-row-9630");
    const scroll = vi.fn();
    row.scrollIntoView = scroll;
    fireEvent.click(screen.getByTestId("needs-you-ready"));
    expect(scroll).toHaveBeenCalledWith(expect.objectContaining({ block: "center" }));
    expect(row).toHaveAttribute("data-highlighted", "true");
  });

  it("says when the factory does not exist", async () => {
    serve(() => json({ error: { message: "not found" } }, 404));
    renderPage();
    expect(await screen.findByTestId("factory-missing")).toBeInTheDocument();
  });
});
