import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { withBootTimeout } from "./bootCapabilities";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  // capabilities.ts caches the probe at module scope.
  vi.resetModules();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("useBootServerInfo", () => {
  it("stays loading while it retries an unreachable server, then hands over the answer", async () => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch")).mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ features: { factory: true } }),
    } as unknown as Response);
    const { useBootServerInfo } = await import("./bootCapabilities");

    const { result } = renderHook(() => useBootServerInfo());
    expect(result.current).toEqual({ info: "loading", probe: { kind: "pending" } });

    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(result.current.info).toBe("loading");
    expect(result.current.probe).toMatchObject({ kind: "retrying", failures: 1 });

    await act(() => vi.advanceTimersByTimeAsync(1_000));
    expect(result.current.probe).toEqual({ kind: "settled" });
    expect(result.current.info).not.toBe("loading");
    expect(result.current.info !== "loading" && result.current.info.features.factory).toBe(true);
  });
});

// Shared by the /v1/info and /v1/me boot gates in main.tsx: both must be
// awaited before first render, and neither may deadlock first paint.
describe("withBootTimeout", () => {
  it("resolves with the probe when it beats the timeout", async () => {
    await expect(withBootTimeout(Promise.resolve("alice"), null, 1500)).resolves.toBe("alice");
  });

  it("falls back when the probe hangs past the timeout", async () => {
    vi.useFakeTimers();
    const hung = new Promise<string | null>(() => {});
    const result = vi.fn();
    void withBootTimeout(hung, null, 1500).then(result);

    await vi.advanceTimersByTimeAsync(1500);

    expect(result).toHaveBeenCalledWith(null);
  });

  it("clears the timer once the probe settles", async () => {
    vi.useFakeTimers();
    const probe = deferred<string | null>();
    const settled = withBootTimeout(probe.promise, null, 1500);
    probe.resolve("alice");
    await expect(settled).resolves.toBe("alice");

    // A live timer here would keep the fallback armed (and hold the event
    // loop open) long after boot resolved.
    expect(vi.getTimerCount()).toBe(0);
  });
});
