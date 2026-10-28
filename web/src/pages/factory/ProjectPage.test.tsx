import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LIVE_RUN_FACTORY_ID, pendingForever } from "@/components/factory/__fixtures__/liveRun";
import { resetFactoryControls } from "@/components/factory/controls/useFactoryControls";
import { PROJECT_SECTIONS } from "@/components/factory/factoryRoutes";
import {
  UX_PR_ID,
  projectScenario,
} from "@/components/factory/project/__fixtures__/projectFixtures";
import { FactoryPage } from "./FactoryPage";
import { type FactoryServer, SNAPSHOT, json, serveFactory } from "./testServer";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({ useSessionUpdatesConnected: () => true }));

const PL_API = "b408c5f09ff659e6b907fd9f688829e9";
const PAGE = `/factory/${LIVE_RUN_FACTORY_ID}/projects`;

let location = "";
function LocationProbe() {
  const loc = useLocation();
  location = loc.pathname + loc.search + loc.hash;
  return null;
}

function renderPage(slug = "api-version", rest = "") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`${PAGE}/${slug}${rest}`]}>
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
function serve(snapshot?: () => Response | Promise<Response>) {
  server = serveFactory(authenticatedFetch, snapshot ? { snapshot } : {});
}

beforeEach(() => {
  authenticatedFetch.mockReset();
  resetFactoryControls();
});
afterEach(() => vi.unstubAllGlobals());

describe("ProjectPage", () => {
  it("is a page: breadcrumb, header and every section anchored, no drawer", async () => {
    serve();
    renderPage();
    const header = await screen.findByTestId("project-header");
    const crumbs = screen.getByTestId("project-breadcrumb");
    expect(crumbs).toHaveTextContent("ProjectsAPI version endpoint");
    expect(header).toHaveTextContent("P1");
    expect(within(header).getByTestId("project-repo")).toHaveTextContent("caffeinelabs/app#main");
    expect(within(header).getByTestId("project-open-pl")).toHaveAttribute("href", `/c/${PL_API}`);
    for (const section of PROJECT_SECTIONS) {
      expect(screen.getByTestId(`project-section-${section}`)).toHaveAttribute("id", section);
      expect(screen.getByTestId(`project-nav-${section}`)).toHaveAttribute("href", `#${section}`);
    }
    expect(screen.getByTestId("project-nav-prs")).toHaveTextContent("PRs2");
    expect(screen.getByTestId("project-nav-demos")).toHaveTextContent("Demos3");
    expect(screen.getByTestId("project-nav-room")).toHaveAttribute(
      "href",
      `/factory/${LIVE_RUN_FACTORY_ID}/room/project%3Aapi-version`,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.querySelector("[data-slot=sheet-content]")).toBeNull();
  });

  it("shows the charter, progress, plan, team and decisions", async () => {
    serve();
    renderPage();
    const charter = await screen.findByTestId("project-charter");
    expect(charter).toHaveTextContent("Expose the deployed commit: GET /version on caffeine-api");
    expect(within(charter).getByTestId("project-charter-settings")).toHaveTextContent(
      "Previews on · 2 slots",
    );
    expect(screen.getByTestId("project-progress")).toHaveTextContent("Waiting onnobody");
    expect(screen.getByTestId("project-plan-link")).toHaveAttribute(
      "href",
      `/c/${PL_API}?file=~%2Ffactory%2Fapi-version%2Fplan.md`,
    );
    const team = screen.getByTestId("project-team");
    expect(within(team).getByTestId(`project-member-${PL_API}`)).toHaveTextContent(
      "PL · api-version",
    );
    expect(screen.getByTestId("decisions-answered-toggle")).toHaveTextContent("Answered (1)");
    fireEvent.click(screen.getByTestId("charter-edit"));
    expect(screen.getByTestId("edit-charter-dialog")).toHaveTextContent(
      "Edit charter · api-version",
    );
  });

  it("keeps withdrawn and expired decisions, opened on a ?decision= link", async () => {
    serve();
    renderPage("api-version", "?decision=D-4");
    expect(await screen.findByTestId("decision-closed-D-4")).toHaveAttribute(
      "data-highlighted",
      "true",
    );
  });

  it("points a blocked project's D-n at its decision", async () => {
    serve();
    renderPage("db-version");
    const card = await screen.findByTestId("decision-card-D-2");
    expect(card).toHaveTextContent("Which table is the source of truth for the DB version");
    fireEvent.click(screen.getByTestId("blocked-link-db-version"));
    await waitFor(() => expect(location).toContain("decision=D-2#decisions"));
    expect(screen.getByTestId("decision-card-D-2")).toHaveAttribute("data-highlighted", "true");
    expect(screen.getByTestId("project-progress")).toHaveTextContent("D-2");
  });

  it("plays every demo inline from the project's demos route, older runs per PR", async () => {
    serve();
    renderPage("api-version", "#demos");
    expect(await screen.findByTestId("demo-card-9634")).toBeInTheDocument();
    expect(screen.getByTestId("project-demos-summary")).toHaveTextContent("2 PRs · 3 runs");
    expect(server.calls.some((c) => c.path === `${SNAPSHOT}/projects/api-version/demos`)).toBe(
      true,
    );
    expect(screen.getByTestId("demo-earlier-9630")).toHaveTextContent("1 earlier run");
    expect(screen.getByTestId("demo-review-9634")).toHaveAttribute(
      "href",
      `/factory/${LIVE_RUN_FACTORY_ID}/prs/${UX_PR_ID}`,
    );
  });

  it("opens the PR page, not a drawer, from a PR row", async () => {
    serve();
    renderPage();
    fireEvent.click(await screen.findByTestId("review-open-9634"));
    expect(await screen.findByTestId("pr-page")).toHaveAttribute("data-pr", "9634");
    expect(location).toBe(`/factory/${LIVE_RUN_FACTORY_ID}/prs/${UX_PR_ID}`);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("sends an old ?tab= link to its section", async () => {
    serve();
    renderPage("api-version", "?tab=demos");
    await waitFor(() => expect(location).toBe(`${PAGE}/api-version#demos`));
    expect(await screen.findByTestId("project-section-demos")).toBeInTheDocument();
  });

  it("sends an old ?review= link on the project page to the PR page", async () => {
    serve();
    renderPage("api-version", "?review=9634");
    await waitFor(() => expect(location).toBe(`/factory/${LIVE_RUN_FACTORY_ID}/prs/${UX_PR_ID}`));
  });

  it("shows the dev env and each PR's preview, and redeploys one", async () => {
    serve();
    renderPage();
    const env = await screen.findByTestId("project-dev-env");
    expect(env).toHaveTextContent("Probed 21s ago · ok");
    const preview = screen.getByTestId("project-preview-9634");
    fireEvent.click(within(preview).getByTestId("project-preview-redeploy-9634"));
    await waitFor(() =>
      expect(server.controls).toEqual([
        { action: "preview", target: "project:api-version", options: { pr_id: UX_PR_ID } },
      ]),
    );
  });

  it("lists only the project's activity", async () => {
    serve();
    renderPage("db-version");
    await screen.findAllByTestId("activity-row");
    const pinned = server.calls.filter(
      (c) => c.path.startsWith(`${SNAPSHOT}/events`) && c.path.includes("project="),
    );
    expect(pinned.length).toBeGreaterThan(0);
    expect(
      pinned.every((c) => new URL(c.path, "http://x").searchParams.get("project") === "db-version"),
    ).toBe(true);
  });

  it("disables the levers for a viewer and shows the read-only banner", async () => {
    serve(() => {
      const { snapshot } = projectScenario();
      snapshot.viewer = {
        user_id: "ops@caffeine.ai",
        role: "viewer",
        level: 1,
        is_creator: false,
        can: {
          post: false,
          answer: false,
          review: false,
          control: false,
          intervene: false,
          apply_projects: false,
          apply_tuning: false,
          restart: false,
          stop: false,
          invite: false,
          settings: false,
          terminal: false,
          start: false,
        },
      };
      return json(snapshot);
    });
    renderPage("db-version");
    expect(await screen.findByTestId("factory-viewer-banner")).toHaveTextContent(
      "You can watch everything",
    );
    expect(screen.getByTestId("charter-edit")).toBeDisabled();
    expect(screen.getByTestId("factory-add-project")).toBeDisabled();
    expect(screen.getByTestId("factory-pause-all")).toBeDisabled();
    expect(screen.getByTestId("decision-option-D-2-0")).toBeDisabled();
  });

  it("says when the slug is not on the board", async () => {
    serve();
    renderPage("nope");
    expect(await screen.findByTestId("project-missing")).toHaveTextContent(
      "There is no project nope in this factory",
    );
  });

  it("shows a skeleton, never an empty page, while the first read is pending", () => {
    serve(() => pendingForever());
    renderPage();
    expect(screen.getByTestId("factory-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("project-header")).toBeNull();
  });

  it("alerts with Retry when the first read fails", async () => {
    let fail = true;
    serve(() =>
      fail ? json({ error: { message: "down" } }, 503) : json(projectScenario().snapshot),
    );
    renderPage();
    const alert = await screen.findByTestId("factory-load-error", {}, { timeout: 5000 });
    expect(alert).toHaveTextContent("Couldn’t load this factory");
    fail = false;
    fireEvent.click(screen.getByTestId("factory-load-error-retry"));
    expect(await screen.findByTestId("project-header")).toBeInTheDocument();
  });

  it("says when the factory does not exist or access was removed", async () => {
    serve(() => json({ error: { message: "not found" } }, 404));
    renderPage();
    expect(await screen.findByTestId("factory-missing")).toHaveTextContent("no longer have access");
  });
});
