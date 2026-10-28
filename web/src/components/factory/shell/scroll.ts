// Scrolling inside factory pages: jump to an element (with a brief ring so the
// eye finds it), and follow `#hash` links such as `#needs-you` or a project
// page's `#demos` section once the page has rendered.

import { useEffect } from "react";
import { useLocation } from "@/lib/routing";

const FLASH_MS = 1200;

function reducedMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

/** Scroll an element into view and flash a ring around it (still under reduced motion). */
export function jumpTo(targetId: string, block: ScrollLogicalPosition = "center"): void {
  const el = document.getElementById(targetId);
  if (!el) return;
  const reduce = reducedMotion();
  el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block });
  el.dataset.highlighted = "true";
  if (!reduce) {
    el.animate?.(
      [
        { boxShadow: "0 0 0 0 var(--ring)" },
        { boxShadow: "0 0 0 4px var(--ring)", offset: 0.2 },
        { boxShadow: "0 0 0 0 transparent" },
      ],
      { duration: 900, easing: "ease-out" },
    );
  }
  window.setTimeout(() => delete el.dataset.highlighted, FLASH_MS);
}

/** Scroll to the element the URL's `#hash` names, whenever the hash changes and `ready`. */
export function useHashScroll(ready = true): void {
  const { hash } = useLocation();
  useEffect(() => {
    if (!ready || !hash) return;
    const id = decodeURIComponent(hash.slice(1));
    // After paint: the section may mount in this same commit.
    const frame = window.requestAnimationFrame(() => {
      document
        .getElementById(id)
        ?.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [hash, ready]);
}
