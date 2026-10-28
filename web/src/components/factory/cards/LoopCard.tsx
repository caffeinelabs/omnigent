/**
 * A loop that needs a human (exhausted, stalled, thrashing, escalated) as a
 * Room card: the loop, its iterations against the budget, what it spent,
 * the last reason, and its remedies (+1 attempt, switch vendor, fresh
 * restart, abandon), which answer the loop's decision in one click.
 */

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2Icon, RefreshCwIcon, StarIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { factoryInvalidationKeys } from "@/hooks/useFactory";
import {
  type DecisionOptionView,
  type DecisionView,
  FactoryApiError,
  type LoopView,
  answerDecision,
} from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { answerBlockedReason, answerLabel } from "../decisions/DecisionCard";
import { TONE_CHIP, type FactoryTone, formatUsd } from "../factoryFormat";
import { ReasonGated, useViewer, useViewerCan } from "../shell/viewerCan";

const STATE_TONE: Record<LoopView["state"], FactoryTone> = {
  running: "active",
  converged: "pass",
  stalled: "warning",
  thrashing: "warning",
  exhausted: "fail",
  escalated: "fail",
  cancelled: "muted",
  superseded: "muted",
};

/** `"implement · T-4 · 3/3 · $11.80/$15"`. */
export function loopHeadline(loop: LoopView): string {
  const parts: string[] = [loop.kind];
  if (loop.task_ref) parts.push(loop.task_ref);
  else if (loop.pr_number) parts.push(`!${loop.pr_number}`);
  parts.push(loop.limit ? `${loop.iteration}/${loop.limit}` : `iteration ${loop.iteration}`);
  const budget = typeof loop.spec.usd === "number" ? loop.spec.usd : null;
  const usd = (value: number) => formatUsd(value) ?? "—";
  if (loop.spent.usd != null) {
    parts.push(budget != null ? `${usd(loop.spent.usd)}/${usd(budget)}` : usd(loop.spent.usd));
  }
  return parts.join(" · ");
}

function Remedies({ factoryId, decision }: { factoryId: string; decision: DecisionView }) {
  const queryClient = useQueryClient();
  const viewer = useViewer();
  const { can } = useViewerCan();
  const blocked = answerBlockedReason(decision, can, viewer?.user_id ?? null);
  const [busy, setBusy] = useState<string | null>(null);
  const [done, setDone] = useState<DecisionView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const answered = done ?? (decision.status === "answered" ? decision : null);
  const pick = async (option: DecisionOptionView) => {
    if (busy || blocked) return;
    setBusy(option.id);
    setError(null);
    try {
      const res = await answerDecision(decision.id, { option_id: option.id });
      setDone(res.decision);
      for (const key of factoryInvalidationKeys(factoryId)) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
    } catch (err) {
      if (err instanceof FactoryApiError && err.status === 409) {
        const by = (err.body as { answered_by?: string } | null)?.answered_by;
        setError(`Already answered${by ? ` by ${by}` : ""}`);
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setBusy(null);
    }
  };
  if (answered) {
    return (
      <p data-testid={`loop-remedy-done-${decision.ref}`} className="text-muted-foreground">
        {answerLabel(answered)}
        {answered.answered_by ? ` · ${answered.answered_by.split("@")[0]}` : ""}
        <span className="ml-1 font-mono text-xs">{decision.ref}</span>
      </p>
    );
  }
  if (decision.status !== "open") return null;
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div className="flex flex-wrap items-center gap-1">
        {decision.options.map((option) => (
          <ReasonGated key={option.id} reason={blocked} testId="remedy">
            <Button
              size="xs"
              variant={option.recommended ? "default" : "outline"}
              onClick={() => void pick(option)}
              disabled={busy != null}
              data-testid={`loop-remedy-${decision.ref}-${option.id}`}
              componentId="factory.room.loop_remedy"
            >
              {busy === option.id ? (
                <Loader2Icon className="animate-spin" />
              ) : (
                option.recommended && <StarIcon />
              )}
              {option.label}
            </Button>
          </ReasonGated>
        ))}
        <span className="ml-auto font-mono text-xs text-muted-foreground">{decision.ref}</span>
      </div>
      {error && <p className="text-destructive">{error}</p>}
    </div>
  );
}

export function LoopCard({
  factoryId,
  loop,
  decision,
  line,
  className,
}: {
  factoryId: string;
  /** Null on a server without the loop engine: the event line stands in. */
  loop: LoopView | null;
  decision: DecisionView | null;
  line: string;
  className?: string;
}) {
  const state = loop?.state ?? (/exhausted/i.test(line) ? "exhausted" : "stalled");
  return (
    <article
      data-testid={`loop-card-${decision?.ref ?? loop?.id ?? "loop"}`}
      data-state={state}
      className={cn(
        "relative flex min-w-0 flex-col gap-1.5 rounded-lg border border-border bg-card p-3 text-sm",
        className,
      )}
    >
      <header className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1">
        <RefreshCwIcon aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="min-w-0 font-medium break-words">{loop ? loopHeadline(loop) : line}</span>
        <span
          className={cn(
            "ml-auto shrink-0 rounded-full px-1.5 text-xs leading-5 uppercase",
            TONE_CHIP[STATE_TONE[state]],
          )}
        >
          {state}
        </span>
      </header>
      {loop?.last_reason && (
        <p className="break-words text-muted-foreground">
          last: {loop.last_verdict ? `${loop.last_verdict} · ` : ""}
          {loop.last_reason}
        </p>
      )}
      {!loop && decision?.context && (
        <p className="break-words text-muted-foreground">{decision.context}</p>
      )}
      {decision && <Remedies factoryId={factoryId} decision={decision} />}
    </article>
  );
}
