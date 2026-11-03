// The node chat's place on the page: a column beside the node from 1024 px
// (40 % of the window by default; dragged wider or narrower and kept in this
// browser), else a composer pinned at the bottom that opens the thread full
// screen (`?chat=1`, so Back closes it). The shell reaches the chat through a
// slot: `/`, `r` and the top bar's Room button focus it.

import { createContext, useCallback, useContext, useState } from "react";

/** Wide enough for the chat beside the page. */
export const CHAT_BESIDE_QUERY = "(min-width: 1024px)";
/** `?chat=1`: the thread is open full screen (narrow windows), or the box takes focus. */
export const CHAT_PARAM = "chat";

export const CHAT_WIDTH_KEY = "omnigent.factory.v3.chatWidth";
export const CHAT_MIN_PX = 320;

function viewportWidth(): number {
  return typeof window === "undefined" ? 1440 : window.innerWidth || 1440;
}

/** The widest the column gets: 60 % of the window. */
export function chatMaxPx(viewport: number): number {
  return Math.max(CHAT_MIN_PX + 40, Math.round(viewport * 0.6));
}

export function clampChatWidth(px: number, viewport: number): number {
  const fallback = defaultChatWidth(viewport);
  if (!Number.isFinite(px)) return fallback;
  return Math.round(Math.min(chatMaxPx(viewport), Math.max(CHAT_MIN_PX, px)));
}

/** 40 % of the window (the page is at most 1600 px wide). */
export function defaultChatWidth(viewport: number): number {
  const px = Math.round(Math.min(viewport, 1600) * 0.4);
  return Math.min(chatMaxPx(viewport), Math.max(CHAT_MIN_PX, px));
}

export function loadChatWidth(viewport: number): number {
  try {
    const raw = window.localStorage.getItem(CHAT_WIDTH_KEY);
    return raw == null ? defaultChatWidth(viewport) : clampChatWidth(Number(raw), viewport);
  } catch {
    return defaultChatWidth(viewport);
  }
}

function saveChatWidth(px: number): void {
  try {
    window.localStorage.setItem(CHAT_WIDTH_KEY, String(px));
  } catch {
    // The width lasts this page only.
  }
}

/** The column's width, kept in this browser; `setWidth` clamps and stores it. */
export function useChatWidth(): [number, (px: number) => void] {
  const [width, setState] = useState(() => loadChatWidth(viewportWidth()));
  const setWidth = useCallback((px: number) => {
    const next = clampChatWidth(px, viewportWidth());
    setState(next);
    saveChatWidth(next);
  }, []);
  return [width, setWidth];
}

/** How the shell reaches the node chat on the page. */
export interface NodeChatSlot {
  /** Focus the chat's box (a narrow window opens the thread); false when no chat is on the page. */
  focus: () => boolean;
  /** The chat on the page says how to focus it; the returned call forgets it again. */
  register: (focus: () => void) => () => void;
}

export function createNodeChatSlot(): NodeChatSlot {
  let current: (() => void) | null = null;
  return {
    focus: () => {
      if (!current) return false;
      current();
      return true;
    },
    register: (focus) => {
      current = focus;
      return () => {
        if (current === focus) current = null;
      };
    },
  };
}

export const NodeChatSlotContext = createContext<NodeChatSlot | null>(null);

/** The shell's slot (null outside cockpit v3's shell: the chat binds its own keys). */
export function useNodeChatSlot(): NodeChatSlot | null {
  return useContext(NodeChatSlotContext);
}
