// The Team verb on a live factory: the team editor's own path (the project's
// team spec, the catalog, the definition's preview), a definition version on
// Apply, and Undo by re-pinning the version before, only while the version it
// made is the one in force.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { configure, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DefinitionVersionsResponse, FactorySnapshot } from "@/lib/factoryApi";
import { json, serveFactory } from "@/pages/factory/testServer";
import { FactoryShellContext, type FactoryShellValue } from "../../shell/FactoryShellContext";
import {
  TEAM_SLUG,
  TEAM_NOW_S,
  previewOneChange,
  teamRoutes,
  teamSnapshot,
  teamVersions,
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
    // The server's clock is the fixture's (an Undo's window counts from its version's time).
    clockOffsetS: snapshot.server_time - Math.floor(Date.now() / 1000),
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

/** v13 (this apply) was made from v12, then someone else landed v14 on top: v14 is in force. */
function newerOnTop(): DefinitionVersionsResponse {
  const applied = teamVersions({ justApplied: true });
  const v13 = applied.data.find((v) => v.version === 13)!;
  const v14 = {
    ...v13,
    version: 14,
    parent_version: 13,
    created_by: "editor@example.test",
    created_at: TEAM_NOW_S - 30,
    pinned: true,
  };
  return {
    data: [v14, ...applied.data.map((v) => ({ ...v, pinned: false }))],
    pinned: 14,
  };
}

/** The live Team panel over `snapshot`, its versions as `versions()` says (it changes with each write). */
function renderTeam(
  snapshot: FactorySnapshot,
  versions: { now: () => DefinitionVersionsResponse; afterPut: () => void; afterPin: () => void },
) {
  const fid = snapshot.factory.id;
  const base = `/v1/factory/factories/${fid}`;
  const server = serveFactory(authenticatedFetch, {
    snapshot: () => json(snapshot),
    ops: teamRoutes(snapshot),
    overrides: {
      [`GET ${base}/definition/versions`]: () => json(versions.now()),
      [`POST ${base}/definition/preview`]: () => json(previewOneChange()),
      [`PUT ${base}/definition`]: () => {
        versions.afterPut();
        return json({ proposal: "C-6", proposal_id: "p6", status: "applied", def_version: 13 });
      },
      [`POST ${base}/definition/pin`]: () => {
        versions.afterPin();
        return json({ def_version: 12, changed: true, from: 13, ended: [] });
      },
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
  return server;
}

/** Open Team, move the second builder (Codex today) to Claude and apply it. */
async function applySwap(server: ReturnType<typeof serveFactory>) {
  const team = await screen.findByRole("button", { name: /Team/ });
  await userEvent.click(team);
  const panel = await screen.findByTestId("v3-preview");
  const rows = await within(panel).findAllByTestId("v3-team-row");
  expect(rows).toHaveLength(6);
  // Nothing is preselected: no change, no preview read, Apply off.
  expect(rows.filter((r) => r.dataset.changed != null)).toEqual([]);
  expect(within(panel).getByTestId("v3-preview-apply")).toBeDisabled();
  expect(server.calls.some((c) => c.path.endsWith("/definition/preview"))).toBe(false);
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
  // The first pick never sends a preview with no changes (the server refuses it).
  const previews = server.calls.filter((c) => c.path.endsWith("/definition/preview"));
  for (const call of previews) {
    expect((call.body as { changes: unknown[] }).changes.length).toBeGreaterThan(0);
  }
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
  return toast;
}

const pins = (server: ReturnType<typeof serveFactory>) =>
  server.calls.filter((c) => c.method === "POST" && c.path.endsWith("/definition/pin"));

describe("cockpit v3 · Team on a live factory", () => {
  it(
    "previews through the definition, applies a version and Undo re-pins the one before, once",
    { timeout: 20_000 },
    async () => {
      let versions = teamVersions();
      const server = renderTeam(teamSnapshot(), {
        now: () => versions,
        afterPut: () => (versions = teamVersions({ justApplied: true })),
        afterPin: () => (versions = teamVersions({ undone: true })),
      });
      const toast = await applySwap(server);
      // Beside the verbs for the rest of the window, counted from v13's own time (2 min ago).
      await waitFor(() =>
        expect(screen.getByTestId("v3-undo-latest")).toHaveTextContent("Team: Undo (8 min left)"),
      );
      await userEvent.click(within(toast).getByRole("button", { name: "Undo" }));
      await waitFor(() => expect(pins(server).map((c) => c.body)).toEqual([{ version: 12 }]));
      expect(await screen.findByTestId("factory-toast")).toHaveTextContent(
        "Undone. The team is back as in v12; v13 stays in history.",
      );
      // It ran: nothing is left to undo, so it cannot re-pin again.
      await waitFor(() => expect(screen.queryByTestId("v3-undo-latest")).toBeNull());
    },
  );

  it(
    "offers no Undo once a newer version is in force, and says where to undo from",
    { timeout: 20_000 },
    async () => {
      let versions = teamVersions();
      const server = renderTeam(teamSnapshot(), {
        now: () => versions,
        // Someone lands v14 on top of this apply's v13 right away.
        afterPut: () => (versions = newerOnTop()),
        afterPin: () => undefined,
      });
      const toast = await applySwap(server);
      await waitFor(() => expect(screen.queryByTestId("v3-undo-latest")).toBeNull());
      await userEvent.click(within(toast).getByRole("button", { name: "Undo" }));
      expect(await screen.findByTestId("factory-toast")).toHaveTextContent(
        "v13 is no longer in force; undo from Versions.",
      );
      expect(pins(server)).toEqual([]);
    },
  );
});
