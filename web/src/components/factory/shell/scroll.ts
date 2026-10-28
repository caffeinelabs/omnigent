// Scrolling inside factory pages: jump to an element (with a brief ring so the
// eye finds it), and follow `#hash` links such as `#needs-you` or a project
// page's `#demos` section once the page has rendered.

import { useEffect } from "react";
import { useLocation } from "@/lib/routing";
import { PHONE_TAB_BAR_REM } from "./PhoneTabBar";

const FLASH_MS = 1200;

/**
 * The factory page scroller's top padding over the header and inset. Sticky
 * bars offset by it, since a scroller's padding counts toward `top`.
 */
export const PAGE_EXTRA_TOP = "1.5rem";

/**
 * The factory page scroller's bottom padding beyond the inset: room for the
 * phone's tabs, or for the Room button below 1280 px.
 */
export function pageExtraBottom(phone: boolean, wide: boolean): string {
  return phone ? `${PHONE_TAB_BAR_REM + 1.5}rem` : wide ? "2rem" : "4.5rem";
}

/**
 * `bottom` for a bar stuck to the page scroller's bottom edge (on a phone, to
 * the top of its tabs): the scroller's bottom padding counts, so it is taken out.
 */
export function stickyBottom(phone: boolean, wide: boolean): string {
  const extra = pageExtraBottom(phone, wide);
  return phone
    ? `calc(${PHONE_TAB_BAR_REM}rem - ${extra})`
    : `calc(0px - ${extra} - var(--omnigent-inset-bottom, 0px))`;
}

/** Marks the dock's own scroller (a highlighted card scrolls within it first). */
export const DOCK_SCROLLER_ATTRIBUTE = "data-dock-scroller";

/** The dock's sticky tabs and the gap under them, in px. */
export const DOCK_TABS_PX = 44;

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

/** How long a `#hash` target is kept in place while the sections above it load. */
export const HASH_SETTLE_MS = 4000;

const READER_INPUT = ["wheel", "touchstart", "keydown", "mousedown"] as const;

/**
 * Scroll to the element the URL's `#hash` names, whenever the hash changes and
 * `ready`. Sections above it that load later (demos, posters) push it down,
 * so it is scrolled back to the top until they settle ({@link HASH_SETTLE_MS})
 * or the reader scrolls themselves.
 */
export function useHashScroll(ready = true): void {
  const { hash } = useLocation();
  useEffect(() => {
    if (!ready || !hash) return;
    const id = decodeURIComponent(hash.slice(1));
    const scroll = (behavior: ScrollBehavior) =>
      document.getElementById(id)?.scrollIntoView({ behavior, block: "start" });
    // After paint: the section may mount in this same commit.
    let frame = window.requestAnimationFrame(() => {
      scroll(reducedMotion() ? "auto" : "smooth");
      watch();
    });
    let observer: ResizeObserver | null = null;
    let lastHeight: number | null = null;
    const watch = () => {
      const column = document.getElementById(id)?.parentElement;
      if (!column || typeof ResizeObserver === "undefined") return;
      observer = new ResizeObserver(() => {
        const height = column.getBoundingClientRect().height;
        if (lastHeight != null && height !== lastHeight) {
          window.cancelAnimationFrame(frame);
          frame = window.requestAnimationFrame(() => scroll("auto"));
        }
        lastHeight = height;
      });
      observer.observe(column);
    };
    const stop = () => {
      observer?.disconnect();
      observer = null;
      for (const type of READER_INPUT) window.removeEventListener(type, stop);
    };
    for (const type of READER_INPUT) window.addEventListener(type, stop, { passive: true });
    const timer = window.setTimeout(stop, HASH_SETTLE_MS);
    return () => {
      window.cancelAnimationFrame(frame);
      window.clearTimeout(timer);
      stop();
    };
  }, [hash, ready]);
}
