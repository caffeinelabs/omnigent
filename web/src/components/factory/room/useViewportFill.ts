// Size a panel to the viewport below where it starts, so a chat column keeps
// its composer on screen and scrolls its messages inside (the Room page).

import { type RefObject, useLayoutEffect, useState } from "react";

/**
 * The height that fills the viewport from the element's top (page scrolled
 * to the top) down to `bottomPx` above its bottom edge, never under `minPx`.
 */
export function useViewportFill(
  ref: RefObject<HTMLElement | null>,
  { bottomPx = 24, minPx = 360 }: { bottomPx?: number; minPx?: number } = {},
): number | null {
  const [height, setHeight] = useState<number | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const scroller = el.closest<HTMLElement>('[data-testid="factory-page"]');
    const measure = () => {
      const top = el.getBoundingClientRect().top + (scroller?.scrollTop ?? 0);
      const next = Math.max(minPx, Math.round(window.innerHeight - top - bottomPx));
      setHeight((prev) => (prev === next ? prev : next));
    };
    measure();
    window.addEventListener("resize", measure);
    // A banner appearing above (viewer, reconnecting) moves the panel down.
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => measure());
    const column = scroller?.firstElementChild ?? null;
    if (observer && column) observer.observe(column);
    return () => {
      window.removeEventListener("resize", measure);
      observer?.disconnect();
    };
  }, [ref, bottomPx, minPx]);
  return height;
}
