import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/sessionsApi";
import { PRESENCE_HEARTBEAT_MS, useFactoryPresence } from "./useFactoryPresence";

const post = vi.hoisted(() => vi.fn());
vi.mock("@/lib/factoryApi", () => ({
  postFactoryPresence: (...args: unknown[]) => post(...args),
}));

let visibility: DocumentVisibilityState = "visible";

/** BroadcastChannel delivering at once to the other channels of its name (tabs of one browser). */
class FakeChannel {
  static open = new Set<FakeChannel>();
  onmessage: ((event: MessageEvent) => void) | null = null;
  readonly name: string;
  constructor(name: string) {
    this.name = name;
    FakeChannel.open.add(this);
  }
  postMessage(data: unknown) {
    for (const other of FakeChannel.open) {
      if (other !== this && other.name === this.name) {
        other.onmessage?.(new MessageEvent("message", { data }));
      }
    }
  }
  close() {
    FakeChannel.open.delete(this);
  }
}

beforeEach(() => {
  FakeChannel.open.clear();
  vi.stubGlobal("BroadcastChannel", FakeChannel);
  vi.useFakeTimers();
  post.mockReset();
  post.mockResolvedValue({ present: 1, heartbeat_s: 20, ttl_s: 60 });
  visibility = "visible";
  vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibility);
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function bodies(): unknown[] {
  return post.mock.calls.map(([, body]) => body);
}

describe("useFactoryPresence", () => {
  it("beats at once, then every 20 s, from where the viewer is", () => {
    renderHook(() => useFactoryPresence("f1", "overview"));
    expect(post).toHaveBeenCalledWith("f1", { location: "overview", idle: false });
    act(() => vi.advanceTimersByTime(PRESENCE_HEARTBEAT_MS));
    act(() => vi.advanceTimersByTime(PRESENCE_HEARTBEAT_MS));
    expect(bodies()).toEqual([
      { location: "overview", idle: false },
      { location: "overview", idle: false },
      { location: "overview", idle: false },
    ]);
  });

  it("beats at once when the viewer moves, then keeps beating from there", () => {
    const { rerender } = renderHook(({ at }) => useFactoryPresence("f1", at), {
      initialProps: { at: "overview" },
    });
    rerender({ at: "project:db-version" });
    expect(bodies()).toEqual([
      { location: "overview", idle: false },
      { location: "project:db-version", idle: false },
    ]);
    act(() => vi.advanceTimersByTime(PRESENCE_HEARTBEAT_MS));
    expect(bodies().at(-1)).toEqual({ location: "project:db-version", idle: false });
  });

  it("says idle while the tab is hidden, and back when it returns", () => {
    renderHook(() => useFactoryPresence("f1", "overview"));
    visibility = "hidden";
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    visibility = "visible";
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(bodies()).toEqual([
      { location: "overview", idle: false },
      { location: "overview", idle: true },
      { location: "overview", idle: false },
    ]);
  });

  it("leaves on unmount and stops beating", () => {
    const { unmount } = renderHook(() => useFactoryPresence("f1", "overview"));
    unmount();
    expect(bodies().at(-1)).toEqual({ location: null, idle: false, leave: true });
    const count = post.mock.calls.length;
    act(() => vi.advanceTimersByTime(PRESENCE_HEARTBEAT_MS * 3));
    expect(post).toHaveBeenCalledTimes(count);
  });

  it("stops after a 403 or 404 (no access, no such factory) and sends no leave", async () => {
    post.mockRejectedValueOnce(new ApiError("gone", 404, "not_found"));
    const { unmount } = renderHook(() => useFactoryPresence("f1", "overview"));
    await act(async () => {});
    act(() => vi.advanceTimersByTime(PRESENCE_HEARTBEAT_MS * 2));
    unmount();
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("keeps beating after a transient failure", async () => {
    post.mockRejectedValueOnce(new ApiError("down", 503, null));
    renderHook(() => useFactoryPresence("f1", "overview"));
    await act(async () => {});
    act(() => vi.advanceTimersByTime(PRESENCE_HEARTBEAT_MS));
    expect(post).toHaveBeenCalledTimes(2);
  });

  it("sends nothing without a factory or when disabled", () => {
    renderHook(() => useFactoryPresence(null, "overview"));
    renderHook(() => useFactoryPresence("f1", "overview", { enabled: false }));
    act(() => vi.advanceTimersByTime(PRESENCE_HEARTBEAT_MS));
    expect(post).not.toHaveBeenCalled();
  });

  it("keeps quiet while hidden if another tab is visible", () => {
    visibility = "hidden";
    renderHook(() => useFactoryPresence("f1", "project:db-version"));
    // Another tab of this factory, visible, beats.
    const other = new FakeChannel("omnigent-factory-presence:f1");
    other.postMessage({ tab: "other", visible: true });
    post.mockClear();
    act(() => vi.advanceTimersByTime(PRESENCE_HEARTBEAT_MS));
    expect(post).not.toHaveBeenCalled();
    // Once that tab closes, this one says where the viewer is.
    other.postMessage({ tab: "other", visible: false, gone: true });
    expect(bodies()).toEqual([{ location: "project:db-version", idle: true }]);
  });

  it("sends no leave while another tab of the factory is open; the last one leaves", () => {
    const a = renderHook(() => useFactoryPresence("f1", "overview"));
    const b = renderHook(() => useFactoryPresence("f1", "project:db-version"));
    post.mockClear();
    a.unmount();
    expect(bodies()).not.toContainEqual({ location: null, idle: false, leave: true });
    // The tab left open says where the viewer is.
    expect(bodies()).toEqual([{ location: "project:db-version", idle: false }]);
    b.unmount();
    expect(bodies().at(-1)).toEqual({ location: null, idle: false, leave: true });
  });
});
