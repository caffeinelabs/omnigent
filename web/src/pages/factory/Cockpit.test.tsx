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
import { sampleNodeSource } from "@/components/factory/v3/fixture/sampleNodeSource";
import { NodeSourceProvider } from "@/components/factory/v3/nodeSource";
import type { ThreadView } from "@/lib/factoryApi";
import { FactoryPage } from "./FactoryPage";
import { type FactoryServer, SNAPSHOT, json, serveFactory } from "./testServer";

// The cockpit is one thing for everyone: a sidebar (Home, Demos, Chats,
// Decisions, Search, then the outline of the work) around Home, Demos,
// Chats, Decisions and the node pages. Older addresses land on the place
// that shows the same thing now; there is no cockpit switch.

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
      <NodeSourceProvider source={sampleNodeSource()}>
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
      </NodeSourceProvider>
    </QueryClientProvider>,
  );
}

function thread(key: string, fields: Partial<ThreadView> = {}): ThreadView {
  return {
    key,
    kind: key.startsWith("project:") ? "project" : "factory",
    title: key === "factory" ? "#factory" : `#${key.split(":")[1]}`,
    node_ref: key === "factory" ? "product" : key.split(":")[1]!,
    project_slug: key.startsWith("project:") ? key.split(":")[1]! : null,
    pr_id: null,
    pr_number: null,
    responder_session_id: null,
    responder_title: "Atlas",
    last_seq: 1,
    last_at: 1_700_000_000,
    messages: 3,
    unread: 0,
    ...fields,
  };
}

let server: FactoryServer;
function serve(threads: ThreadView[] = []) {
  server = serveFactory(authenticatedFetch, {
    snapshot: () => json(round3Snapshot()),
    overrides: {
      "GET /v1/factory/preflight": () => json(preflightOk()),
      [`GET ${SNAPSHOT}/notify/me`]: () => json(notifyMe()),
      [`GET ${SNAPSHOT}/definition`]: () => json(definitionResponse()),
      [`GET ${SNAPSHOT}/tasks`]: () => json({ tasks: round3Tasks(), projects: [] }),
      [`GET ${SNAPSHOT}/chat/threads`]: () => json({ data: threads }),
    },
  });
  return server;
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
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the cockpit · one sidebar, always in view", () => {
  it("lists the four places and Search, then the outline of the work, on Home", async () => {
    serve([thread("factory", { unread: 4 })]);
    renderAt("");
    const sidebar = await screen.findByTestId("cockpit-sidebar");
    for (const place of ["home", "demos", "chats", "decisions"]) {
      expect(within(sidebar).getByTestId(`cockpit-nav-${place}`)).toBeInTheDocument();
    }
    expect(within(sidebar).getByTestId("cockpit-nav-home")).toHaveAttribute("aria-current", "page");
    expect(within(sidebar).getByTestId("cockpit-nav-search")).toBeInTheDocument();
    await waitFor(() =>
      expect(within(sidebar).getByTestId("cockpit-nav-chats-count")).toHaveTextContent("4"),
    );
    const outline = within(sidebar).getByTestId("cockpit-outline");
    await waitFor(() =>
      expect(within(outline).getAllByTestId("cockpit-outline-node").length).toBeGreaterThan(0),
    );
    // No cockpit switch, no level strip anywhere.
    expect(screen.queryByTestId("cockpit-you-classic")).toBeNull();
    expect(screen.queryByTestId("v3-level-strip")).toBeNull();
  });

  it("opens a feature in the outline to its projects and marks the page's node", async () => {
    serve();
    renderAt("/n/assistant-core");
    const outline = await screen.findByTestId("cockpit-outline");
    const here = await within(outline).findByRole("link", { current: "page" });
    expect(here).toHaveAttribute("data-ref", "assistant-core");
    expect(screen.getByTestId("v3-breadcrumb")).toHaveTextContent(/^Home/);
  });

  it("goes to Search on its button and to Demos from the sidebar", async () => {
    serve();
    renderAt("");
    fireEvent.click(await screen.findByTestId("cockpit-nav-demos"));
    await waitFor(() => expect(location).toBe(`${BASE}/demos`));
    expect(await screen.findByTestId("cockpit-demos-page")).toBeInTheDocument();
  });
});

describe("the cockpit · Home", () => {
  it("shows what needs you, the features one line each, and nothing else by default", async () => {
    serve();
    renderAt("");
    const home = await screen.findByTestId("cockpit-home");
    expect(within(home).getByTestId("cockpit-home-needs")).toBeInTheDocument();
    const features = await within(home).findAllByTestId("cockpit-home-feature");
    expect(features.length).toBeGreaterThan(0);
    // At most three asks, the rest behind "+n more" (Decisions).
    await waitFor(() =>
      expect(within(home).queryAllByTestId("v3-move-row").length).toBeLessThanOrEqual(3),
    );
  });
});

describe("the cockpit · Demos", () => {
  it("lists every demo newest first, filters by verdict, and plays one in place", async () => {
    serve();
    renderAt("/demos");
    const list = await screen.findByTestId("cockpit-demos");
    const cards = within(list).getAllByTestId("cockpit-demo");
    expect(cards.length).toBeGreaterThan(3);
    expect(cards[0]).toHaveAttribute("data-verdict", "PARTIAL");
    fireEvent.click(screen.getByRole("button", { name: "Failed" }));
    await waitFor(() => expect(location).toBe(`${BASE}/demos?verdict=FAIL`));
    const failed = within(screen.getByTestId("cockpit-demos")).getAllByTestId("cockpit-demo");
    expect(failed.every((c) => c.getAttribute("data-verdict") === "FAIL")).toBe(true);
    fireEvent.click(within(failed[0]!).getByTestId("cockpit-demo-play"));
    expect(await within(failed[0]!).findByTestId("v3-proof-player")).toBeInTheDocument();
  });

  it("opens one playing from the address", async () => {
    serve();
    renderAt("/demos?play=demo-9876");
    const card = (await screen.findAllByTestId("cockpit-demo")).find(
      (c) => c.getAttribute("data-demo") === "demo-9876",
    )!;
    expect(within(card).getByTestId("v3-proof-player")).toBeInTheDocument();
  });
});

describe("the cockpit · Chats", () => {
  it("lists the chats you wrote in first, then the newest, with unread counts and the last line", async () => {
    serve([
      thread("project:api-version", {
        last_at: 1_700_000_900,
        unread: 2,
        last: { who: "Juno", kind: "agent", text: "Merged the schema", at: 1_700_000_900 },
      }),
      thread("factory", {
        mine: true,
        last: { who: "You", kind: "human", text: "Start the dark mode", at: 1_700_000_100 },
      }),
      thread("project:db-version", { messages: 0, last_at: null, last_seq: 0 }),
    ]);
    renderAt("/chats");
    const list = await screen.findByTestId("cockpit-chats");
    const rows = within(list).getAllByTestId("cockpit-chat-row");
    expect(rows.map((r) => r.getAttribute("data-thread"))).toEqual([
      "factory",
      "project:api-version",
    ]);
    expect(rows[0]).toHaveTextContent("Factory chat");
    expect(rows[0]).toHaveTextContent("You: Start the dark mode");
    expect(within(rows[1]!).getByTestId("cockpit-chat-unread")).toHaveTextContent("2");
    // A thread nobody wrote in sits behind "quiet chats".
    expect(screen.getByText(/1 quiet chat/)).toBeInTheDocument();
  });
});

describe("the cockpit · older addresses", () => {
  it.each([
    ["/projects/db-version", "/n/db-version"],
    ["/projects/db-version/history", "/n/db-version"],
    ["/projects", ""],
    ["/initiatives/I-3", "/n/I-3"],
    ["?cockpit=v1", ""],
    ["?peek=needs", "/decisions"],
    ["/n/I-3?cockpit=v3", "/n/I-3"],
  ])("lands %s on %s", async (from, to) => {
    serve();
    renderAt(from);
    await waitFor(() => expect(location).toBe(`${BASE}${to}`));
  });

  it("lands a PR page and a ?review= link on the PR's change", async () => {
    serve();
    const pr = round3Snapshot().prs[0]!;
    renderAt(`/prs/${pr.id}`);
    await waitFor(() => expect(location).toBe(`${BASE}/n/PR-${pr.number}`));
  });

  it("keeps the team editor's address", async () => {
    serve();
    renderAt("/projects/db-version/team");
    await screen.findByTestId("factory-shell");
    expect(location).toBe(`${BASE}/projects/db-version/team`);
  });
});

describe("the cockpit · counts", () => {
  it("never asks the server for a cockpit choice", async () => {
    serve();
    renderAt("");
    await screen.findByTestId("cockpit-home");
    expect(server.calls.some((c) => c.path.endsWith("/notify/me/ui"))).toBe(false);
  });
});
