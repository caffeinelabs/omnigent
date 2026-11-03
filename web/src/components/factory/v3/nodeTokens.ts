// The v3 cockpit's state colours, on the factory's one vocabulary
// (`ui/statusTokens`): amber when a person must act, red when the factory
// cannot heal it, blue for work moving, green for done; everything else is
// grey or hollow, so nothing that needs no one is coloured.

import { MEANING } from "@/components/factory/ui/statusTokens";
import type { NodeState, StatusLine } from "./nodeTypes";

/** Order of a stacked state bar: what needs a person first, done last. */
export const STATE_ORDER: readonly NodeState[] = [
  "broken",
  "needs",
  "waiting",
  "paused",
  "moving",
  "notstarted",
  "offered",
  "done",
];

/** A state's colour for dots and bars (a CSS colour). */
export const STATE_HUE: Record<NodeState, string> = {
  broken: MEANING.broken.hue,
  needs: MEANING.needsYou.hue,
  waiting: MEANING.idle.hue,
  paused: MEANING.idle.hue,
  moving: MEANING.selected.hue,
  done: MEANING.working.hue,
  notstarted: "var(--border-strong)",
  offered: "var(--border-strong)",
  later: "var(--border-strong)",
  ghost: "var(--border-strong)",
};

export const STATE_WORDS: Record<NodeState, string> = {
  broken: "broken",
  needs: "needs you",
  waiting: "waiting",
  paused: "paused",
  moving: "moving",
  done: "done",
  notstarted: "not started",
  offered: "offered",
  later: "later",
  ghost: "proposed",
};

/** The text colour of a status line's label. */
export function labelClass(kind: StatusLine["kind"]): string {
  switch (kind) {
    case "needs":
      return MEANING.needsYou.text;
    case "moving":
      return MEANING.selected.text;
    case "done":
      return MEANING.working.text;
    default:
      return "text-muted-foreground";
  }
}

/** Amber surfaces: the header's status box, a tile that needs you, the answer button. */
export const AMBER = {
  surface: "bg-status-yellow/12 dark:bg-status-yellow/10",
  ring: "ring-1 ring-status-yellow/55",
  text: MEANING.needsYou.text,
  button: "bg-status-yellow text-black hover:bg-status-yellow/90 border-status-yellow",
} as const;
