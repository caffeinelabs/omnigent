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

/**
 * Where a sticky panel's top sits in the viewport, kept current while the
 * page scrolls it up to where it sticks, so the panel can end at the
 * viewport's bottom from the first frame (the Room dock's composer).
 */
export function useStickyTop(
  ref: RefObject<HTMLElement | null>,
  /** Whether the panel is mounted (measured again when it comes back). */
  shown = true,
): number | null {
  const [top, setTop] = useState<number | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || !shown) return;
    const scroller = el.closest<HTMLElement>('[data-testid="factory-page"]');
    let frame = 0;
    const measure = () => {
      frame = 0;
      const next = Math.max(0, Math.round(el.getBoundingClientRect().top));
      setTop((prev) => (prev === next ? prev : next));
    };
    const schedule = () => {
      if (!frame) frame = window.requestAnimationFrame(measure);
    };
    measure();
    scroller?.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(schedule);
    const column = scroller?.firstElementChild ?? null;
    if (observer && column) observer.observe(column);
    return () => {
      if (frame) window.cancelAnimationFrame(frame);
      scroller?.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      observer?.disconnect();
    };
  }, [ref, shown]);
  return top;
}
