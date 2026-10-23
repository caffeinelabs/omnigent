// HTTP contract of the child-session fetch and its factory opt-in: the hook
// asks the server for factory members too, so a lead's Agents tree renders
// its project leads (top-level sessions joined by label) and their ICs.

import { cleanup, render, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useSession } from "@/hooks/useSession";
import { SubagentsPanel } from "@/shell/SubagentsPanel";
import {
  childSessionsQueryKey,
  fetchChildSessions,
  isSubAgentChild,
  STREAM_SEEDED_UPDATED_AT,
  useChildSessions,
  type ChildSessionInfo,
} from "./useChildSessions";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));

vi.mock("@/hooks/useSession", () => ({
  useSession: vi.fn(),
}));

vi.mock("@/components/icons/ClaudeIcon", () => ({
  ClaudeIcon: (props: Record<string, unknown>) => <svg {...props} data-icon="claude" />,
}));
vi.mock("@/components/icons/OttoIcon", () => ({
  OttoIcon: (props: Record<string, unknown>) => <svg {...props} data-icon="otto" />,
}));

const useSessionMock = vi.mocked(useSession);

function row(id: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    title: null,
    tool: null,
    session_name: null,
    current_task_status: null,
    busy: false,
    ...overrides,
  };
}

/** Serve `child_sessions` lists from an id-keyed tree; unknown ids get `[]`. */
function serveTree(tree: Record<string, ReturnType<typeof row>[]>) {
  authenticatedFetch.mockImplementation(async (url: string) => {
    const match = /\/v1\/sessions\/([^/]+)\/child_sessions/.exec(url);
    const id = match ? decodeURIComponent(match[1]) : "";
    return new Response(JSON.stringify({ object: "list", data: tree[id] ?? [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
}

function wrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>
    );
  };
}

function fetchedUrls(): string[] {
  return authenticatedFetch.mock.calls.map((call) => String(call[0]));
}

beforeEach(() => {
  authenticatedFetch.mockReset();
  useSessionMock.mockReturnValue({ session: null, isLoading: false, error: null });
});

afterEach(cleanup);

describe("fetchChildSessions", () => {
  it("leaves the default request untouched", async () => {
    serveTree({});
    await fetchChildSessions("conv/lead");
    expect(fetchedUrls()).toEqual(["/v1/sessions/conv%2Flead/child_sessions"]);
  });

  it("asks for factory members when includeFactory is set", async () => {
    serveTree({
      conv_lead: [
        row("conv_pl", {
          title: "PL · api-version",
          labels: { "omnigent.factory.parent": "conv_lead" },
        }),
      ],
    });

    const rows = await fetchChildSessions("conv_lead", { includeFactory: true });

    expect(fetchedUrls()).toEqual(["/v1/sessions/conv_lead/child_sessions?include_factory=true"]);
    expect(rows.map((r) => [r.id, r.labels])).toEqual([
      ["conv_pl", { "omnigent.factory.parent": "conv_lead" }],
    ]);
  });

  it("carries each row's kind", async () => {
    serveTree({
      conv_lead: [row("conv_pl", { kind: "default" }), row("conv_sub", { kind: "sub_agent" })],
    });

    const rows = await fetchChildSessions("conv_lead", { includeFactory: true });

    expect(rows.map((r) => [r.id, r.kind, isSubAgentChild(r)])).toEqual([
      ["conv_pl", "default", false],
      ["conv_sub", "sub_agent", true],
    ]);
  });
});

describe("isSubAgentChild", () => {
  it("treats rows without a kind (stream-seeded, older servers) as sub-agents", () => {
    const base = { id: "c", title: null, task_summary: null, tool: null, session_name: null };
    const info = (kind?: string | null) =>
      ({ ...base, kind, current_task_status: null, busy: false }) as ChildSessionInfo;
    expect(isSubAgentChild(info(undefined))).toBe(true);
    expect(isSubAgentChild(info(null))).toBe(true);
    expect(isSubAgentChild(info("default"))).toBe(false);
  });
});

describe("useChildSessions", () => {
  it("passes include_factory=true", async () => {
    serveTree({ conv_lead: [row("conv_pl", { title: "PL · api-version" })] });
    const client = new QueryClient();

    const { result } = renderHook(() => useChildSessions("conv_lead"), {
      wrapper: wrapper(client),
    });

    await waitFor(() => expect(result.current.children).toHaveLength(1));
    expect(fetchedUrls()).toEqual(["/v1/sessions/conv_lead/child_sessions?include_factory=true"]);
  });

  it("fetches a cache seeded from the stream on mount, but not a fetched one", async () => {
    serveTree({
      conv_lead: [row("conv_sub"), row("conv_pl", { kind: "default", title: "PL · api" })],
    });
    const client = new QueryClient();
    // The stream's snapshot lists sub-agents only.
    client.setQueryData(childSessionsQueryKey("conv_lead"), [row("conv_sub")], {
      updatedAt: STREAM_SEEDED_UPDATED_AT,
    });
    client.setQueryData(childSessionsQueryKey("conv_fetched"), [row("conv_x")]);

    const seeded = renderHook(() => useChildSessions("conv_lead"), {
      wrapper: wrapper(client),
    });
    const fetched = renderHook(() => useChildSessions("conv_fetched"), {
      wrapper: wrapper(client),
    });

    await waitFor(() => expect(seeded.result.current.children).toHaveLength(2));
    expect(fetched.result.current.children.map((c) => c.id)).toEqual(["conv_x"]);
    expect(fetchedUrls()).toEqual(["/v1/sessions/conv_lead/child_sessions?include_factory=true"]);
  });

  it("nests factory rows: lead → project leads → ICs", async () => {
    serveTree({
      conv_lead: [
        row("conv_sentinel", {
          title: "Lead B · Sentinel",
          labels: { "omnigent.factory.role": "lead", "omnigent.factory.parent": "conv_lead" },
        }),
        row("conv_pl", {
          title: "PL · api-version",
          labels: {
            "omnigent.factory.role": "project_lead",
            "omnigent.factory.project": "api-version",
            "omnigent.factory.parent": "conv_lead",
            "omnigent.factory.summary": "env up",
            "omnigent.factory.health": "blocked",
          },
        }),
      ],
      conv_pl: [
        row("conv_ic", { title: "ic:task-1", tool: "ic", session_name: "task-1" }),
        row("conv_env", { title: "env_keeper:env", tool: "env_keeper", session_name: "env" }),
      ],
    });
    const client = new QueryClient();

    const { container } = render(
      <SubagentsPanel conversationId="conv_lead" rootSessionId="conv_lead" />,
      { wrapper: wrapper(client) },
    );

    await waitFor(() =>
      expect(container.querySelector('[data-child-session-id="conv_env"]')).not.toBeNull(),
    );
    const depthOf = (id: string) =>
      container.querySelector<HTMLElement>(`[data-child-session-id="${id}"]`)?.dataset.depth;
    expect(depthOf("conv_sentinel")).toBe("1");
    expect(depthOf("conv_pl")).toBe("1");
    expect(depthOf("conv_ic")).toBe("2");
    expect(depthOf("conv_env")).toBe("2");
    const pl = container.querySelector<HTMLElement>('[data-child-session-id="conv_pl"]');
    expect(pl?.textContent).toContain("PL · api-version");
    expect(
      pl?.querySelector('[data-testid="subagent-factory-badge"]')?.className.split(/\s+/),
    ).toContain("text-warning");
    // Every tree level, including the factory member's, opts into factory rows.
    const urls = fetchedUrls();
    expect(urls).toContain("/v1/sessions/conv_lead/child_sessions?include_factory=true");
    expect(urls).toContain("/v1/sessions/conv_pl/child_sessions?include_factory=true");
    expect(urls).toContain("/v1/sessions/conv_sentinel/child_sessions?include_factory=true");
    expect(urls.every((u) => u.endsWith("?include_factory=true"))).toBe(true);
  });
});
