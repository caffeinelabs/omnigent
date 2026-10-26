import { useRef, useSyncExternalStore } from "react";

/** The board's wide layout (decisions rail beside the cards). */
export const WIDE_BOARD_QUERY = "(min-width: 1280px)";

/**
 * Whether a media query matches, live. Off the browser (or without
 * `matchMedia`) it reports `fallback`.
 */
export function useMediaQuery(query: string, fallback = false): boolean {
  return useSyncExternalStore(
    (onChange) => {
      if (typeof window === "undefined" || !window.matchMedia) return () => {};
      const mql = window.matchMedia(query);
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    },
    () =>
      typeof window === "undefined" || !window.matchMedia
        ? fallback
        : window.matchMedia(query).matches,
    () => fallback,
  );
}

/**
 * `value` once it has changed since the first render, else `null`. Keyed
 * animations use it so live changes pulse while the first paint stays still.
 */
export function useChangedSinceMount<T extends string | number | boolean>(value: T): T | null {
  const first = useRef(value);
  const changed = useRef(false);
  if (value !== first.current) changed.current = true;
  return changed.current ? value : null;
}
