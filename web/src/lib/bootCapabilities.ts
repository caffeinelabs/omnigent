import { useEffect, useSyncExternalStore } from "react";
import {
  getCachedServerInfo,
  getServerInfoProbeState,
  resolveServerInfo,
  subscribeServerInfoProbe,
  type ServerInfo,
  type ServerInfoProbeState,
} from "./capabilities";

/**
 * Race a boot probe against a timeout so first paint never waits on a
 * stalled server: whichever settles first wins, and `fallback` stands in
 * when the probe is too slow. The timer is cleared once the probe settles
 * so a fast boot doesn't leave a pending handle behind (which would hold
 * the event loop open in tests).
 */
export function withBootTimeout<T>(settled: Promise<T>, fallback: T, timeoutMs = 1500): Promise<T> {
  let timeout: ReturnType<typeof setTimeout>;
  const timer = new Promise<T>((resolve) => {
    timeout = setTimeout(() => resolve(fallback), timeoutMs);
  });
  void settled.then(
    () => clearTimeout(timeout),
    () => clearTimeout(timeout),
  );
  return Promise.race([settled, timer]);
}

/**
 * The boot `/v1/info` answer for `CapabilitiesProvider`: `"loading"` until the
 * probe has one (first attempt, or retrying an unreachable server), plus the
 * probe's progress so routes can tell the two apart. Starts the probe if no
 * one has yet; it is shared and cached, so every caller reads the same answer.
 */
export function useBootServerInfo(): {
  info: ServerInfo | "loading";
  probe: ServerInfoProbeState;
} {
  const probe = useSyncExternalStore(subscribeServerInfoProbe, getServerInfoProbeState);
  useEffect(() => {
    void resolveServerInfo();
  }, []);
  const info = probe.kind === "settled" ? (getCachedServerInfo() ?? "loading") : "loading";
  return { info, probe };
}
