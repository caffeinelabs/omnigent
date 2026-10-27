import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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
    expect(strip).toHaveAttribute("href", `/factory/${LIVE_RUN_FACTORY_ID}/projects/api-version`);
    expect(strip).toHaveTextContent("Atlas factory");
    expect(strip).toHaveTextContent("api-version");
    const stage = within(strip).getByTestId("factory-strip-stage");
    expect(stage).toHaveAttribute("data-stage", "ready");
    expect(stage).toHaveTextContent("Ready");
    // Ready since 760 s before the fixture's now.
    expect(strip).toHaveTextContent("12m");
    expect(strip).toHaveTextContent("PR #9630");
    const dots = within(strip).getByTestId("factory-gate-dots");
    expect(dots.querySelectorAll("[data-gate]")).toHaveLength(7);
    expect(dots).toHaveAccessibleName(expect.stringMatching(/^PR #9630 gates: .*CI pass/));
    expect(within(strip).getByTestId("factory-strip-preview")).toHaveAttribute("data-tone", "pass");
    expect(strip).toHaveTextContent("$7.10");
    expect(strip).toHaveTextContent("Open cockpit");
    const mobile = screen.getByTestId("factory-strip-mobile");
    expect(mobile).toHaveAttribute("href", `/factory/${LIVE_RUN_FACTORY_ID}/projects/api-version`);
    // The same 44 px touch target as the header's other glass pills.
    expect(mobile).toHaveClass("size-11");
  });

  it("reads an IC sub-agent's factory from its root PL and shows the IC's PR", async () => {
    const pl = plSession();
    const ic = icSession();
    renderStrip(ic.id, [pl, ic]);

    const strip = await screen.findByTestId("factory-strip");
    await waitFor(() => expect(strip).toHaveAttribute("data-live", "true"));
    expect(strip).toHaveAttribute("href", `/factory/${LIVE_RUN_FACTORY_ID}/projects/api-version`);
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

  it("holds the name's place while the snapshot loads", async () => {
    authenticatedFetch.mockImplementation(() => pendingForever());
    const pl = plSession();
    renderStrip(pl.id, [pl]);

    const strip = await screen.findByTestId("factory-strip");
    expect(strip).toHaveAttribute("data-live", "false");
    expect(within(strip).getByTestId("factory-strip-name-loading")).toBeInTheDocument();
    expect(strip).not.toHaveTextContent("Factory");
    expect(strip).toHaveTextContent("api-version");
    expect(strip).toHaveTextContent("Open cockpit");
  });

  it("falls back to its labels when the snapshot is unavailable", async () => {
    authenticatedFetch.mockImplementation(
      async () =>
        new Response(JSON.stringify({ error: { message: "nope" } }), {
          status: 404,
          headers: { "Content-Type": "application/json" },
        }),
    );
    const pl = plSession();
    renderStrip(pl.id, [pl]);

    const strip = await screen.findByTestId("factory-strip");
    await waitFor(() => expect(strip).toHaveTextContent("Factory"));
    expect(strip).toHaveAttribute("data-live", "false");
    expect(within(strip).queryByTestId("factory-strip-name-loading")).toBeNull();
    expect(strip).toHaveTextContent("api-version");
  });

  it("links a legacy-prefixed factory id to the bare id the server keys", async () => {
    const pl = plSession();
    const legacy = {
      ...pl,
      labels: { ...pl.labels, "omnigent.factory.id": `conv_${LIVE_RUN_FACTORY_ID}` },
    };
    renderStrip(legacy.id, [legacy]);

    const strip = await screen.findByTestId("factory-strip");
    await waitFor(() => expect(strip).toHaveAttribute("data-live", "true"));
    expect(strip).toHaveAttribute("href", `/factory/${LIVE_RUN_FACTORY_ID}/projects/api-version`);
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

describe("FactoryStrip in a narrow header", () => {
  /** Lay the strip out: a slot of `slot` px, its parts at `widths`, fixed parts 20 px. */
  function mockLayout(slot: number, widths: Record<string, number>) {
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(function (
      this: HTMLElement,
    ) {
      return this.hasAttribute("data-factory-strip-slot") ? slot : 0;
    });
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
      this: HTMLElement,
    ) {
      const key = this.dataset.fitKey;
      const inStrip = this.parentElement?.dataset.testid === "factory-strip";
      const width = key ? (widths[key] ?? 0) : inStrip ? 20 : 0;
      return new DOMRect(0, 0, width, 28);
    });
  }

  afterEach(() => {
    vi.restoreAllMocks();
  });

  const PL_WIDTHS = {
    name: 90,
    project: 80,
    stage: 50,
    elapsed: 50,
    pr: 60,
    gates: 56,
    preview: 66,
    cost: 46,
    cta: 90,
  };

  function hiddenParts(strip: HTMLElement): string[] {
    return [...strip.querySelectorAll<HTMLElement>("[data-fit-hidden]")]
      .map((el) => el.dataset.fitKey ?? "")
      .sort();
  }

  it("drops cost, name and the CTA label before time, the PR gates and preview", async () => {
    mockLayout(430, PL_WIDTHS);
    const pl = plSession();
    renderStrip(pl.id, [pl]);

    const strip = await screen.findByTestId("factory-strip");
    await waitFor(() => expect(hiddenParts(strip)).toEqual(["cost", "cta", "name"]));
    expect(strip.querySelector("[data-fit-key=elapsed]")).not.toHaveAttribute("data-fit-hidden");
    // The separator after the name goes with it: no lone chevron before the project.
    const name = strip.querySelector<HTMLElement>("[data-fit-key=name]")!;
    expect(name.querySelector("svg")).not.toBeNull();
    expect(name).toHaveClass("hidden");
    expect(within(strip).getByTestId("factory-gate-dots")).toBeVisible();
    // The arrow into the cockpit is always there.
    expect(strip.lastElementChild?.querySelector("svg")).not.toBeNull();
    expect(strip.lastElementChild).not.toHaveAttribute("data-fit-key");
  });

  it("keeps the stage, gates and preview in the default chat layout", async () => {
    mockLayout(260, PL_WIDTHS);
    const pl = plSession();
    renderStrip(pl.id, [pl]);

    const strip = await screen.findByTestId("factory-strip");
    await waitFor(() =>
      expect(hiddenParts(strip)).toEqual(["cost", "cta", "elapsed", "name", "pr", "project"]),
    );
    expect(within(strip).getByTestId("factory-strip-stage")).not.toHaveAttribute("data-fit-hidden");
  });

  it("truncates the project before hiding it", async () => {
    // Without cost, name and the CTA label the parts need 422 px; 410 fits a 68 px project.
    mockLayout(410, PL_WIDTHS);
    const pl = plSession();
    renderStrip(pl.id, [pl]);

    const strip = await screen.findByTestId("factory-strip");
    const project = strip.querySelector<HTMLElement>("[data-fit-key=project]")!;
    await waitFor(() => expect(project.style.maxWidth).toBe("68px"));
    expect(project).not.toHaveAttribute("data-fit-hidden");
  });

  it("keeps a lead's open decisions longest", async () => {
    mockLayout(300, { name: 90, decisions: 80, ready: 120, projects: 75, cost: 50, cta: 90 });
    const atlas = atlasSession();
    renderStrip(atlas.id, [atlas]);

    const strip = await screen.findByTestId("factory-strip");
    await waitFor(() => expect(hiddenParts(strip)).toEqual(["cost", "cta", "name", "projects"]));
    expect(strip.querySelector("[data-fit-key=decisions]")).toHaveTextContent("2 decisions");
    // Decisions come first in reading order too.
    const order = [...strip.querySelectorAll<HTMLElement>("[data-fit-key]")].map(
      (el) => el.dataset.fitKey,
    );
    expect(order.indexOf("decisions")).toBeLessThan(order.indexOf("ready"));
    expect(order.indexOf("ready")).toBeLessThan(order.indexOf("projects"));
  });
});
