/**
 * The pipeline as seven small dots for a dense row: passed steps filled, the
 * current one ringed in the stage's tone (pulsing while the PL works), later
 * ones hollow. The stage name and its age follow.
 */

import type { ProjectView } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { stepStates } from "../board/StageStepper";
import {
  type FactoryTone,
  STAGE_SHORT_LABEL,
  TONE_DOT,
  formatElapsed,
  stageTone,
} from "../factoryFormat";

const TONE_RING: Record<FactoryTone, string> = {
  pass: "ring-success/35",
  pending: "ring-warning/35",
  warning: "ring-warning/35",
  fail: "ring-destructive/35",
  merged: "ring-brand-accent/35",
  active: "ring-session-active/35",
  muted: "ring-muted-foreground/30",
};

const LABEL_TONE: Record<FactoryTone, string> = {
  pass: "text-success",
  pending: "text-warning",
  warning: "text-warning",
  fail: "text-destructive",
  merged: "text-brand-accent",
  active: "text-foreground",
  muted: "text-muted-foreground",
};

export function StageDots({
  project,
  nowS,
  showAge = true,
}: {
  project: ProjectView;
  nowS: number;
  showAge?: boolean;
}) {
  const tone = stageTone(project.stage, project.overlay);
  const age = formatElapsed(nowS - project.stage_since);
  const running = project.pl?.running === true;
  // A project held on its dependencies says so instead of a bare "Queued".
  const waiting = project.overlay === "waiting";
  const deps = project.depends_on ?? [];
  const label = waiting ? "Waiting" : STAGE_SHORT_LABEL[project.stage];
  const title = waiting
    ? `Waiting on ${deps.length ? deps.join(", ") : "its dependencies"}`
    : `${STAGE_SHORT_LABEL[project.stage]}${project.overlay ? ` · ${project.overlay}` : ""}`;
  return (
    <span
      data-testid={`stage-dots-${project.slug}`}
      data-stage={project.stage}
      className="inline-flex min-w-0 items-center gap-2"
      title={title}
    >
      <span aria-hidden className="inline-flex shrink-0 items-center gap-[3px]">
        {stepStates(project.stage).map(({ stage, state }) => (
          <span
            key={stage}
            className={cn(
              "size-[7px] rounded-full",
              state === "done" && cn(TONE_DOT[tone], "opacity-55"),
              state === "current" &&
                cn(
                  TONE_DOT[tone],
                  "ring-[3px]",
                  TONE_RING[tone],
                  running && "animate-pulse motion-reduce:animate-none",
                ),
              state === "todo" && "border border-muted-foreground/40",
            )}
          />
        ))}
      </span>
      <span className={cn("shrink-0 text-sm", LABEL_TONE[tone])}>{label}</span>
      {showAge && age && (
        <span className="shrink-0 text-xs text-muted-foreground tabular-nums">{age}</span>
      )}
    </span>
  );
}
