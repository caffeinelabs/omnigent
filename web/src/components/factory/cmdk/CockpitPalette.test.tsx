import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DefinitionResponse, FactorySnapshot } from "@/lib/factoryApi";
import type { SearchResponse } from "@/lib/factoryPaletteApi";
import { definitionResponse } from "../insights/__fixtures__/learnFixtures";
import { TEAM_SLUG, teamCatalog, teamSnapshot } from "../team/__fixtures__/teamFixtures";
import { readTeamStaging, resetTeamStaging } from "../team/teamStaging";
import { FactoryViewerProvider } from "../shell/viewerCan";
import { FactoryToastProvider } from "../ui/Toast";
import { CockpitPalette } from "./CockpitPalette";
import { SEARCH_DEBOUNCE_MS } from "./paletteQueries";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({
  useSessionUpdatesConnected: () => true,
}));

const SNAPSHOT = teamSnapshot();
const FID = SNAPSHOT.factory.id;
const BASE = `/v1/factory/factories/${FID}`;

interface Call {
  method: string;
  path: string;
  body: unknown;
}

let calls: Call[] = [];
let routes: Record<string, (path: string, body: unknown) => Response> = {};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function definition(discovery: boolean): DefinitionResponse {
  const base = definitionResponse();
  return {
    ...base,
    discovery_server: true,
    effective: {
      ...base.effective,
      discovery: {
        ...base.effective.discovery!,
        enabled: discovery,
        budgets: { usd_per_day: 10, per_ask: 2 },
      },
    },
  };
}

function search(q: string, results: SearchResponse["results"]): SearchResponse {
  return { q, discovery: true, results, total: results.length };
}

function serve({ discovery = true }: { discovery?: boolean } = {}) {
  calls = [];
  routes = {
    [`GET ${BASE}/definition`]: () => json(definition(discovery)),
    [`GET ${BASE}/asks`]: () =>
      json({
        factory_id: FID,
        generated_at: 0,
        asks: [
          {
            id: "D-2",
            kind: "decision",
            subject: "decision:D-2",
            ref: "D-2",
            title: "Pick the first file format (sample)",
            meta: [],
            since: 0,
            age_s: 60,
            cost_of_waiting: { score: 1, level: "low", text: "", holds: 0 },
            primary_action: { kind: "open", label: "Open" },
            allow_text: false,
            items: [],
            more_count: 0,
            assignee: "you",
            mine: true,
            folded: [],
            covers: [],
            critical: false,
            project: TEAM_SLUG,
            href: `/factory/${FID}/projects/${TEAM_SLUG}?decision=D-2`,
          },
        ],
        handled: [],
        counts: { total: 1, mine: 1, others: 0, in_steer: 0, folded: 0 },
      }),
    [`GET ${BASE}/chat/threads`]: () => json({ data: [] }),
    [`GET ${BASE}/opportunities`]: () =>
      json({
        data: [{ ref: "O-7", title: "Use my own domain (sample)", band: "now", status: "open" }],
      }),
    [`GET ${BASE}/catalog`]: () => json(teamCatalog()),
    [`GET ${BASE}/search`]: (path) => {
      const q = new URL(path, "http://x").searchParams.get("q") ?? "";
      return json(search(q, []));
    },
  };
  authenticatedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ method, path, body });
    const handler = routes[`${method} ${path.split("?")[0]}`];
    if (!handler) throw new Error(`unexpected ${method} ${path}`);
    return handler(path, body);
  });
}

let location = "";
function LocationProbe() {
  const loc = useLocation();
  location = loc.pathname + loc.search;
  return null;
}

const onRoom = vi.fn();

function renderPalette(snapshot: FactorySnapshot = SNAPSHOT) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/factory/${FID}`]}>
        <Routes>
          <Route
            path="*"
            element={
              <FactoryViewerProvider viewer={snapshot.viewer}>
                <FactoryToastProvider>
                  <CockpitPalette
                    snapshot={snapshot}
                    open
                    onOpenChange={() => undefined}
                    onShare={() => undefined}
                    onAddProject={() => undefined}
                    onRoom={onRoom}
                  />
                  <LocationProbe />
                </FactoryToastProvider>
              </FactoryViewerProvider>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function type(text: string) {
  const input = await screen.findByTestId("factory-palette-input");
  fireEvent.change(input, { target: { value: text } });
  return input;
}

/** The rows' text, in order. */
function rows(): string[] {
  const list = screen.getByTestId("factory-palette-list");
  return within(list)
    .queryAllByRole("option")
    .map((row) => row.textContent ?? "");
}

beforeEach(() => {
  authenticatedFetch.mockReset();
  onRoom.mockReset();
  resetTeamStaging();
  location = "";
});
afterEach(() => {
  vi.useRealTimers();
});

describe("the new cockpit's palette", () => {
  it("opens on Needs you, the four verbs, the top opportunities and the places", async () => {
    serve();
    renderPalette();

    await screen.findByText("Pick the first file format (sample)");
    await screen.findByText("Use my own domain (sample)");
    const groups = screen.getAllByTestId(/^factory-palette-group-/).map((g) => g.dataset.testid);
    expect(groups).toEqual([
      "factory-palette-group-needs",
      "factory-palette-group-actions",
      "factory-palette-group-opportunities",
      "factory-palette-group-places",
    ]);
    const actions = within(screen.getByTestId("factory-palette-group-actions"))
      .getAllByRole("option")
      .map((row) => row.textContent);
    expect(actions.map((t) => t?.split(/add a|a research|change|light/)[0])).toEqual([
      "hunch: …",
      "ask scout: …",
      "swap … to …",
      "Toggle theme",
    ]);
  });

  it("hides discovery's verbs while discovery is off", async () => {
    serve({ discovery: false });
    renderPalette();

    await screen.findByText("Pick the first file format (sample)");
    await waitFor(() => expect(rows().some((r) => r.startsWith("swap … to …"))).toBe(true));
    expect(rows().some((r) => r.startsWith("hunch:"))).toBe(false);
    expect(screen.queryByTestId("factory-palette-group-opportunities")).toBeNull();
  });

  it("stages a swap in the team editor and never applies it", async () => {
    serve();
    renderPalette();
    await screen.findByText("Pick the first file format (sample)");

    await type("swap rex to claude · opus");
    const row = await screen.findByText("Swap Rex to claude · opus");
    fireEvent.click(row);

    await waitFor(() => expect(location).toBe(`/factory/${FID}/projects/${TEAM_SLUG}/team`));
    expect(readTeamStaging(FID, TEAM_SLUG).changes).toEqual([
      { kind: "model", member: "M-3", vendor: "claude", model: "opus" },
    ]);
    // Staged only: no definition write, no preview, nothing posted.
    expect(calls.filter((c) => c.method !== "GET")).toEqual([]);
    expect(calls.some((c) => c.path.includes("/definition") && c.method !== "GET")).toBe(false);
  });

  it("finds through the server, titles first, and falls back to the snapshot without it", async () => {
    serve();
    routes[`GET ${BASE}/search`] = (path) => {
      const q = new URL(path, "http://x").searchParams.get("q") ?? "";
      return json(
        search(q, [
          {
            kind: "opportunity",
            ref: "O-7",
            title: "Use my own custom domain (sample)",
            detail: "G-1 · open",
            score: 3,
          },
          {
            kind: "setting",
            ref: "role_defaults.ic.model",
            title: "role_defaults.ic.model",
            detail: "role_defaults",
            score: 1,
            path: "role_defaults.ic.model",
            alias: null,
          },
        ]),
      );
    };
    renderPalette();
    await screen.findByText("Pick the first file format (sample)");

    await type("custom domain");

    await screen.findByText("Use my own custom domain (sample)");
    const groups = screen.getAllByTestId(/^factory-palette-group-/).map((g) => g.dataset.testid);
    expect(groups[0]).toBe("factory-palette-group-opportunities");
    expect(screen.getByText("Models · Implementers")).toBeInTheDocument();
    expect(calls.some((c) => c.path === `${BASE}/search?q=custom+domain`)).toBe(true);
  });

  it("searches the snapshot on a server without the search route", async () => {
    serve();
    routes[`GET ${BASE}/search`] = () => json({ error: { message: "Not Found" } }, 404);
    renderPalette();
    await screen.findByText("Pick the first file format (sample)");

    await type(`#${TEAM_SLUG}`);

    await waitFor(() =>
      expect(screen.getByTestId("factory-palette-group-projects")).toBeInTheDocument(),
    );
    await waitFor(() => expect(screen.queryByTestId("factory-palette-searching")).toBeNull());
  });

  it("offers only Ask Atlas when nothing matches, and posts it to #factory", async () => {
    serve();
    routes[`POST ${BASE}/chat`] = () => json({ message: { id: "m1" }, route: {} });
    renderPalette();
    await screen.findByText("Pick the first file format (sample)");

    await type("zzz nothing here (sample)");
    const atlas = await screen.findByText("Ask Atlas: “zzz nothing here (sample)”");
    expect(rows()).toHaveLength(1);
    fireEvent.click(atlas);

    await waitFor(() => expect(onRoom).toHaveBeenCalledWith("factory"));
    expect(calls.find((c) => c.method === "POST")).toEqual({
      method: "POST",
      path: `${BASE}/chat`,
      body: { thread: "factory", body: "zzz nothing here (sample)" },
    });
  });

  it("tells a viewer who can run a verb instead of offering it", async () => {
    serve();
    renderPalette(teamSnapshot("standard", { viewer: "viewer" }));
    await screen.findByText("Pick the first file format (sample)");

    await type("swap rex to claude");

    expect(await screen.findByTestId("factory-palette-empty")).toHaveTextContent(
      "Nothing matches “swap rex to claude”. Ask an operator to change this.",
    );
    expect(rows()).toEqual([]);
  });

  it("adds a hunch through discovery's route, with Undo withdrawing it", async () => {
    serve();
    routes[`POST ${BASE}/hunches`] = () => json({ signal: { id: "S-9" } }, 201);
    routes[`DELETE ${BASE}/hunches/S-9`] = () => json({ signal: { id: "S-9" } });
    renderPalette();
    await screen.findByText("Pick the first file format (sample)");

    await type("hunch: people share drafts first (sample)");
    fireEvent.click(await screen.findByText("Add hunch: “people share drafts first (sample)”"));

    const toast = await screen.findByTestId("factory-toast");
    expect(toast).toHaveTextContent("Hunch added with weight 0.1");
    fireEvent.click(within(toast).getByRole("button", { name: "Undo" }));
    await waitFor(() =>
      expect(calls.some((c) => c.method === "DELETE" && c.path === `${BASE}/hunches/S-9`)).toBe(
        true,
      ),
    );
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      text: "people share drafts first (sample)",
    });
  });

  it("is a combobox over a listbox with no axe violations, empty and typed", async () => {
    serve();
    renderPalette();
    await screen.findByText("Pick the first file format (sample)");
    const options: axe.RunOptions = {
      rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
    };
    const palette = screen.getByTestId("factory-palette");
    expect((await axe.run(palette, options)).violations.map((v) => v.id)).toEqual([]);

    const input = await type("swap rex to claude");
    await screen.findByText("Swap Rex to claude · opus");
    expect(input).toHaveAttribute("role", "combobox");
    fireEvent.keyDown(input, { key: "ArrowDown" });
    await waitFor(() => expect(input).toHaveAttribute("aria-activedescendant"));
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    expect((await axe.run(palette, options)).violations.map((v) => v.id)).toEqual([]);
  });

  it("debounces the search while someone types", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    serve();
    renderPalette();
    await screen.findByText("Pick the first file format (sample)");

    await type("c");
    await type("cu");
    await type("cus");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SEARCH_DEBOUNCE_MS + 50);
    });

    await waitFor(() =>
      expect(calls.filter((c) => c.path.startsWith(`${BASE}/search`)).map((c) => c.path)).toEqual([
        `${BASE}/search?q=cus`,
      ]),
    );
  });
});
