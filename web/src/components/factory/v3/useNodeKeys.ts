// The node page's keys. Tile and row titles are real links with a roving
// tabindex (one Tab stop for the list); `j`/`k` move DOM focus over them,
// Enter or `l` step into the focused one, `h` steps out to the parent, `w`
// opens "why" and `v` shows the next lens; a verb's key (`a`, `s`, `t`, `u`, `p`, `m`, `n`) opens its
// preview. Keys never fire while someone types or works a menu or dialog,
// Enter never answers a decision (the answer buttons swallow it), and `u`
// stays the toast's Undo while one is offered.

import { type RefObject, useEffect, useRef, useState } from "react";
import { CHORD_MS, keyOwnedByTarget } from "../cmdk/useFactoryPaletteHotkey";
import { TOAST_UNDO_SELECTOR } from "../ui/Toast";

const STOP = "[data-node-link]";

function stops(container: HTMLElement): HTMLElement[] {
  return [...container.querySelectorAll<HTMLElement>(STOP)];
}

/**
 * Keeps one roving stop tabbable (the last focused, else the first) as the
 * list changes; true while the container holds any.
 */
export function useRovingStops(container: RefObject<HTMLElement | null>): boolean {
  const active = useRef<string | null>(null);
  const [any, setAny] = useState(false);
  useEffect(() => {
    const root = container.current;
    if (!root) return;
    const apply = () => {
      const all = stops(root);
      setAny(all.length > 0);
      const current = all.find((el) => el.dataset.nodeLink === active.current) ?? all[0];
      for (const el of all) {
        const want = el === current ? "0" : "-1";
        if (el.getAttribute("tabindex") !== want) el.setAttribute("tabindex", want);
      }
    };
    const onFocus = (event: FocusEvent) => {
      const el = (event.target as HTMLElement | null)?.closest<HTMLElement>(STOP);
      if (!el || !root.contains(el)) return;
      active.current = el.dataset.nodeLink ?? null;
      apply();
    };
    apply();
    const observer = new MutationObserver(apply);
    observer.observe(root, { childList: true, subtree: true });
    root.addEventListener("focusin", onFocus);
    return () => {
      observer.disconnect();
      root.removeEventListener("focusin", onFocus);
    };
  }, [container]);
  return any;
}

export interface NodeKeys {
  /** `h`: the parent's page (absent at a root). */
  onStepOut?: () => void;
  /** `w`: "why" opens or closes. */
  onWhy?: () => void;
  /** Any other single letter: a verb's key; true when it opened something. */
  onVerbKey?: (key: string) => boolean;
  /** `v`: the next lens. */
  onLens?: () => void;
}

/** Binds j/k/l/h/w while mounted; `container` holds the roving stops. */
export function useNodeKeys(container: RefObject<HTMLElement | null>, keys: NodeKeys): void {
  const latest = useRef(keys);
  latest.current = keys;
  useEffect(() => {
    // `g` then a letter is the shell's chord (`g h` goes Home), never a node key.
    let chordAt = 0;
    const handler = (event: KeyboardEvent) => {
      if (event.repeat || event.defaultPrevented) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (keyOwnedByTarget(event.target)) return;
      if (event.key === "g") {
        chordAt = Date.now();
        return;
      }
      if (Date.now() - chordAt < CHORD_MS) {
        chordAt = 0;
        return;
      }
      const root = container.current;
      if (!root) return;
      const focused = document.activeElement as HTMLElement | null;
      const onStop = Boolean(focused?.matches?.(STOP) && root.contains(focused));
      const free = !focused || focused === document.body || onStop || root.contains(focused);
      const move = (delta: number) => {
        const all = stops(root);
        if (!all.length) return;
        const at = onStop ? all.indexOf(focused!) : -1;
        const next =
          at < 0
            ? delta > 0
              ? 0
              : all.length - 1
            : Math.max(0, Math.min(all.length - 1, at + delta));
        all[next]!.focus();
        all[next]!.scrollIntoView?.({ block: "nearest" });
      };
      switch (event.key) {
        case "j":
          if (!free) return;
          event.preventDefault();
          move(1);
          return;
        case "k":
          if (!free) return;
          event.preventDefault();
          move(-1);
          return;
        case "ArrowDown":
        case "ArrowUp":
          if (!onStop) return;
          event.preventDefault();
          move(event.key === "ArrowDown" ? 1 : -1);
          return;
        case "l":
          if (!onStop) return;
          event.preventDefault();
          focused!.click();
          return;
        case "h":
          if (!free || !latest.current.onStepOut) return;
          event.preventDefault();
          latest.current.onStepOut();
          return;
        case "w":
          if (!free || !latest.current.onWhy) return;
          event.preventDefault();
          latest.current.onWhy();
          return;
        case "v":
          if (!free || !latest.current.onLens) return;
          event.preventDefault();
          latest.current.onLens();
          return;
        default: {
          if (!free || event.shiftKey || !/^[a-z]$/.test(event.key)) return;
          if (event.key === "u" && document.querySelector(TOAST_UNDO_SELECTOR)) return;
          if (latest.current.onVerbKey?.(event.key)) event.preventDefault();
        }
      }
    };
    // Capture: a verb key the page offers (Rename levels' `r`) wins over the
    // shell's single keys; every key the page leaves alone still reaches them.
    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
  }, [container]);
}
