/**
 * A project's stage as six segments (Plan, Build, Review, Verify, Ready for
 * merge, Shipped): done segments grey, the current one blue, red when the
 * project is broken. Its label says the same in words.
 */

import type { FactoryStage } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { STAGE_STEPS, STAGE_STEP_LABEL, type StageStep, stageStepOf } from "./statusTokens";

export interface StageBarProps {
  /** A bar step, or a project's derived stage (mapped onto the six steps). */
  stage: StageStep | FactoryStage;
  /** The project is stopped or failed: the current segment turns red. */
  broken?: boolean;
  /** Print the six step names under the bar (project page). */
  labels?: boolean;
  className?: string;
}

function asStep(stage: StageStep | FactoryStage): StageStep {
  return (STAGE_STEPS as readonly string[]).includes(stage)
    ? (stage as StageStep)
    : stageStepOf(stage as FactoryStage);
}

/** "Stage: Verify, step 4 of 6" (", stopped" when broken). */
export function stageBarLabel(stage: StageStep | FactoryStage, broken = false): string {
  const step = asStep(stage);
  const index = STAGE_STEPS.indexOf(step);
  return `Stage: ${STAGE_STEP_LABEL[step]}, step ${index + 1} of ${STAGE_STEPS.length}${broken ? ", stopped" : ""}`;
}

export function StageBar({ stage, broken = false, labels = false, className }: StageBarProps) {
  const step = asStep(stage);
  const current = STAGE_STEPS.indexOf(step);
  return (
    <div className={cn("w-full", className)}>
      <div
        role="img"
        aria-label={stageBarLabel(step, broken)}
        data-stage={step}
        data-broken={broken || undefined}
        className="flex w-full gap-[3px]"
      >
        {STAGE_STEPS.map((s, i) => (
          <span
            key={s}
            data-segment={i < current ? "done" : i === current ? "current" : "todo"}
            className={cn(
              "h-[5px] flex-1 rounded-full",
              i < current && "bg-foreground/45",
              i === current && (broken ? "bg-status-red" : "bg-status-blue"),
              i > current && "bg-status-gray/25",
            )}
          />
        ))}
      </div>
      {labels && (
        <div aria-hidden className="mt-1 flex justify-between gap-2 text-sm text-muted-foreground">
          {STAGE_STEPS.map((s, i) => (
            <span key={s} className={cn(i === current && "font-semibold text-foreground")}>
              {STAGE_STEP_LABEL[s]}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
