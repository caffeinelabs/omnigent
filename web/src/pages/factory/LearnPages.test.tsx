import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { configure, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { opsRoutes, spansResponse } from "@/components/factory/agents/__fixtures__/opsFixtures";
import { resetFactoryControls } from "@/components/factory/controls/useFactoryControls";
import type { StoryRoutes } from "@/components/factory/factoryStoryFixtures";
import {
  type LearnRouteOptions,
  learnProposals,
  learnRoutes,
} from "@/components/factory/insights/__fixtures__/learnFixtures";
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
/** Learning reads that can switch state mid-test (e.g. after a save). */
let learn: LearnRouteOptions = {};

function serve(
  role: ViewerRole = "creator",
  overrides: Record<string, () => Response> = {},
  options: LearnRouteOptions = {},
) {
  learn = options;
  const snapshot = round3Snapshot(role);
  const ops = opsRoutes(FID, { snapshot, viewer: role === "viewer" });
  const routes = new Proxy({} as StoryRoutes, {
    get: (_target, key: string) =>
      learnRoutes(FID, { owner: role === "creator" || role === "owner", ...learn })[key] ??
      ops[key] ??
      (key === `${SNAPSHOT}/alerts/routing`
        ? { body: { cockpit: true, browser: true, slack: false } }
        : undefined),
  });
  server = serveFactory(authenticatedFetch, {
    snapshot: () => json(snapshot),
    events: () => round3Events(),
    ops: routes,
    overrides: {
      [`GET ${SNAPSHOT}/spans`]: () => json(spansResponse()),
      "POST /v1/factory/feedback": () => json({ feedback: { id: "fb1" } }, 201),
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

function sent(method: string, path: string): unknown[] {
  return server.calls
    .filter((c) => c.method === method && c.path.split("?")[0] === path)
    .map((c) => c.body);
}

beforeEach(() => {
  authenticatedFetch.mockReset();
  resetFactoryControls();
  location = "";
  learn = {};
});

// ── Insights ───────────────────────────────────────────────────

describe("Insights", () => {
  it("shows every KPI with n, a delta against the previous window and version markers", async () => {
    serve();
    renderAt("/insights");

    const tile = await screen.findByTestId("kpi-merge_acceptance");
    expect(tile).toHaveAttribute("data-n-state", "ok");
    expect(within(tile).getByTestId("kpi-merge_acceptance-value")).toHaveTextContent("11 · 79%");
    expect(within(tile).getByTestId("kpi-merge_acceptance-n")).toHaveTextContent("n=14");
    expect(within(tile).getByTestId("kpi-merge_acceptance-delta")).toHaveTextContent("+6 pp");
    expect(screen.getByTestId("kpi-lead_time-delta")).toHaveTextContent("−41m");
    expect(screen.getByTestId("kpi-human_prompts_per_day")).toHaveTextContent(
      "band 30–50 · L58 P36 O6",
    );
    expect(await screen.findByTestId("insights-mark-def-7")).toHaveTextContent("def v7");
    expect(screen.getByTestId("insights-mark-img-ver-0bb15c1")).toHaveTextContent("img 0bb15c1");
    expect(await screen.findByTestId("insights-flow")).toBeInTheDocument();
    expect(screen.getByTestId("insights-where-rows-api-version")).toBeInTheDocument();
    expect(screen.getByTestId("insights-people-table")).toHaveTextContent("mia@example.com");
  });

  it("calls small samples inconclusive and hides their deltas", async () => {
    serve("creator", {}, { kind: "small" });
    renderAt("/insights");

    const tile = await screen.findByTestId("kpi-merge_acceptance");
    expect(tile).toHaveAttribute("data-n-state", "small");
    expect(within(tile).getByTestId("kpi-merge_acceptance-small-n")).toHaveTextContent(
      "n=2 — inconclusive",
    );
    expect(within(tile).queryByTestId("kpi-merge_acceptance-delta")).toBeNull();
  });

  it("says no data at n=0", async () => {
    serve("creator", {}, { kind: "empty" });
    renderAt("/insights");

    const tile = await screen.findByTestId("kpi-lead_time");
    expect(tile).toHaveAttribute("data-n-state", "none");
    expect(within(tile).getByTestId("kpi-lead_time-small-n")).toHaveTextContent("no data yet");
    expect(await screen.findByTestId("insights-empty")).toBeInTheDocument();
  });

  it("keeps per-person numbers for owners and the propose lever from viewers", async () => {
    serve("viewer");
    renderAt("/insights");

    expect(await screen.findByTestId("insights-people-owners-only")).toBeInTheDocument();
    expect(screen.queryByTestId("insights-people-table")).toBeNull();
    expect(screen.getByTestId("kpi-merge_acceptance-propose")).toBeDisabled();
  });

  it("marks small groups inconclusive in the group-by table", async () => {
    serve();
    renderAt("/insights?group=project");

    const table = await screen.findByTestId("insights-group-table");
    await waitFor(() => expect(table.querySelector("[data-small-n]")).not.toBeNull());
  });

  it("measures one definition version when its marker is pressed", async () => {
    serve();
    renderAt("/insights");

    fireEvent.click(await screen.findByTestId("insights-mark-def-7"));

    await waitFor(() => expect(location).toContain("def=7"));
    expect(await screen.findByTestId("insights-def-filter")).toHaveTextContent(
      "Measuring definition v7 only",
    );
    await waitFor(() =>
      expect(
        server.calls.some((c) => c.path.includes("/insights?") && c.path.includes("def_version=7")),
      ).toBe(true),
    );
  });
});

// ── Settings → History ─────────────────────────────────────────

// ── Improvements ───────────────────────────────────────────────

describe("Improvements", () => {
  it("records an idle trial's extensions on its page", async () => {
    serve("creator", {}, { idle: true });
    renderAt("/improvements/C-13");

    const record = await screen.findByTestId("proposal-record");
    expect(record).toHaveTextContent("Extended2× by its own length: nothing merged or finished");
    expect(record).toHaveTextContent("Stays appliedIn 22h if nobody answers — nothing got worse");
    expect(await screen.findByTestId("proposal-C-13-scorecard-table-verdict")).toHaveTextContent(
      "idle · no merged PRs or finished tasks",
    );
    // Its extensions are used up: the hint says what happens now, not the general rule.
    expect(screen.getByTestId("proposal-C-13-scorecard-table-hint")).toHaveTextContent(
      "extended twice; the owners decide now — it stays applied if nobody answers in 22h",
    );
  });

  it("shows a regressed guard on an automatically reverted change", async () => {
    serve();
    renderAt("/improvements/C-12");

    const card = await screen.findByTestId("proposal-C-12");
    const guard = await within(card).findByTestId(
      "proposal-C-12-scorecard-guard-human_prompts_per_day",
    );
    expect(guard).toHaveAttribute("data-status", "regressed");
    expect(within(card).getByTestId("proposal-C-12-scorecard-table-verdict")).toHaveTextContent(
      "reverted · guard regressed",
    );
  });

  it("shows the reject note on a rejected change", async () => {
    const rejected = {
      ...learnProposals().find((p) => p.ref === "C-15")!,
      status: "rejected" as const,
      decided_by: "lena@example.com",
      result: { note: "Plan human-only steps in the charter instead" },
    };
    serve("creator", {
      [`GET ${SNAPSHOT}/proposals/C-15`]: () => json({ proposal: rejected }),
    });
    renderAt("/improvements/C-15");

    expect(await screen.findByTestId("proposal-C-15-outcome")).toHaveTextContent(
      "Rejected by lena: “Plan human-only steps in the charter instead”",
    );
    expect(screen.queryByTestId("proposal-C-15-reject")).toBeNull();
  });

  it("names the member who decided, not their session id", async () => {
    serve();
    renderAt("/improvements/C-14");

    const record = await screen.findByTestId("proposal-record");
    await waitFor(() => expect(record).toHaveTextContent(/Decided\s*Atlas/));
    expect(record).not.toHaveTextContent(/[0-9a-f]{32}/);
  });
});

// ── Feedback round trips ───────────────────────────────────────

describe("feedback", () => {
  it("flags a question as unnecessary", async () => {
    serve();
    renderAt("/decisions");

    const [button] = await screen.findAllByTestId("decision-unneeded-D-2");
    fireEvent.click(button!);

    await waitFor(() =>
      expect(sent("POST", "/v1/factory/feedback")).toEqual([
        expect.objectContaining({
          target_kind: "decision",
          target_id: "a9d44647df3050199c1577ba21f16b67",
          kind: "unneeded",
          project: "db-version",
        }),
      ]),
    );
  });
});
