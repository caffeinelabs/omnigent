/**
 * A bet's predicted range as a band on an axis with a dashed zero line, and
 * the measured result as a dot once a readout lands. Without an actual it
 * says "not measured yet", never a success.
 */

import { cn } from "@/lib/utils";
import { READOUT_MARK, READOUT_WORD, type ReadoutVerdict } from "./statusTokens";

export interface RangeBarProps {
  low: number;
  high: number;
  /** The measured value; null until a readout lands. */
  actual?: number | null;
  /** Unit after each number ("pp", "%"). */
  unit: string;
  /** The readout's verdict; only a win paints the dot green. */
  verdict?: ReadoutVerdict | null;
  /** Print what the band and the dot mean under the bar. */
  legend?: boolean;
  /** Number format (defaults to the plain number). */
  format?: (value: number) => string;
  className?: string;
}

const plain = (value: number) => String(value);

/** "Predicted 1 to 4 pp, actual 2.6 pp, won" or "…, not measured yet". */
export function rangeBarLabel({
  low,
  high,
  actual,
  unit,
  verdict,
  format = plain,
}: Pick<RangeBarProps, "low" | "high" | "actual" | "unit" | "verdict" | "format">): string {
  const [a, b] = low <= high ? [low, high] : [high, low];
  const predicted = `Predicted ${format(a)} to ${format(b)} ${unit}`;
  if (actual == null) return `${predicted}, not measured yet`;
  return `${predicted}, actual ${format(actual)} ${unit}${verdict ? `, ${READOUT_WORD[verdict]}` : ""}`;
}

export function RangeBar({
  low,
  high,
  actual = null,
  unit,
  verdict = null,
  legend = false,
  format = plain,
  className,
}: RangeBarProps) {
  const [a, b] = low <= high ? [low, high] : [high, low];
  const lo = Math.min(a, actual ?? a, 0);
  const hi = Math.max(b, actual ?? b, 0);
  const span = hi - lo || 1;
  const pos = (v: number) => ((v - lo) / span) * 100;
  return (
    <div className={cn("w-full", className)}>
      <div
        role="img"
        aria-label={rangeBarLabel({ low, high, actual, unit, verdict, format })}
        className="relative h-7"
      >
        <span className="absolute inset-x-0 top-[13px] border-t border-border" />
        <span
          className="absolute top-1 bottom-1 border-l border-dashed border-border-strong"
          style={{ left: `${pos(0)}%` }}
        />
        <span
          data-part="band"
          className="absolute top-2 h-3 rounded-[3px] border border-status-blue bg-status-blue/15"
          style={{ left: `${pos(a)}%`, width: `${((b - a) / span) * 100}%` }}
        />
        {actual != null && (
          <span
            data-part="actual"
            className={cn(
              "absolute top-[7px] -ml-[7px] size-3.5 rounded-full border-2 border-card-solid",
              verdict === "won" ? "bg-status-green" : "bg-foreground",
            )}
            style={{ left: `${pos(actual)}%` }}
          />
        )}
      </div>
      {legend && (
        <div
          aria-hidden
          className="flex flex-wrap justify-between gap-x-3 text-sm text-muted-foreground"
        >
          <span>
            predicted {format(a)} to {format(b)} {unit} (band)
          </span>
          <span>
            {actual == null
              ? "not measured yet"
              : `actual ${format(actual)} ${unit} (dot)${verdict ? ` ${READOUT_MARK[verdict]} ${READOUT_WORD[verdict]}` : ""}`}
          </span>
        </div>
      )}
    </div>
  );
}
