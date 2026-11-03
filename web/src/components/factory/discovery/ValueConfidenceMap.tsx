/**
 * Discovery's Map: each open opportunity as a dot, its value (reach ×
 * intensity × fit) against its confidence (the evidence meter), with the
 * quadrants Test first, Bet, Wait for signals and Quick win if tiny. The
 * dividers are rubric fields. Green is building, amber waits for you, the
 * data hue is the rest; every dot is a button that opens the opportunity.
 * "How to read it" and the list by quadrant are the map's written equivalent.
 */

import { cn } from "@/lib/utils";
import { DATA_HUE, FOCUS_RING, MEANING } from "../ui/statusTokens";
import {
  type MapDividers,
  type MapPoint,
  QUADRANT_WORD,
  type Quadrant,
  rankText,
} from "./discoveryModel";

const TONE: Record<MapPoint["tone"], string> = {
  building: MEANING.working.hue,
  waits: MEANING.needsYou.hue,
  data: DATA_HUE,
};

const QUADRANT_ORDER: readonly Quadrant[] = ["bet", "test_first", "quick_win", "wait"];

/** What a dot says besides its title: value, confidence, quadrant and state. */
function dotFacts(p: MapPoint): string {
  return [
    `value ${rankText(p.value)}`,
    `confidence ${rankText(p.confidence)}`,
    p.quadrant ? QUADRANT_WORD[p.quadrant] : null,
    p.tone === "waits" ? "waits for you" : p.tone === "building" ? "building" : null,
  ]
    .filter(Boolean)
    .join(", ");
}

export function ValueConfidenceMap({
  points,
  dividers,
  defVersion,
  onOpen,
}: {
  points: readonly MapPoint[];
  dividers: MapDividers | null;
  defVersion: number | null;
  onOpen: (id: string) => void;
}) {
  const left = (c: number) => `${c * 100}%`;
  const top = (v: number) => `${(1 - v) * 100}%`;
  return (
    <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
      <section
        aria-labelledby="map-h"
        data-testid="discovery-map"
        className="flex min-w-0 flex-col gap-3 rounded-xl border border-border bg-card p-4"
      >
        <div className="flex flex-wrap items-baseline gap-x-3">
          <h2 id="map-h" className="grow text-base font-semibold">
            Value × confidence
          </h2>
          <span className="text-sm text-muted-foreground">dividers are rubric fields</span>
        </div>
        <div className="flex min-w-0 gap-2">
          <span
            aria-hidden
            className="shrink-0 text-xs text-muted-foreground [writing-mode:vertical-rl] rotate-180 text-center"
          >
            value: reach × intensity × fit →
          </span>
          <div className="min-w-0 flex-1">
            <div
              role="group"
              aria-label="Opportunities by value and confidence"
              className="relative h-80 w-full rounded-md border border-border max-md:h-64"
            >
              {dividers && (
                <>
                  <span
                    aria-hidden
                    className="absolute inset-y-0 border-l border-dashed border-border-strong"
                    style={{ left: left(dividers.confidence) }}
                  />
                  <span
                    aria-hidden
                    className="absolute inset-x-0 border-t border-dashed border-border-strong"
                    style={{ top: top(dividers.value) }}
                  />
                  <span className="absolute top-1.5 left-2 text-xs font-semibold text-muted-foreground">
                    {QUADRANT_WORD.test_first}
                  </span>
                  <span
                    className="absolute top-1.5 text-xs font-semibold text-muted-foreground"
                    style={{ left: `calc(${left(dividers.confidence)} + 0.5rem)` }}
                  >
                    {QUADRANT_WORD.bet}
                  </span>
                  <span className="absolute bottom-1.5 left-2 text-xs font-semibold text-muted-foreground">
                    {QUADRANT_WORD.wait}
                  </span>
                  <span
                    className="absolute bottom-1.5 text-xs font-semibold text-muted-foreground"
                    style={{ left: `calc(${left(dividers.confidence)} + 0.5rem)` }}
                  >
                    {QUADRANT_WORD.quick_win}
                  </span>
                </>
              )}
              {points.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  data-testid={`map-dot-${p.id}`}
                  data-tone={p.tone}
                  onClick={() => onOpen(p.id)}
                  className={cn(
                    "group absolute flex -translate-x-[7px] -translate-y-1/2 items-center gap-1.5 rounded-full",
                    FOCUS_RING,
                  )}
                  style={{ left: left(p.confidence), top: top(p.value) }}
                >
                  <span
                    aria-hidden
                    className="size-3.5 shrink-0 rounded-full border-2 border-card-solid"
                    style={{ background: TONE[p.tone] }}
                  />
                  {/* The full title is the name (CSS shortens it); a phone shows only the dot. */}
                  <span
                    title={p.title}
                    className="max-w-44 truncate text-xs text-muted-foreground group-hover:text-foreground max-md:sr-only"
                  >
                    {p.title}
                  </span>
                  <span className="sr-only">: {dotFacts(p)}</span>
                </button>
              ))}
            </div>
            <p aria-hidden className="mt-1 text-center text-xs text-muted-foreground">
              confidence (evidence level: hunch → proven) →
            </p>
          </div>
        </div>
      </section>
      <section
        aria-labelledby="map-read-h"
        className="flex min-w-0 flex-col gap-2 rounded-xl border border-border bg-card p-4 text-sm"
      >
        <h2 id="map-read-h" className="text-base font-semibold">
          How to read it
        </h2>
        <p>
          Each dot is an opportunity. Right means stronger evidence; up means more people, more pain
          and a better fit with the goal.
        </p>
        <p>
          <b>Bet</b>: three solutions, then an outcome bet. <b>Test first</b>: the cheapest test
          that could raise confidence. <b>Wait</b>: more signals. <b>Quick win</b>: only if tiny.
        </p>
        <ul className="m-0 flex list-none flex-wrap gap-x-3 gap-y-1 p-0 text-muted-foreground">
          {(
            [
              ["building", "building"],
              ["waits", "waits for you"],
              ["data", "everything else"],
            ] as const
          ).map(([tone, word]) => (
            <li key={tone} className="flex items-center gap-1.5">
              <span
                aria-hidden
                className="size-2.5 rounded-full"
                style={{ background: TONE[tone] }}
              />
              {word}
            </li>
          ))}
        </ul>
        <p className="text-muted-foreground">
          {dividers
            ? `Dividers: value ${rankText(dividers.value)}, confidence ${rankText(dividers.confidence)}${defVersion != null ? ` (definition v${defVersion})` : ""}. Change them in Steer › Scoring rubric.`
            : "The rubric in force sets no map dividers; each dot still sits in the quadrant the server gave it."}
        </p>
        <div
          data-testid="map-by-quadrant"
          className="flex flex-col gap-1 border-t border-border pt-2"
        >
          {QUADRANT_ORDER.map((q) => {
            const here = points.filter((p) => p.quadrant === q);
            if (here.length === 0) return null;
            return (
              <p key={q} className="text-muted-foreground">
                <span className="font-medium text-foreground">{QUADRANT_WORD[q]}:</span>{" "}
                {here.map((p, i) => (
                  <span key={p.id}>
                    {i > 0 && ", "}
                    <button
                      type="button"
                      onClick={() => onOpen(p.id)}
                      className={cn(
                        "rounded underline decoration-dotted underline-offset-2",
                        FOCUS_RING,
                      )}
                    >
                      {p.title}
                    </button>
                  </span>
                ))}
              </p>
            );
          })}
        </div>
      </section>
    </div>
  );
}
