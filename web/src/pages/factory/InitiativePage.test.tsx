import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LIVE_RUN_FACTORY_ID, preflightOk } from "@/components/factory/__fixtures__/liveRun";
import { round3IssueList } from "@/components/factory/agents/__fixtures__/opsFixtures";
import { resetFactoryControls } from "@/components/factory/controls/useFactoryControls";
import { asksOf } from "@/components/factory/home/__fixtures__/asksFixtures";
import {
  sampleInitiativeLine,
  sampleInitiativeView,
} from "@/components/factory/initiatives/__fixtures__/initiativeFixtures";
import { definitionResponse } from "@/components/factory/insights/__fixtures__/learnFixtures";
import { notifyMe } from "@/components/factory/notify/__fixtures__/notifyFixtures";
import { roomProposals } from "@/components/factory/room/__fixtures__/roomFixtures";
import { round3Snapshot } from "@/components/factory/shell/__fixtures__/shellFixtures";
import { resetCockpitChoiceCache } from "@/components/factory/shell/cockpitV2";
import type { InitiativeView } from "@/lib/factoryInitiativesApi";
import { FactoryPage } from "./FactoryPage";
import { initiativeRef } from "./InitiativePage";
import { type FactoryServer, SNAPSHOT, json, serveFactory } from "./testServer";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({
  useSessionUpdatesConnected: () => true,
}));

const BASE = `/factory/${LIVE_RUN_FACTORY_ID}`;
const PAGE = `${SNAPSHOT}/initiatives/I-3`;

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`${BASE}${path}`]}>
        <Routes>
          <Route path="/factory/:factoryId/*" element={<FactoryPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

let server: FactoryServer;

function serve(view: InitiativeView, ran?: InitiativeView) {
  const snapshot = round3Snapshot();
  const asks = asksOf(snapshot, { incidents: round3IssueList(), proposals: roomProposals() });
  server = serveFactory(authenticatedFetch, {
    snapshot: () => json(snapshot),
    overrides: {
      "GET /v1/factory/preflight": () => json({ ...preflightOk(), cockpit_v2: true }),
      [`GET ${SNAPSHOT}/notify/me/ui`]: () => json({ cockpit: "v2" }),
      [`GET ${SNAPSHOT}/notify/me`]: () => json(notifyMe()),
      [`GET ${SNAPSHOT}/definition`]: () =>
        json({ ...definitionResponse(), discovery_server: true }),
      [`GET ${SNAPSHOT}/asks`]: () => json({ ...asks, initiatives: [sampleInitiativeLine(view)] }),
      [`GET ${PAGE}`]: () => json(view),
      [`POST ${PAGE}/acceptance`]: () => json(ran ?? view),
      [`GET ${SNAPSHOT}/initiatives/I-9`]: () =>
        json({ error: { code: "unknown_initiative", message: "no initiative" } }, 404),
    },
  });
}

beforeEach(() => {
  authenticatedFetch.mockReset();
  resetFactoryControls();
  resetCockpitChoiceCache();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("initiatives", () => {
  it("reads 3 and i-3 as I-3", () => {
    expect(initiativeRef("3")).toBe("I-3");
    expect(initiativeRef("i-3")).toBe("I-3");
    expect(initiativeRef("I-3")).toBe("I-3");
    expect(initiativeRef("x")).toBe("x");
  });

  it("puts a card per initiative on Home, above the projects", async () => {
    serve(sampleInitiativeView("failing"));
    renderAt("");

    const card = await screen.findByTestId("initiative-card-I-3", {}, { timeout: 15_000 });
    expect(within(card).getByRole("link", { name: "Versions in Settings" })).toHaveAttribute(
      "href",
      `/factory/${LIVE_RUN_FACTORY_ID}/initiatives/I-3`,
    );
    expect(within(card).getByTestId("initiative-state")).toHaveTextContent("Acceptance failing");
    expect(within(card).getByRole("img", { name: "1/3 merged" })).toBeInTheDocument();
    expect(within(card).getByTestId("initiative-acceptance")).toHaveTextContent(
      "Acceptance failing · 1 of 4 steps",
    );
    expect(within(card).getByTestId("initiative-try")).toHaveAttribute(
      "href",
      "https://app-1a2b.envs.sandbox.example.com",
    );
    const section = screen.getByTestId("home-initiatives");
    const projects = screen.getByTestId("home-projects");
    expect(
      section.compareDocumentPosition(projects) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  }, 30_000);

  it("shows the ask, the acceptance with its evidence and the projects by wave", async () => {
    serve(sampleInitiativeView("failing"));
    renderAt("/initiatives/3");

    const page = await screen.findByTestId("initiative-page", {}, { timeout: 15_000 });
    expect(within(page).getByRole("heading", { level: 1 })).toHaveTextContent(
      "Versions in SettingsI-3Acceptance failing",
    );
    expect(
      within(screen.getByTestId("initiative-ask")).getByText(/Can we show which/),
    ).toBeTruthy();
    expect(screen.getByTestId("initiative-ask")).toHaveTextContent(
      "Asked by Lena (sample) · 5h ago",
    );
    expect(within(page).getByTestId("initiative-try")).toHaveAttribute(
      "href",
      "https://app-1a2b.envs.sandbox.example.com",
    );
    expect(page).toHaveTextContent("Turn on settings.versions there");
    expect(screen.getByTestId("initiative-step-3")).toHaveAttribute("data-mark", "fail");
    expect(screen.getByTestId("initiative-step-3")).toHaveTextContent(
      "the row says “unknown” instead of a migration id",
    );
    expect(screen.getByTestId("initiative-step-4")).toHaveTextContent(
      "Cannot run in envs: synthetic login unavailable on the base env",
    );
    expect(screen.getByTestId("initiative-last-run")).toHaveTextContent(
      "Run 2 · failed · 4c1d9e0 · tasks T-14",
    );
    expect(within(screen.getByTestId("initiative-step-1")).getByText("Screenshot")).toHaveAttribute(
      "href",
      expect.stringMatching(/^\/v1\/factory\/demos\//),
    );
    expect(screen.getByTestId("initiative-project-settings-row")).toHaveAttribute(
      "data-role",
      "skeleton",
    );
    expect(screen.getByTestId("initiative-wave-2")).toHaveTextContent(
      "Uses api-version: serves its version through the same route",
    );
    expect(screen.getByTestId("initiative-next-wave")).toHaveTextContent(
      "Next: wave 3 · Copy the versions from the CLI too",
    );
  }, 30_000);

  it("runs the acceptance now", async () => {
    const view = sampleInitiativeView("in_progress");
    const ran: InitiativeView = {
      ...view,
      runs: [...view.runs, { n: 2, status: "requested", sha: view.base?.env_sha, tasks: [] }],
    };
    serve(view, ran);
    renderAt("/initiatives/I-3");

    fireEvent.click(await screen.findByTestId("initiative-run-now", {}, { timeout: 15_000 }));

    await waitFor(() =>
      expect(server.calls.some((c) => c.method === "POST" && c.path === `${PAGE}/acceptance`)).toBe(
        true,
      ),
    );
    await waitFor(() =>
      expect(screen.getByTestId("initiative-run-now")).toHaveTextContent("Run on its way"),
    );
  }, 30_000);

  it("says when there is no such initiative", async () => {
    serve(sampleInitiativeView());
    renderAt("/initiatives/9");

    expect(
      await screen.findByTestId("initiative-missing", {}, { timeout: 15_000 }),
    ).toHaveTextContent("This factory has no initiative I-9.");
  }, 30_000);
});
