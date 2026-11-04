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
import serverLevels from "@/components/factory/__fixtures__/nodeLevels.server.json";
import serverPages from "@/components/factory/__fixtures__/nodePages.server.json";
import { sampleNodeSource } from "@/components/factory/v3/fixture/sampleNodeSource";
import { type NodeSource, NodeSourceProvider } from "@/components/factory/v3/nodeSource";
import type { LevelView, NodePage } from "@/components/factory/v3/nodeTypes";
import { factoryQueryKey } from "@/hooks/useFactory";
import type { CockpitChoice, FactorySnapshot } from "@/lib/factoryApi";
import { FactoryPage } from "./FactoryPage";
import { type FactoryServer, SNAPSHOT, json, serveFactory } from "./testServer";

// Cockpit v3 ships behind the cockpit choice: `?cockpit=v3` (while the server
// offers the new cockpit) turns it on in this browser; v1 and v2 stay as they
// are, and v2 stays what someone who chose the new cockpit sees by default.
// v3 reads the server's node API; the switch tests pass the sample factory
// as their source, the live tests serve the server's own pages.

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

/** Render the factory at *path*; v3 reads *source* (the sample), or the server when null. */
function renderAt(path: string, source: NodeSource | null = sampleNodeSource()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const routes = (
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
  );
  const view = render(
    <QueryClientProvider client={client}>
      {source ? <NodeSourceProvider source={source}>{routes}</NodeSourceProvider> : routes}
    </QueryClientProvider>,
  );
  return { ...view, client };
}

const PAGES = serverPages as unknown as Record<string, NodePage>;
const LEVELS = serverLevels as unknown as Record<string, LevelView>;

/** The server's node API: its pages and level views (written by the server's tests). */
function nodeRoutes(): Record<string, () => Response> {
  const routes: Record<string, () => Response> = {};
  for (const [ref, page] of Object.entries(PAGES)) {
    routes[`GET ${SNAPSHOT}/n/${encodeURIComponent(ref)}`] = () => json(page);
  }
  for (const [key, view] of Object.entries(LEVELS)) {
    const [ref, level] = key.split("/") as [string, string];
    routes[`GET ${SNAPSHOT}/n/${encodeURIComponent(ref)}/level/${level}`] = () => json(view);
  }
  return routes;
}

let server: FactoryServer;
let saved: CockpitChoice | null;

function serve({
  choice = "v2" as CockpitChoice | null,
  offered = true,
  extra = {} as Record<string, () => Response>,
  snapshot = round3Snapshot as () => FactorySnapshot,
} = {}) {
  saved = choice;
  server = serveFactory(authenticatedFetch, {
    snapshot: () => json(snapshot()),
    overrides: {
      ...extra,
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
    // The sample source is labelled as sample.
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
    expect(screen.getAllByTestId("v3-group").length).toBeGreaterThan(0);
    // Grouped under the features' names; no meta text says so.
    expect(screen.getByTestId("v3-below")).not.toHaveTextContent("grouped under");
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

  it("sends a feature's v3 link opened on v2 to its initiative's page", async () => {
    serve({ choice: "v2" });
    renderAt("/n/I-1");

    await waitFor(() => expect(location).toBe(`${BASE}/initiatives/I-1`));
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
    const move = await screen.findByTestId("v3-your-move");

    fireEvent.click(within(move).getAllByRole("button", { name: "Apply" })[0]!);

    expect(await screen.findByText(/Sample data: nothing was answered/)).toBeInTheDocument();
    expect(server.calls.filter((c) => c.path.includes("/decisions/"))).toEqual([]);
  });

  it("opens v3's sheets from the shell: Needs you from an ask's peek, the base env, the keys", async () => {
    serve();
    renderAt("?cockpit=v3");
    const move = await screen.findByTestId("v3-your-move");
    const row = within(move).getAllByTestId("v3-move-row")[0]!;
    fireEvent.click(within(row).getByRole("button", { name: /Apply C-16/ }));
    fireEvent.click(within(row).getByRole("button", { name: "All asks" }));
    // The sheet lists the asks as Your move does (short words, never the raw title).
    const needs = await screen.findByTestId("v3-needs-sheet", {}, { timeout: 4000 });
    await waitFor(() => expect(needs).toHaveTextContent("Apply C-16"));
    expect(needs).not.toHaveTextContent("proposes C-16");
    await waitFor(() => expect(location).toBe(`${BASE}?peek=needs`));
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("v3-needs-sheet")).toBeNull());
    // One count: every ask of both trees (Product's 5 and Factory's 3), also those that block nothing.
    expect(await screen.findByTestId("cockpit-needs-you")).toHaveTextContent("8 need you");
    const env = await screen.findByTestId("cockpit-base-env");
    expect(env).toHaveTextContent("Base env sample-main @817480d");
    fireEvent.click(env);
    expect(await screen.findByTestId("v3-env-sheet")).toHaveTextContent("smoke OK");
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("v3-env-sheet")).toBeNull());
    fireEvent.keyDown(window, { key: "?" });
    expect(await screen.findByTestId("cockpit-keys")).toHaveTextContent("a · s · t");
    expect(location).toBe(`${BASE}?peek=keys`);
  });

  it("raises the “need you” pill when a new ask opens, without any v3 write", async () => {
    let snapshot = round3Snapshot();
    serve({ snapshot: () => snapshot });
    const sample = sampleNodeSource();
    let asked = false;
    const source: NodeSource = {
      ...sample,
      // The ranked list gains the new ask once it is open.
      needsYou: async (fid, tree, signal) => {
        const list = await sample.needsYou!(fid, tree, signal);
        const first = list.items[0]!;
        return asked
          ? {
              ...list,
              items: [...list.items, { ...first, decision: { ...first.decision, id: "D-99" } }],
            }
          : list;
      },
    };
    const { client } = renderAt("?cockpit=v3", source);
    // The ranked count (every ask, also those that block nothing) once the list is read.
    await waitFor(() =>
      expect(screen.getByTestId("cockpit-needs-you")).toHaveTextContent("8 need you"),
    );
    // An agent asks: the next snapshot (a push frame re-reads it) holds one more open decision.
    const open = snapshot.decisions.find((d) => d.status === "open")!;
    asked = true;
    snapshot = { ...snapshot, decisions: [...snapshot.decisions, { ...open, id: "D-99" }] };
    await client.invalidateQueries({ queryKey: factoryQueryKey(LIVE_RUN_FACTORY_ID) });
    await waitFor(() =>
      expect(screen.getByTestId("cockpit-needs-you")).toHaveTextContent("9 need you"),
    );
  });

  it("ignores ?cockpit=v3 while the server does not offer the new cockpit", async () => {
    serve({ offered: false, choice: null });
    renderAt("?cockpit=v3");

    expect(await screen.findByTestId("factory-header")).toBeInTheDocument();
    expect(screen.queryByTestId("v3-node-page")).toBeNull();
    expect(localStorage.getItem(COCKPIT_STORAGE_KEY)).toBeNull();
  });
});

describe("cockpit v3 · the server's node API", () => {
  it("reads the server's pages by default: Home lists its features under their needs", async () => {
    serve({ extra: nodeRoutes() });
    renderAt("?cockpit=v3", null);

    const page = await screen.findByTestId("v3-node-page");
    expect(page).toHaveAttribute("data-ref", "product");
    const view = await screen.findByTestId("v3-level-view");
    expect(within(view).getAllByTestId("v3-group")).toHaveLength(
      LEVELS["product/feature"]!.groups.length,
    );
    expect(screen.queryByTestId("v3-sample-notice")).toBeNull();
    const reads = server.calls.map((c) => c.path);
    expect(reads).toContain(`${SNAPSHOT}/n/product`);
    expect(reads).toContain(`${SNAPSHOT}/n/product/level/feature`);
  });

  it("Apply in Your move answers through the answer route and reads the page again", async () => {
    const decision = PAGES["assistant-core"]!.status.decision!;
    const answer = `/v1/factory/decisions/${decision.row_id}/answer`;
    serve({
      extra: { ...nodeRoutes(), [`POST ${answer}`]: () => json({ decision: { id: "x" } }) },
    });
    localStorage.setItem(COCKPIT_STORAGE_KEY, "v3");
    renderAt("/n/assistant-core", null);
    const move = await screen.findByTestId("v3-your-move");
    const reads = () => server.calls.filter((c) => c.path === `${SNAPSHOT}/n/assistant-core`);
    const before = reads().length;

    fireEvent.click(within(move).getByRole("button", { name: "Apply" }));

    await waitFor(() =>
      expect(server.calls.find((c) => c.path === answer)?.body).toEqual({ option_id: "apply" }),
    );
    await waitFor(() => expect(reads().length).toBeGreaterThan(before));
    expect(await screen.findByText("D-45: Apply")).toBeInTheDocument();
  });
});

describe("cockpit v3 · the factory tree in the shell", () => {
  it("Product | Factory in the top bar: one tree at a time, the factory's asks counted", async () => {
    localStorage.setItem(COCKPIT_STORAGE_KEY, "v3");
    serve();
    renderAt("/n/fx");

    expect(await screen.findByTestId("v3-node-page")).toHaveAttribute("data-ref", "fx");
    const trees = screen.getByTestId("cockpit-trees");
    await waitFor(() =>
      expect(within(trees).getByTestId("cockpit-tree-factory")).toHaveAttribute(
        "aria-current",
        "page",
      ),
    );
    // C-17 and C-18 wait on a person, and an older ask on Sentinel.
    expect(within(trees).getByTestId("cockpit-tree-factory-count")).toHaveTextContent("3");
    // v3's two trees take the place of v2's places.
    expect(screen.queryByTestId("cockpit-tabs")).toBeNull();

    fireEvent.click(within(trees).getByTestId("cockpit-tree-product"));
    expect(await screen.findByTestId("v3-node-page")).toHaveAttribute("data-ref", "product");
    await waitFor(() =>
      expect(
        within(screen.getByTestId("cockpit-trees")).getByTestId("cockpit-tree-product"),
      ).toHaveAttribute("aria-current", "page"),
    );
  });

  it("on a phone: one ⋯ menu in the top bar, and the bottom bar is Product · Factory · Chat", async () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn((query: string) => ({
        matches: query.includes("max-width: 767.98px"),
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    );
    localStorage.setItem(COCKPIT_STORAGE_KEY, "v3");
    serve();
    renderAt("");
    expect(await screen.findByTestId("v3-node-page")).toHaveAttribute("data-ref", "product");
    const bar = screen.getByTestId("cockpit-topbar");
    // Search, the chat, the theme and you fold into one menu: the bar fits 390 px.
    for (const gone of ["cockpit-search", "cockpit-room", "cockpit-theme", "cockpit-you"]) {
      expect(within(bar).queryByTestId(gone)).toBeNull();
    }
    fireEvent.pointerDown(within(bar).getByTestId("cockpit-more"), {
      button: 0,
      ctrlKey: false,
      pointerType: "mouse",
    });
    expect(await screen.findByTestId("cockpit-more-search")).toBeInTheDocument();
    expect(screen.getByTestId("cockpit-you-settings")).toBeInTheDocument();
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
    // No tab leaves v3: its two trees (each with its asks) and the chat.
    const tabs = screen.getByTestId("cockpit-phone-tabs");
    expect(tabs).toHaveAttribute("data-cockpit", "v3");
    expect(tabs).toHaveTextContent(/^\d*Product\d*FactoryChat$/);
    await waitFor(() =>
      expect(within(tabs).getByTestId("cockpit-phone-product-badge")).toHaveTextContent("5"),
    );
    expect(within(tabs).getByTestId("cockpit-phone-factory-badge")).toHaveTextContent("3");
    expect(screen.queryByTestId("cockpit-phone-discovery")).toBeNull();
  });

  it("an area deep in the factory tree is the Factory's too", async () => {
    localStorage.setItem(COCKPIT_STORAGE_KEY, "v3");
    serve();
    renderAt("/n/fx:health");

    expect(await screen.findByTestId("v3-closed-alarms")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("cockpit-tree-factory")).toHaveAttribute("aria-current", "page"),
    );
  });

  it("r on Factory › Definition renames the levels; elsewhere r still talks in the chat", async () => {
    localStorage.setItem(COCKPIT_STORAGE_KEY, "v3");
    serve();
    const first = renderAt("/n/fx:definition");
    expect(await screen.findByTestId("v3-levels-block")).toBeInTheDocument();

    fireEvent.keyDown(document.body, { key: "r" });

    expect(await screen.findByTestId("v3-preview")).toHaveAttribute("data-label", "Rename levels");
    expect(screen.queryByTestId("v3-chat-full")).toBeNull();
    first.unmount();

    renderAt("/n/I-1");
    expect(await screen.findByTestId("v3-node-page")).toHaveAttribute("data-ref", "I-1");
    fireEvent.keyDown(document.body, { key: "r" });
    expect(await screen.findByTestId("v3-chat-full")).toBeInTheDocument();
    expect(screen.queryByTestId("v3-preview")).toBeNull();
  });
});

describe("cockpit v3 · the node chat in the shell", () => {
  it("replaces the Room drawer: the Room button opens the node's chat", async () => {
    serve();
    renderAt("?cockpit=v3");
    expect(await screen.findByTestId("v3-chat-pinned")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("cockpit-room"));

    const full = await screen.findByTestId("v3-chat-full");
    expect(screen.queryByTestId("room-drawer")).toBeNull();
    // Sample nodes have no threads: the chat is this factory's #factory, and says so.
    expect(within(full).getByTestId("room-thread")).toHaveAttribute("data-thread", "factory");
    expect(within(full).getByTestId("v3-chat-title")).toHaveAttribute(
      "title",
      expect.stringContaining("this factory’s #factory: the sample’s nodes have no threads"),
    );
    await waitFor(() => expect(location).toBe(`${BASE}?chat=1`));
  });

  it("sends an old ?room= link to the node the thread is about, with its chat open", async () => {
    serve({ choice: null });
    localStorage.setItem(COCKPIT_STORAGE_KEY, "v3");
    renderAt("?room=factory");

    expect(await screen.findByTestId("v3-chat-full")).toBeInTheDocument();
    await waitFor(() => expect(location).toBe(`${BASE}/n/product?chat=1`));
    expect(screen.queryByTestId("room-drawer")).toBeNull();
  });

  it("sends a project's ?room= link on sample data to the product's chat, never a missing page", async () => {
    serve({ choice: null });
    localStorage.setItem(COCKPIT_STORAGE_KEY, "v3");
    renderAt("?room=project:db-version");

    expect(await screen.findByTestId("v3-chat-full")).toBeInTheDocument();
    await waitFor(() => expect(location).toBe(`${BASE}/n/product?chat=1`));
    expect(screen.queryByTestId("v3-node-missing")).toBeNull();
  });
});
