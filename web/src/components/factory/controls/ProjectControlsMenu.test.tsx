import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useFactory } from "@/hooks/useFactory";
import type { EventView, FactorySnapshot, ProjectView } from "@/lib/factoryApi";
import { LIVE_RUN_PL_API_ID, liveRunSnapshot } from "../__fixtures__/liveRun";
import { FactoryNowContext } from "../factoryClock";
import { ackEvent, openMenu, renderControls, serveFactory } from "./__fixtures__/controlsKit";
import { ProjectControlsMenu } from "./ProjectControlsMenu";
import { resetFactoryControls } from "./useFactoryControls";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({ useSessionUpdatesConnected: () => true }));

function setup(
  mutate?: (snapshot: FactorySnapshot, project: ProjectView) => void,
  events: EventView[] = [],
  now?: Date,
) {
  const snapshot = liveRunSnapshot();
  const project = snapshot.projects.find((p) => p.slug === "api-version")!;
  mutate?.(snapshot, project);
  const server = serveFactory(authenticatedFetch, snapshot);
  server.events = events;
  const prs = snapshot.prs.filter((pr) => project.pr_ids.includes(pr.id));
  const menu = (at?: Date) => (
    <FactoryNowContext.Provider value={at ?? null}>
      <ProjectControlsMenu
        factoryId={snapshot.factory.id}
        project={project}
        prs={prs}
        members={snapshot.members}
      />
    </FactoryNowContext.Provider>
  );
  const view = renderControls(menu(now), snapshot);
  /** Re-render the menu with the cockpit clock at `at`. */
  const at = (later: Date) => view.rerender(menu(later));
  const controlPath = `/v1/factory/factories/${snapshot.factory.id}/control`;
  const controlBodies = () =>
    server
      .writes()
      .filter((c) => c.path === controlPath)
      .map((c) => c.body);
  return { snapshot, project, server, controlBodies, at, ...view };
}

function open() {
  openMenu(screen.getByTestId("project-controls-api-version"));
}

beforeEach(() => {
  authenticatedFetch.mockReset();
  resetFactoryControls();
  // Sonner replays toasts still active to a new toaster.
  toast.dismiss();
});

describe("ProjectControlsMenu", () => {
  it("pauses through the dialog, keeping previews unless asked to tear them down", async () => {
    const { controlBodies } = setup();
    open();
    fireEvent.click(screen.getByTestId("control-pause"));
    const dialog = await screen.findByTestId("control-pause-dialog");
    expect(within(dialog).getByTestId("control-consequences")).toHaveTextContent(
      "1 factory preview stays up for review",
    );
    fireEvent.click(within(dialog).getByTestId("control-teardown"));
    fireEvent.change(within(dialog).getByTestId("control-note"), {
      target: { value: "lunch" },
    });
    fireEvent.click(within(dialog).getByTestId("control-confirm"));
    await waitFor(() =>
      expect(controlBodies()).toEqual([
        {
          action: "pause",
          target: "project:api-version",
          note: "lunch",
          options: { keep_previews: false },
        },
      ]),
    );
  });

  it("sends the right body for every other project control", async () => {
    const { controlBodies, snapshot } = setup((_, project) => {
      project.overlay = "paused";
    });
    const pr9630 = snapshot.prs.find((pr) => pr.number === 9630)!;

    open();
    fireEvent.click(screen.getByTestId("control-resume"));
    await waitFor(() => expect(controlBodies()).toHaveLength(1));

    open();
    fireEvent.click(screen.getByTestId("control-restart"));
    fireEvent.change(await screen.findByTestId("control-note"), { target: { value: "stuck" } });
    fireEvent.click(screen.getByTestId("control-confirm"));
    await waitFor(() => expect(controlBodies()).toHaveLength(2));

    open();
    fireEvent.click(screen.getByTestId("control-relaunch"));
    fireEvent.click(await screen.findByTestId("control-confirm"));
    await waitFor(() => expect(controlBodies()).toHaveLength(3));

    open();
    fireEvent.click(screen.getByTestId("control-preview"));
    fireEvent.click(await screen.findByTestId("control-preview-9630"));
    await waitFor(() => expect(controlBodies()).toHaveLength(4));

    open();
    fireEvent.click(screen.getByTestId("control-env-status"));
    await waitFor(() => expect(controlBodies()).toHaveLength(5));

    open();
    fireEvent.click(screen.getByTestId("control-preview"));
    fireEvent.click(await screen.findByTestId("control-preview-down-9630"));
    await waitFor(() => expect(controlBodies()).toHaveLength(6));

    expect(controlBodies()).toEqual([
      { action: "resume", target: "project:api-version" },
      { action: "restart", target: "project:api-version", note: "stuck" },
      { action: "relaunch", target: "project:api-version" },
      { action: "preview", target: "project:api-version", options: { pr_id: pr9630.id } },
      { action: "env_status", target: "project:api-version" },
      {
        action: "preview",
        target: "project:api-version",
        options: { pr_id: pr9630.id, mode: "down" },
      },
    ]);
    expect(screen.getByTestId("control-pending-api-version")).toHaveTextContent(
      "Taking down preview #9630… waiting for PL",
    );
  });

  it("stops only once the slug is typed", async () => {
    const { controlBodies } = setup();
    open();
    fireEvent.click(screen.getByTestId("control-stop"));
    const confirm = await screen.findByTestId("control-confirm");
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByTestId("control-confirm-input"), {
      target: { value: "api-versio" },
    });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByTestId("control-confirm-input"), {
      target: { value: "api-version" },
    });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    await waitFor(() =>
      expect(controlBodies()).toEqual([{ action: "stop", target: "project:api-version" }]),
    );
  });

  it("highlights the relaunch with the drift fields when the PL drifted", async () => {
    setup((snapshot) => {
      const pl = snapshot.members.find((m) => m.session_id === LIVE_RUN_PL_API_ID)!;
      pl.launch_drift = {
        fields: ["permission_mode"],
        stored: { permission_mode: "auto" },
        spec: { permission_mode: "bypassPermissions" },
      };
    });
    expect(screen.getByTestId("project-controls-api-version")).toHaveAttribute(
      "data-drift",
      "true",
    );
    open();
    const item = screen.getByTestId("control-relaunch");
    expect(item).toHaveAttribute("data-drift", "true");
    expect(within(item).getByTestId("control-relaunch-drift")).toHaveTextContent(
      "permission mode auto → bypassPermissions",
    );
    fireEvent.click(item);
    expect(await screen.findByTestId("control-drift-table")).toHaveTextContent("bypassPermissions");
  });

  it("shows no drift on the relaunch item when the launch matches the spec", () => {
    setup();
    open();
    expect(screen.getByTestId("control-relaunch")).toHaveAttribute("data-drift", "false");
    expect(screen.queryByTestId("control-relaunch-drift")).not.toBeInTheDocument();
  });

  it("is pending until the PL acknowledges, then confirms", async () => {
    const { server, client, snapshot } = setup();
    open();
    fireEvent.click(screen.getByTestId("control-pause"));
    fireEvent.click(await screen.findByTestId("control-confirm"));
    const chip = await screen.findByTestId("control-pending-api-version");
    await waitFor(() => expect(chip).toHaveAttribute("data-state", "waiting"));
    expect(chip).toHaveTextContent("Pausing… waiting for PL");

    // The server's event (ev-1, the first write) and the PL's ack arrive with the next read.
    server.events = [
      ackEvent("ev-1", LIVE_RUN_PL_API_ID),
      {
        ...ackEvent("ev-1", LIVE_RUN_PL_API_ID),
        id: "ev-1",
        kind: "control.pause",
        from_id: null,
        project_slug: "api-version",
        data: { target: "project:api-version", recipients: [LIVE_RUN_PL_API_ID] },
      },
    ];
    await act(() =>
      client.invalidateQueries({ queryKey: ["factory-events", snapshot.factory.id] }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("control-pending-api-version")).toHaveAttribute(
        "data-state",
        "done",
      ),
    );
    expect(screen.getByTestId("control-pending-api-version")).toHaveTextContent("PL paused");
  });

  it("never brings an acknowledged pause back as unanswered once the tab sat open", async () => {
    const start = Date.now();
    const { server, client, snapshot, at } = setup(undefined, [], new Date(start));
    open();
    fireEvent.click(screen.getByTestId("control-pause"));
    fireEvent.click(await screen.findByTestId("control-confirm"));
    await waitFor(() =>
      expect(screen.getByTestId("control-pending-api-version")).toHaveAttribute(
        "data-state",
        "waiting",
      ),
    );
    const pause = {
      ...ackEvent("ev-1", LIVE_RUN_PL_API_ID, start + 60_000),
      id: "ev-1",
      ts: start,
      kind: "control.pause",
      from_id: null,
      project_slug: "api-version",
      data: { target: "project:api-version", recipients: [LIVE_RUN_PL_API_ID] },
    };
    server.events = [ackEvent("ev-1", LIVE_RUN_PL_API_ID, start + 60_000), pause];
    await act(() =>
      client.invalidateQueries({ queryKey: ["factory-events", snapshot.factory.id] }),
    );
    at(new Date(start + 2 * 60_000));
    await waitFor(() =>
      expect(screen.getByTestId("control-pending-api-version")).toHaveTextContent("PL paused"),
    );

    // Overnight: the control is history, and 100 newer control events pushed it off the page.
    at(new Date(start + 7 * 3600_000));
    expect(screen.queryByTestId("control-pending-api-version")).not.toBeInTheDocument();
    server.events = [];
    await act(() =>
      client.invalidateQueries({ queryKey: ["factory-events", snapshot.factory.id] }),
    );
    at(new Date(start + 30 * 60_000));
    expect(screen.queryByTestId("control-pending-api-version")).not.toBeInTheDocument();
  });

  it("offers Undo for 5 s after a pause, which sends resume", async () => {
    const { controlBodies } = setup();
    open();
    fireEvent.click(screen.getByTestId("control-pause"));
    fireEvent.click(await screen.findByTestId("control-confirm"));
    fireEvent.click(await screen.findByTestId("control-undo"));
    await waitFor(() =>
      expect(controlBodies()).toEqual([
        { action: "pause", target: "project:api-version", options: { keep_previews: true } },
        { action: "resume", target: "project:api-version" },
      ]),
    );
  });

  it("takes the Undo toast down when the pause is refused", async () => {
    const { server } = setup();
    server.failNext(403, { error: { code: "forbidden", message: "Forbidden" } });
    const serveRest = authenticatedFetch.getMockImplementation()!;
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    authenticatedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "POST") await held;
      return serveRest(path, init);
    });
    open();
    fireEvent.click(screen.getByTestId("control-pause"));
    fireEvent.click(await screen.findByTestId("control-confirm"));
    expect(await screen.findByTestId("control-undo")).toBeInTheDocument();
    release();
    await waitFor(() =>
      expect(screen.getByTestId("control-pending-api-version")).toHaveAttribute(
        "data-state",
        "error",
      ),
    );
    await waitFor(() => expect(screen.queryByTestId("control-undo")).not.toBeInTheDocument());
  });

  it("explains a 403 inline and in a toast", async () => {
    const { server } = setup();
    server.failNext(403, { error: { code: "forbidden", message: "Forbidden" } });
    open();
    fireEvent.click(screen.getByTestId("control-env-status"));
    const chip = await screen.findByTestId("control-pending-api-version");
    await waitFor(() => expect(chip).toHaveAttribute("data-state", "error"));
    expect(chip).toHaveTextContent("Env status: You need edit/owner access");
    expect(await screen.findByText("You need edit/owner access")).toBeInTheDocument();
  });

  it("starts a missing PL from the menu", async () => {
    const { server, snapshot } = setup((_, project) => {
      project.pl = null;
    });
    open();
    expect(screen.getByText("No PL yet")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("control-start-pl"));
    const startPath = `/v1/factory/factories/${snapshot.factory.id}/projects/api-version/start`;
    await waitFor(() =>
      expect(server.writes().map((c) => [c.method, c.path])).toEqual([["POST", startPath]]),
    );
    expect(
      await screen.findByText(
        "Requested the PL of API version endpoint; it appears here once created",
      ),
    ).toBeInTheDocument();
  });

  it("explains a refused Start PL", async () => {
    const { server } = setup((_, project) => {
      project.pl = null;
    });
    server.failNext(409, { error: { code: "has_pl", message: "project already has a PL" } });
    open();
    fireEvent.click(screen.getByTestId("control-start-pl"));
    expect(
      await screen.findByText("PL not started: The project already has a PL; resume or restart it"),
    ).toBeInTheDocument();
  });

  it("offers Start PL only for a project without a PL that nothing else is starting", () => {
    setup();
    open();
    expect(screen.queryByTestId("control-start-pl")).toBeNull();
    cleanup();

    setup((_, project) => {
      project.pl = null;
      project.overlay = "waiting";
    });
    open();
    expect(screen.getByTestId("control-start-pl")).toHaveAttribute("aria-disabled", "true");
  });

  it("offers Resume inline on a paused project", () => {
    setup((_, project) => {
      project.overlay = "paused";
    });
    expect(screen.getByTestId("control-resume-quick-api-version")).toBeInTheDocument();
  });

  it("keeps Resume in the menu only on a done project", () => {
    setup((snapshot, project) => {
      project.stage = "done";
      const pl = snapshot.members.find((m) => m.session_id === project.pl!.session_id)!;
      pl.state = "done";
    });
    expect(screen.queryByTestId("control-resume-quick-api-version")).toBeNull();
    open();
    expect(screen.getByTestId("control-resume")).toBeInTheDocument();
  });

  it("offers Restart and Relaunch inline on a failed project", () => {
    setup((_, project) => {
      project.overlay = "failed";
    });
    expect(screen.getByTestId("control-restart-quick-api-version")).toBeInTheDocument();
    expect(screen.getByTestId("control-relaunch-quick-api-version")).toBeInTheDocument();
  });

  it("ages control events on the server's clock, not a skewed browser's", async () => {
    // The browser runs an hour ahead of the server; the pause was sent just now.
    const snapshot = liveRunSnapshot();
    snapshot.server_time = Math.floor(Date.now() / 1000) - 3600;
    const project = snapshot.projects.find((p) => p.slug === "api-version")!;
    const server = serveFactory(authenticatedFetch, snapshot);
    server.events = [
      {
        ...ackEvent("x", LIVE_RUN_PL_API_ID),
        id: "ev-now",
        ts: Date.now() - 3600_000,
        kind: "control.pause",
        from_id: null,
        project_slug: "api-version",
        data: { target: "project:api-version", recipients: [LIVE_RUN_PL_API_ID] },
      },
    ];
    function Live() {
      // The cockpit's snapshot read measures the server clock.
      useFactory(snapshot.factory.id);
      return (
        <ProjectControlsMenu
          factoryId={snapshot.factory.id}
          project={project}
          prs={[]}
          members={snapshot.members}
        />
      );
    }
    renderControls(<Live />, snapshot);
    const chip = await screen.findByTestId("control-pending-api-version");
    await waitFor(() =>
      expect(
        server.calls.some((c) => c.path === `/v1/factory/factories/${snapshot.factory.id}`),
      ).toBe(true),
    );
    await waitFor(() => expect(chip).toHaveAttribute("data-state", "waiting"));
  });

  it("turns amber after 15 min without a reply and offers a restart", async () => {
    setup(undefined, [
      {
        ...ackEvent("x", LIVE_RUN_PL_API_ID),
        id: "ev-old",
        ts: Date.now() - 20 * 60_000,
        kind: "control.pause",
        from_id: null,
        project_slug: "api-version",
        data: { target: "project:api-version", recipients: [LIVE_RUN_PL_API_ID] },
      },
    ]);
    const chip = await screen.findByTestId("control-pending-api-version");
    await waitFor(() => expect(chip).toHaveAttribute("data-state", "late"));
    expect(chip).toHaveTextContent("No reply from PL in 15 min");
    fireEvent.click(within(chip).getByTestId("control-late-restart"));
    expect(await screen.findByTestId("control-restart-dialog")).toBeInTheDocument();
  });
});
