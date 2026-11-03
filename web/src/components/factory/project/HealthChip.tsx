/**
 * A project's health chip: Stopped (red), Needs your merge (amber), On track
 * (green), Off track (red), At risk (neutral, with a mark: amber is only for
 * what needs a person), stale (grey) or Closed/Shipped. Its full meaning is
 * its title and accessible name.
 */

import { CheckIcon, CircleAlertIcon, ClockIcon, GitMergeIcon, OctagonXIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import type { HealthChip as HealthChipData } from "./projectModel";

const TONE: Record<HealthChipData["tone"], string> = {
  green: "border-status-green/55 bg-status-green/15 text-status-green-fg",
  amber: "border-status-yellow/55 bg-status-yellow/15 text-status-yellow-fg",
  red: "border-status-red/55 bg-status-red/15 text-status-red-fg",
  grey: "border-border bg-muted text-foreground/80",
  neutral: "border-border bg-background text-foreground",
};

function Icon({ chip }: { chip: HealthChipData }) {
  const cls = "size-3 shrink-0";
  if (chip.tone === "green") return <CheckIcon aria-hidden className={cls} />;
  if (chip.tone === "amber") return <GitMergeIcon aria-hidden className={cls} />;
  if (chip.tone === "red") return <OctagonXIcon aria-hidden className={cls} />;
  if (chip.tone === "grey") return <ClockIcon aria-hidden className={cls} />;
  if (chip.label === "At risk") return <CircleAlertIcon aria-hidden className={cls} />;
  return null;
}

export function HealthChip({
  chip,
  testId,
  className,
}: {
  chip: HealthChipData;
  testId?: string;
  className?: string;
}) {
  return (
    <span
      data-testid={testId}
      data-tone={chip.tone}
      title={chip.title}
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-md border px-1.5 py-0.5 text-xs leading-4 font-medium whitespace-nowrap",
        TONE[chip.tone],
        className,
      )}
    >
      <Icon chip={chip} />
      {chip.label}
      <span className="sr-only">. {chip.title}</span>
    </span>
  );
}
