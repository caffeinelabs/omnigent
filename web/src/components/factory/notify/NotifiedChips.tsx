/**
 * "Slack · carol 10:04 · reminder 14:04 · ⚠ mia not linked" under a decision
 * or incident, and the deadline chip ("expires in 6h") of a decision that
 * defaults on its own. Renders nothing when there is neither.
 */

import { HourglassIcon, MessageSquareShareIcon } from "lucide-react";
import type { NotifiedView } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { TONE_CHIP } from "../factoryFormat";
import { deadlineText, deadlineUrgent, notifiedChips } from "./notifyModel";

export function NotifiedLine({
  notified,
  deadlineAt,
  deadlineEffect,
  nowS,
  testId,
  className,
}: {
  notified?: readonly NotifiedView[];
  deadlineAt?: number | null;
  deadlineEffect?: string | null;
  nowS: number;
  /** e.g. `decision-notified-D-19`. */
  testId: string;
  className?: string;
}) {
  const chips = notifiedChips(notified);
  const deadline = deadlineText(deadlineAt, deadlineEffect, nowS);
  if (chips.length === 0 && deadline == null) return null;
  return (
    <p
      data-testid={testId}
      className={cn(
        "flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-muted-foreground",
        className,
      )}
    >
      {deadline && (
        <span
          data-testid={`${testId}-deadline`}
          title={deadlineEffect ? `If nobody answers, it ${deadlineEffect}` : undefined}
          className={cn(
            "inline-flex h-5 items-center gap-1 rounded-sm px-1.5 font-medium",
            deadlineUrgent(deadlineAt, nowS) ? TONE_CHIP.fail : TONE_CHIP.warning,
          )}
        >
          <HourglassIcon aria-hidden className="size-3" />
          {deadline}
        </span>
      )}
      {chips.length > 0 && (
        <span className="inline-flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1">
          <span className="inline-flex items-center gap-1">
            <MessageSquareShareIcon aria-hidden className="size-3" />
            Slack
          </span>
          {chips.map((chip) => (
            <span
              key={chip.key}
              title={chip.title}
              data-ok={chip.ok ? "true" : "false"}
              className={cn(
                "inline-flex h-5 items-center rounded-sm px-1.5 font-mono tabular-nums",
                chip.ok ? TONE_CHIP.muted : TONE_CHIP.fail,
              )}
            >
              {chip.text}
            </span>
          ))}
        </span>
      )}
    </p>
  );
}
