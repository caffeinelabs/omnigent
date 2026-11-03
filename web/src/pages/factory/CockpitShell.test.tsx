import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LIVE_RUN_FACTORY_ID, preflightOk } from "@/components/factory/__fixtures__/liveRun";
import { resetFactoryControls } from "@/components/factory/controls/useFactoryControls";
import { definitionResponse } from "@/components/factory/insights/__fixtures__/learnFixtures";
import { notifyMe } from "@/components/factory/notify/__fixtures__/notifyFixtures";
import { round3Snapshot } from "@/components/factory/shell/__fixtures__/shellFixtures";
import { COCKPIT_STORAGE_KEY, resetCockpitChoiceCache } from "@/components/factory/shell/cockpitV2";
import type { CockpitChoice, FactorySnapshot } from "@/lib/factoryApi";
import { FactoryPage } from "./FactoryPage";
import { type FactoryServer, SNAPSHOT, json, serveFactory } from "./testServer";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({
  useSessionUpdatesConnected: () => true,
}));

const FID = LIVE_RUN_FACTORY_ID;
const BASE = `/factory/${FID}`;

function setWidth({ wide, phone = false }: { wide: boolean; phone?: boolean }) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: query.includes("min-width: 1280px")
        ? wide
        : query.includes("max-width: 767.98px")
          ? phone
          : false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

let location = "";
function LocationProbe() {
  const loc = useLocation();
  location = loc.pathname + loc.search + loc.hash;
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

const NOT_ON_ROSTER = { error: { code: "not_on_roster", message: "not on the roster" } };

/** The server offers the new cockpit (or not), and the person's saved choice. */
function serve({
  offered = true,
  choice = "v2",
  snapshot = round3Snapshot(),
  onRoster = true,
  summary,
  prefs,
}: {
  offered?: boolean;
  choice?: CockpitChoice | null;
  snapshot?: FactorySnapshot;
  onRoster?: boolean;
  /** Overrides the status-summary read (say, a failure). */
  summary?: () => Response | Promise<Response>;
  /** Overrides the saved-choice read (say, a slow one). */
  prefs?: () => Response | Promise<Response>;
} = {}) {
  saved = choice;
  server = serveFactory(authenticatedFetch, {
    snapshot: () => json(snapshot),
    overrides: {
      "GET /v1/factory/preflight": () => json({ ...preflightOk(), cockpit_v2: offered }),
      [`GET ${SNAPSHOT}/notify/me/ui`]:
        prefs ?? (() => (onRoster ? json({ cockpit: saved }) : json(NOT_ON_ROSTER, 409))),
      [`PUT ${SNAPSHOT}/notify/me/ui`]: async () => {
        if (!onRoster) return json(NOT_ON_ROSTER, 409);
        const last = server.calls.at(-1)!.body as { cockpit: CockpitChoice };
        saved = last.cockpit;
        return json({ cockpit: saved });
      },
      [`GET ${SNAPSHOT}/notify/me`]: () => json(notifyMe()),
      ...(summary ? { [`GET ${SNAPSHOT}/status-summary`]: summary } : {}),
      [`GET ${SNAPSHOT}/definition`]: () => {
        const body = definitionResponse();
        return json({
          ...body,
          effective: { ...body.effective, product: { name: "Sample app (sample)" } },
        });
      },
    },
  });
}

function puts(): unknown[] {
  return server.calls
    .filter((c) => c.method === "PUT" && c.path.endsWith("/notify/me/ui"))
    .map((c) => c.body);
}

/** A response the test answers later. */
function later(): { answer: (r: Response) => void; respond: () => Promise<Response> } {
  let answer: (r: Response) => void = () => undefined;
  const pending = new Promise<Response>((resolve) => {
    answer = resolve;
  });
  return { answer: (r) => answer(r), respond: () => pending };
}

function openMenu(trigger: HTMLElement) {
  fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false, pointerType: "mouse" });
}

beforeEach(() => {
  authenticatedFetch.mockReset();
  resetFactoryControls();
  setWidth({ wide: true });
  localStorage.clear();
  resetCockpitChoiceCache();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the new cockpit · the switch", () => {
  it("stays classic while the server does not offer it, whatever this browser remembers", async () => {
    localStorage.setItem(COCKPIT_STORAGE_KEY, "v2");
    serve({ offered: false });
    renderAt("");

    expect(await screen.findByTestId("factory-header")).toBeInTheDocument();
    expect(screen.queryByTestId("cockpit-topbar")).toBeNull();
    expect(screen.queryByTestId("factory-try-cockpit")).toBeNull();
    expect(puts()).toEqual([]);
  });

  it("offers Try the new cockpit, and keeps the choice as the person's preference", async () => {
    serve({ choice: null });
    renderAt("");

    fireEvent.click(await screen.findByTestId("factory-try-cockpit"));

    expect(await screen.findByTestId("cockpit-topbar")).toBeInTheDocument();
    expect(screen.queryByTestId("factory-header")).toBeNull();
    await waitFor(() => expect(puts()).toEqual([{ cockpit: "v2" }]));
    expect(localStorage.getItem(COCKPIT_STORAGE_KEY)).toBe("v2");
  });

  it("goes back to the classic cockpit from the avatar menu", async () => {
    serve();
    renderAt("/projects");

    openMenu(await screen.findByTestId("cockpit-you"));
    fireEvent.click(await screen.findByTestId("cockpit-you-classic"));

    expect(await screen.findByTestId("factory-header")).toBeInTheDocument();
    await waitFor(() => expect(puts()).toEqual([{ cockpit: "v1" }]));
    expect(location).toBe(`${BASE}/projects`);
  });

  it("follows the person's saved choice over this browser's", async () => {
    localStorage.setItem(COCKPIT_STORAGE_KEY, "v1");
    serve({ choice: "v2" });
    renderAt("");

    expect(await screen.findByTestId("cockpit-topbar")).toBeInTheDocument();
  });

  it("keeps the choice in this browser for someone the server cannot store it for", async () => {
    localStorage.setItem(COCKPIT_STORAGE_KEY, "v2");
    serve({ onRoster: false });
    renderAt("");

    expect(await screen.findByTestId("cockpit-topbar")).toBeInTheDocument();
    expect(puts()).toEqual([]);
  });

  it("keeps a switch made before the saved choice answers, and saves it", async () => {
    localStorage.setItem(COCKPIT_STORAGE_KEY, "v1");
    const slow = later();
    serve({ choice: "v1", prefs: slow.respond });
    renderAt("/projects");

    // The classic page shows from this browser's copy while the read is out.
    fireEvent.click(await screen.findByTestId("factory-try-cockpit"));
    expect(await screen.findByTestId("cockpit-topbar")).toBeInTheDocument();
    await waitFor(() => expect(puts()).toEqual([{ cockpit: "v2" }]));
    // The read that was already out answers with the old choice: the click still wins.
    slow.answer(json({ cockpit: "v1" }));
    await act(async () => {
      await new Promise<void>((resolve) => {
        setTimeout(resolve, 50);
      });
    });
    expect(screen.getByTestId("cockpit-topbar")).toBeInTheDocument();
    expect(saved).toBe("v2");
  });

  it("ignores a ?cockpit= link while the server does not offer the new cockpit", async () => {
    serve({ offered: false, choice: null });
    renderAt("/projects?cockpit=v2");

    expect(await screen.findByTestId("factory-header")).toBeInTheDocument();
    expect(localStorage.getItem(COCKPIT_STORAGE_KEY)).toBeNull();
    expect(puts()).toEqual([]);
  });

  it("keeps the classic page up for someone the server cannot store a choice for", async () => {
    serve({ choice: null, onRoster: false });
    renderAt("/projects");

    expect(await screen.findByTestId("factory-try-cockpit")).toBeInTheDocument();
    await act(async () => {
      await new Promise<void>((resolve) => {
        setTimeout(resolve, 300);
      });
    });
    // The refused preference is asked for a few times at most, never in a loop.
    const asked = server.calls.filter(
      (c) => c.method === "GET" && c.path.endsWith("/notify/me/ui"),
    );
    expect(asked.length).toBeLessThanOrEqual(3);
    expect(screen.getByTestId("factory-header")).toBeInTheDocument();
  });

  it("takes a ?cockpit= link, then drops it from the address", async () => {
    serve({ choice: null });
    renderAt("/projects?cockpit=v2");

    expect(await screen.findByTestId("cockpit-topbar")).toBeInTheDocument();
    await waitFor(() => expect(location).toBe(`${BASE}/projects`));
    await waitFor(() => expect(puts()).toEqual([{ cockpit: "v2" }]));
  });
});

describe("the new cockpit · the shell", () => {
  it("frames the page with one top bar: switcher, verdict, five places, search, Room, you", async () => {
    serve();
    renderAt("");

    const bar = await screen.findByTestId("cockpit-topbar");
    expect(within(bar).getByTestId("cockpit-switcher")).toHaveTextContent("Atlas factory");
    expect(await within(bar).findByTestId("cockpit-switcher-product")).toHaveTextContent(
      "building Sample app (sample)",
    );
    const tabs = within(bar).getByTestId("cockpit-tabs");
    expect(
      within(tabs)
        .getAllByRole("link")
        .map((a) => a.textContent?.replace(/\d+$/, "")),
    ).toEqual(["Home", "Discovery", "Projects", "Teams", "Steer"]);
    expect(within(tabs).getByTestId("cockpit-tab-home")).toHaveAttribute("aria-current", "page");
    // Round 3 waits on the creator: Home carries the one amber count.
    expect(await within(tabs).findByTestId("cockpit-tab-home-count")).toHaveTextContent(/\d+/);
    // Steer counts only changes to how the factory works (C-15), not the plan C-4 in Needs you.
    expect(within(tabs).getByTestId("cockpit-tab-steer-count")).toHaveTextContent("1");
    const verdict = await within(bar).findByTestId("cockpit-verdict");
    await waitFor(() => expect(verdict).toHaveAttribute("data-dot", "red"));
    // The served summary is the server's builder over the page scenario (fixture-built).
    expect(verdict).toHaveTextContent("Running, 1 lead stopped and 1 critical incident");
    expect(within(bar).getByTestId("cockpit-search")).toBeInTheDocument();
    expect(within(bar).getByTestId("cockpit-room")).toBeInTheDocument();
    // No dock, no second row of facts, no classic tabs.
    expect(screen.queryByTestId("room-dock-column")).toBeNull();
    expect(screen.queryByTestId("factory-tabs")).toBeNull();
    expect(screen.queryByTestId("factory-live-dot")).toBeNull();
    expect(screen.getByTestId("factory-shell")).toHaveAttribute("data-cockpit", "v2");
    // The switch reads the small preference, never the Slack view (it pages the DM ledger).
    const reads = server.calls.filter((c) => c.method === "GET").map((c) => c.path.split("?")[0]);
    expect(reads).toContain(`${SNAPSHOT}/notify/me/ui`);
    expect(reads).not.toContain(`${SNAPSHOT}/notify/me`);
  });

  it("never says green while the server's verdict is out and the snapshot shows a stopped project", async () => {
    const snapshot = round3Snapshot();
    expect(snapshot.projects.some((p) => p.overlay === "failed")).toBe(true);
    serve({
      snapshot,
      summary: () => json({ error: { code: "internal", message: "boom" } }, 500),
    });
    renderAt("");

    const verdict = await screen.findByTestId("cockpit-verdict");
    await waitFor(() =>
      expect(server.calls.some((c) => c.path.endsWith("/status-summary"))).toBe(true),
    );
    await act(async () => {
      await new Promise<void>((resolve) => {
        setTimeout(resolve, 50);
      });
    });
    expect(verdict).not.toHaveAttribute("data-dot", "green");
    expect(verdict).toHaveAttribute("data-dot", "red");
    expect(verdict).toHaveTextContent(/stopped/);
  });

  it("says the health is unknown, in grey, when the verdict fails for a calm factory", async () => {
    const snapshot = round3Snapshot();
    snapshot.projects = snapshot.projects.map((p) => ({ ...p, overlay: null }));
    snapshot.members = snapshot.members.map((m) => ({ ...m, health: null, status: "idle" }));
    serve({
      snapshot,
      summary: () => json({ error: { code: "internal", message: "boom" } }, 500),
    });
    renderAt("");

    const verdict = await screen.findByTestId("cockpit-verdict");
    // Grey "Checking health…" while the read (and its one retry) is out, then unknown.
    expect(verdict).toHaveAttribute("data-dot", "grey");
    await waitFor(() => expect(verdict).toHaveTextContent("Health unknown"), { timeout: 4000 });
    expect(verdict).toHaveAttribute("data-dot", "grey");
  });

  it("opens the Health panel from the verdict, with why and the machine facts", async () => {
    serve();
    renderAt("");

    fireEvent.click(await screen.findByTestId("cockpit-verdict"));

    expect(location).toContain("peek=health");
    const peek = await screen.findByTestId("health-peek");
    expect(within(peek).getByTestId("health-reasons")).toHaveTextContent("cache-warm");
    expect(within(peek).getByTestId("health-facts")).toHaveTextContent("Updates");
    expect(within(peek).queryByTestId("health-fact-config")).toBeNull();
    expect(screen.getByTestId("factory-live-dot")).toBeInTheDocument();
    fireEvent.keyDown(peek, { key: "Escape" });
    await waitFor(() => expect(location).not.toContain("peek="));
  });

  it("names a deployment config problem among the machine facts", async () => {
    const snapshot = round3Snapshot();
    snapshot.health.config = [
      "no always-deny repos configured (OMNIGENT_FACTORY_ALWAYS_DENY_REPOS)",
    ];
    serve({ snapshot });
    renderAt("");

    fireEvent.click(await screen.findByTestId("cockpit-verdict"));

    const peek = await screen.findByTestId("health-peek");
    expect(within(peek).getByTestId("health-fact-config")).toHaveTextContent(
      "no always-deny repos configured (OMNIGENT_FACTORY_ALWAYS_DENY_REPOS)",
    );
  });

  it("keeps the Room as a drawer: the button opens it, the address names it, Esc closes it", async () => {
    serve();
    renderAt("/projects");

    fireEvent.click(await screen.findByTestId("cockpit-room"));

    const drawer = await screen.findByTestId("room-drawer");
    expect(location).toBe(`${BASE}/projects?room=`);
    expect(drawer).toHaveAttribute("data-thread", "factory");
    fireEvent.keyDown(drawer, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("room-drawer")).toBeNull());
    expect(location).toBe(`${BASE}/projects`);
  });

  it("opens an old Room link as the drawer over Home", async () => {
    serve();
    renderAt("/room/factory");

    const drawer = await screen.findByTestId("room-drawer");
    expect(drawer).toHaveAttribute("data-thread", "factory");
    expect(location).toBe(`${BASE}?room=factory`);
    expect(screen.getByTestId("factory-shell")).toHaveAttribute("data-page", "overview");
  });

  it("reaches Debug from the avatar menu, with today's views one level down", async () => {
    serve();
    renderAt("");

    openMenu(await screen.findByTestId("cockpit-you"));
    fireEvent.click(await screen.findByTestId("cockpit-you-debug"));

    const debug = await screen.findByTestId("factory-debug-page");
    expect(location).toBe(`${BASE}/debug/fleet`);
    expect(within(debug).getByTestId("debug-tab-fleet")).toHaveAttribute("aria-current", "page");
    fireEvent.click(within(debug).getByTestId("debug-tab-timeline"));
    await waitFor(() => expect(location).toBe(`${BASE}/debug/timeline`));
    expect(await screen.findByTestId("factory-debug-page")).toHaveAttribute(
      "data-view",
      "timeline",
    );
  });

  it.each([
    ["/timeline", `${BASE}/debug/timeline`, "factory-debug-page"],
    ["/alerts", `${BASE}/debug/incidents`, "factory-debug-page"],
    ["/agents", `${BASE}/teams/agents`, "factory-teams-page"],
    ["/settings/budgets", `${BASE}/steer/budgets`, "factory-steer-page"],
    ["/improvements?tab=history", `${BASE}/steer/versions?tab=history`, "factory-steer-page"],
  ])("sends the classic %s to %s", async (from, to, page) => {
    serve();
    renderAt(from);

    await waitFor(() => expect(location).toBe(to));
    expect(await screen.findByTestId(page)).toBeInTheDocument();
  });

  it("opens Decisions as the Needs you panel over Home", async () => {
    serve();
    renderAt("/decisions?decision=D-2");

    await waitFor(() => expect(location).toBe(`${BASE}?peek=needs&decision=D-2`));
    expect(await screen.findByTestId("peek")).toHaveTextContent("Needs you");
  });

  it("keeps Settings personal: people, notifications and integrations", async () => {
    serve();
    renderAt("/settings/people");

    const nav = await screen.findByTestId("settings-nav");
    expect(
      within(nav)
        .getAllByRole("link")
        .map((a) => a.textContent),
    ).toEqual(["People", "Notifications", "Integrations"]);
  });

  it("says Discovery is off when the factory has not turned it on", async () => {
    serve();
    renderAt("/discovery");

    expect(await screen.findByTestId("discovery-off")).toBeInTheDocument();
    expect(screen.getByTestId("cockpit-tab-discovery")).toHaveAttribute("aria-current", "page");
  });

  it("shows a viewer the viewer banner", async () => {
    serve({ snapshot: round3Snapshot("viewer") });
    renderAt("");

    expect(await screen.findByTestId("factory-viewer-banner")).toBeInTheDocument();
  });

  it("has no axe violations in the top bar, the Health panel or the Room drawer", async () => {
    serve();
    renderAt("?room=factory");
    await screen.findByTestId("room-drawer");
    const options: axe.RunOptions = {
      // jsdom paints nothing (contrast is checked in the browser over Storybook).
      rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
    };
    const bar = await axe.run(screen.getByTestId("cockpit-topbar"), options);
    expect(bar.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
    const drawer = await axe.run(screen.getByTestId("room-drawer"), options);
    expect(drawer.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
  });

  it("opens the palette on / and moves between places with g chords, r and ?", async () => {
    serve();
    renderAt("/projects");
    await screen.findByTestId("cockpit-topbar");

    fireEvent.keyDown(window, { key: "/" });
    const palette = await screen.findByTestId("factory-palette");
    expect(palette).toHaveAttribute("data-cockpit", "v2");
    fireEvent.keyDown(palette, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("factory-palette")).toBeNull());

    fireEvent.keyDown(window, { key: "g" });
    fireEvent.keyDown(window, { key: "t" });
    await waitFor(() => expect(location).toBe(`${BASE}/teams`));

    fireEvent.keyDown(window, { key: "r" });
    expect(await screen.findByTestId("room-drawer")).toBeInTheDocument();
    expect(location).toBe(`${BASE}/teams?room=`);
    fireEvent.keyDown(screen.getByTestId("room-drawer"), { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("room-drawer")).toBeNull());

    fireEvent.keyDown(window, { key: "?", shiftKey: true });
    expect(await screen.findByTestId("cockpit-keys")).toHaveTextContent("g h · g d");
  });

  it("puts the five places in a bottom bar on a phone", async () => {
    setWidth({ wide: false, phone: true });
    serve();
    renderAt("/projects");

    const bar = await screen.findByTestId("cockpit-phone-tabs");
    expect(within(bar).getAllByRole("link")).toHaveLength(5);
    expect(within(bar).getByTestId("cockpit-phone-projects")).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.queryByTestId("cockpit-tabs")).toBeNull();
    await act(async () => undefined);
  });
});
