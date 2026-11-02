/**
 * Where an opportunity's evidence comes from: one stacked bar in the data hue,
 * sources told apart by shade (darkest is the largest) and by their short
 * mono tags, never by a second hue. People's hunches are hatched, since they
 * are not user evidence.
 */

import { cn } from "@/lib/utils";
import { DATA_HUE, hatch } from "./statusTokens";

export interface SourceCount {
  /** Short source tag as the factory's data names it ("forum", "web"). */
  tag: string;
  count: number;
  /** A person's hunch rather than user evidence. */
  hunch?: boolean;
}

const SHADE = [1, 0.72, 0.48, 0.3, 0.18];

/** Non-empty sources, largest first, hunches last. */
export function orderedSources(sources: readonly SourceCount[]): SourceCount[] {
  return sources
    .filter((s) => s.count > 0)
    .map((s, i) => ({ s, i }))
    .sort(
      (a, b) =>
        Number(a.s.hunch ?? false) - Number(b.s.hunch ?? false) ||
        b.s.count - a.s.count ||
        a.i - b.i,
    )
    .map(({ s }) => s);
}

/** "Sources: forum 11, web 9, team 1 hunch". */
export function sourceMixLabel(sources: readonly SourceCount[]): string {
  const parts = orderedSources(sources).map(
    (s) => `${s.tag} ${s.count}${s.hunch ? (s.count === 1 ? " hunch" : " hunches") : ""}`,
  );
  return parts.length ? `Sources: ${parts.join(", ")}` : "Sources: none";
}

export interface SourceMixProps {
  sources: readonly SourceCount[];
  /** Print the source tags after the bar. */
  showTags?: boolean;
  /** Bar width in px. */
  width?: number;
  className?: string;
}

export function SourceMix({ sources, showTags = false, width = 96, className }: SourceMixProps) {
  const shown = orderedSources(sources);
  const total = shown.reduce((sum, s) => sum + s.count, 0) || 1;
  let evidenceIndex = 0;
  return (
    <span className={cn("inline-flex flex-wrap items-center gap-x-2 gap-y-1", className)}>
      <span
        role="img"
        aria-label={sourceMixLabel(sources)}
        className="flex h-1.5 shrink-0 gap-px overflow-hidden rounded-full bg-status-gray/20"
        style={{ width }}
      >
        {shown.map((s) => {
          const shade = s.hunch ? 1 : (SHADE[evidenceIndex++] ?? 0.18);
          return (
            <i
              key={`${s.tag}-${s.hunch ? "h" : "e"}`}
              data-tag={s.tag}
              className="block h-full"
              style={{
                width: `${(s.count / total) * 100}%`,
                background: s.hunch ? hatch(DATA_HUE, 2) : DATA_HUE,
                opacity: shade,
              }}
            />
          );
        })}
      </span>
      {showTags && shown.length > 0 && (
        <span
          aria-hidden
          className="inline-flex flex-wrap gap-1.5 font-mono text-sm text-muted-foreground"
        >
          {shown.map((s) => (
            <span key={`${s.tag}-${s.hunch ? "h" : "e"}`}>
              {s.hunch ? `${s.tag} hunch` : s.tag}
            </span>
          ))}
        </span>
      )}
    </span>
  );
}
