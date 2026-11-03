import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { configure, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { preflightOk } from "@/components/factory/__fixtures__/liveRun";
import { opsRoutes } from "@/components/factory/agents/__fixtures__/opsFixtures";
import { resetFactoryControls } from "@/components/factory/controls/useFactoryControls";
import {
  EVIDENCE_SLUG,
  IDEA_SLUG,
  type ProjectV2Options,
  REQUESTED_SLUG,
  SHIPPED_SLUG,
  STOPPED_SLUG,
  projectV2Routes,
  projectsV2Snapshot,
} from "@/components/factory/project/__fixtures__/projectV2Fixtures";
import { COCKPIT_STORAGE_KEY } from "@/components/factory/shell/cockpitV2";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { FactoryPage } from "./FactoryPage";
import { json, serveFactory } from "./testServer";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({ useSessionUpdatesConnected: () => true }));
configure({ asyncUtilTimeout: 5000 });

let location = "";
function LocationProbe() {
  const loc = useLocation();
  location = loc.pathname + loc.search + loc.hash;
  return null;
}

function serve(options: ProjectV2Options = {}): FactorySnapshot {
  const snapshot = projectsV2Snapshot(options);
  const fid = snapshot.factory.id;
  serveFactory(authenticatedFetch, {
    snapshot: () => json(snapshot),
    ops: {
      ...opsRoutes(fid, { snapshot }),
      ...projectV2Routes(snapshot),
      [`/v1/factory/factories/${fid}/asks`]: { error: 404 },
    },
    overrides: {
      "GET /v1/factory/preflight": () => json({ ...preflightOk(), cockpit_v2: true }),
    },
  });
  return snapshot;
}

function renderAt(snapshot: FactorySnapshot, path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/factory/${snapshot.factory.id}${path}`]}>
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

function cardSlugs(): string[] {
  return [...document.querySelectorAll("article[data-testid^='project-card-']")].map((c) =>
    c.getAttribute("data-testid")!.replace("project-card-", ""),
  );
}

beforeEach(() => {
  authenticatedFetch.mockReset();
  resetFactoryControls();
  window.localStorage.clear();
  window.localStorage.setItem(COCKPIT_STORAGE_KEY, "v2");
  location = "";
});

describe("Projects (new cockpit)", () => {
  it("shows one outcome-first card per live project, and folds shipped work", async () => {
    const snapshot = serve({ question: true });
    const { container } = renderAt(snapshot, "/projects");
    await screen.findByTestId("project-cards");
    expect(cardSlugs()).toEqual([EVIDENCE_SLUG, IDEA_SLUG, REQUESTED_SLUG, STOPPED_SLUG]);
    const evidence = screen.getByTestId(`project-card-${EVIDENCE_SLUG}`);
    expect(within(evidence).getByRole("heading", { level: 2 })).toHaveTextContent(
      "(sample) People can export a report as CSV",
    );
    expect(within(evidence).getByTestId(`project-card-${EVIDENCE_SLUG}-why`)).toHaveTextContent(
      "from O-3 · measured by weekly_exports",
    );
    expect(within(evidence).getByTestId(`project-card-${EVIDENCE_SLUG}-update`)).toHaveTextContent(
      "nova: (sample) T-3 merged",
    );
    expect(within(evidence).getByTestId("project-card-spend")).toHaveTextContent("$25.35 of $60");
    expect(within(evidence).getByTestId("project-team-line")).toHaveTextContent("Lead opus");
    expect(screen.getByTestId(`project-card-${IDEA_SLUG}-why`)).toHaveTextContent(
      "No user evidence · Atlas's idea, counted as a hunch · not measured",
    );
    expect(screen.getByTestId(`project-card-${REQUESTED_SLUG}`)).toHaveAttribute(
      "data-stripe",
      "amber",
    );
    expect(screen.getByTestId(`project-card-${STOPPED_SLUG}`)).toHaveAttribute(
      "data-stripe",
      "red",
    );
    expect(screen.getByTestId(`projects-shipped-${SHIPPED_SLUG}`)).toHaveTextContent(
      "not measured",
    );
    const results = await axe.run(container, {
      runOnly: ["heading-order", "landmark-unique", "button-name", "link-name"],
    });
    expect(results.violations.map((v) => v.id)).toEqual([]);
  });

  it("filters to what needs you, from evidence, requested or stopped, in the address", async () => {
    const snapshot = serve({ question: true });
    renderAt(snapshot, "/projects");
    await screen.findByTestId("project-cards");
    expect(screen.getByTestId("projects-filter-all")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("projects-filter-needs")).toHaveTextContent("Needs you1");
    fireEvent.click(screen.getByTestId("projects-filter-needs"));
    await waitFor(() => expect(cardSlugs()).toEqual([REQUESTED_SLUG]));
    expect(location).toContain("f=needs");
    fireEvent.click(screen.getByTestId("projects-filter-requested"));
    await waitFor(() => expect(cardSlugs()).toEqual([IDEA_SLUG, REQUESTED_SLUG]));
  });

  it("says when a filter matches nothing, and clears it", async () => {
    const snapshot = serve({ only: [REQUESTED_SLUG] });
    renderAt(snapshot, "/projects?f=stopped");
    const empty = await screen.findByTestId("projects-empty");
    fireEvent.click(within(empty).getByRole("button", { name: "Show all projects" }));
    await waitFor(() => expect(cardSlugs()).toEqual([REQUESTED_SLUG]));
  });

  it("lays the projects out as a board by stage; stopped work stays off it", async () => {
    const snapshot = serve();
    renderAt(snapshot, "/projects?view=board");
    const board = await screen.findByTestId("project-board");
    expect(within(board).getByTestId("project-board-column-verify")).toHaveTextContent(
      "(sample) People can export a report as CSV",
    );
    expect(within(board).getByTestId("project-board-column-shipped")).toHaveTextContent(
      "not measured",
    );
    expect(within(board).queryByTestId(`project-board-card-${STOPPED_SLUG}`)).toBeNull();
    expect(screen.getByTestId("project-board-stopped")).toHaveTextContent(
      `${STOPPED_SLUG} stopped. Not on the board.`,
    );
    expect(screen.getByTestId("projects-view-board")).toHaveAttribute("aria-pressed", "true");
  });

  it("opens an agent's panel from its ring", async () => {
    const snapshot = serve();
    renderAt(snapshot, "/projects");
    const card = await screen.findByTestId(`project-card-${EVIDENCE_SLUG}`);
    fireEvent.click(within(card).getByTestId("project-ring-dot"));
    await waitFor(() => expect(location).toContain("peek=agent%3Adot"));
    expect(await screen.findByTestId("agent-panel")).toBeInTheDocument();
  });

  it("moves the table to Debug", async () => {
    const snapshot = serve();
    renderAt(snapshot, "/projects?view=table");
    await waitFor(() => expect(location).toBe(`/factory/${snapshot.factory.id}/debug/projects`));
    expect(await screen.findByTestId("factory-debug-page")).toHaveAttribute(
      "data-view",
      "projects",
    );
  });
});
