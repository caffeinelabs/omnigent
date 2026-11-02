/**
 * The chain from evidence to impact as counts (signals → opportunities →
 * bets → building → shipped → measured). Each count is a filter toggle;
 * zeros are faint so "0 measured" visibly says no loop has closed yet.
 */

import { cn } from "@/lib/utils";
import { FAINT_TEXT, FOCUS_RING, MEANING } from "./statusTokens";

export interface ChainCount {
  key: string;
  count: number;
  /** What is counted ("signals this week"). */
  label: string;
  /** Something in this step waits for you: the count turns amber. */
  waiting?: boolean;
}

export interface ChainCountsProps {
  counts: readonly ChainCount[];
  /** The count used as a filter, if any. */
  selected?: string | null;
  /** Toggles a filter; called with null when the selected count is pressed again. */
  onSelect?: (key: string | null) => void;
  label?: string;
  className?: string;
}

export function ChainCounts({
  counts,
  selected = null,
  onSelect,
  label = "From evidence to impact",
  className,
}: ChainCountsProps) {
  return (
    <section
      aria-label={label}
      className={cn(
        "flex items-stretch overflow-hidden rounded-xl border border-border bg-card max-[760px]:grid max-[760px]:grid-cols-3",
        className,
      )}
    >
      {counts.map((c) => {
        const body = (
          <>
            <span
              className={cn(
                "block text-xl font-semibold tabular-nums",
                c.count === 0 ? FAINT_TEXT : c.waiting && MEANING.needsYou.text,
              )}
            >
              {c.count}
            </span>
            <span
              className={cn(
                "block text-sm",
                selected === c.key ? "text-foreground" : "text-muted-foreground",
              )}
            >
              {c.label}
            </span>
          </>
        );
        const cell =
          "min-w-0 flex-1 border-r border-border px-3 py-2.5 text-left last:border-r-0 max-[760px]:border-b";
        return onSelect ? (
          <button
            key={c.key}
            type="button"
            aria-pressed={selected === c.key}
            data-key={c.key}
            onClick={() => onSelect(selected === c.key ? null : c.key)}
            className={cn(
              cell,
              "cursor-pointer hover:bg-muted aria-pressed:bg-status-blue/15",
              FOCUS_RING,
              "focus-visible:ring-inset",
            )}
          >
            {body}
          </button>
        ) : (
          <div key={c.key} data-key={c.key} className={cell}>
            {body}
          </div>
        );
      })}
    </section>
  );
}
