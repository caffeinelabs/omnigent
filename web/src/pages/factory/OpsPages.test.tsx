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

describe("two clicks from a symptom to the lever", () => {
  it("goes from the Overview's repeating-IC chip to the Agent page and interrupts it", async () => {
    serve();
    renderAt("");
    let clicks = 0;
    const chip = await screen.findByTestId("needs-issue-agent-INC-13");
    expect(chip).toHaveTextContent("ic:route-r2");
    fireEvent.click(chip);
    clicks += 1;
    expect(location).toBe(`${BASE}/agents/${IC_ROUTE_R2}`);
    const interrupt = await screen.findByTestId("agent-interrupt");
    expect(screen.getByTestId("agent-header")).toHaveTextContent("same failing command 7×");
    fireEvent.click(interrupt);
    clicks += 1;
    await waitFor(() => expect(server.controls).toHaveLength(1));
    expect(server.controls[0]).toEqual({ action: "interrupt", target: `member:${IC_ROUTE_R2}` });
    expect(clicks).toBe(2);
  });
});

describe("AgentsPage", () => {
  it("lists the fleet grouped by project and narrows it by the query", async () => {
    serve();
    renderAt("/agents");
    expect(await screen.findByTestId("fleet-table")).toBeInTheDocument();
    expect(screen.getByTestId("fleet-counts")).toHaveTextContent("1 dead");
    expect(screen.getByTestId("fleet-group-project:db-version")).toBeInTheDocument();
    fireEvent.change(screen.getByTestId("fleet-query"), {
      target: { value: "state:running tool:Bash cost>2 project:db-*" },
    });
    await waitFor(() => expect(screen.getAllByTestId(/^fleet-row-/)).toHaveLength(1));
    expect(screen.getByTestId(`fleet-row-${IC_ROUTE_R2}`)).toBeInTheDocument();
    expect(location).toContain("q=state%3Arunning");
  });

  it("toggles a saved view into the query", async () => {
    serve();
    renderAt("/agents");
    fireEvent.click(await screen.findByTestId("fleet-view-looping"));
    await waitFor(() => expect(screen.getByTestId("fleet-query")).toHaveValue("is:looping"));
    expect(screen.getByTestId(`fleet-row-${IC_ROUTE_R2}`)).toBeInTheDocument();
  });

  it("bulk-interrupts the selected agents behind a confirmation", async () => {
    serve();
    renderAt("/agents?q=task:T-4");
    await screen.findByTestId(`fleet-row-${IC_ROUTE_R2}`);
    for (const row of screen.getAllByTestId(/^fleet-row-/)) {
      fireEvent.click(within(row).getByRole("checkbox"));
    }
    expect(screen.getByTestId("fleet-bulk-bar")).toHaveTextContent("3 agents selected");
    fireEvent.click(screen.getByTestId("fleet-bulk-redispatch"));
    const dialog = await screen.findByTestId("intervention-dialog");
    expect(within(dialog).getByTestId("intervention-targets")).toHaveTextContent("T-4");
    fireEvent.click(within(dialog).getByTestId("intervention-confirm"));
    await waitFor(() => expect(server.controls).toHaveLength(1));
    expect(server.controls[0]).toEqual({ action: "redispatch", target: "task:T-4" });
  });

  it("shows Show on Timeline for a selection", async () => {
    serve();
    renderAt("/agents?q=flag:repeat");
    const row = await screen.findByTestId(`fleet-row-${IC_ROUTE_R2}`);
    fireEvent.click(within(row).getByRole("checkbox"));
    fireEvent.click(screen.getByTestId("fleet-bulk-timeline"));
    expect(location).toBe(`${BASE}/timeline?agents=${IC_ROUTE_R2}`);
  });

  it("disables every lever for a viewer", async () => {
    serve("viewer");
    renderAt("/agents?q=flag:repeat");
    const row = await screen.findByTestId(`fleet-row-${IC_ROUTE_R2}`);
    fireEvent.click(within(row).getByRole("checkbox"));
    for (const kind of ["message", "interrupt", "redispatch", "reassign"]) {
      expect(screen.getByTestId(`fleet-bulk-${kind}`)).toBeDisabled();
    }
  });
});

describe("AgentPage", () => {
  it("shows the now line, the issue's runbook, tabs and a composer that messages as the viewer", async () => {
    serve();
    renderAt(`/agents/${IC_ROUTE_R2}`);
    const now = await screen.findByTestId("agent-now");
    expect(now).toHaveTextContent("Bash pnpm -F caffeine-api test");
    expect(screen.getByTestId("agent-meta")).toHaveTextContent("attempt 3/3 ⛔");
    expect(screen.getByTestId("agent-issue-INC-13")).toBeInTheDocument();
    expect(screen.getByTestId("agent-panel-live")).toHaveTextContent(
      'relation "knex_migrations" does not exist',
    );
    fireEvent.click(screen.getByTestId("agent-tab-mail"));
    expect(await screen.findByTestId("agent-panel-mail")).toHaveTextContent(
      "re-run with knex seed",
    );
    expect(location).toContain("tab=mail");
    fireEvent.change(screen.getByTestId("agent-composer-input"), {
      target: { value: "seed it in setup" },
    });
    fireEvent.click(screen.getByTestId("agent-composer-send"));
    await waitFor(() => expect(server.controls).toHaveLength(1));
    expect(server.controls[0]).toEqual({
      action: "message",
      target: `member:${IC_ROUTE_R2}`,
      note: "seed it in setup",
    });
  });

  it("marks the moment the Timeline opened it at", async () => {
    serve();
    renderAt(`/agents/${IC_ROUTE_R2}?tab=tools&at=${(1793017800 - 7 * 60) * 1000}`);
    expect(await screen.findByTestId("agent-at")).toBeInTheDocument();
    const marked = screen
      .getAllByTestId("agent-tool-call")
      .filter((li) => li.className.includes("ring-1"));
    expect(marked).toHaveLength(1);
  });

  it("says when the agent isn't in this factory", async () => {
    serve("creator", {
      [`GET ${SNAPSHOT}/agents/00000000000000000000000000000000`]: () =>
        json({ error: { code: "not_found", message: "agent not found" } }, 404),
    });
    renderAt("/agents/00000000000000000000000000000000");
    expect(await screen.findByTestId("agent-missing")).toBeInTheDocument();
  });
});

describe("Alerts and the incident page", () => {
  it("lists open incidents and acknowledges one", async () => {
    const inc13 = round3IssueList()[0]!;
    serve("creator", {
      [`POST ${SNAPSHOT}/issues/INC-13/ack`]: () =>
        json({ issue: { ...inc13, status: "acked", acked_by: "luka@caffeine.ai" } }),
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

  it("switches to findings", async () => {
    serve();
    renderAt("/alerts?class=finding");
    expect(await screen.findByTestId("issue-card-F-7")).toBeInTheDocument();
  });

  it("disables lifecycle and runbook levers for a viewer", async () => {
    serve("viewer");
    renderAt("/alerts");
    const card = await screen.findByTestId("issue-card-INC-13");
    expect(within(card).getByTestId("issue-ack-INC-13")).toBeDisabled();
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
    expect(screen.getByTestId("incident-propose")).toBeDisabled();
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

describe("Loop UI", () => {
  it("draws the PR's loop strip with attempts, budget and remedies", async () => {
    serve();
    renderAt(`/prs/${PR_9631}`);
    const strip = await screen.findByTestId("pr-loop-strip");
    const impl = within(strip).getByTestId("loop-line-implement");
    expect(impl).toHaveAttribute("data-state", "exhausted");
    expect(within(impl).getByTestId("loop-state-implement")).toHaveTextContent("⛔ exhausted 3/3");
    expect(within(strip).getByTestId("loop-line-review")).toHaveAttribute(
      "data-state",
      "converged",
    );
    expect(within(strip).getByTestId("loop-flake")).toHaveTextContent("F-7");
  });

  it("shows the project's loops on its Loops tab", async () => {
    serve();
    renderAt("/projects/db-version#loops");
    const loops = await screen.findByTestId("project-loops");
    expect(within(loops).getByTestId("project-loops-pr-9631")).toBeInTheDocument();
    expect(within(loops).getByTestId("project-loops-rest")).toHaveTextContent("plan");
  });
});
