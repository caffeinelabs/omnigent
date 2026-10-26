// Shared visual vocabulary of the factory transcript cards: tone classes
// built only from the theme's semantic tokens, role colours for sender chips,
// and the clock format the cards stamp.

import type { FactoryMarkerTone } from "@/lib/factoryMessages";

/** Pill classes per tone (border + tinted fill + text), light and dark via tokens. */
export const TONE_PILL: Record<FactoryMarkerTone, string> = {
  muted: "border-border bg-muted text-muted-foreground",
  info: "border-info/25 bg-info/10 text-info",
  success: "border-success/25 bg-success/10 text-success",
  accent: "border-brand-accent/25 bg-brand-accent/10 text-brand-accent",
  warning: "border-warning/30 bg-warning/10 text-warning",
  danger: "border-destructive/25 bg-destructive/10 text-destructive",
};

/** Icon colour per tone. */
export const TONE_ICON: Record<FactoryMarkerTone, string> = {
  muted: "text-muted-foreground",
  info: "text-info",
  success: "text-success",
  accent: "text-brand-accent",
  warning: "text-warning",
  danger: "text-destructive",
};

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

/** Sender role → classes layered over `FactoryRoleChip`'s neutral chip. */
export function roleChipTone(role: string | null | undefined): string {
  switch (role) {
    case "lead":
      return "border-brand-accent/40 text-brand-accent";
    case "project_lead":
      return "border-info/40 text-info";
    case "env_keeper":
    case "demo":
      return "border-success/40 text-success";
    default:
      return "";
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
