/**
 * What every factory page reads from the shell: the live snapshot (read once
 * by the layout), the server-aligned clock, the page the viewer is on, and
 * the shell's dialogs (Share, ⌘K, Add project).
 */

import { createContext, useContext } from "react";
import type { FactorySnapshot } from "@/lib/factoryApi";
import type { FactoryLocation } from "./shellRoutes";

export interface FactoryShellValue {
  factoryId: string;
  snapshot: FactorySnapshot;
  /** Seconds the server clock runs ahead of this browser's. */
  clockOffsetS: number;
  /** "Now" on the server's clock, epoch seconds (ticks). */
  nowS: number;
  /** When the snapshot was fetched (epoch ms). */
  updatedAt: number;
  location: FactoryLocation;
  /** The dock sits beside the page (≥ 1280 px). */
  wide: boolean;
  openShare: () => void;
  openPalette: () => void;
  openAddProject: () => void;
}

export const FactoryShellContext = createContext<FactoryShellValue | null>(null);

/** The factory shell around this page; throws outside a factory page. */
export function useFactoryShell(): FactoryShellValue {
  const value = useContext(FactoryShellContext);
  if (!value) throw new Error("useFactoryShell must be used inside FactoryLayout");
  return value;
}

/** The shell when there is one (components that also render outside a factory page). */
export function useOptionalFactoryShell(): FactoryShellValue | null {
  return useContext(FactoryShellContext);
}
