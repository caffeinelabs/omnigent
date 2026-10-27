import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { LIVE_RUN_PL_DB_ID, liveRunSnapshot } from "../__fixtures__/liveRun";
import { TeamStrip } from "../board/TeamStrip";
import { renderControls, serveFactory } from "./__fixtures__/controlsKit";
import { FactoryControlsProvider } from "./FactoryControlsContext";
import { resetFactoryControls } from "./useFactoryControls";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({ useSessionUpdatesConnected: () => true }));

const DRIFT = {
  fields: ["permission_mode"],
  stored: { permission_mode: "auto" },
  spec: { permission_mode: "bypassPermissions" },
};

/** db-version's PL and one of its ICs both run an older launch mode. */
function driftedSnapshot(): { snapshot: FactorySnapshot; icId: string } {
  const snapshot = liveRunSnapshot();
  const project = snapshot.projects.find((p) => p.slug === "db-version")!;
  project.pl = { ...project.pl!, has_launch_drift: true };
  const ic = project.team.find((m) => m.role === "ic")!;
  ic.has_launch_drift = true;
  snapshot.members = snapshot.members.map((m) =>
    m.session_id === LIVE_RUN_PL_DB_ID || m.session_id === ic.session_id
      ? { ...m, launch_drift: DRIFT }
      : m,
  );
  return { snapshot, icId: ic.session_id };
}

function renderStrip(withProvider: boolean) {
  const { snapshot, icId } = driftedSnapshot();
  const server = serveFactory(authenticatedFetch, snapshot);
  const project = snapshot.projects.find((p) => p.slug === "db-version")!;
  const strip = (
    <TeamStrip
      pl={project.pl}
      team={project.team}
      members={snapshot.members}
      nowS={snapshot.server_time}
    />
  );
  renderControls(
    withProvider ? (
      <FactoryControlsProvider snapshot={snapshot}>{strip}</FactoryControlsProvider>
    ) : (
      strip
    ),
    snapshot,
  );
  return { server, icId, controlPath: `/v1/factory/factories/${snapshot.factory.id}/control` };
}

beforeEach(() => {
  authenticatedFetch.mockReset();
  resetFactoryControls();
});

describe("FactoryControlsProvider", () => {
  it("turns a PL's drift chip into the relaunch-with-spec confirm", async () => {
    const { server, controlPath } = renderStrip(true);
    const chip = screen.getByTestId(`launch-drift-chip-${LIVE_RUN_PL_DB_ID}`);
    expect(chip.tagName).toBe("BUTTON");
    expect(chip).toHaveTextContent("old launch mode · relaunch");
    fireEvent.click(chip);
    const dialog = await screen.findByTestId("control-relaunch-dialog");
    expect(within(dialog).getByTestId("control-drift-table")).toHaveTextContent(
      "bypassPermissions",
    );
    fireEvent.click(within(dialog).getByTestId("control-confirm"));
    await waitFor(() =>
      expect(
        server
          .writes()
          .filter((c) => c.path === controlPath)
          .map((c) => c.body),
      ).toEqual([{ action: "relaunch", target: "project:db-version" }]),
    );
  });

  it("leaves a sub-agent's chip a label: its PL relaunches it", () => {
    const { icId } = renderStrip(true);
    expect(screen.getByTestId(`launch-drift-chip-${icId}`).tagName).toBe("SPAN");
  });

  it("leaves every chip a label outside the cockpit", () => {
    renderStrip(false);
    expect(screen.getByTestId(`launch-drift-chip-${LIVE_RUN_PL_DB_ID}`).tagName).toBe("SPAN");
  });
});
