import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { configure, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { preflightOk } from "@/components/factory/__fixtures__/liveRun";
import { opsRoutes } from "@/components/factory/agents/__fixtures__/opsFixtures";
import { resetFactoryControls } from "@/components/factory/controls/useFactoryControls";
import { COCKPIT_STORAGE_KEY } from "@/components/factory/shell/cockpitV2";
import type { DefinitionPreviewResponse, FactorySnapshot } from "@/lib/factoryApi";
import { FactoryPage } from "@/pages/factory/FactoryPage";
import { type ServedCall, json, serveFactory } from "@/pages/factory/testServer";
import {
  TEAM_SLUG,
  type TeamRouteOptions,
  previewNewMember,
  previewOneChange,
  previewOperator,
  teamRoutes,
  teamSnapshot,
} from "./__fixtures__/teamFixtures";
import { readTeamStaging, resetTeamStaging, writeTeamStaging } from "./teamStaging";

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
}

function serve(
  options: TeamRouteOptions & {
    snapshot?: FactorySnapshot;
    cockpitV2?: boolean;
    preview?: DefinitionPreviewResponse;
  } = {},
): Served {
  const snapshot = options.snapshot ?? teamSnapshot(options.shape, { mode: options.mode });
  const fid = snapshot.factory.id;
  const base = `/v1/factory/factories/${fid}`;
  const server = serveFactory(authenticatedFetch, {
    snapshot: () => json(snapshot),
    ops: { ...opsRoutes(fid, { snapshot }), ...teamRoutes(snapshot, options) },
    overrides: {
      "GET /v1/factory/preflight": () =>
        json({ ...preflightOk(), cockpit_v2: options.cockpitV2 ?? true }),
      [`POST ${base}/definition/preview`]: () => json(options.preview ?? previewOneChange()),
      [`PUT ${base}/definition`]: () =>
        json({ proposal: "C-6", proposal_id: "p6", status: "applied", def_version: 13 }),
      [`POST ${base}/definition/pin`]: () =>
        json({ def_version: 12, changed: true, from: 13, ended: [] }),
      [`POST ${base}/definition/proposals`]: () =>
        json({ proposal: { ref: "C-7", id: "p7", number: 7 }, decision: "D-9" }, 201),
    },
  });
  return { calls: server.calls, snapshot };
}

function renderAt(snapshot: FactorySnapshot, path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
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

const EDITOR = `/projects/${TEAM_SLUG}/team`;

beforeEach(() => {
  authenticatedFetch.mockReset();
  resetFactoryControls();
  resetTeamStaging();
  window.localStorage.clear();
  window.localStorage.setItem(COCKPIT_STORAGE_KEY, "v2");
  location = "";
});

describe("the team editor", () => {
  it("locks removals a gate needs, with the gate named; the lead has no remove", async () => {
    const { snapshot } = serve();
    renderAt(snapshot, EDITOR);
    const quill = await screen.findByTestId("team-edit-tile-M-4");
    expect(await within(quill).findByRole("button", { name: /Remove Quill/ })).toBeDisabled();
    expect(within(quill).getByTestId("team-lock-M-4")).toHaveTextContent(
      "Can't remove: the review gate needs a reviewer (Tier 0)",
    );
    expect(screen.getByTestId("team-lock-M-5")).toHaveTextContent(
      "the verify gate needs an env keeper",
    );
    expect(screen.getByTestId("team-lock-M-6")).toHaveTextContent("the demo gate needs one");
    // Two builders: either may go.
    expect(
      within(screen.getByTestId("team-edit-tile-M-2")).getByRole("button", { name: /Remove Ivy/ }),
    ).toBeEnabled();
    const lead = screen.getByTestId("team-edit-tile-M-1");
    expect(within(lead).queryByRole("button", { name: /Remove/ })).toBeNull();
  });

  it("offers a project lead no Codex, and says so on its tile", async () => {
    const { snapshot } = serve();
    renderAt(snapshot, EDITOR);
    const lead = await screen.findByTestId("team-edit-tile-M-1");
    const select = await within(lead).findByRole("combobox", { name: "Vendor and model for Nova" });
    const codex = within(select)
      .getAllByRole("option")
      .filter((o) => o.textContent?.startsWith("codex"));
    expect(codex).toHaveLength(1);
    expect(codex[0]).toBeDisabled();
    expect(lead).toHaveTextContent("Codex is not available for project leads yet");
  });

  it("unlocks removing the first reviewer once a second is staged", async () => {
    const { snapshot } = serve({ preview: previewNewMember() });
    renderAt(snapshot, EDITOR);
    expect(await screen.findByRole("button", { name: /Remove Quill/ })).toBeDisabled();
    fireEvent.pointerDown(screen.getByTestId("team-ghost-check"), { button: 0, ctrlKey: false });
    fireEvent.click(await screen.findByTestId("role-catalog-reviewer"));
    await waitFor(() => expect(screen.getByRole("button", { name: /Remove Quill/ })).toBeEnabled());
    expect(screen.queryByTestId("team-lock-M-4")).toBeNull();
    const panel = screen.getByTestId("staged-team-panel");
    expect(within(panel).getByText("1. Add a reviewer")).toBeInTheDocument();
    // Designers are listed but have no bundle.
    fireEvent.pointerDown(screen.getByTestId("team-ghost-build"), { button: 0, ctrlKey: false });
    expect(await screen.findByTestId("role-catalog-missing-designer")).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });

  it("stages a model change, applies it as one version, and Undo pins the one before", async () => {
    const { snapshot, calls } = serve();
    renderAt(snapshot, EDITOR);
    const select = await screen.findByRole("combobox", { name: "Vendor and model for Rex" });
    fireEvent.change(select, { target: { value: "claude|" } });
    expect(await screen.findByText("1. Rex (Builder): vendor and model")).toBeInTheDocument();
    expect(screen.getByTestId("team-edit-tile-M-3")).toHaveAttribute("data-tag", "staged");
    const effects = await screen.findByTestId("staged-team-effects");
    expect(effects).toHaveTextContent("$5.60/h → $5.90/h");
    expect(effects).toHaveTextContent("Becomes definition v13. Nova gets one TEAM UPDATE");
    expect(screen.getByTestId("team-no-trial")).toHaveTextContent("No trial for one project.");
    await waitFor(() => expect(screen.getByTestId("team-apply")).toBeEnabled());
    fireEvent.click(screen.getByTestId("team-apply"));
    await waitFor(() =>
      expect(location).toBe(`/factory/${snapshot.factory.id}/projects/${TEAM_SLUG}`),
    );
    expect(sent(calls, "PUT", "/definition")).toEqual([
      {
        changes: [
          {
            op: "replace",
            path: `team.projects.${TEAM_SLUG}.members.M-3`,
            value: { vendor: "claude", model: null },
          },
        ],
        when: "after_current",
        base_version: 12,
      },
    ]);
    expect(readTeamStaging(snapshot.factory.id, TEAM_SLUG).changes).toEqual([]);
    const toast = await screen.findByTestId("factory-toast");
    expect(toast).toHaveTextContent("Applied as definition v13. Nova gets one TEAM UPDATE.");
    fireEvent.click(within(toast).getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(sent(calls, "POST", "/definition/pin")).toEqual([{ version: 12 }]));
    expect(await screen.findByTestId("factory-toast")).toHaveTextContent(
      "Undone. The team is back as in v12",
    );
    expect(readTeamStaging(snapshot.factory.id, TEAM_SLUG).changes).toEqual([]);
  });

  it("sends when=now and the template's shape when asked", async () => {
    const { snapshot, calls } = serve();
    writeTeamStaging(snapshot.factory.id, TEAM_SLUG, {
      changes: [{ kind: "model", member: "M-3", vendor: "claude", model: null }],
      when: "after_current",
      scope: "project",
    });
    renderAt(snapshot, EDITOR);
    fireEvent.click(await screen.findByTestId("team-when-now"));
    fireEvent.click(screen.getByTestId("team-scope-template"));
    await waitFor(() => {
      const last = sent(calls, "POST", "/definition/preview").at(-1) as {
        changes: { path: string }[];
        when: string;
      };
      expect(last.when).toBe("now");
      expect(last.changes.at(-1)!.path).toBe("team.templates.standard.roles");
    });
  });

  it("keeps Apply off until the server enforces teams (read-only editor)", async () => {
    const { snapshot } = serve({ mode: "warn" });
    writeTeamStaging(snapshot.factory.id, TEAM_SLUG, {
      changes: [{ kind: "budget", value: 80 }],
      when: "after_current",
      scope: "project",
    });
    renderAt(snapshot, EDITOR);
    expect(await screen.findByTestId("team-editor-note")).toHaveTextContent(
      "OMNIGENT_FACTORY_TEAM_SPEC=enforce",
    );
    await screen.findByTestId("staged-team-effects");
    expect(screen.getByTestId("team-apply")).toBeDisabled();
  });

  it("lets an operator propose an owner change to the owners", async () => {
    const snapshot = teamSnapshot("standard", { viewer: "operator" });
    const { calls } = serve({ snapshot, preview: previewOperator() });
    writeTeamStaging(snapshot.factory.id, TEAM_SLUG, {
      changes: [{ kind: "model", member: "M-3", vendor: "claude", model: null }],
      when: "after_current",
      scope: "project",
    });
    renderAt(snapshot, EDITOR);
    expect(
      await screen.findByText(/It waits for an owner: propose it to them/),
    ).toBeInTheDocument();
    const propose = screen.getByRole("button", { name: "Propose to owners" });
    expect(propose).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Why (the owners read it)"), {
      target: { value: "(sample) Codex reviews its own PRs today" },
    });
    fireEvent.click(propose);
    await waitFor(() =>
      expect(sent(calls, "POST", "/definition/proposals")).toEqual([
        {
          kind: "tuning",
          changes: [
            {
              op: "replace",
              path: `team.projects.${TEAM_SLUG}.members.M-3`,
              value: { vendor: "claude", model: null },
            },
          ],
          rationale: "(sample) Codex reviews its own PRs today",
        },
      ]),
    );
    expect(await screen.findByTestId("factory-toast")).toHaveTextContent("Proposed as C-7");
  });

  it("drops a staged change from the panel and discards all", async () => {
    const { snapshot } = serve();
    writeTeamStaging(snapshot.factory.id, TEAM_SLUG, {
      changes: [
        { kind: "model", member: "M-3", vendor: "claude", model: null },
        { kind: "budget", value: 80 },
      ],
      when: "after_current",
      scope: "project",
    });
    renderAt(snapshot, EDITOR);
    fireEvent.click(await screen.findByRole("button", { name: /Drop change 2: Project budget/ }));
    expect(readTeamStaging(snapshot.factory.id, TEAM_SLUG).changes).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Discard all" }));
    expect(readTeamStaging(snapshot.factory.id, TEAM_SLUG).changes).toEqual([]);
    expect(screen.getByTestId("staged-team-panel")).toHaveTextContent("Staged changes");
  });

  it("lands on the project's Team section without the new cockpit", async () => {
    window.localStorage.clear();
    const { snapshot } = serve({ cockpitV2: false });
    renderAt(snapshot, EDITOR);
    await waitFor(() =>
      expect(location).toBe(`/factory/${snapshot.factory.id}/projects/${TEAM_SLUG}#team`),
    );
  });
});

describe("the project page's team", () => {
  it("draws the team as a chart with each member's model, state and spend", async () => {
    const { snapshot } = serve();
    renderAt(snapshot, `/projects/${TEAM_SLUG}`);
    const section = await screen.findByTestId("team-section");
    await waitFor(() =>
      expect(within(section).getByTestId("team-summary")).toHaveTextContent(
        "6 members · 2 builders at once · changed in definition v12",
      ),
    );
    await waitFor(() =>
      expect(within(section).getByTestId("team-tile-M-6")).toHaveTextContent("Dot"),
    );
    const dot = within(section).getByTestId("team-tile-M-6");
    expect(dot).toHaveTextContent("Dot");
    expect(dot).toHaveTextContent("claude · sonnet");
    expect(dot).toHaveTextContent("Stuck");
    expect(within(section).getByTestId("team-group-build")).toHaveTextContent("Ivy");
    fireEvent.click(dot);
    await waitFor(() => expect(location).toContain("peek=agent%3Adot"));
    const panel = await screen.findByTestId("agent-panel");
    // "Change model…" on an agent opens its project's team editor.
    expect(within(panel).getByRole("link", { name: /Change model/ })).toHaveAttribute(
      "href",
      `/factory/${snapshot.factory.id}${EDITOR}`,
    );
  });

  it("links each project team on Teams to its editor", async () => {
    const { snapshot } = serve();
    renderAt(snapshot, "/teams");
    const card = await screen.findByTestId(`team-card-${TEAM_SLUG}`);
    expect(within(card).getByRole("link", { name: "Edit team" })).toHaveAttribute(
      "href",
      `/factory/${snapshot.factory.id}${EDITOR}`,
    );
  });

  it("restores an earlier team from History as a staged change in the editor", async () => {
    const { snapshot } = serve();
    renderAt(snapshot, `/projects/${TEAM_SLUG}`);
    await screen.findByTestId("team-section");
    fireEvent.click(screen.getByRole("button", { name: "History" }));
    const v10 = await screen.findByTestId("team-history-v10");
    expect(screen.getByTestId("team-history-v12")).toHaveTextContent("current");
    fireEvent.click(within(v10).getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(location).toBe(`/factory/${snapshot.factory.id}${EDITOR}`));
    expect(readTeamStaging(snapshot.factory.id, TEAM_SLUG).changes).toMatchObject([
      { kind: "restore", version: 10 },
    ]);
    await screen.findByTestId("team-editor");
  });

  it("goes to the editor on `e`", async () => {
    const { snapshot } = serve();
    renderAt(snapshot, `/projects/${TEAM_SLUG}`);
    await screen.findByTestId("team-section");
    fireEvent.keyDown(window, { key: "e" });
    await waitFor(() => expect(location).toBe(`/factory/${snapshot.factory.id}${EDITOR}`));
  });

  it("offers Undo in the header while the last team change is fresh", async () => {
    const { snapshot, calls } = serve({ justApplied: true });
    renderAt(snapshot, `/projects/${TEAM_SLUG}`);
    const undo = await screen.findByTestId("team-undo");
    expect(undo).toHaveTextContent("Undo v13");
    fireEvent.click(undo);
    await waitFor(() => expect(sent(calls, "POST", "/definition/pin")).toEqual([{ version: 12 }]));
  });
});
