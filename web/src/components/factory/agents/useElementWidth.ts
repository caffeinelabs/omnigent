// An element's width as it renders and resizes: measured before paint, so a
// layout that depends on it (the fleet's columns, the timeline's ticks) is
// right on the first frame; null until measured (and under jsdom).

import { useLayoutEffect, useState } from "react";

/** Width of the element in px, following resizes; null until measured. */
export function useElementWidth(ref: React.RefObject<HTMLElement | null>): number | null {
  const [width, setWidth] = useState<number | null>(null);
  // Measured before paint, so the first rows already have the right layout.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const first = el.getBoundingClientRect().width;
    if (first > 0) setWidth(Math.round(first));
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(Math.round(entry.contentRect.width));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}
