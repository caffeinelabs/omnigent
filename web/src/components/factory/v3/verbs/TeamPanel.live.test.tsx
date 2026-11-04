// The Team verb on a live factory: the team editor's own path (the project's
// team spec, the catalog, the definition's preview), a definition version on
// Apply, and Undo by re-pinning the version before.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { configure, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { json, serveFactory } from "@/pages/factory/testServer";
import { FactoryShellContext, type FactoryShellValue } from "../../shell/FactoryShellContext";
import {
  TEAM_SLUG,
  previewOneChange,
  teamRoutes,
  teamSnapshot,
} from "../../team/__fixtures__/teamFixtures";
import { resetTeamStaging } from "../../team/teamStaging";
import { FactoryToastProvider } from "../../ui/Toast";
import { sampleEngine } from "../fixture/sampleNodeSource";
import { NodePage } from "../NodePage";
import { nodeHref } from "../nodeRoutes";
import { type NodeSource, NodeSourceProvider } from "../nodeSource";
import { resetNodeUndos } from "../nodeSteer";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({ useSessionUpdatesConnected: () => true }));
configure({ asyncUtilTimeout: 5000 });

function shellOf(snapshot: FactorySnapshot): FactoryShellValue {
  return {
    factoryId: snapshot.factory.id,
    snapshot,
    clockOffsetS: 0,
    nowS: snapshot.server_time,
    updatedAt: 0,
    location: { page: "project", id: TEAM_SLUG },
    wide: false,
    cockpit: "v2",
    openShare: () => {},
    openPalette: () => {},
    openAddProject: () => {},
    openAddProjectTitled: () => {},
  };
}

/** A live-shaped source without a team or preview route: the sample's project page, as the snapshot's project. */
function liveProjectSource(): NodeSource {
  const page = sampleEngine().node("assistant-core")!;
  return {
    kind: "live",
    node: async () => ({
      ...page,
      node: { ...page.node, ref: TEAM_SLUG, title: TEAM_SLUG },
      breadcrumb: page.breadcrumb,
    }),
    level: async () => null,
    answer: async () => undefined,
  };
}

beforeEach(() => {
  authenticatedFetch.mockReset();
  resetTeamStaging();
  resetNodeUndos();
});

describe("cockpit v3 · Team on a live factory", () => {
  it(
    "previews through the definition, applies a version and Undo re-pins the one before",
    { timeout: 20_000 },
    async () => {
      const snapshot = teamSnapshot();
      const fid = snapshot.factory.id;
      const base = `/v1/factory/factories/${fid}`;
      const server = serveFactory(authenticatedFetch, {
        snapshot: () => json(snapshot),
        ops: teamRoutes(snapshot),
        overrides: {
          [`POST ${base}/definition/preview`]: () => json(previewOneChange()),
          [`PUT ${base}/definition`]: () =>
            json({ proposal: "C-6", proposal_id: "p6", status: "applied", def_version: 13 }),
          [`POST ${base}/definition/pin`]: () =>
            json({ def_version: 12, changed: true, from: 13, ended: [] }),
        },
      });
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      const shell = shellOf(snapshot);
      render(
        <QueryClientProvider client={client}>
          <NodeSourceProvider source={liveProjectSource()}>
            <MemoryRouter initialEntries={[nodeHref(fid, TEAM_SLUG)]}>
              <FactoryShellContext.Provider value={shell}>
                <FactoryToastProvider>
                  <Routes>
                    <Route
                      path="/factory/:factoryId/n/:ref"
                      element={<NodePage factoryId={fid} nodeRef={TEAM_SLUG} />}
                    />
                  </Routes>
                </FactoryToastProvider>
              </FactoryShellContext.Provider>
            </MemoryRouter>
          </NodeSourceProvider>
        </QueryClientProvider>,
      );
      const team = await screen.findByRole("button", { name: /Team/ });
      await userEvent.click(team);
      const panel = await screen.findByTestId("v3-preview");
      const rows = await within(panel).findAllByTestId("v3-team-row");
      expect(rows).toHaveLength(6);
      // Nothing is preselected: no change, no preview read, Apply off.
      expect(rows.filter((r) => r.dataset.changed != null)).toEqual([]);
      expect(within(panel).getByTestId("v3-preview-apply")).toBeDisabled();
      expect(server.calls.some((c) => c.path.endsWith("/definition/preview"))).toBe(false);
      // The second builder (Codex today) moves to Claude.
      const builder = rows[2]!;
      const vendor = within(builder).getByRole("combobox", { name: /vendor/ });
      await waitFor(() =>
        expect(
          within(vendor)
            .getAllByRole("option")
            .map((o) => o.textContent),
        ).toContain("claude"),
      );
      await userEvent.selectOptions(vendor, "claude");
      await waitFor(() => expect(builder).toHaveAttribute("data-changed"));
      await waitFor(() =>
        expect(server.calls.some((c) => c.path.endsWith("/definition/preview"))).toBe(true),
      );
      const lines = await within(panel).findByTestId("v3-preview-lines");
      expect(lines).toHaveTextContent("From each member’s next task.");
      expect(lines).toHaveTextContent("Cost$5.6/h → about $5.9/h while working (estimate)");
      await waitFor(() => expect(lines).toHaveTextContent("Who approves"));
      const apply = within(panel).getByTestId("v3-preview-apply");
      await waitFor(() => expect(apply).toBeEnabled());
      await userEvent.click(apply);
      const toast = await screen.findByTestId("factory-toast");
      expect(toast).toHaveTextContent(`Applied as definition v13: the team of ${TEAM_SLUG}`);
      const put = server.calls.find((c) => c.method === "PUT" && c.path.endsWith("/definition"));
      expect(put?.body).toMatchObject({ when: "after_current", base_version: 12 });
      expect(screen.getByTestId("v3-undo-latest")).toHaveTextContent("Team: Undo (10 min left)");
      await userEvent.click(within(toast).getByRole("button", { name: "Undo" }));
      await waitFor(() =>
        expect(
          server.calls.find((c) => c.method === "POST" && c.path.endsWith("/definition/pin"))?.body,
        ).toEqual({ version: 12 }),
      );
    },
  );
});
