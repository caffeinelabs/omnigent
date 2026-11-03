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
  STOPPED_SLUG,
  charterVersions,
  projectV2Routes,
  projectsV2Snapshot,
} from "@/components/factory/project/__fixtures__/projectV2Fixtures";
import { COCKPIT_STORAGE_KEY } from "@/components/factory/shell/cockpitV2";
import { resetTeamStaging } from "@/components/factory/team/teamStaging";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { FactoryPage } from "./FactoryPage";
import { type ServedCall, json, serveFactory } from "./testServer";

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

interface Served {
  calls: ServedCall[];
  snapshot: FactorySnapshot;
  base: string;
}

function serve(
  options: {
    snapshot?: ProjectV2Options;
    cockpitV2?: boolean;
    discovery?: boolean;
    /** Extra answers, `"<METHOD> <path>"` → response, given the factory's API base. */
    overrides?: (base: string, snapshot: FactorySnapshot) => Record<string, () => Response>;
  } = {},
): Served {
  const snapshot = projectsV2Snapshot(options.snapshot);
  const fid = snapshot.factory.id;
  const base = `/v1/factory/factories/${fid}`;
  const server = serveFactory(authenticatedFetch, {
    snapshot: () => json(snapshot),
    ops: {
      ...opsRoutes(fid, { snapshot }),
      ...projectV2Routes(snapshot, { discovery: options.discovery }),
      [`${base}/asks`]: { error: 404 },
    },
    overrides: {
      "GET /v1/factory/preflight": () =>
        json({ ...preflightOk(), cockpit_v2: options.cockpitV2 ?? true }),
      ...options.overrides?.(base, snapshot),
    },
  });
  return { calls: server.calls, snapshot, base };
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

function sent(calls: ServedCall[], method: string, suffix: string): unknown[] {
  return calls.filter((c) => c.method === method && c.path.endsWith(suffix)).map((c) => c.body);
}

beforeEach(() => {
  authenticatedFetch.mockReset();
  resetFactoryControls();
  resetTeamStaging();
  window.localStorage.clear();
  window.localStorage.setItem(COCKPIT_STORAGE_KEY, "v2");
  location = "";
});

describe("the project page (new cockpit)", () => {
  it("leads with the outcome, the facts, why it exists, the team, then four tabs", async () => {
    const { snapshot } = serve({ discovery: true });
    const { container } = renderAt(snapshot, `/projects/${EVIDENCE_SLUG}`);
    const page = await screen.findByTestId(`project-page-${EVIDENCE_SLUG}`);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "(sample) People can export a report as CSV",
    );
    const headline = screen.getByTestId("project-headline");
    expect(headline).toHaveTextContent("sample-org/sample-app → main");
    // The lead said on track; a stuck member makes the facts say at risk.
    const chip = within(headline).getByTestId("project-health");
    expect(chip).toHaveTextContent("At risk");
    expect(chip).toHaveAttribute(
      "title",
      "At risk from the facts: 1 member stuck (the lead said on track)",
    );
    const why = screen.getByTestId("project-why");
    await waitFor(() => expect(why).toHaveTextContent("41 accounts"));
    expect(why).toHaveTextContent("O-3 · moderate");
    expect(why).toHaveTextContent("expects +1 to +4 pp");
    expect(why).toHaveTextContent("day 7 and 28 after release");
    expect(within(page).getAllByRole("region", { name: "Team" })).toHaveLength(1);
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Overview", "Work1 PR", "History", "Charter"]);
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", "project-tab-overview");
    const update = screen.getByTestId("project-latest-update");
    expect(update).toHaveTextContent("(sample) T-3 merged; the export endpoint streams rows");
    expect(update).toHaveTextContent("The facts say at risk: checks green, budget at 42 %");
    expect(screen.getByTestId("project-pr-gates-41")).toHaveTextContent(
      "Checks passed · Review passed · Verify waiting · Demo waiting",
    );
    expect(screen.getByTestId("project-measured")).toHaveTextContent("weekly_exports");
    expect(screen.getByTestId("project-proof")).toHaveTextContent("Demo partly passed");
    const results = await axe.run(container, {
      runOnly: ["landmark-unique", "heading-order", "aria-allowed-attr", "aria-required-children"],
    });
    expect(results.violations.map((v) => v.id)).toEqual([]);
  });

  it("says requested work has no evidence, offers the levers while discovery runs, and its merge", async () => {
    const { snapshot } = serve({ discovery: true, snapshot: { question: true } });
    renderAt(snapshot, `/projects/${REQUESTED_SLUG}?decision=D-41`);
    const why = await screen.findByTestId("project-why");
    expect(why).toHaveAttribute("data-kind", "requested");
    expect(screen.getByTestId("project-why-sentence")).toHaveTextContent(
      "Requested by mia. No user evidence. Not measured.",
    );
    expect(await within(why).findByRole("link", { name: /Link an opportunity/ })).toBeVisible();
    expect(within(why).getByRole("button", { name: /Add a metric/ })).toBeInTheDocument();
    expect(why.querySelectorAll("[data-missing]")).toHaveLength(4);
    expect(screen.getByTestId("project-health")).toHaveTextContent("Needs your merge");
    expect(screen.getByTestId("project-waiting")).toHaveTextContent(
      "(sample) Keep the old theme toggle for a week?",
    );
    const merge = screen.getByTestId("project-ready-merge");
    expect(merge).toHaveTextContent("A person merges every pull request in this repo (sample).");
    expect(within(merge).getByRole("link", { name: /Open on the code host/ })).toHaveAttribute(
      "href",
      "https://example.com/sample-org/sample-app/pull/38",
    );
    expect(screen.getByTestId("project-measured")).toHaveTextContent(
      "It will not be: requested work has no metric.",
    );
  });

  it("names an agent's idea and hides the discovery levers while discovery is off", async () => {
    const { snapshot } = serve();
    renderAt(snapshot, `/projects/${IDEA_SLUG}`);
    expect(await screen.findByTestId("project-why-sentence")).toHaveTextContent(
      "Atlas's idea, counted as a hunch. No user evidence. Not measured.",
    );
    expect(screen.queryByRole("link", { name: /Link an opportunity/ })).toBeNull();
    expect(screen.getByTestId("project-update-none")).toBeInTheDocument();
  });

  it("closes a stopped project after a confirm; its PRs stay open", async () => {
    const { snapshot, calls } = serve({
      overrides: (base) => ({
        [`POST ${base}/projects/${STOPPED_SLUG}/cancel`]: () =>
          json({ project: { slug: STOPPED_SLUG, status: "cancelled" } }),
      }),
    });
    renderAt(snapshot, `/projects/${STOPPED_SLUG}`);
    const card = await screen.findByTestId("project-stopped");
    expect(card).toHaveTextContent("(sample) its lead stopped and could not restart");
    fireEvent.click(within(card).getByTestId("project-close"));
    fireEvent.click(within(card).getByTestId("project-close-confirm"));
    await waitFor(() =>
      expect(sent(calls, "POST", `/projects/${STOPPED_SLUG}/cancel`)).toEqual([
        { reason: "closed from its page" },
      ]),
    );
    expect(await screen.findByText(/Closed search-filters/)).toBeInTheDocument();
  });

  it("opens the tab that holds an old section link, and every tab has its own address", async () => {
    const { snapshot } = serve();
    renderAt(snapshot, `/projects/${EVIDENCE_SLUG}#prs`);
    await screen.findByTestId("project-work");
    expect(location).toBe(`/factory/${snapshot.factory.id}/projects/${EVIDENCE_SLUG}/work#prs`);
    expect(screen.getByTestId("project-tab-work")).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByTestId("project-tab-history"));
    await screen.findByTestId("project-history");
    expect(location).toBe(`/factory/${snapshot.factory.id}/projects/${EVIDENCE_SLUG}/history`);
    expect(await screen.findByTestId("project-charter-history")).toHaveTextContent("C-34");
  });

  it("lands an old #activity link on the activity card of History", async () => {
    const { snapshot } = serve();
    renderAt(snapshot, `/projects/${EVIDENCE_SLUG}#activity`);
    const card = await screen.findByTestId("project-activity");
    expect(location).toBe(
      `/factory/${snapshot.factory.id}/projects/${EVIDENCE_SLUG}/history#activity`,
    );
    expect(document.getElementById("activity")).toBe(card);
  });

  it("lands an old #decisions link on the Overview's Waiting on you card", async () => {
    const { snapshot } = serve({ snapshot: { question: true } });
    renderAt(snapshot, `/projects/${REQUESTED_SLUG}#decisions`);
    const card = await screen.findByTestId("project-waiting");
    expect(location).toBe(`/factory/${snapshot.factory.id}/projects/${REQUESTED_SLUG}#decisions`);
    expect(document.getElementById("decisions")).toBe(card);
  });

  it("keeps the team editor route, and sends a bad tab to the overview", async () => {
    const { snapshot } = serve();
    renderAt(snapshot, `/projects/${EVIDENCE_SLUG}/nowhere`);
    await screen.findByTestId(`project-page-${EVIDENCE_SLUG}`);
    expect(location).toBe(`/factory/${snapshot.factory.id}/projects/${EVIDENCE_SLUG}`);
  });

  it("says in one line that the work comes from user evidence", async () => {
    const { snapshot } = serve();
    renderAt(snapshot, `/projects/${EVIDENCE_SLUG}`);
    expect(await screen.findByTestId("project-why-sentence")).toHaveTextContent(
      /^From user evidence \(O-\d+\)\. (Measured after release|Not measured yet)\.$/,
    );
  });
});

describe("the PR page (new cockpit)", () => {
  it("says why its project's work exists: evidence, or a person's request", async () => {
    const { snapshot } = serve();
    const evidence = snapshot.projects.find((p) => p.slug === EVIDENCE_SLUG)!;
    const requested = snapshot.projects.find((p) => p.slug === REQUESTED_SLUG)!;
    const first = renderAt(snapshot, `/prs/${evidence.pr_ids[0]}`);
    const why = await screen.findByTestId("pr-why");
    expect(why).toHaveAttribute("data-kind", "evidence");
    expect(why).toHaveTextContent(/^from O-\d+/);
    first.unmount();
    renderAt(snapshot, `/prs/${requested.pr_ids[0]}`);
    const asked = await screen.findByTestId("pr-why");
    expect(asked).toHaveAttribute("data-kind", "requested");
    expect(asked).toHaveTextContent("No user evidence · requested by mia · not measured");
  });
});

describe("the Charter tab", () => {
  it("lists every version with what it changed, Undo while open and Revert after", async () => {
    const { snapshot } = serve();
    renderAt(snapshot, `/projects/${EVIDENCE_SLUG}/charter`);
    const card = await screen.findByTestId("project-charter-card");
    expect(card).toHaveTextContent("A person merges every pull request in this repo (sample)");
    expect(card).toHaveTextContent("kebab-case keys, off by default (sample)");
    const edited = await screen.findByTestId("charter-version-C-34");
    expect(edited).toHaveTextContent("Done when: (sample) the export works →");
    expect(within(edited).getByRole("button", { name: "Revert" })).toBeInTheDocument();
    expect(screen.getByTestId("charter-version-C-29")).toHaveTextContent("Started the project");
    expect(within(screen.getByTestId("charter-version-C-29")).queryByRole("button")).toBeNull();
  });

  it("saves an edit as a version whose toast undoes it", { timeout: 20_000 }, async () => {
    const { snapshot, calls } = serve({
      overrides: (base, snap) => ({
        [`PATCH ${base}/projects/${EVIDENCE_SLUG}`]: () =>
          json({
            project: {},
            changed: true,
            mail_id: "mail_1",
            proposal: "C-35",
            undo_until: snap.server_time + 600,
          }),
        [`POST ${base}/proposals/C-35/undo`]: () =>
          json({ proposal: { ref: "C-35", status: "undone" } }),
      }),
    });
    renderAt(snapshot, `/projects/${EVIDENCE_SLUG}/charter`);
    fireEvent.click(await screen.findByTestId("project-charter-edit"));
    const goal = await screen.findByTestId("charter-goal-0");
    fireEvent.change(goal, { target: { value: "(sample) Export any report as CSV" } });
    fireEvent.click(screen.getByTestId("charter-review"));
    fireEvent.click(await screen.findByTestId("charter-send"));
    await waitFor(() =>
      expect(sent(calls, "PATCH", `/projects/${EVIDENCE_SLUG}`)).toEqual([
        { goal: "(sample) Export any report as CSV" },
      ]),
    );
    expect(await screen.findByText(/^Saved as C-35\./)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(sent(calls, "POST", "/proposals/C-35/undo")).toEqual([{}]));
  });

  it("reverts an older version as a new one", async () => {
    const { snapshot, calls } = serve({
      overrides: (base) => ({
        [`POST ${base}/projects/${EVIDENCE_SLUG}/charter/revert`]: () =>
          json({ project: {}, proposal: { ref: "C-36" }, changed: true, mail_id: null }),
      }),
    });
    renderAt(snapshot, `/projects/${EVIDENCE_SLUG}/charter`);
    const row = await screen.findByTestId("charter-version-C-31");
    fireEvent.click(within(row).getByRole("button", { name: "Revert" }));
    await waitFor(() =>
      expect(sent(calls, "POST", "/charter/revert")).toEqual([{ proposal: "C-31" }]),
    );
    expect(await screen.findByText("Reverted C-31 as C-36.")).toBeVisible();
  });

  it("offers Undo on a fresh edit for the rest of its 10 minutes", async () => {
    const { snapshot } = serve({
      overrides: (base, snap) => ({
        [`GET ${base}/projects/${EVIDENCE_SLUG}/charter/versions`]: () =>
          json(charterVersions(snap.server_time, { justEdited: true })),
      }),
    });
    renderAt(snapshot, `/projects/${EVIDENCE_SLUG}/charter`);
    expect(await screen.findByTestId("charter-undo-C-34")).toHaveTextContent(/Undo \(\d+m left\)/);
  });

  it("lets a viewer read it, with the levers hidden", async () => {
    const { snapshot } = serve({ snapshot: { viewer: "viewer" } });
    renderAt(snapshot, `/projects/${EVIDENCE_SLUG}/charter`);
    const card = await screen.findByTestId("project-charter-card");
    expect(card).toHaveTextContent("Ask an operator to change this.");
    expect(screen.queryByTestId("project-charter-edit")).toBeNull();
    await screen.findByTestId("charter-version-C-34");
    expect(screen.queryByRole("button", { name: "Revert" })).toBeNull();
  });
});

describe("the classic cockpit", () => {
  it("sends a new-cockpit tab address to its section", async () => {
    window.localStorage.setItem(COCKPIT_STORAGE_KEY, "v1");
    const { snapshot } = serve({ cockpitV2: false });
    renderAt(snapshot, `/projects/${EVIDENCE_SLUG}/work`);
    await screen.findByTestId("project-section-nav");
    expect(location).toBe(`/factory/${snapshot.factory.id}/projects/${EVIDENCE_SLUG}#tasks`);
  });
});
