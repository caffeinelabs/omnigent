/**
 * The seven-step pipeline of one project (Queued → Merged) with labels below.
 * Passed steps are filled, the current one is ringed in the stage's tone (an
 * animated ring while the PL is working), later ones are hollow.
 */

import { CheckIcon } from "lucide-react";
import { RunningDot } from "@/components/RunningDot";
import type { FactoryOverlay, FactoryStage } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { SANDBOX_STAGE_LABELS } from "@/pages/ChatIndicators";
import { STAGE_SHORT_LABEL, TONE_DOT, stageTone } from "../factoryFormat";
import { BOARD_STAGES, boardStage } from "./boardModel";

export type StepState = "done" | "current" | "todo";

/** Each board stage's state relative to the project's current one. */
export function stepStates(stage: FactoryStage): { stage: string; state: StepState }[] {
  const current = BOARD_STAGES.indexOf(boardStage(stage));
  return BOARD_STAGES.map((s, i) => ({
    stage: s,
    // A finished project has passed every step, Merged included.
    state: stage === "done" || i < current ? "done" : i === current ? "current" : "todo",
  }));
}

export function StageStepper({
  stage,
  overlay,
  running,
  dimmed = false,
  className,
}: {
  stage: FactoryStage;
  overlay: FactoryOverlay | null;
  /** The PL is working right now: the current step's ring animates. */
  running: boolean;
  dimmed?: boolean;
  className?: string;
}) {
  const tone = stageTone(stage, overlay);
  const steps = stepStates(stage);
  return (
    <ol
      data-testid="stage-stepper"
      data-current={boardStage(stage)}
      data-running={running ? "true" : "false"}
      aria-label={`Pipeline: ${STAGE_SHORT_LABEL[boardStage(stage)]}`}
      className={cn("flex w-full min-w-0 items-start", dimmed && "opacity-50", className)}
    >
      {steps.map(({ stage: step, state }, i) => (
        <li
          key={step}
          data-stage={step}
          data-state={state}
          aria-current={state === "current" ? "step" : undefined}
          className="flex min-w-0 flex-1 flex-col items-center gap-1"
        >
          <span className="flex h-3 w-full items-center">
            <span
              aria-hidden
              className={cn(
                "h-px flex-1",
                i === 0
                  ? "bg-transparent"
                  : state === "todo"
                    ? "bg-border"
                    : "bg-muted-foreground/40",
              )}
            />
            <span aria-hidden className="relative flex size-3 shrink-0 items-center justify-center">
              {state === "current" && running && (
                <span
                  className={cn(
                    "absolute inset-0 animate-ping rounded-full opacity-60 motion-reduce:animate-none",
                    TONE_DOT[tone],
                  )}
                />
              )}
              <span
                className={cn(
                  "relative rounded-full",
                  state === "done" && "size-2 bg-muted-foreground/60",
                  state === "current" &&
                    cn(
                      "size-2.5 ring-2 ring-offset-1 ring-offset-card",
                      TONE_DOT[tone],
                      ringFor(tone),
                    ),
                  state === "todo" && "size-2 border border-muted-foreground/40 bg-card",
                )}
              />
            </span>
            <span
              aria-hidden
              className={cn(
                "h-px flex-1",
                i === steps.length - 1
                  ? "bg-transparent"
                  : steps[i + 1]?.state === "todo"
                    ? "bg-border"
                    : "bg-muted-foreground/40",
              )}
            />
          </span>
          <span
            className={cn(
              "max-w-full truncate text-sm leading-4",
              state === "current"
                ? "font-medium text-foreground"
                : "text-muted-foreground @max-md:invisible",
              state === "todo" && "text-muted-foreground/70",
            )}
          >
            {STAGE_SHORT_LABEL[step as FactoryStage]}
          </span>
        </li>
      ))}
    </ol>
  );
}

const RING: Record<string, string> = {
  pass: "ring-success/30",
  pending: "ring-warning/30",
  warning: "ring-warning/30",
  fail: "ring-destructive/30",
  merged: "ring-brand-accent/30",
  active: "ring-session-active/30",
  muted: "ring-muted-foreground/30",
};

function ringFor(tone: string): string {
  return RING[tone] ?? RING.muted;
}

/** A queued project's PL sandbox coming up, then the brief landing. */
export const PROVISIONING_STEPS = [
  { stage: "provisioning", label: SANDBOX_STAGE_LABELS.provisioning ?? "Provisioning sandbox" },
  { stage: "cloning", label: SANDBOX_STAGE_LABELS.cloning ?? "Cloning repository" },
  { stage: "starting", label: SANDBOX_STAGE_LABELS.starting ?? "Connecting host" },
  { stage: "connecting", label: SANDBOX_STAGE_LABELS.connecting ?? "Starting agent" },
  { stage: "briefing", label: "Briefed" },
] as const;

export function ProvisioningTimeline({ stage }: { stage: string }) {
  const current = PROVISIONING_STEPS.findIndex((s) => s.stage === stage);
  return (
    <ol
      data-testid="provisioning-timeline"
      data-step={stage}
      aria-label="Starting the project lead"
      className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-sm"
    >
      {PROVISIONING_STEPS.map((step, i) => {
        const state: StepState = i < current ? "done" : i === current ? "current" : "todo";
        return (
          <li
            key={step.stage}
            data-stage={step.stage}
            data-state={state}
            className={cn(
              "inline-flex items-center gap-1",
              state === "done" && "text-muted-foreground",
              state === "current" && "font-medium text-foreground",
              state === "todo" && "text-muted-foreground/60",
            )}
          >
            {i > 0 && (
              <span aria-hidden className="mr-0.5 text-muted-foreground/50">
                →
              </span>
            )}
            {state === "done" && <CheckIcon aria-hidden className="size-3 text-success" />}
            {state === "current" && <RunningDot className="size-3" />}
            {step.stage === "briefing" && state === "current" ? "Briefing the PL" : step.label}
          </li>
        );
      })}
    </ol>
  );
}
