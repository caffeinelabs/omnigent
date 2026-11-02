/**
 * The loop the factory runs (Sense → Make sense → Decide → Build → Measure)
 * in one card: the goal line on top, then each stage with its count, one line
 * of detail and the lead that owns it, then a dashed return line. With
 * discovery off only Build and Measure show, with a way to turn it on.
 * Every word and number is the factory's data; nothing here names a product.
 */

import { ChevronRightIcon, CornerDownLeftIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { MemberAvatar, type MemberIdentity, memberAvatarLabel } from "./MemberAvatar";
import {
  DISCOVERY_STAGES,
  FOCUS_RING,
  LOOP_STAGE_LABEL,
  type LoopStageKey,
  MEANING,
} from "./statusTokens";

export interface LoopStage {
  key: LoopStageKey;
  count: number;
  /** What the count counts ("signals this week"). */
  unit: string;
  /** One line under the count ("4 sources on"). */
  detail?: string;
  /** The part of the detail that waits for you ("1 waits for you"), in amber. */
  waiting?: string;
  /** The lead that owns this stage. */
  owner?: MemberIdentity;
  to?: string;
  onSelect?: () => void;
}

export interface LoopGoal {
  /** Small id before the statement ("G-1"). */
  id?: string;
  /** The goal as people wrote it; null when there is none yet. */
  statement: string | null;
  measured: boolean;
  /** Why it is not measured, or what to do without a goal. */
  note?: string;
}

export interface LoopStripProps {
  stages: readonly LoopStage[];
  discoveryOn: boolean;
  goal?: LoopGoal | null;
  /** Where the goal is edited ("Edit strategy"). */
  goalAction?: { label: string; to?: string; onSelect?: () => void };
  /** Shown instead of the discovery stages while discovery is off. */
  turnOn?: { label: string; to?: string; onSelect?: () => void };
  /** The dashed return line's words. */
  returnLine?: string;
  className?: string;
}

const DEFAULT_RETURN =
  "every readout updates its opportunity, the evidence meter and the prediction ledger";

/** "Sense: 214 signals this week. 4 sources on · 1 waits for you. Owned by Scout." */
export function loopStageLabel(stage: LoopStage): string {
  const detail = [stage.detail, stage.waiting].filter(Boolean).join(" · ");
  const owner = stage.owner
    ? ` Owned by ${stage.owner.name ?? memberAvatarLabel(stage.owner)}.`
    : "";
  return `${LOOP_STAGE_LABEL[stage.key]}: ${stage.count} ${stage.unit}.${detail ? ` ${detail}.` : ""}${owner}`;
}

function Action({
  action,
  className,
  children,
}: {
  action: { to?: string; onSelect?: () => void };
  className?: string;
  children: ReactNode;
}) {
  if (action.to) {
    return (
      <Link to={action.to} className={cn(FOCUS_RING, className)}>
        {children}
      </Link>
    );
  }
  return (
    <button
      type="button"
      onClick={action.onSelect}
      className={cn("cursor-pointer", FOCUS_RING, className)}
    >
      {children}
    </button>
  );
}

function StageCell({ stage, last }: { stage: LoopStage; last: boolean }) {
  const content = (
    <>
      <span className="flex items-center gap-1.5 text-sm font-semibold tracking-wide text-muted-foreground uppercase">
        {LOOP_STAGE_LABEL[stage.key]}
        {stage.owner && <MemberAvatar {...stage.owner} size={18} />}
      </span>
      <span className="text-xl leading-tight font-semibold tabular-nums max-[760px]:text-lg">
        {stage.count}
        <small className="ml-1 text-ui font-medium text-foreground/80">{stage.unit}</small>
      </span>
      {(stage.detail || stage.waiting) && (
        <span className="text-sm text-muted-foreground">
          {stage.detail}
          {stage.detail && stage.waiting && " · "}
          {stage.waiting && <span className={MEANING.needsYou.text}>{stage.waiting}</span>}
        </span>
      )}
    </>
  );
  const cell =
    "relative flex min-w-0 flex-col gap-0.5 rounded-lg py-1.5 pr-5 pl-3 text-left max-[760px]:px-2";
  const interactive = stage.to || stage.onSelect;
  return (
    <li className="relative min-w-0" data-stage={stage.key}>
      {interactive ? (
        <Action action={stage} className={cn(cell, "w-full hover:bg-muted")}>
          <span className="sr-only">{loopStageLabel(stage)}</span>
          <span aria-hidden className="contents">
            {content}
          </span>
        </Action>
      ) : (
        <div className={cell}>{content}</div>
      )}
      {!last && (
        <ChevronRightIcon
          aria-hidden
          className="absolute top-1/2 right-0 size-3.5 -translate-y-1/2 text-foreground-tertiary max-[760px]:hidden"
        />
      )}
    </li>
  );
}

export function LoopStrip({
  stages,
  discoveryOn,
  goal,
  goalAction,
  turnOn,
  returnLine = DEFAULT_RETURN,
  className,
}: LoopStripProps) {
  const shown = discoveryOn ? stages : stages.filter((s) => !DISCOVERY_STAGES.has(s.key));
  return (
    <section
      aria-label="How the factory works right now"
      data-discovery={discoveryOn ? "on" : "off"}
      className={cn("rounded-xl border border-border bg-card px-3.5 pt-3 pb-2.5", className)}
    >
      {(goal !== undefined || goalAction) && discoveryOn && (
        <div className="mb-2.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 border-b border-dashed border-border px-1 pb-2.5 text-sm">
          <span className="font-semibold tracking-wide text-muted-foreground uppercase">
            Goal{goal?.id ? ` ${goal.id}` : ""}
          </span>
          <span className="font-semibold text-foreground">{goal?.statement ?? "No goal yet."}</span>
          {goal?.statement && (
            <span
              className={cn(
                "rounded-md px-1.5",
                goal.measured
                  ? `${MEANING.working.surface} ${MEANING.working.text}`
                  : "bg-muted text-foreground/85",
              )}
            >
              {goal.measured ? "Measured" : "Not measured yet"}
            </span>
          )}
          {goal?.note && <span className="text-muted-foreground">{goal.note}</span>}
          {goalAction && (
            <Action
              action={goalAction}
              className="ml-auto rounded text-status-blue-fg underline-offset-2 hover:underline"
            >
              {goalAction.label}
            </Action>
          )}
        </div>
      )}
      <ol
        className={cn(
          "m-0 grid list-none p-0",
          // Five stages wrap to three columns on a phone; Build and Measure keep two.
          shown.length >= 5 ? "grid-cols-5 max-[760px]:grid-cols-3" : "grid-cols-2",
          "max-[760px]:gap-y-1.5",
        )}
      >
        {shown.map((stage, i) => (
          <StageCell key={stage.key} stage={stage} last={i === shown.length - 1} />
        ))}
      </ol>
      {!discoveryOn && turnOn && (
        <p className="mt-2 px-1 text-sm text-muted-foreground">
          Discovery is off, so the factory only builds what people request.{" "}
          <Action
            action={turnOn}
            className="rounded text-status-blue-fg underline underline-offset-2"
          >
            {turnOn.label}
          </Action>
        </p>
      )}
      {discoveryOn && (
        <div
          aria-hidden
          className="mt-1.5 flex items-center gap-2 px-1.5 text-sm text-muted-foreground"
        >
          <CornerDownLeftIcon className="size-3.5 shrink-0" />
          <span className="h-0 flex-1 border-t-[1.5px] border-dashed border-border-strong" />
          <span className="text-center">{returnLine}</span>
          <span className="h-0 flex-1 border-t-[1.5px] border-dashed border-border-strong max-[760px]:hidden" />
        </div>
      )}
    </section>
  );
}
