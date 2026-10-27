import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  createEvent,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { preflightAllFailing, preflightOk } from "@/components/factory/__fixtures__/liveRun";
import { DRAFT_KEY } from "@/components/factory/start/projectForm";
import { CapabilitiesProvider } from "@/lib/CapabilitiesContext";
import { FALLBACK_SERVER_INFO, type ServerInfo } from "@/lib/capabilities";
import { useParams } from "@/lib/routing";
import { NewFactoryPage } from "./NewFactoryPage";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));

interface Call {
  method: string;
  path: string;
  body: unknown;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type Reply = () => Response | Promise<Response>;

function serve(options: { preflight?: unknown; register?: Reply[]; session?: Reply } = {}) {
  const calls: Call[] = [];
  const register = [...(options.register ?? [])];
  authenticatedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (path === "/v1/factory/preflight") return json(options.preflight ?? preflightOk());
    if (path === "/v1/connections/github/repos") {
      return json({
        connected: true,
        repos: [
          {
            full_name: "caffeinelabs/app",
            clone_url: null,
            default_branch: "main",
            private: true,
            pushed_at: null,
          },
        ],
      });
    }
    if (path.startsWith("/v1/connections/github/repos/")) {
      return json({ connected: true, branches: ["main", "staging"] });
    }
    if (path === "/v1/agents") return json({ data: [{ id: "ag_lead", name: "factory-lead" }] });
    if (path === "/v1/sessions" && method === "POST") {
      return options.session ? options.session() : json({ id: "conv_atlas" }, 201);
    }
    if (path === "/v1/factory/factories" && method === "POST") {
      const next = register.shift();
      return next ? next() : json({ factory_id: "conv_atlas", url: "/factory/conv_atlas" }, 201);
    }
    throw new Error(`unexpected ${method} ${path}`);
  });
  return calls;
}

function CockpitStub() {
  const { factoryId } = useParams<{ factoryId: string }>();
  return <p data-testid="cockpit-stub">cockpit {factoryId}</p>;
}

function renderPage(info: ServerInfo | "loading" = "loading") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <CapabilitiesProvider info={info}>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/factory/new"]}>
          <Routes>
            <Route path="/factory/new" element={<NewFactoryPage />} />
            <Route path="/factory/:factoryId" element={<CockpitStub />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </CapabilitiesProvider>,
  );
}

const type = (testId: string, value: string) =>
  fireEvent.change(screen.getByTestId(testId), { target: { value } });

const writes = (calls: Call[]) => calls.filter((c) => c.method !== "GET");

beforeEach(() => {
  authenticatedFetch.mockReset();
  localStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("NewFactoryPage", () => {
  it("lists GitHub repos only where the server offers the GitHub connection", async () => {
    const calls = serve();
    const { unmount } = renderPage({ ...FALLBACK_SERVER_INFO, enabled_connections: [] });
    fireEvent.click(screen.getByTestId("factory-new-repo-0"));
    expect(await screen.findByText("GitHub isn’t connected. Type owner/name.")).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/v1/connections/github/repos")).toBe(false);
    unmount();

    renderPage({ ...FALLBACK_SERVER_INFO, enabled_connections: ["github"] });
    fireEvent.click(screen.getByTestId("factory-new-repo-0"));
    expect(await screen.findByText("caffeinelabs/app")).toBeInTheDocument();
    expect(calls.filter((c) => c.path === "/v1/connections/github/repos")).toHaveLength(1);
  });

  it("derives the slug from the title until it is edited", () => {
    serve();
    renderPage();
    type("factory-new-project-title-0", "API status endpoint");
    expect(screen.getByTestId("factory-new-slug-0")).toHaveValue("api-status-endpoint");
    expect(screen.getByTestId("factory-new-slug-mode-0")).toHaveTextContent("auto");
    type("factory-new-slug-0", "api-status");
    expect(screen.getByTestId("factory-new-slug-mode-0")).toHaveTextContent("edited");
    type("factory-new-project-title-0", "Status route");
    expect(screen.getByTestId("factory-new-slug-0")).toHaveValue("api-status");
  });

  it("shows inline errors on submit and sends nothing", async () => {
    const calls = serve();
    renderPage();
    type("factory-new-name", "");
    fireEvent.click(screen.getByTestId("factory-new-submit"));
    expect(await screen.findByTestId("factory-new-error-name")).toHaveTextContent(
      "Name the factory",
    );
    expect(screen.getByTestId("factory-new-error-title-0")).toHaveTextContent("A slug is required");
    expect(screen.getByTestId("factory-new-error-repo-0")).toHaveTextContent("Pick a repository");
    expect(screen.getByTestId("factory-new-error-goal-0")).toHaveTextContent(
      "Say what the project should achieve",
    );
    expect(screen.getByTestId("factory-new-error-dod-0")).toHaveTextContent(
      "Add at least one check besides the fixed one",
    );
    type("factory-new-slug-0", "Bad slug!");
    expect(screen.getByTestId("factory-new-error-title-0")).toHaveTextContent(
      "Lowercase letters, digits and inner dashes; 1–48 characters",
    );
    expect(writes(calls)).toEqual([]);
  });

  it("loads the three-project example and sums it up", () => {
    serve();
    renderPage();
    fireEvent.click(screen.getByTestId("factory-new-load-example"));
    expect(screen.getByTestId("factory-new-slug-0")).toHaveValue("api-status");
    expect(screen.getByTestId("factory-new-slug-1")).toHaveValue("ux-env-badge");
    expect(screen.getByTestId("factory-new-slug-2")).toHaveValue("docs-factory-envs");
    expect(screen.getByTestId("factory-new-env-2")).toHaveAttribute("data-state", "unchecked");
    expect(screen.queryByTestId("factory-new-demo-2")).not.toBeInTheDocument();
    expect(screen.getByTestId("factory-new-demo-1")).toHaveValue(
      "Open the app in the preview, show the ENV badge in the header and hover it.",
    );

    const summary = screen.getByTestId("factory-new-preview");
    expect(within(summary).getByTestId("factory-new-preview-pl-0")).toHaveTextContent(
      "PL api-statusenv · 2 prev",
    );
    expect(within(summary).getByTestId("factory-new-preview-pl-2")).toHaveTextContent("no env");
    expect(within(summary).getByTestId("factory-new-preview-sandboxes")).toHaveTextContent(
      "5 sandboxes",
    );
    expect(within(summary).getByTestId("factory-new-preview-previews")).toHaveTextContent(
      "Up to 4 previews at once",
    );
    expect(within(summary).getByTestId("factory-new-preview-heartbeats")).toHaveTextContent(
      "≈42 heartbeat turns/h max",
    );

    fireEvent.click(screen.getByTestId("factory-new-cadence-normal"));
    expect(within(summary).getByTestId("factory-new-preview-heartbeats")).toHaveTextContent(
      "≈4 heartbeat turns/h max",
    );
    fireEvent.click(screen.getByTestId("factory-new-preview-0"));
    expect(within(summary).getByTestId("factory-new-preview-previews")).toHaveTextContent(
      "Up to 2 previews at once",
    );
    expect(within(summary).getByTestId("factory-new-preview-pl-0")).toHaveTextContent("env");
  });

  it("keeps a draft and offers to restore it", async () => {
    serve();
    const first = renderPage();
    type("factory-new-project-title-0", "Kept project");
    await waitFor(() => expect(localStorage.getItem(DRAFT_KEY)).toContain("Kept project"));
    first.unmount();

    renderPage();
    const banner = screen.getByTestId("factory-new-draft");
    expect(banner).toHaveTextContent("Restore draft?");
    expect(screen.getByTestId("factory-new-project-title-0")).toHaveValue("");
    fireEvent.click(screen.getByTestId("factory-new-draft-restore"));
    expect(screen.getByTestId("factory-new-project-title-0")).toHaveValue("Kept project");
    expect(screen.queryByTestId("factory-new-draft")).not.toBeInTheDocument();
  });

  it("leaves the saved draft alone while Restore or Discard is pending", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const saved = {
      savedAt: Date.now(),
      draft: { name: "Old", cadence: "demo", projects: [{ title: "Old project", goal: "x" }] },
    };
    localStorage.setItem(DRAFT_KEY, JSON.stringify(saved));
    serve();
    renderPage();
    type("factory-new-project-title-0", "New edits");
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(localStorage.getItem(DRAFT_KEY)).toContain("Old project");
    fireEvent.click(screen.getByTestId("factory-new-draft-discard"));
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(localStorage.getItem(DRAFT_KEY)).toContain("New edits");
  });

  it("discards a draft", () => {
    localStorage.setItem(
      DRAFT_KEY,
      JSON.stringify({
        savedAt: Date.now(),
        draft: { name: "Old", cadence: "demo", projects: [{ title: "Old project", goal: "x" }] },
      }),
    );
    serve();
    renderPage();
    fireEvent.click(screen.getByTestId("factory-new-draft-discard"));
    expect(screen.queryByTestId("factory-new-draft")).not.toBeInTheDocument();
    expect(localStorage.getItem(DRAFT_KEY)).toBeNull();
  });

  it("creates Atlas, then stores the charters, then opens the cockpit", async () => {
    let releaseSession: () => void = () => {};
    const calls = serve({
      session: () =>
        new Promise((resolve) => {
          releaseSession = () => resolve(json({ id: "conv_atlas" }, 201));
        }),
    });
    renderPage();
    fireEvent.click(screen.getByTestId("factory-new-load-example"));
    fireEvent.click(screen.getByTestId("factory-new-submit"));
    expect(await screen.findByTestId("factory-new-progress")).toHaveTextContent("Creating Atlas…");
    expect(screen.getByTestId("factory-new-submit")).toBeDisabled();
    await act(async () => releaseSession());
    expect(await screen.findByTestId("cockpit-stub")).toHaveTextContent("cockpit conv_atlas");

    const sent = writes(calls);
    expect(sent.map((c) => `${c.method} ${c.path}`)).toEqual([
      "POST /v1/sessions",
      "POST /v1/factory/factories",
    ]);
    expect(sent[0]!.body).toMatchObject({
      agent_id: "ag_lead",
      host_type: "managed",
      workspaces: ["https://github.com/caffeinelabs/app#main"],
      title: "Atlas",
    });
    const register = sent[1]!.body as {
      root_session_id: string;
      name: string;
      projects: unknown[];
    };
    expect(register).toMatchObject({
      root_session_id: "conv_atlas",
      name: "Atlas factory",
      cadence: "demo",
    });
    expect(register.projects).toHaveLength(3);
    expect(register.projects[2]).toMatchObject({
      slug: "docs-factory-envs",
      env_needed: false,
      preview: { enabled: false, slots: 2 },
      demo_focus: null,
    });
    expect(localStorage.getItem(DRAFT_KEY)).toBeNull();
  });

  it("retries storing the charters against the same Atlas", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const unavailable = () => json({ error: { message: "Service unavailable" } }, 503);
    const calls = serve({ register: [unavailable, unavailable, unavailable, unavailable] });
    renderPage();
    fireEvent.click(screen.getByTestId("factory-new-load-example"));
    fireEvent.click(screen.getByTestId("factory-new-submit"));
    await act(() => vi.advanceTimersByTimeAsync(5000));
    const failure = await screen.findByTestId("factory-new-failure");
    expect(failure).toHaveTextContent("Atlas was created, but the charters weren’t stored");
    expect(failure).toHaveTextContent("Service unavailable");

    expect(screen.getByTestId("factory-new-retry-note")).toHaveTextContent(
      "Retry stores the charters with this Atlas.",
    );
    expect(screen.getByTestId("factory-new-submit")).toHaveTextContent("Retry storing charters");
    fireEvent.click(screen.getByTestId("factory-new-submit"));
    expect(await screen.findByTestId("cockpit-stub")).toBeInTheDocument();
    const sent = writes(calls).map((c) => `${c.method} ${c.path}`);
    expect(sent.filter((c) => c === "POST /v1/sessions")).toHaveLength(1);
    expect(sent.filter((c) => c === "POST /v1/factory/factories")).toHaveLength(5);
    const last = writes(calls).at(-1)!.body as { root_session_id: string };
    expect(last.root_session_id).toBe("conv_atlas");
  });

  it("creates a new Atlas when the first project's branch changed after a failed start", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const unavailable = () => json({ error: { message: "Service unavailable" } }, 503);
    const calls = serve({ register: [unavailable, unavailable, unavailable, unavailable] });
    renderPage();
    fireEvent.click(screen.getByTestId("factory-new-load-example"));
    fireEvent.click(screen.getByTestId("factory-new-submit"));
    await act(() => vi.advanceTimersByTimeAsync(5000));
    await screen.findByTestId("factory-new-failure");

    fireEvent.click(screen.getByTestId("factory-new-branch-0"));
    fireEvent.change(await screen.findByTestId("factory-new-branch-0-search"), {
      target: { value: "release" },
    });
    fireEvent.click(await screen.findByText("release"));
    expect(screen.getByTestId("factory-new-branch-0")).toHaveTextContent("release");
    expect(screen.getByTestId("factory-new-retry-note")).toHaveTextContent(
      "The first project’s repository changed, so Start creates a new Atlas",
    );
    expect(screen.getByTestId("factory-new-submit")).toHaveTextContent("Start factory");

    fireEvent.click(screen.getByTestId("factory-new-submit"));
    expect(await screen.findByTestId("cockpit-stub")).toBeInTheDocument();
    const sessions = writes(calls).filter((c) => c.path === "/v1/sessions");
    expect(sessions).toHaveLength(2);
    expect(sessions[1]!.body).toMatchObject({
      workspaces: ["https://github.com/caffeinelabs/app#release"],
    });
  });

  it("puts a server error on a field without a slot into the failure alert", async () => {
    serve({
      register: [
        () =>
          json(
            {
              error: { code: "invalid_body", message: "demo_focus needs previews" },
              field: "projects[2].demo_focus",
            },
            400,
          ),
        () =>
          json(
            {
              error: { code: "invalid_body", message: "priority must be at most 3" },
              field: "projects[0].priority",
            },
            400,
          ),
      ],
    });
    renderPage();
    fireEvent.click(screen.getByTestId("factory-new-load-example"));
    fireEvent.click(screen.getByTestId("factory-new-submit"));
    // Project 3 has no env, so it shows no demo field: the alert says it.
    expect(await screen.findByTestId("factory-new-failure")).toHaveTextContent(
      "Demo_focus needs previews",
    );
    fireEvent.click(screen.getByTestId("factory-new-submit"));
    expect(await screen.findByTestId("factory-new-error-priority-0")).toHaveTextContent(
      "Priority must be at most 3",
    );
  });

  it("never submits on Enter in a one-line field", () => {
    serve();
    renderPage();
    fireEvent.click(screen.getByTestId("factory-new-load-example"));
    const title = screen.getByTestId("factory-new-project-title-0");
    const enter = createEvent.keyDown(title, { key: "Enter" });
    fireEvent(title, enter);
    expect(enter.defaultPrevented).toBe(true);
    const goal = screen.getByTestId("factory-new-goal-0");
    const newline = createEvent.keyDown(goal, { key: "Enter" });
    fireEvent(goal, newline);
    expect(newline.defaultPrevented).toBe(false);
  });

  it("puts the server's field errors on the field", async () => {
    serve({
      register: [
        () =>
          json(
            {
              error: { code: "invalid_body", message: "slug 'ux-env-badge' is used twice" },
              field: "projects[1].slug",
              message: "slug 'ux-env-badge' is used twice",
            },
            400,
          ),
      ],
    });
    renderPage();
    fireEvent.click(screen.getByTestId("factory-new-load-example"));
    fireEvent.click(screen.getByTestId("factory-new-submit"));
    expect(await screen.findByTestId("factory-new-error-title-1")).toHaveTextContent(
      "Slug 'ux-env-badge' is used twice",
    );
    expect(screen.queryByTestId("factory-new-failure")).not.toBeInTheDocument();
    // Editing the field clears the server's message.
    type("factory-new-slug-1", "ux-env-badge-2");
    expect(screen.queryByTestId("factory-new-error-title-1")).not.toBeInTheDocument();
  });

  it("shows a conflict inline", async () => {
    serve({
      register: [
        () =>
          json(
            {
              error: {
                code: "factory_exists",
                message: "this lead already runs a factory with other projects",
              },
            },
            409,
          ),
      ],
    });
    renderPage();
    fireEvent.click(screen.getByTestId("factory-new-load-example"));
    fireEvent.click(screen.getByTestId("factory-new-submit"));
    expect(await screen.findByTestId("factory-new-failure")).toHaveTextContent(
      "This lead already runs a factory with other projects",
    );
  });

  it("blocks the start while the preflight fails", async () => {
    const calls = serve({ preflight: preflightAllFailing() });
    renderPage();
    expect(await screen.findByTestId("factory-new-preflight")).toHaveTextContent(
      "This server can’t start a factory yet",
    );
    expect(screen.getByTestId("factory-new-submit")).toBeDisabled();
    expect(writes(calls)).toEqual([]);
  });

  it("adds, duplicates and removes projects", () => {
    serve();
    renderPage();
    fireEvent.click(screen.getByTestId("factory-new-add-project"));
    expect(screen.getByTestId("factory-new-project-1")).toBeInTheDocument();
    type("factory-new-project-title-1", "Second");
    fireEvent.click(screen.getByTestId("factory-new-duplicate-1"));
    expect(screen.getByTestId("factory-new-slug-2")).toHaveValue("second-2");
    fireEvent.click(screen.getByTestId("factory-new-remove-0"));
    expect(screen.getByTestId("factory-new-slug-0")).toHaveValue("second");
    expect(screen.queryByTestId("factory-new-project-2")).not.toBeInTheDocument();
  });
});
