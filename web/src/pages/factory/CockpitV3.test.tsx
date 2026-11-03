import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LIVE_RUN_FACTORY_ID, preflightOk } from "@/components/factory/__fixtures__/liveRun";
import { round3Tasks } from "@/components/factory/agents/__fixtures__/opsFixtures";
import { resetFactoryControls } from "@/components/factory/controls/useFactoryControls";
import { definitionResponse } from "@/components/factory/insights/__fixtures__/learnFixtures";
import { notifyMe } from "@/components/factory/notify/__fixtures__/notifyFixtures";
import { round3Snapshot } from "@/components/factory/shell/__fixtures__/shellFixtures";
import { COCKPIT_STORAGE_KEY, resetCockpitChoiceCache } from "@/components/factory/shell/cockpitV2";
import type { CockpitChoice } from "@/lib/factoryApi";
import { FactoryPage } from "./FactoryPage";
import { type FactoryServer, SNAPSHOT, json, serveFactory } from "./testServer";

// Cockpit v3 ships behind the cockpit choice: `?cockpit=v3` (while the server
// offers the new cockpit) turns it on in this browser; v1 and v2 stay as they
// are, and v2 stays what someone who chose the new cockpit sees by default.

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({
  useSessionUpdatesConnected: () => true,
}));

const BASE = `/factory/${LIVE_RUN_FACTORY_ID}`;

let location = "";
function LocationProbe() {
  const loc = useLocation();
  location = loc.pathname + loc.search;
  return null;
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`${BASE}${path}`]}>
        <Routes>
          <Route
            path="/factory/:factoryId/*"
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
}

let server: FactoryServer;
let saved: CockpitChoice | null;

function serve({ choice = "v2" as CockpitChoice | null, offered = true } = {}) {
  saved = choice;
  server = serveFactory(authenticatedFetch, {
    snapshot: () => json(round3Snapshot()),
    overrides: {
      "GET /v1/factory/preflight": () => json({ ...preflightOk(), cockpit_v2: offered }),
      [`GET ${SNAPSHOT}/notify/me/ui`]: () => json({ cockpit: saved }),
      [`PUT ${SNAPSHOT}/notify/me/ui`]: async () => {
        saved = (server.calls.at(-1)!.body as { cockpit: CockpitChoice }).cockpit;
        return json({ cockpit: saved });
      },
      [`GET ${SNAPSHOT}/notify/me`]: () => json(notifyMe()),
      [`GET ${SNAPSHOT}/definition`]: () => json(definitionResponse()),
      [`GET ${SNAPSHOT}/tasks`]: () => json({ tasks: round3Tasks(), projects: [] }),
    },
  });
}

function puts(): unknown[] {
  return server.calls
    .filter((c) => c.method === "PUT" && c.path.endsWith("/notify/me/ui"))
    .map((c) => c.body);
}

beforeEach(() => {
  authenticatedFetch.mockReset();
  resetFactoryControls();
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
  localStorage.clear();
  resetCockpitChoiceCache();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("cockpit v3 · the switch and routes", () => {
  it("takes a ?cockpit=v3 link: Home is the product's node page in the new cockpit's shell", async () => {
    serve();
    renderAt("?cockpit=v3");

    const page = await screen.findByTestId("v3-node-page");
    expect(page).toHaveAttribute("data-ref", "product");
    expect(screen.getByTestId("factory-shell")).toHaveAttribute("data-cockpit", "v3");
    expect(screen.getByTestId("cockpit-topbar")).toBeInTheDocument();
    // This server has no node API: the page says it shows the sample.
    expect(screen.getByTestId("v3-sample-notice")).toHaveTextContent("Sample data");
    await waitFor(() => expect(location).toBe(BASE));
    expect(localStorage.getItem(COCKPIT_STORAGE_KEY)).toBe("v3");
    // The server stores v1 or v2 only: v3 stays in this browser.
    expect(puts()).toEqual([]);
  });

  it("keeps v3 after a reload, over the person's stored v2", async () => {
    serve({ choice: "v2" });
    const first = renderAt("?cockpit=v3");
    await screen.findByTestId("v3-node-page");
    first.unmount();
    resetCockpitChoiceCache();

    renderAt("");
    expect(await screen.findByTestId("v3-node-page")).toHaveAttribute("data-ref", "product");
  });

  it("a v3 link to a node or a level survives a reload", async () => {
    localStorage.setItem(COCKPIT_STORAGE_KEY, "v3");
    serve();
    const first = renderAt("/n/I-1");
    expect(await screen.findByTestId("v3-node-page")).toHaveAttribute("data-ref", "I-1");
    first.unmount();

    renderAt("/n/I-1");
    expect(await screen.findByTestId("v3-node-page")).toHaveAttribute("data-ref", "I-1");
    expect(location).toBe(`${BASE}/n/I-1`);
    expect(screen.getByTestId("v3-level-strip")).toBeInTheDocument();
  });

  it("opens one level under a node from its address", async () => {
    localStorage.setItem(COCKPIT_STORAGE_KEY, "v3");
    serve();
    renderAt("/n/product/project");

    expect(await screen.findByTestId("v3-level-view")).toBeInTheDocument();
    expect(screen.getByTestId("v3-below")).toHaveTextContent("grouped under features");
  });

  it("goes back to v2 from the page, and saves v2 as before", async () => {
    serve({ choice: "v1" });
    renderAt("?cockpit=v3");
    fireEvent.click(await screen.findByRole("button", { name: "Back to cockpit v2" }));

    await waitFor(() => expect(screen.queryByTestId("v3-node-page")).toBeNull());
    expect(screen.getByTestId("factory-shell")).toHaveAttribute("data-cockpit", "v2");
    await waitFor(() => expect(puts()).toEqual([{ cockpit: "v2" }]));
    expect(localStorage.getItem(COCKPIT_STORAGE_KEY)).toBe("v2");
  });

  it("leaves v2 the default for someone who chose the new cockpit", async () => {
    serve({ choice: "v2" });
    renderAt("");

    expect(await screen.findByTestId("cockpit-topbar")).toBeInTheDocument();
    expect(screen.getByTestId("factory-shell")).toHaveAttribute("data-cockpit", "v2");
    expect(screen.queryByTestId("v3-node-page")).toBeNull();
  });

  it("sends a v3 link opened on v2 to the closest page there (a project's page)", async () => {
    serve({ choice: "v2" });
    renderAt("/n/db-version");

    await waitFor(() => expect(location).toBe(`${BASE}/projects/db-version`));
  });

  it("sends a task's v3 link opened on v2 to its project's page", async () => {
    serve({ choice: "v2" });
    renderAt("/n/T-3");

    await waitFor(() => expect(location).toBe(`${BASE}/projects/api-version`));
  });

  it("offers the way back to v2 in the person's menu while v3 is on", async () => {
    serve({ choice: "v2" });
    renderAt("?cockpit=v3");
    await screen.findByTestId("v3-node-page");

    fireEvent.pointerDown(screen.getByTestId("cockpit-you"), {
      button: 0,
      ctrlKey: false,
      pointerType: "mouse",
    });
    fireEvent.click(await screen.findByTestId("cockpit-you-v2"));

    await waitFor(() => expect(screen.queryByTestId("v3-node-page")).toBeNull());
    expect(screen.getByTestId("factory-shell")).toHaveAttribute("data-cockpit", "v2");
    expect(localStorage.getItem(COCKPIT_STORAGE_KEY)).toBe("v2");
  });

  it("says an answer on sample data answered nothing", async () => {
    serve();
    renderAt("?cockpit=v3");
    const status = await screen.findByTestId("v3-status");

    fireEvent.click(within(status).getAllByRole("button", { name: "Apply" })[0]!);

    expect(await screen.findByText(/Sample data: nothing was answered/)).toBeInTheDocument();
    expect(server.calls.filter((c) => c.path.includes("/decisions/"))).toEqual([]);
  });

  it("ignores ?cockpit=v3 while the server does not offer the new cockpit", async () => {
    serve({ offered: false, choice: null });
    renderAt("?cockpit=v3");

    expect(await screen.findByTestId("factory-header")).toBeInTheDocument();
    expect(screen.queryByTestId("v3-node-page")).toBeNull();
    expect(localStorage.getItem(COCKPIT_STORAGE_KEY)).toBeNull();
  });
});
