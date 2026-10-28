import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LIVE_RUN_FACTORY_ID } from "@/components/factory/__fixtures__/liveRun";
import { resetFactoryControls } from "@/components/factory/controls/useFactoryControls";
import { busyThread, chatPage } from "@/components/factory/room/__fixtures__/roomFixtures";
import { round3Snapshot } from "@/components/factory/shell/__fixtures__/shellFixtures";
import type { EventView, FactorySnapshot, ViewerRole } from "@/lib/factoryApi";
import { FactoryPage } from "./FactoryPage";
import { type FactoryServer, SNAPSHOT, json, serveFactory } from "./testServer";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({ useSessionUpdatesConnected: () => true }));

const FID = LIVE_RUN_FACTORY_ID;

function setWidth(wide: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches:
        query.includes("min-width: 1280px") || query.includes("min-width: 1024px") ? wide : false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

let server: FactoryServer;
function open(
  path: string,
  options: {
    role?: ViewerRole;
    snapshot?: FactorySnapshot;
    overrides?: Record<string, () => Response | Promise<Response>>;
    events?: EventView[];
  } = {},
) {
  server = serveFactory(authenticatedFetch, {
    snapshot: () => json(options.snapshot ?? round3Snapshot(options.role ?? "creator")),
    overrides: options.overrides,
    ...(options.events ? { events: () => ({ data: options.events!, has_more: false }) } : {}),
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/factory/${FID}${path}`]}>
        <Routes>
          <Route path="/factory/:factoryId/*" element={<FactoryPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  authenticatedFetch.mockReset();
  resetFactoryControls();
  setWidth(true);
  localStorage.clear();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

const composer = () => screen.getByTestId("room-composer-input") as HTMLTextAreaElement;
const typeIn = (value: string) =>
  fireEvent.change(composer(), { target: { value, selectionStart: value.length } });

describe("Room page", () => {
  it("lists threads with unread counts and shows #factory with its cards", async () => {
    open("/room");
    const list = await screen.findByTestId("room-thread-list");
    const factory = await within(list).findByTestId("room-thread-factory");
    expect(factory).toHaveAttribute("aria-current", "page");
    // The open thread reads as read; the others keep their counts.
    await waitFor(() => expect(within(factory).queryByTestId("room-thread-unread")).toBeNull());
    expect(within(list).getByTestId("room-thread-project:db-version")).toHaveTextContent("3");
    expect(
      within(list).getByTestId(`room-thread-pr:8f15c0f9a98e5831a39615fb58f35036`),
    ).toHaveTextContent("!9631");
    expect(await screen.findByTestId("proposal-card-C-4")).toHaveAttribute("data-state", "pending");
    expect(screen.getByTestId("decision-embed-D-34")).toBeInTheDocument();
    const mia = screen.getByTestId("room-message-6");
    expect(mia).toHaveTextContent("Mia");
    expect(within(mia).getByTestId("room-ref-@atlas")).toHaveTextContent("@Atlas");
    expect(mia).toHaveTextContent("ui must wait for the API");
    expect(
      within(screen.getByTestId("room-body-2")).getByTestId("room-ref-C-2"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("room-context")).toHaveTextContent("Atlas factory");
  });

  it("opens a project thread with its loop remedies and context", async () => {
    open("/room/project%3Adb-version");
    expect(await screen.findByTestId("loop-card-D-31")).toHaveAttribute("data-state", "exhausted");
    expect(screen.getByTestId("loop-remedy-D-31-raise")).toHaveTextContent("+1 attempt");
    const context = screen.getByTestId("room-context");
    expect(context).toHaveTextContent("DB version endpoint");
    expect(within(context).getByTestId("room-open-project")).toHaveAttribute(
      "href",
      `/factory/${FID}/projects/db-version`,
    );
  });

  it("pages back through a long thread with before_seq", async () => {
    const messages = busyThread("factory", 120);
    open("/room", {
      overrides: {
        [`GET ${SNAPSHOT}/chat`]: () =>
          json(chatPage(messages, String(authenticatedFetch.mock.lastCall?.[0]))),
      },
    });
    expect(await screen.findByTestId("room-message-1120")).toBeInTheDocument();
    expect(screen.queryByTestId("room-message-1050")).toBeNull();
    fireEvent.click(screen.getByTestId("room-load-earlier"));
    expect(await screen.findByTestId("room-message-1050")).toBeInTheDocument();
    const reads = server.calls.filter((c) => c.path.includes("/chat?"));
    expect(reads.some((c) => c.path.includes("before_seq=1071"))).toBe(true);
  });

  it("runs /pause as a control, never as a message", async () => {
    open("/room");
    await screen.findByTestId("room-message-1");
    typeIn("/pause #db-version");
    expect(screen.getByTestId("room-composer-hint")).toHaveTextContent(
      "Pause #db-version · keep previews",
    );
    fireEvent.click(screen.getByTestId("room-composer-send"));
    await waitFor(() =>
      expect(server.controls).toEqual([
        { action: "pause", target: "project:db-version", options: { keep_previews: true } },
      ]),
    );
    expect(server.posts).toEqual([]);
    expect(await screen.findByTestId("room-note")).toHaveTextContent(
      "Pause #db-version · keep previews · sent",
    );
  });

  it("shows /status as a note only the viewer sees", async () => {
    open("/room");
    await screen.findByTestId("room-message-1");
    typeIn("/status");
    fireEvent.click(screen.getByTestId("room-composer-send"));
    expect(await screen.findByTestId("room-status-digest")).toHaveTextContent("Atlas factory");
    expect(server.posts).toEqual([]);
  });

  it("says why a command can't run", async () => {
    open("/room");
    await screen.findByTestId("room-message-1");
    typeIn("/pause #nope");
    expect(screen.getByTestId("room-composer-hint")).toHaveTextContent("No project #nope here");
    expect(screen.getByTestId("room-composer-hint")).toHaveAttribute("data-ok", "false");
  });

  it("completes typed tokens from the snapshot", async () => {
    open("/room");
    await screen.findByTestId("room-message-1");
    typeIn("ask @pl:db");
    const menu = await screen.findByTestId("room-composer-menu");
    expect(within(menu).getByTestId("room-composer-option-@pl:db-version")).toBeInTheDocument();
    fireEvent.keyDown(composer(), { key: "Enter" });
    expect(composer().value).toBe("ask @pl:db-version ");
    typeIn("see !96");
    expect(
      within(await screen.findByTestId("room-composer-menu")).getAllByRole("option").length,
    ).toBeGreaterThan(1);
  });

  it("interleaves the bus as one-liners when asked", async () => {
    const event: EventView = {
      id: "ev-bus-1",
      ts: (round3Snapshot().server_time - 30) * 1000,
      kind: "message.queued",
      severity: "info",
      project_slug: null,
      from_id: "a",
      to_id: "b",
      from_title: "Atlas",
      to_title: "Lead B · Sentinel",
      subject: null,
      summary: "verify !9631",
      data: { kind: "request", excerpt: "verify !9631" },
    };
    open("/room", { events: [event] });
    await screen.findByTestId("room-message-1");
    expect(screen.queryByTestId("room-bus-ev-bus-1")).toBeNull();
    fireEvent.click(screen.getByTestId("room-bus-toggle"));
    expect(await screen.findByTestId("room-bus-ev-bus-1")).toHaveTextContent(
      "Atlas → Lead B · Sentinel · request · verify !9631",
    );
    const read = server.calls.find(
      (c) => c.path.includes("/events?") && c.path.includes("message.queued"),
    );
    expect(read).toBeDefined();
  });

  it("is read-only for a viewer", async () => {
    open("/room", { role: "viewer" });
    await screen.findByTestId("room-message-1");
    expect(composer()).toBeDisabled();
    expect(screen.getByTestId("room-composer-identity")).toHaveTextContent(
      "Operators can post in the Room",
    );
    expect(await screen.findByTestId("proposal-apply-C-4")).toBeDisabled();
  });

  it("marks the open thread read", async () => {
    open("/room");
    await screen.findByTestId("room-message-15");
    await waitFor(() =>
      expect(
        server.calls.find((c) => c.method === "PUT" && c.path.endsWith("/chat/reads"))?.body,
      ).toEqual({
        thread: "factory",
        last_seq: 15,
      }),
    );
  });
});

describe("Decisions page", () => {
  it("lists what is assigned to me first, then my role's, with proposal cards", async () => {
    open("/decisions", { role: "operator" });
    const page = await screen.findByTestId("factory-decisions-page");
    const mine = within(page).getByTestId("decisions-mine");
    expect(within(mine).getByTestId("decision-embed-D-2")).toBeInTheDocument();
    const role = within(page).getByTestId("decisions-role");
    expect(await within(role).findByTestId("proposal-card-C-4")).toBeInTheDocument();
    expect(within(role).getByTestId("decision-embed-D-31")).toBeInTheDocument();
    const others = within(page).getByTestId("decisions-others");
    expect(within(others).getByTestId("decision-embed-D-3")).toBeInTheDocument();
    expect(within(mine).getByTestId("decision-assign-bar-D-2")).toHaveTextContent("for you");
  });

  it("disables Assign with Claim for a viewer, and on an owners' decision for an operator", async () => {
    const viewer = open("/decisions", { role: "viewer" });
    expect(await screen.findByTestId("decision-assign-D-2")).toBeDisabled();
    expect(screen.getByTestId("decision-claim-D-2")).toBeDisabled();
    viewer.unmount();
    open("/decisions", { role: "operator" });
    expect(await screen.findByTestId("decision-assign-D-34")).toBeDisabled();
    expect(screen.getByTestId("decision-assign-D-2")).toBeEnabled();
  });

  it("filters by source", async () => {
    open("/decisions");
    await screen.findByTestId("factory-decisions-page");
    // Radix Select: pick through its hidden native select.
    const trigger = screen.getByTestId("decisions-filter-source");
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    fireEvent.click(await screen.findByRole("option", { name: "Loop" }));
    await waitFor(() => expect(screen.queryByTestId("decision-embed-D-2")).toBeNull());
    expect(screen.getByTestId("decision-embed-D-31")).toBeInTheDocument();
  });

  it("claims a decision", async () => {
    open("/decisions", {
      role: "operator",
      overrides: {
        "POST /v1/factory/decisions/ae1fb5c75b755a4dbc645077510d63ef/claim": () =>
          json({ decision: { ...round3Snapshot().decisions[2]!, claimed_by: "mia@caffeine.ai" } }),
      },
    });
    fireEvent.click(await screen.findByTestId("decision-claim-D-31"));
    await waitFor(() =>
      expect(
        server.calls.some((c) => c.path.endsWith("/ae1fb5c75b755a4dbc645077510d63ef/claim")),
      ).toBe(true),
    );
  });
});

describe("Dock decisions", () => {
  it("draws a proposal as its card, whose Edit & apply opens the form without answering", async () => {
    open("", { role: "operator" });
    const dock = await screen.findByTestId("room-dock");
    fireEvent.mouseDown(within(dock).getByTestId("room-dock-tab-decisions"));
    const card = await within(dock).findByTestId("proposal-card-C-4");
    expect(within(dock).queryByTestId("decision-card-D-33")).toBeNull();
    fireEvent.click(within(card).getByTestId("proposal-edit-C-4"));
    expect(await screen.findByTestId("edit-apply-dialog")).toBeInTheDocument();
    expect(server.calls.some((c) => c.method === "POST" && c.path.endsWith("/answer"))).toBe(false);
    expect(server.calls.some((c) => c.method === "POST" && c.path.includes("/proposals/"))).toBe(
      false,
    );
  });
});

describe("Graph page", () => {
  it("draws the work graph and opens a task's inspector", async () => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    open("/graph?task=T-4");
    // 11 tasks in 5 lanes under 3 wave headers.
    expect(await screen.findByTestId("graph-work")).toHaveAttribute("data-nodes", "19");
    const inspector = await screen.findByTestId("graph-task-inspector");
    expect(inspector).toHaveAttribute("data-task", "T-4");
    expect(inspector).toHaveTextContent("critical path");
    expect(within(inspector).getByTestId("graph-task-card")).toBeEnabled();
  });

  it("scopes to one project without leaving the Graph", async () => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    open("/graph?scope=db-version");
    expect(await screen.findByTestId("factory-graph-page")).toBeInTheDocument();
    await waitFor(() =>
      expect(
        server.calls.some(
          (c) => c.path.includes("/graph?") && c.path.includes("project=db-version"),
        ),
      ).toBe(true),
    );
    expect(screen.queryByTestId("project-page-db-version")).toBeNull();
  });

  it("switches to the bus, flow and org graphs", async () => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    open("/graph?kind=bus");
    expect(await screen.findByTestId("graph-bus")).toHaveAttribute("data-nodes", "12");
    expect(screen.getByTestId("graph-bus-flags")).toHaveTextContent("ping-pong");
    fireEvent.mouseDown(screen.getByTestId("graph-kind-flow"));
    expect(await screen.findByTestId("graph-flow")).toHaveAttribute("data-nodes", "7");
    fireEvent.mouseDown(screen.getByTestId("graph-kind-org"));
    expect(await screen.findByTestId("graph-org-summary")).toHaveTextContent("41 agents");
  });
});
