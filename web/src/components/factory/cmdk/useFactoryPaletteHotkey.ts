// ⌘K (Ctrl+K off macOS) on a factory page opens the factory's palette. The
// page marks its root with `PAGE_PALETTE_ATTRIBUTE`, so the app shell's global
// palette leaves the chord alone while a factory page is mounted.

import { useEffect, useRef } from "react";
import { isCommandPaletteHotkey } from "@/hooks/useCommandPaletteHotkey";

/** Toggle the factory palette on ⌘K while mounted. */
export function useFactoryPaletteHotkey(onToggle: () => void, enabled = true): void {
  const latest = useRef(onToggle);
  latest.current = onToggle;
  useEffect(() => {
    if (!enabled) return;
    const handler = (event: KeyboardEvent) => {
      if (event.repeat || event.defaultPrevented || !isCommandPaletteHotkey(event)) return;
      event.preventDefault();
      latest.current();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [enabled]);
}
