import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { configure, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LIVE_RUN_FACTORY_ID, preflightOk } from "@/components/factory/__fixtures__/liveRun";
import { opsRoutes } from "@/components/factory/agents/__fixtures__/opsFixtures";
import { resetFactoryControls } from "@/components/factory/controls/useFactoryControls";
import { notifyMe } from "@/components/factory/notify/__fixtures__/notifyFixtures";
import { round3Snapshot } from "@/components/factory/shell/__fixtures__/shellFixtures";
import { COCKPIT_STORAGE_KEY, resetCockpitChoiceCache } from "@/components/factory/shell/cockpitV2";
import {
  type SteerRouteOptions,
  previewFocus,
  previewOperator,
  previewRestore,
  previewRubric,
  steerRoutes,
} from "@/components/factory/steer/__fixtures__/steerFixtures";
import { resetSteerStaging } from "@/components/factory/steer/steerStaging";
import type { DefinitionPreviewResponse, ViewerRole } from "@/lib/factoryApi";
import { FactoryPage } from "./FactoryPage";
import { type FactoryServer, SNAPSHOT, json, serveFactory } from "./testServer";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({ useSessionUpdatesConnected: () => true }));
configure({ asyncUtilTimeout: 5000 });

const FID = LIVE_RUN_FACTORY_ID;
const BASE = `/factory/${FID}`;

let location = "";
function LocationProbe() {
  const loc = useLocation();
  location = loc.pathname + loc.search + loc.hash;
  return null;
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

let server: FactoryServer;

function serve(
  options: SteerRouteOptions & {
    role?: ViewerRole;
    /** The preview for what the last preview call sent (a restore, else changes). */
    preview?: DefinitionPreviewResponse;
  } = {},
) {
  const snapshot = round3Snapshot(options.role ?? "creator");
  server = serveFactory(authenticatedFetch, {
    snapshot: () => json(snapshot),
    ops: { ...opsRoutes(FID, { snapshot }), ...steerRoutes(FID, options) },
    overrides: {
      "GET /v1/factory/preflight": () => json({ ...preflightOk(), cockpit_v2: true }),
      [`GET ${SNAPSHOT}/notify/me/ui`]: () => json({ cockpit: "v2" }),
      [`GET ${SNAPSHOT}/notify/me`]: () => json(notifyMe()),
      [`POST ${SNAPSHOT}/definition/preview`]: () => {
        const last = server.calls.at(-1)!.body as { restore?: number };
        return json(last.restore != null ? previewRestore() : (options.preview ?? previewFocus()));
      },
      [`PUT ${SNAPSHOT}/definition`]: () =>
        json({ proposal: "C-40", proposal_id: "p40", status: "applied", def_version: 13 }),
      [`POST ${SNAPSHOT}/definition/pin`]: () =>
        json({ def_version: 12, changed: true, from: 13, ended: [] }),
      [`POST ${SNAPSHOT}/definition/restore`]: () =>
        json({
          proposal: "C-41",
          proposal_id: "p41",
          status: "applied",
          def_version: 13,
          restored: 10,
          from: 12,
          ended: ["C-33"],
        }),
      [`POST ${SNAPSHOT}/definition/proposals`]: () =>
        json({ proposal: { ref: "C-42", id: "p42", number: 42 }, decision: "D-9" }, 201),
      [`POST ${SNAPSHOT}/proposals/C-35/apply`]: () =>
        json({ proposal: { ref: "C-35", def_version_to: 13 }, decision: {} }),
      [`POST ${SNAPSHOT}/sources/forum/pause`]: () =>
        json({ changed: true, state: "paused", def_version: 13, undo: { pin: 12, until: 0 } }),
    },
  });
}

function sent(method: string, suffix: string): unknown[] {
  return server.calls
    .filter((c) => c.method === method && c.path.endsWith(suffix))
    .map((c) => c.body);
}

beforeEach(() => {
  authenticatedFetch.mockReset();
  resetFactoryControls();
  resetSteerStaging();
  resetCockpitChoiceCache();
  localStorage.clear();
  localStorage.setItem(COCKPIT_STORAGE_KEY, "v2");
  location = "";
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Steer", () => {
  it("opens on the Overview with the outline's groups and each section's value", async () => {
    serve();
    renderAt("/steer");

    await waitFor(() => expect(location).toBe(`${BASE}/steer/overview`));
    const outline = await screen.findByTestId("steer-outline");
    expect(within(outline).getByTestId("steer-nav-strategy")).toHaveTextContent(
      "1 goal · focus New users",
    );
    await waitFor(() =>
      expect(within(outline).getByTestId("steer-nav-sources")).toHaveTextContent("1 waits for you"),
    );
    expect(within(outline).getByTestId("steer-nav-rubric")).toHaveTextContent("on trial");
    expect(await screen.findByTestId("steer-stage-sense")).toBeInTheDocument();
    expect(screen.getByTestId("steer-waiting")).toHaveTextContent("Approve a bet");
    expect(screen.getByTestId("steer-version")).toHaveTextContent("definition v12");
  });

  it("toggles a focus segment, shows the re-rank, applies one version, and Undo pins the one before", async () => {
    serve();
    renderAt("/steer/strategy");

    fireEvent.click(await screen.findByTestId("steer-focus-active-builders"));

    const column = screen.getByTestId("steer-change-column");
    expect(await within(column).findByText("Staged for definition v13")).toBeInTheDocument();
    expect(await within(column).findByTestId("rerank-O-7")).toHaveTextContent("Next → Now");
    expect(within(column).getByTestId("steer-trial-rule")).toHaveTextContent(
      "No trial for strategy.",
    );
    expect(within(column).getByTestId("steer-who")).toHaveTextContent(
      "You are an owner, so it applies when you press Apply. Restore v12 any time.",
    );
    expect(screen.getByTestId("steer-nav-strategy")).toHaveAttribute("data-staged", "true");
    expect(within(column).queryByTestId("steer-apply-trial")).toBeNull();

    fireEvent.click(within(column).getByTestId("steer-apply"));

    await waitFor(() =>
      expect(sent("PUT", "/definition")).toEqual([
        {
          changes: [
            {
              op: "replace",
              path: "strategy.focus_segments",
              value: ["new-users", "active-builders"],
            },
          ],
          base_version: 12,
        },
      ]),
    );
    const toast = await screen.findByTestId("factory-toast");
    expect(toast).toHaveTextContent("Applied as definition v13.");
    expect(await screen.findByTestId("steer-staged-empty")).toBeInTheDocument();
    fireEvent.click(within(toast).getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(sent("POST", "/definition/pin")).toEqual([{ version: 12 }]));
    expect(await screen.findByTestId("factory-toast")).toHaveTextContent(
      "Undone. v12 is in force again; v13 stays in history.",
    );
  });

  it("refuses a second trial on the block a running trial holds, and locks its field", async () => {
    serve({ preview: previewRubric() });
    renderAt("/steer/rubric");

    expect(await screen.findByTestId("trial-lock-C-33")).toHaveTextContent(
      "Locked while C-33 runs",
    );
    fireEvent.change(screen.getByTestId("steer-slider-discovery.rubric.off_focus_factor"), {
      target: { value: "0.8" },
    });

    const rule = await screen.findByTestId("steer-trial-rule");
    await waitFor(() => expect(rule).toHaveAttribute("data-code", "trial_overlap"));
    expect(rule).toHaveTextContent("No second trial on discovery.");
    expect(rule).toHaveTextContent("Apply directly, or wait for C-33 to end.");
    expect(screen.queryByTestId("steer-apply-trial")).toBeNull();
    expect(screen.getByTestId("steer-running-trials")).toHaveTextContent("C-33");
  });

  it("stages a restore with its diff and applies it as a new version", async () => {
    serve();
    renderAt("/steer/versions");

    expect(await screen.findByTestId("steer-version-v11")).toHaveTextContent("undone by mia");
    fireEvent.click(screen.getByTestId("steer-restore-v10"));

    const restore = await screen.findByTestId("steer-staged-restore");
    expect(within(restore).getByText("Restore v10")).toBeInTheDocument();
    expect(
      await within(restore).findByText("discovery.rubric.decay_half_life_days: 45 → 60"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("steer-effects")).toHaveTextContent("Ends trial C-33");
    expect(screen.getByTestId("steer-trial-rule")).toHaveTextContent("No trial for a restore.");

    await waitFor(() => expect(screen.getByTestId("steer-apply")).not.toBeDisabled());
    fireEvent.click(screen.getByTestId("steer-apply"));
    await waitFor(() =>
      expect(sent("POST", "/definition/restore")).toEqual([{ version: 10, base_version: 12 }]),
    );
    expect(await screen.findByTestId("factory-toast")).toHaveTextContent(
      "Restored v10 as definition v13.",
    );
  });

  it("stages an agent's proposal and Apply answers it", async () => {
    serve();
    renderAt("/steer/roles?role=scout");

    fireEvent.click(await screen.findByTestId("steer-proposal-stage-C-35"));
    expect(await screen.findByTestId("steer-staged-proposal")).toHaveTextContent(
      "From C-35 (Sentinel). Apply answers it.",
    );
    expect(screen.getByTestId("steer-field-briefs.scout")).toHaveAttribute("data-staged", "true");
    const apply = screen.getByTestId("steer-apply");
    expect(apply).toHaveTextContent("Apply C-35 as v13");
    await waitFor(() => expect(apply).not.toBeDisabled());
    fireEvent.click(apply);
    await waitFor(() => expect(sent("POST", "/proposals/C-35/apply")).toHaveLength(1));
    expect(sent("PUT", "/definition")).toEqual([]);
  });

  it("lets an operator stage a change and propose it to the owners", async () => {
    serve({ role: "operator", preview: previewOperator() });
    renderAt("/steer/budgets");

    fireEvent.change(await screen.findByTestId("steer-input-budgets.factory_usd_per_day"), {
      target: { value: "300" },
    });
    fireEvent.click(await screen.findByTestId("steer-propose"));
    fireEvent.change(screen.getByTestId("steer-rationale"), {
      target: { value: "More builds this week (sample)." },
    });
    fireEvent.click(screen.getByTestId("steer-propose-send"));
    await waitFor(() =>
      expect(sent("POST", "/definition/proposals")).toEqual([
        {
          kind: "tuning",
          changes: [{ op: "replace", path: "budgets.factory_usd_per_day", value: 300 }],
          rationale: "More builds this week (sample).",
        },
      ]),
    );
    expect(screen.queryByTestId("steer-apply")).toBeNull();
  });

  it("keeps a decimal being typed while it stages each step", async () => {
    serve();
    renderAt("/steer/budgets");

    const input = await screen.findByTestId("steer-input-budgets.usd_per_hour_alert");
    fireEvent.change(input, { target: { value: "12." } });
    expect(input).toHaveValue("12.");
    fireEvent.change(input, { target: { value: "12.5" } });
    expect(input).toHaveValue("12.5");
    expect(await screen.findByTestId("steer-staged-budgets.usd_per_hour_alert")).toHaveTextContent(
      "12.5",
    );
  });

  it("shows a viewer every value and no lever", async () => {
    serve({ role: "viewer" });
    renderAt("/steer/strategy");

    expect(await screen.findByTestId("steer-focus-new-users")).toBeDisabled();
    expect(screen.getByTestId("steer-staged-empty")).toHaveTextContent(
      "You can look, not change. Ask an operator or an owner to change this.",
    );
    expect(screen.queryByTestId("steer-add-goal-open")).toBeNull();
    expect(screen.queryByTestId("steer-proposal-stage-C-32")).toBeNull();
  });

  it("hides sources and the rubric while discovery is off, and stages the starter from the Overview", async () => {
    serve({ discovery: false });
    renderAt("/steer/sources");

    await waitFor(() => expect(location).toBe(`${BASE}/steer/overview`));
    const outline = await screen.findByTestId("steer-outline");
    expect(within(outline).queryByTestId("steer-nav-sources")).toBeNull();
    expect(within(outline).queryByTestId("steer-nav-rubric")).toBeNull();
    expect(within(outline).getByTestId("steer-nav-strategy")).toBeInTheDocument();
    expect(within(outline).getByTestId("steer-nav-product")).toBeInTheDocument();

    fireEvent.click(await screen.findByTestId("steer-starter-stage"));
    const column = screen.getByTestId("steer-change-column");
    expect(await within(column).findByTestId("steer-staged-discovery.enabled")).toBeInTheDocument();
    expect(within(column).getByTestId("steer-staged-discovery.sources+repo")).toHaveTextContent(
      "The factory's repos",
    );
  });

  it("pauses a source at once, with Undo", async () => {
    serve();
    renderAt("/steer/sources");

    expect(await screen.findByTestId("steer-source-waiting-C-36")).toHaveTextContent(
      "waits for an owner",
    );
    fireEvent.click(await screen.findByTestId("steer-source-pause-forum"));
    await waitFor(() => expect(sent("POST", "/sources/forum/pause")).toHaveLength(1));
    const toast = await screen.findByTestId("factory-toast");
    expect(toast).toHaveTextContent("Paused Public community forum (sample)");
    fireEvent.click(within(toast).getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(sent("POST", "/definition/pin")).toEqual([{ version: 12 }]));
  });

  it("adds a source in four steps and stages it", async () => {
    serve();
    renderAt("/steer/sources");

    fireEvent.click(await screen.findByTestId("steer-add-source-open"));
    const sheet = await screen.findByTestId("steer-add-source");
    expect(within(sheet).getByLabelText(/Issue tracker/)).toBeDisabled();
    for (const step of [2, 3, 4]) {
      fireEvent.click(within(sheet).getByTestId("steer-add-source-next"));
      // Each step shows only after the last one.
      // oxlint-disable-next-line no-await-in-loop
      await waitFor(() => expect(sheet).toHaveAttribute("data-step", String(step)));
    }
    expect(within(sheet).getByTestId("steer-add-source-words")).toHaveTextContent(
      "It starts with a 14-day trial.",
    );
    fireEvent.click(within(sheet).getByTestId("steer-add-source-stage"));
    const column = screen.getByTestId("steer-change-column");
    expect(await within(column).findByText(/Source: Code host issues/)).toBeInTheDocument();
  });

  it.each([
    "strategy",
    "product",
    "sources",
    "rubric",
    "roles",
    "templates",
    "gates",
    "budgets",
    "alerts",
    "cadence",
    "versions",
    "overview",
  ])("has no axe violations on %s with a change staged", async (section) => {
    serve();
    renderAt(`/steer/strategy`);
    fireEvent.click(await screen.findByTestId("steer-focus-active-builders"));
    await screen.findByTestId("rerank-O-7");
    if (section !== "strategy") {
      fireEvent.click(screen.getByTestId(`steer-nav-${section}`));
      await waitFor(() => expect(location).toBe(`${BASE}/steer/${section}`));
      await screen.findByTestId("steer-section");
    }
    const options: axe.RunOptions = {
      // jsdom paints nothing (contrast is checked in the browser over Storybook).
      rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
    };
    const page = await axe.run(screen.getByTestId("factory-steer-page"), options);
    expect(page.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
  });

  it("shows the gates read only", async () => {
    serve();
    renderAt("/steer/gates");

    expect(await screen.findAllByTestId("steer-gate-review")).not.toHaveLength(0);
    expect(screen.getByTestId("steer-gates-table")).toHaveTextContent("Tier 0 · read only");
    expect(screen.queryByTestId("steer-apply")).toBeNull();
  });
});
