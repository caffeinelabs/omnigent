/**
 * Evidence strength as five squares in the data hue: one per level from weak
 * to proven, and a single hollow square for a hunch (a person's conviction,
 * not user evidence). The level word can sit beside it.
 */

import { cn } from "@/lib/utils";
import { DATA_HUE, type EvidenceLevel, evidenceSquares } from "./statusTokens";

export interface EvidenceMeterProps {
  level: EvidenceLevel;
  /** Print the level word after the squares. */
  showLabel?: boolean;
  className?: string;
}

export function EvidenceMeter({ level, showLabel = false, className }: EvidenceMeterProps) {
  const filled = evidenceSquares(level);
  const hunch = level === "hunch";
  return (
    <span className={cn("inline-flex items-center gap-1.5 align-middle", className)}>
      <span
        role="img"
        aria-label={`Evidence: ${level}`}
        data-level={level}
        className="inline-flex gap-0.5"
      >
        {[1, 2, 3, 4, 5].map((k) => (
          <i
            key={k}
            data-on={k <= filled || undefined}
            className={cn(
              "inline-block size-[7px] rounded-[2px]",
              k > filled && "bg-status-gray/25",
            )}
            style={
              k <= filled
                ? hunch
                  ? { border: `1.5px solid ${DATA_HUE}` }
                  : { background: DATA_HUE }
                : undefined
            }
          />
        ))}
      </span>
      {showLabel && (
        <span aria-hidden className="text-sm text-muted-foreground">
          {level}
        </span>
      )}
    </span>
  );
}
