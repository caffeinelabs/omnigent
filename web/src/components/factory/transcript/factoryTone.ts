// Shared visual vocabulary of the factory transcript cards: tone classes
// built only from the theme's semantic tokens, role colours for sender chips,
// and the clock format the cards stamp.

import type { FactoryMarkerTone } from "@/lib/factoryMessages";

// The tone and role-chip classes live with the rest of the status grammar.
export { TONE_ICON, TONE_PILL, roleChipTone } from "../ui/statusTokens";

/** Envelope kind → pill tone. */
export function messageKindTone(kind: string | undefined): FactoryMarkerTone {
  switch (kind) {
    case "request":
      return "info";
    case "reply":
      return "success";
    case "brief":
      return "accent";
    case "alert":
      return "warning";
    default:
      return "muted";
  }
}

/**
 * Wall-clock time of an epoch-seconds stamp in the user bubble's format,
 * e.g. "2:02 PM" or "14:02" depending on the locale.
 *
 * @returns `null` when the stamp is missing.
 */
export function clockTime(epochSeconds: number | undefined | null): string | null {
  if (!epochSeconds) return null;
  return new Date(epochSeconds * 1000).toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
  });
}

/** Wall-clock time of an ISO stamp, or `null` when it does not parse. */
export function clockTimeIso(iso: string | undefined | null): string | null {
  if (!iso) return null;
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : clockTime(ms / 1000);
}

const ISO_STAMP_RE = /\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})/g;

/** Text with every ISO stamp shown as a wall-clock time ("since 2026-10-26T13:40:12Z" → "since 14:40"). */
export function formatIsoStamps(text: string): string {
  return text.replace(ISO_STAMP_RE, (stamp) => clockTimeIso(stamp) ?? stamp);
}

/** First 7 characters of a commit sha. */
export function shortSha(sha: string | undefined | null): string | null {
  return sha ? sha.slice(0, 7) : null;
}
