// ⌘K (Ctrl+K off macOS) on a factory page opens the factory's palette. The
// page marks its root with `PAGE_PALETTE_ATTRIBUTE`, so the app shell's global
// palette leaves the chord alone while a factory page is mounted.
//
// The new cockpit adds a keyboard layer (`useCockpitKeys`): `/` opens the
// palette too, `g` then `h d p t s` jumps to Home, Discovery, Projects, Teams
// or Steer, `?` lists the keys and `r` opens the Room. `u` is the cockpit
// toast's (it presses Undo while one is offered) and `e` the project page's
// (edit the team). Keys never fire while someone types in a field or works a
// menu, list or dialog.

import { useEffect, useRef } from "react";
import { isCommandPaletteHotkey } from "@/hooks/useCommandPaletteHotkey";
import {
  type CockpitPlace,
  discoveryPageHref,
  factoryPageHref,
  projectsPageHref,
  steerPageHref,
  teamsPageHref,
} from "../factoryRoutes";

/** Toggle the factory palette on ⌘K while mounted. */
export function useFactoryPaletteHotkey(onToggle: () => void, enabled = true): void {
  const latest = useRef(onToggle);
  latest.current = onToggle;
  useEffect(() => {
    if (!enabled) return;
    const handler = (event: KeyboardEvent) => {
      if (event.repeat || event.defaultPrevented || !isCommandPaletteHotkey(event)) return;
      event.preventDefault();
      latest.current();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [enabled]);
}

/** `g` then this key: where it goes. */
export const GO_KEYS: Readonly<Record<string, CockpitPlace>> = {
  h: "home",
  d: "discovery",
  p: "projects",
  t: "teams",
  s: "steer",
};

/** Where each of the five places is. */
export const PLACE_HREF: Readonly<Record<CockpitPlace, (factoryId: string) => string>> = {
  home: factoryPageHref,
  discovery: (fid) => discoveryPageHref(fid),
  projects: projectsPageHref,
  teams: (fid) => teamsPageHref(fid),
  steer: (fid) => steerPageHref(fid),
};

/** How long a `g` waits for its second key. */
export const CHORD_MS = 1000;

/** Widgets that read printable keys themselves (fields, menus, lists, dialogs). */
const KEY_OWNERS = [
  "dialog",
  "alertdialog",
  "menu",
  "menubar",
  "menuitem",
  "menuitemcheckbox",
  "menuitemradio",
  "listbox",
  "option",
  "combobox",
  "tree",
  "treeitem",
  "grid",
  "slider",
  "spinbutton",
  "textbox",
  "searchbox",
]
  .map((role) => `[role=${role}]`)
  .join(",");

/** True when a key press belongs to whatever has focus, not to the page's keys. */
export function keyOwnedByTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return true;
  return target.closest(KEY_OWNERS) !== null;
}

export interface CockpitKeys {
  /** `/`: open the palette. */
  onPalette: () => void;
  /** `g h`, `g d`, `g p`, `g t`, `g s`. */
  onGo: (place: CockpitPlace) => void;
  /** `?`: the key list. */
  onHelp: () => void;
  /** `r`: the Room. */
  onRoom: () => void;
}

/** The new cockpit's single keys and `g` chords while mounted. */
export function useCockpitKeys(keys: CockpitKeys, enabled = true): void {
  const latest = useRef(keys);
  latest.current = keys;
  useEffect(() => {
    if (!enabled) return;
    let chord: ReturnType<typeof setTimeout> | null = null;
    const endChord = () => {
      if (chord) clearTimeout(chord);
      chord = null;
    };
    const handler = (event: KeyboardEvent) => {
      if (event.repeat || event.defaultPrevented) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (keyOwnedByTarget(event.target)) return;
      if (chord) {
        endChord();
        const place = GO_KEYS[event.key];
        if (!place) return;
        event.preventDefault();
        latest.current.onGo(place);
        return;
      }
      switch (event.key) {
        case "/":
          event.preventDefault();
          latest.current.onPalette();
          return;
        case "?":
          event.preventDefault();
          latest.current.onHelp();
          return;
        case "r":
          if (event.shiftKey) return;
          event.preventDefault();
          latest.current.onRoom();
          return;
        case "g":
          if (event.shiftKey) return;
          event.preventDefault();
          chord = setTimeout(endChord, CHORD_MS);
          return;
      }
    };
    window.addEventListener("keydown", handler);
    return () => {
      endChord();
      window.removeEventListener("keydown", handler);
    };
  }, [enabled]);
}
