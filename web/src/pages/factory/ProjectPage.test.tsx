import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LIVE_RUN_FACTORY_ID, pendingForever } from "@/components/factory/__fixtures__/liveRun";
import { boardActivityEvents } from "@/components/factory/board/__fixtures__/boardFixtures";
import { resetFactoryControls } from "@/components/factory/controls/useFactoryControls";
import { filterEventPage } from "@/components/factory/factoryStoryFixtures";
import {
  UX_PR_ID,
  filterDemos,
  projectScenario,
} from "@/components/factory/project/__fixtures__/projectFixtures";
import { ProjectPage } from "./ProjectPage";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({ useSessionUpdatesConnected: () => true }));

const SNAPSHOT = `/v1/factory/factories/${LIVE_RUN_FACTORY_ID}`;
const PL_API = "b408c5f09ff659e6b907fd9f688829e9";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

let controls: unknown[] = [];

function serve(
  snapshot: () => Response | Promise<Response> = () => json(projectScenario().snapshot),
) {
  authenticatedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path.startsWith(`${SNAPSHOT}/events`)) {
      return json(filterEventPage(boardActivityEvents(), path));
    }
    if (path.startsWith(`${SNAPSHOT}/demos`))
      return json(filterDemos(projectScenario().demos, path));
    if (path === `${SNAPSHOT}/control` && init?.method === "POST") {
      controls.push(JSON.parse(String(init.body)));
      return json({ events: [] });
    }
    if (path === SNAPSHOT) return snapshot();
    throw new Error(`unexpected ${path}`);
  });
}

let location = "";
function LocationProbe() {
  const loc = useLocation();
  location = loc.pathname + loc.search;
  return null;
}

function renderPage(slug = "api-version", search = "") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/factory/${LIVE_RUN_FACTORY_ID}/projects/${slug}${search}`]}>
        <Routes>
          <Route
            path="/factory/:factoryId/projects/:slug"
            element={
              <>
                <ProjectPage />
                <LocationProbe />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function fetchedPaths(): string[] {
  return authenticatedFetch.mock.calls.map(([path]) => path as string);
}

beforeEach(() => {
  authenticatedFetch.mockReset();
  controls = [];
  resetFactoryControls();
});
afterEach(() => vi.unstubAllGlobals());

describe("ProjectPage", () => {
  it("renders the breadcrumb, header and tab counts from the live snapshot", async () => {
    serve();
    renderPage();
    const header = await screen.findByTestId("project-header");
    const crumbs = screen.getByTestId("project-breadcrumb");
    expect(crumbs).toHaveTextContent("Factory");
    expect(crumbs).toHaveTextContent("Atlas factory");
    expect(crumbs).toHaveTextContent("API version endpoint");
    expect(within(crumbs).getByTestId("project-breadcrumb-factory")).toHaveAttribute(
      "href",
      `/factory/${LIVE_RUN_FACTORY_ID}`,
    );
    expect(header).toHaveTextContent("API version endpoint");
    expect(header).toHaveTextContent("P1");
    expect(within(header).getByTestId("project-slug")).toHaveTextContent("api-version");
    expect(within(header).getByTestId("project-repo")).toHaveTextContent("caffeinelabs/app#main");
    expect(within(header).getByTestId("project-open-pl")).toHaveAttribute("href", `/c/${PL_API}`);
    expect(within(header).getByTestId("stage-stepper")).toHaveAttribute("data-current", "ready");
    expect(within(header).getByTestId("project-cost")).toHaveTextContent("$7.10");
    expect(screen.getByTestId("project-tab-prs")).toHaveTextContent("PRs2");
    expect(screen.getByTestId("project-tab-demos")).toHaveTextContent("Demos3");
  });

  it("shows the charter, team, decisions and plan on the Overview", async () => {
    serve();
    renderPage();
    const charter = await screen.findByTestId("project-charter");
    expect(charter).toHaveTextContent("Expose the deployed commit: GET /version on caffeine-api");
    expect(within(charter).getByTestId("project-dod")).toHaveTextContent(
      "curl -fsS {api}/version → 200 with gitSha = the deployed sha",
    );
    expect(charter).toHaveTextContent("services/caffeine-api/**");
    expect(charter).toHaveTextContent("Don't touch the auth middleware");
    expect(within(charter).getByTestId("project-demo-focus")).toHaveTextContent(
      "Open Settings → About and show the API version row",
    );
    const settings = within(charter).getByTestId("project-charter-settings");
    expect(settings).toHaveTextContent("Needs a dev env");
    expect(settings).toHaveTextContent("Previews on · 2 slots");
    expect(settings).toHaveTextContent("Up to 3 ICs");
    const team = screen.getByTestId("project-team");
    expect(within(team).getByTestId(`project-member-${PL_API}`)).toHaveTextContent(
      "PL · api-version",
    );
    expect(team).toHaveTextContent("demo-version-endpoint");
    expect(screen.getByTestId("decisions-rail")).toHaveTextContent("Decisions · 0 open");
    expect(screen.getByTestId("decisions-answered-toggle")).toHaveTextContent("Answered (1)");
    expect(screen.getByTestId("project-plan-link")).toHaveAttribute(
      "href",
      `/c/${PL_API}?file=~%2Ffactory%2Fapi-version%2Fplan.md`,
    );
    fireEvent.click(screen.getByTestId("charter-edit"));
    expect(screen.getByTestId("edit-charter-dialog")).toHaveTextContent(
      "Edit charter · api-version",
    );
  });

  it("keeps the project's withdrawn and expired decisions on the Overview", async () => {
    serve();
    renderPage();
    const toggle = await screen.findByTestId("decisions-closed-toggle");
    expect(toggle).toHaveTextContent("Withdrawn / expired (2)");
    fireEvent.click(toggle);
    const closed = screen.getByTestId("decisions-closed");
    expect(within(closed).getByTestId("decision-closed-D-4")).toHaveTextContent(
      "D-4withdrawn · 1h agoShould /version also report the build time?",
    );
    expect(within(closed).getByTestId("decision-closed-D-5")).toHaveTextContent(
      "D-5expired · 1d agoShow the API version in the footer too",
    );
    expect(screen.getByTestId("decisions-answered-toggle")).toHaveTextContent("Answered (1)");
  });

  it("opens the closed decisions on a ?decision= link to one of them", async () => {
    serve();
    renderPage("api-version", "?decision=D-4");
    expect(await screen.findByTestId("decision-closed-D-4")).toHaveAttribute(
      "data-highlighted",
      "true",
    );
  });

  it("lists no closed decisions for a project that has none", async () => {
    serve();
    renderPage("db-version");
    await screen.findByTestId("decision-card-D-2");
    expect(screen.queryByTestId("decisions-closed-toggle")).toBeNull();
  });

  it("answers a blocked project's decision in one click from its Overview", async () => {
    serve();
    renderPage("db-version");
    const card = await screen.findByTestId("decision-card-D-2");
    expect(card).toHaveTextContent("Which table is the source of truth for the DB version");
    fireEvent.click(screen.getByTestId("blocked-link-db-version"));
    await waitFor(() => expect(location).toContain("decision=D-2"));
    expect(screen.getByTestId("decision-card-D-2")).toHaveAttribute("data-highlighted", "true");
  });

  it("switches tabs through ?tab=, and Overview leaves the param out", async () => {
    serve();
    renderPage();
    fireEvent.mouseDown(await screen.findByTestId("project-tab-prs"));
    await waitFor(() => expect(location).toContain("?tab=prs"));
    expect(await screen.findByTestId("pr-row-9630")).toBeInTheDocument();
    expect(screen.getByTestId("pr-row-9634")).toBeInTheDocument();
    expect(screen.queryByTestId("project-overview")).toBeNull();
    fireEvent.mouseDown(screen.getByTestId("project-tab-overview"));
    await waitFor(() => expect(location).not.toContain("tab="));
    expect(await screen.findByTestId("project-overview")).toBeInTheDocument();
  });

  it("opens the review drawer on the page from a PR row and clears it on close", async () => {
    serve();
    renderPage("api-version", "?tab=prs");
    fireEvent.click(await screen.findByTestId("review-open-9634"));
    const drawer = await screen.findByTestId("review-drawer");
    expect(drawer).toHaveTextContent("Review · PR #9634");
    expect(location).toContain("review=9634");
    fireEvent.click(within(drawer).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByTestId("review-drawer")).toBeNull());
    expect(location).toBe(`/factory/${LIVE_RUN_FACTORY_ID}/projects/api-version?tab=prs`);
  });

  it("deep links to the Demos tab and asks for the project's demos", async () => {
    serve();
    renderPage("api-version", "?tab=demos");
    expect(await screen.findByTestId("demo-card-9634")).toBeInTheDocument();
    const demos = fetchedPaths().find((p) => p.startsWith(`${SNAPSHOT}/demos`))!;
    const query = new URL(demos, "http://x").searchParams;
    expect(query.get("project")).toBe("api-version");
    expect(query.get("limit")).toBe("200");
    fireEvent.click(screen.getByTestId("demo-review-9634"));
    expect(await screen.findByTestId("review-drawer")).toHaveTextContent("Review · PR #9634");
  });

  it("shows the dev env and each PR's preview, and redeploys one", async () => {
    serve();
    renderPage("api-version", "?tab=previews");
    const env = await screen.findByTestId("project-dev-env");
    expect(env).toHaveTextContent("app-b693ef0a-2c6683-a38d.envs.sandbox.caffeinelabs.io");
    expect(env).toHaveTextContent("Probed 21s ago · ok");
    const preview = screen.getByTestId("project-preview-9634");
    expect(preview).toHaveTextContent("slot 2");
    expect(preview).toHaveTextContent("3c9d8e7");
    expect(within(preview).getByTestId("project-preview-open-9634")).toHaveAttribute(
      "href",
      `/v1/factory/prs/${UX_PR_ID}/preview`,
    );
    expect(screen.getByTestId("project-previews")).toHaveTextContent("2 of 2 slots in use");
    fireEvent.click(within(preview).getByTestId("project-preview-redeploy-9634"));
    await waitFor(() =>
      expect(controls).toEqual([
        { action: "preview", target: "project:api-version", options: { pr_id: UX_PR_ID } },
      ]),
    );
  });

  it("lists only the project's activity", async () => {
    serve();
    renderPage("db-version", "?tab=activity");
    await screen.findAllByTestId("activity-row");
    const events = fetchedPaths().filter(
      (p) => p.startsWith(`${SNAPSHOT}/events`) && !p.includes("control"),
    );
    expect(
      events.every((p) => new URL(p, "http://x").searchParams.get("project") === "db-version"),
    ).toBe(true);
    expect(screen.queryByTestId("activity-filter-project")).toBeNull();
  });

  it("says when the slug is not on the board", async () => {
    serve();
    renderPage("nope");
    expect(await screen.findByTestId("project-missing")).toHaveTextContent(
      "There is no project nope in this factory",
    );
    expect(screen.getByTestId("project-breadcrumb")).toHaveTextContent("nope");
  });

  it("shows a skeleton, never an empty page, while the first read is pending", () => {
    serve(() => pendingForever());
    renderPage();
    expect(screen.getByTestId("project-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("project-header")).toBeNull();
  });

  it("alerts with Retry when the first read fails", async () => {
    let fail = true;
    serve(() =>
      fail ? json({ error: { message: "down" } }, 503) : json(projectScenario().snapshot),
    );
    renderPage();
    // A 5xx is retried once before the error shows.
    const alert = await screen.findByTestId("factory-load-error", {}, { timeout: 5000 });
    expect(alert).toHaveTextContent("Couldn’t load this project");
    fail = false;
    fireEvent.click(screen.getByTestId("factory-load-error-retry"));
    expect(await screen.findByTestId("project-header")).toBeInTheDocument();
  });

  it("says when the factory does not exist", async () => {
    serve(() => json({ error: { message: "not found" } }, 404));
    renderPage();
    expect(await screen.findByTestId("factory-missing")).toBeInTheDocument();
  });
});
