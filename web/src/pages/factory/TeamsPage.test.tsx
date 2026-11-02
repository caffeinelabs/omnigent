import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { configure, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { preflightOk } from "@/components/factory/__fixtures__/liveRun";
import { opsRoutes } from "@/components/factory/agents/__fixtures__/opsFixtures";
import {
  benchResponse,
  teamsAgentRows,
  teamsRoutes,
  teamsSnapshot,
} from "@/components/factory/agents/__fixtures__/teamsFixtures";
import { resetFactoryControls } from "@/components/factory/controls/useFactoryControls";
import { COCKPIT_STORAGE_KEY } from "@/components/factory/shell/cockpitV2";
import { FactoryPage } from "./FactoryPage";
import { json, serveFactory } from "./testServer";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({ useSessionUpdatesConnected: () => true }));
configure({ asyncUtilTimeout: 5000 });

const snapshot = teamsSnapshot();
const FID = snapshot.factory.id;
const BASE = `/factory/${FID}`;

let location = "";
function LocationProbe() {
  const loc = useLocation();
  location = loc.pathname + loc.search;
  return null;
}

function serve(cockpitV2: boolean, routes = teamsRoutes(snapshot)) {
  serveFactory(authenticatedFetch, {
    snapshot: () => json(snapshot),
    ops: { ...opsRoutes(FID, { snapshot }), ...routes },
    overrides: {
      "GET /v1/factory/preflight": () => json({ ...preflightOk(), cockpit_v2: cockpitV2 }),
    },
  });
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
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

beforeEach(() => {
  authenticatedFetch.mockReset();
  resetFactoryControls();
  window.localStorage.clear();
  location = "";
});

describe("the new cockpit switch", () => {
  it("keeps Teams dark: without the server flag /teams lands on the fleet", async () => {
    serve(false);
    renderAt("/teams?cockpit=v2");
    await screen.findByTestId("factory-agents-page");
    expect(location).toBe(`${BASE}/agents`);
    expect(screen.queryByTestId("try-teams-view")).toBeNull();
  });

  it("offers the new view on the fleet, and a link switches this person to it", async () => {
    serve(true);
    renderAt("/agents");
    const link = await screen.findByTestId("try-teams-view");
    fireEvent.click(link);
    await screen.findByTestId("factory-teams-page");
    expect(window.localStorage.getItem(COCKPIT_STORAGE_KEY)).toBe("v2");
  });

  it("links back to Teams from the fleet once this person switched", async () => {
    window.localStorage.setItem(COCKPIT_STORAGE_KEY, "v2");
    serve(true);
    // The new cockpit keeps the fleet table in Debug (`/agents` lands on Teams › All agents).
    renderAt("/debug/fleet");
    fireEvent.click(await screen.findByTestId("back-to-teams"));
    await screen.findByTestId("factory-teams-page");
    expect(location).toBe(`${BASE}/teams`);
  });

  it("switches back to the classic view", async () => {
    window.localStorage.setItem(COCKPIT_STORAGE_KEY, "v2");
    serve(true);
    renderAt("/teams");
    fireEvent.click(await screen.findByRole("button", { name: "Back to the classic view" }));
    await screen.findByTestId("factory-agents-page");
    expect(window.localStorage.getItem(COCKPIT_STORAGE_KEY)).toBe("v1");
  });
});

describe("Teams", () => {
  beforeEach(() => {
    window.localStorage.setItem(COCKPIT_STORAGE_KEY, "v2");
  });

  it("shows the lead seats and every project team with each member on one line", async () => {
    serve(true);
    renderAt("/teams");
    expect(await screen.findByTestId("team-seat-portfolio")).toHaveTextContent("Atlas");
    expect(screen.getByTestId("team-seat-reliability")).toHaveTextContent("Sentinel");
    const csv = screen.getByTestId("team-card-csv-export");
    expect(within(csv).getByTestId("team-member-dot")).toHaveTextContent("Dot");
    expect(within(csv).getByTestId("team-member-dot")).toHaveTextContent("claude · sonnet");
    expect(within(csv).getByTestId("team-member-dot")).toHaveTextContent("Stuck");
    expect(within(csv).getByTestId("team-line")).toHaveTextContent("Builder sonnet, gpt-5-codex");
    expect(screen.getByTestId("team-card-dark-mode")).toHaveTextContent("Needs a merge");
    expect(screen.getByTestId("teams-shared-env")).toHaveTextContent("Shared environment: ready");
    expect(screen.queryByTestId("team-card-base-env")).toBeNull();
    expect(screen.getByTestId("teams-state-stuck")).toHaveTextContent("1 stuck");
  });

  it("filters by vendor and model, and by a state count", async () => {
    serve(true);
    renderAt("/teams");
    fireEvent.change(await screen.findByTestId("teams-filter"), { target: { value: "codex" } });
    await waitFor(() => expect(location).toContain("q=codex"));
    expect(screen.queryByTestId("team-member-dot")).toBeNull();
    expect(screen.getByTestId("team-member-rex")).toBeInTheDocument();
    fireEvent.change(screen.getByTestId("teams-filter"), { target: { value: "" } });
    fireEvent.click(screen.getByTestId("teams-state-stuck"));
    await waitFor(() => expect(location).toContain("state=stuck"));
    expect(screen.getByTestId("teams-state-stuck")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("team-member-dot")).toBeInTheDocument();
    expect(screen.queryByTestId("team-member-ivy")).toBeNull();
  });

  it("says when no member matches, and clears", async () => {
    serve(true);
    renderAt("/teams?q=nobody");
    const empty = await screen.findByTestId("teams-no-match");
    expect(empty).toHaveTextContent(
      "The filter matches name, role, vendor, model, state and project.",
    );
    fireEvent.click(within(empty).getByRole("button", { name: "Clear" }));
    await screen.findByTestId("team-member-dot");
  });

  it("opens a member's panel from its line, with a link to its full page", async () => {
    serve(true);
    renderAt("/teams");
    fireEvent.click(await screen.findByTestId("team-member-dot"));
    await waitFor(() => expect(location).toContain("peek=agent%3Adot"));
    const panel = await screen.findByTestId("agent-panel");
    expect(within(panel).getByTestId("agent-state-sentence")).toHaveTextContent(
      "Stuck: same failing Bash call 3 times",
    );
    expect(screen.getByRole("link", { name: /Full page/ })).toHaveAttribute(
      "href",
      `${BASE}/agents/dot`,
    );
  });

  it("shows the leads' helpers in Other agents, so every counted agent has a line", async () => {
    const rows = teamsAgentRows(snapshot);
    const atlas = rows.find((r) => r.handle === "atlas")!;
    const helper = {
      ...rows.find((r) => r.handle === "ivy")!,
      session_id: "f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0",
      handle: "lark",
      title: "(sample) helper",
      project: null,
      parent_session_id: atlas.session_id,
      status: "running",
      stuck: true,
      stuck_reason: "running with no new activity for 20 min",
    };
    serve(true, teamsRoutes(snapshot, { rows: [...rows, helper] }));
    renderAt("/teams?state=stuck");
    const others = await screen.findByTestId("team-card-others");
    expect(within(others).getByTestId("team-member-lark")).toHaveTextContent("Stuck");
    expect(screen.getByTestId("teams-state-stuck")).toHaveTextContent("2 stuck");
  });

  it("says when a peeked agent is not in this factory", async () => {
    serve(true);
    renderAt("/teams?peek=agent%3Anobody");
    expect(await screen.findByTestId("agent-peek-missing")).toHaveTextContent(
      "Not in this factory",
    );
    expect(screen.queryByTestId("agent-panel")).toBeNull();
  });

  it("shows the bench with low-n rows marked", async () => {
    serve(true);
    renderAt("/teams/bench");
    const table = await screen.findByTestId("bench-table");
    expect(table).toHaveTextContent("Track record by role and model");
    expect(table.querySelectorAll("tr[data-low]").length).toBe(2);
    expect(table).toHaveTextContent("first-pass review · not enough data");
  });

  it("says when the bench has cost by model only", async () => {
    serve(true, { ...teamsRoutes(snapshot, { bench: benchResponse(false) }) });
    renderAt("/teams/bench");
    expect(await screen.findByTestId("bench-note")).toHaveTextContent(
      "Quality by model needs a few more days of data",
    );
  });

  it("lists every agent with its model in All agents", async () => {
    serve(true);
    renderAt("/teams/agents");
    const table = await screen.findByTestId("all-agents");
    expect(within(table).getAllByRole("row")).toHaveLength(14);
    expect(table).toHaveTextContent("codex · gpt-5-codex");
  });
});

describe("an agent's full page", () => {
  it("opens by handle in the new cockpit", async () => {
    window.localStorage.setItem(COCKPIT_STORAGE_KEY, "v2");
    serve(true);
    renderAt("/agents/dot");
    const page = await screen.findByTestId("factory-agent-profile");
    expect(within(page).getByRole("navigation", { name: "Breadcrumb" })).toHaveTextContent("Teams");
    expect(within(page).getByTestId("agent-state-sentence")).toHaveTextContent("Stuck");
  });

  it("keeps the classic page for its debug tabs and without the new cockpit", async () => {
    window.localStorage.setItem(COCKPIT_STORAGE_KEY, "v2");
    serve(true);
    renderAt(`/agents/${snapshot.members.at(-1)!.session_id}?tab=transcript`);
    await waitFor(() => expect(authenticatedFetch).toHaveBeenCalled());
    expect(screen.queryByTestId("factory-agent-profile")).toBeNull();
  });

  it("keeps the classic page on its Live tab and for a replay moment", async () => {
    window.localStorage.setItem(COCKPIT_STORAGE_KEY, "v2");
    serve(true);
    const sid = snapshot.members.at(-1)!.session_id;
    renderAt(`/agents/${sid}?tab=transcript`);
    fireEvent.click(await screen.findByTestId("agent-tab-live"));
    await waitFor(() => expect(location).toContain("tab=live"));
    expect(screen.getByTestId("factory-agent-page")).toBeInTheDocument();
    expect(screen.queryByTestId("factory-agent-profile")).toBeNull();
  });

  it("opens a Timeline replay link on the classic page", async () => {
    window.localStorage.setItem(COCKPIT_STORAGE_KEY, "v2");
    serve(true);
    const sid = snapshot.members.at(-1)!.session_id;
    renderAt(`/agents/${sid}?at=${(snapshot.server_time - 600) * 1000}`);
    expect(await screen.findByTestId("factory-agent-page")).toBeInTheDocument();
    expect(screen.queryByTestId("factory-agent-profile")).toBeNull();
    expect(location).toContain("at=");
  });
});
