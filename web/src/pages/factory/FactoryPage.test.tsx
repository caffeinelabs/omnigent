import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  LIVE_RUN_FACTORY_ID,
  liveRunSnapshot,
  pendingForever,
} from "@/components/factory/__fixtures__/liveRun";
import { resetFactoryControls } from "@/components/factory/controls/useFactoryControls";
import { round3Snapshot } from "@/components/factory/shell/__fixtures__/shellFixtures";
import { factoryQueryKey } from "@/hooks/useFactory";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { isMacPlatform } from "@/lib/hotkeys";
import { FactoryPage } from "./FactoryPage";
import { type FactoryServer, SNAPSHOT, json, serveFactory } from "./testServer";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
const connected = vi.hoisted(() => ({ value: true }));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({
  useSessionUpdatesConnected: () => connected.value,
}));

const FID = LIVE_RUN_FACTORY_ID;
const PR_9630 = "f74d5967c53455139e85ba1b402a5bd4";

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

function renderAt(path: string, seed?: (client: QueryClient) => void) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  seed?.(client);
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/factory/${FID}${path}`]}>
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
  return { client, ...view };
}

let server: FactoryServer;
function serve(snapshot: () => FactorySnapshot | Response | Promise<Response> = liveRunSnapshot) {
  server = serveFactory(authenticatedFetch, {
    snapshot: async () => {
      const body = await snapshot();
      return body instanceof Response ? body : json(body);
    },
  });
}

beforeEach(() => {
  authenticatedFetch.mockReset();
  connected.value = true;
  resetFactoryControls();
  setWidth({ wide: true });
  localStorage.clear();
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("FactoryPage · shell", () => {
  it("shows the shell skeleton, never an empty page, while the first read is pending", () => {
    serve(() => pendingForever());
    renderAt("");
    expect(screen.getByTestId("factory-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("factory-needs-you")).toBeNull();
  });

  it("frames the Overview with the header, tabs and the Room dock", async () => {
    serve();
    renderAt("");
    const header = await screen.findByTestId("factory-header");
    expect(header).toHaveTextContent("Atlas factory");
    expect(screen.getByTestId("factory-live-dot")).toHaveAttribute("data-state", "live");
    expect(screen.getByTestId("factory-tab-overview")).toHaveAttribute("aria-current", "page");
    expect(screen.getByTestId("factory-needs-you")).toBeInTheDocument();
    expect(screen.getByTestId("projects-table")).toBeInTheDocument();
    expect(screen.getByTestId("overview-heatmap")).toBeInTheDocument();
    expect(screen.getByTestId("overview-live-feed")).toBeInTheDocument();
    expect(screen.getByTestId("factory-health-strip")).toBeInTheDocument();
    const dock = screen.getByTestId("room-dock");
    expect(dock).toHaveAttribute("data-thread", "factory");
    expect(await within(dock).findByTestId("room-message-1")).toHaveTextContent(
      "Show the deployed versions in Settings",
    );
    fireEvent.mouseDown(within(dock).getByTestId("room-dock-tab-decisions"));
    expect(await within(dock).findByTestId("decision-card-D-2")).toBeInTheDocument();
  });

  it("shows the base, engine mode and definition version only when the server sends them", async () => {
    serve(() => round3Snapshot());
    renderAt("");
    await screen.findByTestId("factory-header");
    expect(screen.getByTestId("factory-engine-mode")).toHaveTextContent("assist");
    expect(screen.getByTestId("factory-def-version")).toHaveTextContent("def v7");
    expect(screen.getByTestId("factory-base-line")).toHaveTextContent("app#temp-main@57ab671");
    expect(screen.getByTestId("factory-base-line")).toHaveAttribute("data-env", "ready");
    expect(screen.getByTestId("factory-presence")).toHaveAttribute("data-count", "2");
  });

  it("leaves the chips out on an older server", async () => {
    serve();
    renderAt("");
    await screen.findByTestId("factory-header");
    expect(screen.queryByTestId("factory-engine-mode")).toBeNull();
    expect(screen.queryByTestId("factory-def-version")).toBeNull();
    expect(screen.queryByTestId("factory-base-line")).toBeNull();
  });

  it("drops the dock below 1280 px", async () => {
    setWidth({ wide: false });
    serve();
    renderAt("");
    await screen.findByTestId("factory-needs-you");
    expect(screen.getByTestId("factory-layout")).toHaveAttribute("data-wide", "false");
    expect(screen.queryByTestId("room-dock")).toBeNull();
  });

  it("swaps the top tabs for the bottom tabs on a phone", async () => {
    setWidth({ wide: false, phone: true });
    serve();
    renderAt("");
    await screen.findByTestId("factory-needs-you");
    expect(screen.queryByTestId("factory-tabs")).toBeNull();
    const bar = screen.getByTestId("factory-phone-tabs");
    expect(within(bar).getByTestId("factory-phone-room")).toHaveTextContent("Room");
    expect(within(bar).getByTestId("factory-phone-needs-you")).toHaveTextContent("Needs you");
    expect(within(bar).getByTestId("factory-phone-projects")).toHaveTextContent("Projects");
    expect(within(bar).getByTestId("factory-phone-more")).toHaveTextContent("More");
  });

  it("keeps stale data under an error alert when a refresh fails, and retries", async () => {
    let fail = true;
    serve(() => (fail ? json({ error: { message: "down" } }, 503) : liveRunSnapshot()));
    const { client } = renderAt("", (c) =>
      c.setQueryData(factoryQueryKey(FID), liveRunSnapshot(), {
        updatedAt: new Date(2026, 9, 26, 14, 2).getTime(),
      }),
    );
    await act(async () => {
      await client.invalidateQueries({ queryKey: factoryQueryKey(FID) });
    });
    const alert = await screen.findByTestId("factory-load-error");
    expect(alert).toHaveTextContent("Couldn’t refresh this factory");
    expect(screen.getByTestId("factory-needs-you")).toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByTestId("factory-load-error-retry"));
    await waitFor(() => expect(screen.queryByTestId("factory-load-error")).toBeNull());
  });

  it("says a removed person no longer has access", async () => {
    serve(() => json({ error: { message: "not found" } }, 404));
    renderAt("");
    expect(await screen.findByTestId("factory-missing")).toHaveTextContent(
      "you no longer have access",
    );
  });
});

describe("FactoryPage · routes and old links", () => {
  it.each([
    ["?project=api-version", `/factory/${FID}/projects/api-version`],
    ["?project=db-version&decision=D-2", `/factory/${FID}/projects/db-version?decision=D-2`],
    ["?review=9630", `/factory/${FID}/prs/${PR_9630}`],
    [
      `?review=9630&pr=${PR_9630}&preview=unavailable`,
      `/factory/${FID}/prs/${PR_9630}?preview=unavailable`,
    ],
    [`?pr=${PR_9630}&preview=unavailable`, `/factory/${FID}/prs/${PR_9630}?preview=unavailable`],
    ["?project=api-version&review=9630", `/factory/${FID}/prs/${PR_9630}`],
    ["?tab=activity", `/factory/${FID}/activity`],
    ["?tab=board", `/factory/${FID}`],
    ["?tab=board&decision=D-2", `/factory/${FID}?decision=D-2`],
    ["/projects/api-version?tab=prs", `/factory/${FID}/projects/api-version#prs`],
    ["/projects/api-version?review=9630", `/factory/${FID}/prs/${PR_9630}`],
  ])("sends %s to %s", async (from, to) => {
    serve();
    renderAt(from);
    await waitFor(() => expect(location).toBe(to));
    expect(await screen.findByTestId("factory-shell")).toBeInTheDocument();
  });

  it("lands an old ?review= link from a PR body on the PR page", async () => {
    serve();
    renderAt("?review=9630");
    const page = await screen.findByTestId("pr-page");
    expect(page).toHaveAttribute("data-pr", "9630");
    expect(page).toHaveTextContent("Ready for human merge");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("explains a stable preview link that landed on the PR page", async () => {
    serve(() => {
      const snapshot = liveRunSnapshot();
      const pr = snapshot.prs.find((p) => p.number === 9630)!;
      pr.preview = { ...pr.preview!, state: "parked" };
      return snapshot;
    });
    renderAt(`?review=9630&pr=${PR_9630}&preview=unavailable`);
    expect(await screen.findByTestId("review-preview-unavailable")).toHaveTextContent(
      "this PR’s factory preview isn’t running (parked)",
    );
  });

  it("names a PR number the factory doesn't track", async () => {
    serve();
    renderAt("?review=9999");
    await waitFor(() => expect(location).toBe(`/factory/${FID}/prs/9999`));
    expect(await screen.findByTestId("pr-missing")).toHaveTextContent("9999");
  });

  it("moves between pages from the tabs", async () => {
    serve();
    renderAt("");
    fireEvent.click(await screen.findByTestId("factory-tab-projects"));
    expect(await screen.findByTestId("factory-projects-page")).toBeInTheDocument();
    expect(location).toBe(`/factory/${FID}/projects`);
    fireEvent.click(screen.getByTestId("factory-tab-activity"));
    expect(await screen.findByTestId("activity-list")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("factory-tab-settings"));
    expect(await screen.findByTestId("people-settings")).toBeInTheDocument();
    expect(location).toBe(`/factory/${FID}/settings/people`);
    fireEvent.click(screen.getByTestId("factory-tab-room"));
    expect(await screen.findByTestId("factory-room-page")).toHaveAttribute(
      "data-thread",
      "factory",
    );
    // The Room page holds the thread itself: no dock beside it.
    expect(screen.queryByTestId("room-dock")).toBeNull();
    fireEvent.click(screen.getByTestId("factory-tab-graph"));
    expect(await screen.findByTestId("factory-graph-page")).toHaveAttribute("data-kind", "work");
    fireEvent.click(screen.getByTestId("factory-tab-decisions"));
    expect(await screen.findByTestId("factory-decisions-page")).toBeInTheDocument();
    expect(location).toBe(`/factory/${FID}/decisions`);
  });

  it("opens a project's page from its table row, never a drawer", async () => {
    serve();
    renderAt("");
    fireEvent.click(await screen.findByTestId("projects-row-link-api-version"));
    expect(await screen.findByTestId("project-page-api-version")).toBeInTheDocument();
    expect(location).toBe(`/factory/${FID}/projects/api-version`);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows the round-2 cards on Projects for a few projects, the table past four", async () => {
    serve();
    renderAt("/projects");
    expect(await screen.findByTestId("factory-projects-page")).toHaveAttribute(
      "data-view",
      "cards",
    );
    fireEvent.click(await screen.findByTestId("project-open-api-version"));
    expect(await screen.findByTestId("project-page-api-version")).toBeInTheDocument();
  });

  it("defaults Projects to the table for the round-3 factory", async () => {
    serve(() => round3Snapshot());
    renderAt("/projects");
    expect(await screen.findByTestId("factory-projects-page")).toHaveAttribute(
      "data-view",
      "table",
    );
    expect(screen.getByTestId("projects-envs")).toHaveTextContent("base-env");
    expect(screen.getByTestId("projects-closed")).toHaveTextContent("Closed (1)");
  });

  it("says when a factory path has no page", async () => {
    serve();
    renderAt("/nowhere");
    expect(await screen.findByTestId("factory-unknown-page")).toBeInTheDocument();
  });
});

describe("FactoryPage · presence, ⌘K and sharing", () => {
  it("beats presence with the page the viewer is on, and moves with them", async () => {
    serve();
    const { unmount } = renderAt("");
    await screen.findByTestId("factory-needs-you");
    await waitFor(() => expect(server.beats).toContainEqual({ location: "overview", idle: false }));
    fireEvent.click(screen.getByTestId("projects-row-link-db-version"));
    await waitFor(() =>
      expect(server.beats).toContainEqual({ location: "project:db-version", idle: false }),
    );
    unmount();
    await waitFor(() =>
      expect(server.beats).toContainEqual({ location: null, idle: false, leave: true }),
    );
  });

  it("opens the palette on ⌘K, jumps to a project and runs a slash command", async () => {
    serve();
    renderAt("");
    await screen.findByTestId("factory-needs-you");
    const mac = isMacPlatform();
    fireEvent.keyDown(window, { key: "k", metaKey: mac, ctrlKey: !mac });
    const input = await screen.findByTestId("factory-palette-input");
    fireEvent.change(input, { target: { value: "db-version" } });
    fireEvent.click(await screen.findByTestId("factory-palette-item-project:db-version"));
    expect(await screen.findByTestId("project-page-db-version")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("factory-palette-open"));
    fireEvent.change(await screen.findByTestId("factory-palette-input"), {
      target: { value: "/pause #db-version" },
    });
    fireEvent.click(await screen.findByTestId("factory-palette-run"));
    await waitFor(() =>
      expect(server.controls).toEqual([{ action: "pause", target: "project:db-version" }]),
    );
  });

  it("opens Share from the header and invites through the people API", async () => {
    serve(() => round3Snapshot());
    renderAt("");
    fireEvent.click(await screen.findByTestId("factory-share"));
    const dialog = await screen.findByTestId("share-dialog");
    expect(await within(dialog).findByTestId("people-row-mia@caffeine.ai")).toBeInTheDocument();
    expect(within(dialog).getByTestId("share-credential-line")).toHaveTextContent(
      "Agents act as luka@caffeine.ai’s GitHub & Databricks",
    );
  });

  it("disables every header lever for a viewer, with Request access", async () => {
    serve(() => round3Snapshot("viewer"));
    renderAt("");
    expect(await screen.findByTestId("factory-viewer-banner")).toBeInTheDocument();
    expect(screen.getByTestId("factory-add-project")).toBeDisabled();
    expect(screen.getByTestId("factory-pause-all")).toBeDisabled();
    expect(screen.getByTestId("factory-request-access")).toBeEnabled();
  });

  it("highlights ?decision in the dock", async () => {
    serve();
    renderAt("?decision=D-2");
    const dock = await screen.findByTestId("room-dock");
    expect(within(dock).getByTestId("decision-card-D-2")).toHaveAttribute(
      "data-highlighted",
      "true",
    );
  });

  it("reads the snapshot once for the whole shell", async () => {
    serve();
    renderAt("/projects/api-version");
    await screen.findByTestId("project-page-api-version");
    expect(server.calls.filter((c) => c.method === "GET" && c.path === SNAPSHOT)).toHaveLength(1);
  });
});

describe("FactoryPage · the Room dock", () => {
  it("follows the page: #slug on a project page, !n on a PR page, the last thread elsewhere", async () => {
    serve();
    renderAt("/projects/db-version");
    const dock = await screen.findByTestId("room-dock");
    expect(dock).toHaveAttribute("data-thread", "project:db-version");
    expect(within(dock).getByTestId("room-thread-title")).toHaveTextContent("#db-version");
    fireEvent.click(await screen.findByTestId("factory-tab-projects"));
    await waitFor(() =>
      expect(screen.getByTestId("room-dock")).toHaveAttribute("data-thread", "project:db-version"),
    );
    fireEvent.click(screen.getByTestId("factory-tab-overview"));
    await screen.findByTestId("factory-needs-you");
    // Off project and PR pages the dock keeps the last thread it showed.
    expect(screen.getByTestId("room-dock")).toHaveAttribute("data-thread", "project:db-version");
  });

  it("binds a PR page to its !n thread", async () => {
    serve();
    renderAt(`/prs/${PR_9630}`);
    const dock = await screen.findByTestId("room-dock");
    expect(dock).toHaveAttribute("data-thread", `pr:${PR_9630}`);
    expect(within(dock).getByTestId("room-thread-title")).toHaveTextContent("!9630");
  });

  it("keeps its width from the last drag", async () => {
    localStorage.setItem("omnigent.factory.roomDock.width", "420");
    serve();
    renderAt("");
    const layout = await screen.findByTestId("factory-layout");
    expect(layout.style.gridTemplateColumns).toBe("minmax(0,1fr) 420px");
    fireEvent.keyDown(screen.getByTestId("room-dock-resize"), { key: "ArrowLeft" });
    expect(layout.style.gridTemplateColumns).toBe("minmax(0,1fr) 436px");
    expect(localStorage.getItem("omnigent.factory.roomDock.width")).toBe("436");
  });

  it("is a bottom sheet with the unread count below 1280 px", async () => {
    setWidth({ wide: false });
    serve(() => round3Snapshot());
    renderAt("");
    const button = await screen.findByTestId("room-sheet-open");
    expect(await screen.findByTestId("room-sheet-unread")).toHaveTextContent("6");
    expect(screen.queryByTestId("room-dock")).toBeNull();
    fireEvent.click(button);
    expect(await screen.findByTestId("room-sheet")).toBeInTheDocument();
    expect(screen.getByTestId("room-dock")).toHaveAttribute("data-thread", "factory");
  });

  it("posts from the dock as the viewer", async () => {
    serve(() => round3Snapshot());
    renderAt("");
    const dock = await screen.findByTestId("room-dock");
    const input = await within(dock).findByTestId("room-composer-input");
    expect(within(dock).getByTestId("room-composer-identity")).toHaveTextContent(
      "Posting as Luka · owner",
    );
    fireEvent.change(input, {
      target: { value: "@atlas add a project for a /health badge on settings" },
    });
    fireEvent.click(within(dock).getByTestId("room-composer-send"));
    await waitFor(() =>
      expect(server.posts).toEqual([
        {
          thread: "factory",
          body: "@atlas add a project for a /health badge on settings",
          mentions: ["@atlas"],
        },
      ]),
    );
  });
});
