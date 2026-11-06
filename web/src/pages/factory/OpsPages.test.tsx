import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { configure, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  IC_ROUTE_R2,
  PR_9631,
  opsRoutes,
  round3IssueList,
  spansResponse,
} from "@/components/factory/agents/__fixtures__/opsFixtures";
import { resetFactoryControls } from "@/components/factory/controls/useFactoryControls";
import {
  round3Events,
  round3Snapshot,
} from "@/components/factory/shell/__fixtures__/shellFixtures";
import type { ViewerRole } from "@/lib/factoryApi";
import { FactoryPage } from "./FactoryPage";
import { type FactoryServer, SNAPSHOT, json, serveFactory } from "./testServer";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({ useSessionUpdatesConnected: () => true }));

// These pages chain several reads; a busy machine needs more than the default second.
configure({ asyncUtilTimeout: 5000 });

const FID = round3Snapshot().factory.id;
const BASE = `/factory/${FID}`;

let location = "";
function LocationProbe() {
  const loc = useLocation();
  location = loc.pathname + loc.search + loc.hash;
  return null;
}

let server: FactoryServer;
function serve(role: ViewerRole = "creator", overrides: Record<string, () => Response> = {}) {
  const snapshot = round3Snapshot(role);
  server = serveFactory(authenticatedFetch, {
    snapshot: () => json(snapshot),
    events: () => round3Events(),
    ops: opsRoutes(FID, { snapshot, viewer: role === "viewer" }),
    overrides: {
      [`GET ${SNAPSHOT}/spans`]: () => json(spansResponse()),
      ...overrides,
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
  location = "";
});

describe("AgentPage", () => {
  it("marks the moment the Timeline opened it at", async () => {
    serve();
    renderAt(`/agents/${IC_ROUTE_R2}?tab=tools&at=${(1793017800 - 7 * 60) * 1000}`);
    expect(await screen.findByTestId("agent-at")).toBeInTheDocument();
    const marked = screen
      .getAllByTestId("agent-tool-call")
      .filter((li) => li.className.includes("ring-1"));
    expect(marked).toHaveLength(1);
  });
});

describe("Alerts and the incident page", () => {
  it("lists open incidents and acknowledges one", async () => {
    const inc13 = round3IssueList()[0]!;
    serve("creator", {
      [`POST ${SNAPSHOT}/issues/INC-13/ack`]: () =>
        json({ issue: { ...inc13, status: "acked", acked_by: "lena@example.com" } }),
    });
    renderAt("/alerts");
    const card = await screen.findByTestId("issue-card-INC-13");
    expect(screen.getByTestId("issue-card-INC-12")).toHaveAttribute("data-severity", "critical");
    expect(within(card).getByTestId("runbook-INC-13-interrupt")).toBeInTheDocument();
    fireEvent.click(within(card).getByTestId("issue-ack-INC-13"));
    await waitFor(() =>
      expect(
        server.calls.some((c) => c.method === "POST" && c.path.endsWith("/issues/INC-13/ack")),
      ).toBe(true),
    );
  });

  it("switches to findings, a flaky-CI one linking its PR", async () => {
    serve();
    renderAt("/alerts?class=finding");
    expect(await screen.findByTestId("issue-card-F-7")).toBeInTheDocument();
    // The server names the head (`pr:<id>@<sha7>`); the link still finds the PR.
    expect(screen.getByTestId("issue-target-F-7")).toHaveTextContent("!9631");
    expect(screen.getByTestId("issue-target-F-7")).toHaveAttribute(
      "href",
      `${BASE}/prs/${PR_9631}`,
    );
  });

  it("links a finding's evidence: the event on the Timeline, the head on its PR", async () => {
    serve();
    renderAt("/alerts/F-7");
    const evidence = await screen.findByTestId("incident-evidence");
    expect(within(evidence).getByText("!9631@5e1c2d4")).toHaveAttribute(
      "href",
      `${BASE}/prs/${PR_9631}`,
    );
    expect(within(evidence).getByText(/gate\.changed/)).toHaveAttribute(
      "href",
      expect.stringMatching(new RegExp(`^${BASE}/timeline\\?focus=db-version&at=\\d+$`)),
    );
  });

  it("disables lifecycle and runbook levers for a viewer", async () => {
    serve("viewer");
    renderAt("/alerts");
    const card = await screen.findByTestId("issue-card-INC-13");
    expect(within(card).getByTestId("issue-ack-INC-13")).toBeDisabled();
    expect(within(card).getByTestId("issue-assign-INC-13")).toBeDisabled();
    expect(within(card).getByTestId("issue-snooze-INC-13")).toBeDisabled();
    expect(within(card).getByTestId("issue-resolve-INC-13")).toBeDisabled();
    expect(within(card).getByTestId("runbook-INC-13-interrupt")).toBeDisabled();
  });

  it("shows the steps before an incident and resolves it with a confirmed root cause", async () => {
    const inc13 = round3IssueList()[0]!;
    serve("creator", {
      [`POST ${SNAPSHOT}/issues/INC-13/resolve`]: () =>
        json({ issue: { ...inc13, status: "resolved", category: "env_flaky" } }),
    });
    renderAt("/alerts/13");
    expect(await screen.findByTestId("factory-incident-page")).toHaveAttribute(
      "data-ref",
      "INC-13",
    );
    expect(screen.getByTestId("incident-swimlanes")).toBeInTheDocument();
    expect(screen.getByTestId("incident-events")).toHaveTextContent("dispatched route-r2");
    expect(screen.getByTestId("incident-propose")).toBeEnabled();
    fireEvent.click(screen.getByTestId("incident-cause-env_flaky"));
    fireEvent.click(screen.getByTestId("incident-confirm-cause"));
    await waitFor(() => {
      const call = server.calls.find(
        (c) => c.method === "POST" && c.path.endsWith("/issues/INC-13/resolve"),
      );
      expect(call?.body).toEqual({ root_cause: "env_flaky" });
    });
  });
});

describe("TimelinePage", () => {
  it("draws the PL, IC and env lanes with waits and r0..rk, and a bar opens the agent", async () => {
    serve();
    renderAt("/timeline?window=6h");
    const view = await screen.findByTestId("timeline-view");
    const db = within(view).getByTestId("timeline-group-db-version");
    expect(
      within(db)
        .getAllByTestId("timeline-bar-attempt")
        .map((b) => b.textContent),
    ).toEqual(["r0 ✗", "r1 ✗", "r2 ✗"]);
    expect(within(db).getAllByTestId("timeline-bar-wait").length).toBeGreaterThan(0);
    expect(within(db).getAllByTestId("timeline-lane-env")).toHaveLength(1);
    fireEvent.click(within(db).getAllByTestId("timeline-bar-tool")[0]!);
    expect(location).toMatch(new RegExp(`^${BASE}/agents/${IC_ROUTE_R2}\\?at=\\d+$`));
  });

  it("replays as of a moment from the scrubber", async () => {
    serve();
    renderAt("/timeline?window=6h");
    await screen.findByTestId("timeline-view");
    fireEvent.click(screen.getByTestId("timeline-replay-back"));
    await waitFor(() => expect(location).toContain("at="));
    expect(screen.getByTestId("timeline-scope")).toHaveTextContent("Replaying as of");
    fireEvent.click(screen.getByTestId("timeline-now"));
    await waitFor(() => expect(location).not.toContain("at="));
  });
});
