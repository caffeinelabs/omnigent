/**
 * React context holding the result of the ``/v1/info`` probe.
 *
 * Populated by :class:`CapabilitiesProvider` in ``main.tsx`` after
 * the probe resolves, then read by ``App.tsx`` (to gate route
 * registration) and ``AccountMenu`` (to gate rendering). Components
 * use the typed :func:`useServerInfo` hook to consume the value;
 * the raw ``Context`` is exported for testing convenience.
 *
 * The provider's default value is ``"loading"`` so consumers can
 * distinguish "probe in flight, do nothing yet" from "probe done,
 * accounts is off". This matters for the AccountMenu which would
 * flash an empty placeholder on first paint otherwise.
 */

import { createContext, type ReactNode, useContext } from "react";
import type { ServerInfo, ServerInfoProbeState } from "./capabilities";

type CapabilitiesValue = ServerInfo | "loading";

export const CapabilitiesContext = createContext<CapabilitiesValue>("loading");

const PENDING: ServerInfoProbeState = { kind: "pending" };
const SETTLED: ServerInfoProbeState = { kind: "settled" };

/** Why the info is still ``"loading"``: first attempt in flight, or retrying. */
export const ServerInfoProbeContext = createContext<ServerInfoProbeState>(PENDING);

export function CapabilitiesProvider({
  info,
  probe,
  children,
}: {
  info: ServerInfo | "loading";
  /** Probe progress; defaults to pending while ``info`` is loading, else settled. */
  probe?: ServerInfoProbeState;
  children: ReactNode;
}) {
  return (
    <CapabilitiesContext.Provider value={info}>
      <ServerInfoProbeContext.Provider value={probe ?? (info === "loading" ? PENDING : SETTLED)}>
        {children}
      </ServerInfoProbeContext.Provider>
    </CapabilitiesContext.Provider>
  );
}

/**
 * Read the current server-info value.
 *
 * Returns ``"loading"`` until the boot probe has a real answer — including
 * while it retries an unreachable server (see :func:`useServerInfoProbe`).
 * Once resolved, returns the :class:`ServerInfo` shape — never null.
 */
export function useServerInfo(): CapabilitiesValue {
  return useContext(CapabilitiesContext);
}

/** The boot probe's progress, to tell "still loading" from "can't reach the server". */
export function useServerInfoProbe(): ServerInfoProbeState {
  return useContext(ServerInfoProbeContext);
}
