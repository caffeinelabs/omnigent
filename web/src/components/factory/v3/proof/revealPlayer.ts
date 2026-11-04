// A demo that starts playing is in view (calm rule 10: proof is never hidden).
// Pressing ▶ on a row low on the page opens the player below the fold, and on a
// phone the pinned chat box and the tab bar sit over the page's bottom edge; the
// player scrolls up just far enough to clear them.

/** What sits over the page's bottom edge: the pinned chat box (v3) and the phone's tab bar. */
const COVERS = '[data-testid="v3-chat-pinned"], [data-testid="factory-phone-tabs"]';

/** Room kept between the player and whatever covers the page's bottom edge. */
const GAP_PX = 12;

/** How far up from the viewport's bottom edge the fixed bars cover the page (0 when none). */
export function coveredBelow(doc: Document = document): number {
  const height = doc.defaultView?.innerHeight ?? 0;
  let top = height;
  for (const el of doc.querySelectorAll<HTMLElement>(COVERS)) {
    const box = el.getBoundingClientRect();
    if (box.height > 0 && box.top < top) top = box.top;
  }
  return Math.max(0, height - top);
}

function reducedMotion(): boolean {
  try {
    return window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches === true;
  } catch {
    return false;
  }
}

/**
 * Scroll `el` (a player that just opened) into view, clear of the pinned chat
 * box and the tab bar. In a sheet or the full-screen chat (a dialog over the
 * page) nothing covers it but the dialog's own edge.
 */
export function revealPlayer(el: HTMLElement): void {
  const covered = el.closest('[role="dialog"]') ? 0 : coveredBelow(el.ownerDocument);
  el.style.scrollMarginBottom = `${covered + GAP_PX}px`;
  el.style.scrollMarginTop = `${GAP_PX}px`;
  el.scrollIntoView?.({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" });
}
