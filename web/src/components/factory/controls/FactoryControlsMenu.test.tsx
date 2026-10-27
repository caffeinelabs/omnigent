import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { LIVE_RUN_ATLAS_ID, liveRunSnapshot } from "../__fixtures__/liveRun";
import { openMenu, renderControls, serveFactory } from "./__fixtures__/controlsKit";
import { FactoryControlsMenu } from "./FactoryControlsMenu";
import { resetFactoryControls } from "./useFactoryControls";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({ useSessionUpdatesConnected: () => true }));

function setup(mutate?: (snapshot: FactorySnapshot) => void) {
  const snapshot = liveRunSnapshot();
  mutate?.(snapshot);
  const server = serveFactory(authenticatedFetch, snapshot);
  renderControls(
    <FactoryControlsMenu
      factoryId={snapshot.factory.id}
      factory={snapshot.factory}
      members={snapshot.members}
    />,
    snapshot,
  );
  const controlBodies = () =>
    server
      .writes()
      .filter((c) => c.path.endsWith("/control"))
      .map((c) => c.body);
  return { snapshot, server, controlBodies };
}

const open = () => openMenu(screen.getByTestId("factory-controls-menu"));

beforeEach(() => {
  authenticatedFetch.mockReset();
  resetFactoryControls();
});

describe("FactoryControlsMenu", () => {
  it("pauses everything from the header button, with Undo sending resume", async () => {
    const { controlBodies } = setup();
    fireEvent.click(screen.getByTestId("factory-pause-all"));
    const dialog = await screen.findByTestId("control-pause-dialog");
    expect(dialog).toHaveTextContent("Pause all of Atlas factory?");
    expect(dialog).toHaveTextContent("Every PL pauses first, then Sentinel, then Atlas.");
    expect(dialog).toHaveTextContent("2 factory previews stay up for review");
    fireEvent.click(within(dialog).getByTestId("control-confirm"));
    fireEvent.click(await screen.findByTestId("control-undo"));
    await waitFor(() =>
      expect(controlBodies()).toEqual([
        { action: "pause", target: "factory", options: { keep_previews: true } },
        { action: "resume", target: "factory" },
      ]),
    );
  });

  it("resumes a paused factory in one click", async () => {
    const { controlBodies } = setup((s) => {
      s.factory.state = "paused";
    });
    expect(screen.getByTestId("factory-pause-all")).toHaveTextContent("Resume all");
    fireEvent.click(screen.getByTestId("factory-pause-all"));
    await waitFor(() => expect(controlBodies()).toEqual([{ action: "resume", target: "factory" }]));
  });

  it("switches cadence and shows what each costs", async () => {
    const { controlBodies } = setup();
    open();
    fireEvent.click(screen.getByTestId("control-cadence"));
    expect(await screen.findByTestId("control-cadence-estimate")).toHaveTextContent(
      "≈36 vs ≈3 heartbeat turns/h",
    );
    const demo = screen.getByTestId("control-cadence-demo");
    expect(demo).toHaveAttribute("data-checked", "true");
    expect(screen.getByTestId("control-cadence-normal")).toHaveTextContent("1h / 2h · ≈3/h");
    fireEvent.click(screen.getByTestId("control-cadence-normal"));
    await waitFor(() =>
      expect(controlBodies()).toEqual([
        { action: "cadence", target: "factory", options: { cadence: "normal" } },
      ]),
    );
    expect(await screen.findByText(/Cadence → Normal · heartbeats 1h \/ 2h/)).toBeInTheDocument();
  });

  it("stops the factory only after its name is typed", async () => {
    const { controlBodies } = setup();
    open();
    fireEvent.click(screen.getByTestId("control-stop-factory"));
    const confirm = await screen.findByTestId("control-confirm");
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByTestId("control-confirm-input"), {
      target: { value: "Atlas factory" },
    });
    fireEvent.click(confirm);
    await waitFor(() => expect(controlBodies()).toEqual([{ action: "stop", target: "factory" }]));
  });

  it("offers to relaunch a drifted lead with its spec mode", async () => {
    const { controlBodies } = setup();
    expect(screen.getByTestId("factory-controls-menu")).toHaveAttribute("data-drift", "true");
    open();
    const item = screen.getByTestId(`control-relaunch-${LIVE_RUN_ATLAS_ID}`);
    expect(item).toHaveTextContent("Relaunch Atlas with spec mode");
    fireEvent.click(item);
    const dialog = await screen.findByTestId("control-relaunch-dialog");
    expect(within(dialog).getByTestId("control-drift-table")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByTestId("control-confirm"));
    await waitFor(() =>
      expect(controlBodies()).toEqual([
        { action: "relaunch", target: `member:${LIVE_RUN_ATLAS_ID}` },
      ]),
    );
    expect(await screen.findByTestId("control-pending-factory")).toHaveTextContent(
      "Relaunching… waiting for Atlas",
    );
  });

  it("links to both leads", () => {
    setup();
    open();
    expect(screen.getByTestId("control-open-atlas")).toHaveAttribute(
      "href",
      `/c/${LIVE_RUN_ATLAS_ID}`,
    );
    expect(screen.getByTestId("control-open-sentinel")).toHaveTextContent("Open Lead B · Sentinel");
  });
});
