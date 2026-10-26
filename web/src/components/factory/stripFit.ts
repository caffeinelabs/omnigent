// Priority+ fitting for the chat-header factory strip. Every part of the strip
// is a direct child tagged with `data-fit-key` and a priority; when the slot is
// narrower than the parts, the lowest-priority parts are hidden whole (or the
// one text part at the cut truncates), so the bordered strip always hugs what
// it shows. Untagged children (icon, stage dot, arrow) are always shown.

import { type RefObject, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

export interface FitItem {
  key: string;
  /** Higher stays longer. */
  priority: number;
  /** Natural width in px. */
  width: number;
  /** A text part may truncate down to this width before it is hidden. */
  minWidth?: number;
}

export interface FitPlan {
  hidden: ReadonlySet<string>;
  /** The one text part clamped to a width (truncating), if any. */
  clamp: { key: string; width: number } | null;
}

export const NO_FIT: FitPlan = { hidden: new Set(), clamp: null };

/**
 * Hide the lowest-priority parts until the rest fit `available`.
 *
 * @param available - Width of the slot in px.
 * @param fixed - Width of the always-shown parts plus padding and border.
 * @param items - The optional parts with their natural widths.
 */
export function planStripFit(available: number, fixed: number, items: readonly FitItem[]): FitPlan {
  let total = fixed + items.reduce((sum, item) => sum + item.width, 0);
  const hidden = new Set<string>();
  const byPriority = [...items].sort((a, b) => a.priority - b.priority);
  for (const item of byPriority) {
    if (total <= available) break;
    if (item.minWidth !== undefined && total - item.width + item.minWidth <= available) {
      return { hidden, clamp: { key: item.key, width: item.width - (total - available) } };
    }
    hidden.add(item.key);
    total -= item.width;
  }
  return { hidden, clamp: null };
}

function samePlan(a: FitPlan, b: FitPlan): boolean {
  if (a.hidden.size !== b.hidden.size || [...a.hidden].some((key) => !b.hidden.has(key))) {
    return false;
  }
  return a.clamp?.key === b.clamp?.key && a.clamp?.width === b.clamp?.width;
}

function px(value: string): number {
  return Number.parseFloat(value) || 0;
}

/** A part's natural width: its box plus whatever its truncating text hides. */
function naturalWidth(el: HTMLElement): number {
  const box = Math.ceil(el.getBoundingClientRect().width);
  const text = el.querySelector<HTMLElement>("[data-fit-text]");
  const overflow = text ? Math.max(0, text.scrollWidth - text.clientWidth) : 0;
  const max = Number(el.dataset.fitMax);
  return max > 0 ? Math.min(box + overflow, max) : box + overflow;
}

/**
 * Fit the strip's tagged parts into its slot; re-plans after every render and
 * whenever the slot resizes. Widths of hidden parts are remembered from when
 * they last showed. A slot that is not laid out (phones, tests) shows all.
 */
export function useStripFit(
  slotRef: RefObject<HTMLElement | null>,
  stripRef: RefObject<HTMLElement | null>,
): FitPlan {
  const [plan, setPlan] = useState<FitPlan>(NO_FIT);
  const widths = useRef(new Map<string, number>());
  const refit = useCallback(() => {
    const slot = slotRef.current;
    const strip = stripRef.current;
    if (!slot || !strip) return;
    const available = slot.clientWidth;
    if (available <= 0) return;
    const style = getComputedStyle(strip);
    let fixed =
      px(style.paddingLeft) +
      px(style.paddingRight) +
      px(style.borderLeftWidth) +
      px(style.borderRightWidth);
    const items: FitItem[] = [];
    for (const child of strip.children) {
      if (!(child instanceof HTMLElement)) continue;
      const key = child.dataset.fitKey;
      if (!key) {
        fixed += Math.ceil(child.getBoundingClientRect().width);
        continue;
      }
      if (!child.hasAttribute("data-fit-hidden")) widths.current.set(key, naturalWidth(child));
      const width = widths.current.get(key);
      if (width === undefined) continue;
      const min = Number(child.dataset.fitMin);
      items.push({
        key,
        priority: Number(child.dataset.fitPriority) || 0,
        width,
        minWidth: min > 0 ? Math.min(min, width) : undefined,
      });
    }
    const next = planStripFit(available, fixed, items);
    setPlan((prev) => (samePlan(prev, next) ? prev : next));
  }, [slotRef, stripRef]);

  // Content changes (a new stage, a longer cost) re-plan before paint.
  useLayoutEffect(refit);

  useEffect(() => {
    const slot = slotRef.current;
    if (!slot || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => refit());
    observer.observe(slot);
    return () => observer.disconnect();
  }, [slotRef, refit]);

  return plan;
}
