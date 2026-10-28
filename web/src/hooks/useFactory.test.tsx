import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { liveRunSnapshot } from "@/components/factory/__fixtures__/liveRun";
import {
  FACTORY_FALLBACK_POLL_MS,
  factoryQueryKey,
  useFactories,
  useFactory,
  useFactoryDecisionCount,
  nextEventCursor,
  useFactoryEvents,
  useFactorySpans,
  useProjectDemos,
} from "./useFactory";
import type { EventPage } from "@/lib/factoryApi";
import { useSessionUpdatesConnected } from "./useSessionUpdatesConnected";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("./useSessionUpdatesConnected", () => ({ useSessionUpdatesConnected: vi.fn() }));

function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

function wrapperFor(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

function newClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

const FID = "f4f4ba9c77725331b12f0fa7072dc4d3";

beforeEach(() => {
  authenticatedFetch.mockReset();
  vi.mocked(useSessionUpdatesConnected).mockReturnValue(true);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useFactory", () => {
  it("keeps the last snapshot on screen when a refresh fails, and says so", async () => {
    const snapshot = liveRunSnapshot();
    authenticatedFetch.mockResolvedValueOnce(json(snapshot, 200, { ETag: '"v1"' }));
    const client = newClient();
    const { result } = renderHook(() => useFactory(FID), { wrapper: wrapperFor(client) });
    await waitFor(() => expect(result.current.data).toBeDefined());
    const firstUpdate = result.current.updatedAt;
    expect(result.current.isStale).toBe(false);
    expect(firstUpdate).toBeGreaterThan(0);

    authenticatedFetch.mockResolvedValueOnce(json({ error: { message: "down" } }, 503));
    await act(() => client.invalidateQueries({ queryKey: factoryQueryKey(FID) }));

    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.data?.factory.name).toBe("Atlas factory");
    expect(result.current.isStale).toBe(true);
    expect(result.current.updatedAt).toBe(firstUpdate);
  });

  it("revalidates with the ETag and keeps the same object on a 304", async () => {
    authenticatedFetch.mockResolvedValueOnce(json(liveRunSnapshot(), 200, { ETag: '"v1"' }));
    const client = newClient();
    const { result } = renderHook(() => useFactory(FID), { wrapper: wrapperFor(client) });
    await waitFor(() => expect(result.current.data).toBeDefined());
    const first = result.current.data;

    authenticatedFetch.mockResolvedValueOnce(new Response(null, { status: 304 }));
    await act(() => client.invalidateQueries({ queryKey: factoryQueryKey(FID) }));

    await waitFor(() => expect(authenticatedFetch).toHaveBeenCalledTimes(2));
    expect(authenticatedFetch.mock.calls[1]![1]).toMatchObject({
      headers: { "If-None-Match": '"v1"' },
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.data).toBe(first);
    expect(result.current.error).toBeNull();
  });

  it("polls every 15 s only while the session-updates socket is down", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    authenticatedFetch.mockImplementation(async () => json(liveRunSnapshot()));
    vi.mocked(useSessionUpdatesConnected).mockReturnValue(true);
    const client = newClient();
    const { result, rerender } = renderHook(() => useFactory(FID), {
      wrapper: wrapperFor(client),
    });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(authenticatedFetch).toHaveBeenCalledTimes(1);

    // Live socket: frames drive freshness, so no timer refetch.
    await act(() => vi.advanceTimersByTimeAsync(FACTORY_FALLBACK_POLL_MS * 2));
    expect(authenticatedFetch).toHaveBeenCalledTimes(1);

    vi.mocked(useSessionUpdatesConnected).mockReturnValue(false);
    rerender();
    await act(() => vi.advanceTimersByTimeAsync(FACTORY_FALLBACK_POLL_MS + 10));
    expect(authenticatedFetch.mock.calls.length).toBeGreaterThanOrEqual(2);
    const afterOnePoll = authenticatedFetch.mock.calls.length;
    await act(() => vi.advanceTimersByTimeAsync(FACTORY_FALLBACK_POLL_MS));
    expect(authenticatedFetch.mock.calls.length).toBe(afterOnePoll + 1);

    vi.mocked(useSessionUpdatesConnected).mockReturnValue(true);
    rerender();
    const reconnected = authenticatedFetch.mock.calls.length;
    await act(() => vi.advanceTimersByTimeAsync(FACTORY_FALLBACK_POLL_MS * 2));
    expect(authenticatedFetch.mock.calls.length).toBe(reconnected);
  });

  it("re-reads at least every minRefreshMs while the socket is live", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    authenticatedFetch.mockImplementation(async () => json(liveRunSnapshot()));
    const client = newClient();
    const { result } = renderHook(() => useFactory(FID, { minRefreshMs: 60_000 }), {
      wrapper: wrapperFor(client),
    });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(authenticatedFetch).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(60_000 + 10));
    expect(authenticatedFetch).toHaveBeenCalledTimes(2);
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(authenticatedFetch).toHaveBeenCalledTimes(3);
  });

  it("measures the server clock against the browser's when a body arrives", async () => {
    const snapshot = liveRunSnapshot();
    snapshot.server_time = Math.floor(Date.now() / 1000) + 300;
    authenticatedFetch.mockResolvedValueOnce(json(snapshot, 200, { ETag: '"v1"' }));
    const client = newClient();
    const { result } = renderHook(() => useFactory(FID), { wrapper: wrapperFor(client) });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.clockOffsetS).toBeGreaterThan(298);
    expect(result.current.clockOffsetS).toBeLessThanOrEqual(300);

    // A 304 keeps the body, so it keeps the offset measured with it.
    authenticatedFetch.mockResolvedValueOnce(new Response(null, { status: 304 }));
    await act(() => client.invalidateQueries({ queryKey: factoryQueryKey(FID) }));
    expect(result.current.clockOffsetS).toBeGreaterThan(298);
  });

  it("does not read anything without a factory id", () => {
    const { result } = renderHook(() => useFactory(null), { wrapper: wrapperFor(newClient()) });
    expect(result.current.data).toBeUndefined();
    expect(result.current.isLoading).toBe(false);
    expect(authenticatedFetch).not.toHaveBeenCalled();
  });

  it("does not retry a 404", async () => {
    authenticatedFetch.mockResolvedValue(json({ error: { message: "nope" } }, 404));
    const client = new QueryClient();
    const { result } = renderHook(() => useFactory(FID), { wrapper: wrapperFor(client) });
    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(authenticatedFetch).toHaveBeenCalledTimes(1);
    expect(result.current.isStale).toBe(false);
  });
});

describe("factory list, events and decisions", () => {
  it("lists factories", async () => {
    authenticatedFetch.mockResolvedValue(json({ data: [{ id: "f1" }] }));
    const { result } = renderHook(() => useFactories(), { wrapper: wrapperFor(newClient()) });
    await waitFor(() => expect(result.current.data?.data).toHaveLength(1));
    expect(authenticatedFetch).toHaveBeenCalledWith("/v1/factory/factories");
  });

  it("stays idle when disabled (feature off)", () => {
    renderHook(() => useFactories({ enabled: false }), { wrapper: wrapperFor(newClient()) });
    expect(authenticatedFetch).not.toHaveBeenCalled();
  });

  it("pages events by the server's (time, id) cursor", async () => {
    authenticatedFetch
      .mockResolvedValueOnce(
        json({
          data: [
            { id: "e2", ts: 2000 },
            { id: "e1", ts: 1000 },
          ],
          has_more: true,
          // Past hidden events the viewer cannot read.
          next_before: 900,
          next_before_id: "e1h",
        }),
      )
      .mockResolvedValueOnce(json({ data: [{ id: "e0", ts: 500 }], has_more: false }));
    const { result } = renderHook(() => useFactoryEvents(FID, { project: "api-version" }), {
      wrapper: wrapperFor(newClient()),
    });
    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    await act(() => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.data?.pages).toHaveLength(2));
    expect(result.current.hasNextPage).toBe(false);
    const second = new URL(String(authenticatedFetch.mock.calls[1]![0]), "http://x");
    expect(second.searchParams.get("before")).toBe("900");
    expect(second.searchParams.get("before_id")).toBe("e1h");
    expect(second.searchParams.get("project")).toBe("api-version");
  });

  it("falls back to the oldest event's time and id, so same-ms events are not skipped", () => {
    const page = (extra: Partial<EventPage>): EventPage => ({
      data: [
        { id: "e2", ts: 1000 },
        { id: "e1", ts: 1000 },
      ] as EventPage["data"],
      has_more: true,
      ...extra,
    });
    expect(nextEventCursor(page({}))).toEqual({ before: 1000, before_id: "e1" });
    expect(nextEventCursor(page({ has_more: false }))).toBeUndefined();
    expect(nextEventCursor(page({ data: [] }))).toBeUndefined();
  });

  it("counts open decisions assigned to the viewer or their role", async () => {
    authenticatedFetch.mockResolvedValue(json({ data: [], count: 3 }));
    const { result } = renderHook(() => useFactoryDecisionCount(), {
      wrapper: wrapperFor(newClient()),
    });
    await waitFor(() => expect(result.current).toBe(3));
    expect(authenticatedFetch).toHaveBeenCalledWith(
      "/v1/factory/decisions?status=open&assigned=me&count_only=1",
    );
  });
});

describe("useFactorySpans", () => {
  it("follows the cursor so the newest spans are never cut off", async () => {
    const span = (id: string, at: number) => ({ id, started_at: at });
    authenticatedFetch
      .mockResolvedValueOnce(
        json({ data: [span("a", 1), span("b", 2)], has_more: true, next_since: 3 }),
      )
      .mockResolvedValueOnce(json({ data: [span("c", 3)], has_more: false, next_since: null }));
    const { result } = renderHook(() => useFactorySpans(FID, 1000), {
      wrapper: wrapperFor(newClient()),
    });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data!.data.map((s) => s.id)).toEqual(["a", "b", "c"]);
    expect(result.current.data!.has_more).toBe(false);
    const paths = authenticatedFetch.mock.calls.map(([path]) => path as string);
    expect(paths[0]).toContain(`/v1/factory/factories/${FID}/spans?since=1000&limit=2000`);
    expect(paths[1]).toContain("since=3");
  });

  it("asks for the given kinds and keeps the last spans on screen while a new window loads", async () => {
    let release: (r: Response) => void = () => undefined;
    authenticatedFetch
      .mockResolvedValueOnce(json({ data: [{ id: "a", started_at: 1 }], has_more: false }))
      .mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            release = resolve;
          }),
      );
    const { result, rerender } = renderHook(
      ({ since }) => useFactorySpans(FID, since, { kinds: ["gate", "decision"] }),
      { wrapper: wrapperFor(newClient()), initialProps: { since: 1000 } },
    );
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(authenticatedFetch.mock.calls[0]![0]).toContain("kind=gate%2Cdecision");
    rerender({ since: 2000 });
    expect(result.current.data!.data.map((s) => s.id)).toEqual(["a"]);
    expect(result.current.isPlaceholderData).toBe(true);
    release(json({ data: [{ id: "b", started_at: 2 }], has_more: false }));
    await waitFor(() => expect(result.current.data!.data.map((s) => s.id)).toEqual(["b"]));
  });

  it("reports a server without spans as a 404 (the heatmap falls back to events)", async () => {
    authenticatedFetch.mockResolvedValueOnce(json({ error: { message: "not found" } }, 404));
    const { result } = renderHook(() => useFactorySpans(FID, 1000), {
      wrapper: wrapperFor(newClient()),
    });
    await waitFor(() => expect(result.current.error).toBeTruthy());
    expect((result.current.error as { status?: number }).status).toBe(404);
  });
});

describe("useProjectDemos", () => {
  it("reads one project's demos route", async () => {
    authenticatedFetch.mockResolvedValueOnce(json({ data: [], count: 0 }));
    const { result } = renderHook(() => useProjectDemos(FID, "db version"), {
      wrapper: wrapperFor(newClient()),
    });
    await waitFor(() => expect(result.current.data).toEqual({ data: [], count: 0 }));
    expect(authenticatedFetch.mock.calls[0]![0]).toBe(
      `/v1/factory/factories/${FID}/projects/db%20version/demos`,
    );
  });
});
